# -*- coding: utf-8 -*-
"""
Habillage des compils avec ton pseudo, dans la DA Hexa Labs (menthe #2EE6A8, encre #0B0F14,
Space Grotesk, angle de la coupe du logo) :

- plaque pseudo : pastille encre avec un hexagone menthe, en haut des clips ;
- transition : balayage diagonal (angle de la coupe) encre + liseré menthe, qui cache chaque coupe ;
- écran de fin animé : pseudo, Elo de la session qui défile, bilan, appel à s'abonner ;
- piste de « whoosh » calée sur les transitions.
Tout est dessiné avec Pillow (livré dans l'exe) puis assemblé par FFmpeg.
"""
from __future__ import annotations

import math
import os
import random
import re
import subprocess
import wave
from array import array

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import ffmpeg_tools as ff
import paths

W, H, FPS = 1080, 1920, 60
INK = (11, 15, 20)
MINT = (46, 230, 168)
SOFT = (201, 209, 219)
STINGER_S = 0.46          # durée de la transition
STINGER_CUT = 0.23        # instant de la coupe dans la transition (écran entièrement couvert)
OUTRO_S = 2.8
SLOPE = 0.5               # pente de la coupe du logo (monte vers la droite)
SIGNAL_COLORS = ((46, 230, 168), (255, 255, 255), (11, 15, 20))


def font(size, weight="Bold"):
    return ImageFont.truetype(paths.font_file(weight) or paths.font_file("Bold"), size)


def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = min(1.0, max(0.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def hexagon(cx, cy, r):
    """Hexagone pointe en haut (comme le logo)."""
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(-90, 270, 60)]


def with_alpha(img, a):
    if a >= 1:
        return img
    r, g, b, al = img.split()
    return Image.merge("RGBA", (r, g, b, al.point(lambda v: int(v * max(0.0, a)))))


def glow_background():
    bg = Image.new("RGB", (W, H), INK)
    glow = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(glow)
    cx, cy = W // 2, 860
    for r, a in ((560, 24), (380, 32), (230, 40)):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=a)
    glow = glow.filter(ImageFilter.GaussianBlur(130))
    return Image.composite(Image.new("RGB", (W, H), MINT), bg, glow)


# ----------------------------------------------------------------------------
# Plaque pseudo (filigrane des clips)
# ----------------------------------------------------------------------------
def nameplate(pseudo, path, height=92):
    f = font(int(height * 0.54))
    tw = ImageDraw.Draw(Image.new("RGB", (4, 4))).textlength(pseudo, font=f)
    pad = int(height * 0.36)
    hx = int(height * 0.25)
    w = int(pad + 2 * hx + pad * 0.7 + tw + pad)
    img = Image.new("RGBA", (w + 12, height + 12), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((6, 9, w + 6, height + 9), radius=height // 2, fill=(0, 0, 0, 110))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(5)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 6, w + 6, height + 6), radius=height // 2, fill=INK + (215,), outline=MINT + (90,), width=2)
    cx, cy = 6 + pad + hx, 6 + height // 2
    d.polygon(hexagon(cx, cy, hx), fill=MINT)
    asc, desc = f.getmetrics()
    d.text((cx + hx + pad * 0.7, 6 + (height - asc - desc) / 2 + 2), pseudo, font=f, fill=(255, 255, 255))
    img.save(path)
    return path


# ----------------------------------------------------------------------------
# Transition (balayage diagonal)
# ----------------------------------------------------------------------------
def _band(d, c0, c1, fill):
    """Bande entre deux droites parallèles à la coupe du logo : y = c - SLOPE * x."""
    if c1 <= c0:
        return
    d.polygon([(0, c0), (W, c0 - SLOPE * W), (W, c1 - SLOPE * W), (0, c1)], fill=fill)


def stinger_frames():
    cmax = H + SLOPE * W + 80          # tout en bas à droite
    span = cmax + 160
    n = int(round(STINGER_S * FPS))
    for i in range(n):
        t = i / FPS
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        lead = cmax - span * ease_in_out(t / (STINGER_CUT + 0.02))            # bord avant (monte)
        trail = cmax + 200 - (span + 260) * ease_in_out((t - STINGER_CUT + 0.02) / (STINGER_S - STINGER_CUT))
        trail = max(trail, lead) if t >= STINGER_CUT - 0.02 else cmax + 400
        _band(d, lead, trail, INK + (255,))
        _band(d, lead - 8, lead + 44, MINT + (255,))                          # liseré menthe à l'avant
        _band(d, lead + 58, lead + 66, (255, 255, 255, 200))
        if t >= STINGER_CUT - 0.02:
            _band(d, trail - 44, trail + 8, MINT + (255,))                    # liseré menthe à l'arrière
        # hexagone au centre pendant que l'écran est couvert
        cover = 1 - min(1.0, abs(t - STINGER_CUT) / 0.09)
        if cover > 0:
            r = 60 + 30 * cover
            d.polygon(hexagon(W / 2, H / 2, r), fill=MINT + (int(255 * cover),))
        yield img


def render_stinger(path):
    if os.path.isfile(path):
        return path
    return _encode_rgba(stinger_frames(), path)


# ----------------------------------------------------------------------------
# Écran de fin
# ----------------------------------------------------------------------------
def outro_frames(pseudo, fin=None, appel=""):
    bg = glow_background()
    f_ps = font(132)
    f_elo = font(150)
    f_mid = font(48)
    f_small = font(42, "Regular")
    f_cta = font(46)
    m = re.match(r"^([+-]?\d[\d  ]*)\s*(.*)$", (fin or {}).get("ligne1", ""))
    delta = int(re.sub(r"\D", "", m.group(1)) or 0) * (-1 if m and m.group(1).startswith("-") else 1) if m else None
    rest = m.group(2) if m else (fin or {}).get("ligne1", "")
    if m:
        rest = re.sub(r"^ELO\s*", "", rest, flags=re.I)        # « +75 ELO cette session » -> « cette session »
    n = int(OUTRO_S * FPS)
    for i in range(n):
        t = i / FPS
        img = bg.copy()
        d = ImageDraw.Draw(img, "RGBA")
        y = 560
        # pseudo
        if pseudo:
            q = ease_out((t - 0.08) / 0.4)
            tw = d.textlength(pseudo, font=f_ps)
            col = tuple(int(INK[k] + (255 - INK[k]) * q) for k in range(3))
            d.text(((W - tw) / 2, y + 60 * (1 - q)), pseudo, font=f_ps, fill=col)
            lw = (tw * 0.5 + 40) * ease_out((t - 0.3) / 0.35)
            if lw > 1:
                d.rounded_rectangle((W / 2 - lw / 2, y + 178, W / 2 + lw / 2, y + 188), radius=5, fill=MINT)
            r = 20
            if t > 0.3:
                d.polygon(hexagon(W / 2, y - 50, r * ease_out((t - 0.3) / 0.3)), fill=MINT)
            y += 290
        # Elo de la session qui défile
        if fin:
            q = ease_out((t - 0.45) / 0.3)
            if delta is not None:
                v = int(round(delta * ease_out((t - 0.45) / 0.8)))
                big = (f"+{v}" if delta > 0 else str(v)) + " ELO"
            else:
                big = rest
                rest = ""
            tw = d.textlength(big, font=f_elo)
            a = int(255 * q)
            d.text(((W - tw) / 2, y + 40 * (1 - q)), big, font=f_elo, fill=MINT + (a,))
            if rest:
                tw2 = d.textlength(rest, font=f_mid)
                d.text(((W - tw2) / 2, y + 185), rest, font=f_mid, fill=(255, 255, 255, a))
            if fin.get("ligne2") and t > 0.8:
                q2 = ease_out((t - 0.8) / 0.35)
                tw3 = d.textlength(fin["ligne2"], font=f_small)
                d.text(((W - tw3) / 2, y + 265 + 20 * (1 - q2)), fin["ligne2"], font=f_small,
                       fill=SOFT + (int(255 * q2),))
            y += 420
        # appel à s'abonner
        if appel and t > 1.05:
            q = (t - 1.05) / 0.25
            sc = 0.8 + 0.25 * ease_out(q) - 0.05 * min(1.0, max(0.0, (q - 1) * 3))
            pulse = 1 + 0.025 * math.sin(max(0.0, t - 1.5) * 7) if t > 1.5 else 1
            tw = d.textlength(appel, font=f_cta)
            pw, ph = (tw + 90) * sc * pulse, 104 * sc * pulse
            x0, y0 = (W - pw) / 2, y + 30 - ph / 2 + 50
            d.rounded_rectangle((x0, y0, x0 + pw, y0 + ph), radius=ph / 2, outline=MINT + (int(255 * min(1, q * 2)),),
                                width=5)
            if sc * pulse > 0.95:
                d.text(((W - tw) / 2, y0 + (ph - 58) / 2), appel, font=f_cta, fill=(255, 255, 255))
        yield img.convert("RGB")


def render_outro(path, pseudo, fin=None, appel=""):
    return _encode_rgb(outro_frames(pseudo, fin, appel), path, sound=_impact_track(OUTRO_S, [0.1]))


# ----------------------------------------------------------------------------
# Sons
# ----------------------------------------------------------------------------
def _whoosh(buf, rate, center, rnd, gain=0.5):
    """Souffle qui monte jusqu'à la coupe puis retombe vite."""
    a, b = center - 0.24, center + 0.18
    lp = bp = 0.0
    for i in range(max(0, int(a * rate)), min(len(buf) // 2, int(b * rate))):
        t = i / rate
        if t < center:
            env = ((t - a) / (center - a)) ** 2
        else:
            env = math.exp(-(t - center) * 22)
        cut = 0.03 + 0.35 * env
        lp += cut * ((rnd.random() * 2 - 1) - lp)
        bp += 0.5 * (lp - bp)
        s = (lp - bp * 0.6) * env * gain
        v = max(-32767, min(32767, buf[2 * i] + int(s * 32767)))
        buf[2 * i] = buf[2 * i + 1] = v


def whoosh_track(path, total, cuts):
    rate = 48000
    buf = array("h", bytes(4 * int(rate * total + rate)))
    rnd = random.Random(11)
    for c in cuts:
        _whoosh(buf, rate, c, rnd)
    _write_wav(path, buf, rate)
    return path


def _impact_track(duration, times):
    rate = 48000
    buf = array("h", bytes(4 * int(rate * duration)))
    for t0 in times:
        for i in range(int(t0 * rate), min(len(buf) // 2, int((t0 + 0.5) * rate))):
            dt = i / rate - t0
            s = math.sin(2 * math.pi * (62 - 25 * min(1, dt / 0.3)) * dt) * math.exp(-dt * 8) * 0.45
            buf[2 * i] = buf[2 * i + 1] = int(s * 32767)
    return buf


def _write_wav(path, buf, rate=48000):
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(buf.tobytes())


# ----------------------------------------------------------------------------
# Encodage
# ----------------------------------------------------------------------------
def _encode_rgba(frames, path):
    """Images RGBA -> .mov PNG avec transparence (pour être superposé)."""
    cmd = [ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0", "-c:v", "png", "-pix_fmt", "rgba", path]
    return _pipe(cmd, frames, path)


def _encode_rgb(frames, path, sound=None):
    wav = None
    cmd = [ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0"]
    if sound is not None:
        wav = path + ".wav"
        _write_wav(wav, sound)
        cmd += ["-i", wav, "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", path]
    try:
        return _pipe(cmd, frames, path)
    finally:
        if wav:
            try:
                os.remove(wav)
            except OSError:
                pass


def _pipe(cmd, frames, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    p = ff.popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for img in frames:
        p.stdin.write(img.tobytes())
    p.stdin.close()
    err = p.stderr.read().decode("utf-8", "ignore")
    if p.wait(180) != 0:
        raise RuntimeError(f"Rendu impossible : {err[-400:]}")
    return path


# ----------------------------------------------------------------------------
# Transition « signal TV perdu » (commune aux styles, couleurs du style)
# ----------------------------------------------------------------------------
SIGNAL_S = 0.5
SIGNAL_CUT = 0.25        # la coupe a lieu sous la neige, quand l'écran est entièrement brouillé
SIGNAL_FX = 0.2          # durée (de chaque côté de la coupe) du décalage RVB + bruit sur l'image


def signal_frames(colors=((255, 61, 165), (47, 123, 255), (255, 31, 61))):
    n = int(round(SIGNAL_S * FPS))
    gw, gh = W // 4, H // 4                       # grain de la neige (4 px)
    for i in range(n):
        t = i / FPS
        rnd = random.Random(7919 * i + 13)
        d_cut = abs(t - SIGNAL_CUT)
        k = max(0.0, 1 - d_cut / SIGNAL_CUT) ** 1.6          # intensité du brouillage
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        # 1. neige : entièrement opaque autour de la coupe, puis par bandes
        full = d_cut < 0.06
        snow_a = 255 if full else int(200 * max(0.0, (k - 0.45) / 0.55))
        if snow_a > 0:
            noise = Image.frombytes("L", (gw, gh), os.urandom(gw * gh)).resize((W, H), Image.NEAREST)
            snow = Image.merge("RGBA", (noise, noise, noise, Image.new("L", (W, H), snow_a)))
            img.alpha_composite(snow)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))       # dessin à part, puis fondu par-dessus
        d = ImageDraw.Draw(layer)
        # 2. bandes de glitch (couleurs du style + neige claire)
        for _ in range(int(2 + 10 * k)):
            y = rnd.randrange(0, H)
            h = rnd.randrange(6, 26 + int(90 * k))
            x0 = rnd.randrange(-200, W // 2)
            x1 = x0 + rnd.randrange(W // 3, W + 200)
            if rnd.random() < 0.55:
                c = rnd.choice(colors)
                d.rectangle((x0, y, x1, y + h), fill=c + (int(110 + 120 * k),))
            else:
                g = rnd.randrange(150, 255)
                d.rectangle((x0, y, x1, y + h), fill=(g, g, g, int(80 + 150 * k)))
        img.alpha_composite(layer)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        # 3. lignes de balayage
        if k > 0.05:
            a = int(70 * k)
            for y in range(0, H, 6):
                d.line((0, y, W, y), fill=(0, 0, 0, a), width=2)
        img.alpha_composite(layer)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        # 4. barre de synchro qui défile
        if k > 0.1:
            yb = int((t * 5200) % (H + 300)) - 150
            d.rectangle((0, yb, W, yb + 140), fill=(255, 255, 255, int(55 * k)))
        # 5. au pic : l'image s'écrase en une ligne blanche (écran cathodique)
        if d_cut < 0.035:
            for w_, a in ((40, 60), (16, 140), (6, 255)):
                ln = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ImageDraw.Draw(ln).rectangle((0, H // 2 - w_, W, H // 2 + w_), fill=(255, 255, 255, a))
                layer.alpha_composite(ln)
        img.alpha_composite(layer)
        yield img


def render_signal(path, colors=None):
    if os.path.isfile(path):
        return path
    return _encode_rgba(signal_frames(colors) if colors else signal_frames(), path)


def _static_burst(buf, rate, center, rnd, gain=0.42):
    """Grésillement de télé : bruit blanc haché, maximal à la coupe, plus un petit « clac »."""
    a, b = center - SIGNAL_CUT, center + SIGNAL_CUT
    hp = 0.0
    for i in range(max(0, int(a * rate)), min(len(buf) // 2, int(b * rate))):
        t = i / rate
        env = max(0.0, 1 - abs(t - center) / SIGNAL_CUT) ** 1.3
        flutter = 0.55 + 0.45 * (1 if math.sin(2 * math.pi * 55 * t) > 0 else -1) * (0.5 + 0.5 * rnd.random())
        x = rnd.random() * 2 - 1
        hp = 0.6 * hp + 0.4 * x
        s = (x - hp) * env * flutter * gain
        if 0 <= t - center < 0.012:
            s += 0.5 * (1 - (t - center) / 0.012)
        v = max(-32767, min(32767, buf[2 * i] + int(s * 32767)))
        buf[2 * i] = buf[2 * i + 1] = v


def static_track(path, total, cuts):
    rate = 48000
    buf = array("h", bytes(4 * int(rate * total + rate)))
    rnd = random.Random(5)
    for c in cuts:
        _static_burst(buf, rate, c, rnd)
    _write_wav(path, buf, rate)
    return path


def transition_track(path, total, events):
    """Piste des sons de transition : events = [(instant, « leger » | « whoosh » | « grésillement »)]."""
    rate = 48000
    buf = array("h", bytes(4 * int(rate * total + rate)))
    rnd = random.Random(17)
    for at, kind in events:
        if kind == "grésillement":
            _static_burst(buf, rate, at, rnd)
        elif kind == "whoosh":
            _whoosh(buf, rate, at, rnd, gain=0.5)
        else:
            _whoosh(buf, rate, at, rnd, gain=0.22)
    _write_wav(path, buf, rate)
    return path
