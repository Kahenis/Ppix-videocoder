"""Boîtes de dialogue : racine réseau avant scan, confirmation."""
from __future__ import annotations

import threading
from typing import Callable

import customtkinter as ctk

from .config import save_settings
from .theme import ACCENT, ACCENT_HOVER, BG, CARD, MUTED, OK_DARK, TEXT, WARN


def _modal(parent, title: str, geometry: str) -> ctk.CTkToplevel:
    box = ctk.CTkToplevel(parent)
    box.title(title)
    box.geometry(geometry)
    box.configure(fg_color=BG)
    box.transient(parent)
    box.lift()
    box.after(150, lambda: _grab(box))
    return box


def _grab(box) -> None:
    try:
        if box.winfo_exists():
            box.grab_set()
            box.focus_force()
    except Exception:
        pass


def _close(box) -> None:
    try:
        box.grab_release()
    except Exception:
        pass
    try:
        box.destroy()
    except Exception:
        pass


def confirm(parent, title: str, message: str, on_yes: Callable[[], None],
            yes_label: str = "Oui", no_label: str = "Annuler", danger: bool = False) -> None:
    box = _modal(parent, title, "480x230")
    ctk.CTkLabel(box, text=message, wraplength=440, justify="left", text_color=TEXT).pack(
        padx=18, pady=18, fill="both", expand=True)
    row = ctk.CTkFrame(box, fg_color=BG)
    row.pack(pady=(0, 14))

    def yes():
        _close(box)
        on_yes()

    ctk.CTkButton(row, text=yes_label, fg_color="#5C1A1A" if danger else ACCENT,
                  text_color="#fff" if danger else "#111",
                  hover_color="#8A3618" if danger else ACCENT_HOVER, command=yes).pack(side="left", padx=6)
    ctk.CTkButton(row, text=no_label, fg_color="#333", command=lambda: _close(box)).pack(side="left", padx=6)
    box.protocol("WM_DELETE_WINDOW", lambda: _close(box))


def ask_network_root_before_scan(app) -> None:
    """Propose l'autodétection de la racine réseau ; l'utilisateur peut corriger,
    ou continuer sans (Plex sur ce PC / chemins déjà accessibles)."""
    box = _modal(app, "Racine réseau", "600x360")
    ctk.CTkLabel(
        box,
        text=(
            "Aucun chemin réseau n'est défini.\n"
            "Si Plex tourne sur un NAS, indiquez le partage Windows correspondant,\n"
            "par exemple \\\\MON-NAS\\Videos  ou  \\\\192.168.1.10\\Media."
        ),
        wraplength=560, justify="left", text_color=TEXT,
    ).pack(padx=18, pady=(18, 8), anchor="w")
    status = ctk.CTkLabel(box, text="", text_color=MUTED, wraplength=560, justify="left")
    status.pack(padx=18, pady=4, anchor="w")
    entry = ctk.CTkEntry(box, width=540, fg_color=CARD, placeholder_text=r"\\IP_ou_NAS\Partage")
    entry.pack(padx=18, pady=8)
    detected = {"prefix": ""}

    def accept_and_scan(root: str):
        root = (root or "").strip()
        if not root:
            status.configure(text="Indiquez un chemin réseau, ou « Continuer sans ».", text_color=WARN)
            return
        app.settings["network_root"] = root
        if detected["prefix"] and not (app.settings.get("plex_prefix") or "").strip():
            app.settings["plex_prefix"] = detected["prefix"]
        app.settings["network_root_asked"] = True
        save_settings(app.settings)
        app._log(f"Racine réseau : {root}")
        _close(box)
        app._do_start_scan()

    def skip_and_scan():
        app.settings["network_root_asked"] = True
        save_settings(app.settings)
        _close(box)
        app._do_start_scan()

    def on_detect():
        status.configure(text="Recherche en cours…", text_color=WARN)

        def work():
            try:
                info = app.plex.detect_path_mapping()
            except Exception as e:
                info = {"network_root": "", "plex_prefix": "", "message": str(e)}

            def apply():
                if not box.winfo_exists():
                    return
                root = (info.get("network_root") or "").strip()
                cands = info.get("candidates") or []
                detected["prefix"] = (info.get("plex_prefix") or "").strip()
                value = root or (cands[0] if cands else "")
                if value:
                    entry.delete(0, "end")
                    entry.insert(0, value)
                msg = info.get("message") or "Saisie manuelle requise."
                if value:
                    msg += "\nCorrigez si besoin puis cliquez « Utiliser ce chemin »."
                status.configure(text=msg, text_color=TEXT if value else WARN)

            app._ui(apply)

        threading.Thread(target=work, daemon=True).start()

    row = ctk.CTkFrame(box, fg_color=BG)
    row.pack(pady=12)
    ctk.CTkButton(row, text="Chercher automatiquement", width=190, fg_color=ACCENT, text_color="#111",
                  hover_color=ACCENT_HOVER, command=on_detect).pack(side="left", padx=4)
    ctk.CTkButton(row, text="Utiliser ce chemin", width=150, fg_color=OK_DARK, text_color="#fff",
                  command=lambda: accept_and_scan(entry.get())).pack(side="left", padx=4)
    ctk.CTkButton(row, text="Continuer sans", width=130, fg_color="#333",
                  command=skip_and_scan).pack(side="left", padx=4)
    box.protocol("WM_DELETE_WINDOW", lambda: _close(box))
