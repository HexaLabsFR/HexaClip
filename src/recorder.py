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

import collections
import os
import shutil
import sys
import threading
import time

import ffmpeg_tools as ff

SEG_S = 2
IS_WIN = sys.platform == "win32"


# ----------------------------------------------------------------------------
# Son du PC (WASAPI loopback)
# ----------------------------------------------------------------------------
class AudioPump:
    """Lit le son de la sortie par défaut de Windows et l'écrit en PCM 16 bits dans un flux."""

    def __init__(self):
        self.rate, self.ch = 48000, 2
        self.ok = False
        self.error = ""
        self._p = self._stream = None
        self._stop = threading.Event()
        self.name = ""

    def open(self):
        if not IS_WIN:
            self.error = "son PC : Windows uniquement"
            return False
        try:
            import pyaudiowpatch as pyaudio
            p = pyaudio.PyAudio()
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not spk.get("isLoopbackDevice"):
                for lb in p.get_loopback_device_info_generator():
                    if spk["name"] in lb["name"]:
                        spk = lb
                        break
                else:
                    raise RuntimeError("aucune sortie son en boucle trouvée")
            self.rate = int(spk["defaultSampleRate"])
            self.ch = max(1, int(spk["maxInputChannels"]))
            self._stream = p.open(format=pyaudio.paInt16, channels=self.ch, rate=self.rate, input=True,
                                  input_device_index=spk["index"], frames_per_buffer=1024)
            self._p, self.name, self.ok = p, spk["name"], True
            return True
        except Exception as e:
            self.error = f"son PC indisponible ({e})"
            return False

    def pump(self, out):
        """Boucle de copie vers out (stdin de FFmpeg) jusqu'à stop()."""
        try:
            while not self._stop.is_set():
                data = self._stream.read(1024, exception_on_overflow=False)
                out.write(data)
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        for fn in (lambda: self._stream.stop_stream(), lambda: self._stream.close(), lambda: self._p.terminate()):
            try:
                fn()
            except Exception:
                pass


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
                pump = AudioPump()
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
        t_end = time.time() + wait_pending
        while self.pending and time.time() < t_end:
            time.sleep(0.2)
        if self.audio:
            self.audio.stop()
            self.audio = None
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                if p.stdin:
                    p.stdin.close()
            except Exception:
                pass
            p.terminate()
            try:
                p.wait(5)
            except Exception:
                p.kill()

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

    # --- sauvegarde d'un passage ------------------------------------------
    def save(self, wall_a, wall_b, out_path, done):
        """Sauvegarde [wall_a, wall_b] (heures murales) dans out_path, en tâche de fond.
        done(chemin | None, debut_fichier_mural, erreur)."""
        self.pending += 1
        threading.Thread(target=self._save, args=(wall_a, wall_b, out_path, done), daemon=True).start()

    def _save(self, wall_a, wall_b, out_path, done):
        try:
            res = self._save_inner(wall_a, wall_b, out_path)
            done(*res)
        except Exception as e:
            done(None, 0.0, str(e))
        finally:
            self.pending -= 1

    def _save_inner(self, wall_a, wall_b, out_path):
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
            return None, 0.0, "aucune image enregistrée (capture arrêtée ?)"
        sa, sb = wall_a - off, wall_b - off
        chosen = [s for s in segs if s[2] > sa and s[1] < sb]
        if not chosen:
            return None, 0.0, "passage plus ancien que le tampon"
        # garder une suite continue (le tampon tourne : un même nom peut revenir)
        run = [chosen[-1]]
        for s in reversed(chosen[:-1]):
            if abs(s[2] - run[0][1]) < 0.6:
                run.insert(0, s)
            else:
                break
        tmp = out_path + ".parts"
        os.makedirs(tmp, exist_ok=True)
        lst = os.path.join(tmp, "liste.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for i, (name, _, _) in enumerate(run):
                dst = os.path.join(tmp, f"{i:03d}.ts")
                shutil.copyfile(os.path.join(self.buf_dir, name), dst)
                f.write(f"file '{dst.replace(os.sep, '/')}'\n")
        ff.run([ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                "-i", lst, "-c", "copy", "-bsf:a", "aac_adtstoasc", "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart", out_path], timeout=120, check=True)
        shutil.rmtree(tmp, ignore_errors=True)
        return out_path, run[0][1] + off, ""
