# -*- coding: utf-8 -*-
"""
Aperçu vidéo intégré (onglet Clips) : le clip sélectionné tourne en boucle, sans le son, directement
dans la fenêtre, avec une frise qui marque chaque kill. « Avec le son » ouvre le lecteur complet.

FFmpeg décode le clip en petites images RVB (400×225, 30 i/s, à vitesse réelle) ; un fil les lit
et l'interface affiche la plus récente toutes les 33 ms.
"""
from __future__ import annotations

import subprocess
import threading
import time

import ffmpeg_tools as ff

W, H, FPS = 400, 225, 30


def frame_cmd(path, frames=None):
    """Commande FFmpeg qui sort le clip en images RVB brutes W×H (en boucle, à vitesse réelle si frames=None)."""
    vf = (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=fast_bilinear,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2")
    cmd = [ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "2", "-hwaccel", "auto"]
    cmd += ["-re", "-stream_loop", "-1"] if frames is None else []
    cmd += ["-i", path, "-an", "-vf", vf]
    cmd += ["-frames:v", str(frames)] if frames else []
    return cmd + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]


class MiniPlayer:
    def __init__(self, tk_mod, parent, colors, font):
        self.tk = tk_mod
        self.C = colors
        self.view = tk_mod.Label(parent, bg="#000000", width=W, height=H, bd=0, cursor="hand2")
        self.bar = tk_mod.Canvas(parent, width=W, height=14, bg=colors["raise"], highlightthickness=0, bd=0)
        self.img = tk_mod.PhotoImage(width=W, height=H)
        self.view.configure(image=self.img)
        self._still = None
        self._proc = None
        self._gen = 0
        self._frame = None          # (n° d'image, octets PPM) la plus récente
        self._shown = -1
        self._dur = 0.0
        self._kills = []
        self._ticking = False
        self._active = False

    # --- commandes -------------------------------------------------------
    def show(self, path, kills_t=(), still=None, duration=None):
        """Affiche la miniature tout de suite puis lance la lecture en boucle de path."""
        self.stop()
        self._gen += 1
        self._dur = max(0.1, float(duration or max(list(kills_t or []) + [1.0]) + 2))
        self._kills = [float(k) for k in (kills_t or []) if k is not None]
        self._draw_bar(0.0)
        self._show_still(still)
        if not path or not ff.find("ffmpeg"):
            return
        gen = self._gen
        self._active = True
        threading.Thread(target=self._decode, args=(path, gen, duration is None), daemon=True).start()
        if not self._ticking:
            self._ticking = True
            self.view.after(33, self._tick)

    def blank(self, text=""):
        self.stop()
        self._gen += 1
        self.img.blank()
        self.bar.delete("all")
        if text:
            self.bar.create_text(W // 2, 7, text=text, fill=self.C["muted"], font=("Segoe UI", 8))

    def stop(self):
        self._active = False
        p, self._proc = self._proc, None
        self._frame, self._shown = None, -1
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass

    # --- interne ---------------------------------------------------------
    def _show_still(self, png):
        self.img.blank()
        if not png:
            return
        try:
            small = self.tk.PhotoImage(file=png)
            z = max(1, min(W // max(1, small.width()), H // max(1, small.height())))
            self._still = small.zoom(z) if z > 1 else small
            self.img.copy(self._still, to=((W - self._still.width()) // 2, (H - self._still.height()) // 2))
        except Exception:
            pass

    def _decode(self, path, gen, probe):
        if probe:
            try:
                d = ff.probe(path).get("duree")
                if d and gen == self._gen:
                    self._dur = float(d)
            except Exception:
                pass
        cmd = frame_cmd(path)
        try:
            p = ff.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except Exception:
            return
        if gen != self._gen:
            p.kill()
            return
        self._proc = p
        size, head, n = W * H * 3, f"P6 {W} {H} 255\n".encode(), 0
        try:
            while gen == self._gen:
                buf = p.stdout.read(size)
                if not buf or len(buf) < size:
                    break
                self._frame = (n, head + buf)
                n += 1
        except Exception:
            pass
        finally:
            if p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass

    def _tick(self):
        fr = self._frame
        if fr and fr[0] != self._shown:
            self._shown = fr[0]
            try:
                self.img.configure(data=fr[1], format="PPM")
            except Exception:
                pass
            self._draw_bar((fr[0] / FPS) % self._dur)
        if self._active:
            self.view.after(33, self._tick)
        else:
            self._ticking = False

    def _draw_bar(self, t):
        c = self.bar
        c.delete("all")
        d = self._dur
        c.create_rectangle(0, 5, W, 9, fill=self.C["line2"], width=0)
        c.create_rectangle(0, 5, int(W * min(1.0, t / d)), 9, fill=self.C["accent_d"], width=0)
        for k in self._kills:
            x = int(W * min(1.0, max(0.0, k / d)))
            c.create_rectangle(x - 1, 1, x + 2, 13, fill=self.C["accent"], width=0)
        x = int(W * min(1.0, t / d))
        c.create_rectangle(x - 1, 0, x + 1, 14, fill=self.C["fg"], width=0)
