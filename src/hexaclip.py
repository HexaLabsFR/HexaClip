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

VERSION = "0.4.0"
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
                pystray.MenuItem("Rechercher une mise à jour", lambda: app.call(app.check_update)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quitter", lambda: app.call(app.quit)),
            )
            self.icon = pystray.Icon("HexaClip", img, "HexaClip", m)
            self.icon.run_detached()
        except Exception as e:
            app.engine.emit("info", f"Icône de notification indisponible ({e})")

    def set_title(self, text):
        if self.icon and getattr(self, "_title", None) != text:
            self._title = text
            try:
                self.icon.title = text
            except Exception:
                pass

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
class DemoDialog:
    """Fenêtre « Importer une démo » : analyse, choix du joueur et des rounds, lecture + enregistrement."""

    def __init__(self, app, path):
        import demos
        self.app, self.path, self.demos = app, path, demos
        tk, ttk = app.tk, app.ttk
        self.win = w = tk.Toplevel(app.root, bg=C["bg"])
        w.title("HexaClip · Démo")
        w.geometry("760x680")
        w.minsize(640, 520)
        w.transient(app.root)
        self.info, self.rounds, self.checked, self.players = None, [], set(), []
        pad = dict(padx=20)
        self.title = app.label(w, "Analyse de la démo…", 15, "fg", True)
        self.title.pack(anchor="w", pady=(18, 2), **pad)
        self.sub = app.label(w, os.path.basename(path), 9, "muted")
        self.sub.pack(anchor="w", **pad)

        row = tk.Frame(w, bg=C["bg"])
        row.pack(fill="x", pady=(14, 6), **pad)
        app.label(row, "Joueur", 10, "soft", True).pack(side="left")
        self.player = tk.StringVar()
        self.pcombo = ttk.Combobox(row, textvariable=self.player, state="readonly", width=38)
        self.pcombo.pack(side="left", padx=10)
        self.pcombo.bind("<<ComboboxSelected>>", lambda e: self._fill_rounds())
        for txt, n in (("3K et plus", 3), ("2K et plus", 2), ("Tous", 1)):
            app.button(row, txt, lambda n=n: self._check_min(n)).pack(side="right", padx=(6, 0))

        cols = [("sel", "☐", 40, "center"), ("round", "Round", 70, "center"), ("type", "Action", 80, "center"),
                ("armes", "Armes", 260, "w"), ("hs", "HS", 50, "center")]
        box, self.tv = app._tree(w, cols, heights=12)
        box.pack(fill="both", expand=True, pady=(6, 0), **pad)
        self._box = box
        self.tv.column("armes", stretch=True)
        self.tv.tag_configure("coche", foreground=C["accent"])
        self.tv.bind("<Button-1>", self._click, add="+")
        self.tv.bind("<space>", lambda e: self._toggle([int(i) for i in self.tv.selection()]))
        self.tv.bind("<Double-1>", lambda e: "break")

        self.note = app.label(w, "HexaClip ouvre CS2 en mode rendu : le jeu calcule lui-même chaque passage (image et "
                                 "son du jeu, sans la barre de la démo), puis se ferme tout seul. Pas besoin de le "
                                 "garder au premier plan : tu peux utiliser ton PC pendant ce temps.", 9, "muted",
                              wraplength=700, justify="left")
        self.note.pack(side="bottom", anchor="w", pady=(10, 0), before=box, **pad)
        self.prog_box = tk.Frame(w, bg=C["bg"])
        self.prog = ttk.Progressbar(self.prog_box, style="Hexa.Horizontal.TProgressbar", orient="horizontal",
                                    mode="determinate", maximum=1000)
        self.prog.pack(fill="x")
        self.prog_lbl = app.label(self.prog_box, "", 9, "muted")
        self.prog_lbl.pack(anchor="w", pady=(4, 0))
        foot = tk.Frame(w, bg=C["bg"])
        foot.pack(side="bottom", fill="x", pady=14, before=self.note, **pad)
        self._pad = pad
        self.est = app.label(foot, "", 11, "fg", True)
        self.est.pack(side="left")
        self.go = app.button(foot, "Clipper ces rounds", self._start, primary=True)
        self.go.pack(side="right")
        self.stop_btn = app.button(foot, "Arrêter", self._stop)
        self.extra = app.button(foot, "Fermer CS2", self._close_cs2)
        threading.Thread(target=self._analyse, daemon=True).start()

    # --- analyse ---------------------------------------------------------
    def _analyse(self):
        try:
            info = self.demos.analyse(self.path)
            self.app.call(lambda: self._show(info))
        except Exception as ex:
            msg = str(ex)
            self.app.call(lambda: self._fail(msg))

    def _fail(self, msg):
        self.title.configure(text="Démo illisible")
        self.sub.configure(text=msg, fg=C["bad"])
        self.go.configure(state="disabled")

    def _show(self, info):
        self.info = info
        rs = info.get("rounds") or []
        mins = int((info.get("duree_s") or 0) // 60)
        self.title.configure(text=f"{pretty_map(info.get('carte', '')) or 'Démo'} · {len(rs)} rounds")
        self.sub.configure(text=f"{os.path.basename(self.path)} · {mins} min · {len(info.get('kills', []))} kills")
        js = sorted(info.get("joueurs", []), key=lambda j: -j.get("kills", 0))
        self.players = js
        labels = [f"{j['nom']} — {j['kills']} kills ({j['hs']} HS)" for j in js]
        self.pcombo.configure(values=labels)
        eng = self.app.engine
        mine = {str(eng.cfg["steamid"]), str((eng.faceit.profile or {}).get("steamid") or "")} - {""}
        pseudo = (eng.cfg["faceit_pseudo"] or "").lower()
        idx = next((i for i, j in enumerate(js) if j["steamid"] in mine), None)
        if idx is None:
            idx = next((i for i, j in enumerate(js) if pseudo and j["nom"].lower() == pseudo), 0)
        if labels:
            self.pcombo.current(idx)
        self._fill_rounds()

    def _steamid(self):
        i = self.pcombo.current()
        return self.players[i]["steamid"] if 0 <= i < len(self.players) else ""

    def _fill_rounds(self):
        from moments import LABELS
        tv = self.tv
        tv.delete(*tv.get_children())
        self.rounds = self.demos.player_rounds(self.info, self._steamid())
        rs = self.info.get("rounds") or []
        j = self.players[self.pcombo.current()] if self.players and self.pcombo.current() >= 0 else {}
        if rs and j.get("equipe") in ("CT", "T"):
            last = rs[-1]
            a, b = (last["score_ct"], last["score_t"]) if j["equipe"] == "CT" else (last["score_t"], last["score_ct"])
            res = "Victoire" if a > b else ("Défaite" if a < b else "Égalité")
            self.title.configure(text=f"{pretty_map(self.info.get('carte', '')) or 'Démo'} · {res} {a}-{b} "
                                      f"· {len(rs)} rounds")
        self.checked = {r["round"] for r in self.rounds if len(r["kills"]) >= 2}
        for r in self.rounds:
            on = r["round"] in self.checked
            tv.insert("", "end", iid=str(r["round"]), tags=("coche",) if on else (),
                      values=("☑" if on else "☐", r["round"], LABELS.get(r["type"], r["type"]),
                              ", ".join(r["armes"]), r["hs"] or ""))
        self._update()

    def _paint(self):
        for r in self.rounds:
            on = r["round"] in self.checked
            self.tv.set(str(r["round"]), "sel", "☑" if on else "☐")
            self.tv.item(str(r["round"]), tags=("coche",) if on else ())
        self._update()

    def _toggle(self, ids):
        if not ids:
            return
        on = not all(i in self.checked for i in ids)
        for i in ids:
            (self.checked.add if on else self.checked.discard)(i)
        self._paint()

    def _click(self, e):
        tv = self.tv
        if tv.identify_region(e.x, e.y) == "cell":
            row = tv.identify_row(e.y)
            if row:
                self._toggle([int(row)])
                return "break"

    def _check_min(self, n):
        self.checked = {r["round"] for r in self.rounds if len(r["kills"]) >= n}
        self._paint()

    def _chosen(self):
        return [r for r in self.rounds if r["round"] in self.checked]

    def _update(self):
        ch = self._chosen()
        if not self.info:
            return
        cfg = self.app.engine.cfg
        sec = self.demos.estimate_s(ch, self.info.get("tickrate"), float(cfg["avant_s"]), float(cfg["apres_s"]))
        k = sum(len(r["kills"]) for r in ch)
        self.est.configure(text=(f"{len(ch)} round{'s' if len(ch) > 1 else ''} · {k} kills · "
                                 f"≈ {max(1, round(sec / 60))} min de lecture") if ch else "Coche au moins un round")
        self.go.configure(state="normal" if ch else "disabled")

    # --- lecture -----------------------------------------------------------
    def _start(self):
        from tkinter import messagebox
        ch = self._chosen()
        if not ch:
            return
        if self.demos.cs2_alive():
            if not messagebox.askyesno(APP, "CS2 est ouvert. HexaClip doit le relancer en mode rendu pour calculer "
                                            "les clips.\n\nFermer CS2 maintenant ?", parent=self.win):
                return
            self.est.configure(text="Fermeture de CS2…", fg=C["fg"])
            self.win.update_idletasks()
            self.demos.kill_cs2()
        self.extra.pack_forget()
        d = self.app.engine.start_demo(self.info, self._steamid(), ch)
        self.go.pack_forget()
        self.stop_btn.pack(side="right")
        self.pcombo.configure(state="disabled")
        self.prog.configure(value=0)
        self.prog_box.pack(side="bottom", fill="x", pady=(10, 0), after=self.note, **self._pad)
        self._watch(d)

    def _watch(self, d):
        if not self.win.winfo_exists():
            return
        if d.state in ("terminé", "erreur", "arrêté"):
            self.stop_btn.pack_forget()
            self.go.pack(side="right")
            self.pcombo.configure(state="readonly")
            frac, _ = d.progress()
            self.prog.configure(value=int(frac * 1000))
            self.prog_lbl.configure(text="")
            if d.state == "terminé":
                n = d.encoded_n
                self.est.configure(text=f"Terminé : {n} passage{'s' if n > 1 else ''} calculé{'s' if n > 1 else ''}. "
                                        "Les clips sont dans Montage et Clips.", fg=C["accent"])
                self.go.configure(text="Recommencer")
            else:
                self.est.configure(text=d.error or d.state, fg=C["bad"] if d.state == "erreur" else C["warn"])
                if "CS2 est ouvert" in (d.error or ""):
                    self.extra.pack(side="right", padx=8)
            self.app.refresh_montage(keep_period=False) if self.app.current == "Montage" else None
            return
        frac, left = d.progress()
        self.prog.configure(value=int(frac * 1000))
        self.est.configure(text=f"Démo : {d.state}…", fg=C["accent"])
        reste = "" if left is None else (" · encore ~" + (f"{left // 60} min {left % 60:02d} s" if left >= 60
                                                           else f"{left} s"))
        self.prog_lbl.configure(text=f"{int(frac * 100)} %{reste} · CS2 calcule les passages, il se fermera tout seul")
        self.win.after(400, lambda: self._watch(d))

    def _stop(self):
        d = self.app.engine.demo
        if d:
            d.cancel()

    def _close_cs2(self):
        self.demos.kill_cs2()
        self.extra.pack_forget()
        self.est.configure(text="CS2 fermé. Clique sur « Clipper ces rounds ».", fg=C["fg"])


class App:
    TABS = ["Accueil", "Montage", "Clips", "Compils", "Faceit", "Réglages"]

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
        self.scrollers = {}
        self.root.bind_all("<MouseWheel>", self._wheel)
        self.root.bind_all("<Button-4>", lambda e: self._wheel(e, -120))
        self.root.bind_all("<Button-5>", lambda e: self._wheel(e, 120))
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.events_log = []
        import updater
        self.updater = updater.Updater(VERSION, on_change=lambda u: self.call(self._on_update),
                                       log=__import__("engine").log)
        self.tray = Tray(self)
        self.told_tray = False
        self._build()
        if hidden:
            self.root.withdraw()
        self.root.after(400, self._poll)
        self.root.after(1500, self._first_run)
        self.root.after(20_000, self._auto_update_tick)

    def _wheel(self, e, delta=None):
        cv = self.scrollers.get(self.current)
        if cv is not None:
            d = delta if delta is not None else -e.delta
            cv.yview_scroll(int(d / 120) or (1 if d > 0 else -1), "units")

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
        st.configure("Hexa.Horizontal.TProgressbar", troughcolor=C["raise"], background=C["accent"],
                     bordercolor=C["line"], lightcolor=C["accent"], darkcolor=C["accent_d"], thickness=14)
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
        self.upd_btn = self.button(top, "", self.restart_update, primary=True)   # visible quand une MàJ est prête

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
            "Accueil": self._page_home(), "Montage": self._page_montage(), "Clips": self._page_clips(),
            "Compils": self._page_compils(),
            "Faceit": self._page_faceit(), "Réglages": self._page_settings(),
        }
        self.current = None
        self.show_tab("Accueil")

    def show_tab(self, name):
        for t, b in self.tab_btns.items():
            b.configure(fg=C["accent"] if t == name else C["muted"])
        if self.current:
            self.pages[self.current].pack_forget()
        if name != "Clips" and getattr(self, "clip_player", None):
            self.clip_player.stop()
        self.pages[name].pack(fill="both", expand=True)
        self.current = name
        {"Clips": self.refresh_clips, "Compils": self.refresh_compils, "Faceit": self.refresh_faceit_tab,
         "Montage": self.refresh_montage}.get(name, lambda: None)()

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
            nom = s.get("son_nom") or "sortie par défaut"
            if not s["son"]:
                self._set_stat("son", "Muet", "Vidéo sans son (voir journal)", "warn")
            elif s.get("son_silence_s", 0) > 90:
                self._set_stat("son", "Silence", f"Rien entendu sur « {nom} » depuis "
                               f"{int(s['son_silence_s'] // 60)} min : vérifie ta sortie son Windows", "warn")
            else:
                self._set_stat("son", "Capté", nom, "good")
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
        d = self.engine.demo
        if d is not None and d.state not in ("terminé", "erreur", "arrêté"):
            frac, _ = d.progress()
            rec = f"● Démo {int(frac * 100)} %"
        self.pill.configure(text=rec, fg=C["bad"] if s["rec"] or rec.startswith("● Démo") else C["muted"])
        self.tray.set_title(f"HexaClip · {rec.lstrip('● ')}")

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

    # ------------------------------------------------------------- Montage (choix des kills)
    def _page_montage(self):
        tk, ttk = self.tk, self.ttk
        f = tk.Frame(self.body, bg=C["bg"])
        head = tk.Frame(f, bg=C["bg"])
        head.pack(fill="x", pady=(0, 10))
        self.label(head, "Choisis tes kills", 14, "fg", True).pack(side="left")
        self.mt_period = tk.StringVar()
        self.mt_combo = ttk.Combobox(head, textvariable=self.mt_period, width=40, state="readonly")
        self.mt_combo.pack(side="left", padx=14)
        self.mt_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_montage(keep_period=True))
        self.button(head, "Tout décocher", lambda: self._mt_all(False)).pack(side="right")
        self.button(head, "Tout cocher", lambda: self._mt_all(True)).pack(side="right", padx=8)
        self.button(head, "Sélection auto", self._mt_auto).pack(side="right")
        self.button(head, "Importer une démo", self._demo_import).pack(side="right", padx=8)

        outer = tk.Frame(f, bg=C["bg"])
        outer.pack(fill="both", expand=True)
        cv = tk.Canvas(outer, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        self.mt_list = tk.Frame(cv, bg=C["bg"])
        self.mt_list.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        win = cv.create_window((0, 0), window=self.mt_list, anchor="nw")
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.scrollers["Montage"] = cv

        foot = self.card(f)
        foot.pack(fill="x", pady=(12, 0))
        self.mt_count = self.label(foot, "", 12, "fg", True, bg="panel")
        self.mt_count.pack(side="left", padx=16, pady=12)
        self.mt_hint = self.label(foot, "", 9, "muted", bg="panel")
        self.mt_hint.pack(side="left")
        self.button(foot, "Créer la compil", self._mt_compile, primary=True).pack(side="right", padx=12, pady=10)
        self.button(foot, "Voir la sélection", self._mt_preview_all).pack(side="right")
        self.mt_sel, self.mt_moments, self.mt_imgs, self.mt_chips = {}, [], [], {}
        return f

    def _mt_periods(self):
        out = []
        for s in self.engine.db.sessions(30):
            k = s.get("kills") or 0
            d = fmt_date(s["debut"])
            quand = ("d'" + d) if d.startswith(("aujourd'hui", "hier")) else ("du " + d)
            quoi = "Démo" if str(s["session"]).startswith("demo-") else "Session"
            out.append((f"{quoi} {quand} · {k} kill{'s' if k > 1 else ''}", ("session", s["session"])))
        out.append(("Dernières 24 h", ("since", 1)))
        out.append(("7 derniers jours", ("since", 7)))
        return out

    def refresh_montage(self, keep_period=False):
        from moments import all_indices
        periods = self._mt_periods()
        self.mt_period_map = dict(periods)
        labels = [p[0] for p in periods]
        self.mt_combo.configure(values=labels)
        if not keep_period or self.mt_period.get() not in self.mt_period_map:
            self.mt_period.set(labels[0])
        kind, val = self.mt_period_map[self.mt_period.get()]
        db = self.engine.db
        if kind == "session":
            ms = db.moments(session=val, limit=1000)
        else:
            ms = db.moments(since=time.time() - val * 86400, limit=1000)
        ms = [m for m in ms if m.get("fichier") and os.path.isfile(m["fichier"]) and m.get("defaut") != "figée"]
        ms.sort(key=lambda m: m["cree"])
        self.mt_moments = ms
        auto = self.engine.effective_choice(ms, self.engine.compile_budget(self._fin_on()))
        self.mt_sel = {m["id"]: set(auto.get(m["id"], [])) & set(all_indices(m)) for m in ms}
        self._mt_render()

    def _fin_on(self):
        return bool(self.cfg["compil_fin_faceit"] and self.engine.faceit.configured())

    def _mt_render(self):
        from moments import LABELS, all_indices, per_kill
        tk = self.tk
        for w in self.mt_list.winfo_children():
            w.destroy()
        self.mt_imgs, self.mt_chips = [], {}
        if not self.mt_moments:
            self.label(self.mt_list, "Aucun kill sur cette période. Lance CS2 : chaque kill est gardé tout seul, "
                                     "puis tu choisis ici ceux qui vont dans la compil.", 11, "muted",
                       wraplength=800, justify="left").pack(anchor="w", pady=20)
            self._mt_update_count()
            return
        last_group = None
        for m in self.mt_moments:
            group = (m.get("session"), m.get("partie"), m.get("carte"))
            if group != last_group:
                last_group = group
                hdr = tk.Frame(self.mt_list, bg=C["bg"])
                hdr.pack(fill="x", pady=(14, 6))
                title = (m.get("carte") or "Partie") + f" · {fmt_date(m['cree'])}"
                self.label(hdr, title.upper(), 9, "accent", True).pack(side="left")
                fm = self._match_for(m)
                if fm:
                    res = {1: "victoire", 0: "défaite"}.get(fm.get("victoire"), "")
                    self.label(hdr, f"  Faceit {fm.get('score') or ''} {res}", 9, "muted").pack(side="left")
            row = self.card(self.mt_list)
            row.pack(fill="x", pady=4)
            img = None
            if m.get("miniature") and os.path.isfile(m["miniature"]):
                try:
                    img = tk.PhotoImage(file=m["miniature"])
                    self.mt_imgs.append(img)
                except Exception:
                    img = None
            th = tk.Label(row, image=img, bg=C["raise"], width=192 if img else 24, height=108 if img else 6,
                          cursor="hand2")
            th.pack(side="left", padx=10, pady=10)
            th.bind("<Button-1>", lambda e, m=m: self._mt_preview(m))
            info = tk.Frame(row, bg=C["panel"])
            info.pack(side="left", padx=(4, 12), anchor="n", pady=12)
            line = tk.Frame(info, bg=C["panel"])
            line.pack(anchor="w")
            self.label(line, f"Round {m['round']}" if m.get("round") else "Clip", 12, "fg", True,
                       bg="panel").pack(side="left")
            big = m["type"] in ("3k", "4k", "ace")
            tk.Label(line, text=LABELS.get(m["type"], m["type"]), bg=C["accent"] if big else C["raise"],
                     fg=C["on_accent"] if big else C["soft"], font=(FONT, 9, "bold"), padx=8, pady=1).pack(
                side="left", padx=8)
            if m.get("favori"):
                self.label(line, "★", 12, "warn", bg="panel").pack(side="left")
            hs = sum(1 for x in (m.get("kills_hs") or []) if x)
            sub = f"{dt.datetime.fromtimestamp(m['cree']):%H:%M}" + (f" · {hs} HS" if hs else "")
            self.label(info, sub, 9, "muted", bg="panel").pack(anchor="w", pady=(2, 8))
            chips = tk.Frame(info, bg=C["panel"])
            chips.pack(anchor="w")
            hs_list = m.get("kills_hs") or []
            for i in all_indices(m):
                if per_kill(m):
                    txt = f"Kill {i + 1}" + (" · HS" if i < len(hs_list) and hs_list[i] else "")
                else:
                    txt = "Clip entier" if m["type"] == "manuel" else "Round entier"
                b = tk.Label(chips, text=txt, font=(FONT, 10, "bold"), padx=12, pady=6, cursor="hand2")
                b.pack(side="left", padx=(0, 6))
                b.bind("<Button-1>", lambda e, mid=m["id"], i=i: self._mt_toggle(mid, i))
                b.bind("<Button-3>", lambda e, m=m, i=i: self._mt_preview(m, [i]))
                self.mt_chips[(m["id"], i)] = b
            side = tk.Frame(row, bg=C["panel"])
            side.pack(side="right", padx=12)
            self.button(side, "▶ Voir", lambda m=m: self._mt_preview(m), bg="panel").pack(pady=(0, 6))
            self.button(side, "★" if not m.get("favori") else "Retirer ★",
                        lambda m=m: self._mt_fav(m), bg="panel").pack()
        self._mt_paint()

    def _preview_outro(self):
        """Capture l'overlay FaceitSync (si un lien est donné) et montre l'écran de fin tel qu'il sortira."""
        self._save_settings()

        def run():
            import overlay_fin
            import montage
            try:
                th = montage.theme_module(self.cfg["compil_style"])
                fin = None
                if self.engine.faceit.configured():
                    from faceit import outro_lines
                    fin = outro_lines(self.engine.faceit.session_summary(time.time() - 6 * 3600))
                url = (self.cfg["faceitsync_overlay"] or "").strip()
                if url:
                    png = overlay_fin.capture(url, paths.data("overlay_fin.png"))
                    if not png:
                        self.engine.emit("erreur", "Overlay FaceitSync : capture impossible (vérifie le lien).")
                    else:
                        fin = dict(fin or {}, overlay=png)
                frames = list(th.outro_frames(self.cfg["pseudo"], fin, self.cfg["compil_appel"]))
                out = paths.data("apercu_fin.png")
                frames[-1].save(out)
                self.call(lambda: open_path(out))
            except Exception as ex:
                msg = str(ex)
                self.engine.emit("erreur", f"Aperçu de l'écran de fin : {msg}")
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------- Démos
    def _demo_import(self):
        from tkinter import filedialog
        start = os.path.join(os.path.expanduser("~"), "Downloads")
        path = filedialog.askopenfilename(
            parent=self.root, title="Choisis une démo CS2", initialdir=start if os.path.isdir(start) else None,
            filetypes=[("Démos CS2", "*.dem *.zst *.gz *.bz2"), ("Tous les fichiers", "*.*")])
        if path:
            DemoDialog(self, path)

    def _match_for(self, m):
        if not m.get("match_faceit"):
            return None
        if not hasattr(self, "_mcache") or time.time() - self._mcache[0] > 30:
            self._mcache = (time.time(), {x["match_id"]: x for x in self.engine.db.matchs(200)})
        return self._mcache[1].get(m["match_faceit"])

    def _mt_paint(self):
        for (mid, i), b in self.mt_chips.items():
            on = i in self.mt_sel.get(mid, set())
            b.configure(bg=C["accent"] if on else C["raise"], fg=C["on_accent"] if on else C["muted"])
        self._mt_update_count()

    def _mt_update_count(self):
        from moments import choice_duration
        n_kills, dur = 0, 0.0
        for m in self.mt_moments:
            idx = sorted(self.mt_sel.get(m["id"], set()))
            if idx:
                dur += choice_duration(m, idx)
                n_kills += len(idx) if m.get("kills_t") else 0
        target = self.engine.compile_budget(self._fin_on())
        over = dur > target + 0.5
        self.mt_count.configure(text=f"Sélection : {n_kills} kill{'s' if n_kills > 1 else ''} · {dur:.0f} s / "
                                     f"{target:.0f} s", fg=C["warn"] if over else C["fg"])
        self.mt_hint.configure(text=("Plus long que la durée choisie : la compil sera plus longue."
                                     if over else "Clic sur un kill pour l'ajouter ou l'enlever · clic droit "
                                                  "pour le revoir"))

    def _mt_save(self):
        for m in self.mt_moments:
            self.engine.db.set_choice(m["id"], sorted(self.mt_sel.get(m["id"], set())))

    def _mt_toggle(self, mid, i):
        s = self.mt_sel.setdefault(mid, set())
        s.symmetric_difference_update({i})
        self._mt_save()
        self._mt_paint()

    def _mt_all(self, on):
        from moments import all_indices
        for m in self.mt_moments:
            self.mt_sel[m["id"]] = set(all_indices(m)) if on else set()
        self._mt_save()
        self._mt_paint()

    def _mt_auto(self):
        for m in self.mt_moments:
            self.engine.db.set_choice(m["id"], None)
            m["choix"] = None
        auto = self.engine.effective_choice(self.mt_moments, self.engine.compile_budget(self._fin_on()))
        self.mt_sel = {m["id"]: set(auto.get(m["id"], [])) for m in self.mt_moments}
        self._mt_paint()

    def _mt_fav(self, m):
        self.engine.db.set_moment(m["id"], favori=0 if m.get("favori") else 1)
        m["favori"] = 0 if m.get("favori") else 1
        self._mt_render()

    def _mt_preview(self, m, idx=None):
        """Revoit le round (ou les kills donnés) avec ffplay, sinon le lecteur vidéo de Windows."""
        from moments import kill_windows, merge, per_kill
        import ffmpeg_tools as ff
        wins = kill_windows(m)
        if idx is not None and per_kill(m):
            wins = [wins[i] for i in idx]
        wins = merge(wins) or [[0, 0]]
        a, b = wins[0][0], wins[-1][1]
        play = ff.find("ffplay")
        if play and b > a:
            ff.popen([play, "-hide_banner", "-loglevel", "error", "-autoexit", "-x", "1280", "-y", "720",
                      "-window_title", f"HexaClip · Round {m.get('round') or ''}", "-ss", f"{max(0, a):.2f}",
                      "-t", f"{b - a:.2f}", m["fichier"]], creationflags=0)
        else:
            open_path(m["fichier"])

    def _mt_preview_all(self):
        first = next((m for m in self.mt_moments if self.mt_sel.get(m["id"])), None)
        if first:
            self._mt_preview(first, sorted(self.mt_sel[first["id"]]))

    def _mt_compile(self):
        sel = {m["id"]: sorted(self.mt_sel.get(m["id"], set())) for m in self.mt_moments
               if self.mt_sel.get(m["id"])}
        if not sel:
            self.mt_hint.configure(text="Coche au moins un kill.", fg=C["warn"])
            return
        self._mt_save()
        threading.Thread(target=self.engine.compile, kwargs={"selection": sel}, daemon=True).start()
        self.mt_hint.configure(text="Montage en cours… la compil apparaîtra dans l'onglet Compils.", fg=C["accent"])

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

    CLIP_FILTERS = ["Tous les clips", "★ Favoris", "3K et plus", "Aujourd'hui", "Écartés des compils",
                    "Images figées"]

    def _page_clips(self):
        tk, ttk = self.tk, self.ttk
        f = tk.Frame(self.body, bg=C["bg"])
        head = tk.Frame(f, bg=C["bg"])
        head.pack(fill="x", pady=(0, 10))
        self.label(head, "Tes actions gardées", 14, "fg", True).pack(side="left")
        self.clips_count = self.label(head, "", 10, "muted")
        self.clips_count.pack(side="left", padx=12)
        self.clips_filter = tk.StringVar(value=self.CLIP_FILTERS[0])
        cb = ttk.Combobox(head, textvariable=self.clips_filter, values=self.CLIP_FILTERS, state="readonly", width=20)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_clips())
        self.button(head, "Tout décocher", lambda: self._clips_check_all(False)).pack(side="right", padx=8)
        self.button(head, "Tout cocher", lambda: self._clips_check_all(True)).pack(side="right")

        main = tk.Frame(f, bg=C["bg"])
        main.pack(fill="both", expand=True)
        # aperçu à droite
        side = self.card(main)
        side.pack(side="right", fill="y", padx=(12, 0))
        from apercu import MiniPlayer
        self.clip_player = MiniPlayer(tk, side, C, FONT)
        self.clip_player.view.pack(padx=12, pady=(12, 0))
        self.clip_player.view.bind("<Button-1>", lambda e: self._clip_action("lire"))
        self.clip_player.bar.pack(padx=12, pady=(6, 0))
        self.clip_title = self.label(side, "Sélectionne un clip", 13, "fg", True, bg="panel")
        self.clip_title.pack(anchor="w", padx=14, pady=(12, 0))
        self.clip_sub = self.label(side, "Il se lance ici en boucle, sans le son.", 9, "muted", bg="panel",
                                   wraplength=390, justify="left")
        self.clip_sub.pack(anchor="w", padx=14, pady=(2, 10))
        row = tk.Frame(side, bg=C["panel"])
        row.pack(anchor="w", padx=12, pady=(0, 12))
        self.button(row, "▶ Avec le son", lambda: self._clip_action("lire"), primary=True, bg="panel").pack(
            side="left")
        self.button(row, "Dossier", lambda: self._clip_action("dossier"), bg="panel").pack(side="left", padx=6)
        self.button(row, "★", lambda: self._clip_action("fav_un"), bg="panel").pack(side="left")
        self.label(side, "Frise : chaque trait vert = un kill.", 8, "muted", bg="panel").pack(anchor="w", padx=14)

        cols = [("sel", "☐", 36, "center"), ("quand", "Quand", 130, "w"), ("type", "Action", 76, "center"),
                ("carte", "Map", 110, "w"), ("round", "Round", 54, "center"), ("hs", "HS", 40, "center"),
                ("taille", "Taille", 70, "e"), ("fav", "★", 32, "center"), ("etat", "Compil", 86, "center")]
        box, self.clips_tv = self._tree(main, cols)
        box.pack(side="left", fill="both", expand=True)
        tv = self.clips_tv
        tv.column("sel", stretch=False)
        tv.column("quand", stretch=True)
        tv.tag_configure("coche", foreground=C["accent"])
        tv.tag_configure("absent", foreground=C["muted"])
        for key in ("quand", "type", "carte", "taille"):
            tv.heading(key, command=lambda k=key: self._clips_sort(k))
        tv.heading("sel", command=lambda: self._clips_check_all(len(self.clips_checked) < len(self._clips_view)))
        tv.bind("<Button-1>", self._clips_click, add="+")
        tv.bind("<space>", lambda e: self._clips_toggle([int(i) for i in tv.selection()]))
        tv.bind("<<TreeviewSelect>>", lambda e: self._clips_preview())
        tv.bind("<Double-1>", lambda e: self._clip_action("lire"))
        tv.bind("<Delete>", lambda e: self._clip_action("suppr"))

        btns = tk.Frame(f, bg=C["bg"])
        btns.pack(fill="x", pady=(12, 0))
        self.clips_sel_lbl = self.label(btns, "", 10, "soft", True)
        self.clips_sel_lbl.pack(side="left", padx=(0, 12))
        for text, act in [("★ Favori", "fav"), ("Garder / écarter des compils", "exclu"), ("Supprimer", "suppr")]:
            self.button(btns, text, lambda a=act: self._clip_action(a)).pack(side="left", padx=(0, 8))
        self.button(btns, "Compil avec la sélection", lambda: self._clip_action("compil"), primary=True).pack(
            side="right")
        self.label(f, "Coche les clips (case ☐ ou barre Espace, Ctrl / Maj pour en prendre plusieurs) pour les "
                      "supprimer, les mettre en favori ou en faire une compil. Les favoris (★) passent en priorité "
                      "et ne sont jamais supprimés par le ménage automatique.", 9, "muted", wraplength=1000,
                   justify="left").pack(anchor="w", pady=(10, 0))
        self.clips_checked, self._clips, self._clips_view, self._clips_sort_key = set(), {}, [], ("quand", True)
        return f

    @staticmethod
    def _size(m):
        try:
            return os.path.getsize(m["fichier"]) if m.get("fichier") else 0
        except OSError:
            return 0

    @staticmethod
    def _fmt_size(n):
        return f"{n / 1e9:.1f} Go" if n >= 1e9 else f"{n / 1e6:.0f} Mo"

    def refresh_clips(self):
        from moments import LABELS
        tv = self.clips_tv
        keep = tv.selection()
        tv.delete(*tv.get_children())
        self._clips = {m["id"]: m for m in self.engine.db.moments(limit=2000)}
        for m in self._clips.values():
            m["_taille"] = self._size(m)
        flt = self.clips_filter.get()
        today = dt.date.today()
        view = [m for m in self._clips.values() if
                flt == "Tous les clips"
                or (flt == "★ Favoris" and m.get("favori"))
                or (flt == "3K et plus" and m["type"] in ("3k", "4k", "ace"))
                or (flt == "Aujourd'hui" and dt.date.fromtimestamp(m["cree"]) == today)
                or (flt == "Écartés des compils" and m.get("exclu"))
                or (flt == "Images figées" and m.get("defaut") == "figée")]
        key, desc = self._clips_sort_key
        order = {"quand": lambda m: m["cree"], "type": lambda m: (m.get("kills") or 0, m["cree"]),
                 "carte": lambda m: ((m.get("carte") or "").lower(), m["cree"]), "taille": lambda m: m["_taille"]}
        view.sort(key=order[key], reverse=desc)
        self._clips_view = view
        self.clips_checked &= set(self._clips)
        for m in view:
            exists = m["_taille"] > 0
            etat = "image figée" if m.get("defaut") == "figée" else (
                "écarté" if m.get("exclu") else ("" if exists else "fichier absent"))
            on = m["id"] in self.clips_checked
            tv.insert("", "end", iid=str(m["id"]), tags=("coche",) if on else (("absent",) if not exists else ()),
                      values=("☑" if on else "☐", fmt_date(m["cree"]), LABELS.get(m["type"], m["type"]),
                              m.get("carte") or "—", m.get("round") or "—", m.get("hs") if m.get("kills") else "—",
                              self._fmt_size(m["_taille"]) if exists else "—", "★" if m.get("favori") else "", etat))
        total = sum(m["_taille"] for m in self._clips.values())
        self.clips_count.configure(text=f"{len(view)} clips affichés · {self._fmt_size(total)} au total")
        keep = [i for i in keep if tv.exists(i)]
        if keep:
            tv.selection_set(keep)
        self._clips_update_sel()

    def _clips_update_sel(self):
        ch = [self._clips[i] for i in self.clips_checked if i in self._clips]
        if ch:
            self.clips_sel_lbl.configure(text=f"{len(ch)} coché{'s' if len(ch) > 1 else ''} · "
                                              f"{self._fmt_size(sum(m.get('_taille', 0) for m in ch))}", fg=C["accent"])
        else:
            self.clips_sel_lbl.configure(text="Aucun clip coché", fg=C["muted"])
        self.clips_tv.heading("sel", text="☑" if ch and len(ch) >= len(self._clips_view) else "☐")

    def _clips_paint(self, ids):
        tv = self.clips_tv
        for i in ids:
            if not tv.exists(str(i)):
                continue
            on = i in self.clips_checked
            tv.set(str(i), "sel", "☑" if on else "☐")
            tv.item(str(i), tags=("coche",) if on else ())
        self._clips_update_sel()

    def _clips_toggle(self, ids):
        ids = [i for i in ids if i in self._clips]
        if not ids:
            return
        on = not all(i in self.clips_checked for i in ids)
        for i in ids:
            (self.clips_checked.add if on else self.clips_checked.discard)(i)
        self._clips_paint(ids)

    def _clips_click(self, e):
        tv = self.clips_tv
        if tv.identify_region(e.x, e.y) == "cell" and tv.identify_column(e.x) == "#1":
            row = tv.identify_row(e.y)
            if row:
                sel = [int(i) for i in tv.selection()]
                # case cochée sur une ligne déjà dans une sélection multiple : on coche toute la sélection
                self._clips_toggle(sel if int(row) in sel and len(sel) > 1 else [int(row)])
                return "break"

    def _clips_check_all(self, on):
        ids = [m["id"] for m in self._clips_view]
        if on:
            self.clips_checked |= set(ids)
        else:
            self.clips_checked -= set(ids)
        self._clips_paint(ids)

    def _clips_sort(self, key):
        cur, desc = self._clips_sort_key
        self._clips_sort_key = (key, not desc if cur == key else key in ("quand", "taille", "type"))
        self.refresh_clips()

    def _clips_preview(self):
        from moments import LABELS
        sel = self.clips_tv.selection()
        m = self._clips.get(int(sel[0])) if sel else None
        if not m:
            return
        exists = m.get("_taille", 0) > 0
        title = LABELS.get(m["type"], m["type"]) + " · " + (m.get("carte") or "CS2")
        if m.get("round"):
            title += f" · round {m['round']}"
        self.clip_title.configure(text=title)
        hs = m.get("hs") or 0
        parts = [fmt_date(m["cree"])]
        if m.get("kills"):
            parts.append(f"{m['kills']} kill{'s' if m['kills'] > 1 else ''}" + (f" dont {hs} HS" if hs else ""))
        parts.append(self._fmt_size(m["_taille"]) if exists else "fichier absent")
        if m.get("favori"):
            parts.append("★ favori")
        self.clip_sub.configure(text=" · ".join(parts))
        if exists:
            mini = m.get("miniature") if m.get("miniature") and os.path.isfile(m["miniature"]) else None
            self.clip_player.show(m["fichier"], m.get("kills_t") or [], mini)
        else:
            self.clip_player.blank("Fichier introuvable")

    def _selected_clips(self):
        """Clips cochés ; sinon la ou les lignes surlignées."""
        ids = [i for i in self.clips_checked if i in self._clips] or \
              [int(i) for i in self.clips_tv.selection() if int(i) in self._clips]
        return [self._clips[i] for i in ids]

    def _clip_action(self, act):
        from tkinter import messagebox
        if act in ("lire", "dossier", "fav_un"):
            cur = self.clips_tv.selection()
            sel = [self._clips[int(cur[0])]] if cur and int(cur[0]) in self._clips else self._selected_clips()[:1]
        else:
            sel = self._selected_clips()
        if not sel:
            return
        if act == "lire":
            self._play_with_sound(sel[0])
            return
        if act == "dossier":
            show_in_folder(sel[0]["fichier"])
            return
        if act in ("fav", "fav_un"):
            on = 0 if all(m.get("favori") for m in sel) else 1
            for m in sel:
                self.engine.db.set_moment(m["id"], favori=on)
        elif act == "exclu":
            on = 0 if all(m.get("exclu") for m in sel) else 1
            for m in sel:
                self.engine.db.set_moment(m["id"], exclu=on)
        elif act == "suppr":
            size = self._fmt_size(sum(m.get("_taille", 0) for m in sel))
            fav = sum(1 for m in sel if m.get("favori"))
            msg = f"Supprimer {len(sel)} clip{'s' if len(sel) > 1 else ''} du disque ({size} libérés) ?"
            if fav:
                msg += f"\n\nDont {fav} favori{'s' if fav > 1 else ''} ★."
            if not messagebox.askyesno(APP, msg):
                return
            self.clip_player.blank()
            for m in sel:
                self.engine.delete_moment(m["id"])
                self.clips_checked.discard(m["id"])
        elif act == "compil":
            ids = [m["id"] for m in sorted(sel, key=lambda m: m["cree"])]
            threading.Thread(target=self.engine.compile, kwargs={"ids": ids}, daemon=True).start()
            self.show_tab("Accueil")
            return
        self.refresh_clips()
        if act in ("fav", "fav_un"):
            self._clips_preview()

    def _play_with_sound(self, m):
        """Lecteur complet avec le son (ffplay), sinon le lecteur vidéo de Windows."""
        import ffmpeg_tools as ff
        play = ff.find("ffplay")
        if not m.get("fichier") or not os.path.isfile(m["fichier"]):
            return
        if play:
            ff.popen([play, "-hide_banner", "-loglevel", "error", "-autoexit", "-x", "1280", "-y", "720",
                      "-window_title", f"HexaClip · {m.get('carte') or ''} round {m.get('round') or ''}",
                      m["fichier"]], creationflags=0)
        else:
            open_path(m["fichier"])

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
        self.scrollers["Réglages"] = cv
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

        def folder(key, title, file=False, types=(("Image PNG", "*.png"),)):
            v = tk.StringVar(value=self.cfg[key])
            self.vars[key] = (v, str)

            def pick():
                from tkinter import filedialog
                p = (filedialog.askopenfilename(title=title, filetypes=list(types)) if file
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
        row("Garder", combo("kills_min", [1, 2, 3, 4, 5], 18, ["Tous les kills", "Rounds à 2 kills et +",
                                                               "3k et +", "4k et +", "Aces seulement"]),
            "tu choisis ensuite dans l'onglet Montage")
        row("Secondes avant le 1er kill", entry("avant_s", 8))
        row("Secondes après le dernier kill", entry("apres_s", 8))
        row("Touche « sauver maintenant »", combo("touche_manuelle", ["F9", "F8", "F10", "F11", "F7", "F6", ""], 18,
                                                  ["F9", "F8", "F10", "F11", "F7", "F6", "Aucune"]),
            "prise en compte au prochain lancement")
        row("Durée sauvée par la touche", combo("duree_manuelle_s", [10, 15, 20, 30], 18,
                                                ["10 s", "15 s", "20 s", "30 s"]))

        section("Compils TikTok", "Habillage : intro animée avec ton pseudo, plaque pseudo en haut des clips, "
                                  "transitions, écran de fin avec ton Elo et « HexaClip by Hexa Labs ».")
        row("Ton pseudo", entry("pseudo", 22), "affiché dans l'intro, en haut des clips et à la fin")
        row("Style des vidéos", combo("compil_style", ["spectre", "hexaclip"], 26,
                                      ["Spectre (dégradé + viseur)", "HexaClip (menthe)"]))
        row("Compil toute seule à la fermeture de CS2", check("compil_auto"),
            "sinon tu choisis tes kills dans Montage")
        row("Durée", combo("compil_duree_s", [30, 45, 60, 90], 18, ["30 s", "45 s", "1 min", "1 min 30"]))
        row("Ordre", combo("compil_ordre", ["crescendo", "meilleure_d_abord", "chrono"], 18,
                           ["Meilleure à la fin", "Meilleure d'abord", "Chronologique"]))
        row("Cadrage", combo("compil_cadrage", ["flou", "zoom"], 18, ["Jeu entier + fond flou",
                                                                     "Zoom sur le centre"]))
        row("Compteur de kills à l'écran", check("compil_badges"))
        row("Intro animée", check("compil_intro"), "logo + pseudo + accroche, 1,7 s")
        row("Transitions entre les kills", check("compil_transitions"))
        row("Texte de fin", entry("compil_appel", 30), "ex. Abonne-toi pour la suite (vide = rien)")
        row("Texte en haut (facultatif)", entry("compil_texte", 30), "ex. le nom de ta chaîne")
        row("Logo perso (PNG transparent)", folder("compil_logo", "Logo PNG", file=True))
        self.label(f, "Vide = ton pseudo (ou le logo HexaClip si tu n'as pas mis de pseudo).", 9, "muted").pack(
            anchor="w")
        row("Écran de fin avec l'Elo Faceit", check("compil_fin_faceit"))
        row("Overlay FaceitSync (lien)", entry("faceitsync_overlay", 46), "remplace l'Elo à la fin")
        r = tk.Frame(f, bg=C["bg"])
        r.pack(fill="x", pady=(0, 4))
        self.label(r, "", 10, "soft", width=30).pack(side="left")
        self.button(r, "Voir l'aperçu de l'écran de fin", self._preview_outro).pack(side="left")
        self._transition_library(f, section, row, combo, entry, folder)
        row("Ajouter une musique", check("musique_on"), "fichiers libres de droits uniquement")
        row("Dossier des musiques", folder("musique_dossier", "Dossier de musiques"))
        row("Volume de la musique", combo("musique_volume", [0.15, 0.25, 0.4, 0.6], 18,
                                          ["Discret", "Moyen", "Fort", "Très fort"]))

        section("Mises à jour", "Comme HexaRise : vérification au démarrage puis toutes les 4 h, téléchargement en "
                                "fond, puis bouton Redémarrer. Rien à retélécharger à la main.")
        row("Mises à jour automatiques", check("maj_auto"))
        r = tk.Frame(f, bg=C["bg"])
        r.pack(fill="x", pady=4)
        self.button(r, "Rechercher maintenant", self.check_update).pack(side="left")
        self.upd_install = self.button(r, "Redémarrer et installer", self.restart_update, primary=True)
        self.upd_msg = self.label(r, f"Version installée : {VERSION}", 10, "muted")
        self.upd_msg.pack(side="left", padx=12)

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

    def _transition_library(self, f, section, row, combo, entry, folder):
        """Réglages : transitions cochées (tirées au hasard), transition après l'intro, fichier du pack, aperçu."""
        import transitions as tr
        tk = self.tk
        section("Bibliothèque de transitions", "Coche celles que tu aimes : à chaque compil, HexaClip les tire au "
                                               "hasard, jamais deux fois la même à la suite.")
        grid = tk.Frame(f, bg=C["bg"])
        grid.pack(anchor="w", pady=(2, 6))
        actives = set(self.cfg["compil_trans_lib"])
        boxes = {}
        for k, t in enumerate(tr.LIB):
            bv = tk.BooleanVar(value=t.id in actives)
            boxes[t.id] = bv
            tk.Checkbutton(grid, text=t.nom, variable=bv, bg=C["bg"], fg=C["fg"], selectcolor=C["raise"],
                           activebackground=C["bg"], activeforeground=C["fg"], font=(FONT, 10), anchor="w",
                           width=30).grid(row=k // 3, column=k % 3, sticky="w", padx=(0, 10), pady=1)
        self.vars["compil_trans_lib"] = (boxes, "lib")
        ids = ["auto"] + [t.id for t in tr.LIB]
        noms = ["Automatique (pack si fichier, sinon balayage)"] + [t.nom for t in tr.LIB]
        row("Transition après l'intro", combo("compil_trans_ouverture", ids, 40, noms))
        row("Transition du pack (vidéo)", folder("compil_pack_fichier", "Transition du pack Twitch", file=True,
                                                 types=(("Vidéo", "*.webm *.mov *.mp4 *.mkv"), ("Tous", "*.*"))))
        row("Moment de la coupe (s)", entry("compil_pack_coupe", 8),
            "quand ta transition couvre tout l'écran (0 = au milieu)")
        row("Cadrage de la transition du pack", combo("compil_pack_cadrage", ["plein", "logo"], 34,
                                                       ["Plein écran (recommandé)",
                                                        "Logo entier (moins de zoom, bords prolongés)"]))
        r = tk.Frame(f, bg=C["bg"])
        r.pack(fill="x", pady=4)
        self.button(r, "Voir un aperçu des transitions cochées", self._preview_transitions).pack(side="left")
        self.button(r, "Retrouver mon pack", self._find_pack).pack(side="left", padx=8)
        self.trans_msg = self.label(r, "", 10, "muted")
        self.trans_msg.pack(side="left", padx=12)

    def _find_pack(self):
        def run():
            p = self.engine.detect_pack(force=True)

            def done():
                if p:
                    self.vars["compil_pack_fichier"][0].set(p)
                    self.vars["compil_pack_coupe"][0].set(str(self.cfg["compil_pack_coupe"]))
                    self.trans_msg.configure(text=f"Trouvé : {os.path.basename(p)}", fg=C["good"])
                else:
                    self.trans_msg.configure(text="Aucune transition de pack trouvée : choisis le fichier.",
                                             fg=C["warn"])
            self.call(done)
        self.trans_msg.configure(text="Recherche…", fg=C["muted"])
        threading.Thread(target=run, daemon=True).start()

    def _preview_transitions(self):
        import transitions as tr
        new = self._read_settings()
        ids = [t for t in new.get("compil_trans_lib", []) if t in tr.BY_ID]
        if "pack" in ids and not os.path.isfile(new.get("compil_pack_fichier") or ""):
            ids.remove("pack")
        if not ids:
            self.trans_msg.configure(text="Coche au moins une transition.", fg=C["warn"])
            return
        out = os.path.join(self.cfg.sub("compils"), "Apercu_transitions.mp4")
        self.trans_msg.configure(text=f"Fabrication de l'aperçu ({len(ids)} transitions)…", fg=C["accent"])

        def run():
            try:
                tr.catalogue(out, {"style": new.get("compil_style", "spectre"),
                                   "pack_file": new.get("compil_pack_fichier", ""),
                                   "pack_cut": new.get("compil_pack_coupe", 0)}, ids)
                self.call(lambda: (self.trans_msg.configure(text="Aperçu prêt.", fg=C["good"]), open_path(out)))
            except Exception as e:
                msg = f"Aperçu impossible : {str(e)[:120]}"
                self.call(lambda: self.trans_msg.configure(text=msg, fg=C["bad"]))
        threading.Thread(target=run, daemon=True).start()

    def _read_settings(self):
        out = {}
        for key, (var, kind) in self.vars.items():
            raw = var.get() if kind != "lib" else None
            if kind == "lib":
                out[key] = [tid for tid, bv in var.items() if bv.get()]
                continue
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
                if kind in ("clip", "compil", "erreur", "session"):
                    self.tray.notify(text)
                if kind in ("clip", "kill") and self.current == "Clips":
                    self.refresh_clips()
                if kind == "session" and self.current == "Montage":
                    self.refresh_montage()
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
        if getattr(self, "clip_player", None):
            self.clip_player.stop()
        if not self.told_tray:
            self.told_tray = True
            self.tray.notify("HexaClip continue dans la barre des tâches. Clic droit sur l'icône pour quitter.")

    def quit(self):
        self.tray.stop()
        self.engine.shutdown()
        if self.updater.state == "ready":       # comme HexaRise : installée en quittant
            self.updater.install(restart=False)
        self.root.destroy()

    # ------------------------------------------------------------- mises à jour
    def _auto_update_tick(self):
        if self.cfg["maj_auto"] and self.updater.state != "ready":
            threading.Thread(target=self.updater.check, daemon=True).start()
        self.root.after(4 * 3600 * 1000, self._auto_update_tick)

    def check_update(self):
        self.upd_msg.configure(text="Recherche de mise à jour…", fg=C["muted"])
        threading.Thread(target=self.updater.check, daemon=True).start()

    def _on_update(self):
        u = self.updater
        col = {"ready": "accent", "error": "warn", "uptodate": "good"}.get(u.state, "muted")
        self.upd_msg.configure(text=u.message, fg=C[col])
        if u.state == "ready":
            self.upd_btn.configure(text=f"Mise à jour {u.version} · Redémarrer")
            self.upd_btn.pack(side="right", padx=12)
            self.upd_install.pack(side="left", padx=8, before=self.upd_msg)
            self.tray.notify(f"Mise à jour {u.version} prête : clique sur Redémarrer dans HexaClip.")
        else:
            self.upd_btn.pack_forget()
            self.upd_install.pack_forget()

    def restart_update(self):
        from tkinter import messagebox
        if self.updater.state != "ready":
            return
        if self.engine.recorder.running and not messagebox.askyesno(
                APP, "CS2 est en cours d'enregistrement. Redémarrer HexaClip maintenant pour installer la mise à jour ?"):
            return
        self.tray.stop()
        self.engine.shutdown()
        if self.updater.install(restart=True):
            self.root.destroy()
            return
        messagebox.showerror(APP, self.updater.message)
        self.engine.start()
        self.tray = Tray(self)

    def run(self):
        self.root.mainloop()


# ----------------------------------------------------------------------------
# Lancement
# ----------------------------------------------------------------------------
def _single_instance(wait_s=0):
    """Une seule HexaClip à la fois. wait_s : attendre que l'ancienne version se ferme (après une MàJ)."""
    if not IS_WIN:
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        end = time.time() + wait_s
        while True:
            h = k32.CreateMutexW(None, False, "Local\\HexaLabs.HexaClip")
            if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
                globals()["_MUTEX"] = h
                return True
            k32.CloseHandle(h)
            if time.time() > end:
                return False
            time.sleep(0.5)
    except Exception:
        return True


def main():
    global FONT
    ap = argparse.ArgumentParser(prog="HexaClip")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", help="fichier de sortie pour --selftest (l'exe n'a pas de console)")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("--discret", action="store_true", help="démarrer caché dans la barre des tâches")
    ap.add_argument("--apres-maj", action="store_true", help="relancé par la mise à jour")
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
                            "gsi_cs2", "moments", "montage", "recorder", "selftest", "updater", "brand", "intro", "spectre", "transitions") + \
                        (("pyaudiowpatch",) if IS_WIN else ()):
                    try:
                        m = importlib.import_module(mod)
                        print(f"module {mod} : OK {getattr(m, '__version__', '')}".rstrip())
                    except Exception as e:
                        print(f"module {mod} : ERREUR {e}")
                        ok = False
                for f in ("assets/hexaclip.ico", "assets/app_64.png", "assets/symbole_36.png",
                          "assets/filigrane.png", "assets/SpaceGrotesk-Bold.ttf", "assets/coupe_haut.png",
                          "assets/coupe_bas.png", "assets/Audiowide-Regular.ttf", "assets/Rajdhani-Bold.ttf"):
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
    if not _single_instance(wait_s=20 if args.apres_maj else 0):
        ctypes.windll.user32.MessageBoxW(None, "HexaClip est déjà ouvert (regarde dans la barre des tâches, "
                                               "près de l'horloge).", APP, 0x40)
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HexaLabs.HexaClip")
    except Exception:
        pass
    FONT = paths.load_brand_font() or FONT
    from engine import Engine, log_error, LOG_PATH
    import updater
    threading.Thread(target=updater.cleanup_after_update, daemon=True).start()
    try:
        engine = Engine()
        engine.start()
        if args.apres_maj:
            engine.emit("info", f"HexaClip est à jour : version {VERSION}.")
        App(engine, hidden=args.discret).run()
    except Exception:
        log_error("Démarrage")
        try:
            ctypes.windll.user32.MessageBoxW(None, f"Erreur au démarrage, détails dans :\n{LOG_PATH}", APP, 0x10)
        except Exception:
            pass


if __name__ == "__main__":
    main()
