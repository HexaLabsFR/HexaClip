# -*- coding: utf-8 -*-
"""Base locale SQLite : actions (clips), compils, matchs Faceit et relevés d'Elo."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS moments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cree REAL NOT NULL,             -- heure (epoch) de l'action
    session TEXT,                   -- identifiant de la session CS2
    carte TEXT, round INTEGER, mode TEXT,
    kills INTEGER, hs INTEGER, duree_kills REAL,
    type TEXT,                      -- 3k, 4k, ace, manuel...
    score REAL,
    fichier TEXT,                   -- clip brut (mp4)
    coupes TEXT,                    -- JSON [[debut, fin], ...] en secondes dans le fichier
    kills_t TEXT,                   -- JSON instants des kills dans le fichier
    son INTEGER DEFAULT 1,
    favori INTEGER DEFAULT 0,
    exclu INTEGER DEFAULT 0,
    match_faceit TEXT,
    kills_hs TEXT,                  -- JSON [true, false…] headshot de chaque kill
    choix TEXT,                     -- JSON indices des kills choisis pour la compil (NULL = automatique)
    miniature TEXT,                 -- image PNG du round
    partie INTEGER                  -- numéro de partie dans la session
);
CREATE TABLE IF NOT EXISTS compils (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cree REAL NOT NULL, fichier TEXT, duree REAL, moments TEXT, session TEXT
);
CREATE TABLE IF NOT EXISTS elo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t REAL NOT NULL, elo INTEGER, niveau INTEGER
);
CREATE TABLE IF NOT EXISTS matchs (
    match_id TEXT PRIMARY KEY,
    debut REAL, fin REAL, carte TEXT, score TEXT, victoire INTEGER,
    kills INTEGER, morts INTEGER, assists INTEGER, kd REAL, adr REAL, hs INTEGER,
    elo_apres INTEGER, elo_delta INTEGER
);
"""


class DB:
    def __init__(self, path=None):
        self.path = path or paths.data("hexaclip.db")
        self.lock = threading.Lock()
        self.c = sqlite3.connect(self.path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        with self.lock:
            self.c.executescript(SCHEMA)
            # bases créées par la 0.1 : ajouter les colonnes manquantes
            have = {r[1] for r in self.c.execute("PRAGMA table_info(moments)")}
            for col, typ in (("kills_hs", "TEXT"), ("choix", "TEXT"), ("miniature", "TEXT"), ("partie", "INTEGER")):
                if col not in have:
                    self.c.execute(f"ALTER TABLE moments ADD COLUMN {col} {typ}")
            self.c.commit()

    def _x(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args)
            self.c.commit()
            return cur

    def _all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    # --- actions ---------------------------------------------------------
    def add_moment(self, m: dict) -> int:
        cols = ["cree", "session", "carte", "round", "mode", "kills", "hs", "duree_kills", "type", "score",
                "fichier", "coupes", "kills_t", "son", "kills_hs", "miniature", "partie"]
        vals = [m.get(c) for c in cols]
        for i, c in enumerate(cols):
            if c in ("coupes", "kills_t", "kills_hs") and not isinstance(vals[i], str):
                vals[i] = json.dumps(vals[i] or [])
        cur = self._x(f"INSERT INTO moments ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        return cur.lastrowid

    def moments(self, since=None, session=None, limit=500):
        q, a = "SELECT * FROM moments WHERE 1=1", []
        if since is not None:
            q += " AND cree >= ?"
            a.append(since)
        if session:
            q += " AND session = ?"
            a.append(session)
        q += " ORDER BY cree DESC LIMIT ?"
        a.append(limit)
        return [self._decode(r) for r in self._all(q, a)]

    @staticmethod
    def _decode(r):
        r["coupes"] = json.loads(r["coupes"] or "[]")
        r["kills_t"] = json.loads(r["kills_t"] or "[]")
        r["kills_hs"] = json.loads(r.get("kills_hs") or "[]")
        r["choix"] = json.loads(r["choix"]) if r.get("choix") is not None else None
        return r

    def moments_by_ids(self, ids):
        ids = list(ids)
        if not ids:
            return []
        q = f"SELECT * FROM moments WHERE id IN ({','.join('?' * len(ids))}) ORDER BY cree"
        return [self._decode(r) for r in self._all(q, ids)]

    def sessions(self, limit=30):
        """Sessions de jeu (du lancement à la fermeture de CS2), les plus récentes d'abord."""
        return self._all("SELECT session, MIN(cree) AS debut, MAX(cree) AS fin, COUNT(*) AS rounds, "
                         "SUM(kills) AS kills FROM moments WHERE session IS NOT NULL GROUP BY session "
                         "ORDER BY debut DESC LIMIT ?", (limit,))

    def set_choice(self, mid, idx):
        self._x("UPDATE moments SET choix=? WHERE id=?", (None if idx is None else json.dumps(list(idx)), mid))

    def set_moment(self, mid, **kw):
        if not kw:
            return
        self._x(f"UPDATE moments SET {', '.join(k + '=?' for k in kw)} WHERE id=?", [*kw.values(), mid])

    def delete_moment(self, mid):
        self._x("DELETE FROM moments WHERE id=?", (mid,))

    # --- compils ---------------------------------------------------------
    def add_compil(self, fichier, duree, moment_ids, session=None):
        return self._x("INSERT INTO compils (cree, fichier, duree, moments, session) VALUES (?,?,?,?,?)",
                       (time.time(), fichier, duree, json.dumps(moment_ids), session)).lastrowid

    def compils(self, limit=200):
        return self._all("SELECT * FROM compils ORDER BY cree DESC LIMIT ?", (limit,))

    def delete_compil(self, cid):
        self._x("DELETE FROM compils WHERE id=?", (cid,))

    # --- Faceit ----------------------------------------------------------
    def add_elo(self, elo, niveau, t=None):
        last = self._all("SELECT elo, niveau FROM elo ORDER BY t DESC LIMIT 1")
        if last and last[0]["elo"] == elo and last[0]["niveau"] == niveau:
            return False   # rien de neuf
        self._x("INSERT INTO elo (t, elo, niveau) VALUES (?,?,?)", (t or time.time(), elo, niveau))
        return True

    def elo_series(self, since=None):
        if since:
            return self._all("SELECT t, elo, niveau FROM elo WHERE t >= ? ORDER BY t", (since,))
        return self._all("SELECT t, elo, niveau FROM elo ORDER BY t")

    def elo_before(self, t):
        r = self._all("SELECT elo FROM elo WHERE t < ? ORDER BY t DESC LIMIT 1", (t,))
        return r[0]["elo"] if r else None

    def upsert_match(self, m: dict):
        cols = ["match_id", "debut", "fin", "carte", "score", "victoire", "kills", "morts", "assists", "kd",
                "adr", "hs", "elo_apres", "elo_delta"]
        old = self._all("SELECT * FROM matchs WHERE match_id=?", (m["match_id"],))
        if old:   # ne jamais effacer une info connue avec un vide
            m = {**old[0], **{k: v for k, v in m.items() if v is not None}}
        self._x(f"INSERT OR REPLACE INTO matchs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [m.get(c) for c in cols])

    def matchs(self, limit=50):
        return self._all("SELECT * FROM matchs ORDER BY fin DESC LIMIT ?", (limit,))

    def link_moments_to_match(self, match_id, debut, fin):
        self._x("UPDATE moments SET match_faceit=? WHERE cree BETWEEN ? AND ? AND match_faceit IS NULL",
                (match_id, debut - 60, fin + 60))
