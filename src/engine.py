# -*- coding: utf-8 -*-
"""
Le moteur de HexaClip (sans interface) : surveille CS2, lance l'enregistrement, transforme les
actions détectées en clips, monte les compils et suit Faceit. L'interface lit status() et la file
d'événements.
"""
from __future__ import annotations

import datetime as dt
import os
import queue
import re
import sys
import threading
import time
import traceback

import ffmpeg_tools as ff
import montage
import paths
from config import Config
from db import DB
from faceit import Faceit, FaceitError, outro_lines
from gsi_cs2 import GSIServer, cfg_installed, install_cfg, pretty_map
from moments import (KillTracker, LABELS, auto_choice, build_item, choice_duration, manual_moment,
                     merge)
from recorder import Recorder, detect_method

IS_WIN = sys.platform == "win32"
CS2_EXE = "cs2.exe"
LOG_PATH = paths.data("journal.txt")


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 2_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".ancien")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if sys.stdout:
        try:
            print(line)
        except Exception:
            pass


def log_error(where):
    log(f"ERREUR {where} :\n{traceback.format_exc()}")


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "", s.replace(" ", "_"))[:40]


def _quiet(fn, *a):
    try:
        fn(*a)
    except Exception:
        log_error("téléchargement en fond")


def make_thumb(video, t, png):
    """Petite image du round (au dernier kill) pour l'onglet Montage."""
    try:
        os.makedirs(os.path.dirname(png), exist_ok=True)
        ff.run([ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, t):.3f}",
                "-i", video, "-frames:v", "1", "-vf", "scale=192:108:force_original_aspect_ratio=decrease,"
                "pad=192:108:-1:-1", png], timeout=30)
        return png if os.path.isfile(png) else None
    except Exception:
        return None


def remove_files(m):
    for k in ("fichier", "miniature"):
        p = m.get(k)
        try:
            if p and os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


class Engine:
    def __init__(self, cfg: Config | None = None, db_path=None):
        self.cfg = cfg or Config()
        self.db = DB(db_path)
        self.events = queue.Queue()          # (type, texte) pour l'interface
        self.tracker = KillTracker(self._on_moment, int(self.cfg["kills_min"]))
        self.gsi = GSIServer(self._on_gsi)
        self.recorder = Recorder(os.path.join(self.cfg.video_dir(), ".tampon"), int(self.cfg["tampon_s"]), log=log)
        self.faceit = Faceit(self.cfg["faceit_cle_api"], self.cfg["faceit_pseudo"], self.db, log=log)
        self.method = None                   # méthode de capture validée sur ce PC
        self.method_error = ""
        self.cs2_running = False
        self.last_gsi = 0.0                  # dernier message de CS2 (preuve que le jeu tourne)
        self.session_id = None
        self.session_start = None
        self.session_moments = 0
        self.busy = ""                       # tâche longue en cours (montage, téléchargement…)
        self._stop = threading.Event()
        self._hotkey = None
        self._compile_lock = threading.Lock()

    # ------------------------------------------------------------------ cycle de vie
    def start(self):
        if not self.gsi.start():
            self.emit("erreur", f"Liaison CS2 impossible : {self.gsi.error}")
        threading.Thread(target=self._prepare, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        self._start_hotkey()

    def shutdown(self):
        self._stop.set()
        self.recorder.stop(wait_pending=10)
        self.gsi.stop()

    def emit(self, kind, text):
        log(f"[{kind}] {text}")
        self.events.put((kind, text))

    def _prepare(self):
        """FFmpeg présent ? méthode de capture ? liaison CS2 ? ménage des vieux clips."""
        try:
            if not ff.available():
                self.busy = "Téléchargement du moteur vidéo (une seule fois)…"
                ff.download(lambda f, t: setattr(self, "busy", t))
            self.busy = ""
            if IS_WIN and not ff.find("ffplay"):
                # installé par la 0.1 sans le lecteur d'aperçu : on le complète en fond
                threading.Thread(target=lambda: _quiet(ff.download), daemon=True).start()
        except Exception as e:
            self.busy = ""
            self.method_error = str(e)
            self.emit("erreur", str(e))
            return
        if not cfg_installed():
            ok, msg = install_cfg()
            self.emit("info" if ok else "erreur", msg)
        self.recorder._kill_orphan()
        self.detect_pack()
        self.detect_capture()
        self.cleanup_old()
        if self.faceit.configured():
            self.refresh_faceit()

    def detect_pack(self, force=False):
        """Trouve la transition de ton pack Twitch (stinger) pour la mettre après l'intro."""
        cur = self.cfg["compil_pack_fichier"]
        try:
            import transitions
            path, cut = transitions.find_pack()
        except Exception:
            log_error("recherche du pack")
            return cur if cur and os.path.isfile(cur) else None
        if cur and os.path.isfile(cur) and not force:
            # garder le fichier choisi, sauf si un pack plus récent est apparu (pack mis à jour)
            try:
                newer = bool(path) and path != cur and os.path.getmtime(path) > os.path.getmtime(cur)
            except OSError:
                newer = False
            if not newer:
                return cur
        if path:
            self.cfg["compil_pack_fichier"] = path
            if cut:
                self.cfg["compil_pack_coupe"] = round(float(cut), 3)
            self.cfg.save()
            self.emit("info", f"Transition de ton pack trouvée : {os.path.basename(path)} "
                              f"(coupe à {self.cfg['compil_pack_coupe'] or 'mi-parcours'} s), utilisée après l'intro.")
        return path

    def detect_capture(self):
        self.busy = "Test de la capture sur ce PC…"
        try:
            m = detect_method(self.cfg, log=log)
        except Exception as e:
            m = None
            log_error("détection de la capture")
            self.method_error = str(e)
        self.busy = ""
        if m:
            m["qualite"] = int(self.cfg["qualite"])
            m["ecran"] = int(self.cfg["ecran"])
            self.method, self.method_error = m, ""
            if self.cs2_running and self.cfg["capture_auto"] and not self.recorder.running:
                self.start_recording()
        else:
            self.method = None
            self.method_error = "Aucune méthode de capture ne marche sur ce PC (voir journal.txt)."
            self.emit("erreur", self.method_error)
        return m

    # ------------------------------------------------------------------ CS2
    def _cs2_alive(self):
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                if (p.info.get("name") or "").lower() == CS2_EXE:
                    return True
        except Exception:
            pass
        return False

    def _watch(self):
        restarts = 0
        last_faceit = 0.0
        while not self._stop.is_set():
            try:
                # CS2 envoie la partie en direct : s'il parle, il tourne (même si la liste des processus rate)
                alive = self._cs2_alive() or (time.time() - self.last_gsi < 20)
                if alive and not self.cs2_running:
                    self.cs2_running = True
                    self._session_begin()
                elif not alive and self.cs2_running:
                    self.cs2_running = False
                    self._session_end()
                # FFmpeg tombé pendant que CS2 tourne : on relance (3 fois max)
                if self.cs2_running and self.method and self.cfg["capture_auto"] and not self.recorder.running:
                    if restarts < 3:
                        restarts += 1
                        log("Capture arrêtée, relance. " + self.recorder.error_tail()[-400:])
                        self.start_recording()
                elif self.recorder.running:
                    restarts = 0
                self.tracker.tick()
                # relevé Faceit : toutes les 4 min pendant le jeu, toutes les 30 min sinon
                gap = 240 if self.cs2_running else 1800
                if self.faceit.configured() and time.time() - last_faceit > gap:
                    last_faceit = time.time()
                    threading.Thread(target=self.refresh_faceit, daemon=True).start()
            except Exception:
                log_error("surveillance de CS2")
            self._stop.wait(2.0)

    def _session_begin(self):
        self.session_start = time.time()
        self.session_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session_moments = 0
        self.emit("info", "CS2 détecté.")
        if self.cfg["capture_auto"] and self.method:
            self.start_recording()

    def _session_end(self):
        self.emit("info", "CS2 fermé.")
        self.recorder.stop(wait_pending=15)
        sid, start = self.session_id, self.session_start
        if self.session_moments <= 0:
            return
        if self.cfg["compil_auto"]:
            threading.Thread(target=self._auto_compile, args=(sid, start), daemon=True).start()
        else:
            ms = self.db.moments(session=sid, limit=1000)
            kills = sum(m.get("kills") or 0 for m in ms)
            self.emit("session", f"Session terminée : {kills} kills gardés sur {len(ms)} rounds. "
                                 "Choisis tes kills dans l'onglet Montage.")

    def _auto_compile(self, sid, start):
        # laisser à Faceit le temps de mettre l'Elo à jour
        if self.faceit.configured():
            time.sleep(45)
            self.refresh_faceit()
        self.compile(session=sid, since=start)

    def start_recording(self):
        if not self.method:
            return False
        self.recorder.buf_dir = os.path.join(self.cfg.video_dir(), ".tampon")
        self.recorder.buffer_s = int(self.cfg["tampon_s"])
        try:
            self.recorder.start(self.method, audio=bool(self.cfg["son"]),
                                audio_offset_ms=int(self.cfg["decalage_son_ms"]))
            b = int(self.cfg["tampon_s"])
            self.emit("info", "Enregistrement en cours (tampon de "
                              + (f"{b // 60} min" if b >= 60 else f"{b} s") + ", rien n'est gardé sans action).")
            return True
        except Exception as e:
            log_error("démarrage de la capture")
            self.emit("erreur", f"Capture impossible : {e}")
            return False

    def stop_recording(self):
        self.recorder.stop()

    # ------------------------------------------------------------------ actions
    def _on_gsi(self, payload, t):
        self.last_gsi = time.time()
        self.tracker.min_kills = int(self.cfg["kills_min"])
        self.tracker.feed(payload, t)

    def _on_moment(self, m):
        if not self.recorder.running:
            log(f"Action {m['type']} non enregistrée : capture arrêtée.")
            return
        pre, post = float(self.cfg["avant_s"]), float(self.cfg["apres_s"])
        if m.get("fenetre_abs"):
            per = [list(m["fenetre_abs"])]                     # clip manuel : un seul passage
        else:
            per = [[k - pre, k + post] for k in m["kills_abs"]]   # un passage par kill
        keep = merge([[a - 0.6, b + 0.6] for a, b in per])
        day = dt.datetime.fromtimestamp(m["cree"]).strftime("%Y-%m-%d")
        stem = (dt.datetime.fromtimestamp(m["cree"]).strftime("%Hh%M_%S") + "_" + safe_name(m.get("carte") or "CS2")
                + "_" + m["type"])
        folder = self.cfg.sub(os.path.join("clips", day))
        out = os.path.join(folder, stem + ".mp4")
        session = self.session_id

        def done(path, to_file, err):
            if not path:
                self.emit("erreur", f"Clip {m['type']} perdu : {err}")
                return
            row = dict(m)
            row["fichier"] = path
            row["session"] = session
            row["coupes"] = [[to_file(a), to_file(b)] for a, b in per]
            row["kills_t"] = [to_file(k) for k in m.get("kills_abs", [])]
            row["son"] = 1 if self.recorder.has_audio else 0
            t_img = row["kills_t"][-1] if row["kills_t"] else (row["coupes"][0][0] + row["coupes"][0][1]) / 2
            row["miniature"] = make_thumb(path, t_img, os.path.join(folder, ".miniatures", stem + ".png"))
            self.db.add_moment(row)
            self.session_moments += 1
            where = f" sur {m['carte']}" if m.get("carte") else ""
            if m["type"] in ("3k", "4k", "ace", "manuel"):
                self.emit("clip", f"{LABELS.get(m['type'], m['type'])}{where} enregistré.")
            else:
                self.events.put(("kill", f"{LABELS.get(m['type'], m['type'])}{where} gardé."))

        self.recorder.save(keep, out, done)

    def manual_clip(self):
        if not self.recorder.running:
            self.emit("erreur", "Rien à sauvegarder : l'enregistrement ne tourne pas.")
            return
        m = manual_moment(time.time(), float(self.cfg["duree_manuelle_s"]),
                          carte=pretty_map(self.tracker.map),
                          round_no=self.tracker.round_no, partie=self.tracker.partie)
        self._on_moment(m)

    def _start_hotkey(self):
        if not IS_WIN or not self.cfg["touche_manuelle"]:
            return
        key = str(self.cfg["touche_manuelle"]).upper()
        m = re.fullmatch(r"F([1-9]|1[0-2])", key)
        if not m:
            return
        vk = 0x6F + int(m.group(1))   # F1 = 0x70

        def loop():
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            if not user32.RegisterHotKey(None, 1, 0x4000, vk):   # MOD_NOREPEAT
                self.emit("erreur", f"La touche {key} est déjà prise par un autre programme.")
                return
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == 0x0312:   # WM_HOTKEY
                    self.manual_clip()
        self._hotkey = threading.Thread(target=loop, daemon=True)
        self._hotkey.start()

    # ------------------------------------------------------------------ compils
    def effective_choice(self, ms, budget):
        """Kills retenus : ton choix quand tu en as fait un, sinon la sélection automatique."""
        fixed = {m["id"]: m["choix"] for m in ms if m.get("choix") is not None}
        used = sum(choice_duration(m, fixed[m["id"]]) for m in ms if m["id"] in fixed)
        auto = auto_choice([m for m in ms if m["id"] not in fixed], max(0.0, budget - used))
        return {**auto, **fixed}

    def compile_budget(self, fin=True):
        """Durée laissée aux kills : durée de la compil moins l'intro et l'écran de fin."""
        th = montage.theme_module(self.cfg["compil_style"])
        b = float(self.cfg["compil_duree_s"])
        if self.cfg["compil_intro"]:
            b -= getattr(th, "INTRO_S", montage.INTRO_S)
        if fin or self.cfg["pseudo"].strip() or self.cfg["compil_appel"].strip():
            b -= th.OUTRO_S
        return max(10.0, b)

    def compile(self, session=None, since=None, ids=None, selection=None):
        """Monte une compil. selection = {id_action: [indices des kills]} (onglet Montage) ; sinon les kills
        choisis (ou automatiques) de la session / période. Renvoie le chemin ou None."""
        if not self._compile_lock.acquire(blocking=False):
            self.emit("erreur", "Une compil est déjà en cours.")
            return None
        try:
            fin = None
            if self.cfg["compil_fin_faceit"] and self.faceit.configured():
                try:
                    fin = outro_lines(self.faceit.session_summary(since or self.session_start or time.time()))
                except Exception:
                    log_error("résumé Faceit")
            if selection is not None:
                ms = self.db.moments_by_ids(selection.keys())
                choice = {int(k): v for k, v in selection.items()}
            else:
                ms = self.db.moments_by_ids(ids) if ids else self.db.moments(since=since, session=session, limit=400)
                choice = self.effective_choice(ms, self.compile_budget(bool(fin)))
            items = [build_item(m, choice.get(m["id"], [])) for m in ms
                     if choice.get(m["id"]) and m.get("fichier") and os.path.isfile(m["fichier"])]
            items = [it for it in items if it["coupes"]]
            if not items:
                self.emit("info", "Aucun kill choisi (ou fichiers introuvables) : rien à monter.")
                return None
            order = self.cfg["compil_ordre"]
            if order == "chrono":
                items.sort(key=lambda m: m["cree"])
            elif order == "meilleure_d_abord":
                items.sort(key=lambda m: (m.get("favori", 0), m.get("score", 0)), reverse=True)
            else:
                items.sort(key=lambda m: (m.get("favori", 0), m.get("score", 0)))
            enc = (self.method or {}).get("encodeur", "libx264")
            opts = {
                "cadrage": self.cfg["compil_cadrage"], "badges": self.cfg["compil_badges"],
                "texte": self.cfg["compil_texte"].strip(),
                "logo": self.cfg["compil_logo"] if os.path.isfile(self.cfg["compil_logo"] or "") else None,
                "fin": fin, "encodeur": enc,
                "pseudo": self.cfg["pseudo"].strip(), "transitions": bool(self.cfg["compil_transitions"]),
                "style": self.cfg["compil_style"], "trans_actives": list(self.cfg["compil_trans_lib"]),
                "trans_ouverture": self.cfg["compil_trans_ouverture"], "pack_file": self.cfg["compil_pack_fichier"],
                "pack_cut": float(self.cfg["compil_pack_coupe"] or 0),
                "pack_cadrage": self.cfg["compil_pack_cadrage"],
                "appel": self.cfg["compil_appel"].strip(),
                "musique": montage.pick_music(self.cfg["musique_dossier"]) if self.cfg["musique_on"] else None,
                "musique_volume": float(self.cfg["musique_volume"]),
            }
            n_kills = sum(len(it.get("kills_t") or []) for it in items)
            if self.cfg["compil_intro"]:
                import intro
                opts["intro"] = intro.hook(items)
            self.busy = f"Montage de la compil ({n_kills} kills)…"
            out, dur = montage.make(items, self.cfg.sub("compils"), opts, log=log)
            self.db.add_compil(out, dur, [m["id"] for m in items], session)
            self.emit("compil", f"Compil prête : {os.path.basename(out)} ({dur:.0f} s)")
            return out
        except Exception as e:
            log_error("montage")
            self.emit("erreur", f"Montage impossible : {str(e)[:300]}")
            return None
        finally:
            self.busy = ""
            self._compile_lock.release()

    # ------------------------------------------------------------------ Faceit
    def reload_faceit(self):
        self.faceit = Faceit(self.cfg["faceit_cle_api"], self.cfg["faceit_pseudo"], self.db, log=log)

    def refresh_faceit(self):
        try:
            prof = self.faceit.refresh()
            self.events.put(("faceit", f"Faceit : {prof['elo']} Elo, niveau {prof['niveau']}"))
            return prof
        except FaceitError as e:
            self.faceit.last_error = str(e)
            log(f"Faceit : {e}")
        except Exception as e:
            self.faceit.last_error = f"Erreur Faceit : {e}"
            log_error("Faceit")
        return None

    # ------------------------------------------------------------------ ménage
    def cleanup_old(self):
        days = int(self.cfg["garder_clips_jours"] or 0)
        if days <= 0:
            return
        limit = time.time() - days * 86400
        n = 0
        for m in self.db.moments(limit=5000):
            if m["cree"] < limit and not m.get("favori"):
                remove_files(m)
                self.db.delete_moment(m["id"])
                n += 1
        if n:
            log(f"Ménage : {n} clips de plus de {days} jours supprimés (les favoris sont gardés).")

    def delete_moment(self, mid):
        for m in self.db.moments_by_ids([mid]):
            remove_files(m)
        self.db.delete_moment(mid)

    # ------------------------------------------------------------------ état
    def status(self):
        return {
            "cs2": self.cs2_running,
            "gsi": self.gsi.alive(),
            "rec": self.recorder.running,
            "son": self.recorder.has_audio,
            "son_nom": self.recorder.audio_name,
            "son_silence_s": self.recorder.audio_silent_for(),
            "method": self.method,
            "method_error": self.method_error,
            "busy": self.busy,
            "carte": self.tracker.map,
            "round": self.tracker.round_no,
            "kills_round": len(self.tracker.kills),
            "session_moments": self.session_moments,
            "faceit": self.faceit.profile,
            "faceit_error": self.faceit.last_error,
            "ffmpeg": ff.available(),
            "cfg_cs2": cfg_installed(),
        }


# ----------------------------------------------------------------------------
# Lancement avec Windows
# ----------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(on: bool):
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                exe = sys.executable if getattr(sys, "frozen", False) else \
                    f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
                cmd = f'"{exe}" --discret' if getattr(sys, "frozen", False) else f"{exe} --discret"
                winreg.SetValueEx(k, paths.APP, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, paths.APP)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
