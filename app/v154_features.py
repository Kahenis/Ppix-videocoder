"""v1.5.4 features: network root prompt, auto-queue on checkbox."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from .config import (
    SUPPORTED_CODECS, save_settings, path_exists_for_open, is_legacy_container,
    is_audio_safe_for_plex,
)
from .plex_client import VideoInfo
from .encoder import EncodeJob

ACCENT = "#E5A00D"
BG = "#161616"
TEXT = "#F2F2F2"
MUTED = "#A0A0A0"
WARN = "#FFB020"


def ask_network_root_before_scan(app) -> None:
    """Propose auto-detection of network_root; user can edit before accepting."""
    box = ctk.CTkToplevel(app)
    box.title("Racine réseau")
    box.geometry("560x320")
    box.configure(fg_color=BG)
    box.transient(app)
    try:
        box.grab_set()
    except Exception:
        pass
    ctk.CTkLabel(
        box,
        text=(
            "Vous n'avez pas saisi de chemin réseau dans les paramètres.\n"
            "Exemple attendu : \\\\Nazgul\\Pour-tous  ou  \\\\192.168.1.240\\Pour-tous"
        ),
        wraplength=520,
        justify="left",
        text_color=TEXT,
    ).pack(padx=18, pady=(18, 8))
    status = ctk.CTkLabel(box, text="", text_color=MUTED, wraplength=520, justify="left")
    status.pack(padx=18, pady=4)
    entry = ctk.CTkEntry(box, width=480, fg_color=CARD, placeholder_text=r"\\IP_ou_NAS\Partage")
    entry.pack(padx=18, pady=8)

    def close_box():
        try:
            box.grab_release()
        except Exception:
            pass
        try:
            box.destroy()
        except Exception:
            pass

    def on_no():
        close_box()
        app._show_settings()

    def accept_and_scan(root: str, pref: str = ""):
        root = (root or "").strip()
        if not root:
            status.configure(text="Indiquez un chemin réseau valide.", text_color=WARN)
            return
        app.settings["network_root"] = root
        if pref and not (app.settings.get("plex_prefix") or "").strip():
            app.settings["plex_prefix"] = pref
        save_settings(app.settings)
        app._log(f"Racine réseau : {root}")
        close_box()
        app._do_start_scan()

    def on_use_entry():
        accept_and_scan(entry.get())

    def on_yes():
        status.configure(text="Recherche en cours…", text_color=WARN)
        box.update_idletasks()

        def work():
            try:
                info = app.plex.detect_path_mapping()
            except Exception as e:
                info = {"network_root": "", "plex_prefix": "", "message": str(e)}

            def apply():
                root = (info.get("network_root") or "").strip()
                pref = (info.get("plex_prefix") or "").strip()
                msg = info.get("message") or ""
                if root:
                    entry.delete(0, "end")
                    entry.insert(0, root)
                    status.configure(
                        text=msg + "\nCorrigez si besoin puis cliquez « Utiliser ce chemin ».",
                        text_color=TEXT,
                    )
                    # store prefix for accept
                    box._detected_prefix = pref  # type: ignore
                else:
                    status.configure(
                        text=(msg or "Récupération impossible.")
                        + "\nSaisissez le chemin manuellement ou ouvrez les paramètres.",
                        text_color=WARN,
                    )
                    box._detected_prefix = ""  # type: ignore

            app._ui(apply)

        threading.Thread(target=work, daemon=True).start()

    def on_use_detected():
        accept_and_scan(entry.get(), getattr(box, "_detected_prefix", "") or "")

    row = ctk.CTkFrame(box, fg_color=BG)
    row.pack(pady=12)
    ctk.CTkButton(
        row, text="Chercher automatiquement", width=180,
        fg_color=ACCENT, text_color="#111", hover_color="#C48A0B", command=on_yes,
    ).pack(side="left", padx=4)
    ctk.CTkButton(
        row, text="Utiliser ce chemin", width=150,
        fg_color=OK if False else "#2E7D4F", text_color="#fff", command=on_use_detected,
    ).pack(side="left", padx=4)
    ctk.CTkButton(
        row, text="Paramètres", width=110,
        fg_color="#333", command=on_no,
    ).pack(side="left", padx=4)
    box.protocol("WM_DELETE_WINDOW", on_no)


def make_job_for_video(app, v: VideoInfo) -> Optional[EncodeJob]:
    key = f"{v.rating_key}|{v.file_path}"
    codec = app.codec_choice.get(key, app.settings.get("preferred_codec", "hevc"))
    if codec not in SUPPORTED_CODECS:
        codec = "hevc"
    mapped = app._resolve_path(v.file_path)
    src_for_encode = mapped if path_exists_for_open(mapped) else v.file_path
    cont = app.settings.get("container", "mp4")
    src_cont = (v.container or Path(v.file_path).suffix.lstrip(".")).lower()
    if app.settings.get("mkv_for_legacy", True) and is_legacy_container(src_cont):
        cont = "mkv"
    out_ext = ".mkv" if cont == "mkv" else ".mp4"
    out_path = str(Path(src_for_encode).with_suffix(f".optimized{out_ext}"))
    keep_copy = bool(app.settings.get("keep_audio_copy", False))
    if keep_copy and not is_audio_safe_for_plex(v.audio_codec):
        app._log(
            f"Audio « {v.audio_codec or '?'} » non Direct Play Plex "
            f"pour {Path(v.file_path).name} → réencodage AAC."
        )
    job = EncodeJob(
        video_path=src_for_encode,
        output_path=out_path,
        codec=codec,
        crf=int(app.settings.get("crf", 24)),
        preset=app.settings.get("preset", "medium"),
        audio_bitrate=app.settings.get("audio_bitrate", "192k"),
        audio_channels=int(app.settings.get("audio_channels", 2)),
        keep_audio_copy=keep_copy,
        hardware=app.settings.get("hardware_accel", "none"),
        container=cont,
        dry_run=bool(app.settings.get("dry_run", False)),
        source_audio_codec=v.audio_codec or "",
        source_container=src_cont,
    )
    job._video_info = v  # type: ignore
    job._plex_path = v.file_path  # type: ignore
    return job


def queue_add_video(app, v: VideoInfo) -> bool:
    try:
        if any(
            (getattr(j, "_plex_path", None) == v.file_path or j.video_path == v.file_path)
            and j.status in ("pending", "running")
            for j in app.queue
        ):
            return False
        job = make_job_for_video(app, v)
        if not job:
            return False
        app.queue.append(job)
        app._update_queue_badge()
        return True
    except Exception as e:
        app._log(f"Erreur ajout file: {e}")
        return False


def queue_remove_video(app, v: VideoInfo) -> bool:
    before = len(app.queue)
    mapped = app._resolve_path(v.file_path)
    app.queue = [
        j for j in app.queue
        if not (
            j.status == "pending"
            and (
                getattr(j, "_plex_path", None) == v.file_path
                or j.video_path == v.file_path
                or j.video_path == mapped
            )
        )
    ]
    removed = before != len(app.queue)
    if removed:
        app._update_queue_badge()
    return removed


def on_result_select(app, video: VideoInfo, checked: bool) -> None:
    if checked:
        queue_add_video(app, video)
    else:
        queue_remove_video(app, video)
