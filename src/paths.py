# -*- coding: utf-8 -*-
"""
Emplacements de HexaClip.

- RES_DIR   : fichiers livrés avec l'outil (dans l'exe : dossier temporaire de PyInstaller).
- DATA_DIR  : réglages, base de données, journal, FFmpeg. Sous Windows :
              Documents\\Hexa Labs\\HexaClip (suit la redirection OneDrive de « Documents »).
- VIDEO_DIR : clips et compils (gros fichiers). Par défaut Vidéos\\HexaClip, modifiable dans les réglages.
Variable HEXACLIP_DATA : dossier de données forcé (tests, développement).
"""
from __future__ import annotations

import os
import sys

APP = "HexaClip"
BRAND = "Hexa Labs"
FROZEN = bool(getattr(sys, "frozen", False))
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", SRC_DIR)
IS_WIN = sys.platform == "win32"


def _known_folder(guid_parts):
    if not IS_WIN:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]
        d1, d2, d3, d4 = guid_parts
        fid = GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(p)) == 0:
            path = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            return path
    except Exception:
        pass
    return None


# FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
_DOCS = (0xFDD39AD0, 0x238F, 0x46AF, (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
# FOLDERID_Videos {18989B1D-99B5-455B-841C-AB7C74E4DDFC}
_VIDEOS = (0x18989B1D, 0x99B5, 0x455B, (0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC))


def _data_dir():
    env = os.environ.get("HEXACLIP_DATA")
    if env:
        return env
    if IS_WIN:
        docs = _known_folder(_DOCS) or os.path.join(os.path.expanduser("~"), "Documents")
        return os.path.join(docs, BRAND, APP)
    return os.path.join(os.path.expanduser("~"), ".hexaclip")


def default_video_dir():
    env = os.environ.get("HEXACLIP_DATA")
    if env:
        return os.path.join(env, "videos")
    if IS_WIN:
        vids = _known_folder(_VIDEOS) or os.path.join(os.path.expanduser("~"), "Videos")
        return os.path.join(vids, APP)
    return os.path.join(_data_dir(), "videos")


DATA_DIR = _data_dir()
os.makedirs(DATA_DIR, exist_ok=True)


def res(*parts):
    return os.path.join(RES_DIR, *parts)


def data(*parts):
    return os.path.join(DATA_DIR, *parts)


def font_file(weight="Bold"):
    p = res("assets", f"SpaceGrotesk-{weight}.ttf")
    return p if os.path.isfile(p) else None


def load_brand_font():
    """Charge Space Grotesk (police de la charte) pour ce processus uniquement. Renvoie le nom de famille."""
    if not IS_WIN:
        return None
    try:
        import ctypes
        ok = False
        for w in ("Regular", "Bold"):
            p = font_file(w)
            if p and ctypes.windll.gdi32.AddFontResourceExW(p, 0x10, 0):  # FR_PRIVATE
                ok = True
        return "Space Grotesk" if ok else None
    except Exception:
        return None
