# -*- coding: utf-8 -*-
"""
Bibliothèque de transitions des compils.

Deux familles :
- « fondus » : les deux clips se chevauchent (xfade de FFmpeg) : fondu, décalage à droite, zoom…
- « habillages » : une animation passe par-dessus et la coupe se fait dessous (signal TV perdu,
  balayage du style, transition du pack Twitch fournie en fichier vidéo).

À chaque compil, HexaClip tire les transitions au hasard parmi celles que tu as cochées, sans jamais
répéter la même deux fois de suite. La première (après l'intro) peut être imposée (ex. celle du pack).
"""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class T:
    id: str
    nom: str
    famille: str        # xfade | habillage
    effet: str          # nom xfade, ou source de l'habillage (signal, balayage, pack)
    duree: float = 0.0  # pour les xfade
    son: str = "leger"  # aucun | leger | whoosh | grésillement | fichier


LIB = [
    T("fondu", "Fondu", "xfade", "fade", 0.35, "aucun"),
    T("fondu_noir", "Fondu au noir", "xfade", "fadeblack", 0.4, "aucun"),
    T("flash", "Flash blanc", "xfade", "fadewhite", 0.3, "leger"),
    T("droite", "Décalage à droite", "xfade", "slideright", 0.3, "leger"),
    T("gauche", "Décalage à gauche", "xfade", "slideleft", 0.3, "leger"),
    T("haut", "Décalage vers le haut", "xfade", "slideup", 0.3, "leger"),
    T("poussee", "Poussée douce", "xfade", "smoothleft", 0.35, "leger"),
    T("zoom", "Zoom avant", "xfade", "zoomin", 0.35, "leger"),
    T("flou", "Flou de vitesse", "xfade", "hblur", 0.3, "leger"),
    T("pixels", "Pixels", "xfade", "pixelize", 0.4, "aucun"),
    T("cercle", "Ouverture en cercle", "xfade", "circleopen", 0.4, "leger"),
    T("rideau", "Rideau", "xfade", "horzopen", 0.35, "leger"),
    T("diagonale", "Diagonale", "xfade", "diagtr", 0.35, "leger"),
    T("ecrase", "Écrasement", "xfade", "squeezeh", 0.3, "leger"),
    T("radar", "Radar", "xfade", "radial", 0.4, "leger"),
    T("signal", "Signal TV perdu", "habillage", "signal", son="grésillement"),
    T("balayage", "Balayage du style", "habillage", "balayage", son="whoosh"),
    T("pack", "Transition du pack (fichier vidéo)", "habillage", "pack", son="fichier"),
]
BY_ID = {t.id: t for t in LIB}
PAR_DEFAUT = ["fondu", "flash", "droite", "gauche", "haut", "poussee", "zoom", "flou", "signal", "balayage"]


def ouverture_effective(choix, pack_ok):
    """Transition après l'intro : « auto » = celle du pack si un fichier est donné, sinon le balayage."""
    if choix in BY_ID and (choix != "pack" or pack_ok):
        return choix
    return "pack" if pack_ok else "balayage"


def plan(n, actives, ouverture=None, graine=None):
    """Liste de n transitions (ids). ouverture : la première, si on en impose une."""
    pool = [t for t in actives if t in BY_ID] or ["fondu"]
    rnd = random.Random(graine)
    out = []
    for k in range(n):
        if k == 0 and ouverture:
            out.append(ouverture)
            continue
        recent = out[-2:] if len(pool) > 2 else out[-1:]
        choix = [t for t in pool if t not in recent] or pool
        out.append(rnd.choice(choix))
    return out


# ----------------------------------------------------------------------------
# Aperçu : une vidéo qui montre chaque transition cochée, avec son nom
# ----------------------------------------------------------------------------
def catalogue(out_path, opts, ids, log=print):
    """Cartes colorées enchaînées avec chaque transition de ids. opts : style, pack_file, pack_cut."""
    import os
    from PIL import Image, ImageDraw
    import ffmpeg_tools as ff
    import montage
    th = montage.theme_module(opts.get("style", "spectre"))
    work = os.path.join(os.path.dirname(out_path), ".catalogue")
    os.makedirs(work, exist_ok=True)
    W, H = 1080, 1920
    try:
        title = th.title_font(84) if hasattr(th, "title_font") else th.font(84)
        small = th.text_font(46) if hasattr(th, "text_font") else th.font(46, "Regular")
    except Exception:
        title = small = None
    bg_a = th.background() if hasattr(th, "background") else th.glow_background()
    bg_b = th.gradient(W, H, 150) if hasattr(th, "gradient") else Image.new("RGB", (W, H), (46, 230, 168))
    cards = []
    for k in range(len(ids) + 1):
        img = (bg_a if k % 2 == 0 else bg_b).copy().convert("RGB")
        d = ImageDraw.Draw(img)
        if k < len(ids):
            name = BY_ID[ids[k]].nom
            top = f"{k + 1} / {len(ids)}"
            for txt, f, y in ((top, small, 700), (name, title, 800), ("transition suivante", small, 960)):
                if f is None:
                    continue
                tw = d.textlength(txt, font=f)
                d.text(((W - tw) / 2, y), txt, font=f, fill=(255, 255, 255))
        else:
            tw = d.textlength("FIN", font=title) if title else 0
            if title:
                d.text(((W - tw) / 2, 820), "FIN", font=title, fill=(255, 255, 255))
        png = os.path.join(work, f"carte{k}.png")
        img.save(png)
        mp4 = os.path.join(work, f"carte{k}.mp4")
        ff.run([ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "60",
                "-t", "1.6", "-i", png, "-f", "lavfi", "-t", "1.6", "-i", "anullsrc=r=48000:cl=stereo",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", mp4],
               timeout=120, check=True)
        cards.append({"id": k, "fichier": mp4, "coupes": [[0.0, 1.6]], "kills_t": [], "type": "manuel", "son": 1,
                      "score": 0, "vertical": True})
    o = {"style": opts.get("style", "spectre"), "theme": th, "badges": False, "punch": False, "encodeur": "libx264",
         "trans_actives": ids, "trans_ouverture": "", "pack_file": opts.get("pack_file", ""),
         "pack_cut": opts.get("pack_cut", 0), "logo": os.path.join(work, "vide.png")}
    Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(o["logo"])
    montage.prepare_transitions(o, th, work, log)
    o["trans_force"] = list(ids)
    cmd, total = montage.build(cards, out_path, o)
    r = ff.run(cmd, timeout=900)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "ignore")[-600:])
    return out_path


# ----------------------------------------------------------------------------
# Recherche automatique de la transition d'un pack Twitch sur le PC
# ----------------------------------------------------------------------------
VIDEO_EXT = (".webm", ".mov", ".mkv", ".mp4")


def _cut_from_readme(folder):
    """Lit « Point de transition : 700 ms » dans un LISEZ-MOI / README du pack (en secondes)."""
    import os
    import re
    for up in range(4):
        try:
            names = os.listdir(folder)
        except OSError:
            names = []
        for n in names:
            if n.lower().endswith(".txt") and re.search(r"lisez|readme|lis-moi|info", n, re.I):
                try:
                    with open(os.path.join(folder, n), encoding="utf-8", errors="ignore") as f:
                        m = re.search(r"point de transition\s*[:=]\s*(\d+(?:[.,]\d+)?)\s*(ms|s)", f.read(), re.I)
                    if m:
                        v = float(m.group(1).replace(",", "."))
                        return v / 1000 if m.group(2).lower() == "ms" else v
                except OSError:
                    pass
        folder = os.path.dirname(folder)
    return 0.0


def find_pack():
    """Cherche une transition de pack (stinger) dans Téléchargements, Bureau, Documents, Vidéos.
    Renvoie (chemin, coupe_en_s) ou (None, 0)."""
    import os
    home = os.path.expanduser("~")
    roots = [os.path.join(home, n) for n in ("Downloads", "Téléchargements", "Desktop", "Bureau", "Documents",
                                             "Videos", "Vidéos")]
    roots += [os.path.join(home, "OneDrive", n) for n in ("Desktop", "Bureau", "Documents")]
    found = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        base_depth = r.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(r):
            if dirpath.count(os.sep) - base_depth >= 4:
                dirnames[:] = []
            low_dir = dirpath.lower()
            if "hexaclip" in low_dir:
                continue
            for fn in filenames:
                low = fn.lower()
                if low.endswith(VIDEO_EXT) and ("transition" in low or "stinger" in low or
                                                ("transition" in low_dir and "pack" in low_dir)):
                    p = os.path.join(dirpath, fn)
                    score = ("pack" in low_dir) + ("twitch" in low_dir) + low.endswith(".webm")
                    found.append((score, os.path.getmtime(p), p))
    if not found:
        return None, 0.0
    found.sort(reverse=True)
    p = found[0][2]
    return p, _cut_from_readme(os.path.dirname(p))
