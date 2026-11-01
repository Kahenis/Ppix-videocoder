"""« Parcourir local… » : conversion d'un fichier choisi sur le disque, hors Plex.

Assistant en 3 étapes dans une seule fenêtre :
  1. Vidéo : H.265 ou H.264
  2. Audio : format (conserver, AAC, AC3, E-AC3, MP3) et canaux (conserver, 5.1, stéréo)
  3. Destination : par défaut le fichier d'origine (remplacement sécurisé)
puis le fichier entre dans la file et la conversion démarre.
"""
from __future__ import annotations

import os
import threading
from pathlib import PurePath
from tkinter import filedialog
from typing import Any, Dict

import customtkinter as ctk

from .encoder import LOCAL_AUDIO_BITRATES, is_hdr_stream
from .jobs import container_for_path, default_local_destination, make_local_job
from .logger import log
from .theme import ACCENT, ACCENT_HOVER, BG, CARD, MUTED, PANEL, TEXT, WARN

VIDEO_TYPES = [
    ("Vidéos", "*.mkv *.mp4 *.m4v *.avi *.mov *.wmv *.asf *.mpg *.mpeg *.vob *.ts *.m2ts *.mts "
               "*.flv *.webm *.divx *.3gp *.ogm *.rm *.rmvb"),
    ("Tous les fichiers", "*.*"),
]
AUDIO_CHOICES = [
    ("copy", "Conserver tel quel"),
    ("aac", "AAC (le plus compatible)"),
    ("ac3", "AC3 — Dolby Digital"),
    ("eac3", "E-AC3 — Dolby Digital Plus"),
    ("mp3", "MP3"),
]
LAYOUT_CHOICES = [("keep", "Conserver les canaux"), ("5.1", "5.1"), ("stereo", "Stéréo")]


def _fmt_size(n: float) -> str:
    n = float(n or 0)
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} Go"
    return f"{n / 1024 ** 2:.0f} Mo"


def _fmt_dur(s: float) -> str:
    s = int(s or 0)
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}h{m:02d}" if h else f"{m} min {sec:02d} s"


def describe_source(info: Dict[str, Any], path: str, encoder) -> str:
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), {})
    parts = []
    if v:
        hdr = " HDR" if is_hdr_stream(v) else ""
        parts.append(f"{v.get('codec_name', '?')} {v.get('width', '?')}x{v.get('height', '?')}{hdr}")
    for a in (s for s in streams if s.get("codec_type") == "audio"):
        lang = (a.get("tags") or {}).get("language", "")
        ch = a.get("channels") or "?"
        layout = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(ch, f"{ch}ch")
        parts.append(f"audio {a.get('codec_name', '?')} {layout}" + (f" ({lang})" if lang else ""))
    n_subs = sum(1 for s in streams if s.get("codec_type") == "subtitle")
    if n_subs:
        parts.append(f"{n_subs} sous-titre(s)")
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    parts.append(_fmt_size(size))
    parts.append(_fmt_dur(encoder.probe_duration(info)))
    return " · ".join(parts)


def ask_local_file(app) -> None:
    """Ouvre la fenêtre Windows de sélection, puis l'assistant."""
    initial = app.settings.get("last_local_dir") or os.path.expanduser("~")
    path = filedialog.askopenfilename(parent=app, title="Choisir une vidéo à convertir",
                                      initialdir=initial, filetypes=VIDEO_TYPES)
    if not path:
        return
    path = os.path.normpath(path)
    app.settings["last_local_dir"] = os.path.dirname(path)
    LocalConvertDialog(app, path)


class LocalConvertDialog:
    def __init__(self, app, path: str):
        self.app = app
        self.path = path
        self.info: Dict[str, Any] = {}
        self.codec = app.settings.get("preferred_codec", "hevc")
        self.audio_target = "copy"
        self.audio_layout = "keep"
        self.dest = default_local_destination(path)
        self.delete_original = self._same_stem_same_folder(self.dest)
        self.step = 0
        self.hdr = False
        w = self.win = ctk.CTkToplevel(app)
        w.title("Convertir un fichier local")
        w.geometry("660x560")
        w.minsize(620, 540)
        w.configure(fg_color=BG)
        w.transient(app)
        w.after(150, w.lift)
        ctk.CTkLabel(w, text=PurePath(path.replace("\\", "/")).name, font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=ACCENT, wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(14, 2))
        self.lbl_info = ctk.CTkLabel(w, text="Analyse du fichier…", text_color=MUTED, wraplength=600,
                                     justify="left")
        self.lbl_info.pack(anchor="w", padx=16)
        self.lbl_step = ctk.CTkLabel(w, text="", text_color=MUTED)
        self.lbl_step.pack(anchor="w", padx=16, pady=(10, 0))
        self.body = ctk.CTkFrame(w, fg_color=PANEL, corner_radius=10)
        self.body.pack(fill="both", expand=True, padx=12, pady=8)
        nav = ctk.CTkFrame(w, fg_color=BG)
        nav.pack(fill="x", padx=12, pady=(0, 12))
        self.btn_back = ctk.CTkButton(nav, text="← Retour", width=100, fg_color="#333", command=self.back)
        self.btn_back.pack(side="left")
        ctk.CTkButton(nav, text="Annuler", width=100, fg_color="#333", command=self.close).pack(side="left", padx=8)
        self.btn_next = ctk.CTkButton(nav, text="Suivant →", width=180, fg_color=ACCENT, text_color="#111",
                                      hover_color=ACCENT_HOVER, command=self.next, state="disabled")
        self.btn_next.pack(side="right")
        w.protocol("WM_DELETE_WINDOW", self.close)
        app._local_dialog = self
        threading.Thread(target=self._probe, daemon=True).start()
        self.show_step(0)

    # ------------------------------------------------------------------
    def _probe(self):
        info = self.app.encoder.probe(self.path)

        def apply():
            if not self.win.winfo_exists():
                return
            self.info = info
            if not info or not any(s.get("codec_type") == "video" for s in info.get("streams") or []):
                self.lbl_info.configure(text="Ce fichier n'est pas une vidéo lisible par FFmpeg.", text_color=WARN)
                return
            v = next(s for s in info["streams"] if s.get("codec_type") == "video")
            self.hdr = is_hdr_stream(v)
            if self.hdr:
                self.codec = "hevc"
            self.lbl_info.configure(text=describe_source(info, self.path, self.app.encoder), text_color=TEXT)
            self.btn_next.configure(state="normal")
            self.show_step(self.step)
        self.app._ui(apply)

    def _same_stem_same_folder(self, dest: str) -> bool:
        a, b = PurePath(self.path.replace("\\", "/")), PurePath(dest.replace("\\", "/"))
        return (a != b and os.path.normcase(str(a.parent)) == os.path.normcase(str(b.parent))
                and a.stem.lower() == b.stem.lower())

    def _is_same_file(self, dest: str) -> bool:
        return os.path.normcase(os.path.abspath(dest)) == os.path.normcase(os.path.abspath(self.path))

    # ------------------------------------------------------------------
    def show_step(self, step: int):
        self.step = step
        for c in self.body.winfo_children():
            c.destroy()
        self.lbl_step.configure(text=f"Étape {step + 1} / 3")
        self.btn_back.configure(state="normal" if step > 0 else "disabled")
        self.btn_next.configure(text="Lancer la conversion" if step == 2 else "Suivant →")
        [self._step_video, self._step_audio, self._step_dest][step]()

    def _choice_buttons(self, parent, choices, current, on_pick, width=260):
        btns = {}

        def paint():
            for k, b in btns.items():
                sel = k == current["v"]
                b.configure(fg_color=ACCENT if sel else "#333", text_color="#111" if sel else TEXT)

        for key, label in choices:
            def pick(k=key):
                current["v"] = k
                on_pick(k)
                paint()
            b = ctk.CTkButton(parent, text=label, width=width, height=34, command=pick)
            b.pack(anchor="w", padx=16, pady=3)
            btns[key] = b
        paint()
        return btns

    def _step_video(self):
        ctk.CTkLabel(self.body, text="Convertir la vidéo en :", text_color=TEXT,
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(12, 6))
        cur = {"v": self.codec}
        self.video_buttons = self._choice_buttons(
            self.body, [("hevc", "H.265 (HEVC) — fichier plus léger, recommandé"),
                        ("h264", "H.264 (AVC) — compatible avec tout")],
            cur, lambda k: setattr(self, "codec", k), width=420)
        if self.hdr:
            self.video_buttons["h264"].configure(state="disabled")
            ctk.CTkLabel(self.body, text="Source HDR : H.265 obligatoire (le H.264 donnerait une image trop sombre).",
                         text_color=WARN, wraplength=560, justify="left").pack(anchor="w", padx=16, pady=6)

    def _step_audio(self):
        ctk.CTkLabel(self.body, text="Son (appliqué à chaque piste audio) :", text_color=TEXT,
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(12, 6))
        row = ctk.CTkFrame(self.body, fg_color=PANEL)
        row.pack(fill="x")
        left = ctk.CTkFrame(row, fg_color=PANEL)
        left.pack(side="left", anchor="n")
        right = ctk.CTkFrame(row, fg_color=PANEL)
        right.pack(side="left", anchor="n", padx=(12, 0))
        ctk.CTkLabel(left, text="Format", text_color=MUTED).pack(anchor="w", padx=16)
        ctk.CTkLabel(right, text="Canaux", text_color=MUTED).pack(anchor="w", padx=16)
        cur_lay = {"v": self.audio_layout}
        self.rate_label = ctk.CTkLabel(self.body, text="", text_color=MUTED, wraplength=580, justify="left")

        def refresh():
            copy = self.audio_target == "copy"
            for k, b in self.layout_buttons.items():
                b.configure(state="disabled" if copy and k != "keep" else "normal")
            if copy and self.audio_layout != "keep":
                self.audio_layout = cur_lay["v"] = "keep"
                for k, b in self.layout_buttons.items():
                    b.configure(fg_color=ACCENT if k == "keep" else "#333",
                                text_color="#111" if k == "keep" else TEXT)
            if self.audio_target == "mp3":
                self.layout_buttons["5.1"].configure(state="disabled")
                if self.audio_layout == "5.1":
                    self.audio_layout = cur_lay["v"] = "stereo"
                    for k, b in self.layout_buttons.items():
                        b.configure(fg_color=ACCENT if k == "stereo" else "#333",
                                    text_color="#111" if k == "stereo" else TEXT)
            self.rate_label.configure(text=self._audio_summary())

        def pick_fmt(k):
            self.audio_target = k
            refresh()

        def pick_lay(k):
            self.audio_layout = k
            refresh()

        self.audio_buttons = self._choice_buttons(left, AUDIO_CHOICES, {"v": self.audio_target}, pick_fmt)
        self.layout_buttons = self._choice_buttons(right, LAYOUT_CHOICES, cur_lay, pick_lay, width=200)
        self.rate_label.pack(anchor="w", padx=16, pady=(10, 6))
        refresh()

    def _audio_summary(self) -> str:
        t = self.audio_target
        if t == "copy":
            return ("Pistes audio copiées sans perte. En MP4, une piste DTS/TrueHD/PCM sera convertie en AAC "
                    "(format non accepté par le MP4).")
        st, s51 = LOCAL_AUDIO_BITRATES[t]
        name = dict(AUDIO_CHOICES)[t].split(" ")[0]
        if t == "mp3":
            return f"{name} : {st} kb/s, stéréo au maximum."
        txt = f"{name} : {st} kb/s en stéréo, {s51} kb/s en 5.1."
        if t in ("ac3", "eac3"):
            txt += " Un 7.1 est réduit en 5.1."
        if self.audio_layout == "5.1":
            txt += " Une source stéréo reste stéréo (pas de faux 5.1)."
        return txt

    def _step_dest(self):
        ctk.CTkLabel(self.body, text="Enregistrer sous :", text_color=TEXT,
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(12, 6))
        row = ctk.CTkFrame(self.body, fg_color=PANEL)
        row.pack(fill="x", padx=16)
        self.dest_entry = ctk.CTkEntry(row, width=440, fg_color=CARD)
        self.dest_entry.pack(side="left")
        self.dest_entry.insert(0, self.dest)
        self.dest_entry.bind("<KeyRelease>", lambda _e: self._dest_changed(self.dest_entry.get()))
        ctk.CTkButton(row, text="Parcourir…", width=110, fg_color="#333", command=self._browse_dest).pack(
            side="left", padx=8)
        self.del_var = ctk.BooleanVar(master=self.win, value=self.delete_original)
        self.del_box = ctk.CTkCheckBox(self.body, text="", variable=self.del_var, fg_color=ACCENT,
                                       command=lambda: setattr(self, "delete_original", bool(self.del_var.get())))
        self.dest_note = ctk.CTkLabel(self.body, text="", text_color=MUTED, wraplength=580, justify="left")
        self.dest_note.pack(anchor="w", padx=16, pady=(10, 2))
        self._dest_changed(self.dest)

    def _browse_dest(self):
        cur = self.dest_entry.get().strip() or self.dest
        cont = container_for_path(cur)
        path = filedialog.asksaveasfilename(
            parent=self.win, title="Destination du fichier converti",
            initialdir=os.path.dirname(cur), initialfile=os.path.basename(cur),
            defaultextension=f".{cont}",
            filetypes=[("Matroska (MKV)", "*.mkv"), ("MP4", "*.mp4")] if cont == "mkv"
            else [("MP4", "*.mp4"), ("Matroska (MKV)", "*.mkv")])
        if path:
            path = os.path.normpath(path)
            self.dest_entry.delete(0, "end")
            self.dest_entry.insert(0, path)
            self._dest_changed(path)

    def _normalize_dest(self, dest: str) -> str:
        dest = os.path.normpath(dest.strip()) if dest.strip() else ""
        ext = PurePath(dest.replace("\\", "/")).suffix.lower()
        if dest and ext not in (".mkv", ".mp4", ".m4v"):
            dest += ".mkv"   # seuls MKV et MP4 sont produits
        return dest

    def _dest_changed(self, raw: str):
        dest = self._normalize_dest(raw)
        changed = dest != getattr(self, "_last_dest", None)
        self._last_dest = dest
        self.dest = dest
        if not hasattr(self, "dest_note") or not self.dest_note.winfo_exists():
            return
        self.del_box.pack_forget()
        if not dest:
            self.dest_note.configure(text="Indiquez une destination.", text_color=WARN)
            return
        cont = container_for_path(dest).upper()
        if self._is_same_file(dest):
            self.delete_original = False
            self.dest_note.configure(
                text=f"Le fichier d'origine sera remplacé ({cont}), uniquement après vérification du résultat. "
                     "En cas d'erreur, l'original est conservé.", text_color=TEXT)
            return
        same_stem = self._same_stem_same_folder(dest)
        name = PurePath(self.path.replace("\\", "/")).name
        self.del_box.configure(text=f"Supprimer l'original « {name} » après vérification")
        self.del_box.pack(anchor="w", padx=16, pady=4)
        if changed:   # nouvelle destination : même nom dans le même dossier → l'original est remplacé
            self.del_var.set(same_stem)
        self.delete_original = bool(self.del_var.get())
        exists = os.path.exists(dest)
        txt = f"Nouveau fichier {cont}" + (" — un fichier existant de ce nom sera écrasé." if exists else ".")
        self.dest_note.configure(text=txt, text_color=WARN if exists else TEXT)

    # ------------------------------------------------------------------
    def back(self):
        if self.step > 0:
            self.show_step(self.step - 1)

    def next(self):
        if self.step < 2:
            self.show_step(self.step + 1)
            return
        self.launch()

    def launch(self) -> bool:
        if hasattr(self, "dest_entry") and self.dest_entry.winfo_exists():
            self._dest_changed(self.dest_entry.get())
        if not self.dest:
            return False
        parent = os.path.dirname(self.dest)
        if parent and not os.path.isdir(parent):
            self.dest_note.configure(text=f"Dossier introuvable : {parent}", text_color=WARN)
            return False
        job = make_local_job(self.path, self.codec, self.audio_target, self.audio_layout, self.dest,
                             delete_original=self.delete_original and not self._is_same_file(self.dest),
                             info=self.info)
        if not self.app.queue.add(job):
            self.lbl_info.configure(text="Ce fichier est déjà dans la file d'encodage.", text_color=WARN)
            return False
        log(f"Conversion locale : {self.path} → {self.dest} ({self.codec}, audio {self.audio_target}/"
            f"{self.audio_layout}, supprimer l'original={job.delete_original})")
        self.app._log(f"Conversion locale ajoutée : {PurePath(self.path.replace(chr(92), '/')).name}")
        self.close()
        self.app._show_page("queue")
        if not self.app.queue_running:
            self.app._start_queue()
        return True

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        if getattr(self.app, "_local_dialog", None) is self:
            self.app._local_dialog = None

