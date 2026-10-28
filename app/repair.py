"""Réparation des fichiers déjà convertis : affiches Plex et réencodage.

- Affiches (vidéos perso) : extraction d'une vraie image + envoi à Plex.
- Réencodage : fichiers avec trop peu d'images-clés (anciennes versions),
  remis dans la file avec une image-clé toutes les 2 s.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Callable, Dict, List, Optional

from .config import reverse_path_map
from .encoder import KEYFRAME_MAX_GAP, Encoder
from .logger import log
from .plex_client import PlexClient, VideoInfo
from .posters import extract_poster


@dataclass
class ConvertedFile:
    local_path: str
    plex_path: str
    library: str = ""
    rating_key: str = ""
    library_type: str = "movie"
    personal: bool = False
    exists: bool = False
    video_codec: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    size: int = 0
    keyframe_gap: Optional[float] = None
    in_plex: bool = False

    @property
    def name(self) -> str:
        return PurePath(self.local_path.replace("\\", "/")).name

    @property
    def needs_reencode(self) -> bool:
        return self.keyframe_gap is not None and self.keyframe_gap > KEYFRAME_MAX_GAP

    @property
    def keyframe_label(self) -> str:
        if not self.exists:
            return "introuvable"
        if self.keyframe_gap is None:
            return "–"
        return f"{self.keyframe_gap:.0f} s" + (" ⚠" if self.needs_reencode else "")


def history_files(history: List[dict]) -> List[dict]:
    """Fichiers remplacés par l'application, dernier passage d'abord, sans doublon."""
    seen, out = set(), []
    for e in reversed(history):
        if e.get("status") not in ("replaced", "done"):
            continue
        f = e.get("file") or ""
        key = f.replace("\\", "/").lower()
        if f and key not in seen:
            seen.add(key)
            out.append(e)
    return out


def analyze_converted(plex: PlexClient, encoder: Encoder, settings: dict, history: List[dict],
                      progress: Optional[Callable[[int, int, str], None]] = None,
                      stop_event: Optional[threading.Event] = None) -> List[ConvertedFile]:
    entries = history_files(history)
    index: Dict[str, object] = {}
    if plex.server:
        if progress:
            progress(0, len(entries) or 1, "Lecture des bibliothèques Plex…")
        index = plex.build_file_index(stop_event=stop_event)
    out: List[ConvertedFile] = []
    for i, e in enumerate(entries):
        if stop_event is not None and stop_event.is_set():
            break
        local = e.get("file") or ""
        cf = ConvertedFile(local_path=local, plex_path=e.get("plex_path") or reverse_path_map(local, settings),
                           library=e.get("library") or "", rating_key=str(e.get("rating_key") or ""))
        if progress:
            progress(i, len(entries), f"Analyse de {cf.name}…")
        item = plex.lookup(index, cf.plex_path) if index else None
        if item is not None:
            cf.in_plex = True
            cf.rating_key = str(item.ratingKey)
            cf.library = getattr(item, "librarySectionTitle", "") or cf.library
            cf.library_type = "show" if getattr(item, "type", "") == "episode" else "movie"
        if cf.library:
            cf.personal = plex.is_personal_library(cf.library)
        cf.exists = os.path.exists(local)
        if cf.exists:
            info = encoder.probe(local)
            v = next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"), {})
            cf.video_codec = (v.get("codec_name") or "").lower()
            cf.width, cf.height = int(v.get("width") or 0), int(v.get("height") or 0)
            cf.duration = encoder.probe_duration(info)
            cf.size = os.path.getsize(local)
            cf.keyframe_gap = encoder.keyframe_max_gap(local, cf.duration)
        out.append(cf)
    if progress:
        progress(len(entries), len(entries) or 1, f"{len(out)} fichier(s) analysé(s)")
    return out


def video_info_for(cf: ConvertedFile) -> VideoInfo:
    ext = PurePath(cf.local_path.replace("\\", "/")).suffix.lstrip(".").lower()
    return VideoInfo(rating_key=cf.rating_key, title=PurePath(cf.name).stem, library=cf.library,
                     library_type=cf.library_type, file_path=cf.plex_path, file_size=cf.size,
                     duration_ms=int(cf.duration * 1000), video_codec=cf.video_codec, container=ext,
                     width=cf.width, height=cf.height, reason="images-clés")


def fix_poster(plex: PlexClient, encoder: Encoder, local_path: str, plex_path: str, library: str,
               rating_key: str = "", wait_s: float = 0.0, only_personal: bool = True) -> str:
    """Extrait une image du fichier et l'envoie comme affiche Plex. Retourne un message."""
    if not plex.server:
        return "Plex non connecté"
    if only_personal and library and not plex.is_personal_library(library):
        return ""   # bibliothèque avec affiches officielles : on ne touche à rien
    item = None
    deadline = time.time() + wait_s
    while True:
        item = plex.find_item_by_file(plex_path, library, rating_key)
        if item is not None or time.time() >= deadline:
            break
        time.sleep(5)   # Plex n'a pas encore indexé le fichier remplacé
    if item is None:
        return f"élément Plex introuvable pour {PurePath(plex_path.replace(chr(92), '/')).name}"
    if only_personal and not library:
        lib = getattr(item, "librarySectionTitle", "") or ""
        if lib and not plex.is_personal_library(lib):
            return ""
    duration = encoder.probe_duration(encoder.probe(local_path))
    fd, jpg = tempfile.mkstemp(prefix="ppix-affiche-", suffix=".jpg")
    os.close(fd)
    try:
        if not extract_poster(encoder.ffmpeg_path, local_path, jpg, duration):
            return "extraction de l'image impossible"
        if not plex.upload_poster(item, jpg):
            return "envoi à Plex impossible"
        log(f"Affiche envoyée à Plex : {local_path}")
        return "affiche mise à jour"
    finally:
        try:
            Path(jpg).unlink()
        except Exception:
            pass
