"""Main GUI for Ppix-Videocoder – theme PPIX."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import customtkinter as ctk

from .about_dialog import show_about
from .config import (
    APP_NAME, APP_VERSION, AUDIO_BITRATE_OPTIONS, AUDIO_CHANNEL_OPTIONS, CODEC_CHOICE_LABELS,
    HARDWARE_OPTIONS, HARDWARE_SHORT, HEAVY_H264_OPTIONS, MIN_KEEP_OPTIONS, PRESETS,
    QUALITY_OPTIONS, SUPPORTED_CODECS, guess_plex_prefix, load_history, load_settings,
    path_exists_for_open, save_history, save_settings,
)
from .dialogs import ask_network_root_before_scan, confirm
from .encoder import EncodeJob, Encoder
from .jobs import (
    JobQueue, finalize_job_settings, make_job_for_video, plex_folder_of, resolve_local_path,
)
from .logger import clear_log, log, log_exception, open_log_folder, read_log
from .plex_client import PlexClient, ScanCancelled, VideoInfo
from .results_list import ResultsList
from .theme import (
    ACCENT, ACCENT_HOVER, BG, CARD, DANGER, DANGER_HOVER, ERR, MUTED, OK, PANEL,
    STATUS_COLORS, STATUS_LABELS, TEXT, WARN,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

ALL_LIBS = "Toutes les bibliothèques"


def _fmt_size(n: float) -> str:
    n = float(n or 0)
    if n >= 1024 ** 4:
        return f"{n / 1024 ** 4:.2f} To"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} Go"
    return f"{n / 1024 ** 2:.0f} Mo"


def _fmt_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if not h and not m:
        return "< 1 min"
    return f"{h}h{m:02d}" if h else f"{m} min"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1240x780")
        self.minsize(980, 620)
        self.configure(fg_color=BG)
        self.settings = load_settings()
        self.history = load_history()
        self.plex = PlexClient()
        self.encoder = Encoder()
        self.videos: List[VideoInfo] = []
        self.selected: Dict[str, bool] = {}
        self.codec_choice: Dict[str, str] = {}
        self.queue = JobQueue(on_change=lambda: self._ui(self._on_queue_changed))
        self.queue_running = False
        self._worker: Optional[threading.Thread] = None
        self.scan_thread: Optional[threading.Thread] = None
        self._scan_stop = threading.Event()
        self._pin_stop = threading.Event()
        self._hw_ready = threading.Event()
        self.detected_hw: Optional[str] = None
        self._ui_queue: list = []
        self._ui_lock = threading.Lock()
        self._page = "connect"
        self._closing = False
        self._queue_widgets: Dict[int, dict] = {}
        self._filter_after: Optional[str] = None
        self._shutdown_var = ctk.BooleanVar(master=self, value=False)
        self._shutdown_flag = False
        try:
            self._build()
        except Exception:
            err = traceback.format_exc()
            print(err)
            ctk.CTkLabel(self, text=f"Erreur démarrage:\n{err[:800]}", text_color=WARN,
                         justify="left").pack(padx=20, pady=20, fill="both", expand=True)
            return
        self.after(80, self._drain)
        self.after(300, self._try_auto_connect)
        self.after(500, self._detect_hw)
        self.protocol("WM_DELETE_WINDOW", self._request_close)

    # ------------------------------------------------------------------
    # Thread → UI
    # ------------------------------------------------------------------
    def _ui(self, fn, *a, **k):
        with self._ui_lock:
            self._ui_queue.append((fn, a, k))

    def _drain(self):
        if self._closing:
            return
        with self._ui_lock:
            batch, self._ui_queue = self._ui_queue[:], []
        for fn, a, k in batch:
            if self._closing:
                return
            try:
                fn(*a, **k)
            except Exception as exc:
                log(f"UI: {exc}\n{traceback.format_exc()}", level="ERROR")
        if not self._closing:
            try:
                self.after(80, self._drain)
            except Exception:
                pass

    def _log(self, msg: str, level: str = "INFO"):
        log(msg, level=level)
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            self.log.configure(state="normal")
            self.log.insert("end", f"{ts}  {msg}\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except Exception:
            print(msg)

    # ------------------------------------------------------------------
    # Fermeture
    # ------------------------------------------------------------------
    def _request_close(self):
        running = self.queue_running
        waiting = self.queue.count("done")
        if not running and not waiting:
            self._close()
            return
        parts = []
        if running:
            parts.append("Un encodage est en cours : il sera interrompu (fichier partiel supprimé).")
        if waiting:
            parts.append(f"{waiting} fichier(s) encodé(s) attendent votre validation : ils seront "
                         "conservés à côté de l'original sous le nom « …optimized ».")
        confirm(self, "Quitter ?", "\n\n".join(parts) + "\n\nQuitter quand même ?", self._close,
                yes_label="Quitter", danger=True)

    def _close(self):
        self._pin_stop.set()
        self._scan_stop.set()
        self.queue_running = False
        try:
            self.encoder.stop()
        except Exception:
            pass
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=10)
        for job in self.queue.snapshot():
            if job.status == "done":
                self.encoder.keep_both(job)
                self._record_history(job)
        self._closing = True
        try:
            self.quit()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Structure
    # ------------------------------------------------------------------
    def _build(self):
        top = ctk.CTkFrame(self, fg_color=PANEL, height=56, corner_radius=0)
        top.pack(fill="x")
        ctk.CTkLabel(top, text="PPIX Videocoder", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color=ACCENT).pack(side="left", padx=16, pady=12)
        self.lbl_conn = ctk.CTkLabel(top, text="Non connecté", text_color=MUTED)
        self.lbl_conn.pack(side="left", padx=8)
        self.lbl_hw = ctk.CTkLabel(top, text="", text_color=MUTED)
        self.lbl_hw.pack(side="left", padx=8)
        ctk.CTkButton(top, text="Quitter", width=90, fg_color=DANGER, hover_color=DANGER_HOVER,
                      command=self._request_close).pack(side="right", padx=(4, 12))
        ctk.CTkButton(top, text="Paramètres", width=110, fg_color="#333", hover_color="#444",
                      command=self._show_settings).pack(side="right", padx=4)
        ctk.CTkButton(top, text="Historique", width=100, fg_color="#333", hover_color="#444",
                      command=self._show_history).pack(side="right", padx=4)
        nav = ctk.CTkFrame(self, fg_color=BG)
        nav.pack(fill="x", padx=14, pady=(8, 0))
        self.btn_nav_connect = ctk.CTkButton(nav, text="1. Connexion", width=140, fg_color=ACCENT,
                                             text_color="#111", hover_color=ACCENT_HOVER,
                                             command=lambda: self._show_page("connect"))
        self.btn_nav_connect.pack(side="left", padx=(0, 6))
        self.btn_nav_scan = ctk.CTkButton(nav, text="2. Scanner", width=140, fg_color="#333",
                                          state="disabled", command=lambda: self._show_page("scan"))
        self.btn_nav_scan.pack(side="left", padx=6)
        self.btn_nav_queue = ctk.CTkButton(nav, text="3. Encoder", width=170, fg_color="#333",
                                           command=lambda: self._show_page("queue"))
        self.btn_nav_queue.pack(side="left", padx=6)
        self.content = ctk.CTkFrame(self, fg_color=BG)
        self.content.pack(fill="both", expand=True, padx=14, pady=8)
        self.log = ctk.CTkTextbox(self, height=100, fg_color=CARD, text_color=TEXT)
        self.log.pack(fill="x", padx=14, pady=(0, 10))
        self.log.configure(state="disabled")
        self._show_page("connect")
        self._log(f"{APP_NAME} v{APP_VERSION} prêt.")

    def _clear_content(self):
        for w in self.content.winfo_children():
            w.destroy()
        self._queue_widgets = {}

    def _show_page(self, name: str):
        self._page = name
        self._clear_content()
        for btn, page in ((self.btn_nav_connect, "connect"), (self.btn_nav_scan, "scan"),
                          (self.btn_nav_queue, "queue")):
            if page == name:
                btn.configure(fg_color=ACCENT, text_color="#111", hover_color=ACCENT_HOVER)
            else:
                btn.configure(fg_color="#333", text_color=TEXT, hover_color="#444")
        self._update_queue_badge()
        if name == "connect":
            self._page_connect()
        elif name == "scan":
            self._page_scan()
        elif name == "queue":
            self._page_queue()

    def _update_queue_badge(self):
        active = self.queue.count("pending", "running")
        waiting = self.queue.count("done")
        label = "3. Encoder"
        if active or waiting:
            label += f" ({active}" + (f" · {waiting} à valider" if waiting else "") + ")"
        try:
            self.btn_nav_queue.configure(text=label)
        except Exception:
            pass

    def _on_queue_changed(self):
        self._update_queue_badge()
        if self._page == "queue":
            self._render_queue()
        self._update_selection_summary()

    # ------------------------------------------------------------------
    # Connexion
    # ------------------------------------------------------------------
    def _set_connected_ui(self, ok: bool, waiting: bool = False, error: bool = False):
        if waiting:
            self.lbl_conn.configure(text="Connexion…", text_color=WARN)
        elif ok:
            self.lbl_conn.configure(text=f"Connecté · {self.plex.server_name or 'OK'}", text_color=OK)
            self.btn_nav_connect.configure(text="1. Connecté")
            self.btn_nav_scan.configure(state="normal")
        elif error:
            self.lbl_conn.configure(text="Connexion échouée", text_color=ERR)
        else:
            self.lbl_conn.configure(text="Non connecté", text_color=MUTED)

    def _page_connect(self):
        f = self.content
        card = ctk.CTkFrame(f, fg_color=PANEL, corner_radius=10)
        card.pack(fill="x", pady=8)
        ctk.CTkLabel(card, text="Connexion au serveur Plex", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=TEXT).pack(anchor="w", padx=16, pady=(14, 6))
        ctk.CTkLabel(card, text="Un clic génère le code, le copie, ouvre plex.tv/link et attend la "
                                "validation automatiquement.", text_color=MUTED).pack(anchor="w", padx=16)
        self.pin_var = ctk.StringVar(master=self, value="––––")
        ctk.CTkLabel(card, textvariable=self.pin_var, font=ctk.CTkFont(size=36, weight="bold"),
                     text_color=ACCENT).pack(pady=16)
        row = ctk.CTkFrame(card, fg_color=PANEL)
        row.pack(pady=(0, 8))
        self.btn_pin = ctk.CTkButton(row, text="Se connecter via plex.tv/link", width=280, fg_color=ACCENT,
                                     text_color="#111", hover_color=ACCENT_HOVER, command=self._start_pin)
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
        ctk.CTkButton(row2, text="Se déconnecter", width=130, fg_color="#333",
                      command=self._logout).pack(side="left", padx=8)
        self.server_box = ctk.CTkFrame(f, fg_color=PANEL, corner_radius=10)
        self.server_box.pack(fill="x", pady=8)

    def _logout(self):
        self.settings["token"] = ""
        self.settings["last_server_name"] = ""
        save_settings(self.settings)
        self.plex = PlexClient()
        try:
            self.token_entry.delete(0, "end")
        except Exception:
            pass
        self.btn_nav_connect.configure(text="1. Connexion")
        self.btn_nav_scan.configure(state="disabled")
        self._set_connected_ui(False)
        self._log("Déconnecté — token supprimé.")

    def _start_pin(self):
        self._pin_stop.clear()
        self.btn_pin.configure(state="disabled")
        self._set_connected_ui(False, waiting=True)
        self.pin_status.configure(text="Génération du code…")
        threading.Thread(target=self._pin_worker, daemon=True).start()

    def _pin_worker(self):
        try:
            pin = self.plex.start_pin_login()
            self._ui(self.pin_var.set, pin)
            self.plex.open_link_and_copy(pin)
            self._ui(self.pin_status.configure, text="Navigateur ouvert — saisissez le code sur "
                                                     "plex.tv/link (copié dans le presse-papiers)…")
            self._ui(self._log, f"PIN {pin} — attente validation…")
            ok = self.plex.wait_for_pin(timeout=600, poll=1.5, stop_event=self._pin_stop)
            if self._pin_stop.is_set():
                return
            if ok:
                self.settings["token"] = self.plex.token
                save_settings(self.settings)
                self._ui(self.pin_status.configure, text="Compte validé ! Choisissez un serveur.")
                self._ui(self._log, "Compte Plex validé.")
                self._ui(self._fill_servers, self.plex.list_servers())
            else:
                self._ui(self.pin_status.configure, text="Échec ou expiration. Réessayez.")
                self._ui(self._set_connected_ui, False, error=True)
                self._ui(self._log, "PIN expiré ou refusé.")
        except Exception as e:
            self._ui(self.pin_status.configure, text=str(e))
            self._ui(self._set_connected_ui, False, error=True)
            self._ui(self._log, f"PIN erreur: {e}")
        finally:
            self._ui(lambda: self.btn_pin.winfo_exists() and self.btn_pin.configure(state="normal"))

    def _fill_servers(self, servers: List[str]):
        if not self.server_box.winfo_exists():
            return
        for w in self.server_box.winfo_children():
            w.destroy()
        if not servers:
            ctk.CTkLabel(self.server_box, text="Aucun serveur trouvé.", text_color=WARN).pack(padx=16, pady=12)
            self._set_connected_ui(False, error=True)
            return
        ctk.CTkLabel(self.server_box, text="Choisir le serveur", text_color=TEXT).pack(anchor="w", padx=16, pady=(12, 4))
        self.server_var = ctk.StringVar(master=self, value=servers[0])
        ctk.CTkOptionMenu(self.server_box, values=servers, variable=self.server_var, width=280,
                          fg_color=CARD).pack(anchor="w", padx=16, pady=4)
        ctk.CTkButton(self.server_box, text="Connecter à ce serveur", width=200, fg_color=ACCENT,
                      text_color="#111", hover_color=ACCENT_HOVER,
                      command=lambda: self._connect_server(self.server_var.get())).pack(anchor="w", padx=16, pady=(8, 14))

    def _connect_server(self, name: str):
        self._set_connected_ui(False, waiting=True)
        self._log(f"Connexion à {name}…")

        def work():
            ok = self.plex.connect_with_token(self.plex.token, name)
            if ok:
                self.settings["last_server_name"] = name
                save_settings(self.settings)
                self._ui(self._on_connected)
            else:
                self._ui(self._set_connected_ui, False, error=True)
                self._ui(self._log, f"Impossible de joindre {name} : {self.plex.last_error}")
        threading.Thread(target=work, daemon=True).start()

    def _connect_token(self):
        token = self.token_entry.get().strip()
        if not token:
            self._log("Collez un token valide.")
            return
        self._set_connected_ui(False, waiting=True)

        def work():
            ok = self.plex.connect_with_token(token, self.settings.get("last_server_name") or "")
            if ok:
                self.settings["token"] = token
                self.settings["last_server_name"] = self.plex.server_name
                save_settings(self.settings)
                self._ui(self._on_connected)
            else:
                self._ui(self._set_connected_ui, False, error=True)
                self._ui(self._log, f"Token refusé ou serveur injoignable : {self.plex.last_error}")
        threading.Thread(target=work, daemon=True).start()

    def _on_connected(self):
        self._set_connected_ui(True)
        self._log(f"Connecté au serveur « {self.plex.server_name} ».")
        self._show_page("scan")

    def _try_auto_connect(self):
        token = self.settings.get("token") or ""
        if not token:
            return
        self._set_connected_ui(False, waiting=True)
        self._log("Reconnexion…")

        def work():
            ok = self.plex.connect_with_token(token, self.settings.get("last_server_name") or "")
            if ok:
                self._ui(self._on_connected)
            else:
                self._ui(self._set_connected_ui, False, error=True)
                self._ui(self._log, f"Reconnexion impossible ({self.plex.last_error}) — reconnectez-vous.")
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------
    # Accélération matérielle
    # ------------------------------------------------------------------
    def _detect_hw(self, announce: bool = False):
        self._hw_ready.clear()
        self.lbl_hw.configure(text="GPU : test…", text_color=MUTED)

        def work():
            try:
                found = self.encoder.detect_hardware()
            except Exception as e:
                log(f"Détection matérielle: {e}", level="WARN")
                found = "none"
            self.detected_hw = found
            self._hw_ready.set()

            def apply():
                self.settings["detected_hw"] = found
                save_settings(self.settings)
                label = HARDWARE_SHORT.get(found, found)
                self.lbl_hw.configure(text=f"Encodeur : {label}", text_color=OK if found != "none" else MUTED)
                if found != "none":
                    self._log(f"Accélération matérielle opérationnelle : {label}.")
                elif announce or self.settings.get("hardware_accel") in ("auto",):
                    self._log("Aucun encodeur GPU utilisable — encodage processeur.")
            self._ui(apply)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------
    def _page_scan(self):
        f = self.content
        top = ctk.CTkFrame(f, fg_color=PANEL, corner_radius=10)
        top.pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(top, fg_color=PANEL)
        row.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkButton(row, text="Lancer le scan", width=140, fg_color=ACCENT, text_color="#111",
                      hover_color=ACCENT_HOVER, command=self._start_scan).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Arrêter", width=80, fg_color="#333", command=self._stop_scan).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Tout sélectionner", width=140, fg_color="#333",
                      command=self._select_all).pack(side="left", padx=(16, 4))
        ctk.CTkButton(row, text="Tout désélectionner", width=150, fg_color="#333",
                      command=self._deselect_all).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Aller à l'encodage →", width=160, fg_color="#333",
                      command=lambda: self._show_page("queue")).pack(side="right", padx=4)

        filt = ctk.CTkFrame(top, fg_color=PANEL)
        filt.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(filt, text="Ne pas lister :", text_color=MUTED).pack(side="left", padx=(4, 6))
        mk_labels = list(MIN_KEEP_OPTIONS.values())
        self._min_keep_label_to_key = {v: k for k, v in MIN_KEEP_OPTIONS.items()}
        self.min_keep_menu = ctk.CTkOptionMenu(filt, values=mk_labels, width=330, fg_color=CARD,
                                               command=self._on_min_keep_change)
        self.min_keep_menu.set(MIN_KEEP_OPTIONS.get(self.settings.get("min_keep_codec", "h264"), mk_labels[0]))
        self.min_keep_menu.pack(side="left")
        ctk.CTkLabel(filt, text="Rechercher :", text_color=MUTED).pack(side="left", padx=(16, 6))
        self.search_var = ctk.StringVar(master=self, value=getattr(self, "_search_text", ""))
        ent = ctk.CTkEntry(filt, textvariable=self.search_var, width=200, fg_color=CARD,
                           placeholder_text="titre, série, chemin…")
        ent.pack(side="left")
        self.search_var.trace_add("write", lambda *_: self._schedule_filter())
        libs = [ALL_LIBS] + sorted({v.library for v in self.videos})
        self.lib_var = ctk.StringVar(master=self, value=getattr(self, "_lib_filter", ALL_LIBS))
        if self.lib_var.get() not in libs:
            self.lib_var.set(ALL_LIBS)
        self.lib_menu = ctk.CTkOptionMenu(filt, values=libs, variable=self.lib_var, width=200, fg_color=CARD,
                                          command=lambda _v: self._schedule_filter(0))
        self.lib_menu.pack(side="left", padx=8)

        self.scan_progress = ctk.CTkProgressBar(top, progress_color=ACCENT, height=12)
        self.scan_progress.pack(fill="x", padx=16, pady=(6, 2))
        self.scan_progress.set(0)
        self.scan_status = ctk.CTkLabel(top, text="", text_color=MUTED)
        self.scan_status.pack(anchor="w", padx=16)
        self.sel_summary = ctk.CTkLabel(top, text="", text_color=TEXT)
        self.sel_summary.pack(anchor="w", padx=16, pady=(0, 8))
        self.results_list = ResultsList(f, self)
        self.results = self.results_list.frame
        if self.videos:
            self._render_results()
        if self.scan_thread and self.scan_thread.is_alive():
            self.scan_status.configure(text="Scan en cours…")
        self._update_selection_summary()

    def _on_min_keep_change(self, label: str):
        self.settings["min_keep_codec"] = self._min_keep_label_to_key.get(label, "h264")
        save_settings(self.settings)
        self._log("Filtre modifié — relancez le scan pour l'appliquer.")

    def _start_scan(self):
        if self.scan_thread and self.scan_thread.is_alive():
            self._log("Scan déjà en cours.")
            return
        if not self.plex.server:
            self._log("Connectez-vous d'abord à Plex.")
            return
        if not (self.settings.get("network_root") or "").strip() and not self.settings.get("network_root_asked"):
            ask_network_root_before_scan(self)
            return
        self._do_start_scan()

    def _stop_scan(self):
        if self.scan_thread and self.scan_thread.is_alive():
            self._scan_stop.set()
            self._log("Arrêt du scan demandé…")

    def _do_start_scan(self):
        self._scan_stop.clear()
        if hasattr(self, "scan_progress") and self.scan_progress.winfo_exists():
            self.scan_progress.set(0)
            self.scan_status.configure(text="Scan en cours…")
        self._log("Scan des bibliothèques…")
        preferred = self.settings.get("preferred_codec", "hevc")
        min_keep = self.settings.get("min_keep_codec", "h264")
        heavy = float(self.settings.get("heavy_h264_mbps", 0) or 0)
        t0 = time.time()

        def on_progress(done, total, msg):
            def ui():
                if self._page != "scan" or not self.scan_progress.winfo_exists():
                    return
                frac = (done / total) if total else 0
                self.scan_progress.set(min(1.0, max(0.0, frac)))
                self.scan_status.configure(text=f"{int(100 * frac)} % — {msg}")
            self._ui(ui)

        def work():
            try:
                videos = self.plex.scan_libraries(preferred_codec=preferred, min_keep_codec=min_keep,
                                                  heavy_mbps=heavy, progress_cb=on_progress,
                                                  stop_event=self._scan_stop)
                self._ui(self._on_scan_done, videos, time.time() - t0)
            except ScanCancelled:
                self._ui(self._log, "Scan arrêté.")
                self._ui(lambda: self._page == "scan" and self.scan_status.configure(text="Scan arrêté."))
            except Exception as e:
                log_exception("Scan")
                self._ui(self._log, f"Scan erreur: {e}")
        self.scan_thread = threading.Thread(target=work, daemon=True)
        self.scan_thread.start()

    def _on_scan_done(self, videos: List[VideoInfo], elapsed: float = 0.0):
        self.videos = videos
        valid = {v.key for v in videos}
        self.selected = {k: v for k, v in self.selected.items() if k in valid}
        self.codec_choice = {k: v for k, v in self.codec_choice.items() if k in valid}
        total = sum(v.file_size for v in videos)
        err = f" ({self.plex.scan_errors} erreur(s), voir journal)" if self.plex.scan_errors else ""
        self._log(f"Scan terminé en {elapsed:.0f} s : {len(videos)} fichier(s), {_fmt_size(total)}{err}.")
        self._auto_prefix_and_check(videos)
        if self._page == "scan":
            self._page_scan_refresh_libs()
            self._render_results()
            self._update_selection_summary()

    def _auto_prefix_and_check(self, videos: List[VideoInfo]):
        """Déduit le préfixe Plex si besoin et vérifie qu'un fichier est accessible."""
        root = (self.settings.get("network_root") or "").strip()
        if root and not (self.settings.get("plex_prefix") or "").strip():
            locs = []
            try:
                for s in self.plex.list_sections():
                    locs += list(getattr(s, "locations", []) or [])
            except Exception:
                pass
            pref = guess_plex_prefix(root, locs + [v.file_path for v in videos[:50]])
            if pref:
                self.settings["plex_prefix"] = pref
                save_settings(self.settings)
                self._log(f"Préfixe Plex déduit : {pref} ↔ {root}")
            else:
                self._log("Préfixe Plex introuvable : renseignez-le dans Paramètres.", level="WARN")
        if not videos:
            return
        sample = videos[0].file_path

        def check():
            local = resolve_local_path(sample, self.settings)
            ok = path_exists_for_open(local)
            if ok:
                self._ui(self._log, f"Accès fichiers OK ({local}).")
            else:
                self._ui(self._log, f"⚠ Fichier inaccessible depuis ce PC : {local} — vérifiez la racine "
                                    "réseau et le préfixe dans Paramètres.", "WARN")
        threading.Thread(target=check, daemon=True).start()

    def _page_scan_refresh_libs(self):
        try:
            libs = [ALL_LIBS] + sorted({v.library for v in self.videos})
            self.lib_menu.configure(values=libs)
            if self.lib_var.get() not in libs:
                self.lib_var.set(ALL_LIBS)
        except Exception:
            pass

    def _filtered_videos(self) -> List[VideoInfo]:
        text = (self.search_var.get() if hasattr(self, "search_var") else "").strip().lower()
        lib = self.lib_var.get() if hasattr(self, "lib_var") else ALL_LIBS
        self._search_text, self._lib_filter = text, lib
        out = []
        for v in self.videos:
            if lib != ALL_LIBS and v.library != lib:
                continue
            if text and text not in f"{v.display_title} {v.file_path}".lower():
                continue
            out.append(v)
        return out

    def _schedule_filter(self, delay: int = 300):
        if self._filter_after:
            try:
                self.after_cancel(self._filter_after)
            except Exception:
                pass
        self._filter_after = self.after(delay, self._render_results)

    def _render_results(self):
        self._filter_after = None
        if self._page != "scan" or not hasattr(self, "results_list") or not self.results_list.winfo_exists():
            return
        vids = self._filtered_videos()
        self.results_list.render(
            vids, self.selected, self.codec_choice,
            on_status=lambda s: self.scan_status.configure(text=s) if self.scan_status.winfo_exists() else None,
            on_folder=self._open_folder, on_plex=self._open_in_plex,
            on_select=self._on_result_select, on_codec=self._on_codec_change,
        )

    def _on_result_select(self, v: VideoInfo, checked: bool):
        if checked:
            job = make_job_for_video(v, self.settings, self.codec_choice.get(v.key))
            self.queue.add(job)
        else:
            self.queue.remove_pending(v.file_path)

    def _on_codec_change(self, v: VideoInfo, codec: str):
        job = self.queue.find_by_plex_path(v.file_path, statuses=("pending",))
        if job:
            job.codec = codec
            self._on_queue_changed()

    def _select_all(self):
        vids = self._filtered_videos()
        if hasattr(self, "results_list"):
            self.results_list.select_all(vids, self.selected, True)
        self._log(f"{len(vids)} fichier(s) sélectionné(s) et ajouté(s) à la file.")

    def _deselect_all(self):
        vids = self._filtered_videos()
        if hasattr(self, "results_list"):
            self.results_list.select_all(vids, self.selected, False)

    def _update_selection_summary(self):
        if self._page != "scan" or not hasattr(self, "sel_summary") or not self.sel_summary.winfo_exists():
            return
        sel = [v for v in self.videos if self.selected.get(v.key)]
        if not sel:
            total = sum(v.file_size for v in self.videos)
            self.sel_summary.configure(text=f"{len(self.videos)} fichier(s) · {_fmt_size(total)} au total"
                                       if self.videos else "")
            return
        size = sum(v.file_size for v in sel)
        est = sum(v.estimated_output_bytes(self.codec_choice.get(v.key, v.recommended_codec)) for v in sel)
        self.sel_summary.configure(
            text=f"Sélection : {len(sel)} fichier(s) · {_fmt_size(size)} · gain estimé ≈ {_fmt_size(size - est)}")

    def _remove_from_scan(self, plex_path: str):
        before = len(self.videos)
        self.videos = [v for v in self.videos if v.file_path != plex_path]
        if len(self.videos) != before and self._page == "scan":
            self._render_results()

    def _open_in_plex(self, rating_key: str):
        url = self.plex.web_url_for(rating_key)
        if not url:
            self._log("Impossible d'ouvrir Plex (serveur non connecté).")
            return
        webbrowser.open(url)

    def _open_folder(self, path: str):
        mapped = resolve_local_path(path, self.settings)

        def work():
            p = Path(mapped)
            target = str(p.parent) if p.suffix else str(p)
            if path_exists_for_open(target):
                try:
                    if os.name == "nt":
                        if path_exists_for_open(mapped):
                            subprocess.Popen(["explorer", "/select,", mapped])
                        else:
                            os.startfile(target)  # type: ignore[attr-defined]
                    else:
                        subprocess.Popen(["xdg-open", target])
                    self._ui(self._log, f"Dossier ouvert : {target}")
                    return
                except Exception as e:
                    self._ui(self._log, f"Ouverture échouée : {e}")

            def clip():
                self.clipboard_clear()
                self.clipboard_append(mapped)
                self._log(f"Chemin inaccessible : {mapped} (copié dans le presse-papiers).")
            self._ui(clip)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------
    # File d'encodage
    # ------------------------------------------------------------------
    def _page_queue(self):
        f = self.content
        top = ctk.CTkFrame(f, fg_color=PANEL, corner_radius=10)
        top.pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(top, fg_color=PANEL)
        row.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkButton(row, text="Lancer l'encodage", width=150, fg_color=OK, text_color="#111",
                      hover_color="#2bb86e", command=self._start_queue).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Arrêter", width=90, fg_color=DANGER, hover_color=DANGER_HOVER,
                      command=self._stop_queue).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Remplacer tous les validables", width=200, fg_color="#333",
                      command=self._replace_all_done).pack(side="left", padx=(16, 4))
        ctk.CTkButton(row, text="Relancer les erreurs", width=150, fg_color="#333",
                      command=lambda: self._log(f"{self.queue.retry_failed()} job(s) remis en attente.")).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Retirer les terminés", width=150, fg_color="#333",
                      command=lambda: self.queue.clear_finished()).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Vider l'attente", width=120, fg_color="#333",
                      command=self._clear_pending).pack(side="left", padx=4)
        row2 = ctk.CTkFrame(top, fg_color=PANEL)
        row2.pack(fill="x", padx=12, pady=(0, 8))
        self.queue_summary = ctk.CTkLabel(row2, text="", text_color=MUTED)
        self.queue_summary.pack(side="left", padx=4)
        if os.name == "nt":
            ctk.CTkCheckBox(row2, text="Éteindre le PC à la fin", variable=self._shutdown_var,
                            fg_color=ACCENT, command=lambda: setattr(self, "_shutdown_flag", bool(self._shutdown_var.get()))
                            ).pack(side="right", padx=4)
        if self.settings.get("dry_run"):
            ctk.CTkLabel(row2, text="MODE ESSAI ACTIF (aucun fichier écrit)", text_color=WARN).pack(side="right", padx=12)
        self.queue_list = ctk.CTkScrollableFrame(f, fg_color=PANEL, corner_radius=10)
        self.queue_list.pack(fill="both", expand=True)
        self._render_queue()

    def _queue_summary_text(self) -> str:
        jobs = self.queue.snapshot()
        if not jobs:
            return "File vide — cochez des fichiers dans « 2. Scanner »."
        pending = [j for j in jobs if j.status == "pending"]
        running = [j for j in jobs if j.status == "running"]
        replaced = [j for j in jobs if j.status == "replaced"]
        errors = sum(1 for j in jobs if j.status == "error")
        waiting = sum(1 for j in jobs if j.status == "done")
        saved = sum(j.original_size - j.output_size for j in replaced if j.output_size)
        txt = f"{len(pending)} en attente ({_fmt_size(sum(j.original_size for j in pending))})"
        if running:
            r = running[0]
            txt += f" · en cours : {r.progress:.0f} %"
            if r.eta_s:
                txt += f", reste {_fmt_eta(r.eta_s)}"
        if waiting:
            txt += f" · {waiting} à valider"
        if replaced:
            txt += f" · {len(replaced)} remplacé(s), {_fmt_size(saved)} libérés"
        if errors:
            txt += f" · {errors} erreur(s)"
        return txt

    def _render_queue(self):
        if self._page != "queue" or not hasattr(self, "queue_list") or not self.queue_list.winfo_exists():
            return
        for w in self.queue_list.winfo_children():
            w.destroy()
        self._queue_widgets = {}
        jobs = self.queue.snapshot()
        if not jobs:
            ctk.CTkLabel(self.queue_list, text="Aucun fichier dans la file.", text_color=MUTED).pack(pady=20)
        for i, job in enumerate(jobs):
            self._build_queue_row(i, job)
        try:
            self.queue_summary.configure(text=self._queue_summary_text())
        except Exception:
            pass

    @staticmethod
    def _codec_labels(job: EncodeJob):
        vinfo = getattr(job, "_video_info", None)
        old_codec = (getattr(vinfo, "video_codec", "") or "?").lower()
        old_cont = (job.source_container or "").lower()
        old = f"{old_codec}/{old_cont}" if old_cont else old_codec
        new_cont = (job.container or "").lower() or "auto"
        new = f"{CODEC_CHOICE_LABELS.get(job.codec, job.codec)} {new_cont}"
        if job.hw_used:
            new += f" · {HARDWARE_SHORT.get(job.hw_used, job.hw_used)}"
        return old, new

    def _build_queue_row(self, i: int, job: EncodeJob) -> None:
        row = ctk.CTkFrame(self.queue_list, fg_color=CARD, corner_radius=6)
        row.pack(fill="x", padx=8, pady=3)
        line1 = ctk.CTkFrame(row, fg_color=CARD)
        line1.pack(fill="x")
        name = Path(job.plex_path.replace("\\", "/") or job.video_path).name
        ctk.CTkLabel(line1, text=f"{i + 1}. {name[:52]}", width=330, anchor="w",
                     text_color=TEXT).pack(side="left", padx=6, pady=(6, 0))
        old, new = self._codec_labels(job)
        ctk.CTkLabel(line1, text=f"{old}  →  {new}", width=280, anchor="w",
                     text_color=MUTED).pack(side="left", padx=4, pady=(6, 0))
        prog = ctk.CTkProgressBar(line1, width=150, progress_color=ACCENT)
        prog.set(max(0.0, min(1.0, (job.progress or 0) / 100.0)))
        prog.pack(side="left", padx=6, pady=(6, 0))
        btns = ctk.CTkFrame(line1, fg_color=CARD, height=26)
        btns.pack(side="right", padx=4, pady=(4, 0))
        if job.status == "pending":
            for txt, cmd in (("↑", lambda j=job: self.queue.move(j, -1)),
                             ("↓", lambda j=job: self.queue.move(j, 1)),
                             ("✕", lambda j=job: self._remove_job(j))):
                ctk.CTkButton(btns, text=txt, width=30, height=24, fg_color="#333", command=cmd).pack(side="left", padx=1)
        elif job.status == "done":
            ctk.CTkButton(btns, text="Remplacer", width=86, height=24, fg_color=DANGER, hover_color=DANGER_HOVER,
                          command=lambda j=job: self._decide(j, "replace")).pack(side="left", padx=1)
            ctk.CTkButton(btns, text="Garder les 2", width=90, height=24, fg_color="#333",
                          command=lambda j=job: self._decide(j, "keep")).pack(side="left", padx=1)
            ctk.CTkButton(btns, text="Jeter", width=56, height=24, fg_color="#333",
                          command=lambda j=job: self._decide(j, "discard")).pack(side="left", padx=1)
        elif job.is_finished:
            ctk.CTkButton(btns, text="✕", width=30, height=24, fg_color="#333",
                          command=lambda j=job: self.queue.remove(j)).pack(side="left", padx=1)
        status = STATUS_LABELS.get(job.status, job.status)
        detail = job.message if job.status != "pending" else ""
        lbl = ctk.CTkLabel(row, text=f"{status}" + (f" — {detail}" if detail else ""), anchor="w",
                           justify="left", wraplength=1050, text_color=STATUS_COLORS.get(job.status, MUTED))
        lbl.pack(fill="x", padx=10, pady=(0, 6))
        self._queue_widgets[id(job)] = {"prog": prog, "lbl": lbl, "status": job.status}

    def _update_queue_job(self, job: EncodeJob) -> None:
        """Met à jour uniquement la ligne du job (pas de rebuild → pas de clignotement)."""
        if self._page != "queue":
            self._update_queue_badge()
            return
        w = self._queue_widgets.get(id(job))
        if not w or w["status"] != job.status:
            self._render_queue()
            return
        try:
            w["prog"].set(max(0.0, min(1.0, (job.progress or 0) / 100.0)))
            w["lbl"].configure(text=f"{STATUS_LABELS.get(job.status, job.status)} — {job.message}")
            self.queue_summary.configure(text=self._queue_summary_text())
        except Exception:
            pass

    def _remove_job(self, job: EncodeJob):
        self.queue.remove(job)
        self.selected[f"{job.rating_key}|{job.plex_path}"] = False
        if self._page == "scan" and hasattr(self, "results_list"):
            self.results_list.refresh_checks()

    def _clear_pending(self):
        n = self.queue.clear_pending()
        for k in list(self.selected):
            if self.selected[k] and not self.queue.find_by_plex_path(k.split("|", 1)[1]):
                self.selected[k] = False
        self._log(f"{n} job(s) retiré(s) de la file.")

    def _start_queue(self):
        if self.queue_running:
            self._log("Encodage déjà en cours.")
            return
        if not self.queue.count("pending"):
            self._log("Aucun fichier en attente.")
            return
        self.queue_running = True
        self._log("Encodage démarré" + (" (mode essai)" if self.settings.get("dry_run") else "") + ".")
        self._worker = threading.Thread(target=self._queue_worker, daemon=True)
        self._worker.start()

    def _queue_worker(self):
        stopped_by_user = False
        self._keep_awake(True)
        try:
            while self.queue_running:
                job = self.queue.next_pending()
                if job is None:
                    break
                if self.settings.get("hardware_accel") == "auto":
                    self._hw_ready.wait(timeout=60)
                finalize_job_settings(job, self.settings, self.detected_hw)
                self._ui(self._render_queue)

                def cb(p, msg, j=job):
                    j.progress = p
                    j.message = msg
                    self._ui(self._update_queue_job, j)
                self.encoder.encode(job, progress_cb=cb)
                if job.status == "cancelled":
                    stopped_by_user = True
                self._after_encode(job)
                self._ui(self._on_queue_changed)
        except Exception as e:
            log_exception("Worker")
            self._ui(self._log, f"Erreur file d'attente : {e}", "ERROR")
        finally:
            self._keep_awake(False)
            self.queue_running = False
            self._ui(self._on_queue_changed)
            self._ui(self._log, "File d'attente terminée." if not stopped_by_user else "Encodage arrêté.")
            if self._shutdown_flag and not stopped_by_user and os.name == "nt":
                self._ui(self._schedule_shutdown)

    def _after_encode(self, job: EncodeJob):
        name = Path(job.final_path or job.video_path).name
        if job.status == "error":
            self._ui(self._log, f"ERREUR {name} : {job.message}", "ERROR")
            self._record_history(job)
        elif job.status == "simulated":
            self._ui(self._log, f"[essai] {name} : " + " | ".join(job.plan))
        elif job.status == "done":
            if self.settings.get("skip_if_bigger") and job.output_size >= job.original_size:
                self.encoder.discard_output(job)
                job.status = "discarded"
                job.message = (f"Résultat plus gros que l'original ({_fmt_size(job.output_size)} ≥ "
                               f"{_fmt_size(job.original_size)}) — écarté, original conservé.")
                self._ui(self._log, f"{name} : {job.message}", "WARN")
                self._record_history(job)
            elif self.settings.get("auto_replace"):
                self._do_replace(job)
            else:
                self._ui(self._log, f"{name} encodé ({job.message}) — à valider dans « 3. Encoder ».")

    def _decide(self, job: EncodeJob, action: str):
        if job.status != "done":
            return
        job.status = "running"  # verrou visuel pendant l'opération réseau
        job.message = "Opération en cours…"
        self._render_queue()

        def work():
            job.status = "done"
            if action == "replace":
                self._do_replace(job)
            elif action == "keep":
                if self.encoder.keep_both(job):
                    self._ui(self._remove_from_scan, job.plex_path)
                self._record_history(job)
            else:
                self.encoder.discard_output(job)
                job.status = "discarded"
                job.message = "Fichier encodé supprimé, original conservé."
                self._record_history(job)
            self._ui(self._on_queue_changed)
        threading.Thread(target=work, daemon=True).start()

    def _replace_all_done(self):
        jobs = [j for j in self.queue.snapshot() if j.status == "done"]
        if not jobs:
            self._log("Aucun fichier à valider.")
            return

        def go():
            def work():
                for j in jobs:
                    if j.status == "done":
                        self._do_replace(j)
                        self._ui(self._on_queue_changed)
            threading.Thread(target=work, daemon=True).start()
        confirm(self, "Remplacer ?", f"Remplacer les {len(jobs)} original(aux) par les fichiers encodés ?\n"
                                     "Les originaux seront supprimés" +
                ("" if not self.settings.get("keep_backup") else " (une copie .ppix-bak est gardée)") + ".",
                go, yes_label="Remplacer", danger=True)

    def _do_replace(self, job: EncodeJob):
        """Exécuté hors thread UI (opérations réseau)."""
        original = job.video_path
        if not self.encoder.replace_original(job, keep_backup=bool(self.settings.get("keep_backup"))):
            self._ui(self._log, f"{Path(original).name} : {job.message}", "ERROR")
            self._record_history(job)
            return
        ext_changed = Path(original).suffix.lower() != Path(job.final_path).suffix.lower()
        if self.settings.get("refresh_plex_after", True):
            try:
                what = self.plex.refresh_after_replace(job.rating_key, job.library, plex_folder_of(job), ext_changed)
                self._ui(self._log, f"Plex : {what} pour {Path(job.final_path).name}")
            except Exception as ex:
                self._ui(self._log, f"Plex refresh: {ex}", "WARN")
        self._ui(self._log, f"{job.message} (−{job.gain_ratio * 100:.0f} %)")
        self._ui(self._remove_from_scan, job.plex_path)
        self._record_history(job)

    def _record_history(self, job: EncodeJob):
        if getattr(job, "_history_saved", False):
            return
        job._history_saved = True  # type: ignore[attr-defined]
        self.history.append({
            "time": datetime.now().isoformat(timespec="seconds"),
            "file": job.final_path or job.video_path,
            "codec": job.codec, "hw": job.hw_used, "status": job.status,
            "message": job.message, "original_size": job.original_size,
            "output_size": job.output_size, "duration_s": round(job.end_time - job.start_time, 1),
        })
        save_history(self.history)

    def _stop_queue(self):
        if not self.queue_running:
            return
        self.queue_running = False
        self.encoder.stop()
        self._log("Arrêt demandé…")

    @staticmethod
    def _keep_awake(on: bool):
        """Empêche la mise en veille Windows pendant l'encodage (thread courant)."""
        if os.name != "nt":
            return
        try:
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if on else ES_CONTINUOUS
            ctypes.windll.kernel32.SetThreadExecutionState(flags)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _schedule_shutdown(self):
        try:
            subprocess.Popen(["shutdown", "/s", "/t", "120", "/c", "Ppix-Videocoder : encodage terminé"],
                             creationflags=0x08000000)
            self._log("Arrêt du PC dans 2 minutes (tapez « shutdown /a » pour annuler).", "WARN")
        except Exception as e:
            self._log(f"Arrêt du PC impossible : {e}", "ERROR")

    # ------------------------------------------------------------------
    # Paramètres
    # ------------------------------------------------------------------
    def _show_settings(self):
        if getattr(self, "_settings_win", None) is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_force()
            return
        d = ctk.CTkToplevel(self)
        self._settings_win = d
        d.title("Paramètres")
        d.geometry("680x760")
        d.configure(fg_color=BG)
        d.transient(self)
        d.after(150, d.lift)
        scroll = ctk.CTkScrollableFrame(d, fg_color=BG)
        scroll.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        def section(title: str):
            ctk.CTkLabel(scroll, text=title, font=ctk.CTkFont(weight="bold"), text_color=ACCENT).pack(anchor="w", pady=(16, 4))

        def label(text: str):
            ctk.CTkLabel(scroll, text=text, text_color=MUTED).pack(anchor="w", pady=(8, 2))

        def option(values_labels, current, width=420):
            labels = [lab for _, lab in values_labels]
            l2v = {lab: val for val, lab in values_labels}
            cur = next((lab for val, lab in values_labels if val == current), labels[0])
            var = ctk.StringVar(master=d, value=cur)
            ctk.CTkOptionMenu(scroll, values=labels, variable=var, fg_color=CARD, width=width).pack(anchor="w")
            return lambda: l2v.get(var.get(), values_labels[0][0])

        def check(text: str, key: str, default=False):
            var = ctk.BooleanVar(master=d, value=bool(self.settings.get(key, default)))
            ctk.CTkCheckBox(scroll, text=text, variable=var, fg_color=ACCENT).pack(anchor="w", pady=4)
            return var

        s = self.settings
        section("Sélection")
        label("Codec cible")
        get_codec = option(list(CODEC_CHOICE_LABELS.items()), s.get("preferred_codec", "hevc"), 320)
        label("Ne pas lister les fichiers déjà en")
        get_minkeep = option(list(MIN_KEEP_OPTIONS.items()), s.get("min_keep_codec", "h264"))
        label("Lister aussi les fichiers H.264 trop lourds (réencodés en H.265)")
        get_heavy = option(HEAVY_H264_OPTIONS, s.get("heavy_h264_mbps", 0), 360)

        section("Vidéo")
        label("Qualité")
        nearest = min(QUALITY_OPTIONS, key=lambda x: abs(x[0] - int(s.get("crf", 24))))[0]
        get_crf = option(QUALITY_OPTIONS, nearest, 320)
        label("Vitesse d'encodage processeur (plus lent = plus petit)")
        get_preset = option([(p, p) for p in PRESETS], s.get("preset", "medium"), 200)
        label("Accélération")
        get_hw = option(list(HARDWARE_OPTIONS.items()), s.get("hardware_accel", "auto"))
        hw_row = ctk.CTkFrame(scroll, fg_color=BG)
        hw_row.pack(anchor="w", pady=4)
        det = s.get("detected_hw") or "?"
        hw_info = ctk.CTkLabel(hw_row, text=f"Détecté : {HARDWARE_SHORT.get(det, det)}", text_color=MUTED)
        hw_info.pack(side="left", padx=(0, 10))

        def retest():
            hw_info.configure(text="Test en cours…")
            self._detect_hw(announce=True)

            def wait():
                self._hw_ready.wait(30)
                found = self.detected_hw or "none"
                self._ui(lambda: hw_info.winfo_exists() and hw_info.configure(
                    text=f"Détecté : {HARDWARE_SHORT.get(found, found)}"))
            threading.Thread(target=wait, daemon=True).start()
        ctk.CTkButton(hw_row, text="Retester", width=90, fg_color="#333", command=retest).pack(side="left")
        v_fallback = check("Si le GPU échoue, recommencer automatiquement en processeur", "hw_fallback_cpu", True)
        v_deint = check("Désentrelacer automatiquement (DVD, TV)", "deinterlace", True)
        v_lowprio = check("Priorité basse (le PC reste utilisable pendant l'encodage)", "low_priority", True)

        section("Audio et sous-titres")
        label("Débit AAC (pistes réencodées, par paire de canaux)")
        get_ab = option(AUDIO_BITRATE_OPTIONS, s.get("audio_bitrate", "192k"), 300)
        label("Canaux")
        get_ch = option(AUDIO_CHANNEL_OPTIONS, int(s.get("audio_channels", 0)), 360)
        v_keep_a = check("Copier l'audio sans le réencoder s'il est compatible Plex (AAC, AC3, E-AC3, MP3, FLAC)",
                         "keep_audio_copy", True)
        v_subs = check("Conserver les sous-titres", "keep_subtitles", True)

        section("Fichiers")
        v_mkv = check("Conteneur MKV pour AVI/WMV/MPG/TS (recommandé)", "mkv_for_legacy", True)
        v_auto_r = check("Remplacer automatiquement l'original après validation du fichier encodé", "auto_replace")
        v_bak = check("Garder une copie de l'original (.ppix-bak) après remplacement", "keep_backup")
        v_bigger = check("Écarter le résultat s'il est plus gros que l'original", "skip_if_bigger")
        v_refresh = check("Mettre à jour Plex après remplacement", "refresh_plex_after", True)
        v_dry = check("Mode essai (simulation, aucun fichier écrit)", "dry_run")

        section("Accès fichiers (NAS → Windows)")
        ctk.CTkLabel(scroll, text=("Plex voit les fichiers avec ses propres chemins (ex. /volume1/Media/…). "
                                   "Indiquez le partage Windows correspondant. Laissez vide si Plex tourne "
                                   "sur ce PC."),
                     text_color=MUTED, wraplength=600, justify="left").pack(anchor="w", pady=(0, 6))
        label("Racine réseau Windows")
        e_root = ctk.CTkEntry(scroll, width=500, placeholder_text=r"\\192.168.1.10\Media", fg_color=CARD)
        e_root.pack(anchor="w")
        if s.get("network_root"):
            e_root.insert(0, s["network_root"])
        label("Préfixe chemin Plex correspondant")
        e_pref = ctk.CTkEntry(scroll, width=500, placeholder_text="/volume1/Media", fg_color=CARD)
        e_pref.pack(anchor="w")
        if s.get("plex_prefix"):
            e_pref.insert(0, s["plex_prefix"])
        status_detect = ctk.CTkLabel(scroll, text="", text_color=MUTED, wraplength=600, justify="left")
        status_detect.pack(anchor="w", pady=(6, 2))

        def do_auto_detect_root():
            if not self.plex.server:
                status_detect.configure(text="Connectez-vous d'abord au serveur Plex.", text_color=WARN)
                return
            status_detect.configure(text="Recherche en cours…", text_color=WARN)

            def work():
                try:
                    info = self.plex.detect_path_mapping()
                except Exception as e:
                    info = {"network_root": "", "plex_prefix": "", "message": str(e)}

                def apply():
                    if not d.winfo_exists():
                        return
                    root = (info.get("network_root") or "").strip()
                    pref = (info.get("plex_prefix") or "").strip()
                    if root:
                        e_root.delete(0, "end")
                        e_root.insert(0, root)
                    if pref:
                        e_pref.delete(0, "end")
                        e_pref.insert(0, pref)
                    status_detect.configure(text=(info.get("message") or "")[:500],
                                            text_color=TEXT if (root or pref) else WARN)
                self._ui(apply)
            threading.Thread(target=work, daemon=True).start()

        def do_test_path():
            sample = self.videos[0].file_path if self.videos else ""
            if not sample:
                status_detect.configure(text="Scannez d'abord pour tester un fichier réel.", text_color=WARN)
                return
            tmp = dict(self.settings, network_root=e_root.get().strip(), plex_prefix=e_pref.get().strip())
            local = resolve_local_path(sample, tmp)
            status_detect.configure(text=f"Test : {local}…", text_color=MUTED)

            def work():
                ok = path_exists_for_open(local)
                self._ui(lambda: status_detect.winfo_exists() and status_detect.configure(
                    text=("✔ Accessible : " if ok else "✘ Introuvable : ") + local,
                    text_color="#3DDC84" if ok else WARN))
            threading.Thread(target=work, daemon=True).start()

        btn_row = ctk.CTkFrame(scroll, fg_color=BG)
        btn_row.pack(anchor="w", pady=8)
        ctk.CTkButton(btn_row, text="Autodétecter", width=140, fg_color=ACCENT, text_color="#111",
                      hover_color=ACCENT_HOVER, command=do_auto_detect_root).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Tester sur un fichier du scan", width=220, fg_color="#333",
                      command=do_test_path).pack(side="left")

        section("Journal de diagnostic")
        log_row = ctk.CTkFrame(scroll, fg_color=BG)
        log_row.pack(anchor="w", pady=8)

        def copy_log():
            self.clipboard_clear()
            self.clipboard_append(read_log())
            self._log("Journal copié.")

        def show_log_window():
            w = ctk.CTkToplevel(d)
            w.title("Journal")
            w.geometry("900x520")
            w.configure(fg_color=BG)
            box = ctk.CTkTextbox(w, fg_color=CARD, text_color=TEXT, wrap="none")
            box.pack(fill="both", expand=True, padx=10, pady=10)
            box.insert("1.0", read_log())
            box.see("end")
            box.configure(state="disabled")
        ctk.CTkButton(log_row, text="Ouvrir dossier", width=120, fg_color="#333",
                      command=lambda: self._log(f"Journal : {open_log_folder()}")).pack(side="left", padx=4)
        ctk.CTkButton(log_row, text="Voir", width=80, fg_color="#333", command=show_log_window).pack(side="left", padx=4)
        ctk.CTkButton(log_row, text="Copier journal", width=130, fg_color="#333", command=copy_log).pack(side="left", padx=4)
        ctk.CTkButton(log_row, text="Effacer", width=90, fg_color=DANGER, hover_color=DANGER_HOVER,
                      command=lambda: (clear_log(), self._log("Journal effacé."))).pack(side="left", padx=4)
        ctk.CTkButton(scroll, text="À propos", width=120, fg_color="#333",
                      command=lambda: show_about(d)).pack(anchor="w", pady=(8, 8))

        def save():
            s["preferred_codec"] = get_codec()
            s["min_keep_codec"] = get_minkeep()
            s["heavy_h264_mbps"] = get_heavy()
            s["crf"] = get_crf()
            s["preset"] = get_preset()
            s["hardware_accel"] = get_hw()
            s["hw_fallback_cpu"] = v_fallback.get()
            s["deinterlace"] = v_deint.get()
            s["low_priority"] = v_lowprio.get()
            s["audio_bitrate"] = get_ab()
            s["audio_channels"] = get_ch()
            s["keep_audio_copy"] = v_keep_a.get()
            s["keep_subtitles"] = v_subs.get()
            s["mkv_for_legacy"] = v_mkv.get()
            s["auto_replace"] = v_auto_r.get()
            s["keep_backup"] = v_bak.get()
            s["skip_if_bigger"] = v_bigger.get()
            s["refresh_plex_after"] = v_refresh.get()
            s["dry_run"] = v_dry.get()
            new_root, new_pref = e_root.get().strip(), e_pref.get().strip()
            if new_root != s.get("network_root"):
                s["network_root_asked"] = True
            s["network_root"], s["plex_prefix"] = new_root, new_pref
            if s["preferred_codec"] not in SUPPORTED_CODECS:
                s["preferred_codec"] = "hevc"
            save_settings(s)
            self._log("Paramètres enregistrés (appliqués aux prochains encodages).")
            d.destroy()
            if self._page in ("queue", "scan"):
                self._show_page(self._page)

        bottom = ctk.CTkFrame(d, fg_color=BG)
        bottom.pack(fill="x", pady=10)
        ctk.CTkButton(bottom, text="Enregistrer", fg_color=ACCENT, text_color="#111", hover_color=ACCENT_HOVER,
                      command=save).pack(side="right", padx=16)
        ctk.CTkButton(bottom, text="Annuler", fg_color="#333", command=d.destroy).pack(side="right")

    # ------------------------------------------------------------------
    # Historique
    # ------------------------------------------------------------------
    def _show_history(self):
        d = ctk.CTkToplevel(self)
        d.title("Historique")
        d.geometry("860x480")
        d.configure(fg_color=BG)
        d.transient(self)
        d.after(150, d.lift)
        replaced = [e for e in self.history if e.get("status") == "replaced"]
        saved = sum((e.get("original_size") or 0) - (e.get("output_size") or 0) for e in replaced)
        ctk.CTkLabel(d, text=f"{len(replaced)} fichier(s) remplacé(s) au total · {_fmt_size(saved)} libérés",
                     text_color=ACCENT, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(12, 0))
        scroll = ctk.CTkScrollableFrame(d, fg_color=PANEL)
        scroll.pack(fill="both", expand=True, padx=12, pady=12)
        if not self.history:
            ctk.CTkLabel(scroll, text="Aucun encodage encore.", text_color=MUTED).pack(pady=20)
            return
        for e in reversed(self.history[-200:]):
            o, n = e.get("original_size") or 0, e.get("output_size") or 0
            gain = f"−{(1 - n / o) * 100:.0f} %" if o and n else ""
            name = Path(str(e.get("file", "")).replace("\\", "/")).name[:50]
            st = e.get("status", "")
            line = (f"{e.get('time', '')[:16].replace('T', ' ')}  |  {name}  |  {e.get('codec', '')}  |  "
                    f"{STATUS_LABELS.get(st, st)}  {gain}")
            ctk.CTkLabel(scroll, text=line, anchor="w", text_color=STATUS_COLORS.get(st, TEXT)).pack(fill="x", padx=6, pady=1)


def _excepthook(exc_type, exc, tb):
    log("".join(traceback.format_exception(exc_type, exc, tb)), level="ERROR")


sys.excepthook = _excepthook
