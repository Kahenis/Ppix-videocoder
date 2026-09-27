"""Main GUI for Ppix-Videocoder – theme PPIX (or / sombre)."""
from __future__ import annotations

import os
import threading
import time
import traceback
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

import customtkinter as ctk

from .config import (
    load_settings, save_settings, load_history, save_history,
    SUPPORTED_CODECS, PRESETS, HARDWARE_OPTIONS, APP_NAME, APP_VERSION,
)
from .plex_client import PlexClient, VideoInfo
from .encoder import Encoder, EncodeJob

# ----- Theme PPIX (identique a ppix-indexeur) -----
ACCENT = "#E5A00D"
BG = "#161616"
PANEL = "#1E1E1E"
CARD = "#262626"
TEXT = "#F2F2F2"
MUTED = "#A0A0A0"
OK = "#3DDC84"
WARN = "#FFB020"
DANGER = "#5C1A1A"
DANGER_HOVER = "#8A3618"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1200x760")
        self.minsize(960, 600)
        self.configure(fg_color=BG)

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
        self._pin_stop = threading.Event()
        self._ui_queue: list = []
        self._ui_lock = threading.Lock()
        self._page = "connect"

        try:
            self._build()
        except Exception:
            err = traceback.format_exc()
            print(err)
            lbl = ctk.CTkLabel(self, text=f"Erreur demarrage:\n{err[:800]}", text_color=WARN, justify="left")
            lbl.pack(padx=20, pady=20, fill="both", expand=True)
            return

        self.after(80, self._drain)
        self.after(300, self._try_auto_connect)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _ui(self, fn, *a, **k):
        with self._ui_lock:
            self._ui_queue.append((fn, a, k))

    def _drain(self):
        with self._ui_lock:
            batch, self._ui_queue = self._ui_queue[:], []
        for fn, a, k in batch:
            try:
                fn(*a, **k)
            except Exception as exc:
                self._log(f"UI: {exc}")
        self.after(80, self._drain)

    def _log(self, msg: str):
        try:
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except Exception:
            print(msg)

    def _close(self):
        self._pin_stop.set()
        self.queue_running = False
        try:
            self.encoder.stop()
        except Exception:
            pass
        self.destroy()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color=PANEL, height=56, corner_radius=0)
        top.pack(fill="x")
        ctk.CTkLabel(
            top, text="PPIX Videocoder", font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT
        ).pack(side="left", padx=16, pady=12)
        self.lbl_conn = ctk.CTkLabel(top, text="Non connecte", text_color=MUTED)
        self.lbl_conn.pack(side="left", padx=8)
        ctk.CTkButton(top, text="Parametres", width=110, fg_color="#333", hover_color="#444",
                      command=self._show_settings).pack(side="right", padx=12)
        ctk.CTkButton(top, text="Historique", width=100, fg_color="#333", hover_color="#444",
                      command=self._show_history).pack(side="right", padx=4)

        nav = ctk.CTkFrame(self, fg_color=BG)
        nav.pack(fill="x", padx=14, pady=(8, 0))
        self.btn_nav_connect = ctk.CTkButton(
            nav, text="1. Connexion", width=140, fg_color=ACCENT, text_color="#111",
            hover_color="#C48A0B", command=lambda: self._show_page("connect"),
        )
        self.btn_nav_connect.pack(side="left", padx=(0, 6))
        self.btn_nav_scan = ctk.CTkButton(
            nav, text="2. Scanner", width=140, fg_color="#333", state="disabled",
            command=lambda: self._show_page("scan"),
        )
        self.btn_nav_scan.pack(side="left", padx=6)
        self.btn_nav_queue = ctk.CTkButton(
            nav, text="3. File d'attente", width=150, fg_color="#333", state="disabled",
            command=lambda: self._show_page("queue"),
        )
        self.btn_nav_queue.pack(side="left", padx=6)

        self.content = ctk.CTkFrame(self, fg_color=BG)
        self.content.pack(fill="both", expand=True, padx=14, pady=8)

        self.log = ctk.CTkTextbox(self, height=100, fg_color=CARD, text_color=TEXT)
        self.log.pack(fill="x", padx=14, pady=(0, 10))
        self.log.configure(state="disabled")

        self._show_page("connect")
        self._log(f"{APP_NAME} v{APP_VERSION} pret.")

    def _clear_content(self):
        for w in self.content.winfo_children():
            w.destroy()

    def _show_page(self, name: str):
        self._page = name
        self._clear_content()
        for btn, page in (
            (self.btn_nav_connect, "connect"),
            (self.btn_nav_scan, "scan"),
            (self.btn_nav_queue, "queue"),
        ):
            if page == name:
                btn.configure(fg_color=ACCENT, text_color="#111", hover_color="#C48A0B")
            else:
                btn.configure(fg_color="#333", text_color=TEXT, hover_color="#444")
        if name == "connect":
            self._page_connect()
        elif name == "scan":
            self._page_scan()
        elif name == "queue":
            self._page_queue()

    def _set_connected_ui(self, ok: bool, waiting: bool = False, error: bool = False):
        if waiting:
            self.lbl_conn.configure(text="Attente PIN…", text_color=WARN)
            self.btn_nav_connect.configure(fg_color=WARN, text_color="#111")
        elif ok:
            name = self.plex.server_name or "OK"
            self.lbl_conn.configure(text=f"Connecte · {name}", text_color=OK)
            self.btn_nav_connect.configure(fg_color=OK, text_color="#111")
            self.btn_nav_scan.configure(state="normal")
            self.btn_nav_queue.configure(state="normal")
        elif error:
            self.lbl_conn.configure(text="Connexion echouee", text_color="#f87171")
            self.btn_nav_connect.configure(fg_color=DANGER, text_color=TEXT)
        else:
            self.lbl_conn.configure(text="Non connecte", text_color=MUTED)

    def _page_connect(self):
        f = self.content
        card = ctk.CTkFrame(f, fg_color=PANEL, corner_radius=10)
        card.pack(fill="x", pady=8)
        ctk.CTkLabel(card, text="Connexion au serveur Plex", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(14, 6))
        ctk.CTkLabel(card, text="Un clic genere le code, ouvre plex.tv/link et attend la validation automatiquement.",
                     text_color=MUTED).pack(anchor="w", padx=16)
        self.pin_var = ctk.StringVar(value="––––")
        ctk.CTkLabel(card, textvariable=self.pin_var, font=ctk.CTkFont(size=36, weight="bold"),
                     text_color=ACCENT).pack(pady=16)
        row = ctk.CTkFrame(card, fg_color=PANEL)
        row.pack(pady=(0, 8))
        self.btn_pin = ctk.CTkButton(
            row, text="Se connecter via plex.tv/link", width=280,
            fg_color=ACCENT, text_color="#111", hover_color="#C48A0B",
            command=self._start_pin,
        )
        self.btn_pin.pack(side="left", padx=8)
        self.pin_status = ctk.CTkLabel(card, text="", text_color=MUTED)
        self.pin_status.pack(pady=(0, 12))

        tok = ctk.CTkFrame(f, fg_color=PANEL, corner_radius=10)
        tok.pack(fill="x", pady=8)
        ctk.CTkLabel(tok, text="Ou coller un token existant", text_color=MUTED).pack(anchor="w", padx=16, pady=(12, 4))
        row2 = ctk.CTkFrame(tok, fg_color=PANEL)
        row2.pack(fill="x", padx=16, pady=(0, 12))
        self.token_entry = ctk.CTkEntry(row2, width=420, show="•", fg_color=CARD)
        self.token_entry.pack(side="left", padx=(0, 8))
        if self.settings.get("token"):
            self.token_entry.insert(0, self.settings["token"])
        ctk.CTkButton(row2, text="Se connecter", width=120, fg_color="#333",
                      command=self._connect_token).pack(side="left")

        self.server_box = ctk.CTkFrame(f, fg_color=PANEL, corner_radius=10)
        self.server_box.pack(fill="x", pady=8)

    def _start_pin(self):
        self._pin_stop.clear()
        self.btn_pin.configure(state="disabled")
        self._set_connected_ui(False, waiting=True)
        self.pin_status.configure(text="Generation du code…")
        threading.Thread(target=self._pin_worker, daemon=True).start()

    def _pin_worker(self):
        try:
            pin = self.plex.start_pin_login()
            self._ui(self.pin_var.set, pin)
            self.plex.open_link_and_copy(pin)
            self._ui(self.pin_status.configure,
                     text="Navigateur ouvert — validez le code sur plex.tv/link (attente auto)…")
            self._ui(self._log, f"PIN {pin} — attente validation…")
            ok = self.plex.wait_for_pin(timeout=300, poll=1.5)
            if self._pin_stop.is_set():
                return
            if ok:
                self.settings["token"] = self.plex.token
                save_settings(self.settings)
                self._ui(self.pin_status.configure, text="Compte valide ! Choisissez un serveur.")
                self._ui(self._log, "Compte Plex valide.")
                self._ui(self._fill_servers, self.plex.list_servers())
            else:
                self._ui(self.pin_status.configure, text="Echec ou expiration. Reessayez.")
                self._ui(self._set_connected_ui, False, error=True)
                self._ui(self._log, "PIN expire ou refuse.")
        except Exception as e:
            self._ui(self.pin_status.configure, text=str(e))
            self._ui(self._set_connected_ui, False, error=True)
            self._ui(self._log, f"PIN erreur: {e}")
        finally:
            self._ui(lambda: self.btn_pin.configure(state="normal"))

    def _fill_servers(self, servers: List[str]):
        for w in self.server_box.winfo_children():
            w.destroy()
        if not servers:
            ctk.CTkLabel(self.server_box, text="Aucun serveur trouve.", text_color=WARN).pack(padx=16, pady=12)
            self._set_connected_ui(False, error=True)
            return
        ctk.CTkLabel(self.server_box, text="Choisir le serveur (compte principal)", text_color=TEXT).pack(
            anchor="w", padx=16, pady=(12, 4)
        )
        self.server_var = ctk.StringVar(value=servers[0])
        ctk.CTkOptionMenu(self.server_box, values=servers, variable=self.server_var, width=280,
                          fg_color=CARD).pack(anchor="w", padx=16, pady=4)
        ctk.CTkButton(
            self.server_box, text="Connecter a ce serveur", width=200,
            fg_color=ACCENT, text_color="#111", hover_color="#C48A0B",
            command=lambda: self._connect_server(self.server_var.get()),
        ).pack(anchor="w", padx=16, pady=(8, 14))

    def _connect_server(self, name: str):
        self._set_connected_ui(False, waiting=True)
        self._log(f"Connexion a {name}…")

        def work():
            ok = self.plex.connect_with_token(self.plex.token, name)
            if ok:
                self.settings["last_server_name"] = name
                save_settings(self.settings)
                self._ui(self._on_connected)
            else:
                self._ui(self._set_connected_ui, False, error=True)
                self._ui(self._log, f"Impossible de joindre {name}")

        threading.Thread(target=work, daemon=True).start()

    def _connect_token(self):
        token = self.token_entry.get().strip()
        if not token:
            self._log("Collez un token valide.")
            return
        self._set_connected_ui(False, waiting=True)

        def work():
            ok = self.plex.connect_with_token(token)
            if ok:
                self.settings["token"] = token
                self.settings["last_server_name"] = self.plex.server_name
                save_settings(self.settings)
                self._ui(self._on_connected)
            else:
                self._ui(self._set_connected_ui, False, error=True)
                self._ui(self._log, "Token refuse.")

        threading.Thread(target=work, daemon=True).start()

    def _on_connected(self):
        self._set_connected_ui(True)
        self._log(f"Connecte au serveur « {self.plex.server_name} » (compte principal).")
        self._show_page("scan")

    def _try_auto_connect(self):
        token = self.settings.get("token") or ""
        name = self.settings.get("last_server_name") or ""
        if not token:
            return
        self._log("Tentative de reconnexion…")

        def work():
            ok = self.plex.connect_with_token(token, name)
            if ok:
                self._ui(self._on_connected)
            else:
                self._ui(self._log, "Reconnexion impossible — reconnectez-vous.")

        threading.Thread(target=work, daemon=True).start()

    def _page_scan(self):
        f = self.content
        top = ctk.CTkFrame(f, fg_color=PANEL, corner_radius=10)
        top.pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(top, fg_color=PANEL)
        row.pack(fill="x", padx=12, pady=10)
        ctk.CTkButton(row, text="Lancer le scan", width=150, fg_color=ACCENT, text_color="#111",
                      hover_color="#C48A0B", command=self._start_scan).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Tout selectionner", width=140, fg_color="#333",
                      command=self._select_all).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Tout deselectionner", width=150, fg_color="#333",
                      command=self._deselect_all).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Ajouter a la file", width=150, fg_color=OK, text_color="#111",
                      hover_color="#2bb86e", command=self._add_to_queue).pack(side="left", padx=4)
        self.scan_status = ctk.CTkLabel(top, text="", text_color=MUTED)
        self.scan_status.pack(anchor="w", padx=16, pady=(0, 8))

        self.results = ctk.CTkScrollableFrame(f, fg_color=PANEL, corner_radius=10, label_text="Fichiers non optimises")
        self.results.pack(fill="both", expand=True)
        if self.videos:
            self._render_results()

    def _start_scan(self):
        if self.scan_thread and self.scan_thread.is_alive():
            self._log("Scan deja en cours.")
            return
        self.scan_status.configure(text="Scan en cours…")
        self._log("Scan des bibliotheques…")
        preferred = self.settings.get("preferred_codec", "hevc")

        def work():
            try:
                videos = self.plex.scan_libraries(preferred)
                self._ui(self._on_scan_done, videos)
            except Exception as e:
                self._ui(self.scan_status.configure, text=f"Erreur: {e}")
                self._ui(self._log, f"Scan erreur: {e}")

        self.scan_thread = threading.Thread(target=work, daemon=True)
        self.scan_thread.start()

    def _on_scan_done(self, videos: List[VideoInfo]):
        self.videos = videos
        valid = {f"{v.rating_key}|{v.file_path}" for v in videos}
        self.selected = {k: v for k, v in self.selected.items() if k in valid}
        self.codec_choice = {k: v for k, v in self.codec_choice.items() if k in valid}
        self.scan_status.configure(text=f"{len(videos)} fichier(s) non optimise(s).")
        self._log(f"Scan termine: {len(videos)} fichier(s).")
        self._render_results()

    def _render_results(self):
        for w in self.results.winfo_children():
            w.destroy()
        groups: Dict[str, Dict[str, List[VideoInfo]]] = {}
        for v in self.videos:
            lib = v.library
            key = v.series_title if v.library_type == "show" else "_movies_"
            groups.setdefault(lib, {}).setdefault(key, []).append(v)

        for lib, series_dict in sorted(groups.items()):
            ctk.CTkLabel(self.results, text=f"  {lib}", font=ctk.CTkFont(size=15, weight="bold"),
                         text_color=ACCENT).pack(anchor="w", pady=(12, 4), padx=8)
            for series, items in sorted(series_dict.items()):
                if series != "_movies_":
                    ctk.CTkLabel(self.results, text=f"    {series}", font=ctk.CTkFont(size=13, weight="bold"),
                                 text_color=TEXT).pack(anchor="w", padx=12, pady=(6, 2))
                for v in sorted(items, key=lambda x: (x.season_number, x.episode_number, x.title)):
                    self._row_video(v)

    def _row_video(self, v: VideoInfo):
        key = f"{v.rating_key}|{v.file_path}"
        if key not in self.selected:
            self.selected[key] = False
        if key not in self.codec_choice:
            self.codec_choice[key] = v.recommended_codec

        row = ctk.CTkFrame(self.results, fg_color=CARD, corner_radius=6)
        row.pack(fill="x", padx=8, pady=2)

        var = ctk.BooleanVar(value=bool(self.selected.get(key, False)))

        def on_toggle(k=key, bv=var):
            self.selected[k] = bool(bv.get())

        ctk.CTkCheckBox(row, text="", variable=var, command=on_toggle, width=28,
                        fg_color=ACCENT, hover_color="#C48A0B").pack(side="left", padx=6, pady=6)
        ctk.CTkLabel(row, text=v.display_title[:70], anchor="w", width=320, text_color=TEXT).pack(
            side="left", padx=4
        )
        codec_txt = f"{v.video_codec} → {SUPPORTED_CODECS.get(v.recommended_codec, {}).get('label', v.recommended_codec)}"
        ctk.CTkLabel(row, text=codec_txt, text_color=WARN, width=180).pack(side="left", padx=4)
        ctk.CTkLabel(row, text=f"{v.width}x{v.height} · {v.size_gb:.2f} Go · {v.duration_str}",
                     text_color=MUTED, width=180).pack(side="left", padx=4)

        codec_var = ctk.StringVar(value=self.codec_choice.get(key, v.recommended_codec))

        def on_codec(c, k=key):
            self.codec_choice[k] = c

        ctk.CTkOptionMenu(row, values=list(SUPPORTED_CODECS.keys()), variable=codec_var, width=90,
                          fg_color="#333", command=on_codec).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Dossier", width=70, height=26, fg_color="#333",
                      command=lambda p=v.file_path: self._open_folder(p)).pack(side="right", padx=6)

    def _select_all(self):
        for v in self.videos:
            self.selected[f"{v.rating_key}|{v.file_path}"] = True
        self._render_results()
        self._log(f"{len(self.videos)} selectionne(s).")

    def _deselect_all(self):
        for v in self.videos:
            self.selected[f"{v.rating_key}|{v.file_path}"] = False
        self._render_results()

    def _add_to_queue(self):
        """Ajoute sans messagebox (evite fenetre grise / freeze)."""
        added = 0
        try:
            for v in self.videos:
                key = f"{v.rating_key}|{v.file_path}"
                if not self.selected.get(key):
                    continue
                codec = self.codec_choice.get(key, self.settings.get("preferred_codec", "hevc"))
                if codec not in SUPPORTED_CODECS:
                    codec = "hevc"
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
        except Exception as e:
            self._log(f"Erreur ajout file: {e}")
            return

        if added == 0:
            self._log("Aucun fichier selectionne.")
            return
        self._log(f"{added} fichier(s) ajoute(s) a la file.")
        self._show_page("queue")

    def _open_folder(self, path: str):
        folder = str(Path(path).parent)
        try:
            if os.name == "nt":
                os.startfile(folder)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            self._log(f"Dossier: {e}")

    def _page_queue(self):
        f = self.content
        top = ctk.CTkFrame(f, fg_color=PANEL, corner_radius=10)
        top.pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(top, fg_color=PANEL)
        row.pack(fill="x", padx=12, pady=10)
        ctk.CTkButton(row, text="Lancer l'encodage", width=160, fg_color=OK, text_color="#111",
                      hover_color="#2bb86e", command=self._start_queue).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Arreter", width=100, fg_color=DANGER, hover_color=DANGER_HOVER,
                      command=self._stop_queue).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Vider la file", width=120, fg_color="#333",
                      command=self._clear_queue).pack(side="left", padx=4)
        ctk.CTkLabel(top, text=f"{len(self.queue)} job(s) en file", text_color=MUTED).pack(
            anchor="w", padx=16, pady=(0, 8)
        )

        self.queue_list = ctk.CTkScrollableFrame(f, fg_color=PANEL, corner_radius=10)
        self.queue_list.pack(fill="both", expand=True)
        self._render_queue()

    def _render_queue(self):
        if not hasattr(self, "queue_list") or not self.queue_list.winfo_exists():
            return
        for w in self.queue_list.winfo_children():
            w.destroy()
        for i, job in enumerate(self.queue):
            row = ctk.CTkFrame(self.queue_list, fg_color=CARD, corner_radius=6)
            row.pack(fill="x", padx=8, pady=3)
            ctk.CTkLabel(row, text=f"{i+1}. {Path(job.video_path).name[:55]}", width=340,
                         anchor="w", text_color=TEXT).pack(side="left", padx=8, pady=6)
            ctk.CTkLabel(row, text=job.codec.upper(), width=55, text_color=ACCENT).pack(side="left")
            prog = ctk.CTkProgressBar(row, width=180, progress_color=ACCENT)
            prog.set(max(0.0, min(1.0, job.progress / 100.0)))
            prog.pack(side="left", padx=10)
            colors = {"done": OK, "error": "#f87171", "running": "#60a5fa", "cancelled": WARN}
            ctk.CTkLabel(row, text=f"{job.progress:.0f}% · {job.status}",
                         text_color=colors.get(job.status, MUTED)).pack(side="left", padx=6)

    def _start_queue(self):
        if self.queue_running:
            return
        if not any(j.status == "pending" for j in self.queue):
            self._log("Aucun job en attente.")
            return
        self.queue_running = True
        self._log("Encodage demarre.")

        def worker():
            for job in self.queue:
                if not self.queue_running:
                    break
                if job.status != "pending":
                    continue

                def cb(p, msg, j=job):
                    j.progress = p
                    self._ui(self._render_queue)

                self.encoder.encode(job, progress_cb=cb)
                if job.status == "done":
                    auto = self.settings.get("auto_replace", False)
                    do_replace = auto
                    if not auto:
                        result = {"yes": False}
                        ev = threading.Event()

                        def ask():
                            self._ask_replace(job, result, ev)

                        self._ui(ask)
                        ev.wait(timeout=180)
                        do_replace = result["yes"]

                    if do_replace:
                        if self.encoder.replace_original(job):
                            vinfo = getattr(job, "_video_info", None)
                            if vinfo and self.settings.get("refresh_plex_after", True):
                                try:
                                    self.plex.refresh_item(vinfo.rating_key)
                                except Exception:
                                    pass
                            self._ui(self._log, f"Remplace: {Path(job.video_path).name}")
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
                self._ui(self._render_queue)

            self.queue_running = False
            self._ui(self._render_queue)
            self._ui(self._log, "File d'attente terminee.")

        threading.Thread(target=worker, daemon=True).start()

    def _ask_replace(self, job: EncodeJob, result: dict, ev: threading.Event):
        box = ctk.CTkToplevel(self)
        box.title("Remplacer ?")
        box.geometry("480x200")
        box.configure(fg_color=BG)
        box.transient(self)
        try:
            box.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(
            box,
            text=f"Remplacer le fichier original ?\n\n{Path(job.video_path).name}",
            wraplength=440, justify="left", text_color=TEXT,
        ).pack(padx=18, pady=18)

        def yes():
            result["yes"] = True
            try:
                box.grab_release()
            except Exception:
                pass
            box.destroy()
            ev.set()

        def no():
            result["yes"] = False
            try:
                box.grab_release()
            except Exception:
                pass
            box.destroy()
            ev.set()

        row = ctk.CTkFrame(box, fg_color=BG)
        row.pack(pady=8)
        ctk.CTkButton(row, text="Oui, remplacer", fg_color=DANGER, hover_color=DANGER_HOVER,
                      command=yes).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Non, garder les deux", fg_color="#333", command=no).pack(side="left", padx=6)
        box.protocol("WM_DELETE_WINDOW", no)

    def _stop_queue(self):
        self.queue_running = False
        self.encoder.stop()
        self._log("Arret demande.")

    def _clear_queue(self):
        if self.queue_running:
            self._log("Arretez d'abord l'encodage.")
            return
        self.queue.clear()
        self._render_queue()
        self._log("File videe.")

    def _show_settings(self):
        d = ctk.CTkToplevel(self)
        d.title("Parametres")
        d.geometry("520x520")
        d.configure(fg_color=BG)
        d.transient(self)

        scroll = ctk.CTkScrollableFrame(d, fg_color=BG)
        scroll.pack(fill="both", expand=True, padx=12, pady=12)

        ctk.CTkLabel(scroll, text="Codec optimise prefere", text_color=MUTED).pack(anchor="w", pady=(6, 2))
        pref = ctk.StringVar(value=self.settings.get("preferred_codec", "hevc"))
        ctk.CTkOptionMenu(scroll, values=["hevc", "h264", "auto"], variable=pref, fg_color=CARD).pack(anchor="w")

        auto_r = ctk.BooleanVar(value=self.settings.get("auto_replace", False))
        ctk.CTkCheckBox(scroll, text="Remplacer automatiquement le fichier d'origine",
                        variable=auto_r, fg_color=ACCENT).pack(anchor="w", pady=10)

        ctk.CTkLabel(scroll, text="Qualite CRF (18=meilleure, 28=plus petit)", text_color=MUTED).pack(anchor="w")
        crf = ctk.IntVar(value=int(self.settings.get("crf", 24)))
        ctk.CTkSlider(scroll, from_=18, to=32, number_of_steps=14, variable=crf,
                      progress_color=ACCENT).pack(fill="x", pady=4)

        ctk.CTkLabel(scroll, text="Preset FFmpeg", text_color=MUTED).pack(anchor="w", pady=(8, 2))
        preset = ctk.StringVar(value=self.settings.get("preset", "medium"))
        ctk.CTkOptionMenu(scroll, values=PRESETS, variable=preset, fg_color=CARD).pack(anchor="w")

        ctk.CTkLabel(scroll, text="Acceleration materielle", text_color=MUTED).pack(anchor="w", pady=(8, 2))
        hw = ctk.StringVar(value=self.settings.get("hardware_accel", "none"))
        ctk.CTkOptionMenu(scroll, values=list(HARDWARE_OPTIONS.keys()), variable=hw, fg_color=CARD).pack(anchor="w")

        ctk.CTkLabel(scroll, text="Bitrate audio", text_color=MUTED).pack(anchor="w", pady=(8, 2))
        abr = ctk.StringVar(value=self.settings.get("audio_bitrate", "192k"))
        ctk.CTkEntry(scroll, textvariable=abr, width=120, fg_color=CARD).pack(anchor="w")

        keep_a = ctk.BooleanVar(value=self.settings.get("keep_audio_copy", False))
        ctk.CTkCheckBox(scroll, text="Copier l'audio si possible", variable=keep_a, fg_color=ACCENT).pack(
            anchor="w", pady=8
        )
        dry = ctk.BooleanVar(value=self.settings.get("dry_run", False))
        ctk.CTkCheckBox(scroll, text="Mode dry-run (simulation)", variable=dry, fg_color=ACCENT).pack(anchor="w")
        refresh = ctk.BooleanVar(value=self.settings.get("refresh_plex_after", True))
        ctk.CTkCheckBox(scroll, text="Rafraichir Plex apres remplacement", variable=refresh,
                        fg_color=ACCENT).pack(anchor="w", pady=8)

        def save():
            self.settings["preferred_codec"] = pref.get()
            self.settings["auto_replace"] = auto_r.get()
            self.settings["crf"] = crf.get()
            self.settings["preset"] = preset.get()
            self.settings["hardware_accel"] = hw.get()
            self.settings["audio_bitrate"] = abr.get()
            self.settings["keep_audio_copy"] = keep_a.get()
            self.settings["dry_run"] = dry.get()
            self.settings["refresh_plex_after"] = refresh.get()
            save_settings(self.settings)
            self._log("Parametres enregistres.")
            d.destroy()

        ctk.CTkButton(scroll, text="Enregistrer", fg_color=ACCENT, text_color="#111",
                      hover_color="#C48A0B", command=save).pack(pady=16)

    def _show_history(self):
        d = ctk.CTkToplevel(self)
        d.title("Historique")
        d.geometry("720x420")
        d.configure(fg_color=BG)
        d.transient(self)
        scroll = ctk.CTkScrollableFrame(d, fg_color=PANEL)
        scroll.pack(fill="both", expand=True, padx=12, pady=12)
        if not self.history:
            ctk.CTkLabel(scroll, text="Aucun encodage encore.", text_color=MUTED).pack(pady=20)
            return
        for entry in reversed(self.history[-80:]):
            line = (
                f"{entry.get('time', '')[:19]}  |  {Path(entry.get('file', '')).name[:48]}  |  "
                f"{entry.get('codec', '')}  |  {entry.get('status', '')}  |  "
                f"{entry.get('duration_s', 0):.0f}s"
            )
            ctk.CTkLabel(scroll, text=line, anchor="w", text_color=TEXT).pack(fill="x", padx=6, pady=2)
