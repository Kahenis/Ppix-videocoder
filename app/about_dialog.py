"""Dialogue À propos — Ppix-Videocoder."""
from __future__ import annotations

import customtkinter as ctk

from .config import APP_NAME, APP_VERSION

ACCENT = "#E5A00D"
BG = "#161616"
PANEL = "#1E1E1E"
TEXT = "#F2F2F2"


def show_about(parent) -> None:
    box = ctk.CTkToplevel(parent)
    box.title("À propos")
    box.geometry("520x440")
    box.configure(fg_color=BG)
    box.transient(parent)
    box.lift()
    try:
        box.grab_set()
    except Exception:
        pass
    text = (
        f"{APP_NAME}  v{APP_VERSION}\n\n"
        "Logiciel d'optimisation des codecs vidéo pour les bibliothèques Plex.\n\n"
        "Ppix-Videocoder détecte les fichiers dont le codec n'est pas optimal "
        "pour la lecture directe sur Plex (Direct Play), et les réencode en "
        "H.265 (x265) ou H.264 (x264) avec FFmpeg inclus dans l'application.\n\n"
        "Fonctions principales :\n"
        "• Connexion Plex via code PIN (plex.tv/link)\n"
        "• Scan Films et Séries (groupement par série)\n"
        "• File d'attente avec progression et arrêt\n"
        "• Accélération NVIDIA / Intel / AMD ou CPU\n"
        "• Remplacement optionnel du fichier d'origine\n\n"
        "Adresse de contact de l'auteur : jfrancois@guilard.com"
    )
    body = ctk.CTkTextbox(box, wrap="word", fg_color=PANEL, text_color=TEXT)
    body.pack(fill="both", expand=True, padx=14, pady=(14, 8))
    body.insert("1.0", text)
    body.configure(state="disabled")

    def close():
        try:
            box.grab_release()
        except Exception:
            pass
        box.destroy()

    ctk.CTkButton(
        box, text="Fermer", fg_color=ACCENT, text_color="#111",
        hover_color="#C48A0B", command=close,
    ).pack(pady=(0, 12))
    box.protocol("WM_DELETE_WINDOW", close)
