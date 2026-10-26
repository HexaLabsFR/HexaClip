# -*- coding: utf-8 -*-
"""
Compil TikTok : choisit les meilleures actions, les découpe autour des kills et monte une vidéo
verticale 1080×1920 d'environ une minute.

Cadrage « flou » : le jeu entier au centre (côtés légèrement rognés) sur un fond flouté du même jeu.
Cadrage « zoom » : le centre de l'écran (viseur) en plein écran vertical.
Par-dessus : logo en haut, compteur de kills qui monte (1 KILL, 2 KILLS, 3K…), map et round,
puis un écran de fin avec l'Elo Faceit de la session.
"""
from __future__ import annotations

import datetime as dt
import os
import random

import ffmpeg_tools as ff
import paths
from moments import LABELS

W, H, FPS = 1080, 1920, 60
OUTRO_S = 2.6
MINT = "0x2EE6A8"
INK = "0x0B0F14"
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


# ----------------------------------------------------------------------------
# Sélection des actions
# ----------------------------------------------------------------------------
def cut_duration(m):
    return sum(max(0.0, b - a) for a, b in m["coupes"])


def select(moments, budget_s, order="crescendo"):
    """Meilleures actions (favoris d'abord) tant qu'elles rentrent dans la durée ; puis ordre de passage."""
    pool = [m for m in moments if not m.get("exclu") and m.get("fichier") and os.path.isfile(m["fichier"])
            and m.get("coupes")]
    pool.sort(key=lambda m: (m.get("favori", 0), m.get("score", 0), m.get("cree", 0)), reverse=True)
    chosen, used = [], 0.0
    for m in pool:
        d = cut_duration(m)
        if d <= 0:
            continue
        if used + d <= budget_s + 0.5:
            chosen.append(m)
            used += d
        if used >= budget_s - 3:
            break
    if not chosen and pool:                  # une seule action trop longue : on garde ses derniers kills
        m = dict(pool[0])
        cuts, keep, tot = list(m["coupes"]), [], 0.0
        for a, b in reversed(cuts):
            if tot + (b - a) > budget_s:
                a = b - (budget_s - tot)
                if b - a < 2:
                    break
            keep.insert(0, [a, b])
            tot += b - a
            if tot >= budget_s - 0.5:
                break
        m["coupes"] = keep
        chosen = [m]
    if order == "chrono":
        chosen.sort(key=lambda m: m.get("cree", 0))
    elif order == "meilleure_d_abord":
        chosen.sort(key=lambda m: m.get("score", 0), reverse=True)
    else:                                    # crescendo : la meilleure à la fin
        chosen.sort(key=lambda m: (m.get("favori", 0), m.get("score", 0)))
    return chosen


def kill_labels(n_total, kill_type):
    out = []
    for i in range(1, n_total + 1):
        if i == n_total and kill_type in LABELS and kill_type not in ("1k", "2k", "manuel"):
            out.append(LABELS[kill_type])
        else:
            out.append(f"{i} KILL" if i == 1 else f"{i} KILLS")
    return out


# ----------------------------------------------------------------------------
# Construction de la commande FFmpeg
# ----------------------------------------------------------------------------
def _font():
    return ff.filter_path(paths.font_file("Bold") or "")


def _text(label, size, y, color="white", box=None, enable=None, alpha=None, x="(w-tw)/2"):
    s = (f"drawtext=fontfile='{_font()}':text='{ff.filter_text(label)}':fontsize={size}:fontcolor={color}"
         f":x={x}:y={y}")
    if box:
        s += f":box=1:boxcolor={box}:boxborderw=26"
    if alpha:
        s += f":alpha='{alpha}'"
    if enable:
        s += f":enable='{enable}'"
    return s


def build(chosen, out_path, opts):
    """Renvoie (commande, durée). opts : cadrage, badges, texte, logo, fin (dict|None), musique (chemin|None),
    musique_volume, encodeur."""
    args = [ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    chains, labels_v, labels_a = [], [], []
    idx = 0
    total = 0.0
    for m in chosen:
        n_kills = len(m.get("kills_t") or [])
        names = kill_labels(n_kills, m.get("type"))
        kills = sorted(m.get("kills_t") or [])
        sub = []
        if m.get("carte"):
            sub.append(m["carte"].upper())
        if m.get("round"):
            sub.append(f"ROUND {m['round']}")
        subtitle = " · ".join(sub)
        for a, b in m["coupes"]:
            a = max(0.0, a)
            d = b - a
            if d < 0.5:
                continue
            args += ["-ss", f"{a:.3f}", "-t", f"{d:.3f}", "-i", m["fichier"]]
            i = idx
            idx += 1
            v = f"[{i}:v]setpts=PTS-STARTPTS,fps={FPS},scale=1920:1080:force_original_aspect_ratio=decrease," \
                f"pad=1920:1080:-1:-1,setsar=1"
            if opts.get("cadrage") == "zoom":
                v += f",crop=ih*9/16:ih,scale={W}:{H}"
                y_badge, y_sub = int(H * 0.70), int(H * 0.70) + 175
            else:
                v += (f",split=2[bg{i}][fg{i}];[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
                      f"crop={W}:{H},gblur=sigma=28,eq=brightness=-0.14:saturation=1.15[bb{i}];"
                      f"[fg{i}]crop=ih*1.4:ih,scale={W}:-2[ff{i}];[bb{i}][ff{i}]overlay=(W-w)/2:(H-h)/2-90")
                y_badge, y_sub = 1330, 1505
            # compteur de kills : chaque palier s'affiche du kill jusqu'au suivant (ou la fin du passage)
            if opts.get("badges", True):
                for k_i, kt in enumerate(kills):
                    if not (a - 0.05 <= kt <= b + 0.05):
                        continue
                    t0 = max(0.0, kt - a)
                    nxt = kills[k_i + 1] - a if k_i + 1 < len(kills) else d + 1
                    t1 = min(d + 1, nxt)
                    final = k_i == len(kills) - 1
                    v += "," + _text(names[k_i], 150 if final else 104, y_badge,
                                     color=INK if final else "white",
                                     box=f"{MINT}@0.96" if final else "black@0.55",
                                     enable=f"between(t,{t0:.3f},{t1:.3f})",
                                     alpha=f"min(1,(t-{t0:.3f})*7)")
                if m.get("type") == "manuel" and not kills:
                    pass
            if subtitle:
                v += "," + _text(subtitle, 44, y_sub, color="white@0.85")
            if opts.get("texte"):
                v += "," + _text(opts["texte"], 58, 330, color="white")
            v += f",fade=t=in:st=0:d=0.12,format=yuv420p,setsar=1[v{i}]"
            chains.append(v)
            if m.get("son", 1):
                chains.append(f"[{i}:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                              f"apad,atrim=0:{d:.3f},afade=t=in:d=0.06,afade=t=out:st={max(0, d - 0.12):.3f}:d=0.12"
                              f"[a{i}]")
            else:
                chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d:.3f}[a{i}]")
            labels_v.append(f"[v{i}]")
            labels_a.append(f"[a{i}]")
            total += d
    if not labels_v:
        raise ValueError("aucun passage à monter")
    n = len(labels_v)
    chains.append("".join(f"{v}{a}" for v, a in zip(labels_v, labels_a)) + f"concat=n={n}:v=1:a=1[cv][ca]")

    # logo en filigrane sur tout le montage
    logo = opts.get("logo") or paths.res("assets", "filigrane.png")
    args += ["-loop", "1", "-framerate", str(FPS), "-i", logo]
    li = idx
    idx += 1
    chains.append(f"[{li}:v]scale=430:-1,format=rgba,colorchannelmixer=aa=0.93[wm]")
    chains.append("[cv][wm]overlay=(W-w)/2:150:shortest=1:format=auto[cvw]")
    last_v, last_a = "[cvw]", "[ca]"

    # écran de fin
    fin = opts.get("fin")
    if fin is not None:
        sym = paths.res("assets", "filigrane.png")
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{OUTRO_S}", "-i", sym]
        si = idx
        idx += 1
        o = (f"color=c={INK}:s={W}x{H}:r={FPS}:d={OUTRO_S}[obg];[{si}:v]scale=760:-1,format=rgba[osym];"
             f"[obg][osym]overlay=(W-w)/2:(H-h)/2-160:shortest=1")
        if fin.get("ligne1"):
            o += "," + _text(fin["ligne1"], 92, int(H / 2 + 40), color=f"{MINT}")
        if fin.get("ligne2"):
            o += "," + _text(fin["ligne2"], 54, int(H / 2 + 175), color="white@0.85")
        o += ",fade=t=in:st=0:d=0.25,format=yuv420p,setsar=1[ov]"
        chains.append(o)
        chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{OUTRO_S}[oa]")
        chains.append(f"{last_v}{last_a}[ov][oa]concat=n=2:v=1:a=1[fv][fa]")
        last_v, last_a = "[fv]", "[fa]"
        total += OUTRO_S

    # musique (optionnelle) mélangée sous le son du jeu
    if opts.get("musique"):
        args += ["-stream_loop", "-1", "-i", opts["musique"]]
        mi = idx
        idx += 1
        vol = float(opts.get("musique_volume", 0.25))
        chains.append(f"[{mi}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                      f"volume={vol:.2f},afade=t=in:d=0.5,afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[mu]")
        chains.append(f"{last_a}[mu]amix=inputs=2:duration=first:normalize=0[mix]")
        last_a = "[mix]"

    args += ["-filter_complex", ";".join(chains), "-map", last_v, "-map", last_a]
    args += encoder_args(opts.get("encodeur", "libx264"))
    args += ["-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
             "-t", f"{total:.3f}", out_path]
    return args, total


def encoder_args(enc):
    if enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0", "-maxrate", "16M",
                "-bufsize", "32M", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if enc == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "20", "-qp_p", "22",
                "-pix_fmt", "yuv420p"]
    if enc == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "21", "-pix_fmt", "nv12"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p"]


def pick_music(folder):
    if not folder or not os.path.isdir(folder):
        return None
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(AUDIO_EXT)]
    return random.choice(files) if files else None


def make(chosen, out_dir, opts, log=print):
    """Monte la compil. Renvoie (chemin, durée). Repli sur libx264 si l'encodeur matériel refuse."""
    os.makedirs(out_dir, exist_ok=True)
    name = "HexaClip_" + dt.datetime.now().strftime("%Y-%m-%d_%Hh%M") + ".mp4"
    out = os.path.join(out_dir, name)
    enc = opts.get("encodeur", "libx264")
    tries = [enc] + (["libx264"] if enc != "libx264" else [])
    last = None
    for e in tries:
        cmd, total = build(chosen, out, {**opts, "encodeur": e})
        r = ff.run(cmd, timeout=900)
        if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 10000:
            return out, total
        last = (r.stderr or b"").decode("utf-8", "ignore")[-1200:]
        log(f"Montage avec {e} refusé : {last.strip()[-300:]}")
    raise RuntimeError(f"Le montage a échoué :\n{last}")
