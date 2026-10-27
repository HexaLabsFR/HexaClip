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
    # Détection des kills
    "kills_min": 1,                  # 1 = tous les kills (choix ensuite dans Montage), 3 = 3k et plus
    "avant_s": 3.5,                  # secondes gardées avant le premier kill
    "apres_s": 1.8,                  # secondes gardées après le dernier kill
    "touche_manuelle": "F9",         # sauvegarde à la main des dernières secondes
    "duree_manuelle_s": 15,
    # Compils TikTok
    "compil_auto": False,            # compil auto à la fermeture de CS2 (sinon : choix des kills dans Montage)
    "compil_duree_s": 60,
    "compil_ordre": "crescendo",     # crescendo (meilleure à la fin) ou chrono
    "compil_cadrage": "flou",        # flou (jeu entier + fond flou) ou zoom (centre plein écran)
    "compil_badges": True,           # 3K / 4K / ACE à l'écran
    "pseudo": "",                    # ton pseudo : intro, plaque en haut des clips, écran de fin
    "compil_style": "spectre",       # spectre (dégradé + viseur, identité du pseudo) ou hexaclip (menthe)
    "compil_intro": True,            # intro animée avec l'accroche (ex. « ACE SUR MIRAGE »)
    "compil_transitions": True,      # transitions entre les passages
    "compil_trans_lib": ["fondu", "flash", "droite", "gauche", "haut", "poussee", "zoom", "flou", "signal",
                         "balayage"],  # transitions tirées au hasard (bibliothèque, voir transitions.py)
    "compil_trans_ouverture": "auto",  # après l'intro : auto = pack si fichier, sinon balayage
    "compil_pack_fichier": "",       # transition du pack Twitch (vidéo .webm/.mov/.mp4, transparence acceptée)
    "compil_pack_coupe": 0.0,        # seconde de la coupe dans ce fichier (0 = milieu)
    "compil_pack_cadrage": "plein",  # plein (remplit l'écran vertical) ou logo (moins de zoom, bords prolongés)
    "compil_appel": "Abonne-toi pour la suite",   # texte de l'écran de fin (vide = rien)
    "compil_texte": "",              # texte affiché en haut (vide = rien)
    "compil_logo": "",               # PNG perso (vide = logo HexaClip)
    "compil_fin_faceit": True,       # écran de fin avec l'Elo de la session
    "musique_on": False,
    "musique_dossier": "",           # MP3/WAV libres de droits
    "musique_volume": 0.25,
    "garder_clips_jours": 7,         # les favoris sont toujours gardés
    # Windows
    "demarrage_windows": False,
    "premier_lancement_fait": False,
    # Mises à jour
    "maj_auto": True,                # vérifier et télécharger les nouvelles versions tout seul
    "version_reglages": 2,
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
        except (OSError, ValueError):
            return
        for k, val in saved.items():
            if k in DEFAULTS and isinstance(val, type(DEFAULTS[k])) or (
                    k in DEFAULTS and isinstance(DEFAULTS[k], float) and isinstance(val, int)):
                self.v[k] = val
        if int(saved.get("version_reglages", 1)) < 2:
            # 0.2 : tous les kills sont gardés et tu choisis toi-même ceux de la compil
            self.v["kills_min"] = 1
            self.v["compil_auto"] = False
            self.v["garder_clips_jours"] = 7
            self.v["version_reglages"] = 2
            try:
                self.save()
            except OSError:
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
