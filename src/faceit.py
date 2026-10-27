# -*- coding: utf-8 -*-
"""
Suivi du grind Faceit avec l'API officielle (FACEIT Data API v4).

Il faut une clé API gratuite (« Server side ») créée sur developers.faceit.com, et ton pseudo Faceit.
L'API ne donne pas l'Elo match par match : HexaClip relève ton Elo régulièrement et après chaque
match, et enregistre lui-même la courbe et l'Elo gagné ou perdu par match.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://open.faceit.com/data/v4"
GAME = "cs2"


class FaceitError(Exception):
    pass


class Faceit:
    def __init__(self, key, pseudo, db, log=print):
        self.key = (key or "").strip()
        self.pseudo = (pseudo or "").strip()
        self.db = db
        self.log = log
        self.player_id = None
        self.profile = {}
        self.last_refresh = 0.0
        self.last_error = ""

    def configured(self):
        return bool(self.key and self.pseudo)

    # --- appels API -------------------------------------------------------
    def _get(self, path, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(API + path + q, headers={
            "Authorization": f"Bearer {self.key}", "Accept": "application/json", "User-Agent": "HexaClip"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise FaceitError("Clé API Faceit refusée : vérifie-la dans les réglages.")
            if e.code == 404:
                raise FaceitError("Introuvable sur Faceit (pseudo ?).")
            if e.code == 429:
                raise FaceitError("Trop de demandes à Faceit, nouvel essai plus tard.")
            raise FaceitError(f"Faceit a répondu {e.code}.")
        except (urllib.error.URLError, TimeoutError) as e:
            raise FaceitError(f"Faceit injoignable ({getattr(e, 'reason', e)}).")

    def player(self):
        d = self._get("/players", nickname=self.pseudo, game=GAME)
        g = ((d.get("games") or {}).get(GAME)) or {}
        self.player_id = d.get("player_id")
        self.profile = {
            "pseudo": d.get("nickname", self.pseudo),
            "elo": _int(g.get("faceit_elo")),
            "niveau": _int(g.get("skill_level")),
            "region": g.get("region", ""),
            "pays": d.get("country", ""),
            "steamid": str(d.get("steam_id_64") or g.get("game_player_id") or ""),
        }
        if self.profile["elo"] is None:
            raise FaceitError("Ce compte Faceit n'a pas encore d'Elo CS2.")
        return self.profile

    def history(self, limit=20):
        d = self._get(f"/players/{self.player_id}/history", game=GAME, offset=0, limit=limit)
        return d.get("items") or []

    def match_stats(self, match_id):
        return self._get(f"/matches/{match_id}/stats")

    # --- synchronisation --------------------------------------------------
    def refresh(self, max_new_stats=8):
        """Met à jour le profil, relève l'Elo et complète les derniers matchs. Renvoie le profil."""
        if not self.configured():
            raise FaceitError("Ajoute ton pseudo et ta clé API Faceit dans les réglages.")
        prev = self.db.elo_series()
        prev_elo = prev[-1]["elo"] if prev else None
        prev_t = prev[-1]["t"] if prev else 0
        prof = self.player()
        now = time.time()
        changed = self.db.add_elo(prof["elo"], prof["niveau"], now)
        items = self.history()
        known = {m["match_id"]: m for m in self.db.matchs(200)}
        fetched = 0
        for it in items:
            mid = it.get("match_id")
            if not mid:
                continue
            if it.get("status") and str(it.get("status")).upper() not in ("FINISHED",):
                continue
            row = parse_history_item(it, self.player_id)
            k = known.get(mid)
            if (not k or k.get("kills") is None) and fetched < max_new_stats:
                try:
                    row.update(parse_match_stats(self.match_stats(mid), self.player_id))
                except FaceitError as e:
                    self.log(f"Stats du match {mid} : {e}")
                fetched += 1
            self.db.upsert_match(row)
            if row.get("debut") and row.get("fin"):
                self.db.link_moments_to_match(mid, row["debut"], row["fin"])
        # Elo du dernier match : si un seul match est fini depuis le relevé précédent
        if changed and prev_elo is not None:
            new_since = [it for it in items if (it.get("finished_at") or 0) > prev_t]
            if len(new_since) == 1:
                self.db.upsert_match({"match_id": new_since[0]["match_id"], "elo_apres": prof["elo"],
                                      "elo_delta": prof["elo"] - prev_elo})
            elif items:
                self.db.upsert_match({"match_id": items[0]["match_id"], "elo_apres": prof["elo"]})
        self.last_refresh = now
        self.last_error = ""
        return prof

    def session_summary(self, since):
        """Elo gagné, victoires et défaites depuis `since` (début de session)."""
        series = self.db.elo_series()
        cur = series[-1] if series else None
        before = self.db.elo_before(since)
        ms = [m for m in self.db.matchs(100) if (m.get("fin") or 0) >= since]
        w = sum(1 for m in ms if m.get("victoire") == 1)
        l_ = sum(1 for m in ms if m.get("victoire") == 0)
        return {
            "elo": cur["elo"] if cur else None,
            "niveau": cur["niveau"] if cur else None,
            "delta": (cur["elo"] - before) if (cur and before is not None) else None,
            "victoires": w, "defaites": l_,
        }


def outro_lines(summary):
    """Texte de l'écran de fin d'une compil, ou None s'il n'y a rien à afficher."""
    if not summary or summary.get("elo") is None:
        return None
    d, lvl, elo = summary.get("delta"), summary.get("niveau"), summary.get("elo")
    wl = ""
    if summary.get("victoires") or summary.get("defaites"):
        wl = f" · {summary['victoires']} V – {summary['defaites']} D"
    if d and d > 0:
        return {"ligne1": f"+{d} ELO cette session", "ligne2": f"Niveau {lvl} · {fmt_elo(elo)} Elo{wl}"}
    return {"ligne1": f"{fmt_elo(elo)} ELO", "ligne2": f"Niveau {lvl}{wl}"}


def fmt_elo(v):
    return f"{v:,}".replace(",", " ") if isinstance(v, int) else "?"


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_history_item(it, player_id):
    row = {"match_id": it.get("match_id"), "debut": it.get("started_at"), "fin": it.get("finished_at")}
    teams = it.get("teams") or {}
    mine = None
    for fac, t in teams.items():
        for p in (t or {}).get("players") or []:
            if p.get("player_id") == player_id:
                mine = fac
    res = it.get("results") or {}
    if mine and res.get("winner"):
        row["victoire"] = 1 if res["winner"] == mine else 0
    sc = res.get("score") or {}
    if mine and sc:
        other = next((f for f in sc if f != mine), None)
        if other:
            row["score"] = f"{sc.get(mine)}-{sc.get(other)}"
    return row


def parse_match_stats(d, player_id):
    out = {}
    rounds = d.get("rounds") or []
    if not rounds:
        return out
    r0 = rounds[0]
    rs = r0.get("round_stats") or {}
    from gsi_cs2 import pretty_map
    out["carte"] = pretty_map(rs.get("Map", ""))
    for t in r0.get("teams") or []:
        for p in t.get("players") or []:
            if p.get("player_id") != player_id:
                continue
            s = p.get("player_stats") or {}
            out.update({
                "kills": _int(s.get("Kills")), "morts": _int(s.get("Deaths")), "assists": _int(s.get("Assists")),
                "kd": _float(s.get("K/D Ratio")), "adr": _float(s.get("ADR")),
                "hs": _int(s.get("Headshots %")),
            })
            if s.get("Result") in ("0", "1"):
                out["victoire"] = int(s["Result"])
            # score du point de vue de ton équipe
            score = rs.get("Score", "")
            if "/" in score and t.get("team_id") and rs.get("Winner"):
                a, b = [x.strip() for x in score.split("/")[:2]]
                hi, lo = max(int(a), int(b)), min(int(a), int(b))
                out["score"] = f"{hi}-{lo}" if rs["Winner"] == t["team_id"] else f"{lo}-{hi}"
    return out
