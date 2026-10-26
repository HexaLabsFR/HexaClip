# -*- coding: utf-8 -*-
"""
Détection des actions à clipper à partir des messages GSI de CS2.

CS2 donne, pour TON joueur, le nombre de kills du round en cours (player.state.round_kills) et les
headshots (round_killhs). HexaClip note l'heure de chaque nouveau kill ; à la fin de ta vie dans le
round (mort, fin de round, nouveau round, fin de match), si tu as fait assez de kills, l'action est
créée avec ses moments à garder.

Limite connue : le jeu ne dit pas à un joueur combien d'alliés sont encore en vie, donc les clutchs
ne sont pas détectés en tant que tels (un clutch à 3 kills et plus est quand même clippé).
"""
from __future__ import annotations

import threading
import time

from gsi_cs2 import pretty_map

TYPES = {1: "1k", 2: "2k", 3: "3k", 4: "4k", 5: "ace"}
LABELS = {"1k": "1 KILL", "2k": "2K", "3k": "3K", "4k": "4K", "ace": "ACE", "manuel": "CLIP"}
BASE = {1: 5, 2: 12, 3: 30, 4: 55, 5: 100}


def kill_type(n):
    return TYPES.get(min(n, 5), "1k") if n > 0 else "manuel"


def score(kills, hs=0, span=None, manual=False):
    """Note d'une action pour choisir les meilleures de la compil (sur 100 environ)."""
    if manual:
        return 20.0
    s = BASE.get(min(kills, 5), 0) + 4 * min(hs, kills)
    if kills >= 3 and span is not None:
        if span <= 5:
            s += 15        # enchaînement très rapide
        elif span <= 10:
            s += 7
    return float(s)


def cut_windows(kill_times, before, after):
    """Fenêtres [début, fin] autour des kills, fusionnées quand elles se touchent."""
    wins = []
    for k in sorted(kill_times):
        a, b = k - before, k + after
        if wins and a <= wins[-1][1] + 0.6:
            wins[-1][1] = max(wins[-1][1], b)
        else:
            wins.append([a, b])
    return wins


class KillTracker:
    """Suit tes kills round par round et appelle on_moment(dict) quand une action mérite un clip."""

    def __init__(self, on_moment, min_kills=3):
        self.on_moment = on_moment
        self.min_kills = min_kills
        self.lock = threading.Lock()
        self._reset_round()
        self.map = ""
        self.mode = ""
        self.round_no = 0
        self.match_live = False
        self.last_round_phase = ""

    def _reset_round(self):
        self.kills = []            # [(heure, headshot)]
        self.last_k = 0
        self.last_hs = 0
        self.closed = False        # action du round déjà évaluée

    def feed(self, p: dict, t: float | None = None):
        t = t or time.time()
        with self.lock:
            self._feed(p, t)

    def _feed(self, p, t):
        mp = p.get("map") or {}
        rnd = p.get("round") or {}
        player = p.get("player") or {}
        provider = p.get("provider") or {}
        self.map = mp.get("name") or self.map
        self.mode = mp.get("mode") or self.mode
        if "round" in mp:
            # map.round = nombre de rounds déjà joués ; le round en cours est le suivant
            new_round = int(mp.get("round") or 0) + 1
        else:
            new_round = self.round_no
        phase = rnd.get("phase") or ""

        if not mp.get("name"):            # retour au menu : on clôt ce qui reste
            self._close(t, "menu")
            self._reset_round()
            self.round_no = 0
            return

        if new_round != self.round_no or (phase == "freezetime" and self.last_round_phase != "freezetime"):
            self._close(t, "nouveau round")
            self._reset_round()
            self.round_no = new_round
        self.last_round_phase = phase

        me = provider.get("steamid")
        own = bool(player) and (not me or player.get("steamid") == me)
        if not own:
            # tu es mort et tu regardes un allié : plus de kills possibles ce round
            if self.kills:
                self._close(t, "mort")
            return
        state = player.get("state") or {}
        k = int(state.get("round_kills") or 0)
        hs = int(state.get("round_killhs") or 0)
        if k < self.last_k:               # remise à zéro inattendue
            self._close(t, "remise à zéro")
            self._reset_round()
        if k > self.last_k and not self.closed:
            new_hs = max(0, hs - self.last_hs)
            for i in range(k - self.last_k):
                self.kills.append((t, i < new_hs))
        self.last_k, self.last_hs = k, hs
        health = state.get("health")
        if health == 0 and self.kills:
            self._close(t, "mort")
        if mp.get("phase") == "gameover":
            self._close(t, "fin du match")

    def tick(self, t=None):
        """À appeler régulièrement : clôt une action si CS2 ne donne plus de nouvelles."""
        t = t or time.time()
        with self.lock:
            if self.kills and not self.closed and t - self.kills[-1][0] > 25:
                self._close(t, "délai")

    def _close(self, t, why):
        if self.closed or not self.kills:
            self.closed = True if self.kills else self.closed
            return
        self.closed = True
        n = len(self.kills)
        if n < self.min_kills:
            return
        times = [k[0] for k in self.kills]
        hs = sum(1 for k in self.kills if k[1])
        span = times[-1] - times[0]
        m = {
            "cree": times[0],
            "carte": pretty_map(self.map),
            "round": self.round_no,
            "mode": self.mode,
            "kills": n,
            "hs": hs,
            "duree_kills": round(span, 2),
            "type": kill_type(n),
            "score": score(n, hs, span),
            "kills_abs": times,
            "raison": why,
        }
        try:
            self.on_moment(m)
        except Exception:
            pass


def manual_moment(now, duration, carte="", round_no=0):
    return {
        "cree": now - duration,
        "carte": carte,
        "round": round_no,
        "mode": "",
        "kills": 0,
        "hs": 0,
        "duree_kills": 0.0,
        "type": "manuel",
        "score": score(0, manual=True),
        "kills_abs": [],
        "fenetre_abs": [now - duration, now + 0.5],
        "raison": "touche",
    }
