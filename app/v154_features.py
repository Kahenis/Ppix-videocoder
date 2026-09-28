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
    """Propose auto-detection of network_root if not configured."""
    box = ctk.CTkToplevel(app)
    box.title("Racine réseau")
    box.geometry("520x220")
    box.configure(fg_color=BG)
    box.transient(app)
    try:
        box.grab_set()
    except Exception:
        pass
    ctk.CTkLabel(
        box,
        text=(
            "Vous n'avez pas saisi de chemin réseau dans les paramètres.\n\n"
            "Voulez-vous que je le cherche automatiquement ?"
        ),
        wraplength=480,
        justify="left",
        text_color=TEXT,
    ).pack(padx=18, pady=(18, 10))
    status = ctk.CTkLabel(box, text="", text_color=MUTED)
    status.pack(padx=18, pady=4)

    def on_no():
        try:
            box.grab_release()
        except Exception:
            pass
        box.destroy()
        app._show_settings()

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
                if root:
                    app.settings["network_root"] = root
                    if pref and not (app.settings.get("plex_prefix") or "").strip():
                        app.settings["plex_prefix"] = pref
                    save_settings(app.settings)
                    app._log(info.get("message") or f"Racine réseau : {root}")
                    try:
                        box.grab_release()
                    except Exception:
                        pass
                    box.destroy()
                    app._do_start_scan()
                else:
                    status.configure(
                        text="Récupération impossible, retour dans les paramètres",
                        text_color=WARN,
                    )
                    app._log(info.get("message") or "Récupération automatique impossible.")

                    def go_settings():
                        try:
                            box.grab_release()
                        except Exception:
                            pass
                        try:
                            box.destroy()
                        except Exception:
                            pass
                        app._show_settings()

                    app.after(2000, go_settings)

            app._ui(apply)

        threading.Thread(target=work, daemon=True).start()

    row = ctk.CTkFrame(box, fg_color=BG)
    row.pack(pady=14)
    ctk.CTkButton(
        row, text="Oui, chercher automatiquement", width=220,
        fg_color=ACCENT, text_color="#111", hover_color="#C48A0B", command=on_yes,
    ).pack(side="left", padx=6)
    ctk.CTkButton(
        row, text="Non, ouvrir les paramètres", width=200,
        fg_color="#333", command=on_no,
    ).pack(side="left", padx=6)
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
