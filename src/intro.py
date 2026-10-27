# -*- coding: utf-8 -*-
"""
Intro animée des compils (1,7 s, 1080×1920) : les deux moitiés du logo Coupe arrivent en diagonale et
se rejoignent avec un petit choc, puis « HexaClip » et l'accroche de la vidéo (ex. « ACE SUR MIRAGE »).
Images dessinées avec Pillow, son (souffle + impact) synthétisé, assemblées par FFmpeg.
"""
from __future__ import annotations

import math
import os
import random
import struct
import subprocess
import wave

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import ffmpeg_tools as ff
import paths

W, H, FPS = 1080, 1920, 60
DUREE = 1.7
SNAP = 0.36            # instant où les moitiés se rejoignent
INK = (11, 15, 20)
MINT = (46, 230, 168)
LOGO = 430             # taille du logo à l'écran
LOGO_Y = 640           # haut du logo


def _ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def _font(size):
    return ImageFont.truetype(paths.font_file("Bold"), size)


def _background():
    bg = Image.new("RGB", (W, H), INK)
    glow = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(glow)
    cx, cy = W // 2, LOGO_Y + LOGO // 2
    for r, a in ((520, 26), (360, 34), (220, 40)):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=a)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    tint = Image.new("RGB", (W, H), MINT)
    return Image.composite(tint, bg, glow)


def _with_alpha(img, a):
    if a >= 1:
        return img
    r, g, b, al = img.split()
    return Image.merge("RGBA", (r, g, b, al.point(lambda v: int(v * a))))


def _pill(text, size):
    f = _font(size)
    tw = int(ImageDraw.Draw(Image.new("RGB", (4, 4))).textlength(text, font=f))
    asc, desc = f.getmetrics()
    padx, pady = int(size * 0.42), int(size * 0.22)
    img = Image.new("RGBA", (tw + 2 * padx, asc + desc + 2 * pady), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=int(size * 0.28), fill=MINT + (255,))
    d.text((padx, pady), text, font=f, fill=INK)
    return img


def _pseudo(text, size):
    f = _font(size)
    asc, desc = f.getmetrics()
    tw = ImageDraw.Draw(Image.new("RGB", (4, 4))).textlength(text, font=f)
    img = Image.new("RGBA", (int(tw) + 4, asc + desc + 26), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((0, 0), text, font=f, fill=(255, 255, 255))
    lw = tw * 0.42
    d.rounded_rectangle(((tw - lw) / 2, asc + desc + 12, (tw + lw) / 2, asc + desc + 22), radius=5, fill=MINT)
    return img


def _wordmark(size):
    f = _font(size)
    d0 = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    w1, w2 = d0.textlength("Hexa", font=f), d0.textlength("Clip", font=f)
    asc, desc = f.getmetrics()
    img = Image.new("RGBA", (int(w1 + w2) + 4, asc + desc), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((0, 0), "Hexa", font=f, fill=(255, 255, 255))
    d.text((w1, 0), "Clip", font=f, fill=MINT)
    return img


def frames(titre, sous_titre="", pseudo="", fondu=True):
    """Génère les images de l'intro (Pillow, RGB). pseudo : affiché à la place de « HexaClip »."""
    bg = _background()
    top = Image.open(paths.res("assets", "coupe_haut.png")).convert("RGBA").resize((LOGO, LOGO), Image.LANCZOS)
    bot = Image.open(paths.res("assets", "coupe_bas.png")).convert("RGBA").resize((LOGO, LOGO), Image.LANCZOS)
    mark = _pseudo(pseudo, 112) if pseudo else _wordmark(96)
    pill = _pill(titre.upper(), 92) if titre else None
    sub_f = _font(46)
    gap = (int(5 * LOGO / 200), int(-9 * LOGO / 200))     # décalage final de la moitié haute (comme le logo)
    n = int(DUREE * FPS)
    for i in range(n):
        t = i / FPS
        img = bg.copy()
        # 1. moitiés qui arrivent en diagonale puis se calent
        p = _ease_out(t / SNAP)
        bump = 1.0
        if SNAP <= t < SNAP + 0.16:                       # petit choc à la jonction
            bump = 1 + 0.07 * math.sin((t - SNAP) / 0.16 * math.pi)
        size = int(LOGO * bump)
        x0 = (W - size) // 2
        y0 = LOGO_Y + (LOGO - size) // 2
        far = 260 * (1 - p)
        tl = top if size == LOGO else top.resize((size, size), Image.LANCZOS)
        bl = bot if size == LOGO else bot.resize((size, size), Image.LANCZOS)
        a = min(1.0, t / 0.18)
        img.paste(_with_alpha(bl, a), (int(x0 - far * 0.55), int(y0 + far)), _with_alpha(bl, a))
        img.paste(_with_alpha(tl, a), (int(x0 + gap[0] + far * 0.55), int(y0 + gap[1] - far)), _with_alpha(tl, a))
        if SNAP <= t < SNAP + 0.22:                       # éclair menthe à la jonction
            k = 1 - (t - SNAP) / 0.22
            ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rd = ImageDraw.Draw(ring)
            r = int(LOGO * (0.55 + 0.5 * (1 - k)))
            cx, cy = W // 2, LOGO_Y + LOGO // 2
            rd.ellipse((cx - r, cy - r, cx + r, cy + r), outline=MINT + (int(200 * k),), width=10)
            img.paste(ring, (0, 0), ring)
        # 2. « HexaClip » qui monte sous le logo
        if t >= 0.42:
            q = _ease_out((t - 0.42) / 0.25)
            y = int(LOGO_Y + LOGO + 70 + 40 * (1 - q))
            img.paste(_with_alpha(mark, q), ((W - mark.width) // 2, y), _with_alpha(mark, q))
        # 3. accroche dans une pastille menthe (apparition qui « pop »)
        if pill is not None and t >= 0.62:
            q = (t - 0.62) / 0.2
            sc = 0.7 + 0.4 * _ease_out(q) - 0.1 * max(0.0, min(1.0, (q - 1) * 3))
            pw, ph = int(pill.width * sc), int(pill.height * sc)
            pi = pill.resize((max(1, pw), max(1, ph)), Image.LANCZOS)
            pi = _with_alpha(pi, min(1.0, q * 2))
            img.paste(pi, ((W - pw) // 2, 1390 - ph // 2), pi)
        # 4. sous-titre
        if sous_titre and t >= 0.85:
            q = _ease_out((t - 0.85) / 0.25)
            d = ImageDraw.Draw(img)
            tw = d.textlength(sous_titre, font=sub_f)
            col = tuple(int(INK[c] + (200 - INK[c]) * q) for c in range(3))
            d.text(((W - tw) / 2, 1505), sous_titre, font=sub_f, fill=col)
        # 5. passage au jeu : fondu vers le noir sur la fin
        if fondu and t > DUREE - 0.12:
            k = (t - (DUREE - 0.12)) / 0.12
            img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), min(1.0, k))
        yield img


def _sound(path):
    """Souffle qui monte puis impact grave à la jonction (48 kHz stéréo)."""
    rate, n = 48000, int(48000 * DUREE)
    rnd = random.Random(7)
    lp = 0.0
    out = bytearray()
    for i in range(n):
        t = i / rate
        s = 0.0
        if t < SNAP:                                      # souffle filtré de plus en plus fort
            amt = (t / SNAP) ** 2
            cut = 0.02 + 0.25 * amt
            lp += cut * ((rnd.random() * 2 - 1) - lp)
            s += lp * 0.9 * amt
        else:
            dt = t - SNAP
            f = 70 - 30 * min(1.0, dt / 0.25)              # impact : sinus grave qui descend
            s += math.sin(2 * math.pi * f * dt) * math.exp(-dt * 7) * 0.75
            if dt < 0.05:                                  # clic d'attaque
                s += (rnd.random() * 2 - 1) * (1 - dt / 0.05) * 0.5
        v = int(max(-1.0, min(1.0, s * 0.55)) * 32767)
        out += struct.pack("<hh", v, v)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(out))


def render(out_path, titre, sous_titre="", pseudo="", fondu=True):
    """Écrit l'intro en MP4 (H.264 + AAC). Renvoie le chemin."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    wav = out_path + ".wav"
    _sound(wav)
    cmd = [ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
           "-i", wav, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest", out_path]
    p = ff.popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for img in frames(titre, sous_titre, pseudo, fondu):
            p.stdin.write(img.tobytes())
        p.stdin.close()
        err = p.stderr.read().decode("utf-8", "ignore")
        if p.wait(120) != 0:
            raise RuntimeError(f"Intro impossible : {err[-400:]}")
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    return out_path


def hook(items):
    """Accroche de l'intro à partir des actions de la compil : la meilleure action et sa map."""
    from moments import LABELS, kill_type
    if not items:
        return "", ""
    best = max(items, key=lambda m: (m.get("score", 0), len(m.get("kills_t") or [])))
    n = len(best.get("kills_t") or [])
    lab = LABELS.get(best.get("type")) if best.get("type") in ("3k", "4k", "ace") else \
        (LABELS.get(kill_type(n)) if n >= 3 else ("CLIP" if not n else f"{n} KILL" + ("S" if n > 1 else "")))
    titre = f"{lab} SUR {best['carte'].upper()}" if best.get("carte") else lab
    kills = sum(len(m.get("kills_t") or []) for m in items)
    sous = f"{kills} kills · {len(items)} round{'s' if len(items) > 1 else ''}" if kills else ""
    return titre, sous
