"""Affiches pour les vidéos personnelles.

Plex fabrique l'affiche d'une vidéo perso (sans base de données d'affiches)
en sautant à une image-clé. Si le fichier a trop peu d'images-clés, ou si Plex
n'arrive pas à décoder l'image sur le NAS, l'affiche est noire. On extrait
donc nous-mêmes une image représentative (décodage précis, en évitant les
images noires ou blanches) et on l'envoie à Plex.
"""
from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Tuple

from .logger import log

# Agents Plex « sans affiches officielles » : vidéos personnelles
PERSONAL_AGENTS = {"tv.plex.agents.none", "com.plexapp.agents.none"}

_WIN = os.name == "nt"
_NO_WINDOW = 0x08000000


def _luma_at(ffmpeg: str, path: str, t: float) -> Optional[float]:
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
           "-vf", "scale=96:-2,format=gray,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
           "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, creationflags=_NO_WINDOW if _WIN else 0)
    except Exception:
        return None
    for line in (p.stdout or "").splitlines():
        if "YAVG=" in line:
            try:
                return float(line.split("YAVG=", 1)[1])
            except ValueError:
                return None
    return None


def choose_poster_time(ffmpeg: str, path: str, duration: float) -> float:
    """Premier instant « bien exposé » parmi plusieurs positions ; sinon le plus lumineux."""
    if duration <= 0:
        return 0.0
    fracs = (0.30, 0.15, 0.45, 0.60, 0.20, 0.75, 0.10, 0.90)
    best: Tuple[float, float] = (-1.0, duration * 0.3)
    for f in fracs:
        t = duration * f
        y = _luma_at(ffmpeg, path, t)
        if y is None:
            continue
        if 40 <= y <= 200:
            return t
        score = y if y < 200 else 255 - y
        if score > best[0]:
            best = (score, t)
    return best[1]


def extract_poster(ffmpeg: str, video: str, out_jpg: str, duration: float, width: int = 1280) -> bool:
    """Extrait une image JPEG représentative (décodage précis, pas une image-clé approchée)."""
    t = choose_poster_time(ffmpeg, video, duration)
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
           "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", "-update", "1", out_jpg]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=180, creationflags=_NO_WINDOW if _WIN else 0)
    except Exception as e:
        log(f"Extraction d'affiche impossible : {e}", level="WARN")
        return False
    ok = p.returncode == 0 and os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 1000
    if not ok:
        log(f"Extraction d'affiche échouée ({video}) : {(p.stderr or '').strip()[-300:]}", level="WARN")
    return ok


def is_black_image(path: str, threshold: float = 22.0) -> Optional[bool]:
    """Vrai si l'image (affiche téléchargée depuis Plex) est quasi noire."""
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as im:
            im = im.convert("L")
            im.thumbnail((128, 128))
            mean = ImageStat.Stat(im).mean[0]
        return mean < threshold
    except Exception:
        return None


def personal_sections(server) -> List[object]:
    try:
        return [s for s in server.library.sections()
                if s.type in ("movie", "show") and (getattr(s, "agent", "") or "") in PERSONAL_AGENTS]
    except Exception as e:
        log(f"Bibliothèques perso : {e}", level="WARN")
        return []
