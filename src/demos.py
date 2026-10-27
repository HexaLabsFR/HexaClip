# -*- coding: utf-8 -*-
"""
Clips à partir d'une démo CS2 (Faceit, matchmaking…).

1. Analyse : l'outil analyse_demo (Go, livré dans HexaClip) lit la démo, même compressée (.dem.zst de
   Faceit), et donne la map, les joueurs, les rounds et chaque kill avec son tick.
2. Lecture : CS2 est piloté par sa console réseau (option de lancement -netconport). Pour chaque
   groupe de kills choisi : pause, saut au bon tick, caméra verrouillée sur le joueur, reprise.
3. Enregistrement : la capture habituelle de HexaClip tourne pendant la lecture ; les passages des
   kills sont gardés comme pour une partie en direct (mêmes clips, mêmes miniatures, onglet Montage).

L'heure de chaque kill est calculée depuis la reprise de la lecture ; si CS2 envoie aussi les kills
par la liaison (GSI) pendant la démo, ce sont ces heures-là qui sont prises (plus précises).
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time

import paths
import ffmpeg_tools as ff
from gsi_cs2 import pretty_map
from moments import kill_type, score

IS_WIN = sys.platform == "win32"
NETCON_PORT = 2121
STEAM_ID_BASE = 76561197960265728
CS2_APPID = "730"
SETTLE_S = 3.0          # temps laissé à CS2 après un saut dans la démo
GAP_SPLIT_S = 12.0      # au-delà, deux kills du même round sont lus séparément (saut entre les deux)
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
    return hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


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
            tot += (b - a) / float(tickrate or 64) + SETTLE_S + 1.0
    return tot + 40          # chargement de la démo


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
# Console réseau de CS2
# ----------------------------------------------------------------------------
class Netcon:
    def __init__(self, port=NETCON_PORT):
        self.port = port
        self.sock = None
        self.lines = []
        self._lock = threading.Lock()

    def connect(self, timeout=2.0):
        try:
            s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        except OSError:
            return False
        s.settimeout(None)
        self.sock = s
        threading.Thread(target=self._read, daemon=True).start()
        return True

    def _read(self):
        buf = b""
        try:
            while self.sock:
                data = self.sock.recv(4096)
                if not data:
                    break
                buf += data
                *done, buf = buf.split(b"\n")
                with self._lock:
                    self.lines.extend(x.decode("utf-8", "ignore").rstrip() for x in done)
                    self.lines = self.lines[-400:]
        except OSError:
            pass
        self.sock = None

    def send(self, cmd):
        if not self.sock:
            raise DemoError("CS2 ne répond plus.")
        try:
            self.sock.sendall((cmd + "\n").encode("utf-8"))
        except OSError:
            self.sock = None
            raise DemoError("CS2 ne répond plus.")

    @property
    def alive(self):
        return self.sock is not None

    def close(self):
        s, self.sock = self.sock, None
        if s:
            try:
                s.close()
            except OSError:
                pass


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


def launch_cs2(port=NETCON_PORT):
    """Lance CS2 avec la console réseau ouverte (pour cette fois seulement)."""
    exe = steam_exe()
    args = ["-applaunch", CS2_APPID, "-netconport", str(port), "-novid"]
    if exe:
        subprocess.Popen([exe] + args, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return True
    if IS_WIN:
        os.startfile("steam://rungameid/730")     # sans options : il faudra l'option de lancement
    return False


def kill_cs2():
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() == "cs2.exe":
                p.terminate()
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Lecture + enregistrement
# ----------------------------------------------------------------------------
class DemoClipper:
    """Pilote CS2 et appelle engine._on_moment pour chaque groupe de kills, comme en direct."""

    def __init__(self, engine, info, steamid, rounds, log=print):
        self.e = engine
        self.info = info
        self.steamid = str(steamid)
        self.rounds = rounds
        self.log = log
        self.nc = Netcon()
        self.cancelled = False
        self.state = "préparation"
        self.done_n = 0
        self.total = sum(len(segments(r["kills"], info.get("tickrate"), 0, 0)) for r in rounds)
        # avancement : étapes de départ (fixes) puis temps de lecture des passages
        tr = float(info.get("tickrate") or 64)
        pre, post = float(engine.cfg["avant_s"]), float(engine.cfg["apres_s"])
        self.play_total = sum((b - a) / tr + SETTLE_S for r in rounds
                              for a, b, _ in segments(r["kills"], tr, pre, post)) or 1.0
        self.play_done = 0.0
        self._seg_start = None
        self._seg_len = 0.0
        self._t_start = time.time()
        self.error = ""
        self._gsi_kills = []          # heures murales des kills vus par la liaison CS2 pendant la lecture
        self._last_rk = None
        self.gsi_map = ""
        self.session = "demo-" + os.path.basename(info.get("fichier", "demo")).split(".")[0][-24:]

    # --- liaison CS2 pendant la démo -------------------------------------
    def on_gsi(self, payload, t):
        mp = payload.get("map") or {}
        if mp.get("name"):
            self.gsi_map = mp["name"]
        pl = payload.get("player") or {}
        if str(pl.get("steamid", "")) != self.steamid:
            self._last_rk = None
            return
        rk = (pl.get("state") or {}).get("round_kills")
        if isinstance(rk, int):
            if self._last_rk is not None and rk > self._last_rk:
                self._gsi_kills.extend([t] * (rk - self._last_rk))
            self._last_rk = rk

    # --- avancement (barre de chargement) ----------------------------------
    STEPS = {"préparation": 0.0, "préparation de la démo": 0.01, "lancement de CS2": 0.03,
             "chargement de la démo": 0.08}
    PLAY_FROM = 0.12

    def progress(self):
        """(fraction 0..1, secondes restantes estimées ou None)."""
        if self.state == "terminé":
            return 1.0, 0
        if self.state in self.STEPS:
            return self.STEPS[self.state], None
        cur = 0.0
        if self._seg_start is not None:
            cur = min(self._seg_len, time.time() - self._seg_start)
        done = min(self.play_total, self.play_done + cur)
        frac = self.PLAY_FROM + (1 - self.PLAY_FROM) * done / self.play_total
        return min(0.99, frac), max(0, int(self.play_total - done))

    # --- déroulé -----------------------------------------------------------
    def cancel(self):
        self.cancelled = True

    def _check(self):
        if self.cancelled:
            raise DemoError("Arrêté.")

    def _sleep(self, s):
        end = time.time() + s
        while time.time() < end:
            self._check()
            time.sleep(0.05)

    def _connect_cs2(self):
        if self.nc.connect():
            return
        if self.e.cs2_running or self.e._cs2_alive():
            raise DemoError("CS2 est ouvert sans le mode démo. Ferme CS2 : HexaClip le relancera lui-même.")
        self.state = "lancement de CS2"
        if not launch_cs2():
            raise DemoError("Steam introuvable. Ajoute « -netconport 2121 » aux options de lancement de CS2, "
                            "lance CS2 puis réessaie.")
        end = time.time() + 240
        while time.time() < end:
            self._sleep(2)
            if self.nc.connect():
                self._sleep(8)             # laisser le menu finir de charger
                return
        raise DemoError("CS2 ne s'est pas ouvert en mode démo (vérifie Steam).")

    def run(self):
        e, tr = self.e, float(self.info.get("tickrate") or 64)
        pre, post = float(e.cfg["avant_s"]), float(e.cfg["apres_s"])
        try:
            self.state = "préparation de la démo"
            dem = prepare_dem(self.info["fichier"])
            self._connect_cs2()
            # la capture tourne pendant la lecture (même si la capture auto est coupée)
            if not e.recorder.running:
                if not e.method or not e.start_recording():
                    raise DemoError("La capture ne démarre pas (voir Accueil).")
            self.state = "chargement de la démo"
            self.nc.send(f'playdemo "{dem}"')
            want = self.info.get("carte", "")
            end = time.time() + 120
            while time.time() < end and self.gsi_map != want:
                self._sleep(1)
            self._sleep(6 if self.gsi_map == want else 4)
            for cmd in ("demo_timescale 1", "cl_draw_only_deathnotices 1", "spec_show_xray 0",
                        "cl_spec_show_bindings 0", "demo_pause"):
                self.nc.send(cmd)
            acc = int(self.steamid) - STEAM_ID_BASE if self.steamid.isdigit() else 0
            nom = next((j["nom"] for j in self.info.get("joueurs", []) if j["steamid"] == self.steamid), "")
            for r in self.rounds:
                self._check()
                times = []
                for a, b, ks in segments(r["kills"], tr, pre, post):
                    self._check()
                    self.state = f"round {r['round']} ({self.done_n + 1}/{self.total})"
                    self._seg_len = (b - a) / tr + SETTLE_S
                    self._seg_start = time.time()
                    self.nc.send("demo_pause")
                    self.nc.send(f"demo_gototick {a}")
                    self.nc.send("demo_pause")
                    self._sleep(SETTLE_S * 0.5)
                    if acc:
                        self.nc.send(f"spec_lock_to_accountid {acc}")
                    if nom:
                        self.nc.send(f'spec_player "{nom}"')
                    self._sleep(SETTLE_S * 0.5)
                    self._gsi_kills, self._last_rk = [], None
                    w0 = time.time()
                    self.nc.send("demo_resume")
                    self._sleep((b - a) / tr)
                    self.nc.send("demo_pause")
                    calc = [w0 + (k["tick"] - a) / tr for k in ks]
                    seen = [t for t in self._gsi_kills if w0 <= t <= w0 + (b - a) / tr + 1]
                    ok = len(seen) == len(ks) and all(abs(x - y) < 1.5 for x, y in zip(seen, calc))
                    times += seen if ok else calc
                    self.done_n += 1
                    self.play_done += self._seg_len
                    self._seg_start = None
                self._clip(r, r["kills"], times)      # un clip par round, comme en direct
            self.state = "terminé"
            self.nc.send("cl_draw_only_deathnotices 0")
        except DemoError as ex:
            self.error = str(ex)
            self.state = "arrêté" if self.cancelled else "erreur"
        except Exception as ex:          # pragma: no cover
            self.error = f"{type(ex).__name__} : {ex}"
            self.state = "erreur"
        finally:
            try:
                if self.nc.alive:
                    self.nc.send("cl_draw_only_deathnotices 0")
            except DemoError:
                pass
            self.nc.close()
            cleanup()

    def _clip(self, r, ks, times):
        hs = [bool(k.get("hs")) for k in ks]
        span = times[-1] - times[0] if times else 0
        m = {
            "cree": times[0],
            "carte": pretty_map(self.info.get("carte", "")),
            "round": r["round"],
            "mode": "démo",
            "kills": len(ks),
            "hs": sum(hs),
            "duree_kills": round(span, 2),
            "type": kill_type(len(ks)),
            "score": score(len(ks), sum(hs), span),
            "kills_abs": times,
            "kills_hs": hs,
            "partie": 0,
            "raison": "démo",
            "session": self.session,
        }
        self.e._on_moment(m)
