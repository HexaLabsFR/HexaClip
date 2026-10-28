# -*- coding: utf-8 -*-
"""
Clips à partir d'une démo CS2 (Faceit, matchmaking…).

1. Analyse : l'outil analyse_demo (Go, livré dans HexaClip) lit la démo, même compressée (.dem.zst de
   Faceit), et donne la map, les joueurs, les rounds et chaque kill avec son tick.
2. Rendu : CS2 est lancé avec le module de CS Demo Manager (licence MIT, AkiVer), qui exécute des
   commandes au tick près : saut au round, caméra sur le joueur, puis « startmovie » qui fait
   calculer la vidéo image par image par le jeu (60 i/s) avec son propre son (fichier WAV).
   Pas de capture d'écran : CS2 n'a pas besoin d'être au premier plan, le son du PC n'est pas mêlé,
   et la barre de lecture de la démo est masquée (demo_ui_mode 0). CS2 se ferme tout seul à la fin.
3. Encodage : les images + le son de chaque passage deviennent un clip MP4, rangé comme un clip
   enregistré en direct (onglet Montage / Clips).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time

import paths
import ffmpeg_tools as ff
from gsi_cs2 import pretty_map
from moments import kill_type, score

IS_WIN = sys.platform == "win32"
STEAM_ID_BASE = 76561197960265728
CS2_APPID = "730"
SETTLE_S = 3.0          # temps laissé à CS2 après un saut dans la démo
GAP_SPLIT_S = 12.0      # au-delà, deux kills du même round sont lus séparément (saut entre les deux)
CACHE_V = 2             # format de l'analyse (2 : identifiant de joueur dans la démo)
FPS = 60                # images par seconde du rendu
DEMO_EXT = (".dem", ".dem.zst", ".dem.gz", ".dem.bz2", ".zst", ".gz", ".bz2")


class DemoError(Exception):
    pass


# ----------------------------------------------------------------------------
# Analyse
# ----------------------------------------------------------------------------
def tool_path():
    name = "analyse_demo.exe" if IS_WIN else "analyse_demo"
    for p in (paths.res("bin", name), os.path.join(paths.SRC_DIR, "..", "outils", "analyse_demo", name),
              os.environ.get("HEXACLIP_ANALYSE_DEMO", "")):
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    return None


def _work_dir():
    d = paths.data("demos")
    os.makedirs(d, exist_ok=True)
    return d


def _key(path):
    st = os.stat(path)
    return hashlib.sha1(f"v{CACHE_V}|{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


def analyse(path, timeout=300):
    """Infos de la démo (dict), mises en cache. Lève DemoError."""
    if not os.path.isfile(path):
        raise DemoError("Fichier introuvable.")
    cache = os.path.join(_work_dir(), _key(path) + ".json")
    if os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    tool = tool_path()
    if not tool:
        raise DemoError("Outil d'analyse des démos absent de cette version.")
    r = ff.run([tool, path, cache], timeout=timeout)
    try:
        with open(cache, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        info = {}
    if r.returncode != 0 or not info.get("kills"):
        try:
            os.remove(cache)
        except OSError:
            pass
        err = info.get("erreur") or (r.stderr or b"").decode("utf-8", "ignore").strip()[-300:]
        raise DemoError("Démo illisible" + (f" : {err.splitlines()[0][:200]}" if err else "."))
    info["fichier"] = os.path.abspath(path)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(info, f)
    return info


def player_rounds(info, steamid):
    """Rounds où steamid a tué : [{round, kills:[kill…], type, hs, armes}] dans l'ordre."""
    by = {}
    for k in info.get("kills", []):
        if k.get("tueur") == steamid and not k.get("allie") and k.get("victime") != steamid:
            by.setdefault(k["round"], []).append(k)
    out = []
    for n in sorted(by):
        ks = sorted(by[n], key=lambda k: k["tick"])
        armes = []
        for k in ks:
            if k.get("arme") and k["arme"] not in armes:
                armes.append(k["arme"])
        out.append({"round": n, "kills": ks, "type": kill_type(len(ks)), "hs": sum(1 for k in ks if k.get("hs")),
                    "armes": armes})
    return out


def segments(kills, tickrate, pre, post):
    """Découpe les kills d'un round en passages à lire : [(tick_debut, tick_fin, [kills])]."""
    tr = float(tickrate or 64)
    segs = []
    for k in kills:
        if segs and (k["tick"] - segs[-1][2][-1]["tick"]) / tr <= GAP_SPLIT_S:
            segs[-1][2].append(k)
        else:
            segs.append([0, 0, [k]])
    out = []
    for s in segs:
        a = int(s[2][0]["tick"] - (pre + 1.5) * tr)
        b = int(s[2][-1]["tick"] + (post + 0.8) * tr)
        out.append((max(0, a), b, s[2]))
    return out


def estimate_s(rounds, tickrate, pre, post):
    tot = 0.0
    for r in rounds:
        for a, b, _ in segments(r["kills"], tickrate, pre, post):
            tot += (b - a) / float(tickrate or 64) * 1.3 + 4.0
    return tot + 75          # lancement de CS2 + chargement de la démo + encodage


def prepare_dem(path):
    """Chemin d'un .dem lisible par CS2 (décompressé à côté des données de HexaClip si besoin)."""
    low = path.lower()
    if low.endswith(".dem"):
        return path
    out = os.path.join(_work_dir(), _key(path) + ".dem")
    if os.path.isfile(out):
        return out
    tool = tool_path()
    if not tool:
        raise DemoError("Outil d'extraction absent.")
    r = ff.run([tool, "--extraire", path, out], timeout=600)
    if r.returncode != 0 or not os.path.isfile(out):
        raise DemoError("Décompression impossible : " + (r.stderr or b"").decode("utf-8", "ignore")[-200:])
    return out


def cleanup(max_files=3):
    """Garde seulement les dernières démos décompressées (elles pèsent ~200 Mo)."""
    d = _work_dir()
    dems = sorted((os.path.join(d, n) for n in os.listdir(d) if n.endswith(".dem")), key=os.path.getmtime,
                  reverse=True)
    for p in dems[max_files:]:
        try:
            os.remove(p)
        except OSError:
            pass


# ----------------------------------------------------------------------------
# CS2 et son dossier
# ----------------------------------------------------------------------------
PLUGIN_DIR = "hexaclip"          # dossier ajouté à CS2 (game/csgo/hexaclip) le temps du rendu
SEQ_PREFIX = "hexaclip_s"


def steam_exe():
    if not IS_WIN:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            p = winreg.QueryValueEx(k, "SteamExe")[0]
            return os.path.normpath(p) if os.path.isfile(p) else None
    except OSError:
        return None


def cs2_dirs():
    """(dossier game/csgo, cs2.exe) ou (None, None)."""
    from gsi_cs2 import find_cs2_cfg_dir
    cfg = find_cs2_cfg_dir()
    if not cfg:
        return None, None
    csgo = os.path.dirname(cfg)
    exe = os.path.join(os.path.dirname(csgo), "bin", "win64", "cs2.exe")
    return csgo, exe if os.path.isfile(exe) else None


def cs2_alive():
    try:
        import psutil
        return any((p.info.get("name") or "").lower() == "cs2.exe" for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def kill_cs2(wait=20):
    try:
        import psutil
        procs = [p for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == "cs2.exe"]
        for p in procs:
            p.terminate()
        psutil.wait_procs(procs, timeout=wait)
    except Exception:
        pass


def plugin_path():
    p = paths.res("bin", "cs2", "server.dll")
    return p if os.path.isfile(p) else None


def install_plugin(csgo):
    """Ajoute le module à CS2 (dossier + ligne dans gameinfo.gi). Le module remet gameinfo.gi d'origine dès
    son chargement, et HexaClip le fait aussi après le rendu."""
    import shutil
    src = plugin_path()
    if not src:
        raise DemoError("Module de rendu CS2 absent de cette version.")
    for sub in (("bin",), ("bin", "win64")):
        d = os.path.join(csgo, PLUGIN_DIR, *sub)
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(src, os.path.join(d, "server.dll"))
    gi = os.path.join(csgo, "gameinfo.gi")
    with open(gi, encoding="utf-8", errors="ignore") as f:
        content = f.read()
    line = f"Game\tcsgo/{PLUGIN_DIR}"
    if line not in content:
        shutil.copyfile(gi, gi + ".backup")
        new = content.replace("Game\tcsgo", f"{line}\n\t\t\tGame\tcsgo", 1)
        if new == content:
            raise DemoError("gameinfo.gi de CS2 au format inattendu.")
        with open(gi, "w", encoding="utf-8", newline="") as f:
            f.write(new)


def restore_gameinfo(csgo):
    gi = os.path.join(csgo, "gameinfo.gi")
    bak = gi + ".backup"
    try:
        if os.path.isfile(bak):
            with open(bak, "rb") as f:
                data = f.read()
            if f"csgo/{PLUGIN_DIR}".encode() not in data:
                with open(gi, "wb") as f:
                    f.write(data)
                os.remove(bak)
        else:
            with open(gi, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            line = f"Game\tcsgo/{PLUGIN_DIR}\n\t\t\t"
            if line in content:
                with open(gi, "w", encoding="utf-8", newline="") as f:
                    f.write(content.replace(line, "", 1))
    except OSError:
        pass


def plugin_log(csgo):
    return os.path.join(os.path.dirname(csgo), "bin", "win64", "csdm.log")


# ----------------------------------------------------------------------------
# Fichier de commandes au tick près (lu par le module au lancement de la démo)
# ----------------------------------------------------------------------------
SETUP = ["sv_cheats 1", "volume 1", "demo_ui_mode 0", "cl_hud_telemetry_frametime_show 0",
         "cl_hud_telemetry_net_misdelivery_show 0", "cl_hud_telemetry_ping_show 0",
         "cl_hud_telemetry_serverrecvmargin_graph_show 0", "cl_trueview_show_status 0", "r_show_build_info 0",
         "cl_demo_predict 1", "cl_draw_only_deathnotices 1", "spec_show_xray 0", "snd_mute_losefocus 0",
         "engine_no_focus_sleep 0", "tv_listen_voice_indices 0", "tv_listen_voice_indices_h 0", "host_framerate 0"]


def sequences_for(rounds, tickrate, pre, post, slot):
    """[(nom, tick_debut, tick_fin, round, [kills])] : un passage par groupe de kills proches, dans l'ordre."""
    out, n = [], 0
    for r in sorted(rounds, key=lambda r: r["round"]):
        for a, b, ks in segments(r["kills"], tickrate, pre, post):
            n += 1
            out.append((f"{SEQ_PREFIX}{n:02d}", max(2, a), b, r, ks))
    return out


def actions_json(seqs, tickrate, slot):
    """Contenu du fichier <démo>.json pour le module (format CS Demo Manager : [{"actions": [{tick, cmd}]}]).

    Une seule séquence qui avance dans la démo : saut direct d'un passage au suivant (jamais de retour au
    début), rendu image par image (host_framerate) seulement pendant les passages : les sauts et la lecture
    entre deux passages restent à vitesse normale."""
    tr = int(round(tickrate or 64))
    acts = [{"tick": 1, "cmd": c} for c in SETUP]
    cur = 1                                   # tick où se trouve la lecture avant le passage
    for i, (name, a, b, r, ks) in enumerate(seqs):
        setup = max(cur + 1, a - tr)
        if setup - cur > tr * 3:              # loin : saut direct juste avant le passage
            acts.append({"tick": cur, "cmd": f"demo_gototick {setup - 1}"})
        for t in (setup, a - 2):
            acts.append({"tick": max(setup, t), "cmd": "spec_mode 1"})
            acts.append({"tick": max(setup, t), "cmd": f"spec_player {slot}"})
        acts.append({"tick": max(setup, a - 4), "cmd": "pause_playback"})   # laisse CS2 finir de charger le décor
        acts.append({"tick": a, "cmd": f"host_framerate {FPS}"})
        acts.append({"tick": a, "cmd": f"startmovie {name} jpg wav"})
        acts.append({"tick": b, "cmd": "endmovie"})
        acts.append({"tick": b, "cmd": "host_framerate 0"})
        cur = b + 2
    acts.append({"tick": cur + 4, "cmd": "quit"})
    acts.sort(key=lambda x: x["tick"])          # tri stable : l'ordre des commandes d'un même tick est gardé
    return [{"actions": acts}]


def _movie_roots(csgo):
    return [os.path.join(csgo, PLUGIN_DIR, "movie"), os.path.join(csgo, "movie"), os.path.join(csgo, PLUGIN_DIR)]


def find_movie_files(csgo, name):
    """(images triées, fichier son ou None) écrits par « startmovie name » : CS2 les range dans des sous-dossiers
    datés (images dans csgo/hexaclip/movie/…, son dans csgo/movie/…)."""
    imgs, wav = [], None
    low_name = name.lower()
    for root in _movie_roots(csgo):
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            found = []
            for fn in files:
                low = fn.lower()
                if not low.startswith(low_name):
                    continue
                rest = low[len(low_name):]
                if low.endswith((".jpg", ".jpeg", ".tga")) and rest.split(".")[0].isdigit():
                    found.append(os.path.join(dirpath, fn))
                elif low == low_name + ".wav" and wav is None:
                    wav = os.path.join(dirpath, fn)
            if found and not imgs:
                imgs = sorted(found)
    return imgs, wav


def clear_movies(csgo):
    """Efface les images/sons de rendus HexaClip (et les dossiers datés vidés)."""
    for root in _movie_roots(csgo):
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root, topdown=False):
            for fn in files:
                if fn.lower().startswith(SEQ_PREFIX):
                    try:
                        os.remove(os.path.join(dirpath, fn))
                    except OSError:
                        pass
            if dirpath != root:
                try:
                    os.rmdir(dirpath)                 # seulement s'il est vide
                except OSError:
                    pass


def encode_movie(imgs, wav, out_path, encoder="libx264", fps=FPS):
    """Images + son de startmovie → MP4 H.264 + AAC. Renvoie la durée."""
    import re
    first = os.path.basename(imgs[0])
    m = re.match(r"^(.*?)(\d+)(\.\w+)$", first)
    if not m:
        raise DemoError("Images de la démo introuvables.")
    pattern = os.path.join(os.path.dirname(imgs[0]), f"{m.group(1)}%0{len(m.group(2))}d{m.group(3)}")
    cmd = [ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(fps),
           "-start_number", str(int(m.group(2))), "-i", pattern]
    if wav:
        cmd += ["-i", wav]
    cmd += ["-map", "0:v"] + (["-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest"] if wav else [])
    if encoder == "h264_nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    elif encoder == "h264_amf":
        cmd += ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "19", "-qp_p", "19"]
    elif encoder == "h264_qsv":
        cmd += ["-c:v", "h264_qsv", "-global_quality", "19"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    cmd += ["-pix_fmt", "yuv420p", "-g", str(fps), "-movflags", "+faststart", out_path]
    r = ff.run(cmd, timeout=1800)
    if r.returncode != 0 and encoder != "libx264":
        return encode_movie(imgs, wav, out_path, "libx264", fps)
    if r.returncode != 0:
        raise DemoError("Encodage impossible : " + (r.stderr or b"").decode("utf-8", "ignore")[-200:])
    return len(imgs) / float(fps)


# ----------------------------------------------------------------------------
# Rendu complet
# ----------------------------------------------------------------------------
class DemoClipper:
    """Fait calculer les passages choisis par CS2 puis range les clips comme des clips en direct."""

    STEPS = {"préparation": 0.0, "préparation de la démo": 0.02, "lancement de CS2": 0.05,
             "chargement de la démo": 0.08}

    def __init__(self, engine, info, steamid, rounds, log=print):
        self.e = engine
        self.info = info
        self.steamid = str(steamid)
        self.rounds = rounds
        self.log = log
        self.cancelled = False
        self.state = "préparation"
        self.error = ""
        self.tr = float(info.get("tickrate") or 64)
        self.pre, self.post = float(engine.cfg["avant_s"]), float(engine.cfg["apres_s"])
        j = next((j for j in info.get("joueurs", []) if j["steamid"] == self.steamid), {})
        self.slot = int(j.get("userid", -1)) + 1
        self.seqs = sequences_for(rounds, self.tr, self.pre, self.post, self.slot)
        self.total = len(self.seqs)
        self.done_n = 0            # passages filmés par CS2
        self.encoded_n = 0         # passages encodés
        self.session = "demo-" + os.path.basename(info.get("fichier", "demo")).split(".")[0][-24:]
        self.csgo = None
        self.proc_started = None

    # compat. ancienne liaison CS2 (plus utilisée pour le rendu)
    def on_gsi(self, payload, t):
        pass

    def cancel(self):
        self.cancelled = True

    def _check(self):
        if self.cancelled:
            raise DemoError("Arrêté.")

    def _sleep(self, s):
        end = time.time() + s
        while time.time() < end:
            self._check()
            time.sleep(0.1)

    def progress(self):
        if self.state == "terminé":
            return 1.0, 0
        if self.state in self.STEPS:
            return self.STEPS[self.state], None
        n = max(1, self.total)
        frac = 0.1 + 0.7 * self.done_n / n + 0.2 * self.encoded_n / n
        left = int(sum((b - a) / self.tr * 1.3 + 4 for _, a, b, _, _ in self.seqs[self.done_n:]) + 3 * (n - self.encoded_n))
        return min(0.99, frac), left

    def _watch_log(self, logf):
        """Compte les passages lancés par le module (lignes « Executing: startmovie »)."""
        try:
            with open(logf, encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            n = txt.count("Executed: endmovie")
            if n > self.done_n:
                self.done_n = min(n, self.total)
            if "Executing: startmovie" in txt and self.state == "chargement de la démo":
                self.state = f"rendu du passage 1/{self.total}"
            if self.done_n and self.done_n < self.total:
                self.state = f"rendu du passage {self.done_n + 1}/{self.total}"
        except OSError:
            pass

    def run(self):
        e = self.e
        csgo = None
        try:
            if not self.seqs:
                raise DemoError("Aucun round choisi.")
            if self.slot <= 0:
                raise DemoError("Joueur introuvable dans la démo.")
            self.state = "préparation de la démo"
            dem = os.path.abspath(prepare_dem(self.info["fichier"]))
            csgo, exe = cs2_dirs()
            if not csgo or not exe:
                raise DemoError("CS2 introuvable sur ce PC.")
            self.csgo = csgo
            if cs2_alive():
                raise DemoError("CS2 est ouvert : ferme-le, HexaClip le relance en mode rendu.")
            clear_movies(csgo)                      # restes d'un rendu précédent
            with open(dem + ".json", "w", encoding="utf-8") as f:
                json.dump(actions_json(self.seqs, self.tr, self.slot), f)
            install_plugin(csgo)
            logf = plugin_log(csgo)
            try:
                os.remove(logf)
            except OSError:
                pass
            self.state = "lancement de CS2"
            args = [exe, "-insecure", "-novid", "+demo_ui_mode", "0", "+playdemo", dem]
            proc = subprocess.Popen(args, cwd=os.path.dirname(exe))
            self.proc_started = time.time()
            self.state = "chargement de la démo"
            limit = time.time() + 240 + sum((b - a) / self.tr * 4 + 20 for _, a, b, _, _ in self.seqs)
            while proc.poll() is None:
                self._check()
                self._watch_log(logf)
                if time.time() > limit:
                    proc.terminate()
                    raise DemoError("Le rendu dans CS2 a pris trop de temps (arrêté).")
                time.sleep(1)
            self._watch_log(logf)
            try:
                with open(logf, encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
                if "Hooked FrameStageNotify" not in txt:
                    self.log("Démo : le module de rendu ne s'est pas accroché à CS2 (version de CS2 non prise en charge ?).")
            except OSError:
                pass
            self.done_n = self.total
            self.state = "encodage des clips"
            self._encode_all()
            clear_movies(csgo)
            self.state = "terminé"
        except DemoError as ex:
            self.error = str(ex)
            self.state = "arrêté" if self.cancelled else "erreur"
        except Exception as ex:          # pragma: no cover
            self.error = f"{type(ex).__name__} : {ex}"
            self.state = "erreur"
        finally:
            if self.cancelled and cs2_alive() and self.proc_started:
                kill_cs2()
            if self.state == "erreur" and csgo and self.encoded_n == 0 and "aucune image" not in self.error:
                try:                                 # garder ce qui a déjà été calculé
                    self._encode_all()
                    if self.encoded_n:
                        self.error += f" ({self.encoded_n} passage(s) déjà calculé(s) gardé(s))"
                    clear_movies(csgo)
                except Exception:
                    pass
            if csgo:
                restore_gameinfo(csgo)
            cleanup()

    def _encode_all(self):
        from engine import make_thumb
        import datetime as dt
        e = self.e
        enc = (e.method or {}).get("encodeur", "libx264")
        missing = 0
        by_round = {}
        for name, a, b, r, ks in self.seqs:
            by_round.setdefault(r["round"], []).append((name, a, b, r, ks))
        day = dt.datetime.now().strftime("%Y-%m-%d")
        folder = e.cfg.sub(os.path.join("clips", day))
        for rnd, parts in by_round.items():
            self._check()
            files, kills_t, coupes, pos = [], [], [], 0.0
            for name, a, b, r, ks in parts:
                imgs, wav = find_movie_files(self.csgo, name)
                if not imgs:
                    missing += 1
                    continue
                out = os.path.join(folder, f".{name}.mp4")
                dur = encode_movie(imgs, wav, out, enc)
                for p in imgs + ([wav] if wav else []):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                for k in ks:
                    t = pos + (k["tick"] - a) / self.tr
                    kills_t.append(round(t, 3))
                    coupes.append([round(max(pos, t - self.pre), 3), round(min(pos + dur, t + self.post), 3)])
                files.append(out)
                pos += dur
                self.encoded_n += 1
            if not files:
                continue
            r = parts[0][3]
            ks_all = [k for p in parts for k in p[4]]
            stem = (dt.datetime.now().strftime("%Hh%M_%S") + f"_{pretty_map(self.info.get('carte', '')) or 'Demo'}"
                    f"_R{rnd}_{kill_type(len(ks_all))}")
            final = os.path.join(folder, stem + ".mp4")
            if len(files) == 1:
                os.replace(files[0], final)
            else:
                lst = final + ".txt"
                with open(lst, "w", encoding="utf-8") as f:
                    for p in files:
                        f.write(f"file '{p.replace(os.sep, '/')}'\n")
                ff.run([ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", lst, "-c", "copy", "-movflags", "+faststart", final], timeout=300, check=True)
                for p in files + [lst]:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            hs = [bool(k.get("hs")) for k in ks_all]
            span = kills_t[-1] - kills_t[0] if kills_t else 0
            row = {
                "cree": time.time(), "session": self.session, "carte": pretty_map(self.info.get("carte", "")),
                "round": rnd, "mode": "démo", "kills": len(ks_all), "hs": sum(hs), "duree_kills": round(span, 2),
                "type": kill_type(len(ks_all)), "score": score(len(ks_all), sum(hs), span), "fichier": final,
                "coupes": coupes, "kills_t": kills_t, "son": 1, "kills_hs": hs, "partie": 0,
            }
            row["miniature"] = make_thumb(final, kills_t[-1] if kills_t else 1.0,
                                          os.path.join(folder, ".miniatures", stem + ".png"))
            e.db.add_moment(row)
        if missing == self.total:
            raise DemoError("CS2 n'a produit aucune image (module de rendu non chargé ?). Voir journal.txt.")
