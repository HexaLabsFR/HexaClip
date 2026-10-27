# -*- coding: utf-8 -*-
"""
Enregistrement en continu « façon replay » : FFmpeg capture l'écran (Desktop Duplication, ddagrab)
et l'encode avec la carte graphique (NVENC / AMF / Quick Sync) en petits morceaux de 2 s qui
tournent en boucle sur le disque (seulement les 3 dernières minutes). Le son du PC (ce que tu
entends) est capté à part par WASAPI loopback et envoyé dans le même FFmpeg.

Quand une action est détectée, les morceaux qui la couvrent sont recopiés et recollés sans
réencodage dans un clip MP4 : quasi instantané, aucune perte de qualité.
"""
from __future__ import annotations

import array
import collections
import os
import shutil
import sys
import threading
import time
import warnings

import ffmpeg_tools as ff

SEG_S = 2
IS_WIN = sys.platform == "win32"


# ----------------------------------------------------------------------------
# Son du PC (WASAPI loopback)
# ----------------------------------------------------------------------------
OUT_RATE, OUT_CH = 48000, 2          # format envoyé à FFmpeg, fixe même si la sortie son change
SILENCE_REOPEN_S = 20                # silence total plus long que ça : on vérifie la sortie son par défaut
SOUND_THRESHOLD = 8                  # crête (sur 32767) au-dessus de laquelle on considère qu'il y a du son

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import audioop


def to_stereo48(data, ch, rate, state=None):
    """PCM 16 bits entrelacé (ch voies, rate Hz) → stéréo 48 kHz. Renvoie (octets, état du rééchantillonnage)."""
    a = array.array("h")
    a.frombytes(data[:len(data) - len(data) % (2 * max(1, ch))])
    if ch == 2:
        b = a.tobytes()
    else:
        n = len(a) // max(1, ch)
        if ch == 1:
            left = right = a
        else:
            # 5.1 / 7.1 (casques « surround ») : avant + centre + arrière/côtés, comme un mixage stéréo classique
            def voice(i):
                return a[i:n * ch:ch].tobytes() if i < ch else None
            fl, fr = voice(0), voice(1)
            left, right = fl, fr
            extra_l = [voice(2)] + [voice(i) for i in (4, 6) if i < ch]
            extra_r = [voice(2)] + [voice(i) for i in (5, 7) if i < ch]
            for x in extra_l:
                if x:
                    left = audioop.add(left, audioop.mul(x, 2, 0.707), 2)
            for x in extra_r:
                if x:
                    right = audioop.add(right, audioop.mul(x, 2, 0.707), 2)
            left, right = array.array("h", left), array.array("h", right)
        out = array.array("h", bytes(n * 4))
        out[0::2] = left[:n]
        out[1::2] = right[:n]
        b = out.tobytes()
    if rate != OUT_RATE and b:
        b, state = audioop.ratecv(b, 2, 2, int(rate), OUT_RATE, state)
    return b, state


class AudioPump:
    """Capte le son de la sortie par défaut de Windows (ce que tu entends) et l'envoie à FFmpeg en stéréo 48 kHz.

    - capture par rappel (callback) : rien ne bloque, même quand aucun son ne joue ;
    - quand Windows n'envoie rien (aucun son), on comble avec du silence pour garder le flux régulier ;
    - après 20 s de silence total, on vérifie la sortie par défaut (casque allumé ou branché après le
      lancement, changement de sortie…) et on bascule dessus.
    """

    def __init__(self, log=print):
        self.rate, self.ch = OUT_RATE, OUT_CH
        self.ok = False
        self.error = ""
        self.name = ""
        self.log = log
        self.dev_rate, self.dev_ch = OUT_RATE, OUT_CH
        self.last_sound = 0.0
        self._p = self._stream = None
        self._q = collections.deque()
        self._stop = threading.Event()
        self._opened_at = 0.0
        self._last_reopen = 0.0

    # --- ouverture / fermeture de la sortie son ----------------------------
    def _open_device(self):
        import pyaudiowpatch as pyaudio
        p = pyaudio.PyAudio()
        try:
            try:
                spk = p.get_default_wasapi_loopback()
            except Exception:
                wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
                spk = out if out.get("isLoopbackDevice") else next(
                    (lb for lb in p.get_loopback_device_info_generator() if out["name"] in lb["name"]), None)
            if not spk:
                raise RuntimeError("aucune sortie son en boucle trouvée")
            rate = int(spk["defaultSampleRate"])
            ch = max(1, int(spk["maxInputChannels"]))
            q = self._q

            def callback(in_data, frame_count, time_info, status):
                q.append(in_data)
                return None, pyaudio.paContinue

            st = p.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
                        input_device_index=spk["index"], frames_per_buffer=max(256, rate // 100),
                        stream_callback=callback)
            st.start_stream()
            return p, st, spk["name"].replace(" [Loopback]", ""), rate, ch
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
            raise

    def _close_device(self, wait=0.0):
        """Ferme la sortie son dans un fil à part (PortAudio peut bloquer) ; attend au plus wait secondes."""
        p, st = self._p, self._stream
        self._p = self._stream = None

        def close():
            for fn in (lambda: st.stop_stream(), lambda: st.close(), lambda: p.terminate()):
                try:
                    fn()
                except Exception:
                    pass
        if p or st:
            t = threading.Thread(target=close, daemon=True)
            t.start()
            if wait:
                t.join(wait)
                return not t.is_alive()
        return True

    def open(self):
        if not IS_WIN:
            self.error = "son PC : Windows uniquement"
            return False
        try:
            self._p, self._stream, self.name, self.dev_rate, self.dev_ch = self._open_device()
        except Exception as e:
            self.error = f"son PC indisponible ({e})"
            return False
        self.ok = True
        self._opened_at = self._last_reopen = time.time()
        self.log(f"Son : capté sur « {self.name} » ({self.dev_rate} Hz, {self.dev_ch} voies).")
        return True

    def _reopen(self):
        """Rebascule sur la sortie son par défaut actuelle (la liste des sorties n'est relue qu'après fermeture)."""
        self._last_reopen = time.time()
        old = self.name
        if not self._close_device(wait=3.0):
            return
        self._q.clear()
        try:
            self._p, self._stream, self.name, self.dev_rate, self.dev_ch = self._open_device()
            self._opened_at = time.time()
            if self.name != old:
                self.log(f"Son : la sortie Windows a changé, capté maintenant sur « {self.name} ».")
        except Exception as e:
            self.log(f"Son : réouverture impossible ({e}), nouvel essai dans {SILENCE_REOPEN_S} s.")

    def silent_for(self):
        """Secondes depuis le dernier son entendu (depuis l'ouverture si aucun)."""
        return time.time() - max(self.last_sound, self._opened_at) if self.ok else 0.0

    # --- boucle d'envoi vers FFmpeg ----------------------------------------
    def pump(self, out):
        """Copie le son vers out (stdin de FFmpeg) jusqu'à stop()."""
        t0, sent, state = time.monotonic(), 0, None
        try:
            while not self._stop.is_set():
                wrote = False
                while self._q:
                    data = self._q.popleft()
                    b, state = to_stereo48(data, self.dev_ch, self.dev_rate, state)
                    if not b:
                        continue
                    if audioop.max(b, 2) > SOUND_THRESHOLD:
                        self.last_sound = time.time()
                    out.write(b)
                    sent += len(b) // 4
                    wrote = True
                # aucun son joué = Windows n'envoie rien : on comble avec du silence
                due = int((time.monotonic() - t0) * OUT_RATE) - sent
                if due > OUT_RATE // 12:
                    n = due - OUT_RATE // 50
                    out.write(bytes(n * 4))
                    sent += n
                    wrote = True
                elif due < -OUT_RATE:          # en avance de plus d'1 s (paquets en rafale) : on recale
                    t0 = time.monotonic() - sent / OUT_RATE
                if wrote:
                    out.flush()
                # silence prolongé ou sortie disparue : vérifier la sortie par défaut
                now = time.time()
                dead = False
                try:
                    dead = self._stream is None or not self._stream.is_active()
                except Exception:
                    dead = True
                if now - self._last_reopen > SILENCE_REOPEN_S and (dead or self.silent_for() > SILENCE_REOPEN_S):
                    self._reopen()
                    state = None
                time.sleep(0.01)
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        self._close_device()


# ----------------------------------------------------------------------------
# Choix de la méthode de capture
# ----------------------------------------------------------------------------
def video_codec_args(enc, fps, q, source):
    """Arguments d'encodage pour un encodeur et une source (ddagrab = images GPU, gdigrab = images CPU)."""
    gop = str(int(fps))
    if enc == "h264_nvenc":
        a = ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "vbr", "-cq", str(q), "-b:v", "0",
             "-maxrate", "45M", "-bufsize", "90M", "-bf", "0", "-g", gop]
    elif enc == "h264_amf":
        a = ["-c:v", "h264_amf", "-usage", "lowlatency", "-quality", "speed", "-rc", "cqp",
             "-qp_i", str(q), "-qp_p", str(q), "-bf", "0", "-g", gop]
    elif enc == "h264_qsv":
        a = ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", str(q), "-bf", "0", "-g", gop]
        if source == "ddagrab":
            a = ["-vf", "hwmap=derive_device=qsv,format=qsv"] + a
    else:
        a = ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-crf", str(q + 2), "-g", gop]
        vf = "scale=-2:'min(1080,ih)',format=yuv420p"
        a = ["-vf", ("hwdownload,format=bgra," + vf) if source == "ddagrab" else vf] + a
    if source == "gdigrab" and enc in ("h264_nvenc", "h264_amf"):
        a = ["-pix_fmt", "yuv420p"] + a
    return a


def video_input_args(source, fps, screen=0):
    if source == "ddagrab":
        return ["-f", "lavfi", "-i", f"ddagrab=output_idx={int(screen)}:framerate={int(fps)}:draw_mouse=0"]
    if source == "gdigrab":
        return ["-f", "gdigrab", "-framerate", str(int(fps)), "-draw_mouse", "0", "-i", "desktop"]
    raise ValueError(source)


def detect_method(cfg, log=print):
    """Teste les combinaisons source + encodeur et renvoie la première qui marche sur ce PC."""
    fps, q = int(cfg["fps"]), int(cfg["qualite"])
    available = ff.encoders()
    wanted = cfg["encodeur"]
    order = [wanted] if wanted != "auto" else ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"]
    tries = []
    for src in ("ddagrab", "gdigrab"):
        for enc in order:
            if enc in available:
                tries.append((src, enc))
    if wanted != "auto" and "libx264" in available:
        tries.append(("gdigrab", "libx264"))
    for src, enc in tries:
        f = 30 if enc == "libx264" else fps
        cmd = [ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error"] + video_input_args(src, f, cfg["ecran"]) \
            + ["-frames:v", "15"] + video_codec_args(enc, f, q, src) + ["-f", "null", "-"]
        try:
            r = ff.run(cmd, timeout=25)
            if r.returncode == 0:
                log(f"Capture : {src} + {enc}")
                return {"source": src, "encodeur": enc, "fps": f}
            log(f"Capture {src} + {enc} refusée : {(r.stderr or b'').decode('utf-8', 'ignore').strip()[-200:]}")
        except Exception as e:
            log(f"Capture {src} + {enc} : {e}")
    return None


# ----------------------------------------------------------------------------
# Enregistreur
# ----------------------------------------------------------------------------
class Recorder:
    def __init__(self, buf_dir, buffer_s=180, log=print):
        self.buf_dir = buf_dir
        self.buffer_s = buffer_s
        self.log = log
        self.proc = None
        self.audio = None
        self.method = None
        self.started_wall = 0.0
        self._offs = collections.deque(maxlen=40)   # (heure murale - temps du flux) observés
        self._seg_lines = []
        self._seg_pos = 0
        self._lock = threading.Lock()
        self._stderr = collections.deque(maxlen=30)
        self.pending = 0
        self.last_error = ""
        self.has_audio = False

    # --- démarrage / arrêt -----------------------------------------------
    def start(self, method, audio=True, audio_offset_ms=0, test_inputs=None):
        """method = {"source", "encodeur", "fps"} ; test_inputs = arguments d'entrée de remplacement (tests)."""
        self.stop()
        self._kill_orphan()
        shutil.rmtree(self.buf_dir, ignore_errors=True)
        os.makedirs(self.buf_dir, exist_ok=True)
        self.method = method
        self._offs.clear()
        self._seg_lines, self._seg_pos = [], 0
        fps, src = method["fps"], method["source"]
        q = method.get("qualite", 21)
        wrap = int(self.buffer_s / SEG_S) + 6
        args = [ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostats",
                "-progress", "pipe:1", "-stats_period", "0.5"]
        audio_args, maps = [], ["-map", "0:v"]
        self.audio = None
        self.has_audio = False
        if test_inputs:
            args += test_inputs
            if len([a for a in test_inputs if a == "-i"]) > 1:
                maps += ["-map", "1:a"]
                self.has_audio = True
        else:
            args += video_input_args(src, fps, method.get("ecran", 0))
            if audio:
                pump = AudioPump(log=self.log)
                if pump.open():
                    self.audio = pump
                    if audio_offset_ms:
                        audio_args += ["-itsoffset", f"{audio_offset_ms / 1000:.3f}"]
                    audio_args += ["-use_wallclock_as_timestamps", "1", "-thread_queue_size", "2048",
                                   "-f", "s16le", "-ar", str(pump.rate), "-ac", str(pump.ch), "-i", "pipe:0"]
                    maps += ["-map", "1:a"]
                    self.has_audio = True
                else:
                    self.log(pump.error)
            args += audio_args
        args += maps
        args += video_codec_args(method["encodeur"], fps, q, src)
        if self.has_audio:
            args += ["-c:a", "aac", "-b:a", "160k", "-ac", "2", "-af", "aresample=async=1:first_pts=0"]
        args += ["-f", "segment", "-segment_time", str(SEG_S), "-segment_format", "mpegts",
                 "-segment_wrap", str(wrap), "-segment_list", os.path.join(self.buf_dir, "liste.csv"),
                 "-segment_list_type", "csv", "-reset_timestamps", "0",
                 os.path.join(self.buf_dir, "seg%03d.ts")]
        import subprocess
        self.proc = ff.popen(args, stdin=subprocess.PIPE if self.audio else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.started_wall = time.time()
        threading.Thread(target=self._read_progress, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        if self.audio:
            threading.Thread(target=self.audio.pump, args=(self.proc.stdin,), daemon=True).start()
        return True

    def stop(self, wait_pending=8.0):
        """Arrête la capture sans jamais rester bloqué (le son et FFmpeg sont coupés indépendamment)."""
        t_end = time.time() + wait_pending
        while self.pending and time.time() < t_end:
            time.sleep(0.2)
        p, self.proc = self.proc, None
        audio, self.audio = self.audio, None
        if audio:
            audio.stop()                       # non bloquant
        if p and p.poll() is None:
            def close_stdin():
                try:
                    if p.stdin:
                        p.stdin.close()
                except Exception:
                    pass
            threading.Thread(target=close_stdin, daemon=True).start()
            try:
                p.terminate()
                p.wait(5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    # --- FFmpeg resté seul (HexaClip fermé brutalement, ancienne version figée…) ---
    def _kill_orphan(self):
        """Arrête un FFmpeg d'une capture précédente qui écrirait encore dans ce tampon."""
        target = os.path.normcase(os.path.abspath(os.path.join(self.buf_dir, "seg%03d.ts")))
        try:
            import psutil
            for pr in psutil.process_iter(["name", "cmdline"]):
                if "ffmpeg" not in (pr.info.get("name") or "").lower():
                    continue
                args = pr.info.get("cmdline") or []
                if any(os.path.normcase(os.path.abspath(x)) == target for x in args if x.endswith(".ts")):
                    pr.kill()
                    self.log(f"Ancienne capture FFmpeg arrêtée (processus {pr.pid}).")
        except Exception:
            pass

    @property
    def audio_name(self):
        a = self.audio
        return a.name if a else ""

    def audio_silent_for(self):
        a = self.audio
        return a.silent_for() if a else 0.0

    @property
    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    def error_tail(self):
        return "\n".join(self._stderr)

    # --- lecture des sorties de FFmpeg ------------------------------------
    def _read_progress(self):
        p = self.proc
        try:
            for raw in p.stdout:
                line = raw.decode("utf-8", "ignore").strip()
                if line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    if us > 0:
                        self._offs.append(time.time() - us / 1e6)
        except Exception:
            pass

    def _read_stderr(self):
        p = self.proc
        try:
            for raw in p.stderr:
                self._stderr.append(raw.decode("utf-8", "ignore").rstrip())
        except Exception:
            pass

    def offset(self):
        """Heure murale correspondant au temps 0 du flux (les retards ne font qu'augmenter l'écart : on prend le min)."""
        return min(self._offs) if self._offs else None

    def segments(self):
        """Liste [(fichier, début, fin)] en temps du flux, dans l'ordre d'écriture."""
        path = os.path.join(self.buf_dir, "liste.csv")
        with self._lock:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(self._seg_pos)
                    chunk = f.read()
                    # ne garder que les lignes complètes
                    cut = chunk.rfind("\n") + 1
                    self._seg_pos += len(chunk[:cut].encode("utf-8"))
                for line in chunk[:cut].splitlines():
                    parts = line.strip().split(",")
                    if len(parts) >= 3:
                        try:
                            self._seg_lines.append((parts[0], float(parts[1]), float(parts[2])))
                        except ValueError:
                            pass
                keep = int(self.buffer_s / SEG_S) + 4
                self._seg_lines = self._seg_lines[-keep:]
            except OSError:
                pass
            return list(self._seg_lines)

    # --- sauvegarde de passages -----------------------------------------
    def save(self, windows, out_path, done):
        """Sauvegarde un ou plusieurs passages [[debut, fin], …] (heures murales) dans un seul MP4, en tâche
        de fond. done(chemin | None, vers_fichier, erreur) ; vers_fichier(heure murale) -> seconde dans le fichier."""
        self.pending += 1
        threading.Thread(target=self._save, args=(windows, out_path, done), daemon=True).start()

    def _save(self, windows, out_path, done):
        try:
            done(*self._save_inner(windows, out_path))
        except Exception as e:
            done(None, None, str(e))
        finally:
            self.pending -= 1

    def _save_inner(self, windows, out_path):
        windows = sorted([float(a), float(b)] for a, b in windows)
        wall_b = max(b for _, b in windows)
        # attendre que le morceau qui contient la fin soit écrit
        deadline = max(wall_b, time.time()) + SEG_S * 3 + 6
        segs, off = [], None
        while True:
            off = self.offset()
            segs = self.segments()
            if off is not None and segs and segs[-1][2] >= wall_b - off:
                break
            if time.time() > deadline or not self.running:
                break
            time.sleep(0.25)
        if off is None or not segs:
            return None, None, "aucune image enregistrée (capture arrêtée ?)"
        # morceaux qui touchent au moins un passage, dans l'ordre, sans doublon
        chosen, seen = [], set()
        for s in segs:
            key = round(s[1], 2)
            if key in seen:
                continue
            if any(s[2] > a - off and s[1] < b - off for a, b in windows):
                chosen.append(s)
                seen.add(key)
        if not chosen:
            return None, None, "passage plus ancien que le tampon"
        tmp = out_path + ".parts"
        os.makedirs(tmp, exist_ok=True)
        lst = os.path.join(tmp, "liste.txt")
        table, pos = [], 0.0   # (début flux, fin flux, position dans le fichier)
        with open(lst, "w", encoding="utf-8") as f:
            for i, (name, s0, s1) in enumerate(chosen):
                dst = os.path.join(tmp, f"{i:03d}.ts")
                shutil.copyfile(os.path.join(self.buf_dir, name), dst)
                f.write(f"file '{dst.replace(os.sep, '/')}'\nduration {s1 - s0:.6f}\n")
                table.append((s0, s1, pos))
                pos += s1 - s0
        ff.run([ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                "-i", lst, "-c", "copy", "-bsf:a", "aac_adtstoasc", "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart", out_path], timeout=120, check=True)
        shutil.rmtree(tmp, ignore_errors=True)

        def to_file(wall):
            st = wall - off
            best = min(table, key=lambda r: 0 if r[0] <= st <= r[1] else min(abs(st - r[0]), abs(st - r[1])))
            s0, s1, p = best
            return round(p + min(max(st, s0), s1) - s0, 3)
        return out_path, to_file, ""
