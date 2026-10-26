# -*- coding: utf-8 -*-
"""
HexaClip (Hexa Labs) — clips CS2 automatiques, compils TikTok et suivi du grind Faceit.

Se lance avec Windows, attend dans la barre des tâches, enregistre dès que CS2 est ouvert,
garde tes 3k / 4k / aces et monte une compil verticale d'une minute à la fermeture du jeu.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import os
import sys
import threading
import time
import webbrowser

import paths
from gsi_cs2 import pretty_map

VERSION = "0.1.0"
APP, BRAND = paths.APP, paths.BRAND
IS_WIN = sys.platform == "win32"

C = {
    "bg": "#0B0F14", "panel": "#121820", "raise": "#18202A", "line": "#212A35", "line2": "#2F3A47",
    "fg": "#F3F5F7", "soft": "#C9D1DB", "muted": "#8A94A3",
    "accent": "#2EE6A8", "accent_d": "#1A8C66", "on_accent": "#0B0F14",
    "good": "#2EE6A8", "warn": "#F3AC3C", "bad": "#FF6B6B",
}
FONT = "Segoe UI"
FACEIT_DEV = "https://developers.faceit.com/"


def open_path(p):
    try:
        if IS_WIN:
            os.startfile(p)          # noqa: lecteur vidéo / explorateur par défaut
        else:
            import subprocess
            subprocess.Popen(["xdg-open", p])
    except Exception:
        pass


def show_in_folder(p):
    if IS_WIN and os.path.isfile(p):
        import subprocess
        subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
    else:
        open_path(os.path.dirname(p) if os.path.isfile(p) else p)


def fmt_date(t):
    d = dt.datetime.fromtimestamp(t)
    today = dt.date.today()
    if d.date() == today:
        return d.strftime("aujourd'hui %H:%M")
    if d.date() == today - dt.timedelta(days=1):
        return d.strftime("hier %H:%M")
    return d.strftime("%d/%m %H:%M")


# ----------------------------------------------------------------------------
# Icône de la barre des tâches (zone de notification)
# ----------------------------------------------------------------------------
class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = None
        try:
            import pystray
            from PIL import Image
            img = Image.open(paths.res("assets", "app_64.png"))
            m = pystray.Menu(
                pystray.MenuItem("Ouvrir HexaClip", lambda: app.call(app.show), default=True),
                pystray.MenuItem("Créer la compil maintenant", lambda: app.call(app.compile_now)),
                pystray.MenuItem("Sauvegarder les dernières secondes", lambda: app.engine.manual_clip()),
                pystray.MenuItem("Ouvrir le dossier des vidéos", lambda: open_path(app.engine.cfg.video_dir())),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quitter", lambda: app.call(app.quit)),
            )
            self.icon = pystray.Icon("HexaClip", img, "HexaClip", m)
            self.icon.run_detached()
        except Exception as e:
            app.engine.emit("info", f"Icône de notification indisponible ({e})")

    def notify(self, text, title="HexaClip"):
        if self.icon:
            try:
                self.icon.notify(text, title)
            except Exception:
                pass

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


# ----------------------------------------------------------------------------
# Fenêtre
# ----------------------------------------------------------------------------
class App:
    TABS = ["Accueil", "Clips", "Compils", "Faceit", "Réglages"]

    def __init__(self, engine, hidden=False):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.engine = engine
        self.cfg = engine.cfg
        self.root = tk.Tk()
        self.root.title(f"{APP} — {BRAND}")
        try:
            self.root.iconbitmap(default=paths.res("assets", "hexaclip.ico"))
        except Exception:
            pass
        self.root.geometry("1120x780")
        self.root.minsize(940, 640)
        self.root.configure(bg=C["bg"])
        for opt, val in (("*highlightBackground", C["line"]), ("*highlightColor", C["accent"]),
                         ("*selectBackground", C["accent_d"]), ("*selectForeground", C["fg"]),
                         ("*Entry.relief", "flat"), ("*Spinbox.relief", "flat"),
                         ("*Checkbutton.highlightThickness", 0), ("*Button.highlightThickness", 0),
                         ("*Button.borderWidth", 0)):
            self.root.option_add(opt, val)
        self._style()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.events_log = []
        self.tray = Tray(self)
        self.told_tray = False
        self._build()
        if hidden:
            self.root.withdraw()
        self.root.after(400, self._poll)
        self.root.after(1500, self._first_run)

    # ------------------------------------------------------------- style
    def _style(self):
        st = self.ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], foreground=C["fg"],
                     bordercolor=C["line"], lightcolor=C["panel"], darkcolor=C["panel"], rowheight=30,
                     font=(FONT, 10))
        st.configure("Treeview.Heading", background=C["raise"], foreground=C["muted"], relief="flat",
                     font=(FONT, 9, "bold"))
        st.map("Treeview", background=[("selected", C["accent_d"])], foreground=[("selected", C["fg"])])
        st.map("Treeview.Heading", background=[("active", C["line2"])])
        st.configure("TCombobox", fieldbackground=C["raise"], background=C["raise"], foreground=C["fg"],
                     arrowcolor=C["accent"], bordercolor=C["line"], lightcolor=C["raise"], darkcolor=C["raise"],
                     padding=4)
        st.map("TCombobox", fieldbackground=[("readonly", C["raise"])], foreground=[("readonly", C["fg"])])
        self.root.option_add("*TCombobox*Listbox.background", C["raise"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["fg"])
        st.configure("Vertical.TScrollbar", background=C["raise"], troughcolor=C["panel"], bordercolor=C["panel"],
                     arrowcolor=C["muted"])

    def label(self, parent, text="", size=10, color="fg", bold=False, bg="bg", **kw):
        return self.tk.Label(parent, text=text, bg=C[bg], fg=C[color],
                             font=(FONT, size, "bold" if bold else "normal"), **kw)

    def button(self, parent, text, cmd, primary=False, bg="bg", **kw):
        b = self.tk.Button(parent, text=text, command=cmd, cursor="hand2",
                           bg=C["accent"] if primary else C["raise"],
                           fg=C["on_accent"] if primary else C["fg"],
                           activebackground=C["accent_d"] if primary else C["line2"],
                           activeforeground=C["fg"], font=(FONT, 10, "bold" if primary else "normal"),
                           padx=16, pady=8, relief="flat", **kw)
        return b

    def card(self, parent, **kw):
        return self.tk.Frame(parent, bg=C["panel"], highlightbackground=C["line"], highlightthickness=1, **kw)

    # ------------------------------------------------------------- structure
    def _build(self):
        tk = self.tk
        top = tk.Frame(self.root, bg=C["bg"])
        top.pack(fill="x", padx=24, pady=(18, 8))
        try:
            self._logo = tk.PhotoImage(file=paths.res("assets", "symbole_36.png"))
            tk.Label(top, image=self._logo, bg=C["bg"]).pack(side="left")
        except Exception:
            pass
        name = tk.Frame(top, bg=C["bg"])
        name.pack(side="left", padx=(10, 0))
        tk.Label(name, text="Hexa", bg=C["bg"], fg=C["fg"], font=(FONT, 18, "bold"), padx=0, bd=0).pack(side="left")
        tk.Label(name, text="Clip", bg=C["bg"], fg=C["accent"], font=(FONT, 18, "bold"), padx=0, bd=0).pack(
            side="left")
        self.label(top, f"  v{VERSION} · {BRAND}", 9, "muted").pack(side="left", pady=(6, 0))
        self.pill = self.label(top, "", 10, "muted")
        self.pill.pack(side="right")

        bar = tk.Frame(self.root, bg=C["bg"])
        bar.pack(fill="x", padx=24)
        self.tab_btns = {}
        for t in self.TABS:
            b = tk.Label(bar, text=t, bg=C["bg"], fg=C["muted"], font=(FONT, 11, "bold"), padx=14, pady=8,
                         cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, t=t: self.show_tab(t))
            self.tab_btns[t] = b
        tk.Frame(self.root, bg=C["line"], height=1).pack(fill="x", padx=24)
        self.body = tk.Frame(self.root, bg=C["bg"])
        self.body.pack(fill="both", expand=True, padx=24, pady=16)
        self.pages = {
            "Accueil": self._page_home(), "Clips": self._page_clips(), "Compils": self._page_compils(),
            "Faceit": self._page_faceit(), "Réglages": self._page_settings(),
        }
        self.current = None
        self.show_tab("Accueil")

    def show_tab(self, name):
        for t, b in self.tab_btns.items():
            b.configure(fg=C["accent"] if t == name else C["muted"])
        if self.current:
            self.pages[self.current].pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        self.current = name
        {"Clips": self.refresh_clips, "Compils": self.refresh_compils, "Faceit": self.refresh_faceit_tab}.get(
            name, lambda: None)()

    # ------------------------------------------------------------- Accueil
    def _page_home(self):
        tk = self.tk
        f = tk.Frame(self.body, bg=C["bg"])
        self.first_banner = tk.Frame(f, bg=C["bg"])
        self.first_banner.pack(fill="x")
        grid = tk.Frame(f, bg=C["bg"])
        grid.pack(fill="x")
        self.stat = {}
        for i, (key, title) in enumerate([("cs2", "CS2"), ("rec", "Enregistrement"), ("son", "Son du PC"),
                                          ("gsi", "Liaison CS2"), ("faceit", "Faceit")]):
            c = self.card(grid)
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            grid.columnconfigure(i, weight=1, uniform="s")
            self.label(c, title.upper(), 8, "muted", True, bg="panel").pack(anchor="w", padx=14, pady=(12, 2))
            v = self.label(c, "…", 13, "fg", True, bg="panel")
            v.pack(anchor="w", padx=14)
            d = self.label(c, "", 9, "muted", bg="panel", wraplength=180, justify="left")
            d.pack(anchor="w", padx=14, pady=(2, 12))
            self.stat[key] = (v, d)

        act = self.card(f)
        act.pack(fill="x", pady=16)
        left = tk.Frame(act, bg=C["panel"])
        left.pack(side="left", padx=18, pady=16)
        self.session_lbl = self.label(left, "Pas de session en cours", 15, "fg", True, bg="panel")
        self.session_lbl.pack(anchor="w")
        self.session_sub = self.label(left, "Lance CS2 : HexaClip enregistre tout seul et garde tes 3k, 4k et aces.",
                                      10, "muted", bg="panel")
        self.session_sub.pack(anchor="w", pady=(4, 0))
        right = tk.Frame(act, bg=C["panel"])
        right.pack(side="right", padx=18)
        self.button(right, "Créer la compil maintenant", self.compile_now, primary=True).pack(side="right")
        self.btn_manual = self.button(right, f"Sauver les dernières secondes ({self.cfg['touche_manuelle']})",
                                      self.engine.manual_clip)
        self.btn_manual.pack(side="right", padx=10)

        self.busy_lbl = self.label(f, "", 10, "accent")
        self.busy_lbl.pack(anchor="w")
        self.label(f, "DERNIERS ÉVÉNEMENTS", 8, "muted", True).pack(anchor="w", pady=(12, 6))
        self.events_box = tk.Frame(f, bg=C["bg"])
        self.events_box.pack(fill="both", expand=True)
        return f

    def _set_stat(self, key, value, detail="", color="fg"):
        v, d = self.stat[key]
        v.configure(text=value, fg=C[color])
        d.configure(text=detail)

    def _refresh_home(self, s):
        self._set_stat("cs2", "Lancé" if s["cs2"] else "Pas lancé",
                       (f"{pretty_map(s['carte'])} · round {s['round']}"
                        if s["cs2"] and s["carte"] else "En attente du jeu"), "good" if s["cs2"] else "muted")
        m = s["method"]
        if s["rec"]:
            hw = {"h264_nvenc": "NVIDIA", "h264_amf": "AMD", "h264_qsv": "Intel", "libx264": "processeur"}
            self._set_stat("rec", "En cours", f"Encodage {hw.get(m['encodeur'], m['encodeur'])} · {m['fps']} i/s",
                           "good")
        elif not s["ffmpeg"]:
            self._set_stat("rec", "Préparation", "Moteur vidéo en cours d'installation", "warn")
        elif m:
            self._set_stat("rec", "Prêt", "Démarre avec CS2" if self.cfg["capture_auto"] else "Capture auto coupée",
                           "fg")
        else:
            self._set_stat("rec", "Indisponible", s["method_error"] or "Test de la capture…", "bad"
                           if s["method_error"] else "warn")
        if s["rec"]:
            self._set_stat("son", "Capté" if s["son"] else "Muet",
                           "Ce que tu entends dans le casque" if s["son"] else "Vidéo sans son (voir journal)",
                           "good" if s["son"] else "warn")
        else:
            self._set_stat("son", "Activé" if self.cfg["son"] else "Coupé", "Sortie son par défaut de Windows",
                           "fg" if self.cfg["son"] else "muted")
        if s["gsi"]:
            self._set_stat("gsi", "Connectée", "CS2 envoie la partie en direct", "good")
        elif s["cfg_cs2"]:
            self._set_stat("gsi", "Installée", "Active au prochain lancement de CS2", "fg")
        else:
            self._set_stat("gsi", "À installer", "Réglages > Liaison CS2", "warn")
        fp = s["faceit"]
        if fp.get("elo"):
            self._set_stat("faceit", f"{fp['elo']} Elo", f"Niveau {fp['niveau']} · {fp['pseudo']}", "accent")
        elif s["faceit_error"]:
            self._set_stat("faceit", "Erreur", s["faceit_error"], "bad")
        else:
            self._set_stat("faceit", "À configurer", "Pseudo + clé API dans Réglages", "muted")
        if s["cs2"]:
            n = s["session_moments"]
            self.session_lbl.configure(text=f"Session en cours · {n} action{'s' if n > 1 else ''} gardée"
                                            f"{'s' if n > 1 else ''}")
            self.session_sub.configure(text="La compil se monte toute seule à la fermeture de CS2."
                                       if self.cfg["compil_auto"] else "Compil auto coupée dans les réglages.")
        else:
            self.session_lbl.configure(text="Pas de session en cours")
            self.session_sub.configure(text="Lance CS2 : HexaClip enregistre tout seul et garde tes 3k, 4k et aces.")
        self.busy_lbl.configure(text=s["busy"])
        rec = "● REC" if s["rec"] else ("CS2 lancé" if s["cs2"] else "En veille")
        self.pill.configure(text=rec, fg=C["bad"] if s["rec"] else C["muted"])

    def _add_event(self, kind, text):
        self.events_log.insert(0, (time.time(), kind, text))
        self.events_log = self.events_log[:9]
        for w in self.events_box.winfo_children():
            w.destroy()
        colors = {"clip": "accent", "compil": "accent", "erreur": "bad", "faceit": "soft"}
        for t, k, tx in self.events_log:
            row = self.tk.Frame(self.events_box, bg=C["bg"])
            row.pack(fill="x", pady=2)
            self.label(row, dt.datetime.fromtimestamp(t).strftime("%H:%M"), 9, "muted").pack(side="left")
            self.label(row, tx, 10, colors.get(k, "fg"), wraplength=900, justify="left").pack(side="left", padx=10)

    # ------------------------------------------------------------- Clips
    def _tree(self, parent, cols, heights=14):
        ttk, tk = self.ttk, self.tk
        box = tk.Frame(parent, bg=C["bg"])
        tv = ttk.Treeview(box, columns=[c[0] for c in cols], show="headings", height=heights, selectmode="extended")
        for key, title, w, anchor in cols:
            tv.heading(key, text=title)
            tv.column(key, width=w, anchor=anchor, stretch=key == cols[0][0])
        sb = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return box, tv

    def _page_clips(self):
        tk = self.tk
        f = tk.Frame(self.body, bg=C["bg"])
        head = tk.Frame(f, bg=C["bg"])
        head.pack(fill="x", pady=(0, 10))
        self.label(head, "Tes actions gardées", 14, "fg", True).pack(side="left")
        self.clips_count = self.label(head, "", 10, "muted")
        self.clips_count.pack(side="left", padx=12)
        cols = [("quand", "Quand", 150, "w"), ("type", "Action", 80, "center"), ("carte", "Map", 110, "w"),
                ("round", "Round", 60, "center"), ("hs", "HS", 50, "center"), ("score", "Note", 60, "center"),
                ("fav", "★", 40, "center"), ("etat", "Compil", 90, "center")]
        box, self.clips_tv = self._tree(f, cols)
        box.pack(fill="both", expand=True)
        self.clips_tv.bind("<Double-1>", lambda e: self._clip_action("lire"))
        btns = tk.Frame(f, bg=C["bg"])
        btns.pack(fill="x", pady=(12, 0))
        for text, act in [("Lire", "lire"), ("Dans le dossier", "dossier"), ("★ Favori", "fav"),
                          ("Garder / écarter des compils", "exclu"), ("Supprimer", "suppr")]:
            self.button(btns, text, lambda a=act: self._clip_action(a)).pack(side="left", padx=(0, 8))
        self.button(btns, "Compil avec la sélection", lambda: self._clip_action("compil"), primary=True).pack(
            side="right")
        self.label(f, "Les favoris (★) passent en priorité dans les compils et ne sont jamais supprimés par le ménage "
                      "automatique.", 9, "muted").pack(anchor="w", pady=(10, 0))
        return f

    def refresh_clips(self):
        tv = self.clips_tv
        tv.delete(*tv.get_children())
        self._clips = {m["id"]: m for m in self.engine.db.moments(limit=1000)}
        from moments import LABELS
        for m in self._clips.values():
            exists = m.get("fichier") and os.path.isfile(m["fichier"])
            etat = "écarté" if m.get("exclu") else ("" if exists else "fichier absent")
            tv.insert("", "end", iid=str(m["id"]), values=(
                fmt_date(m["cree"]), LABELS.get(m["type"], m["type"]), m.get("carte") or "—",
                m.get("round") or "—", m.get("hs") if m.get("kills") else "—", f"{m.get('score', 0):.0f}",
                "★" if m.get("favori") else "", etat))
        self.clips_count.configure(text=f"{len(self._clips)} clips")

    def _selected_clips(self):
        return [self._clips[int(i)] for i in self.clips_tv.selection() if int(i) in self._clips]

    def _clip_action(self, act):
        from tkinter import messagebox
        sel = self._selected_clips()
        if not sel:
            return
        if act == "lire":
            open_path(sel[0]["fichier"])
        elif act == "dossier":
            show_in_folder(sel[0]["fichier"])
        elif act == "fav":
            for m in sel:
                self.engine.db.set_moment(m["id"], favori=0 if m.get("favori") else 1)
        elif act == "exclu":
            for m in sel:
                self.engine.db.set_moment(m["id"], exclu=0 if m.get("exclu") else 1)
        elif act == "suppr":
            if messagebox.askyesno(APP, f"Supprimer {len(sel)} clip(s) du disque ?"):
                for m in sel:
                    self.engine.delete_moment(m["id"])
        elif act == "compil":
            ids = [m["id"] for m in sel]
            threading.Thread(target=self.engine.compile, kwargs={"ids": ids}, daemon=True).start()
            self.show_tab("Accueil")
            return
        self.refresh_clips()

    # ------------------------------------------------------------- Compils
    def _page_compils(self):
        tk = self.tk
        f = tk.Frame(self.body, bg=C["bg"])
        head = tk.Frame(f, bg=C["bg"])
        head.pack(fill="x", pady=(0, 10))
        self.label(head, "Tes compils TikTok", 14, "fg", True).pack(side="left")
        self.button(head, "Compil de la semaine", lambda: self._compile_since(7), primary=True).pack(side="right")
        self.button(head, "Compil des dernières 24 h", lambda: self._compile_since(1)).pack(side="right", padx=8)
        cols = [("quand", "Créée", 170, "w"), ("fichier", "Fichier", 360, "w"), ("duree", "Durée", 80, "center"),
                ("n", "Actions", 80, "center")]
        box, self.comp_tv = self._tree(f, cols)
        box.pack(fill="both", expand=True)
        self.comp_tv.bind("<Double-1>", lambda e: self._comp_action("lire"))
        btns = tk.Frame(f, bg=C["bg"])
        btns.pack(fill="x", pady=(12, 0))
        for text, act in [("Lire", "lire"), ("Dans le dossier", "dossier"), ("Supprimer", "suppr")]:
            self.button(btns, text, lambda a=act: self._comp_action(a)).pack(side="left", padx=(0, 8))
        self.label(f, "Format TikTok : vertical 1080×1920, 60 i/s. Importe le fichier dans TikTok depuis ton "
                      "téléphone (ou TikTok Studio sur PC) et ajoute un son tendance dans l'app si tu veux.",
                   9, "muted", wraplength=900, justify="left").pack(anchor="w", pady=(10, 0))
        return f

    def refresh_compils(self):
        tv = self.comp_tv
        tv.delete(*tv.get_children())
        self._comps = {c["id"]: c for c in self.engine.db.compils()}
        import json
        for c in self._comps.values():
            tv.insert("", "end", iid=str(c["id"]), values=(
                fmt_date(c["cree"]), os.path.basename(c["fichier"] or ""), f"{c['duree'] or 0:.0f} s",
                len(json.loads(c["moments"] or "[]"))))

    def _comp_action(self, act):
        from tkinter import messagebox
        sel = [self._comps[int(i)] for i in self.comp_tv.selection() if int(i) in self._comps]
        if not sel:
            return
        if act == "lire":
            open_path(sel[0]["fichier"])
        elif act == "dossier":
            show_in_folder(sel[0]["fichier"])
        elif act == "suppr" and messagebox.askyesno(APP, f"Supprimer {len(sel)} compil(s) du disque ?"):
            for c in sel:
                try:
                    os.remove(c["fichier"])
                except OSError:
                    pass
                self.engine.db.delete_compil(c["id"])
        self.refresh_compils()

    def _compile_since(self, days):
        since = time.time() - days * 86400
        threading.Thread(target=self.engine.compile, kwargs={"since": since}, daemon=True).start()
        self.show_tab("Accueil")

    def compile_now(self):
        e = self.engine
        if e.cs2_running and e.session_id:
            kw = {"session": e.session_id, "since": e.session_start}
        else:
            kw = {"since": time.time() - 86400}
        threading.Thread(target=e.compile, kwargs=kw, daemon=True).start()

    # ------------------------------------------------------------- Faceit
    def _page_faceit(self):
        tk = self.tk
        f = tk.Frame(self.body, bg=C["bg"])
        top = tk.Frame(f, bg=C["bg"])
        top.pack(fill="x")
        c = self.card(top)
        c.pack(side="left", fill="y")
        self.fc_elo = self.label(c, "—", 30, "accent", True, bg="panel")
        self.fc_elo.pack(anchor="w", padx=18, pady=(14, 0))
        self.fc_lvl = self.label(c, "", 11, "fg", bg="panel")
        self.fc_lvl.pack(anchor="w", padx=18)
        self.fc_delta = self.label(c, "", 10, "muted", bg="panel")
        self.fc_delta.pack(anchor="w", padx=18, pady=(4, 14))
        g = self.card(top)
        g.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.fc_canvas = tk.Canvas(g, bg=C["panel"], height=150, highlightthickness=0)
        self.fc_canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.fc_canvas.bind("<Configure>", lambda e: self._draw_elo())
        bar = tk.Frame(f, bg=C["bg"])
        bar.pack(fill="x", pady=(14, 8))
        self.label(bar, "Derniers matchs", 13, "fg", True).pack(side="left")
        self.fc_err = self.label(bar, "", 10, "bad")
        self.fc_err.pack(side="left", padx=12)
        self.button(bar, "Actualiser", self._faceit_refresh).pack(side="right")
        cols = [("quand", "Fini", 140, "w"), ("carte", "Map", 110, "w"), ("score", "Score", 80, "center"),
                ("res", "Résultat", 80, "center"), ("kd", "K / D", 90, "center"), ("adr", "ADR", 70, "center"),
                ("hs", "HS %", 70, "center"), ("elo", "Elo", 80, "center")]
        box, self.fc_tv = self._tree(f, cols, heights=10)
        box.pack(fill="both", expand=True)
        self.fc_tv.tag_configure("win", foreground=C["good"])
        self.fc_tv.tag_configure("loss", foreground=C["bad"])
        return f

    def _faceit_refresh(self):
        if not self.engine.faceit.configured():
            self.show_tab("Réglages")
            return
        threading.Thread(target=lambda: (self.engine.refresh_faceit(), self.call(self.refresh_faceit_tab)),
                         daemon=True).start()

    def refresh_faceit_tab(self):
        e = self.engine
        p = e.faceit.profile
        series = e.db.elo_series()
        if p.get("elo") or series:
            elo = p.get("elo") or series[-1]["elo"]
            lvl = p.get("niveau") or series[-1]["niveau"]
            self.fc_elo.configure(text=f"{elo} Elo")
            self.fc_lvl.configure(text=f"Niveau {lvl}" + (f" · {p['pseudo']}" if p.get("pseudo") else ""))
            day = e.faceit.session_summary(time.time() - 86400)
            week = e.faceit.session_summary(time.time() - 7 * 86400)

            def sgn(v):
                return "—" if v is None else (f"+{v}" if v > 0 else str(v))
            self.fc_delta.configure(text=f"24 h : {sgn(day['delta'])} · 7 jours : {sgn(week['delta'])}")
        elif not e.faceit.configured():
            self.fc_elo.configure(text="Faceit")
            self.fc_lvl.configure(text="Ajoute ton pseudo et ta clé API dans Réglages.")
        self.fc_err.configure(text=e.faceit.last_error)
        tv = self.fc_tv
        tv.delete(*tv.get_children())
        for m in e.db.matchs(50):
            kd = f"{m['kills']}-{m['morts']}" if m.get("kills") is not None else "—"
            d = m.get("elo_delta")
            tv.insert("", "end", values=(
                fmt_date(m["fin"]) if m.get("fin") else "—", m.get("carte") or "—", m.get("score") or "—",
                {1: "Victoire", 0: "Défaite"}.get(m.get("victoire"), "—"), kd,
                f"{m['adr']:.0f}" if m.get("adr") else "—", m.get("hs") if m.get("hs") is not None else "—",
                (f"+{d}" if d and d > 0 else str(d)) if d is not None else "—"),
                tags=("win",) if m.get("victoire") == 1 else ("loss",) if m.get("victoire") == 0 else ())
        self._draw_elo()

    def _draw_elo(self):
        cv = self.fc_canvas
        cv.delete("all")
        pts = self.engine.db.elo_series(time.time() - 30 * 86400)
        w, h = max(cv.winfo_width(), 200), max(cv.winfo_height(), 100)
        if len(pts) < 2:
            cv.create_text(w / 2, h / 2, text="La courbe d'Elo se remplit au fil de tes matchs (30 derniers jours).",
                           fill=C["muted"], font=(FONT, 10))
            return
        t0, t1 = pts[0]["t"], pts[-1]["t"]
        lo, hi = min(p["elo"] for p in pts), max(p["elo"] for p in pts)
        if hi - lo < 50:
            lo, hi = lo - 25, hi + 25
        pad = 24

        def xy(p):
            x = pad + (p["t"] - t0) / max(1, t1 - t0) * (w - 2 * pad)
            y = h - pad - (p["elo"] - lo) / (hi - lo) * (h - 2 * pad)
            return x, y
        coords = [c for p in pts for c in xy(p)]
        for v in (lo, hi):
            y = h - pad - (v - lo) / (hi - lo) * (h - 2 * pad)
            cv.create_line(pad, y, w - pad, y, fill=C["line"])
            cv.create_text(pad + 2, y - 8, text=str(int(v)), fill=C["muted"], font=(FONT, 8), anchor="w")
        cv.create_line(*coords, fill=C["accent"], width=2)
        x, y = xy(pts[-1])
        cv.create_oval(x - 4, y - 4, x + 4, y + 4, fill=C["accent"], outline="")

    # ------------------------------------------------------------- Réglages
    def _page_settings(self):
        tk, ttk = self.tk, self.ttk
        outer = tk.Frame(self.body, bg=C["bg"])
        cv = tk.Canvas(outer, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        f = tk.Frame(cv, bg=C["bg"])
        f.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=f, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        cv.bind_all("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120), "units")
                    if self.current == "Réglages" else None)
        self.vars = {}

        def section(title, note=""):
            self.label(f, title.upper(), 9, "accent", True).pack(anchor="w", pady=(18, 2))
            if note:
                self.label(f, note, 9, "muted", wraplength=820, justify="left").pack(anchor="w", pady=(0, 6))

        def row(label, widget_fn, hint=""):
            r = tk.Frame(f, bg=C["bg"])
            r.pack(fill="x", pady=4)
            self.label(r, label, 10, "soft", width=30, anchor="w").pack(side="left")
            widget_fn(r).pack(side="left")
            if hint:
                self.label(r, hint, 9, "muted").pack(side="left", padx=10)

        def entry(key, width=30, show=None):
            v = tk.StringVar(value=str(self.cfg[key]))
            self.vars[key] = (v, type(self.cfg[key]))
            return lambda p: tk.Entry(p, textvariable=v, width=width, bg=C["raise"], fg=C["fg"],
                                      insertbackground=C["fg"], font=(FONT, 10), show=show or "")

        def check(key, text=""):
            v = tk.BooleanVar(value=bool(self.cfg[key]))
            self.vars[key] = (v, bool)
            return lambda p: tk.Checkbutton(p, text=text, variable=v, bg=C["bg"], fg=C["fg"],
                                            selectcolor=C["raise"], activebackground=C["bg"],
                                            activeforeground=C["fg"], font=(FONT, 10))

        def combo(key, values, width=18, labels=None):
            labels = labels or values
            v = tk.StringVar(value=labels[values.index(self.cfg[key])] if self.cfg[key] in values else labels[0])
            self.vars[key] = (v, ("map", values, labels))
            return lambda p: ttk.Combobox(p, textvariable=v, values=labels, width=width, state="readonly")

        def folder(key, title, file=False):
            v = tk.StringVar(value=self.cfg[key])
            self.vars[key] = (v, str)

            def pick():
                from tkinter import filedialog
                p = (filedialog.askopenfilename(title=title, filetypes=[("Image PNG", "*.png")]) if file
                     else filedialog.askdirectory(title=title))
                if p:
                    v.set(p)

            def make(p):
                fr = tk.Frame(p, bg=C["bg"])
                tk.Entry(fr, textvariable=v, width=48, bg=C["raise"], fg=C["fg"], insertbackground=C["fg"],
                         font=(FONT, 10)).pack(side="left")
                self.button(fr, "Choisir…", pick).pack(side="left", padx=6)
                return fr
            return make

        section("Faceit", "Crée une clé API gratuite sur developers.faceit.com (App Studio > ton app > API Keys, "
                          "type « Server side »), puis colle-la ici avec ton pseudo Faceit exact.")
        row("Pseudo Faceit", entry("faceit_pseudo", 26))
        row("Clé API (Server side)", entry("faceit_cle_api", 44, show="•"))
        r = tk.Frame(f, bg=C["bg"])
        r.pack(fill="x", pady=4)
        self.button(r, "Ouvrir developers.faceit.com", lambda: webbrowser.open(FACEIT_DEV)).pack(side="left")
        self.button(r, "Tester", self._test_faceit).pack(side="left", padx=8)
        self.faceit_test = self.label(r, "", 10, "muted")
        self.faceit_test.pack(side="left", padx=8)

        section("Capture", "Encodage par la carte graphique : quasiment aucun impact sur les FPS. Seules les "
                           "dernières minutes sont gardées sur le disque tant qu'aucune action n'est faite.")
        row("Enregistrer dès que CS2 se lance", check("capture_auto"))
        row("Images par seconde", combo("fps", [60, 30], 8, ["60", "30"]))
        row("Qualité", combo("qualite", [18, 21, 24, 27], 18, ["Très haute", "Haute", "Moyenne", "Légère"]))
        row("Encodeur", combo("encodeur", ["auto", "h264_nvenc", "h264_amf", "h264_qsv", "libx264"], 18,
                              ["Automatique", "NVIDIA (NVENC)", "AMD (AMF)", "Intel (Quick Sync)", "Processeur"]))
        row("Écran capturé", combo("ecran", [0, 1, 2], 18, ["Écran principal", "Écran 2", "Écran 3"]))
        row("Capter le son du PC", check("son"))
        row("Décalage du son (ms)", entry("decalage_son_ms", 8), "+ si le son arrive trop tôt, − s'il arrive trop tard")
        row("Durée gardée en arrière", combo("tampon_s", [120, 180, 300], 18, ["2 min", "3 min", "5 min"]))
        row("Dossier des vidéos", folder("dossier_videos", "Dossier des clips et compils"))
        self.label(f, "Vide = Vidéos\\HexaClip. Prévois quelques Go : les clips bruts sont en haute qualité.", 9,
                   "muted").pack(anchor="w")
        r = tk.Frame(f, bg=C["bg"])
        r.pack(fill="x", pady=4)
        self.button(r, "Retester la capture", lambda: threading.Thread(target=self.engine.detect_capture,
                                                                       daemon=True).start()).pack(side="left")
        self.button(r, "Réinstaller la liaison CS2", self._install_gsi).pack(side="left", padx=8)

        section("Actions gardées")
        row("Garder à partir de", combo("kills_min", [2, 3, 4, 5], 18, ["2 kills", "3 kills (3k)", "4 kills (4k)",
                                                                       "Ace seulement"]))
        row("Secondes avant le 1er kill", entry("avant_s", 8))
        row("Secondes après le dernier kill", entry("apres_s", 8))
        row("Touche « sauver maintenant »", combo("touche_manuelle", ["F9", "F8", "F10", "F11", "F7", "F6", ""], 18,
                                                  ["F9", "F8", "F10", "F11", "F7", "F6", "Aucune"]),
            "prise en compte au prochain lancement")
        row("Durée sauvée par la touche", combo("duree_manuelle_s", [10, 15, 20, 30], 18,
                                                ["10 s", "15 s", "20 s", "30 s"]))

        section("Compils TikTok")
        row("Monter la compil à la fermeture de CS2", check("compil_auto"))
        row("Durée", combo("compil_duree_s", [30, 45, 60, 90], 18, ["30 s", "45 s", "1 min", "1 min 30"]))
        row("Ordre", combo("compil_ordre", ["crescendo", "meilleure_d_abord", "chrono"], 18,
                           ["Meilleure à la fin", "Meilleure d'abord", "Chronologique"]))
        row("Cadrage", combo("compil_cadrage", ["flou", "zoom"], 18, ["Jeu entier + fond flou",
                                                                     "Zoom sur le centre"]))
        row("Compteur de kills à l'écran", check("compil_badges"))
        row("Texte en haut (facultatif)", entry("compil_texte", 30), "ex. le nom de ta chaîne")
        row("Logo perso (PNG transparent)", folder("compil_logo", "Logo PNG", file=True))
        self.label(f, "Vide = logo HexaClip.", 9, "muted").pack(anchor="w")
        row("Écran de fin avec l'Elo Faceit", check("compil_fin_faceit"))
        row("Ajouter une musique", check("musique_on"), "fichiers libres de droits uniquement")
        row("Dossier des musiques", folder("musique_dossier", "Dossier de musiques"))
        row("Volume de la musique", combo("musique_volume", [0.15, 0.25, 0.4, 0.6], 18,
                                          ["Discret", "Moyen", "Fort", "Très fort"]))

        section("Windows")
        row("Lancer HexaClip avec Windows", check("demarrage_windows"), "démarre discrètement dans la barre des tâches")
        row("Garder les clips (jours)", combo("garder_clips_jours", [7, 14, 30, 90, 0], 18,
                                              ["7 jours", "14 jours", "30 jours", "90 jours", "Toujours"]),
            "les favoris sont toujours gardés")
        r = tk.Frame(f, bg=C["bg"])
        r.pack(fill="x", pady=(22, 30))
        self.button(r, "Enregistrer les réglages", self._save_settings, primary=True).pack(side="left")
        self.button(r, "Ouvrir le journal", lambda: open_path(paths.data("journal.txt"))).pack(side="left", padx=8)
        self.button(r, "Dossier des vidéos", lambda: open_path(self.cfg.video_dir())).pack(side="left")
        self.settings_msg = self.label(r, "", 10, "accent")
        self.settings_msg.pack(side="left", padx=12)
        return outer

    def _read_settings(self):
        out = {}
        for key, (var, kind) in self.vars.items():
            raw = var.get()
            if isinstance(kind, tuple) and kind[0] == "map":
                _, values, labels = kind
                out[key] = values[labels.index(raw)] if raw in labels else values[0]
            elif kind is bool:
                out[key] = bool(raw)
            elif kind is int:
                try:
                    out[key] = int(float(str(raw).replace(",", ".")))
                except ValueError:
                    out[key] = self.cfg[key]
            elif kind is float:
                try:
                    out[key] = float(str(raw).replace(",", "."))
                except ValueError:
                    out[key] = self.cfg[key]
            else:
                out[key] = str(raw).strip()
        return out

    def _save_settings(self):
        from engine import set_autostart
        new = self._read_settings()
        capture_keys = ("fps", "qualite", "encodeur", "ecran", "son", "decalage_son_ms", "tampon_s", "dossier_videos")
        recapture = any(new.get(k) != self.cfg[k] for k in capture_keys)
        faceit_changed = any(new.get(k) != self.cfg[k] for k in ("faceit_pseudo", "faceit_cle_api"))
        autostart_changed = new.get("demarrage_windows") != self.cfg["demarrage_windows"]
        for k, v in new.items():
            self.cfg[k] = v
        self.cfg.save()
        msg = "Réglages enregistrés."
        if autostart_changed:
            set_autostart(self.cfg["demarrage_windows"])
        if faceit_changed:
            self.engine.reload_faceit()
            if self.engine.faceit.configured():
                threading.Thread(target=self.engine.refresh_faceit, daemon=True).start()
        if recapture:
            if self.engine.recorder.running:
                msg += " La capture reprendra avec les nouveaux réglages au prochain lancement de CS2."
            threading.Thread(target=self.engine.detect_capture, daemon=True).start()
        self.btn_manual.configure(text=f"Sauver les dernières secondes ({self.cfg['touche_manuelle'] or '—'})")
        self.settings_msg.configure(text=msg)

    def _test_faceit(self):
        new = self._read_settings()
        from faceit import Faceit, FaceitError
        f = Faceit(new["faceit_cle_api"], new["faceit_pseudo"], self.engine.db)

        def run():
            try:
                p = f.player()
                txt, col = f"OK : {p['pseudo']}, {p['elo']} Elo, niveau {p['niveau']}", "good"
            except FaceitError as e:
                txt, col = str(e), "bad"
            except Exception as e:
                txt, col = f"Erreur : {e}", "bad"
            self.call(lambda: self.faceit_test.configure(text=txt, fg=C[col]))
        self.faceit_test.configure(text="Test…", fg=C["muted"])
        threading.Thread(target=run, daemon=True).start()

    def _install_gsi(self):
        from tkinter import filedialog, messagebox
        from gsi_cs2 import install_cfg
        ok, msg = install_cfg()
        if not ok:
            d = filedialog.askdirectory(title="Dossier ...\\Counter-Strike Global Offensive\\game\\csgo\\cfg")
            if d:
                ok, msg = install_cfg(d)
        messagebox.showinfo(APP, msg)

    # ------------------------------------------------------------- premier lancement
    def _first_run(self):
        if self.cfg["premier_lancement_fait"]:
            return
        tk = self.tk
        b = self.card(self.first_banner)
        b.pack(fill="x", pady=(0, 16))
        self.label(b, "Bienvenue dans HexaClip", 13, "fg", True, bg="panel").pack(anchor="w", padx=18,
                                                                                    pady=(14, 2))
        self.label(b, "Le plus simple : le laisser démarrer avec Windows. Il attend discrètement dans la barre des "
                      "tâches et enregistre tout seul quand tu lances CS2.", 10, "soft", bg="panel",
                   wraplength=900, justify="left").pack(anchor="w", padx=18)
        r = tk.Frame(b, bg=C["panel"])
        r.pack(anchor="w", padx=18, pady=12)

        def answer(on):
            from engine import set_autostart
            self.cfg["demarrage_windows"] = on
            self.cfg["premier_lancement_fait"] = True
            self.cfg.save()
            set_autostart(on)
            v = self.vars.get("demarrage_windows")
            if v:
                v[0].set(on)
            b.destroy()
            if not self.engine.faceit.configured():
                self._add_event("info", "Pour suivre ton Elo : ajoute ton pseudo et ta clé API Faceit dans Réglages.")
        self.button(r, "Lancer avec Windows", lambda: answer(True), primary=True, bg="panel").pack(side="left")
        self.button(r, "Non merci", lambda: answer(False), bg="panel").pack(side="left", padx=8)

    # ------------------------------------------------------------- boucle
    def call(self, fn):
        """Exécute fn dans le fil de l'interface (depuis un autre fil)."""
        self.root.after(0, fn)

    def _poll(self):
        try:
            while True:
                kind, text = self.engine.events.get_nowait()
                self._add_event(kind, text)
                if kind in ("clip", "compil", "erreur"):
                    self.tray.notify(text)
                if kind == "clip" and self.current == "Clips":
                    self.refresh_clips()
                if kind == "compil" and self.current == "Compils":
                    self.refresh_compils()
                if kind == "faceit" and self.current == "Faceit":
                    self.refresh_faceit_tab()
        except Exception:
            pass
        try:
            self._refresh_home(self.engine.status())
        except Exception:
            pass
        self.root.after(700, self._poll)

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        if not self.tray.icon:
            self.quit()
            return
        self.root.withdraw()
        if not self.told_tray:
            self.told_tray = True
            self.tray.notify("HexaClip continue dans la barre des tâches. Clic droit sur l'icône pour quitter.")

    def quit(self):
        self.tray.stop()
        self.engine.shutdown()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ----------------------------------------------------------------------------
# Lancement
# ----------------------------------------------------------------------------
def _single_instance():
    if not IS_WIN:
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        h = k32.CreateMutexW(None, False, "Local\\HexaLabs.HexaClip")
        globals()["_MUTEX"] = h
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def main():
    global FONT
    ap = argparse.ArgumentParser(prog="HexaClip")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", help="fichier de sortie pour --selftest (l'exe n'a pas de console)")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--discret", action="store_true", help="démarrer caché dans la barre des tâches")
    args, _ = ap.parse_known_args()
    if args.selftest or args.version:
        import io
        import contextlib
        buf = io.StringIO()
        ok = True
        with contextlib.redirect_stdout(buf):
            print(f"{APP} {VERSION} ({BRAND}) · données : {paths.DATA_DIR}")
            if args.selftest:
                import importlib
                for mod in ("psutil", "pystray", "PIL", "config", "db", "engine", "faceit", "ffmpeg_tools",
                            "gsi_cs2", "moments", "montage", "recorder", "selftest") + \
                        (("pyaudiowpatch",) if IS_WIN else ()):
                    try:
                        m = importlib.import_module(mod)
                        print(f"module {mod} : OK {getattr(m, '__version__', '')}".rstrip())
                    except Exception as e:
                        print(f"module {mod} : ERREUR {e}")
                        ok = False
                for f in ("assets/hexaclip.ico", "assets/app_64.png", "assets/symbole_36.png",
                          "assets/filigrane.png", "assets/SpaceGrotesk-Bold.ttf"):
                    exists = os.path.exists(paths.res(*f.split("/")))
                    print(f"ressource {f} : {'OK' if exists else 'MANQUANTE'}")
                    ok = ok and exists
                import selftest
                ok = selftest.run() and ok
        text = buf.getvalue()
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
        if sys.stdout:
            sys.stdout.write(text)
        sys.exit(0 if ok else 1)
    if not IS_WIN:
        print("HexaClip fonctionne sous Windows 10/11 (le moteur se teste ailleurs avec --selftest).")
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    if not _single_instance():
        ctypes.windll.user32.MessageBoxW(None, "HexaClip est déjà ouvert (regarde dans la barre des tâches, "
                                               "près de l'horloge).", APP, 0x40)
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HexaLabs.HexaClip")
    except Exception:
        pass
    FONT = paths.load_brand_font() or FONT
    from engine import Engine, log_error, LOG_PATH
    try:
        engine = Engine()
        engine.start()
        App(engine, hidden=args.discret).run()
    except Exception:
        log_error("Démarrage")
        try:
            ctypes.windll.user32.MessageBoxW(None, f"Erreur au démarrage, détails dans :\n{LOG_PATH}", APP, 0x10)
        except Exception:
            pass


if __name__ == "__main__":
    main()
