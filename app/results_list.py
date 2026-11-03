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
            self.outer, fg_color="#1E1E1E", corner_radius=8, label_text="Fichiers à optimiser"
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
        self._on_codec: Optional[Callable[[VideoInfo, str], None]] = None
        self._videos: List[VideoInfo] = []
        # Sélection multiple (Maj / Ctrl)
        self._order: List[str] = []                 # clés des fichiers dans l'ordre affiché
        self._by_key: Dict[str, VideoInfo] = {}
        self._groups: Dict[int, List[str]] = {}     # index d'en-tête → clés du groupe
        self.anchor: Optional[str] = None           # point de départ pour Maj + clic
        self.row_boxes: Dict[str, Any] = {}

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
        self.row_boxes = {}

    def render(
        self,
        videos: List[VideoInfo],
        selected: dict,
        codec_choice: dict,
        on_status: Callable[[str], None],
        on_folder,
        on_plex,
        on_select: Optional[Callable[[VideoInfo, bool], None]] = None,
        on_codec: Optional[Callable[[VideoInfo, str], None]] = None,
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
        self._on_codec = on_codec
        self._videos = videos
        self._page = 0
        self.clear()
        self._items = []

        if not videos:
            ctk.CTkLabel(self.frame, text="Aucun fichier à afficher.", text_color=MUTED).pack(pady=20)
            self._update_nav()
            try:
                on_status("Aucun fichier à optimiser.")
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
            self._order = [v.key for kind, _, v in items if kind == "row"]
            self._by_key = {v.key: v for kind, _, v in items if kind == "row"}
            self._groups = {}
            for i, (kind, _, _v) in enumerate(items):
                if kind == "row":
                    continue
                keys = []
                for kind2, _, v2 in items[i + 1:]:
                    if kind2 == "h_lib" or (kind == "h_series" and kind2 == "h_series"):
                        break
                    if kind2 == "row":
                        keys.append(v2.key)
                self._groups[i] = keys
            if self.anchor not in self._by_key:
                self.anchor = None
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
        for offset, (kind, title, video) in enumerate(slice_):
            idx = start + offset
            if kind == "h_lib":
                lbl = ctk.CTkLabel(
                    self.frame,
                    text=f"  {title}",
                    font=self._font(15),
                    text_color=ACCENT,
                    cursor="hand2",
                )
                lbl.pack(anchor="w", pady=(12, 4), padx=8)
                lbl.bind("<Button-1>", lambda _e, i=idx: self.toggle_group(i))
            elif kind == "h_series":
                lbl = ctk.CTkLabel(
                    self.frame,
                    text=f"    {title}",
                    font=self._font(13),
                    text_color=TEXT,
                    cursor="hand2",
                )
                lbl.pack(anchor="w", padx=12, pady=(6, 2))
                lbl.bind("<Button-1>", lambda _e, i=idx: self.toggle_group(i))
            else:
                self._row(video)

        try:
            if self._on_status:
                self._on_status(
                    f"{len(self._videos)} fichier(s) à optimiser — page {self._page + 1}/{self._page_count()}"
                )
        except Exception:
            pass

    def _row(self, v: VideoInfo) -> None:
        selected = self._selected
        codec_choice = self._codec_choice
        key = v.key
        if key not in selected:
            selected[key] = False
        if key not in codec_choice:
            codec_choice[key] = v.recommended_codec or "hevc"

        row = ctk.CTkFrame(self.frame, fg_color=CARD, corner_radius=6, height=36)
        row.pack(fill="x", padx=8, pady=2)

        var = ctk.BooleanVar(master=self.app, value=bool(selected.get(key, False)))
        self.row_vars[key] = var
        # Case à cocher dessinée : le clic est géré ici pour connaître Maj / Ctrl
        box = ctk.CTkButton(row, text="", width=24, height=24, corner_radius=5, border_width=2,
                            border_color=ACCENT, hover_color="#3a3a3a", fg_color="transparent",
                            text_color="#111")
        box.pack(side="left", padx=8, pady=4)
        self.row_boxes[key] = box
        var.trace_add("write", lambda *_a, k=key: self._paint(k))
        self._paint(key)

        title = ctk.CTkLabel(
            row, text=(v.display_title or "")[:58], anchor="w", width=280, text_color=TEXT,
        )
        title.pack(side="left", padx=4)
        for w in (box, row, title):
            w.bind("<Button-1>", lambda e, video=v: self.click(video, e), add="+")

        cont = (v.container or "?").upper()
        if v.audio_only:
            ch = f" {v.audio_channels}ch" if v.audio_channels else ""
            ctk.CTkLabel(row, text=f"audio TrueHD{ch} / {cont}", text_color=WARN, width=150,
                         anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(row, text=f"{v.resolution_label} · {v.size_label} · {v.duration_str}",
                         text_color=MUTED, width=170, anchor="w").pack(side="left", padx=4)
            ctk.CTkLabel(row, text="→ AC3 640k", width=110, text_color=TEXT).pack(side="left", padx=4)
            self._row_buttons(row, v)
            return
        extra = f" · {v.bitrate / 1000:.0f} Mb/s" if v.reason == "débit élevé" and v.bitrate else ""
        l1 = ctk.CTkLabel(row, text=f"{v.video_codec or '?'} / {cont}{extra}", text_color=WARN, width=150,
                          anchor="w")
        l1.pack(side="left", padx=4)
        l2 = ctk.CTkLabel(
            row,
            text=f"{v.resolution_label} · {v.size_label} · {v.duration_str}",
            text_color=MUTED,
            width=170,
            anchor="w",
        )
        l2.pack(side="left", padx=4)
        for w in (l1, l2):
            w.bind("<Button-1>", lambda e, video=v: self.click(video, e), add="+")

        cur = codec_choice.get(key, "hevc")
        if cur not in CODEC_CHOICE_LABELS:
            cur = "hevc"
        btn = ctk.CTkButton(
            row, text=CODEC_CHOICE_LABELS[cur], width=110, height=26,
            fg_color="#333", hover_color="#444",
        )

        def cycle(k=key, b=btn, video_for_cycle=v):
            order = list(CODEC_CHOICE_LABELS.keys())
            c0 = codec_choice.get(k, "hevc")
            try:
                n = order[(order.index(c0) + 1) % len(order)]
            except ValueError:
                n = order[0]
            codec_choice[k] = n
            b.configure(text=CODEC_CHOICE_LABELS[n])
            if self._on_codec:
                try:
                    self._on_codec(video_for_cycle, n)
                except Exception:
                    pass

        btn.configure(command=cycle)
        btn.pack(side="left", padx=4)

        self._row_buttons(row, v)

    def _row_buttons(self, row, v: VideoInfo) -> None:
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
            k = v.key
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
                var.set(bool(selected.get(k, False)))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Sélection multiple
    # ------------------------------------------------------------------
    def _font(self, size: int):
        cache = self.__dict__.setdefault("_fonts", {})
        if size not in cache:
            cache[size] = ctk.CTkFont(size=size, weight="bold")
        return cache[size]

    def _paint(self, key: str) -> None:
        box = self.row_boxes.get(key)
        if box is None:
            return
        on = bool(self._selected.get(key, False))
        try:
            box.configure(text="✓" if on else "", fg_color=ACCENT if on else "transparent",
                          hover_color="#C48A0B" if on else "#3a3a3a")
        except Exception:
            pass

    def apply(self, keys: List[str], value: bool) -> int:
        """Coche/décoche des fichiers (toutes pages) ; retourne le nombre de changements."""
        n = 0
        for k in keys:
            if bool(self._selected.get(k, False)) == value:
                continue
            self._selected[k] = value
            n += 1
            v = self._by_key.get(k)
            if v is not None and self._on_select:
                try:
                    self._on_select(v, value)
                except Exception:
                    pass
        self.refresh_checks()
        return n

    def click(self, video: VideoInfo, event=None) -> str:
        """Clic : coche/décoche. Maj + clic : toute la plage depuis le dernier clic.
        Ctrl + clic : un seul fichier (nouveau point de départ)."""
        state = int(getattr(event, "state", 0) or 0)
        shift, ctrl = bool(state & 0x0001), bool(state & 0x0004)
        key = video.key
        value = not bool(self._selected.get(key, False))
        if shift and not ctrl and self.anchor in self._by_key and key in self._by_key:
            a, b = self._order.index(self.anchor), self._order.index(key)
            lo, hi = min(a, b), max(a, b)
            self.apply(self._order[lo:hi + 1], value)
        else:
            self.apply([key], value)
            self.anchor = key
        self._report()
        return "break"

    def toggle_group(self, item_index: int) -> None:
        keys = self._groups.get(item_index, [])
        if not keys:
            return
        value = not all(self._selected.get(k, False) for k in keys)
        self.apply(keys, value)
        self._report()

    def select_visible(self, value: bool) -> None:
        self.apply(list(self._order), value)
        self._report()

    def _report(self) -> None:
        n = sum(1 for k in self._order if self._selected.get(k))
        if self._on_status:
            try:
                self._on_status(f"{n} fichier(s) coché(s) sur {len(self._order)} — Maj + clic : plage, "
                                "Ctrl + clic : un fichier, Ctrl + A : tout, Échap : rien")
            except Exception:
                pass

    def refresh_checks(self) -> None:
        for k, var in self.row_vars.items():
            try:
                var.set(bool(self._selected.get(k, False)))
            except Exception:
                pass
