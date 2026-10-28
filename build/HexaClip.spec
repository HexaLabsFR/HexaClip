# -*- mode: python ; coding: utf-8 -*-
# Fabrication de HexaClip.exe (un seul fichier, sans console) : pyinstaller --noconfirm --clean build/HexaClip.spec
# FFmpeg n'est pas inclus : HexaClip le télécharge au premier lancement.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")

a = Analysis(
    [os.path.join(SRC, "hexaclip.py")],
    pathex=[SRC],
    datas=[(os.path.join(SRC, "assets"), "assets")]
          + ([(os.path.join(SRC, "bin"), "bin")] if os.path.isdir(os.path.join(SRC, "bin")) else []),
    hiddenimports=["paths", "config", "db", "engine", "faceit", "ffmpeg_tools", "gsi_cs2", "moments", "montage",
                   "recorder", "selftest", "updater", "brand", "intro", "spectre", "transitions", "apercu", "demos", "overlay_fin", "pystray._win32", "pyaudiowpatch", "audioop", "array"],
    excludes=["matplotlib", "scipy", "pandas", "numpy", "IPython", "pytest", "setuptools", "pip", "cairosvg"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HexaClip",
    console=False,
    icon=os.path.join(SRC, "assets", "hexaclip.ico"),
    version=os.path.join(SPECPATH, "version_info.txt"),
    upx=False,
    runtime_tmpdir=None,
)
