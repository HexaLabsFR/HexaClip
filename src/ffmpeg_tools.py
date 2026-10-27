# -*- coding: utf-8 -*-
"""
FFmpeg : le moteur vidéo de HexaClip (capture, découpe, montage).

Il n'est pas livré dans l'exe (trop lourd) : au premier lancement, HexaClip le télécharge une fois
depuis les builds Windows officiels de FFmpeg-Builds (GitHub) et le range dans
Documents\\Hexa Labs\\HexaClip\\ffmpeg. Un FFmpeg déjà installé (PATH) est utilisé s'il convient.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

import paths

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0   # CREATE_NO_WINDOW : pas de console noire qui clignote
UA = {"User-Agent": "HexaClip (Hexa Labs)"}
RELEASES_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"
FALLBACK_URLS = [
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]


def _exe(name):
    return name + ".exe" if IS_WIN else name


def local_dir():
    return paths.data("ffmpeg")


def find(name="ffmpeg"):
    """Chemin de ffmpeg / ffprobe : variable HEXACLIP_FFMPEG, copie locale, sinon PATH."""
    env = os.environ.get("HEXACLIP_FFMPEG")
    if env:
        p = os.path.join(env, _exe(name))
        if os.path.isfile(p):
            return p
    p = os.path.join(local_dir(), _exe(name))
    if os.path.isfile(p):
        return p
    return shutil.which(name)


def available():
    return bool(find("ffmpeg") and find("ffprobe"))


def run(args, timeout=None, check=False, capture=True, stdin=None):
    """Lance un programme sans fenêtre console. Renvoie CompletedProcess."""
    kw = dict(timeout=timeout, creationflags=NO_WINDOW)
    if capture:
        kw.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if stdin is not None:
        kw["stdin"] = stdin
    r = subprocess.run(args, **kw)
    if check and r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "ignore")[-1500:]
        raise RuntimeError(f"{os.path.basename(args[0])} a échoué ({r.returncode}) :\n{err}")
    return r


def popen(args, **kw):
    kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.Popen(args, **kw)


# ----------------------------------------------------------------------------
# Téléchargement au premier lancement
# ----------------------------------------------------------------------------
def _pick_asset_url():
    """Le build Windows 64 bits « GPL » le plus récent d'une version stable, sinon master."""
    try:
        req = urllib.request.Request(RELEASES_API, headers={**UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            assets = json.loads(r.read().decode("utf-8")).get("assets", [])
        best, best_v = None, ()
        for a in assets:
            m = re.match(r"^ffmpeg-n(\d+)\.(\d+).*-win64-gpl-[\d.]+\.zip$", a.get("name", ""))
            if m and (int(m.group(1)), int(m.group(2))) > best_v:
                best, best_v = a["browser_download_url"], (int(m.group(1)), int(m.group(2)))
        if best:
            return [best] + FALLBACK_URLS
    except Exception:
        pass
    return list(FALLBACK_URLS)


def download(progress=None):
    """Télécharge et installe ffmpeg, ffprobe et ffplay (aperçus) dans le dossier de données.
    progress(fraction, texte)."""
    os.makedirs(local_dir(), exist_ok=True)
    zpath = os.path.join(local_dir(), "ffmpeg.zip")
    last_err = None
    for url in _pick_asset_url():
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done / total if total else 0.0,
                                 f"Téléchargement du moteur vidéo… {done / 1e6:.0f} Mo" +
                                 (f" / {total / 1e6:.0f} Mo" if total else ""))
            with zipfile.ZipFile(zpath) as z:
                found = 0
                for n in z.namelist():
                    base = os.path.basename(n).lower()
                    if base in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"):
                        target = os.path.join(local_dir(), base)
                        try:
                            with z.open(n) as src, open(target, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            found += 1
                        except OSError:
                            # déjà présent et en cours d'utilisation (capture lancée) : on garde l'actuel
                            if os.path.isfile(target):
                                found += 1
            os.remove(zpath)
            if found >= 2:
                if progress:
                    progress(1.0, "Moteur vidéo installé.")
                return True
            last_err = "archive sans ffmpeg.exe"
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Téléchargement de FFmpeg impossible ({last_err}). Vérifie ta connexion.")


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------
def probe(path):
    """-> {"duree", "w", "h", "fps", "son"} d'un fichier vidéo."""
    r = run([find("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            timeout=30, check=True)
    d = json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
    out = {"duree": float((d.get("format") or {}).get("duration") or 0), "w": 0, "h": 0, "fps": 0.0, "son": False}
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and not out["w"]:
            out["w"], out["h"] = int(s.get("width", 0)), int(s.get("height", 0))
            num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
            try:
                out["fps"] = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                pass
        elif s.get("codec_type") == "audio":
            out["son"] = True
    return out


def filter_path(p):
    """Chemin utilisable dans un filtre FFmpeg (drawtext fontfile=, movie=) sous Windows et Linux."""
    p = os.path.abspath(p).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'")


def filter_text(t):
    """Texte sûr pour drawtext text='…'."""
    return (t.replace("\\", "\\\\").replace("'", "’").replace(":", "\\:").replace("%", "\\%")
            .replace(",", "\\,").replace(";", "\\;").replace("[", "\\[").replace("]", "\\]"))


def encoders():
    """Encodeurs H.264 compilés dans ce FFmpeg."""
    try:
        r = run([find("ffmpeg"), "-hide_banner", "-encoders"], timeout=20)
        txt = r.stdout.decode("utf-8", "ignore")
        return [e for e in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264") if re.search(rf"\b{e}\b", txt)]
    except Exception:
        return []


def has_filter(name):
    try:
        r = run([find("ffmpeg"), "-hide_banner", "-filters"], timeout=20)
        return bool(re.search(rf"\s{re.escape(name)}\s", r.stdout.decode("utf-8", "ignore")))
    except Exception:
        return False


def has_decoder(name):
    try:
        r = run([find("ffmpeg"), "-hide_banner", "-decoders"], timeout=20)
        return bool(re.search(rf"\s{re.escape(name)}\s", r.stdout.decode("utf-8", "ignore")))
    except Exception:
        return False
