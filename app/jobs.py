"""Création des jobs d'encodage et gestion thread-safe de la file."""
from __future__ import annotations

import threading
from pathlib import PurePath
from typing import Callable, List, Optional

from .config import (
    SUPPORTED_CODECS, apply_path_maps, parent_dir, replace_extension, resolve_output_container,
)
from .encoder import WORK_DIR, EncodeJob
from .plex_client import VideoInfo


def resolve_local_path(plex_path: str, settings: dict) -> str:
    return apply_path_maps(
        plex_path,
        path_maps=settings.get("path_maps") or [],
        network_root=settings.get("network_root") or "",
        plex_prefix=settings.get("plex_prefix") or "",
    )


def temp_output_path(local_src: str, container: str) -> str:
    """Fichier de travail dans un sous-dossier caché : Film.avi → .ppix-travail/Film.avi.mkv.

    Le dossier contient un .plexignore : Plex ne voit jamais le fichier en cours.
    Le nom complet de la source est conservé pour que Film.avi et Film.mkv
    n'utilisent jamais le même fichier temporaire.
    """
    sep_pos = max(local_src.rfind("/"), local_src.rfind("\\"))
    folder, name = (local_src[:sep_pos + 1], local_src[sep_pos + 1:]) if sep_pos >= 0 else ("", local_src)
    sep = local_src[sep_pos] if sep_pos >= 0 else "/"
    return f"{folder}{WORK_DIR}{sep}{name}.{container}"


def make_job_for_video(v: VideoInfo, settings: dict, codec: Optional[str] = None) -> EncodeJob:
    """Crée un job sans accès disque (le chemin local est résolu au lancement)."""
    codec = codec or settings.get("preferred_codec", "hevc")
    if codec not in SUPPORTED_CODECS:
        codec = "hevc"
    src_cont = (v.container or PurePath(v.file_path.replace("\\", "/")).suffix.lstrip(".")).lower()
    job = EncodeJob(
        video_path=v.file_path,
        codec=codec,
        source_audio_codec=v.audio_codec or "",
        source_container=src_cont,
        plex_path=v.file_path,
        rating_key=v.rating_key,
        library=v.library,
    )
    job.original_size = v.file_size or 0
    job.audio_only = bool(getattr(v, "audio_only", False))
    job._video_info = v  # type: ignore[attr-defined]
    return job


def finalize_job_settings(job: EncodeJob, settings: dict, detected_hw: Optional[str]) -> None:
    """Applique les paramètres au moment du lancement (les réglages modifiés
    après l'ajout à la file sont pris en compte)."""
    from .config import resolve_hardware
    local = resolve_local_path(job.plex_path or job.video_path, settings)
    job.video_path = local
    cont = resolve_output_container(job.source_container, settings)
    job.container = cont
    job.output_path = temp_output_path(local, cont)
    job.final_path = replace_extension(local, cont)
    job.crf = int(settings.get("crf", 24))
    job.preset = settings.get("preset", "medium")
    job.audio_bitrate = settings.get("audio_bitrate", "192k")
    job.audio_channels = int(settings.get("audio_channels", 0))
    job.keep_audio_copy = bool(settings.get("keep_audio_copy", True))
    job.keep_subtitles = bool(settings.get("keep_subtitles", True))
    job.deinterlace = bool(settings.get("deinterlace", True))
    job.hardware = resolve_hardware(settings.get("hardware_accel", "none"), detected_hw)
    job.hw_fallback = bool(settings.get("hw_fallback_cpu", True))
    job.low_priority = bool(settings.get("low_priority", True))
    job.dry_run = bool(settings.get("dry_run", False))


def plex_folder_of(job: EncodeJob) -> str:
    return parent_dir(job.plex_path) if job.plex_path else ""


class JobQueue:
    """Liste de jobs protégée par un verrou (le worker et l'UI y accèdent)."""

    def __init__(self, on_change: Optional[Callable[[], None]] = None):
        self._jobs: List[EncodeJob] = []
        self._lock = threading.RLock()
        self.on_change = on_change

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)

    def snapshot(self) -> List[EncodeJob]:
        with self._lock:
            return list(self._jobs)

    def find_by_plex_path(self, plex_path: str, statuses=None) -> Optional[EncodeJob]:
        with self._lock:
            for j in self._jobs:
                if j.plex_path == plex_path and (statuses is None or j.status in statuses):
                    return j
        return None

    def add(self, job: EncodeJob) -> bool:
        with self._lock:
            if any(j.plex_path == job.plex_path and j.status in ("pending", "running", "done")
                   for j in self._jobs):
                return False
            # un ancien job terminé (erreur/annulé/simulé) pour ce fichier est remplacé
            self._jobs = [j for j in self._jobs if j.plex_path != job.plex_path]
            self._jobs.append(job)
        self._changed()
        return True

    def remove_pending(self, plex_path: str) -> bool:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if not (j.plex_path == plex_path and j.status == "pending")]
            removed = len(self._jobs) != before
        if removed:
            self._changed()
        return removed

    def remove(self, job: EncodeJob) -> None:
        with self._lock:
            self._jobs = [j for j in self._jobs if j is not job]
        self._changed()

    def move(self, job: EncodeJob, delta: int) -> None:
        with self._lock:
            if job not in self._jobs:
                return
            i = self._jobs.index(job)
            k = max(0, min(len(self._jobs) - 1, i + delta))
            self._jobs.insert(k, self._jobs.pop(i))
        self._changed()

    def next_pending(self) -> Optional[EncodeJob]:
        with self._lock:
            for j in self._jobs:
                if j.status == "pending":
                    j.status = "running"
                    return j
        return None

    def clear_finished(self) -> int:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if not j.is_finished or j.awaiting_decision]
            n = before - len(self._jobs)
        if n:
            self._changed()
        return n

    def clear_pending(self) -> int:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if j.status != "pending"]
            n = before - len(self._jobs)
        if n:
            self._changed()
        return n

    def retry_failed(self) -> int:
        n = 0
        with self._lock:
            for j in self._jobs:
                if j.status in ("error", "cancelled", "simulated"):
                    j.status, j.progress, j.message = "pending", 0.0, ""
                    n += 1
        if n:
            self._changed()
        return n

    def count(self, *statuses: str) -> int:
        with self._lock:
            return sum(1 for j in self._jobs if j.status in statuses)
