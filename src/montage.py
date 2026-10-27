# -*- coding: utf-8 -*-
"""
Compil TikTok : choisit les meilleures actions, les découpe autour des kills et monte une vidéo
verticale 1080×1920 d'environ une minute.

Cadrage « flou » : le jeu entier au centre (côtés légèrement rognés) sur un fond flouté du même jeu.
Cadrage « zoom » : le centre de l'écran (viseur) en plein écran vertical.
Habillage (brand.py) : intro animée avec ton pseudo, plaque pseudo en haut, compteur de kills qui monte
(1 KILL, 2 KILLS, 3K…) avec un coup de zoom sur chaque kill, map et round, transitions en balayage
diagonal avec whoosh, puis écran de fin animé (pseudo, Elo de la session, appel à s'abonner).
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
INTRO_S = 1.7
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
_BADGE_FONT = None      # police des badges (fichier dans assets), fixée par le style


def _font():
    if _BADGE_FONT and os.path.isfile(paths.res("assets", _BADGE_FONT)):
        return ff.filter_path(paths.res("assets", _BADGE_FONT))
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


def _pulse_expr(kills_rel, amp=0.055, dur=0.2, var="t"):
    """Expression FFmpeg du « coup de zoom » sur chaque kill (0 hors des kills)."""
    terms = [f"if(between({var},{k:.3f},{k + dur:.3f}),sin(PI*({var}-{k:.3f})/{dur}),0)" for k in kills_rel]
    return f"{amp}*(" + "+".join(terms) + ")" if terms else "0"


def _punch(kills_rel, w, h):
    """Coup de zoom à taille constante (zoompan) : compatible avec toutes les versions de FFmpeg."""
    if not kills_rel:
        return ""
    z = _pulse_expr(kills_rel, var="it")
    return (f",zoompan=z='1+{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={FPS}")


def build(chosen, out_path, opts):
    """Renvoie (commande, durée). opts : cadrage, badges, texte, logo / plaque (PNG du filigrane), fin (dict),
    intro_file, outro_file (écran de fin animé), stinger_file + whoosh_dir (transitions), punch (zoom sur les kills),
    musique, musique_volume, encodeur."""
    global _BADGE_FONT
    th = opts.get("theme")
    _BADGE_FONT = getattr(th, "BADGE_FONT", None)
    final_box = getattr(th, "BADGE_FINAL_BOX", f"{MINT}@0.96")
    final_txt = getattr(th, "BADGE_FINAL_TEXT", INK)
    intro_s = getattr(th, "INTRO_S", INTRO_S)
    args = [ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    chains, labels_v, labels_a = [], [], []
    idx = 0
    clips_total = 0.0
    trans = bool(opts.get("transitions_cfg"))
    durations = []
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
            rel = [k - a for k in kills if a - 0.05 <= k <= b + 0.05] if opts.get("punch", True) else []
            fg_h = int(round(W / 1.4 / 2)) * 2
            v = f"[{i}:v]setpts=PTS-STARTPTS,fps={FPS},scale=1920:1080:force_original_aspect_ratio=decrease," \
                f"pad=1920:1080:-1:-1,setsar=1"
            if m.get("vertical"):                     # image déjà verticale (aperçu des transitions)
                v = f"[{i}:v]setpts=PTS-STARTPTS,fps={FPS},scale={W}:{H},setsar=1"
                y_badge, y_sub = 1330, 1505
            elif opts.get("cadrage") == "zoom":
                v += f",crop=ih*9/16:ih,scale={W}:{H}" + _punch(rel, W, H)
                y_badge, y_sub = int(H * 0.70), int(H * 0.70) + 175
            else:
                v += (f",split=2[bg{i}][fg{i}];[bg{i}]scale={W}:{H}:force_original_aspect_ratio=increase,"
                      f"crop={W}:{H},gblur=sigma=28,eq=brightness=-0.14:saturation=1.15[bb{i}];"
                      f"[fg{i}]crop=ih*1.4:ih,scale={W}:{fg_h}" + _punch(rel, W, fg_h) +
                      f"[ff{i}];[bb{i}][ff{i}]overlay=(W-w)/2:(H-h)/2-90")
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
                                     color=final_txt if final else "white",
                                     box=final_box if final else "black@0.55",
                                     enable=f"between(t,{t0:.3f},{t1:.3f})",
                                     alpha=f"min(1,(t-{t0:.3f})*7)")
            if subtitle:
                v += "," + _text(subtitle, 44, y_sub, color="white@0.85")
            if opts.get("texte"):
                v += "," + _text(opts["texte"], 58, 330, color="white")
            if not trans:
                v += ",fade=t=in:st=0:d=0.12"
            v += f",format=yuv420p,setsar=1[v{i}]"
            chains.append(v)
            if m.get("son", 1):
                chains.append(f"[{i}:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                              f"apad,atrim=0:{d:.3f},afade=t=in:d=0.06,afade=t=out:st={max(0, d - 0.08):.3f}:d=0.08"
                              f"[a{i}]")
            else:
                chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d:.3f}[a{i}]")
            labels_v.append(f"[v{i}]")
            labels_a.append(f"[a{i}]")
            clips_total += d
            durations.append(d)
    if not labels_v:
        raise ValueError("aucun passage à monter")

    norm = f"fps={FPS},settb=1/{FPS},format=yuv420p,setsar=1"
    segs = []                                  # (vidéo, son, durée) dans l'ordre
    if opts.get("intro_file"):
        args += ["-i", opts["intro_file"]]
        ii = idx
        idx += 1
        chains.append(f"[{ii}:v]scale={W}:{H},{norm}[iv]")
        chains.append(f"[{ii}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{intro_s:.3f}[ia]")
        segs.append(("[iv]", "[ia]", intro_s))
    first_clip = len(segs)
    for k, (lv, la, d) in enumerate(zip(labels_v, labels_a, durations)):
        chains.append(f"{lv}{norm}[pw{k}]")
        segs.append((f"[pw{k}]", la, d))
    last_clip = len(segs) - 1
    fin = opts.get("fin")
    if opts.get("outro_file"):
        args += ["-i", opts["outro_file"]]
        oi = idx
        idx += 1
        od = float(opts.get("outro_s", OUTRO_S))
        chains.append(f"[{oi}:v]scale={W}:{H},{norm}[ov]")
        chains.append(f"[{oi}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{od:.3f}[oa]")
        segs.append(("[ov]", "[oa]", od))
    elif fin is not None:                      # ancien écran de fin (texte fixe)
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
        o += f",fade=t=in:st=0:d=0.25,{norm}[ov]"
        chains.append(o)
        chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{OUTRO_S}[oa]")
        segs.append(("[ov]", "[oa]", OUTRO_S))

    # enchaînement : fondus (xfade, les clips se chevauchent) ou coupes franches (+ habillage par-dessus)
    import transitions as tr
    tplan = []
    if trans and len(segs) > 1:
        cfg = opts["transitions_cfg"]
        tplan = tr.plan(len(segs) - 1, cfg["actives"], cfg.get("ouverture") if opts.get("intro_file") else None,
                        cfg.get("graine"))
        if not opts.get("intro_file") and tplan:
            # pas d'intro : la transition d'ouverture passe quand même avant le 1er clip (voir plus bas)
            tplan = tr.plan(len(segs) - 1, [t for t in cfg["actives"] if t != cfg.get("ouverture")] or cfg["actives"],
                            None, cfg.get("graine"))
        if opts.get("trans_force"):                 # aperçu de la bibliothèque : ordre imposé
            tplan = list(opts["trans_force"])[:len(segs) - 1]
        stingers = opts.get("stingers") or {}
        tplan = [t if tr.BY_ID[t].famille == "xfade" or tr.BY_ID[t].effet in stingers else "fondu" for t in tplan]
    cur_v, cur_a, cur_len = segs[0]
    starts, ends = [0.0], []                   # début / fin de chaque segment dans la vidéo finale
    events = []                                # (instant, transition) pour les sons et habillages
    for k, (sv, sa, sd) in enumerate(segs[1:]):
        tid = tplan[k] if tplan else None
        t = tr.BY_ID.get(tid) if tid else None
        if t is not None and t.famille == "xfade":
            dx = min(t.duree, cur_len / 2, sd / 2)
            chains.append(f"{cur_v}{sv}xfade=transition={t.effet}:duration={dx:.3f}:offset={cur_len - dx:.3f}[jx{k}]")
            chains.append(f"{cur_a}{sa}acrossfade=d={dx:.3f}[ja{k}]")
            events.append((cur_len - dx / 2, t))
            ends.append(cur_len)
            starts.append(cur_len - dx)
            cur_len += sd - dx
        else:
            chains.append(f"{cur_v}{cur_a}{sv}{sa}concat=n=2:v=1:a=1[jc{k}][ja{k}];[jc{k}]{norm}[jx{k}]")
            if t is not None:
                events.append((cur_len, t))
            ends.append(cur_len)
            starts.append(cur_len)
            cur_len += sd
        cur_v, cur_a = f"[jx{k}]", f"[ja{k}]"
    ends.append(cur_len)
    last_v, last_a = cur_v, cur_a
    total = cur_len
    if trans and not opts.get("intro_file") and not opts.get("trans_force"):
        o_id = (opts.get("transitions_cfg") or {}).get("ouverture")
        o_t = tr.BY_ID.get(o_id)
        if o_t is not None and o_t.famille == "habillage" and o_t.effet in (opts.get("stingers") or {}):
            events.insert(0, (0.0, o_t))         # ouverture sur le 1er clip (début de l'animation coupé)

    # filigrane (plaque pseudo ou logo) : une seule image, visible pendant les clips seulement
    logo = opts.get("plaque") or opts.get("logo") or paths.res("assets", "filigrane.png")
    args += ["-loop", "1", "-framerate", str(FPS), "-i", logo]
    li = idx
    idx += 1
    width = "" if opts.get("plaque") else "scale=430:-1,"
    w0 = ends[first_clip - 1] if first_clip > 0 else 0.0                          # intro finie
    w1 = starts[last_clip + 1] if last_clip + 1 < len(starts) else ends[last_clip]   # avant la fin
    chains.append(f"[{li}:v]{width}format=rgba,colorchannelmixer=aa=0.95[wm]")
    chains.append(f"{last_v}[wm]overlay=(W-w)/2:150:shortest=1:format=auto:enable='between(t,{w0:.3f},{w1:.3f})'[wmv]")
    last_v = "[wmv]"

    # habillages superposés aux coupes franches
    over = [(at, t) for at, t in events if t.famille == "habillage"]
    stingers = opts.get("stingers") or {}
    for src in sorted({t.effet for _, t in over}):
        info = stingers[src]
        uses = [at for at, t in over if t.effet == src]
        pre = ["-c:v", "libvpx-vp9"] if info.get("webm_alpha") else []
        args += pre + ["-i", info["file"]]
        si = idx
        idx += 1
        fit = ""
        if info.get("fit") == "large":
            # transition horizontale (OBS 16:9) : un peu moins de zoom pour garder le logo entier,
            # puis le haut et le bas prolongés pour couvrir tout l'écran vertical
            h = int(H * 0.82) // 2 * 2
            b = (H - h) // 2
            fit = (f"scale=-2:{h},crop={W}:{h},format=gbrap,pad={W}:{H}:0:{b}:color=black@0,"
                   f"fillborders=top={b}:bottom={H - h - b}:mode=smear,")
        elif info.get("fit"):
            fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        chains.append(f"[{si}:v]{fit}format=rgba,fps={FPS},split={len(uses)}" +
                      "".join(f"[s{src}{j}]" for j in range(len(uses))))
        for j, at in enumerate(uses):
            skip = f"trim=start={info['cut'] - at:.3f}," if at < info["cut"] else ""
            chains.append(f"[s{src}{j}]{skip}setpts=PTS-STARTPTS+{max(0.0, at - info['cut']):.3f}/TB[d{src}{j}]")
            chains.append(f"{last_v}[d{src}{j}]overlay=eof_action=pass:format=auto[o{src}{j}]")
            last_v = f"[o{src}{j}]"
        if info.get("audio"):                  # son du fichier de transition (pack)
            chains.append(f"[{si}:a]aresample=48000,aformat=channel_layouts=stereo,asplit={len(uses)}" +
                          "".join(f"[sa{src}{j}]" for j in range(len(uses))))
            for j, at in enumerate(uses):
                ms = int(max(0.0, at - info["cut"]) * 1000)
                askip = f"atrim=start={info['cut'] - at:.3f},asetpts=PTS-STARTPTS," if at < info["cut"] else ""
                chains.append(f"[sa{src}{j}]{askip}adelay={ms}|{ms},apad,atrim=0:{total:.3f}[sb{src}{j}]")
            chains.append(f"{last_a}" + "".join(f"[sb{src}{j}]" for j in range(len(uses))) +
                          f"amix=inputs={len(uses) + 1}:duration=first:normalize=0[am{src}]")
            last_a = f"[am{src}]"
    sig = [at for at, t in over if t.effet == "signal"]
    if sig:
        from brand import SIGNAL_FX
        win = "+".join(f"between(t,{max(0.0, c - SIGNAL_FX):.3f},{c + SIGNAL_FX:.3f})" for c in sig)
        chains.append(f"{last_v}rgbashift=rh=-22:bh=22:rv=6:bv=-6:edge=smear:enable='{win}',"
                      f"noise=alls=38:allf=t+u:enable='{win}',eq=contrast=1.18:saturation=1.3:enable='{win}'[fx]")
        last_v = "[fx]"
    # sons des transitions (whoosh, grésillement…) sur une piste à part
    sons = [(at, t.son) for at, t in events if t.son in ("leger", "whoosh", "grésillement")]
    if sons:
        from brand import transition_track
        wpath = os.path.join(opts.get("whoosh_dir") or os.path.dirname(out_path), ".whoosh_tmp.wav")
        transition_track(wpath, total, sons)
        args += ["-i", wpath]
        wi = idx
        idx += 1
        chains.append(f"[{wi}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f}[wh]")
        chains.append(f"{last_a}[wh]amix=inputs=2:duration=first:normalize=0[amw]")
        last_a = "[amw]"
    opts["_plan"] = [t.id for _, t in events]

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


def theme_module(style):
    """Module d'habillage : « spectre » (identité du pseudo) ou « hexaclip » (menthe)."""
    if style == "spectre":
        import spectre
        return spectre
    import brand
    return brand


def prepare_transitions(opts, th, work, log=print):
    """Choisit les transitions de la compil et prépare les animations nécessaires (signal, balayage, pack)."""
    import random as _r
    import brand
    import transitions as tr
    pack = (opts.get("pack_file") or "").strip()
    pack_ok = bool(pack) and os.path.isfile(pack)
    actives = [t for t in (opts.get("trans_actives") or tr.PAR_DEFAUT) if t in tr.BY_ID and (t != "pack" or pack_ok)]
    ouverture = tr.ouverture_effective(opts.get("trans_ouverture", "auto"), pack_ok)
    besoin = {tr.BY_ID[t].effet for t in actives + [ouverture] if tr.BY_ID[t].famille == "habillage"}
    stingers = {}
    if "signal" in besoin:
        cols = getattr(th, "SIGNAL_COLORS", None)
        stingers["signal"] = {"file": brand.render_signal(paths.data(f"transition_signal_{th.__name__}_v2.mov"), cols),
                              "cut": brand.SIGNAL_CUT}
    if "balayage" in besoin:
        stingers["balayage"] = {"file": th.render_stinger(paths.data(f"transition_{th.__name__}_v2.mov")),
                                "cut": getattr(th, "STINGER_CUT", 0.23)}
    if "pack" in besoin and pack_ok:
        info = ff.probe(pack)
        cut = float(opts.get("pack_cut") or 0) or info["duree"] / 2
        wide = info["w"] and info["h"] and info["w"] / info["h"] > 1.2
        logo_entier = wide and opts.get("pack_cadrage") == "logo"
        stingers["pack"] = {"file": pack, "cut": min(cut, max(0.05, info["duree"] - 0.05)),
                            "fit": "large" if logo_entier else True,
                            "audio": info["son"],
                            "webm_alpha": pack.lower().endswith(".webm") and ff.has_decoder("libvpx-vp9")}
    opts["stingers"] = stingers
    opts["whoosh_dir"] = work
    opts["transitions_cfg"] = {"actives": actives, "ouverture": ouverture,
                               "graine": opts.get("trans_graine", _r.randrange(1 << 30))}


def make(chosen, out_dir, opts, log=print):
    """Monte la compil. Renvoie (chemin, durée). Repli sur libx264 si l'encodeur matériel refuse.
    opts : style (spectre | hexaclip), intro = (titre, sous_titre), pseudo, appel (texte de fin),
    transitions (bool), en plus de build()."""
    os.makedirs(out_dir, exist_ok=True)
    name = "HexaClip_" + dt.datetime.now().strftime("%Y-%m-%d_%Hh%M") + ".mp4"
    out = os.path.join(out_dir, name)
    work = os.path.join(out_dir, ".travail")
    os.makedirs(work, exist_ok=True)
    tmp = []
    opts = dict(opts)
    th = theme_module(opts.get("style", "hexaclip"))
    opts["theme"] = th
    pseudo = (opts.get("pseudo") or "").strip()
    trans = bool(opts.get("transitions", True))
    try:
        if trans:
            try:
                prepare_transitions(opts, th, work, log)
                tmp.append(os.path.join(work, ".whoosh_tmp.wav"))
            except Exception as e:
                log(f"Transitions ignorées : {e}")
                opts.pop("transitions_cfg", None)
        if pseudo and not opts.get("logo"):
            opts["plaque"] = th.nameplate(pseudo, os.path.join(work, "plaque.png"))
            tmp.append(opts["plaque"])
        if opts.get("intro"):
            p = os.path.join(work, "intro.mp4")
            try:
                if hasattr(th, "render_intro"):
                    opts["intro_file"] = th.render_intro(p, *opts["intro"], pseudo=pseudo, fondu=not trans)
                else:
                    import intro
                    opts["intro_file"] = intro.render(p, *opts["intro"], pseudo=pseudo, fondu=not trans)
                tmp.append(p)
            except Exception as e:
                log(f"Intro ignorée : {e}")
        if opts.get("fin") is not None or pseudo or opts.get("appel"):
            p = os.path.join(work, "fin.mp4")
            try:
                opts["outro_file"] = th.render_outro(p, pseudo, opts.get("fin"), opts.get("appel", ""))
                opts["outro_s"] = th.OUTRO_S
                tmp.append(p)
            except Exception as e:
                log(f"Écran de fin animé ignoré : {e}")
        return _make(chosen, out, opts, log)
    finally:
        for p in tmp:
            try:
                os.remove(p)
            except OSError:
                pass


def _make(chosen, out, opts, log):
    enc = opts.get("encodeur", "libx264")
    tries = [enc] + (["libx264"] if enc != "libx264" else [])
    last = None
    for e in tries:
        cmd, total = build(chosen, out, {**opts, "encodeur": e})
        r = ff.run(cmd, timeout=900)
        if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 10000:
            return out, total
        last = (r.stderr or b"").decode("utf-8", "ignore")[-3000:]
        log(f"Montage avec {e} refusé :\n{last.strip()[-1500:]}")
    raise RuntimeError(f"Le montage a échoué :\n{last}")
