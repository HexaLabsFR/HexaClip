# -*- coding: utf-8 -*-
"""
Intégration officielle CS2 : Game State Integration (GSI) de Valve.

CS2 envoie lui-même, en local, l'état de la partie (map, round, tes kills du round, tes stats du match)
à http://127.0.0.1:PORT. Aucune lecture de la mémoire du jeu, rien qui touche à l'anti-triche.
HexaClip utilise son propre port et son propre fichier : il cohabite avec HexaTune.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 3472
CFG_NAME = "gamestate_integration_hexaclip.cfg"
CFG_CONTENT = f'''"HexaClip"
{{
    "uri"       "http://127.0.0.1:{PORT}"
    "timeout"   "1.0"
    "buffer"    "0.0"
    "throttle"  "0.1"
    "heartbeat" "10.0"
    "data"
    {{
        "provider"            "1"
        "map"                 "1"
        "round"               "1"
        "player_id"           "1"
        "player_state"        "1"
        "player_match_stats"  "1"
    }}
}}
'''
STALE_S = 25


def pretty_map(name: str) -> str:
    """de_nuke -> Nuke, de_dust2 -> Dust2, cs_office -> Office."""
    if not name:
        return ""
    base = re.sub(r"^(de|cs|ar|dz|gd|fy|aim|awp)_", "", name.lower())
    return " ".join(w.capitalize() for w in base.split("_"))


class GSIServer:
    """Reçoit les messages de CS2 et les passe à on_payload(payload, heure_reception)."""

    def __init__(self, on_payload=None, port=PORT):
        self.on_payload = on_payload
        self.port = port
        self.error = ""
        self.httpd = None
        self.last_t = 0.0
        self.count = 0
        self.last = {}

    def start(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                t = time.time()
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    payload = json.loads(self.rfile.read(n).decode("utf-8", "ignore") or "{}")
                except Exception:
                    payload = None
                self.send_response(200)
                self.end_headers()
                if payload is not None:
                    srv.last_t, srv.last = t, payload
                    srv.count += 1
                    if srv.on_payload:
                        try:
                            srv.on_payload(payload, t)
                        except Exception:
                            pass

            def log_message(self, *a):
                pass

        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as e:
            self.error = f"port {self.port} occupé ({e})"
            return False
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return True

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()

    def alive(self):
        return self.count > 0 and time.time() - self.last_t < STALE_S


# ----------------------------------------------------------------------------
# Installation du fichier .cfg dans CS2
# ----------------------------------------------------------------------------
def _steam_roots():
    roots = []
    if sys.platform == "win32":
        import winreg
        for hive, path, val in [(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")]:
            try:
                with winreg.OpenKey(hive, path) as k:
                    roots.append(os.path.normpath(winreg.QueryValueEx(k, val)[0]))
            except OSError:
                pass
        roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    return list(dict.fromkeys(r for r in roots if os.path.isdir(r)))


def _libraries(root):
    libs = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
            for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                libs.append(os.path.normpath(m.group(1).replace("\\\\", "\\")))
    except OSError:
        pass
    return list(dict.fromkeys(libs))


def find_cs2_cfg_dir():
    for root in _steam_roots():
        for lib in _libraries(root):
            d = os.path.join(lib, "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg")
            if os.path.isdir(d):
                return d
    return None


def cfg_installed():
    d = find_cs2_cfg_dir()
    return bool(d and os.path.isfile(os.path.join(d, CFG_NAME)))


def install_cfg(target_dir=None):
    """Renvoie (ok, message)."""
    d = target_dir or find_cs2_cfg_dir()
    if not d:
        return False, "Dossier de CS2 introuvable : choisis le dossier ...\\game\\csgo\\cfg à la main."
    try:
        with open(os.path.join(d, CFG_NAME), "w", encoding="utf-8") as f:
            f.write(CFG_CONTENT)
    except OSError as e:
        return False, f"Écriture impossible dans {d} : {e}"
    return True, f"Liaison CS2 installée dans {d}. Redémarre CS2 si le jeu est ouvert."
