# -*- coding: utf-8 -*-
"""
Écran de fin avec ton overlay FaceitSync (le même que sur Twitch).

Au montage d'une compil, HexaClip lit les données de ton overlay (le lien donné dans les réglages, mêmes
chiffres que sur le stream : Elo, niveau, classement, derniers matchs, stats de la session, dernier match)
et dessine la carte en haute définition pour la vidéo verticale, à la place du gros « ELO ».
Si les données ne sont pas lisibles, il tente une capture de la page avec Edge (ou Chrome) invisible.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import paths

IS_WIN = sys.platform == "win32"
SCALE = 3


def browser_exe():
    """Edge (installé avec Windows) ou Chrome."""
    env = os.environ.get("HEXACLIP_NAVIGATEUR")
    cands = [env] if env else []
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("LOCALAPPDATA", "")):
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    else:
        cands += [shutil.which(n) for n in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge")]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def _kill_tree(proc):
    try:
        import psutil
        parent = psutil.Process(proc.pid)
        for c in parent.children(recursive=True):
            c.kill()
        parent.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def api_url(url):
    """Lien de l'overlay → lien des données (même jeton)."""
    return url.split("?")[0].replace("/overlay/u/", "/api/overlay/u/", 1)


def fetch(url, timeout=15):
    import json
    import time
    import urllib.request
    req = urllib.request.Request(api_url(url) + f"?t={int(time.time() * 1000)}", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/130.0 Safari/537.36 HexaClip", "Accept": "application/json", "Referer": url})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    return d if d.get("success") and d.get("elo") else None


# couleurs des niveaux Faceit
LEVEL_COLORS = {1: (238, 238, 238), 2: (29, 255, 179), 3: (29, 255, 179), 4: (255, 200, 0), 5: (255, 200, 0),
                6: (255, 200, 0), 7: (255, 200, 0), 8: (255, 99, 9), 9: (255, 99, 9), 10: (254, 31, 0)}
WIN, LOSS = (46, 204, 113), (255, 72, 72)


def _num(v):
    return f"{int(v):,}".replace(",", "\u202f") if isinstance(v, (int, float)) else "?"


def draw_card(d, width=960):
    """Carte façon overlay Twitch, dessinée en haute définition (RGBA, fond transparent autour)."""
    from PIL import Image, ImageDraw
    import spectre as sp
    W = width
    lvl = int(d.get("level") or 0)
    lc = LEVEL_COLORS.get(lvl, (255, 200, 0))
    H = 470
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(img)
    g.rounded_rectangle((0, 0, W - 1, H - 1), radius=38, fill=(16, 14, 22, 236), outline=(255, 255, 255, 34), width=3)
    pad = 44
    # niveau : anneau + chiffre
    cx, cy, r = pad + 62, 112, 62
    g.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(60, 56, 72, 255), width=12)
    ext = {1: 0.1, 2: 0.2, 3: 0.3, 4: 0.4, 5: 0.5, 6: 0.6, 7: 0.7, 8: 0.8, 9: 0.9, 10: 1.0}.get(lvl, 0.5)
    g.arc((cx - r, cy - r, cx + r, cy + r), start=135, end=135 + 270 * ext, fill=lc + (255,), width=12)
    f_lvl = sp.title_font(62)
    tw = g.textlength(str(lvl), font=f_lvl)
    g.text((cx - tw / 2, cy - 40), str(lvl), font=f_lvl, fill=lc + (255,))
    # pseudo, classement, saison
    x0 = cx + r + 34
    name = d.get("display_name") or d.get("faceit_nickname") or ""
    g.text((x0, 44), name, font=sp.text_font(58), fill=sp.WHITE + (255,))
    rank = d.get("faceit_regional_ranking")
    sub = []
    if rank:
        sub.append(f"#{_num(rank)} {d.get('faceit_region') or ''}".strip())
    if (d.get("season") or {}).get("number"):
        sub.append(f"Saison {d['season']['number']}")
    g.text((x0, 114), "  ·  ".join(sub), font=sp.text_font(38, bold=False), fill=sp.SOFT + (255,))
    # Elo + variation du jour
    f_elo = sp.title_font(96)
    elo = _num(d.get("elo"))
    ex = W - pad - g.textlength(elo + " ELO", font=sp.title_font(96))
    g.text((W - pad - g.textlength(elo, font=f_elo) - 4, 30), elo, font=f_elo, fill=lc + (255,))
    today = (d.get("sessionStats") or {}).get("elo_change", d.get("todayChange"))
    if isinstance(today, (int, float)) and (d.get("sessionStats") or {}).get("total_matches"):
        txt = f"{'+' if today > 0 else ''}{int(today)} aujourd'hui"
        f_t = sp.text_font(40)
        g.text((W - pad - g.textlength(txt, font=f_t), 134), txt, font=f_t,
               fill=(WIN if today > 0 else LOSS if today < 0 else sp.SOFT) + (255,))
    # derniers matchs
    y = 214
    chips = (d.get("recentMatches") or [])[:5]
    g.text((pad, y + 12), f"{len(chips)} DERNIERS MATCHS", font=sp.text_font(28), fill=sp.MUTED + (255,))
    cw, gap = 104, 12
    x = W - pad - len(chips) * cw - max(0, len(chips) - 1) * gap
    for m in chips:
        won = bool(m.get("player_won")) or m.get("result") == "win"
        col = WIN if won else LOSS
        g.rounded_rectangle((x, y, x + cw, y + 58), radius=16, fill=col + (60,), outline=col + (255,), width=3)
        ch = m.get("change")
        t = (f"{'+' if ch and ch > 0 else ''}{ch}" if isinstance(ch, (int, float)) else m.get("letter", "?"))
        f_c = sp.text_font(38)
        g.text((x + (cw - g.textlength(t, font=f_c)) / 2, y + 6), t, font=f_c, fill=sp.WHITE + (255,))
        x += cw + gap
    # stats (session si jouée, sinon 20 derniers matchs) + dernier match
    ss = d.get("sessionFaceitStats") or {}
    st, lab = (ss, "SESSION") if ss.get("matches") else (d.get("faceitStats") or {}, "20 MATCHS")
    vals = [("K/D", st.get("kd_ratio")), ("ADR", st.get("adr")), ("HS", st.get("headshots_percent")),
            ("VICT.", st.get("win_rate_percent"))]
    y = 300
    g.line((pad, y, W - pad, y), fill=(255, 255, 255, 30), width=2)
    g.text((pad, y + 26), lab, font=sp.text_font(32), fill=sp.MUTED + (255,))
    colw = (W - 2 * pad - 190) / 4
    for i, (k, v) in enumerate(vals):
        if v is None:
            continue
        vs = (f"{v:.2f}" if k == "K/D" else f"{v:.0f}" + ("%" if k in ("HS", "VICT.") else "")) \
            if isinstance(v, (int, float)) else str(v)
        xx = pad + 190 + i * colw
        g.text((xx, y + 14), vs, font=sp.text_font(46), fill=sp.WHITE + (255,))
        g.text((xx, y + 66), k, font=sp.text_font(28, bold=False), fill=sp.MUTED + (255,))
    lm = d.get("lastMatch") or {}
    if lm.get("mapName"):
        sc = lm.get("score") or {}
        parts = [f"Dernier match : {lm['mapName']} {sc.get('own', '?')}-{sc.get('opp', '?')}"]
        if lm.get("kills") is not None:
            parts.append(f"{lm['kills']} kills")
        mk = lm.get("multiKills") or {}
        if mk.get("k5"):
            parts.append(f"{mk['k5']} ACE")
        elif mk.get("k4"):
            parts.append(f"{mk['k4']}× 4K")
        txt = " · ".join(parts)
        f_l = sp.text_font(34, bold=False)
        g.text(((W - g.textlength(txt, font=f_l)) / 2, H - 62), txt, font=f_l, fill=sp.SOFT + (255,))
    return img


def capture(url, out_png, timeout=60, wait_ms=9000):
    """Carte de l'overlay (PNG transparent) : d'après ses données, sinon par capture de la page.
    Renvoie out_png ou None."""
    try:
        data = fetch(url)
    except Exception:
        data = None
    if data:
        try:
            os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
            draw_card(data).save(out_png)
            return out_png
        except Exception:
            pass
    return capture_page(url, out_png, timeout, wait_ms)


def capture_page(url, out_png, timeout=60, wait_ms=9000):
    """Capture de la page de l'overlay (PNG transparent, recadré). Renvoie out_png ou None."""
    exe = browser_exe()
    if not exe or not url or not url.startswith("http"):
        return None
    from PIL import Image
    work = tempfile.mkdtemp(prefix="hexaclip_overlay_")
    shot = os.path.join(work, "capture.png")
    profile = paths.data("navigateur_overlay")       # profil à part : n'interfère pas avec ton Edge
    cmd = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio", "--no-first-run",
           "--no-default-browser-check", "--disable-background-networking", "--disable-component-update",
           "--disable-sync", "--disable-extensions", "--no-pings", "--disable-features=Translate,MediaRouter,OptimizationHints",
           f"--user-data-dir={profile}", f"--force-device-scale-factor={SCALE}",
           "--window-size=460,180", "--default-background-color=00000000", f"--virtual-time-budget={wait_ms}",
           f"--screenshot={shot}", url]
    if not IS_WIN:
        cmd.insert(1, "--no-sandbox")
    try:
        import time
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        end, last = time.time() + timeout, -1
        try:
            # le navigateur peut rester ouvert après la capture : on attend juste le fichier complet
            while time.time() < end:
                if os.path.isfile(shot):
                    size = os.path.getsize(shot)
                    if size > 0 and size == last:
                        break
                    last = size
                elif proc.poll() is not None:
                    break
                time.sleep(0.3)
        finally:
            _kill_tree(proc)
        if not os.path.isfile(shot):
            return None
        img = Image.open(shot).convert("RGBA")
        box = img.getchannel("A").point(lambda a: 255 if a > 24 else 0).getbbox()
        if not box or (box[2] - box[0]) < 60 * SCALE or (box[3] - box[1]) < 25 * SCALE:
            return None                    # page vide (lien faux, site injoignable…)
        m = 2 * SCALE
        img = img.crop((max(0, box[0] - m), max(0, box[1] - m), min(img.width, box[2] + m),
                        min(img.height, box[3] + m)))
        os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
        img.save(out_png)
        return out_png
    except Exception:
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
