# -*- coding: utf-8 -*-
"""Réglages de HexaClip (fichier reglages.json dans le dossier de données)."""
from __future__ import annotations

import json
import os
import threading

import paths

DEFAULTS = {
    # Faceit
    "faceit_pseudo": "",
    "faceit_cle_api": "",
    # Capture
    "dossier_videos": "",            # vide = Vidéos\HexaClip
    "capture_auto": True,            # enregistre dès que CS2 est lancé
    "ecran": 0,                      # numéro d'écran capturé (0 = principal)
    "fps": 60,
    "qualite": 21,                   # plus bas = plus beau et plus lourd (18-28)
    "encodeur": "auto",              # auto, h264_nvenc, h264_amf, h264_qsv, libx264
    "son": True,
    "decalage_son_ms": 0,            # à ajuster si le son est en avance (+) ou en retard (-)
    "tampon_s": 180,                 # durée gardée en arrière sur le disque
    # Détection des actions
    "kills_min": 3,                  # 3 = 3k et plus
    "avant_s": 3.5,                  # secondes gardées avant le premier kill
    "apres_s": 1.8,                  # secondes gardées après le dernier kill
    "touche_manuelle": "F9",         # sauvegarde à la main des dernières secondes
    "duree_manuelle_s": 15,
    # Compils TikTok
    "compil_auto": True,             # compil à la fermeture de CS2 s'il y a des actions
    "compil_duree_s": 60,
    "compil_ordre": "crescendo",     # crescendo (meilleure à la fin) ou chrono
    "compil_cadrage": "flou",        # flou (jeu entier + fond flou) ou zoom (centre plein écran)
    "compil_badges": True,           # 3K / 4K / ACE à l'écran
    "compil_texte": "",              # texte affiché en haut (vide = rien)
    "compil_logo": "",               # PNG perso (vide = logo HexaClip)
    "compil_fin_faceit": True,       # écran de fin avec l'Elo de la session
    "musique_on": False,
    "musique_dossier": "",           # MP3/WAV libres de droits
    "musique_volume": 0.25,
    "garder_clips_jours": 30,
    # Windows
    "demarrage_windows": False,
    "premier_lancement_fait": False,
}

ENCODEURS = ["auto", "h264_nvenc", "h264_amf", "h264_qsv", "libx264"]


class Config:
    def __init__(self, path=None):
        self.path = path or paths.data("reglages.json")
        self.lock = threading.Lock()
        self.v = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k, val in saved.items():
                if k in DEFAULTS and isinstance(val, type(DEFAULTS[k])) or (
                        k in DEFAULTS and isinstance(DEFAULTS[k], float) and isinstance(val, int)):
                    self.v[k] = val
        except (OSError, ValueError):
            pass

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.v, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def __getitem__(self, k):
        return self.v.get(k, DEFAULTS.get(k))

    def __setitem__(self, k, val):
        self.v[k] = val

    def video_dir(self):
        d = self["dossier_videos"] or paths.default_video_dir()
        os.makedirs(d, exist_ok=True)
        return d

    def sub(self, name):
        d = os.path.join(self.video_dir(), name)
        os.makedirs(d, exist_ok=True)
        return d
