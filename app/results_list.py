"""Batched results list for scan page (avoids UI freeze / gray window)."""
from __future__ import annotations

from typing import Dict, List, Callable, Any

import customtkinter as ctk

from .config import CODEC_CHOICE_LABELS
from .plex_client import VideoInfo

ACCENT = "#E5A00D"
CARD = "#262626"
TEXT = "#F2F2F2"
MUTED = "#A0A0A0"
WARN = "#FFB020"


class ResultsList:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.frame = ctk.CTkScrollableFrame(
            parent, fg_color="#1E1E1E", corner_radius=10, label_text="Fichiers a optimiser"
        )
        self.frame.pack(fill="both", expand=True)
        self._token = 0
        self.row_vars: Dict[str, Any] = {}

    def winfo_exists(self):
        try:
            return self.frame.winfo_exists()
        except Exception:
            return False

    def clear(self):
        for w in self.frame.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

    def render(
        self,
        videos: List[VideoInfo],
        selected: dict,
        codec_choice: dict,
        on_status: Callable[[str], None],
        on_folder,
        on_plex,
    ):
        self._token += 1
        token = self._token
        self.row_vars = {}
        self.clear()
        if not videos:
            ctk.CTkLabel(self.frame, text="Aucun fichier a afficher.", text_color=MUTED).pack(pady=20)
            return

        wait = ctk.CTkLabel(
            self.frame,
            text="Veuillez patienter, construction de la liste…",
            font=ctk.CTkFont(size=14),
            text_color=WARN,
        )
        wait.pack(pady=24, padx=16)
        on_status("Construction de la liste…")

        items = []
        groups: Dict[str, Dict[str, List[VideoInfo]]] = {}
        for v in videos:
            lib = v.library
            key = v.series_title if v.library_type == "show" else "_movies_"
            groups.setdefault(lib, {}).setdefault(key, []).append(v)
        for lib, series_dict in sorted(groups.items()):
            items.append(("h_lib", lib, None))
            for series, vids in sorted(series_dict.items()):
                if series != "_movies_":
                    items.append(("h_series", series, None))
                for v in sorted(vids, key=lambda x: (x.season_number, x.episode_number, x.title)):
                    items.append(("row", None, v))

        total = len(items)
        batch = 12
        app = self.app

        def step(idx=0):
            if token != self._token or getattr(app, "_closing", False):
                return
            if idx == 0:
                try:
                    wait.destroy()
                except Exception:
                    pass
            end = min(idx + batch, total)
            for i in range(idx, end):
                kind, title, video = items[i]
                if kind == "h_lib":
                    ctk.CTkLabel(
                        self.frame, text=f"  {title}",
                        font=ctk.CTkFont(size=15, weight="bold"), text_color=ACCENT,
                    ).pack(anchor="w", pady=(12, 4), padx=8)
                elif kind == "h_series":
                    ctk.CTkLabel(
                        self.frame, text=f"    {title}",
                        font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT,
                    ).pack(anchor="w", padx=12, pady=(6, 2))
                else:
                    self._row(video, selected, codec_choice, on_folder, on_plex)
            pct = int(100 * end / total) if total else 100
            on_status(f"Affichage {end}/{total} ({pct} %)")
            if end < total:
                app.after(1, lambda: step(end))
            else:
                on_status(f"{len(videos)} fichier(s) a optimiser.")

        app.after(10, lambda: step(0))

    def _row(self, v, selected, codec_choice, on_folder, on_plex):
        key = f"{v.rating_key}|{v.file_path}"
        if key not in selected:
            selected[key] = False
        if key not in codec_choice:
            codec_choice[key] = v.recommended_codec or "hevc"

        row = ctk.CTkFrame(self.frame, fg_color=CARD, corner_radius=6, height=36)
        row.pack(fill="x", padx=8, pady=2)
        var = ctk.BooleanVar(master=self.app, value=bool(selected.get(key, False)))
        self.row_vars[key] = var

        def on_toggle(k=key, bv=var):
            selected[k] = bool(bv.get())

        ctk.CTkCheckBox(
            row, text="", variable=var, command=on_toggle, width=28,
            fg_color=ACCENT, hover_color="#C48A0B",
        ).pack(side="left", padx=6, pady=4)
        ctk.CTkLabel(row, text=v.display_title[:58], anchor="w", width=280, text_color=TEXT).pack(side="left", padx=4)
        rec = CODEC_CHOICE_LABELS.get(v.recommended_codec, v.recommended_codec)
        ctk.CTkLabel(row, text=f"{v.video_codec} → {rec}", text_color=WARN, width=170).pack(side="left", padx=4)
        ctk.CTkLabel(
            row, text=f"{v.width}x{v.height} · {v.size_gb:.2f} Go · {v.duration_str}",
            text_color=MUTED, width=150,
        ).pack(side="left", padx=4)

        cur = codec_choice.get(key, "hevc")
        if cur not in CODEC_CHOICE_LABELS:
            cur = "hevc"
        btn = ctk.CTkButton(
            row, text=CODEC_CHOICE_LABELS[cur], width=110, height=26,
            fg_color="#333", hover_color="#444",
        )

        def cycle(k=key, b=btn):
            order = list(CODEC_CHOICE_LABELS.keys())
            c0 = codec_choice.get(k, "hevc")
            try:
                n = order[(order.index(c0) + 1) % len(order)]
            except ValueError:
                n = order[0]
            codec_choice[k] = n
            b.configure(text=CODEC_CHOICE_LABELS[n])

        btn.configure(command=cycle)
        btn.pack(side="left", padx=4)
        ctk.CTkButton(
            row, text="Dossier", width=70, height=26, fg_color="#333",
            command=lambda p=v.file_path: on_folder(p),
        ).pack(side="right", padx=2)
        ctk.CTkButton(
            row, text="Plex", width=50, height=26, fg_color="#333",
            command=lambda rk=v.rating_key: on_plex(rk),
        ).pack(side="right", padx=2)

    def select_all(self, videos, selected, value: bool):
        for v in videos:
            k = f"{v.rating_key}|{v.file_path}"
            selected[k] = value
            var = self.row_vars.get(k)
            if var is not None:
                try:
                    var.set(value)
                except Exception:
                    pass
