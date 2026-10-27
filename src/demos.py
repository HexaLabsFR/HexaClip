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


# ----------------------------------------------------------------------------
# Pilotage de CS2 sous Windows : fichier .cfg + touche Arrêt défil
# ----------------------------------------------------------------------------
# La console réseau (-netconport) ne marche pas dans CS2 sous Windows (sauf avec les outils du Workshop).
# HexaClip lance donc CS2 avec « +exec hexaclip_demo » : ce fichier associe la touche Arrêt défil
# (Scroll Lock, jamais utilisée en jeu) à « exec hexaclip_cmd ». Pour envoyer des commandes, HexaClip
# écrit hexaclip_cmd.cfg puis appuie sur cette touche dans la fenêtre de CS2.
KEY_NAME = "SCROLLLOCK"
CMD_CFG, INIT_CFG, DEMO_NAME = "hexaclip_cmd", "hexaclip_demo", "hexaclip_demo"


def press_key():
    """Appuie sur Arrêt défil dans la fenêtre de CS2 (mise au premier plan si besoin)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    def send(vk, scan, flags):
        i = INPUT(type=1)
        i.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))

    hwnd = user32.FindWindowW(None, "Counter-Strike 2")
    if hwnd and user32.GetForegroundWindow() != hwnd:
        send(0x12, 0, 0)               # Alt : autorise la mise au premier plan
        send(0x12, 0, 2)
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
    send(0x91, 0x46, 0x8)              # Arrêt défil (code de touche matériel), appui
    time.sleep(0.04)
    send(0x91, 0x46, 0x8 | 0x2)        # relâché
    return bool(hwnd)


class CfgConsole:
    """Envoie des commandes à CS2 par hexaclip_cmd.cfg + touche."""

    def __init__(self, cfg_dir):
        self.cfg_dir = cfg_dir
        self.alive = True

    def send(self, *cmds):
        path = os.path.join(self.cfg_dir, CMD_CFG + ".cfg")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(cmds) + "\n")
            f.flush()
            os.fsync(f.fileno())
        press_key()
        time.sleep(0.12)

    def close(self):
        # la touche reste associée tant que CS2 tourne : on y laisse une commande sans effet
        try:
            with open(os.path.join(self.cfg_dir, CMD_CFG + ".cfg"), "w", encoding="utf-8") as f:
                f.write("cl_draw_only_deathnotices 0\n")
        except OSError:
            pass


class NetconConsole:
    """Console réseau (CS2 lancé avec -netconport et les outils du Workshop, ou tests)."""

    def __init__(self, nc):
        self.nc = nc

    @property
    def alive(self):
        return self.nc.alive

    def send(self, *cmds):
        for c in cmds:
            self.nc.send(c)

    def close(self):
        self.nc.close()


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


_launched = {"pid": None}          # CS2 lancé par HexaClip en mode démo (touche déjà associée)


def cs2_pid():
    try:
        import psutil
        return next((p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == "cs2.exe"),
                    None)
    except Exception:
        return None


def cs2_in_demo_mode():
    pid = cs2_pid()
    return bool(pid) and pid == _launched["pid"]


def place_demo(dem, csgo_dir):
    """Copie la démo dans le dossier du jeu sous un nom libre (l'ancienne peut être encore ouverte par CS2)."""
    import shutil
    for name in (DEMO_NAME, DEMO_NAME + "_b"):
        target = os.path.join(csgo_dir, name + ".dem")
        try:
            if os.path.isfile(target):
                os.remove(target)
            shutil.copyfile(dem, target)
            return name
        except OSError:
            continue
    raise DemoError("Impossible de copier la démo dans le dossier de CS2.")


def write_init_cfg(cfg_dir, settings=(), name=DEMO_NAME):
    lines = [f'bind "{KEY_NAME}" "exec {CMD_CFG}"', *settings, f"playdemo {name}"]
    with open(os.path.join(cfg_dir, INIT_CFG + ".cfg"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def launch_cs2_demo():
    """Lance CS2 en exécutant hexaclip_demo.cfg (association de la touche + lecture de la démo)."""
    exe = steam_exe()
    args = ["-applaunch", CS2_APPID, "-novid", "+exec", INIT_CFG]
    if not exe:
        return False
    subprocess.Popen([exe] + args, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return True


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
        self.gsi_player = ""
        self.session = "demo-" + os.path.basename(info.get("fichier", "demo")).split(".")[0][-24:]

    # --- liaison CS2 pendant la démo -------------------------------------
    def on_gsi(self, payload, t):
        mp = payload.get("map") or {}
        if mp.get("name"):
            self.gsi_map = mp["name"]
        pl = payload.get("player") or {}
        if pl.get("steamid"):
            self.gsi_player = str(pl["steamid"])
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
             "chargement de la démo": 0.06}
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

    SETTINGS = ("demo_timescale 1", "cl_draw_only_deathnotices 1", "spec_show_xray 0")

    def _wait_map(self, want, timeout):
        end = time.time() + timeout
        while time.time() < end and self.gsi_map != want:
            self._sleep(1)
        return self.gsi_map == want

    def _open_cs2(self, dem):
        """Prépare le pilotage de CS2 et lance la lecture de la démo. Renvoie la console."""
        want = self.info.get("carte", "")
        # 1) console réseau disponible (CS2 lancé avec les outils du Workshop, ou tests)
        if self.nc.connect():
            ctl = NetconConsole(self.nc)
            self.state = "chargement de la démo"
            ctl.send(f'playdemo "{dem}"')
            self._wait_map(want, 120)
            return ctl
        if not IS_WIN:
            raise DemoError("Lecture des démos disponible sous Windows uniquement.")
        from gsi_cs2 import find_cs2_cfg_dir
        cfg_dir = find_cs2_cfg_dir()
        if not cfg_dir:
            raise DemoError("Dossier de CS2 introuvable.")
        if cs2_alive() and not cs2_in_demo_mode():
            raise DemoError("CS2 est ouvert sans le mode démo. Ferme CS2 : HexaClip le relancera lui-même.")
        # la démo est copiée dans le dossier du jeu (CS2 lit les démos de son dossier csgo)
        name = place_demo(dem, os.path.dirname(cfg_dir))
        ctl = CfgConsole(cfg_dir)
        write_init_cfg(cfg_dir, self.SETTINGS, name)
        self.state = "chargement de la démo"
        # 2) CS2 déjà ouvert par une démo précédente : la touche est déjà associée
        if cs2_alive():
            self.gsi_map = ""
            ctl.send(f"exec {INIT_CFG}")
            if self._wait_map(want, 60):
                return ctl
            raise DemoError("CS2 ne répond pas : ferme CS2, HexaClip le relancera en mode démo.")
        # 3) lancement de CS2 avec « +exec hexaclip_demo »
        self.state = "lancement de CS2"
        if not launch_cs2_demo():
            raise DemoError("Steam introuvable.")
        end = time.time() + 120
        while time.time() < end and not cs2_alive():
            self._sleep(1)
        if not cs2_alive():
            raise DemoError("CS2 ne s'est pas lancé (vérifie Steam).")
        _launched["pid"] = cs2_pid()
        self.state = "chargement de la démo"
        if not self._wait_map(want, 240):
            self.log("Démo : la liaison CS2 n'a pas confirmé le chargement, on continue.")
        return ctl

    def run(self):
        e, tr = self.e, float(self.info.get("tickrate") or 64)
        pre, post = float(e.cfg["avant_s"]), float(e.cfg["apres_s"])
        ctl = None
        try:
            self.state = "préparation de la démo"
            dem = prepare_dem(self.info["fichier"])
            ctl = self._open_cs2(dem)
            # la capture tourne pendant la lecture (même si la capture auto est coupée)
            end = time.time() + 20
            while not e.recorder.running and time.time() < end:
                self._sleep(1)
            if not e.recorder.running and (not e.method or not e.start_recording()):
                raise DemoError("La capture ne démarre pas (voir Accueil).")
            self._sleep(5)                       # fin du chargement (textures, son)
            ctl.send(*self.SETTINGS, "demo_pause")
            acc = int(self.steamid) - STEAM_ID_BASE if self.steamid.isdigit() else 0
            nom = next((j["nom"] for j in self.info.get("joueurs", []) if j["steamid"] == self.steamid), "")
            cam = [f"spec_lock_to_accountid {acc}"] if acc else []
            if nom:
                cam.append(f'spec_player "{nom}"')
            for r in self.rounds:
                self._check()
                times = []
                for a, b, ks in segments(r["kills"], tr, pre, post):
                    self._check()
                    self.state = f"round {r['round']} ({self.done_n + 1}/{self.total})"
                    self._seg_len = (b - a) / tr + SETTLE_S
                    self._seg_start = time.time()
                    ctl.send("demo_pause", f"demo_gototick {a}", "demo_pause")
                    self._sleep(SETTLE_S * 0.5)
                    if cam:
                        ctl.send(*cam)
                    self._sleep(SETTLE_S * 0.5)
                    if self.gsi_player and self.gsi_player != self.steamid and cam:
                        ctl.send(*cam)                     # caméra pas encore sur le joueur : on insiste
                        self._sleep(0.5)
                    self._gsi_kills, self._last_rk = [], None
                    w0 = time.time()
                    ctl.send("demo_resume")
                    self._sleep((b - a) / tr)
                    ctl.send("demo_pause")
                    calc = [w0 + (k["tick"] - a) / tr for k in ks]
                    seen = [t for t in self._gsi_kills if w0 <= t <= w0 + (b - a) / tr + 1]
                    ok = len(seen) == len(ks) and all(abs(x - y) < 1.5 for x, y in zip(seen, calc))
                    times += seen if ok else calc
                    self.done_n += 1
                    self.play_done += self._seg_len
                    self._seg_start = None
                self._clip(r, r["kills"], times)      # un clip par round, comme en direct
            self.state = "terminé"
        except DemoError as ex:
            self.error = str(ex)
            self.state = "arrêté" if self.cancelled else "erreur"
        except Exception as ex:          # pragma: no cover
            self.error = f"{type(ex).__name__} : {ex}"
            self.state = "erreur"
        finally:
            try:
                if ctl is not None and ctl.alive:
                    ctl.send("cl_draw_only_deathnotices 0", "demo_pause")
            except Exception:
                pass
            if ctl is not None:
                ctl.close()
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
