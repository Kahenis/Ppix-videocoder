"""Paginated results list for scan page.

With thousands of files, creating one CTk widget per row freezes the UI
(Windows "Not Responding" + gray progress bar). We only materialize one
page of rows at a time so the app stays responsive.
"""
from __future__ import annotations

from typing import Dict, List, Callable, Any, Optional, Tuple

import customtkinter as ctk

from .config import CODEC_CHOICE_LABELS
from .plex_client import VideoInfo

ACCENT = "#E5A00D"
CARD = "#262626"
TEXT = "#F2F2F2"
MUTED = "#A0A0A0"
WARN = "#FFB020"
PANEL = "#1E1E1E"

# Rows per page — keep low so page switches stay instant
PAGE_SIZE = 120


class ResultsList:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app

        # Outer container
        self.outer = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=10)
        self.outer.pack(fill="both", expand=True)

        # Nav bar (page controls)
        self.nav = ctk.CTkFrame(self.outer, fg_color=PANEL, height=40)
        self.nav.pack(fill="x", padx=8, pady=(8, 0))

        self.btn_prev = ctk.CTkButton(
            self.nav, text="← Précédent", width=110, height=28,
            fg_color="#333", hover_color="#444", command=self._prev_page, state="disabled",
        )
        self.btn_prev.pack(side="left", padx=(4, 6))

        self.lbl_page = ctk.CTkLabel(self.nav, text="", text_color=MUTED)
        self.lbl_page.pack(side="left", padx=8)

        self.btn_next = ctk.CTkButton(
            self.nav, text="Suivant →", width=110, height=28,
            fg_color="#333", hover_color="#444", command=self._next_page, state="disabled",
        )
        self.btn_next.pack(side="left", padx=6)

        # Scrollable area for the current page only
        self.frame = ctk.CTkScrollableFrame(
            self.outer, fg_color="#1E1E1E", corner_radius=8, label_text="Fichiers a optimiser"
        )
        self.frame.pack(fill="both", expand=True, padx=4, pady=4)

        self._token = 0
        self.row_vars: Dict[str, Any] = {}
        self._items: List[Tuple[str, Optional[str], Optional[VideoInfo]]] = []
        self._page = 0
        self._selected: dict = {}
        self._codec_choice: dict = {}
        self._on_folder = None
        self._on_plex = None
        self._on_status: Optional[Callable[[str], None]] = None
        self._on_select: Optional[Callable[[VideoInfo, bool], None]] = None
        self._videos: List[VideoInfo] = []

    def winfo_exists(self) -> bool:
        try:
            return bool(self.outer.winfo_exists())
        except Exception:
            return False

    def clear(self) -> None:
        for w in list(self.frame.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        self.row_vars = {}

    def render(
        self,
        videos: List[VideoInfo],
        selected: dict,
        codec_choice: dict,
        on_status: Callable[[str], None],
        on_folder,
        on_plex,
        on_select: Optional[Callable[[VideoInfo, bool], None]] = None,
    ) -> None:
        """Prepare data and show page 0. Heavy sorting is deferred one tick
        so the status message can paint first."""
        self._token += 1
        token = self._token
        self._selected = selected
        self._codec_choice = codec_choice
        self._on_folder = on_folder
        self._on_plex = on_plex
        self._on_status = on_status
        self._on_select = on_select
        self._videos = videos
        self._page = 0
        self.clear()
        self._items = []

        if not videos:
            ctk.CTkLabel(self.frame, text="Aucun fichier a afficher.", text_color=MUTED).pack(pady=20)
            self._update_nav()
            try:
                on_status("Aucun fichier a optimiser.")
            except Exception:
                pass
            return

        try:
            on_status("Veuillez patienter, tri en cours…")
        except Exception:
            pass

        wait = ctk.CTkLabel(
            self.frame,
            text="Veuillez patienter, tri en cours…",
            font=ctk.CTkFont(size=14),
            text_color=WARN,
        )
        wait.pack(pady=24, padx=16)

        app = self.app

        def prepare():
            if token != self._token or getattr(app, "_closing", False):
                return
            # Group + sort (CPU only)
            groups: Dict[str, Dict[str, List[VideoInfo]]] = {}
            for v in videos:
                lib = v.library or "Bibliothèque"
                key = v.series_title if v.library_type == "show" else "_movies_"
                groups.setdefault(lib, {}).setdefault(key, []).append(v)

            items: List[Tuple[str, Optional[str], Optional[VideoInfo]]] = []
            for lib, series_dict in sorted(groups.items()):
                items.append(("h_lib", lib, None))
                for series, vids in sorted(series_dict.items()):
                    if series != "_movies_":
                        items.append(("h_series", series, None))
                    for v in sorted(
                        vids,
                        key=lambda x: (x.season_number or 0, x.episode_number or 0, x.title or ""),
                    ):
                        items.append(("row", None, v))

            self._items = items
            try:
                wait.destroy()
            except Exception:
                pass
            self._show_page(0)

        # Let the wait label paint before sorting
        app.after(30, prepare)

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    def _page_count(self) -> int:
        n = len(self._items)
        if n == 0:
            return 1
        return (n + PAGE_SIZE - 1) // PAGE_SIZE

    def _update_nav(self) -> None:
        total_pages = self._page_count()
        page = self._page
        n_videos = len(self._videos)
        n_items = len(self._items)

        if n_items == 0:
            self.lbl_page.configure(text="")
            self.btn_prev.configure(state="disabled")
            self.btn_next.configure(state="disabled")
            return

        start = page * PAGE_SIZE + 1
        end = min((page + 1) * PAGE_SIZE, n_items)
        self.lbl_page.configure(
            text=f"Page {page + 1} / {total_pages}  ·  lignes {start}–{end} / {n_items}  ·  {n_videos} fichier(s)"
        )
        self.btn_prev.configure(state="normal" if page > 0 else "disabled")
        self.btn_next.configure(state="normal" if page < total_pages - 1 else "disabled")

    def _prev_page(self) -> None:
        if self._page > 0:
            self._show_page(self._page - 1)

    def _next_page(self) -> None:
        if self._page < self._page_count() - 1:
            self._show_page(self._page + 1)

    def _show_page(self, page: int) -> None:
        """Destroy current widgets and build only the requested page."""
        if getattr(self.app, "_closing", False):
            return
        self._page = max(0, min(page, self._page_count() - 1))
        self.clear()
        self._update_nav()

        start = self._page * PAGE_SIZE
        end = min(start + PAGE_SIZE, len(self._items))
        slice_ = self._items[start:end]

        if not slice_:
            ctk.CTkLabel(self.frame, text="Aucune ligne sur cette page.", text_color=MUTED).pack(pady=20)
            return

        # Build the page in one go — only ~120 rows, fast enough
        for kind, title, video in slice_:
            if kind == "h_lib":
                ctk.CTkLabel(
                    self.frame,
                    text=f"  {title}",
                    font=ctk.CTkFont(size=15, weight="bold"),
                    text_color=ACCENT,
                ).pack(anchor="w", pady=(12, 4), padx=8)
            elif kind == "h_series":
                ctk.CTkLabel(
                    self.frame,
                    text=f"    {title}",
                    font=ctk.CTkFont(size=13, weight="bold"),
                    text_color=TEXT,
                ).pack(anchor="w", padx=12, pady=(6, 2))
            else:
                self._row(video)

        try:
            if self._on_status:
                self._on_status(
                    f"{len(self._videos)} fichier(s) a optimiser — page {self._page + 1}/{self._page_count()}"
                )
        except Exception:
            pass

    def _row(self, v: VideoInfo) -> None:
        selected = self._selected
        codec_choice = self._codec_choice
        key = f"{v.rating_key}|{v.file_path}"
        if key not in selected:
            selected[key] = False
        if key not in codec_choice:
            codec_choice[key] = v.recommended_codec or "hevc"

        row = ctk.CTkFrame(self.frame, fg_color=CARD, corner_radius=6, height=36)
        row.pack(fill="x", padx=8, pady=2)

        var = ctk.BooleanVar(master=self.app, value=bool(selected.get(key, False)))
        self.row_vars[key] = var

        def on_toggle(k=key, bv=var, video=v):
            checked = bool(bv.get())
            selected[k] = checked
            if self._on_select:
                try:
                    self._on_select(video, checked)
                except Exception:
                    pass

        ctk.CTkCheckBox(
            row, text="", variable=var, command=on_toggle, width=28,
            fg_color=ACCENT, hover_color="#C48A0B",
        ).pack(side="left", padx=6, pady=4)

        ctk.CTkLabel(
            row, text=(v.display_title or "")[:58], anchor="w", width=280, text_color=TEXT,
        ).pack(side="left", padx=4)

        rec = CODEC_CHOICE_LABELS.get(v.recommended_codec, v.recommended_codec or "")
        ctk.CTkLabel(row, text=f"{v.video_codec} → {rec}", text_color=WARN, width=170).pack(
            side="left", padx=4
        )
        ctk.CTkLabel(
            row,
            text=f"{v.width}x{v.height} · {v.size_gb:.2f} Go · {v.duration_str}",
            text_color=MUTED,
            width=150,
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
            command=lambda p=v.file_path: self._on_folder(p) if self._on_folder else None,
        ).pack(side="right", padx=2)
        ctk.CTkButton(
            row, text="Plex", width=50, height=26, fg_color="#333",
            command=lambda rk=v.rating_key: self._on_plex(rk) if self._on_plex else None,
        ).pack(side="right", padx=2)

    def select_all(self, videos, selected, value: bool) -> None:
        """Select / deselect the entire result set (all pages)."""
        for v in videos:
            k = f"{v.rating_key}|{v.file_path}"
            was = bool(selected.get(k, False))
            selected[k] = value
            if was != value and self._on_select:
                try:
                    self._on_select(v, value)
                except Exception:
                    pass
        # Update visible checkboxes only
        for k, var in self.row_vars.items():
            try:
                var.set(value)
            except Exception:
                pass
