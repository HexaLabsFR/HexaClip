# -*- coding: utf-8 -*-
"""
Détection des kills à clipper à partir des messages GSI de CS2.

CS2 donne, pour TON joueur, le nombre de kills du round en cours (player.state.round_kills) et les
headshots (round_killhs). HexaClip note l'heure de chaque nouveau kill ; à la fin de ta vie dans le
round (mort, fin de round, nouveau round, fin de match), le round devient une « action » qui garde
chaque kill séparément : tu choisis ensuite lesquels vont dans la compil (comme sur Allstar).

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

    def __init__(self, on_moment, min_kills=1):
        self.on_moment = on_moment
        self.min_kills = min_kills
        self.lock = threading.Lock()
        self._reset_round()
        self.map = ""
        self.mode = ""
        self.round_no = 0
        self.partie = 0            # numéro de partie (match) dans la session
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
            self._partie_map = None           # la prochaine map est une nouvelle partie
            return
        # nouvelle partie : autre map, ou compteur de rounds qui repart de zéro
        new_match = (mp.get("name") != getattr(self, "_partie_map", None)) or (new_round < self.round_no - 1)

        if new_match or new_round != self.round_no or (phase == "freezetime" and self.last_round_phase != "freezetime"):
            self._close(t, "nouveau round")       # le round précédent garde sa partie
            self._reset_round()
            self.round_no = new_round
        if new_match:
            self.partie += 1
            self._partie_map = mp.get("name")
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
        hs_list = [bool(k[1]) for k in self.kills]
        hs = sum(hs_list)
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
            "kills_hs": hs_list,
            "partie": self.partie,
            "raison": why,
        }
        try:
            self.on_moment(m)
        except Exception:
            pass


def manual_moment(now, duration, carte="", round_no=0, partie=0):
    return {
        "cree": now - duration,
        "carte": carte,
        "round": round_no,
        "partie": partie,
        "mode": "",
        "kills": 0,
        "hs": 0,
        "duree_kills": 0.0,
        "type": "manuel",
        "score": score(0, manual=True),
        "kills_abs": [],
        "kills_hs": [],
        "fenetre_abs": [now - duration, now + 0.5],
        "raison": "touche",
    }


# ----------------------------------------------------------------------------
# Choix des kills pour la compil
# ----------------------------------------------------------------------------
def merge(windows, gap=0.6):
    out = []
    for a, b in sorted(windows):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def kill_windows(m):
    """Fenêtres par kill (dans le fichier). Clips manuels / anciens clips : une seule fenêtre."""
    cuts = m.get("coupes") or []
    kills = m.get("kills_t") or []
    if kills and len(cuts) == len(kills):
        return [list(c) for c in cuts]
    return [list(c) for c in cuts] if not kills else [list(c) for c in merge(cuts)]


def per_kill(m):
    """True si chaque kill a sa propre fenêtre (choix kill par kill possible)."""
    return bool(m.get("kills_t")) and len(m.get("coupes") or []) == len(m["kills_t"])


def choice_duration(m, idx):
    wins = kill_windows(m)
    if not per_kill(m):
        return sum(b - a for a, b in merge(wins)) if idx else 0.0
    return sum(b - a for a, b in merge([wins[i] for i in idx if i < len(wins)]))


def all_indices(m):
    return list(range(len(m["kills_t"]))) if per_kill(m) else [0]


def auto_choice(moments, budget_s):
    """Sélection automatique : favoris, puis les meilleurs rounds entiers (aces, 4k, 3k…), puis les kills
    seuls à la tête, tant que ça rentre dans la durée. Renvoie {id: [indices]}."""
    order = sorted(moments, key=lambda m: (m.get("favori", 0), m.get("score", 0), sum(m.get("kills_hs") or [])),
                   reverse=True)
    out, used = {m["id"]: [] for m in moments}, 0.0
    for m in order:
        idx = all_indices(m)
        d = choice_duration(m, idx)
        if d <= 0:
            continue
        if used + d <= budget_s + 0.5:
            out[m["id"]] = idx
            used += d
        elif per_kill(m) and m.get("kills", 0) <= 2:
            # essayer un seul kill (le headshot de préférence)
            hs = m.get("kills_hs") or []
            pick = next((i for i in idx if i < len(hs) and hs[i]), idx[-1])
            d1 = choice_duration(m, [pick])
            if used + d1 <= budget_s + 0.5:
                out[m["id"]] = [pick]
                used += d1
        if used >= budget_s - 2:
            break
    return out


def build_item(m, idx):
    """Prépare une action pour le montage avec seulement les kills choisis."""
    item = dict(m)
    if not per_kill(m):
        item["coupes"] = merge(kill_windows(m)) if idx else []
        return item
    idx = sorted(i for i in idx if i < len(m["kills_t"]))
    wins = kill_windows(m)
    item["coupes"] = merge([wins[i] for i in idx])
    item["kills_t"] = [m["kills_t"][i] for i in idx]
    if len(idx) != len(m["kills_t"]):
        item["type"] = "partiel"           # compteur 1 KILL, 2 KILLS… sans annoncer un 3K incomplet
    return item
