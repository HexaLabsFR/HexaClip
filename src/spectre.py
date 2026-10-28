# -*- coding: utf-8 -*-
"""
Style « Spectre » : l'identité du pseudo (pack Twitch ZayKo), indépendante de la DA HexaClip.

- Police Audiowide (titres) + Rajdhani (petits textes)
- Dégradé rose #FF3DA5 → violet #8A3DFF → bleu #2F7BFF
- Viseur rouge #FF1F3D : cercle en pointillés + 4 traits, et le « O » final du pseudo en viseur
- Fond nuit #0E0B14

Mêmes pièces que brand.py : plaque pseudo, intro, transition, écran de fin, couleurs des badges.
L'écran de fin crédite l'outil : « HexaClip by Hexa Labs ».
"""
from __future__ import annotations

import math
import os
import re

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import paths
from brand import _encode_rgb, _encode_rgba, _impact_track, ease_in_out, ease_out, with_alpha

W, H, FPS = 1080, 1920, 60
BG = (14, 11, 20)
GRAD = [(255, 61, 165), (138, 61, 255), (47, 123, 255)]
VISEUR = (255, 31, 61)
WHITE = (242, 244, 247)
SOFT = (196, 190, 214)
MUTED = (128, 120, 150)

INTRO_S = 1.7
OUTRO_S = 3.2
STINGER_S = 0.36
STINGER_CUT = 0.18

SIGNAL_COLORS = (GRAD[0], GRAD[2], VISEUR)     # bandes de la transition « signal perdu »

# badges du compteur de kills (drawtext FFmpeg)
BADGE_FINAL_BOX = "0x8A3DFF@0.96"
BADGE_FINAL_TEXT = "white"
BADGE_FONT = "Audiowide-Regular.ttf"


def title_font(size):
    return ImageFont.truetype(paths.res("assets", "Audiowide-Regular.ttf"), size)


def text_font(size, bold=True):
    return ImageFont.truetype(paths.res("assets", "Rajdhani-Bold.ttf" if bold else "Rajdhani-SemiBold.ttf"), size)


# ----------------------------------------------------------------------------
# Briques graphiques
# ----------------------------------------------------------------------------
def gradient(w, h, angle=100, stops=GRAD):
    """Dégradé linéaire (angle en degrés, façon CSS : 90 = gauche → droite)."""
    a = math.radians(angle)
    dx, dy = math.sin(a), -math.cos(a)
    small_w, small_h = max(2, w // 4), max(2, h // 4)
    img = Image.new("RGB", (small_w, small_h))
    px = img.load()
    lo = min(0, dx * small_w) + min(0, dy * small_h)
    hi = max(0, dx * small_w) + max(0, dy * small_h)
    for y in range(small_h):
        for x in range(small_w):
            t = ((x * dx + y * dy) - lo) / ((hi - lo) or 1)
            seg = min(len(stops) - 2, int(t * (len(stops) - 1)))
            u = t * (len(stops) - 1) - seg
            c0, c1 = stops[seg], stops[seg + 1]
            px[x, y] = tuple(int(c0[k] + (c1[k] - c0[k]) * u) for k in range(3))
    return img.resize((w, h), Image.BILINEAR)


def gradient_text(text, font, angle=100):
    asc, desc = font.getmetrics()
    tw = int(ImageDraw.Draw(Image.new("L", (4, 4))).textlength(text, font=font)) + 4
    mask = Image.new("L", (tw, asc + desc), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=255)
    out = gradient(tw, asc + desc, angle).convert("RGBA")
    out.putalpha(mask)
    return out


def _cap_box(font):
    """Hauteur des capitales (pour centrer le viseur sur le O)."""
    bb = font.getbbox("O")
    return bb[1], bb[3]


def wordmark(pseudo, size):
    """Pseudo en majuscules, dégradé ; un O final devient un viseur rouge (comme la bannière)."""
    text = pseudo.upper()
    f = title_font(size)
    last_o = text[-1:] in ("O", "0")
    head = text[:-1] if last_o else text
    head_img = gradient_text(head, f) if head else Image.new("RGBA", (1, 1))
    asc, desc = f.getmetrics()
    if not last_o:
        return head_img
    d0 = ImageDraw.Draw(Image.new("L", (4, 4)))
    ow = int(d0.textlength("O", font=f)) + 4
    top, bot = _cap_box(f)
    ext = int(size * 0.16)                                  # les traits dépassent du O
    o = Image.new("RGBA", (ow + 2 * ext, asc + desc + 2 * ext), (0, 0, 0, 0))
    ImageDraw.Draw(o).text((ext, ext), "O", font=f, fill=VISEUR)
    cx, cy = ext + ow / 2 - 2, ext + (top + bot) / 2
    rx, ry = ow / 2, (bot - top) / 2
    gap, thin = max(4, int(size * 0.09)), max(3, int(size * 0.045))
    alpha = o.split()[3]
    cut = Image.new("L", o.size, 0)
    cd = ImageDraw.Draw(cut)
    for (x0, y0, x1, y1) in ((cx, cy - ry - ext, cx, cy - ry * 0.45), (cx, cy + ry * 0.45, cx, cy + ry + ext),
                             (cx - rx - ext, cy, cx - rx * 0.45, cy), (cx + rx * 0.45, cy, cx + rx + ext, cy)):
        cd.line((x0, y0, x1, y1), fill=255, width=gap)
    o.putalpha(ImageChops.subtract(alpha, cut))
    d = ImageDraw.Draw(o)
    for (x0, y0, x1, y1) in ((cx, cy - ry - ext, cx, cy - ry * 0.25), (cx, cy + ry * 0.25, cx, cy + ry + ext),
                             (cx - rx - ext, cy, cx - rx * 0.25, cy), (cx + rx * 0.25, cy, cx + rx + ext, cy)):
        d.line((x0, y0, x1, y1), fill=VISEUR, width=thin)
    out = Image.new("RGBA", (head_img.width + o.width - ext, max(head_img.height, o.height)), (0, 0, 0, 0))
    out.alpha_composite(head_img, (0, ext))
    out.alpha_composite(o, (head_img.width - ext, 0))
    return out


def crosshair(size, rot=0.0, dash_alpha=1.0, ticks=1.0, width=None):
    """Viseur : cercle en pointillés (4 arcs) + 4 traits, rouge."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 200
    w = width or max(2, int(6 * s))
    r = 78 * s
    c = size / 2
    col = VISEUR + (int(255 * dash_alpha),)
    for k in range(4):                                        # arcs avec un trou en face de chaque trait
        a0 = -90 + k * 90 + 12 + math.degrees(rot)
        d.arc((c - r, c - r, c + r, c + r), a0, a0 + 66, fill=col, width=w)
    if ticks > 0:
        L = 24 * s * ticks
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            x0, y0 = c + dx * (92 * s), c + dy * (92 * s)
            d.line((x0, y0, x0 - dx * L, y0 - dy * L), fill=VISEUR, width=w)
    return img


def emblem(letter, size, rot=0.0, z_scale=1.0, ticks=1.0):
    """Photo de profil Spectre : lettre en dégradé dans le viseur."""
    img = crosshair(size, rot, ticks=ticks)
    if z_scale > 0:
        f = title_font(max(8, int(size * 0.47 * z_scale)))
        z = gradient_text(letter.upper(), f, angle=135)
        top, bot = f.getbbox(letter.upper())[1], f.getbbox(letter.upper())[3]
        img.alpha_composite(z, (int((size - z.width) / 2), int(size / 2 - (top + bot) / 2)))
    return img


def background():
    bg = Image.new("RGB", (W, H), BG)
    glow = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(glow)
    for (cx, cy, r, a) in ((W * 0.25, 640, 520, 46), (W * 0.8, 1180, 560, 40)):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=a)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    tint = gradient(W, H, angle=150)
    return Image.composite(tint, bg, glow)


def gradient_pill(text, size, outline=False):
    f = title_font(size)
    tw = int(ImageDraw.Draw(Image.new("L", (4, 4))).textlength(text, font=f))
    asc, desc = f.getmetrics()
    padx, pady = int(size * 0.5), int(size * 0.32)
    w, h = tw + 2 * padx, asc + desc + 2 * pady
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=255)
    if outline:
        md.rounded_rectangle((5, 5, w - 6, h - 6), radius=h // 2 - 5, fill=0)
    img = gradient(w, h, angle=100).convert("RGBA")
    img.putalpha(mask)
    ImageDraw.Draw(img).text((padx, pady), text, font=f, fill=WHITE)
    return img


# ----------------------------------------------------------------------------
# Plaque pseudo (en haut des clips)
# ----------------------------------------------------------------------------
def nameplate(pseudo, path, height=96):
    em = emblem(pseudo[:1] or "Z", int(height * 0.92))
    wm = wordmark(pseudo, int(height * 0.42))
    pad = int(height * 0.18)
    w = pad + em.width + int(pad * 0.6) + wm.width + pad * 2
    img = Image.new("RGBA", (w + 12, height + 12), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((6, 9, w + 6, height + 9), radius=height // 2, fill=(0, 0, 0, 120))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(5)))
    ImageDraw.Draw(img).rounded_rectangle((6, 6, w + 6, height + 6), radius=height // 2, fill=BG + (225,))
    img.alpha_composite(em, (6 + pad - 4, 6 + (height - em.height) // 2))
    img.alpha_composite(wm, (6 + pad + em.width + int(pad * 0.6) - 4, 6 + (height - wm.height) // 2))
    img.save(path)
    return path


# ----------------------------------------------------------------------------
# Intro : le viseur se verrouille sur la lettre, puis pseudo et accroche
# ----------------------------------------------------------------------------
def intro_frames(titre, sous_titre="", pseudo=""):
    bg = background()
    pseudo = pseudo or "HexaClip"
    big = 470
    wm = wordmark(pseudo, 108)
    pill = gradient_pill(titre.upper(), 70) if titre else None
    f_sub = text_font(50)
    n = int(INTRO_S * FPS)
    cy = 720
    for i in range(n):
        t = i / FPS
        img = bg.copy().convert("RGBA")
        # 1. viseur qui tourne et se resserre (0 → 0,4 s), puis la lettre apparaît
        q = ease_out(t / 0.4)
        size = int(big * (1.7 - 0.7 * q))
        rot = (1 - q) * math.pi * 0.9
        zs = ease_out((t - 0.3) / 0.18) * (1 + 0.12 * math.sin(min(1.0, max(0.0, (t - 0.3) / 0.25)) * math.pi))
        em = emblem(pseudo[:1], size, rot=rot, z_scale=zs, ticks=ease_out((t - 0.25) / 0.2))
        em = with_alpha(em, min(1.0, t / 0.12))
        img.alpha_composite(em, ((W - size) // 2, cy - size // 2))
        if 0.3 <= t < 0.5:                                    # éclair rouge au verrouillage
            k = 1 - (t - 0.3) / 0.2
            fl = Image.new("RGBA", (W, H), VISEUR + (int(34 * k),))
            img.alpha_composite(fl)
        # 2. pseudo (glisse de la gauche)
        if t >= 0.48:
            q2 = ease_out((t - 0.48) / 0.28)
            x = int((W - wm.width) / 2 - 120 * (1 - q2))
            img.alpha_composite(with_alpha(wm, q2), (x, cy + big // 2 + 40))
        # 3. accroche dans une pastille dégradée
        if pill is not None and t >= 0.7:
            q3 = (t - 0.7) / 0.2
            sc = 0.75 + 0.33 * ease_out(q3) - 0.08 * min(1.0, max(0.0, (q3 - 1) * 3))
            pw, ph = max(1, int(pill.width * sc)), max(1, int(pill.height * sc))
            pi = with_alpha(pill.resize((pw, ph), Image.LANCZOS), min(1.0, q3 * 2))
            img.alpha_composite(pi, ((W - pw) // 2, 1330 - ph // 2))
        # 4. sous-titre
        if sous_titre and t >= 0.9:
            q4 = ease_out((t - 0.9) / 0.25)
            d = ImageDraw.Draw(img)
            tw = d.textlength(sous_titre.upper(), font=f_sub)
            d.text(((W - tw) / 2, 1440 + 20 * (1 - q4)), sous_titre.upper(), font=f_sub, fill=SOFT + (int(255 * q4),))
        yield img.convert("RGB")


def render_intro(path, titre, sous_titre="", pseudo="", fondu=True):
    return _encode_rgb(intro_frames(titre, sous_titre, pseudo), path, sound=_impact_track(INTRO_S, [0.3]))


# ----------------------------------------------------------------------------
# Transition : panneau dégradé qui traverse l'écran (simple et rapide)
# ----------------------------------------------------------------------------
def stinger_frames():
    panel = gradient(W + 400, H, angle=100).convert("RGBA")
    skew = 260                                               # bord en biais
    n = int(round(STINGER_S * FPS))
    for i in range(n):
        t = i / FPS
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if t <= STINGER_CUT:
            lead = W + skew - (W + 2 * skew) * ease_in_out(t / STINGER_CUT)       # entre par la droite
            trail = W + 2 * skew + 50
        else:
            lead = -skew - 50
            trail = W + skew - (W + 2 * skew) * ease_in_out((t - STINGER_CUT) / (STINGER_S - STINGER_CUT))
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        md.polygon([(lead, 0), (trail, 0), (trail - skew, H), (lead - skew, H)], fill=255)
        layer = panel.crop((200, 0, 200 + W, H))
        layer.putalpha(mask)
        img.alpha_composite(layer)
        d = ImageDraw.Draw(img)
        # liseré rouge (viseur) sur le bord qui avance
        edge = lead if t <= STINGER_CUT else trail
        d.line((edge, 0, edge - skew, H), fill=VISEUR, width=12)
        yield img


def render_stinger(path):
    if os.path.isfile(path):
        return path
    return _encode_rgba(stinger_frames(), path)


# ----------------------------------------------------------------------------
# Écran de fin : pseudo, Elo de la session, abonne-toi, crédit HexaClip by Hexa Labs
# ----------------------------------------------------------------------------
def outro_frames(pseudo, fin=None, appel=""):
    bg = background()
    pseudo = pseudo or ""
    wm = wordmark(pseudo, 118) if pseudo else None
    f_elo = title_font(150)
    f_mid = text_font(56)
    f_small = text_font(46, bold=False)
    f_credit = text_font(40)
    cta = gradient_pill(appel, 46, outline=True) if appel else None
    sym = Image.open(paths.res("assets", "symbole_72.png")).convert("RGBA").resize((48, 55), Image.LANCZOS)
    m = re.match(r"^([+-]\d[\d  ]*)\s*(.*)$", (fin or {}).get("ligne1", ""))
    delta = None
    rest = (fin or {}).get("ligne1", "")
    if m:
        delta = int(re.sub(r"\D", "", m.group(1)) or 0) * (-1 if m.group(1).startswith("-") else 1)
        rest = re.sub(r"^ELO\s*", "", m.group(2), flags=re.I)
    card = None
    if fin and fin.get("overlay") and os.path.isfile(fin["overlay"]):
        # overlay FaceitSync capturé : il remplace le bloc Elo
        try:
            card = Image.open(fin["overlay"]).convert("RGBA")
            cw = 960
            if card.width != cw:
                card = card.resize((cw, max(1, int(card.height * cw / card.width))), Image.LANCZOS)
        except Exception:
            card = None
    n = int(OUTRO_S * FPS)
    for i in range(n):
        t = i / FPS
        img = bg.copy().convert("RGBA")
        d = ImageDraw.Draw(img)
        y = 520 if card is None else 470
        if wm is not None:
            q = ease_out((t - 0.05) / 0.35)
            img.alpha_composite(with_alpha(wm, q), (int((W - wm.width) / 2), int(y + 50 * (1 - q))))
            y += wm.height + 120
        if card is not None:
            q = ease_out((t - 0.3) / 0.4)
            sc = 0.92 + 0.08 * q
            cw, ch = max(1, int(card.width * sc)), max(1, int(card.height * sc))
            img.alpha_composite(with_alpha(card.resize((cw, ch), Image.BILINEAR), q),
                                ((W - cw) // 2, int(y + 40 * (1 - q))))
            y += card.height + 140
        elif fin:
            q = ease_out((t - 0.35) / 0.3)
            if delta is not None:
                v = int(round(delta * ease_out((t - 0.35) / 0.9)))
                big = (f"+{v}" if delta > 0 else str(v)) + " ELO"
                txt = gradient_text(big, f_elo) if delta >= 0 else None
                if txt is None:
                    txt = Image.new("RGBA", (10, 10))
                    tw = d.textlength(big, font=f_elo)
                    d.text(((W - tw) / 2, y), big, font=f_elo, fill=VISEUR + (int(255 * q),))
                else:
                    img.alpha_composite(with_alpha(txt, q), (int((W - txt.width) / 2), int(y + 40 * (1 - q))))
            else:
                # pas d'écart d'Elo connu : l'Elo actuel en grand (« 1 100 ELO »), sans « + »
                big_txt = gradient_text(fin.get("ligne1", ""), f_elo) if fin.get("ligne1") else None
                if big_txt is not None:
                    if big_txt.width > W - 80:
                        big_txt = big_txt.resize((W - 80, int(big_txt.height * (W - 80) / big_txt.width)), Image.LANCZOS)
                    img.alpha_composite(with_alpha(big_txt, q), (int((W - big_txt.width) / 2), int(y + 40 * (1 - q))))
            if rest and delta is not None:
                tw = d.textlength(rest.upper(), font=f_mid)
                d.text(((W - tw) / 2, y + 190), rest.upper(), font=f_mid, fill=WHITE + (int(255 * q),))
            if fin.get("ligne2") and t > 0.75:
                q2 = ease_out((t - 0.75) / 0.35)
                tw = d.textlength(fin["ligne2"], font=f_small)
                d.text(((W - tw) / 2, y + 270 + 20 * (1 - q2)), fin["ligne2"], font=f_small,
                       fill=SOFT + (int(255 * q2),))
            y += 420
        if cta is not None and t > 1.0:
            q = (t - 1.0) / 0.25
            sc = 0.8 + 0.25 * ease_out(q) - 0.05 * min(1.0, max(0.0, (q - 1) * 3))
            if t > 1.5:
                sc *= 1 + 0.025 * math.sin((t - 1.5) * 7)
            pw, ph = max(1, int(cta.width * sc)), max(1, int(cta.height * sc))
            pi = with_alpha(cta.resize((pw, ph), Image.LANCZOS), min(1.0, q * 2))
            img.alpha_composite(pi, ((W - pw) // 2, y + 40 - ph // 2))
        # crédit de l'outil, en bas
        if t > 1.3:
            q = ease_out((t - 1.3) / 0.4)
            label_a, label_b = "HexaClip", " by Hexa Labs"
            wa, wb = d.textlength(label_a, font=f_credit), d.textlength(label_b, font=f_credit)
            total = sym.width + 16 + wa + wb
            x = (W - total) / 2
            yb = 1700 + 20 * (1 - q)
            img.alpha_composite(with_alpha(sym, q * 0.9), (int(x), int(yb - 6)))
            x += sym.width + 16
            d.text((x, yb), label_a, font=f_credit, fill=WHITE + (int(220 * q),))
            d.text((x + wa, yb), label_b, font=f_credit, fill=MUTED + (int(255 * q),))
        yield img.convert("RGB")


def render_outro(path, pseudo, fin=None, appel=""):
    return _encode_rgb(outro_frames(pseudo, fin, appel), path, sound=_impact_track(OUTRO_S, [0.05]))
