# -*- coding: utf-8 -*-
"""
Mises à jour automatiques, comme HexaRise : HexaClip regarde la dernière version publiée sur GitHub
au démarrage puis toutes les 4 h, la télécharge en fond, vérifie qu'elle se lance, puis propose
« Redémarrer ». Le remplacement se fait sur place (même dossier, même nom) : rien à retélécharger
à la main. Si tu quittes HexaClip avec une mise à jour prête, elle est installée en sortant.

Sous Windows, un .exe en cours d'exécution peut être renommé (pas supprimé) : l'ancien devient
HexaClip.ancien.exe, le nouveau prend sa place, et l'ancien est effacé au lancement suivant.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import paths

REPO = "HexaLabsFR/HexaClip"          # dépôt où sont publiées les versions (Releases)
ASSET = "HexaClip.exe"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
FROZEN = bool(getattr(sys, "frozen", False))
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
DETACHED = 0x00000008 if sys.platform == "win32" else 0


def parse(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def newer(a, b):
    """True si la version a est plus récente que b ('v0.10.0' > '0.9.3')."""
    pa, pb = parse(a), parse(b)
    n = max(len(pa), len(pb))
    return pa + (0,) * (n - len(pa)) > pb + (0,) * (n - len(pb))


class Updater:
    def __init__(self, current, on_change=None, log=print):
        self.current = current
        self.on_change = on_change
        self.log = log
        self.state = "idle"        # idle, checking, downloading, ready, uptodate, error
        self.message = "Vérification au démarrage puis toutes les 4 h."
        self.version = None
        self.file = None
        self.lock = threading.Lock()
        self.last_check = 0.0

    def _set(self, state, message, **kw):
        self.state, self.message = state, message
        for k, v in kw.items():
            setattr(self, k, v)
        if self.on_change:
            try:
                self.on_change(self)
            except Exception:
                pass

    # --- recherche + téléchargement ---------------------------------------
    def check(self, download=True):
        if not self.lock.acquire(blocking=False):
            return
        try:
            if self.state == "ready":
                return
            self.last_check = time.time()
            self._set("checking", "Recherche de mise à jour…")
            try:
                req = urllib.request.Request(API, headers={"User-Agent": "HexaClip", "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    rel = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self._set("error", "Mises à jour indisponibles : aucune version publique trouvée (dépôt privé ?).")
                elif e.code == 403:
                    self._set("error", "GitHub limite les vérifications, nouvel essai plus tard.")
                else:
                    self._set("error", f"Mise à jour impossible (GitHub {e.code}).")
                return
            except Exception as e:
                self._set("error", f"Mise à jour impossible : pas de connexion ({getattr(e, 'reason', e)}).")
                return
            tag = rel.get("tag_name", "")
            if not newer(tag, self.current):
                self._set("uptodate", f"Tu as la dernière version ({self.current}).")
                return
            asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET), None)
            if not asset:
                self._set("error", f"La version {tag} n'a pas de {ASSET}.")
                return
            v = ".".join(str(x) for x in parse(tag))
            if not download:
                self._set("available", f"Version {v} disponible.", version=v)
                return
            self._download(asset, v)
        finally:
            self.lock.release()

    def _download(self, asset, v):
        folder = paths.data("maj")
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, f"HexaClip-{v}.exe")
        size = int(asset.get("size") or 0)
        if not (os.path.isfile(dst) and os.path.getsize(dst) == size):
            part = dst + ".part"
            try:
                req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "HexaClip"})
                with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                    done = 0
                    last = 0.0
                    while True:
                        chunk = r.read(1 << 18)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 0.5:
                            last = time.time()
                            pct = f"{done * 100 // size} %" if size else f"{done / 1e6:.0f} Mo"
                            self._set("downloading", f"Téléchargement de la version {v} : {pct}", version=v)
                os.replace(part, dst)
            except Exception as e:
                self._set("error", f"Téléchargement de la mise à jour impossible : {e}")
                return
        with open(dst, "rb") as f:
            ok = f.read(2) == b"MZ" and (not size or os.path.getsize(dst) == size)
        if not ok:
            os.remove(dst)
            self._set("error", "Mise à jour reçue abîmée, nouvel essai plus tard.")
            return
        if sys.platform == "win32" and not self._runs(dst, v):
            self._set("error", f"La version {v} ne démarre pas correctement : elle n'est pas installée.")
            return
        for old in os.listdir(folder):     # anciennes versions téléchargées
            if old.endswith(".exe") and old != os.path.basename(dst):
                try:
                    os.remove(os.path.join(folder, old))
                except OSError:
                    pass
        self._set("ready", f"Version {v} prête : clique sur Redémarrer pour l'installer.", version=v, file=dst)

    def _runs(self, exe, v):
        out = paths.data("maj", "verif.txt")
        try:
            subprocess.run([exe, "--version", "--out", out], timeout=120, creationflags=NO_WINDOW)
            with open(out, encoding="utf-8") as f:
                return v in f.read()
        except Exception as e:
            self.log(f"Vérification de la mise à jour : {e}")
            return False

    # --- installation -----------------------------------------------------
    def install(self, restart=True):
        """Remplace l'exe en cours par la nouvelle version. Renvoie True si c'est fait."""
        if self.state != "ready" or not self.file or not os.path.isfile(self.file):
            return False
        if not FROZEN:
            self._set("error", "Mode développement : mise à jour non installée (lance l'exe).")
            return False
        exe = sys.executable
        old = exe[:-4] + ".ancien.exe"
        try:
            if os.path.exists(old):
                os.remove(old)
            os.rename(exe, old)
        except OSError as e:
            self._set("error", f"Installation impossible dans {os.path.dirname(exe)} ({e}). "
                               f"Range HexaClip.exe dans un dossier à toi (ex. Documents).")
            return False
        try:
            shutil.copy2(self.file, exe)
        except OSError as e:
            try:
                os.rename(old, exe)       # remettre l'ancienne version en place
            except OSError:
                pass
            self._set("error", f"Installation impossible ({e}).")
            return False
        try:
            os.remove(self.file)
        except OSError:
            pass
        self.log(f"Mise à jour {self.current} -> {self.version} installée.")
        if restart:
            subprocess.Popen([exe, "--apres-maj"], creationflags=DETACHED, close_fds=True,
                             cwd=os.path.dirname(exe))
        return True


def cleanup_after_update():
    """Efface l'ancien exe laissé par la mise à jour précédente (au démarrage)."""
    if not FROZEN:
        return
    old = sys.executable[:-4] + ".ancien.exe"
    for _ in range(20):
        if not os.path.exists(old):
            return
        try:
            os.remove(old)
            return
        except OSError:
            time.sleep(0.5)    # l'ancienne version finit de se fermer
