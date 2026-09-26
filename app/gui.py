"""Main GUI for Ppix-Videocoder – CustomTkinter."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

import customtkinter as ctk
from tkinter import messagebox, filedialog

from .config import (
    load_settings, save_settings, load_history, save_history,
    SUPPORTED_CODECS, PRESETS, HARDWARE_OPTIONS, APP_NAME, APP_VERSION
)
from .plex_client import PlexClient, VideoInfo
from .encoder import Encoder, EncodeJob


ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1280x800")
        self.minsize(1000, 650)

        self.settings = load_settings()
        self.history = load_history()
        self.plex = PlexClient()
        self.encoder = Encoder()
        self.videos: List[VideoInfo] = []
        self.selected: Dict[str, bool] = {}
        self.codec_choice: Dict[str, str] = {}
        self.queue: List[EncodeJob] = []
        self.queue_running = False
        self.scan_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._try_auto_connect()

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(8, weight=1)

        ctk.CTkLabel(self.sidebar, text="Ppix-Videocoder",
                     font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, padx=20, pady=(20, 10))

        self.btn_connect = ctk.CTkButton(self.sidebar, text="1. Connexion", command=self.show_connect)
        self.btn_connect.grid(row=1, column=0, padx=20, pady=8, sticky="ew")

        self.btn_scan = ctk.CTkButton(self.sidebar, text="2. Scanner", command=self.show_scan, state="disabled")
        self.btn_scan.grid(row=2, column=0, padx=20, pady=8, sticky="ew")

        self.btn_queue = ctk.CTkButton(self.sidebar, text="3. File d'attente", command=self.show_queue, state="disabled")
        self.btn_queue.grid(row=3, column=0, padx=20, pady=8, sticky="ew")

        self.btn_settings = ctk.CTkButton(self.sidebar, text="Paramètres", command=self.show_settings)
        self.btn_settings.grid(row=4, column=0, padx=20, pady=8, sticky="ew")

        self.btn_history = ctk.CTkButton(self.sidebar, text="Historique", command=self.show_history)
        self.btn_history.grid(row=5, column=0, padx=20, pady=8, sticky="ew")

        self.status_label = ctk.CTkLabel(self.sidebar, text="Non connecté", text_color="gray")
        self.status_label.grid(row=9, column=0, padx=20, pady=20)

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.frames: Dict[str, ctk.CTkFrame] = {}
        for name in ("connect", "scan", "queue", "settings", "history"):
            f = ctk.CTkFrame(self.content, fg_color="transparent")
            f.grid(row=0, column=0, sticky="nsew")
            self.frames[name] = f
        self.show_connect()

    def _clear_frame(self, frame: ctk.CTkFrame):
        for w in frame.winfo_children():
            w.destroy()

    def show_frame(self, name: str):
        for n, f in self.frames.items():
            f.grid_remove()
        self.frames[name].grid()

    def show_connect(self):
        self.show_frame("connect")
        f = self.frames["connect"]
        self._clear_frame(f)
        f.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(f, text="Connexion au serveur Plex",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, pady=(10, 20))

        pin_frame = ctk.CTkFrame(f)
        pin_frame.grid(row=1, column=0, padx=40, pady=10, sticky="ew")
        pin_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(pin_frame, text="Méthode recommandée : code PIN").grid(row=0, column=0, columnspan=2, pady=10)

        self.pin_var = ctk.StringVar(value="––––")
        ctk.CTkLabel(pin_frame, textvariable=self.pin_var,
                     font=ctk.CTkFont(size=32, weight="bold")).grid(row=1, column=0, columnspan=2, pady=10)

        btn_row = ctk.CTkFrame(pin_frame, fg_color="transparent")
        btn_row.grid(row=2, column=0, columnspan=2, pady=10)
        ctk.CTkButton(btn_row, text="Générer un code PIN", command=self._generate_pin).pack(side="left", padx=5)
        ctk.CTkButton(btn_row, text="Ouvrir plex.tv/link + coller", command=self._open_link).pack(side="left", padx=5)
        ctk.CTkButton(btn_row, text="Attendre la validation…", command=self._wait_pin).pack(side="left", padx=5)

        self.pin_status = ctk.CTkLabel(pin_frame, text="")
        self.pin_status.grid(row=3, column=0, columnspan=2, pady=5)

        tok_frame = ctk.CTkFrame(f)
        tok_frame.grid(row=2, column=0, padx=40, pady=20, sticky="ew")
        tok_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tok_frame, text="Ou coller un token existant :").grid(row=0, column=0, padx=10, pady=10)
        self.token_entry = ctk.CTkEntry(tok_frame, width=400, show="•")
        self.token_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        if self.settings.get("token"):
            self.token_entry.insert(0, self.settings["token"])
        ctk.CTkButton(tok_frame, text="Se connecter", command=self._connect_token).grid(row=0, column=2, padx=10)

        self.server_frame = ctk.CTkFrame(f)
        self.server_frame.grid(row=3, column=0, padx=40, pady=10, sticky="ew")

    def _generate_pin(self):
        try:
            pin = self.plex.start_pin_login()
            self.pin_var.set(pin)
            self.pin_status.configure(text="Code généré. Cliquez sur « Ouvrir plex.tv/link + coller » puis validez sur le site.")
        except Exception as e:
            messagebox.showerror("Erreur", str(e))

    def _open_link(self):
        pin = self.pin_var.get()
        if pin and pin != "––––":
            self.plex.open_link_and_copy(pin)
            self.pin_status.configure(text="Navigateur ouvert. Collez le code (Ctrl+V) sur plex.tv/link puis cliquez « Attendre ».")
        else:
            messagebox.showwarning("PIN manquant", "Générez d’abord un code PIN.")

    def _wait_pin(self):
        self.pin_status.configure(text="Attente de validation… (max 5 min)")
        def worker():
            ok = self.plex.wait_for_pin(timeout=300)
            self.after(0, lambda: self._on_pin_result(ok))
        threading.Thread(target=worker, daemon=True).start()

    def _on_pin_result(self, ok: bool):
        if ok:
            self.pin_status.configure(text="Connecté ! Sélectionnez un serveur.")
            self.settings["token"] = self.plex.token
            save_settings(self.settings)
            self._show_servers()
        else:
            self.pin_status.configure(text="Échec ou expiration. Réessayez.")

    def _connect_token(self):
        token = self.token_entry.get().strip()
        if not token:
            messagebox.showwarning("Token", "Collez un token valide.")
            return
        if self.plex.connect_with_token(token):
            self.settings["token"] = token
            self.settings["last_server_name"] = self.plex.server_name
            save_settings(self.settings)
            self._on_connected()
        else:
            messagebox.showerror("Erreur", "Impossible de se connecter avec ce token.")

    def _show_servers(self):
        for w in self.server_frame.winfo_children():
            w.destroy()
        servers = self.plex.list_servers()
        if not servers:
            ctk.CTkLabel(self.server_frame, text="Aucun serveur trouvé.").pack()
            return
        ctk.CTkLabel(self.server_frame, text="Choisir le serveur (compte principal) :").pack(pady=5)
        self.server_var = ctk.StringVar(value=servers[0])
        menu = ctk.CTkOptionMenu(self.server_frame, values=servers, variable=self.server_var)
        menu.pack(pady=5)
        ctk.CTkButton(self.server_frame, text="Connecter à ce serveur",
                      command=lambda: self._connect_server(self.server_var.get())).pack(pady=10)

    def _connect_server(self, name: str):
        if self.plex.connect_with_token(self.plex.token, name):
            self.settings["last_server_name"] = name
            save_settings(self.settings)
            self._on_connected()
        else:
            messagebox.showerror("Erreur", f"Impossible de joindre le serveur « {name} ».")

    def _on_connected(self):
        self.status_label.configure(text=f"Connecté : {self.plex.server_name}", text_color="#4ade80")
        self.btn_scan.configure(state="normal")
        self.btn_queue.configure(state="normal")
        messagebox.showinfo("Succès", f"Connecté au serveur « {self.plex.server_name} » (compte principal).")

    def _try_auto_connect(self):
        token = self.settings.get("token")
        name = self.settings.get("last_server_name", "")
        if token:
            if self.plex.connect_with_token(token, name):
                self._on_connected()

    def show_scan(self):
        self.show_frame("scan")
        f = self.frames["scan"]
        self._clear_frame(f)
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=5)
        ctk.CTkButton(top, text="Lancer le scan des bibliothèques", command=self._start_scan).pack(side="left", padx=5)
        ctk.CTkButton(top, text="Tout sélectionner", command=self._select_all).pack(side="left", padx=5)
        ctk.CTkButton(top, text="Tout désélectionner", command=self._deselect_all).pack(side="left", padx=5)
        ctk.CTkButton(top, text="Ajouter à la file d'attente", command=self._add_to_queue,
                      fg_color="#16a34a").pack(side="left", padx=5)

        self.scan_status = ctk.CTkLabel(f, text="")
        self.scan_status.grid(row=1, column=0, sticky="w", padx=5)

        self.results_frame = ctk.CTkScrollableFrame(f, label_text="Fichiers non optimisés")
        self.results_frame.grid(row=2, column=0, sticky="nsew", pady=5)
        self.results_frame.grid_columnconfigure(1, weight=1)

    def _start_scan(self):
        if self.scan_thread and self.scan_thread.is_alive():
            return
        self.scan_status.configure(text="Scan en cours… cela peut prendre plusieurs minutes.")
        preferred = self.settings.get("preferred_codec", "hevc")
        def worker():
            videos = self.plex.scan_libraries(preferred)
            self.after(0, lambda: self._on_scan_done(videos))
        self.scan_thread = threading.Thread(target=worker, daemon=True)
        self.scan_thread.start()

    def _on_scan_done(self, videos: List[VideoInfo]):
        self.videos = videos
        self.selected = {}
        self.codec_choice = {}
        self.scan_status.configure(text=f"{len(videos)} fichier(s) non optimisé(s) trouvé(s).")
        self._render_results()

    def _render_results(self):
        for w in self.results_frame.winfo_children():
            w.destroy()
        groups: Dict[str, Dict[str, List[VideoInfo]]] = {}
        for v in self.videos:
            lib = v.library
            key = v.series_title if v.library_type == "show" else "_movies_"
            groups.setdefault(lib, {}).setdefault(key, []).append(v)
        row = 0
        for lib, series_dict in sorted(groups.items()):
            ctk.CTkLabel(self.results_frame, text=f"📁 {lib}",
                         font=ctk.CTkFont(size=16, weight="bold")).grid(row=row, column=0, columnspan=6, sticky="w", pady=(15, 5))
            row += 1
            for series, items in sorted(series_dict.items()):
                if series != "_movies_":
                    ctk.CTkLabel(self.results_frame, text=f"  📺 {series}",
                                 font=ctk.CTkFont(size=14, weight="bold")).grid(row=row, column=0, columnspan=6, sticky="w", pady=(8, 2))
                    row += 1
                for v in sorted(items, key=lambda x: (x.season_number, x.episode_number, x.title)):
                    self._add_video_row(v, row)
                    row += 1

    def _add_video_row(self, v: VideoInfo, row: int):
        key = f"{v.rating_key}|{v.file_path}"
        var = ctk.BooleanVar(value=self.selected.get(key, False))
        self.selected[key] = var.get()

        def on_toggle():
            self.selected[key] = var.get()

        cb = ctk.CTkCheckBox(self.results_frame, text="", variable=var, command=on_toggle, width=30)
        cb.grid(row=row, column=0, padx=5, pady=2)

        title = v.display_title[:80]
        ctk.CTkLabel(self.results_frame, text=title, anchor="w").grid(row=row, column=1, sticky="w", padx=5)

        codec_txt = f"{v.video_codec} → {SUPPORTED_CODECS.get(v.recommended_codec, {}).get('label', v.recommended_codec)}"
        ctk.CTkLabel(self.results_frame, text=codec_txt, text_color="#fbbf24").grid(row=row, column=2, padx=5)

        info = f"{v.width}x{v.height} | {v.size_gb:.2f} Go | {v.duration_str}"
        ctk.CTkLabel(self.results_frame, text=info, text_color="gray").grid(row=row, column=3, padx=5)

        codec_var = ctk.StringVar(value=self.codec_choice.get(key, v.recommended_codec))
        self.codec_choice[key] = codec_var.get()
        opts = list(SUPPORTED_CODECS.keys())
        menu = ctk.CTkOptionMenu(self.results_frame, values=opts, variable=codec_var, width=90,
                                 command=lambda c, k=key: self.codec_choice.__setitem__(k, c))
        menu.grid(row=row, column=4, padx=5)

        ctk.CTkButton(self.results_frame, text="📁", width=30,
                      command=lambda p=v.file_path: self._open_folder(p)).grid(row=row, column=5, padx=2)

    def _select_all(self):
        for k in list(self.selected.keys()):
            self.selected[k] = True
        self._render_results()

    def _deselect_all(self):
        for k in list(self.selected.keys()):
            self.selected[k] = False
        self._render_results()

    def _add_to_queue(self):
        added = 0
        for v in self.videos:
            key = f"{v.rating_key}|{v.file_path}"
            if not self.selected.get(key):
                continue
            codec = self.codec_choice.get(key, self.settings.get("preferred_codec", "hevc"))
            out_ext = ".mp4" if self.settings.get("container", "mp4") == "mp4" else ".mkv"
            out_path = str(Path(v.file_path).with_suffix(f".optimized{out_ext}"))
            job = EncodeJob(
                video_path=v.file_path,
                output_path=out_path,
                codec=codec,
                crf=int(self.settings.get("crf", 24)),
                preset=self.settings.get("preset", "medium"),
                audio_bitrate=self.settings.get("audio_bitrate", "192k"),
                audio_channels=int(self.settings.get("audio_channels", 2)),
                keep_audio_copy=bool(self.settings.get("keep_audio_copy", False)),
                hardware=self.settings.get("hardware_accel", "none"),
                container=self.settings.get("container", "mp4"),
                dry_run=bool(self.settings.get("dry_run", False)),
            )
            job._video_info = v  # type: ignore
            self.queue.append(job)
            added += 1
        messagebox.showinfo("File d'attente", f"{added} fichier(s) ajouté(s) à la file d'attente.")
        self.show_queue()

    def _open_folder(self, path: str):
        folder = str(Path(path).parent)
        if os.name == "nt":
            os.startfile(folder)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])

    def show_queue(self):
        self.show_frame("queue")
        f = self.frames["queue"]
        self._clear_frame(f)
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=5)
        ctk.CTkButton(top, text="▶ Lancer l'encodage optimisé", command=self._start_queue,
                      fg_color="#16a34a").pack(side="left", padx=5)
        ctk.CTkButton(top, text="⏹ Arrêter", command=self._stop_queue,
                      fg_color="#dc2626").pack(side="left", padx=5)
        ctk.CTkButton(top, text="Vider la file", command=self._clear_queue).pack(side="left", padx=5)

        self.queue_frame = ctk.CTkScrollableFrame(f, label_text=f"File d'attente ({len(self.queue)} jobs)")
        self.queue_frame.grid(row=1, column=0, sticky="nsew")
        self._render_queue()

    def _render_queue(self):
        for w in self.queue_frame.winfo_children():
            w.destroy()
        for i, job in enumerate(self.queue):
            row = ctk.CTkFrame(self.queue_frame)
            row.pack(fill="x", pady=4, padx=5)
            name = Path(job.video_path).name
            ctk.CTkLabel(row, text=f"{i+1}. {name[:60]}", width=350, anchor="w").pack(side="left", padx=5)
            ctk.CTkLabel(row, text=job.codec.upper(), width=60).pack(side="left")
            prog = ctk.CTkProgressBar(row, width=200)
            prog.set(job.progress / 100.0)
            prog.pack(side="left", padx=10)
            status_color = {"done": "#4ade80", "error": "#f87171", "running": "#60a5fa",
                            "cancelled": "#fbbf24"}.get(job.status, "gray")
            ctk.CTkLabel(row, text=f"{job.progress:.0f}% – {job.status}", text_color=status_color).pack(side="left", padx=5)

    def _start_queue(self):
        if self.queue_running:
            return
        pending = [j for j in self.queue if j.status == "pending"]
        if not pending:
            messagebox.showinfo("File", "Aucun job en attente.")
            return
        self.queue_running = True
        def worker():
            for job in self.queue:
                if not self.queue_running:
                    break
                if job.status != "pending":
                    continue
                def cb(p, msg, j=job):
                    j.progress = p
                    self.after(0, self._render_queue)
                self.encoder.encode(job, progress_cb=cb)
                if job.status == "done":
                    auto = self.settings.get("auto_replace", False)
                    if auto or messagebox.askyesno("Remplacer ?",
                                                   f"Remplacer le fichier original ?\n{Path(job.video_path).name}"):
                        if self.encoder.replace_original(job):
                            vinfo = getattr(job, "_video_info", None)
                            if vinfo and self.settings.get("refresh_plex_after", True):
                                self.plex.refresh_item(vinfo.rating_key)
                    self.history.append({
                        "time": datetime.now().isoformat(),
                        "file": job.video_path,
                        "codec": job.codec,
                        "status": job.status,
                        "original_size": job.original_size,
                        "output_size": job.output_size,
                        "duration_s": job.end_time - job.start_time,
                    })
                    save_history(self.history)
            self.queue_running = False
            self.after(0, self._render_queue)
            self.after(0, lambda: messagebox.showinfo("Terminé", "File d'attente traitée."))
        threading.Thread(target=worker, daemon=True).start()

    def _stop_queue(self):
        self.queue_running = False
        self.encoder.stop()
        messagebox.showinfo("Arrêt", "Arrêt demandé. Le job en cours sera interrompu.")

    def _clear_queue(self):
        if self.queue_running:
            messagebox.showwarning("Attention", "Arrêtez d’abord l’encodage.")
            return
        self.queue.clear()
        self._render_queue()

    def show_settings(self):
        self.show_frame("settings")
        f = self.frames["settings"]
        self._clear_frame(f)
        f.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(f, text="Paramètres", font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, columnspan=2, pady=15)

        r = 1
        ctk.CTkLabel(f, text="Codec optimisé préféré").grid(row=r, column=0, sticky="w", padx=20, pady=8)
        self.pref_codec = ctk.StringVar(value=self.settings.get("preferred_codec", "hevc"))
        ctk.CTkOptionMenu(f, values=["hevc", "h264", "auto"], variable=self.pref_codec).grid(row=r, column=1, sticky="w", pady=8)
        r += 1

        self.auto_replace = ctk.BooleanVar(value=self.settings.get("auto_replace", False))
        ctk.CTkCheckBox(f, text="Remplacer automatiquement le fichier d'origine",
                        variable=self.auto_replace).grid(row=r, column=0, columnspan=2, sticky="w", padx=20, pady=8)
        r += 1

        ctk.CTkLabel(f, text="Qualité CRF (18=meilleure, 28=plus petit)").grid(row=r, column=0, sticky="w", padx=20, pady=8)
        self.crf_var = ctk.IntVar(value=int(self.settings.get("crf", 24)))
        ctk.CTkSlider(f, from_=18, to=32, number_of_steps=14, variable=self.crf_var).grid(row=r, column=1, sticky="ew", pady=8)
        r += 1

        ctk.CTkLabel(f, text="Preset FFmpeg").grid(row=r, column=0, sticky="w", padx=20, pady=8)
        self.preset_var = ctk.StringVar(value=self.settings.get("preset", "medium"))
        ctk.CTkOptionMenu(f, values=PRESETS, variable=self.preset_var).grid(row=r, column=1, sticky="w", pady=8)
        r += 1

        ctk.CTkLabel(f, text="Accélération matérielle").grid(row=r, column=0, sticky="w", padx=20, pady=8)
        self.hw_var = ctk.StringVar(value=self.settings.get("hardware_accel", "none"))
        ctk.CTkOptionMenu(f, values=list(HARDWARE_OPTIONS.keys()), variable=self.hw_var).grid(row=r, column=1, sticky="w", pady=8)
        r += 1

        ctk.CTkLabel(f, text="Bitrate audio").grid(row=r, column=0, sticky="w", padx=20, pady=8)
        self.audio_br = ctk.StringVar(value=self.settings.get("audio_bitrate", "192k"))
        ctk.CTkEntry(f, textvariable=self.audio_br, width=100).grid(row=r, column=1, sticky="w", pady=8)
        r += 1

        self.keep_audio = ctk.BooleanVar(value=self.settings.get("keep_audio_copy", False))
        ctk.CTkCheckBox(f, text="Copier l'audio si possible (sans réencodage)",
                        variable=self.keep_audio).grid(row=r, column=0, columnspan=2, sticky="w", padx=20, pady=8)
        r += 1

        self.dry_run = ctk.BooleanVar(value=self.settings.get("dry_run", False))
        ctk.CTkCheckBox(f, text="Mode dry-run (simulation, n'écrit rien)",
                        variable=self.dry_run).grid(row=r, column=0, columnspan=2, sticky="w", padx=20, pady=8)
        r += 1

        self.refresh_plex = ctk.BooleanVar(value=self.settings.get("refresh_plex_after", True))
        ctk.CTkCheckBox(f, text="Rafraîchir l'élément dans Plex après remplacement",
                        variable=self.refresh_plex).grid(row=r, column=0, columnspan=2, sticky="w", padx=20, pady=8)
        r += 1

        ctk.CTkButton(f, text="Enregistrer les paramètres", command=self._save_settings,
                      fg_color="#16a34a").grid(row=r, column=0, columnspan=2, pady=20)

    def _save_settings(self):
        self.settings["preferred_codec"] = self.pref_codec.get()
        self.settings["auto_replace"] = self.auto_replace.get()
        self.settings["crf"] = self.crf_var.get()
        self.settings["preset"] = self.preset_var.get()
        self.settings["hardware_accel"] = self.hw_var.get()
        self.settings["audio_bitrate"] = self.audio_br.get()
        self.settings["keep_audio_copy"] = self.keep_audio.get()
        self.settings["dry_run"] = self.dry_run.get()
        self.settings["refresh_plex_after"] = self.refresh_plex.get()
        save_settings(self.settings)
        messagebox.showinfo("Paramètres", "Paramètres enregistrés.")

    def show_history(self):
        self.show_frame("history")
        f = self.frames["history"]
        self._clear_frame(f)
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(f, label_text="Historique des encodages")
        scroll.grid(row=0, column=0, sticky="nsew")
        for entry in reversed(self.history[-50:]):
            line = (f"{entry.get('time', '')[:19]}  |  {Path(entry.get('file', '')).name[:50]}  |  "
                    f"{entry.get('codec', '')}  |  {entry.get('status', '')}  |  "
                    f"{entry.get('duration_s', 0):.0f}s")
            ctk.CTkLabel(scroll, text=line, anchor="w").pack(fill="x", padx=5, pady=2)
