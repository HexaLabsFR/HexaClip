# -*- coding: utf-8 -*-
"""
Vérification de HexaClip sans jeu ni écran : `HexaClip.exe --selftest --out rapport.txt`.

1. Détection des kills : une fausse partie CS2 (messages GSI) avec un 3k, un 2k, une mort, un changement de map.
2. Choix des kills (sélection auto, kills partiels) et mise à jour (comparaison de versions).
3. Faceit : lecture de réponses d'API types.
4. Si FFmpeg est là : faux écran (mire + son) enregistré en continu ; round simulé avec un kill puis deux
   autres 13 s plus tard → un seul clip qui recolle les deux passages → compil avec les kills choisis
   puis compil automatique, 1080×1920 contrôlées.
Chaque ligne finit par OK ou ERREUR.
"""
from __future__ import annotations

import os
import shutil
import tempfile
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
    import updater
    check("comparaison de versions", updater.newer("0.10.0", "0.9.3") and not updater.newer("0.2.0", "0.2.0")
          and updater.newer("v1.0", "0.9"))


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
    finally:
        e.recorder.stop()
        e.cs2_running = False
    ms = e.db.moments()
    if ms:
        out = e.compile(selection={ms[0]["id"]: [0, 2]})
        ok = bool(out and os.path.isfile(out))
        info = ff.probe(out) if ok else {}
        check("compil avec les kills choisis", ok and info.get("w") == 1080 and info.get("h") == 1920
              and info.get("son") and 9 < info.get("duree", 0) < 13,
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
        for fn in (test_tracker, test_choice, test_faceit):
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
