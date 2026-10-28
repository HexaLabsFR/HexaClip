# -*- coding: utf-8 -*-
"""
Vérification de HexaClip sans jeu ni écran : `HexaClip.exe --selftest --out rapport.txt`.

1. Détection des kills : une fausse partie CS2 (messages GSI) avec un 3k, un 2k, une mort, un changement de map.
2. Choix des kills (sélection auto, kills partiels) et mise à jour (comparaison de versions).
3. Son : conversion 5.1 → stéréo 48 kHz et flux continu sans son. Faceit : réponses d'API types.
4. Si FFmpeg est là : faux écran (mire + son) enregistré en continu ; round simulé avec un kill puis deux
   autres 13 s plus tard → un seul clip qui recolle les deux passages → compil avec les kills choisis
   puis compil automatique, 1080×1920 contrôlées.
Chaque ligne finit par OK ou ERREUR.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import traceback

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"{name} : {'OK' if cond else 'ERREUR'}{(' (' + detail + ')') if detail else ''}")


def payload(rnd=11, phase="live", k=0, hs=0, health=100, own=True, map_phase="live"):
    return {
        "provider": {"steamid": "76561190000000001"},
        "map": {"name": "de_mirage", "mode": "competitive", "phase": map_phase, "round": rnd},
        "round": {"phase": phase},
        "player": {"steamid": "76561190000000001" if own else "76561190000000002", "activity": "playing",
                   "state": {"health": health, "round_kills": k, "round_killhs": hs}},
    }


def test_tracker():
    from moments import KillTracker, cut_windows, score
    got = []
    tr = KillTracker(got.append, min_kills=1)
    t = 1000.0
    seq = [(payload(11, "freezetime"), 0), (payload(11), 5), (payload(11, k=1, hs=1), 7),
           (payload(11, k=2, hs=1), 8.2), (payload(11, k=3, hs=2), 9.9), (payload(11, phase="over", k=3, hs=2), 15),
           (payload(12, "freezetime"), 22),
           # round suivant : 2 kills puis mort
           (payload(12, k=1), 30), (payload(12, k=2), 31), (payload(12, k=2, own=False), 33),
           (payload(13, "freezetime"), 40)]
    for p, dt_ in seq:
        tr.feed(p, t + dt_)
    check("détection de chaque round avec kills", [g["type"] for g in got] == ["3k", "2k"],
          f"{[(g['type'], g['kills'], g['hs']) for g in got]}")
    check("headshot par kill", got and got[0]["kills_hs"] == [True, False, True], str(got[0]["kills_hs"] if got else ""))
    check("round et map", got and got[0]["round"] == 12 and got[0]["carte"] == "Mirage",
          f"{got[0]['round'] if got else '?'} / {got[0]['carte'] if got else '?'}")
    # nouvelle map = nouvelle partie
    p2 = payload(1, "freezetime")
    p2["map"]["name"] = "de_nuke"
    tr.feed(p2, t + 100)
    p3 = payload(1, k=1)
    p3["map"]["name"] = "de_nuke"
    tr.feed(p3, t + 110)
    p4 = payload(2, "freezetime")
    p4["map"]["name"] = "de_nuke"
    tr.feed(p4, t + 120)
    check("parties distinctes par map", len(got) == 3 and got[2]["partie"] == got[0]["partie"] + 1,
          str([g["partie"] for g in got]))
    w = cut_windows([10, 11, 30], 3.5, 1.8)
    check("fenêtres de coupe", w == [[6.5, 12.8], [26.5, 31.8]], str(w))
    check("note ace > 4k > 3k", score(5) > score(4) > score(3, 0, 20))


def test_choice():
    from moments import auto_choice, build_item, choice_duration
    ace = {"id": 1, "score": 110, "kills": 5, "kills_hs": [1, 0, 1, 0, 0], "type": "ace", "favori": 0,
           "kills_t": [5, 6, 7, 8, 9], "coupes": [[k - 3.5, k + 1.8] for k in [5, 6, 7, 8, 9]]}
    one = {"id": 2, "score": 9, "kills": 1, "kills_hs": [1], "type": "1k", "favori": 0,
           "kills_t": [4], "coupes": [[0.5, 5.8]]}
    spread = {"id": 3, "score": 12, "kills": 2, "kills_hs": [0, 1], "type": "2k", "favori": 0,
              "kills_t": [4, 30], "coupes": [[0.5, 5.8], [26.5, 31.8]]}
    d = choice_duration(ace, [0, 1, 2, 3, 4])
    check("durée d'un ace (fenêtres fusionnées)", abs(d - 9.3) < 0.01, f"{d:.2f} s")
    # 16 s : l'ace entier (9,3 s), puis du 2k étalé seulement le kill à la tête (5,3 s), le 1k ne rentre plus
    c = auto_choice([one, ace, spread], 16)
    check("sélection auto dans la durée", c[1] == [0, 1, 2, 3, 4] and c[3] == [1] and c[2] == [],
          str(c))
    it = build_item(spread, [1])
    check("kill partiel", it["kills_t"] == [30] and it["coupes"] == [[26.5, 31.8]] and it["type"] == "partiel",
          str(it["coupes"]))
    import transitions as tr
    pl = tr.plan(12, ["fondu", "droite", "zoom", "signal"], "pack", 3)
    check("bibliothèque de transitions", pl[0] == "pack" and all(a != b for a, b in zip(pl[1:], pl[2:]))
          and len(set(pl[1:])) >= 3, " ".join(pl))
    import updater
    check("comparaison de versions", updater.newer("0.10.0", "0.9.3") and not updater.newer("0.2.0", "0.2.0")
          and updater.newer("v1.0", "0.9"))


def test_audio():
    """Conversion du son (casque 7.1 en 44,1 kHz → stéréo 48 kHz) et envoi régulier même sans son."""
    import array as _array
    import io
    import math
    import recorder as rec
    frames = 4410                                  # 0,1 s à 44,1 kHz, 6 voies (5.1), son seulement au centre
    a = _array.array("h", [0] * frames * 6)
    for i in range(frames):
        a[i * 6 + 2] = int(8000 * math.sin(i / 10))
    b, _ = rec.to_stereo48(a.tobytes(), 6, 44100)
    st = _array.array("h")
    st.frombytes(b)
    n = len(st) // 2
    check("son 5.1 → stéréo 48 kHz", 4700 <= n <= 4900 and max(st[0::2]) > 3000 and max(st[1::2]) > 3000,
          f"{n} échantillons, crête {max(st)}")
    p = rec.AudioPump(log=lambda *_: None)
    p.ok, p.dev_ch, p.dev_rate = True, 6, 44100
    p._opened_at = p._last_reopen = time.time()
    for _ in range(3):
        p._q.append(a.tobytes())                 # 0,3 s de son, puis plus rien (aucun son joué)
    out = io.BytesIO()
    import threading
    th = threading.Thread(target=p.pump, args=(out,), daemon=True)
    th.start()
    time.sleep(1.0)
    p._stop.set()
    th.join(2)
    secs = len(out.getvalue()) / 4 / 48000
    check("son continu même dans le silence", 0.85 < secs < 1.2 and p.last_sound > 0, f"{secs:.2f} s envoyées")


def test_clip_checks(good, tmp):
    """Clip aux horodatages décalés réparé, clip à l'image figée repéré, bon clip laissé tel quel."""
    import ffmpeg_tools as ff
    import recorder
    bad = os.path.join(tmp, "decale.mp4")
    ff.run([ff.find("ffmpeg"), "-v", "error", "-y", "-i", good, "-map", "0:v", "-map", "0:a", "-c", "copy",
            "-output_ts_offset", "0", "-bsf:v", "setts=pts=PTS+40/TB:dts=DTS+40/TB", bad], timeout=60)
    was_bad = recorder.bad_timestamps(bad)
    fixed = recorder.fix_timestamps(bad)
    check("clip aux horodatages décalés réparé", was_bad and fixed and not recorder.bad_timestamps(bad),
          str(recorder.stream_starts(bad)))
    still = os.path.join(tmp, "fige.mp4")
    ff.run([ff.find("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=6",
            "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p", still], timeout=60)
    check("image figée repérée", recorder.frozen_video(still) and not recorder.frozen_video(good)
          and not recorder.bad_timestamps(good))


def test_demo_logic():
    """Démos : rounds d'un joueur, découpage des passages, outil d'analyse livré."""
    import demos
    me, other = "76561198069647509", "76561190000000009"
    info = {"tickrate": 64, "kills": [
        {"tick": 1000, "round": 3, "tueur": me, "victime": other, "arme": "AK-47", "hs": True},
        {"tick": 1100, "round": 3, "tueur": me, "victime": other, "arme": "AK-47", "hs": False},
        {"tick": 3000, "round": 3, "tueur": me, "victime": other, "arme": "Deagle", "hs": True},
        {"tick": 5000, "round": 4, "tueur": other, "victime": me, "arme": "AWP", "hs": False},
        {"tick": 6000, "round": 5, "tueur": me, "victime": me, "arme": "HE", "hs": False}]}
    rs = demos.player_rounds(info, me)
    check("démo : kills d'un joueur par round", len(rs) == 1 and rs[0]["type"] == "3k" and rs[0]["hs"] == 2
          and rs[0]["armes"] == ["AK-47", "Deagle"], str([(r["round"], r["type"]) for r in rs]))
    sg = demos.segments(rs[0]["kills"], 64, 3.5, 1.8)
    check("démo : passages à lire (saut si les kills sont loin)", len(sg) == 2 and len(sg[0][2]) == 2
          and sg[0][0] == 1000 - int(5.0 * 64), str([(a, b, len(k)) for a, b, k in sg]))
    tool = demos.tool_path()
    import ffmpeg_tools as ff
    ok = bool(tool) and ff.run([tool], timeout=30).returncode == 2
    check("démo : outil d'analyse livré", ok, tool or "absent")
    import paths
    if paths.FROZEN:                       # dans l'exe : le module de rendu CS2 doit être livré
        check("démo : module de rendu CS2 livré", bool(demos.plugin_path()), str(demos.plugin_path()))


def test_demo(e, tmp):
    """Rendu d'une démo sans CS2 : fichier de commandes, module ajouté/retiré de CS2, images + son → clip rangé."""
    import json as _json
    import demos
    import ffmpeg_tools as ff
    me = "76561198069647509"
    info = {"fichier": os.path.join(tmp, "match.dem"), "carte": "de_mirage", "tickrate": 64,
            "joueurs": [{"steamid": me, "nom": "ZayKololo", "userid": 0}],
            "kills": [{"tick": 2000, "round": 7, "tueur": me, "victime": "1", "arme": "AK-47", "hs": True},
                      {"tick": 2064, "round": 7, "tueur": me, "victime": "2", "arme": "AK-47", "hs": False}]}
    rounds = demos.player_rounds(info, me)
    d = demos.DemoClipper(e, info, me, rounds, log=lambda *_: None)
    data = demos.actions_json(d.seqs, 64, d.slot)
    cmds = [a["cmd"] for a in data[0]["actions"]]
    ticks = [a["tick"] for a in data[0]["actions"]]
    check("démo : commandes au tick près (caméra, rendu, sans barre de démo)",
          d.slot == 1 and "spec_player 1" in cmds and "demo_ui_mode 0" in cmds and cmds[-1] == "quit"
          and any(c.startswith("startmovie hexaclip_s01") for c in cmds) and "endmovie" in cmds
          and ticks == sorted(ticks), " | ".join(cmds[-6:]))
    # plusieurs passages : on avance toujours dans la démo (pas de retour au début), rendu seulement pendant
    rs2 = [{"round": 3, "kills": [{"tick": 9000}]}, {"round": 9, "kills": [{"tick": 40000}, {"tick": 40100}]}]
    sq = demos.sequences_for(rs2, 64, 3.5, 1.8, 1)
    acts = demos.actions_json(sq, 64, 1)[0]["actions"]
    jumps = [int(x["cmd"].split()[1]) for x in acts if x["cmd"].startswith("demo_gototick")]
    fr = [x["cmd"] for x in acts if x["cmd"].startswith("host_framerate")]
    check("démo : sauts uniquement vers l'avant, rendu image par image seulement pendant les passages",
          len(sq) == 2 and len(jumps) == 2 and jumps == sorted(jumps) and 0 not in jumps
          and fr == ["host_framerate 0", "host_framerate 60", "host_framerate 0", "host_framerate 60", "host_framerate 0"],
          f"sauts {jumps} ; {fr}")
    # module ajouté puis retiré de CS2 (faux dossier du jeu)
    csgo = os.path.join(tmp, "game", "csgo")
    os.makedirs(csgo, exist_ok=True)
    gi = os.path.join(csgo, "gameinfo.gi")
    orig = 'FileSystem\n{\n\tSearchPaths\n\t{\n\t\t\tGame_LowViolence\tcsgo_lv\n\t\t\tGame\tcsgo\n\t\t\tGame\tcore\n\t}\n}\n'
    with open(gi, "w", encoding="utf-8") as f:
        f.write(orig)
    fake_dll = os.path.join(tmp, "server.dll")
    with open(fake_dll, "wb") as f:
        f.write(b"MZ")
    old_pp = demos.plugin_path
    demos.plugin_path = lambda: fake_dll
    try:
        demos.install_plugin(csgo)
        with open(gi, encoding="utf-8") as f:
            patched = f.read()
        demos.restore_gameinfo(csgo)
        with open(gi, encoding="utf-8") as f:
            back = f.read()
    finally:
        demos.plugin_path = old_pp
    check("démo : module ajouté à CS2 puis retiré", "Game\tcsgo/hexaclip\n\t\t\tGame\tcsgo" in patched and back == orig
          and os.path.isfile(os.path.join(csgo, "hexaclip", "bin", "win64", "server.dll")))
    # images + son comme les écrit « startmovie », puis encodage et rangement
    movie = os.path.join(csgo, "hexaclip", "movie")
    os.makedirs(movie, exist_ok=True)
    name, a, b = d.seqs[0][0], d.seqs[0][1], d.seqs[0][2]
    secs = (b - a) / 64
    ff.run([ff.find("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=60:d={secs:.3f}",
            "-q:v", "4", "-start_number", "0", os.path.join(movie, name + "%04d.jpg")], timeout=120)
    ff.run([ff.find("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:d={secs:.3f}",
            os.path.join(csgo, "movie_" + name + ".tmp.wav")], timeout=60)
    os.makedirs(os.path.join(csgo, "movie"), exist_ok=True)
    os.replace(os.path.join(csgo, "movie_" + name + ".tmp.wav"), os.path.join(csgo, "movie", name + ".wav"))
    d.csgo = csgo
    n0 = len(e.db.moments(limit=1000))
    d._encode_all()
    new = [m for m in e.db.moments(limit=1000) if str(m.get("session", "")).startswith("demo-")]
    ok = len(e.db.moments(limit=1000)) == n0 + 1 and len(new) == 1 and os.path.isfile(new[0]["fichier"])
    info_v = ff.probe(new[0]["fichier"]) if ok else {}
    kt = new[0]["kills_t"] if new else []
    check("démo : passage calculé encodé et rangé comme un clip", ok and info_v.get("son") and new[0]["type"] == "2k"
          and abs(info_v.get("duree", 0) - secs) < 0.2 and len(kt) == 2 and abs(kt[1] - kt[0] - 1.0) < 0.05,
          f"{info_v.get('duree', 0):.2f} s, kills {kt}, son {info_v.get('son')}, {new[0]['type'] if new else ''}")
    d.state = "encodage des clips"
    check("démo : barre de chargement", d.progress()[0] > 0.2, str(d.progress()))
    for m in new:
        e.delete_moment(m["id"])


def test_faceit():
    from faceit import outro_lines, parse_history_item, parse_match_stats
    pid = "abc"
    it = {"match_id": "1-x", "started_at": 1000, "finished_at": 3000,
          "teams": {"faction1": {"players": [{"player_id": pid}]}, "faction2": {"players": [{"player_id": "z"}]}},
          "results": {"winner": "faction1", "score": {"faction1": 13, "faction2": 9}}}
    r = parse_history_item(it, pid)
    check("Faceit historique", r["victoire"] == 1 and r["score"] == "13-9", str(r))
    st = {"rounds": [{"round_stats": {"Map": "de_inferno", "Score": "9 / 13", "Winner": "T2"},
                      "teams": [{"team_id": "T1", "players": [{"player_id": pid, "player_stats": {
                          "Kills": "24", "Deaths": "17", "Assists": "5", "K/D Ratio": "1.41", "ADR": "92.5",
                          "Headshots %": "48", "Result": "0"}}]}]}]}
    s = parse_match_stats(st, pid)
    check("Faceit stats de match", s["kills"] == 24 and s["adr"] == 92.5 and s["score"] == "9-13"
          and s["carte"] == "Inferno" and s["victoire"] == 0, str(s))
    o = outro_lines({"elo": 1812, "niveau": 8, "delta": 75, "victoires": 3, "defaites": 1})
    check("écran de fin", o and o["ligne1"] == "+75 ELO cette session", str(o))


def test_pipeline(tmp):
    """Enregistrement continu d'un faux écran + 3k simulé + compil."""
    import ffmpeg_tools as ff
    if not ff.available():
        print("pipeline vidéo : ignoré (FFmpeg absent)")
        return
    from config import Config
    import engine as eng
    cfg = Config(os.path.join(tmp, "reglages.json"))
    cfg["dossier_videos"] = os.path.join(tmp, "videos")
    cfg["tampon_s"] = 40
    cfg["compil_fin_faceit"] = False
    cfg["pseudo"] = "Testeur"
    e = eng.Engine(cfg, db_path=os.path.join(tmp, "test.db"))
    e.method = {"source": "test", "encodeur": "libx264", "fps": 30, "qualite": 23}
    test_in = ["-re", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
               "-re", "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=48000"]
    real_start = e.recorder.start
    e.recorder.start = lambda method, **kw: real_start(method, test_inputs=test_in)
    e.cs2_running = True
    e._session_begin()
    try:
        time.sleep(17)
        check("enregistrement continu", e.recorder.running and len(e.recorder.segments()) >= 5,
              f"{len(e.recorder.segments())} morceaux")
        t = time.time()
        # un kill, puis deux autres 13 s plus tard, dans le même round
        for p, d in [(payload(4, "freezetime"), -16), (payload(4), -15), (payload(4, k=1, hs=1), -14),
                     (payload(4, k=2, hs=1), -1.5), (payload(4, k=3, hs=1), -1)]:
            e.tracker.feed(p, t + d)
        e.tracker.feed(payload(4, k=3, hs=1, own=False), t)     # mort : le round se clôt
        deadline = time.time() + 30
        while not e.db.moments() and time.time() < deadline:
            time.sleep(0.3)
        ms = e.db.moments()
        check("clip extrait", len(ms) == 1 and os.path.isfile(ms[0]["fichier"]),
              ms[0]["fichier"] if ms else "aucun")
        if ms:
            m = ms[0]
            info = ff.probe(m["fichier"])
            k, cuts = m["kills_t"], m["coupes"]
            inside = len(cuts) == 3 and all(a <= x <= b for x, (a, b) in zip(k, cuts))
            check("deux passages recollés (sans le creux)", 9 < info["duree"] < 19.5 and inside,
                  f"durée {info['duree']:.1f} s, kills {k}")
            check("écart entre les kills raccourci", 4 < (k[1] - k[0]) < 9.5, f"{k[1] - k[0]:.1f} s au lieu de 12.5 s")
            check("miniature du round", bool(m.get("miniature")) and os.path.isfile(m["miniature"]))
            test_demo(e, tmp)
            test_clip_checks(m["fichier"], tmp)
            import apercu
            r = ff.run(apercu.frame_cmd(m["fichier"], frames=3), timeout=60)
            check("aperçu intégré (onglet Clips)", r.returncode == 0 and len(r.stdout) == 3 * apercu.W * apercu.H * 3,
                  f"{len(r.stdout)} octets")
    finally:
        e.recorder.stop()
        e.cs2_running = False
    ms = e.db.moments()
    if ms:
        out = e.compile(selection={ms[0]["id"]: [0, 2]})
        ok = bool(out and os.path.isfile(out))
        info = ff.probe(out) if ok else {}
        check("compil avec les kills choisis", ok and info.get("w") == 1080 and info.get("h") == 1920
              and info.get("son") and 13.5 < info.get("duree", 0) < 17.5,      # kills + intro 1,7 s + fin 2,8 s
              f"{info.get('w')}x{info.get('h')} {info.get('duree', 0):.1f} s" if ok else "pas de fichier")
    out = e.compile(session=e.session_id)
    ok = bool(out and os.path.isfile(out))
    info = ff.probe(out) if ok else {}
    check("compil TikTok automatique", ok and info.get("w") == 1080 and info.get("h") == 1920 and info.get("son"),
          f"{info.get('w')}x{info.get('h')} {info.get('duree', 0):.1f} s" if ok else "pas de fichier")
    e.gsi.stop()


def run():
    tmp = tempfile.mkdtemp(prefix="hexaclip_test_")
    try:
        for fn in (test_tracker, test_choice, test_audio, test_demo_logic, test_faceit):
            try:
                fn()
            except Exception:
                check(fn.__name__, False, traceback.format_exc(limit=3))
        try:
            test_pipeline(tmp)
        except Exception:
            check("pipeline vidéo", False, traceback.format_exc(limit=4))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for n, ok in RESULTS if not ok]
    print(f"Bilan : {len(RESULTS) - len(bad)}/{len(RESULTS)} OK" + (f" — ERREUR sur {', '.join(bad)}" if bad else ""))
    return not bad


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
