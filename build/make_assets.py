# -*- coding: utf-8 -*-
"""Fabrique les images du logo HexaClip (piste « Coupe ») dans src/assets.

À lancer seulement si le logo change : python build/make_assets.py
Dépendances (développement uniquement) : cairosvg, Pillow.
"""
import io
import os

import cairosvg
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "src", "assets")
MINT = "#2EE6A8"
BG = "#0B0F14"
HEX = "100,20 169.3,60 169.3,140 100,180 30.7,140 30.7,60"


def coupe_svg(color=MINT, hole=BG, play=True, gap=(5, -9), stroke=10, app_bg=None, scale=1.0):
    """Hexagone plein, coupé en diagonale, moitié haute décalée ; play évidé au centre."""
    tri = f'<polygon points="82,70 82,130 134,100" fill="{hole}"/>' if play else ""
    hexa = f'<polygon points="{HEX}" fill="{color}" stroke="{color}" stroke-width="{stroke}" stroke-linejoin="round"/>'
    bg = ""
    if app_bg:
        bg = f'<rect x="0" y="0" width="200" height="200" rx="44" fill="{app_bg}"/>'
    s = scale
    off = (200 - 200 * s) / 2
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">
<defs>
<clipPath id="t"><polygon points="0,0 200,0 200,48 0,148"/></clipPath>
<clipPath id="b"><polygon points="0,148 200,48 200,200 0,200"/></clipPath>
</defs>
{bg}
<g transform="translate({off},{off}) scale({s})">
<g transform="translate({gap[0]},{gap[1]})"><g clip-path="url(#t)">{hexa}{tri}</g></g>
<g clip-path="url(#b)">{hexa}{tri}</g>
</g>
</svg>'''


def render(svg, size):
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def app_icon(size):
    # petites tailles : coupe plus marquée et sans play (illisible en dessous de 32 px)
    if size <= 24:
        svg = coupe_svg(play=False, gap=(9, -17), stroke=14, app_bg=BG, scale=0.82)
    elif size <= 48:
        svg = coupe_svg(gap=(7, -12), stroke=12, app_bg=BG, scale=0.76)
    else:
        svg = coupe_svg(app_bg=BG, scale=0.72)
    return render(svg, size)


def wordmark(height=120, shadow=True):
    """Filigrane vidéo : symbole + « HexaClip » (Hexa blanc, Clip menthe) sur fond transparent."""
    icon = render(coupe_svg(hole="#0B0F14"), height)
    font = ImageFont.truetype(os.path.join(OUT, "SpaceGrotesk-Bold.ttf"), int(height * 0.62))
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    w1 = tmp.textlength("Hexa", font=font)
    w2 = tmp.textlength("Clip", font=font)
    pad = int(height * 0.18)
    W = int(height + pad + w1 + w2 + pad)
    H = height + pad
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.alpha_composite(icon, (0, pad // 2))
    d = ImageDraw.Draw(img)
    asc, desc = font.getmetrics()
    y = pad // 2 + (height - (asc + desc)) // 2 + int(height * 0.02)
    x = height + pad
    d.text((x, y), "Hexa", font=font, fill="#FFFFFF")
    d.text((x + w1, y), "Clip", font=font, fill=MINT)
    if not shadow:
        return img
    # ombre douce pour rester lisible sur une image claire
    a = img.split()[3].filter(ImageFilter.GaussianBlur(height * 0.08))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.putalpha(a.point(lambda v: int(v * 0.55)))
    out = Image.new("RGBA", (W + 8, H + 8), (0, 0, 0, 0))
    out.alpha_composite(sh, (6, 6))
    out.alpha_composite(img, (2, 2))
    return out


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icons = {s: app_icon(s) for s in sizes}
    icons[256].save(os.path.join(OUT, "hexaclip.ico"), sizes=[(s, s) for s in sizes],
                    append_images=[icons[s] for s in sizes if s != 256])
    icons[64].save(os.path.join(OUT, "app_64.png"))
    icons[256].save(os.path.join(OUT, "app_256.png"))
    for s in (36, 72, 512):
        render(coupe_svg(hole=BG), s).save(os.path.join(OUT, f"symbole_{s}.png"))
    wordmark(120).save(os.path.join(OUT, "filigrane.png"))
    print("Logos écrits dans", OUT)


if __name__ == "__main__":
    main()
