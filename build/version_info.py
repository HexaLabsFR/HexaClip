# -*- coding: utf-8 -*-
"""Écrit les informations de version de HexaClip.exe (visibles dans Propriétés > Détails)."""
import sys

v = sys.argv[1]
parts = [int(x) for x in v.split(".")] + [0, 0, 0]
t = tuple(parts[:4])
sys.stdout.reconfigure(encoding="utf-8")
print(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', 'Hexa Labs'),
      StringStruct('FileDescription', 'HexaClip — clips CS2, compils TikTok et suivi Faceit'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'HexaClip'),
      StringStruct('LegalCopyright', '© Hexa Labs'),
      StringStruct('OriginalFilename', 'HexaClip.exe'),
      StringStruct('ProductName', 'HexaClip'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [0x040C, 1200])])
  ]
)""")
