"""Configuration and settings management for Ppix-Videocoder."""
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "Ppix-Videocoder"
APP_VERSION = "1.3.3"
CLIENT_IDENTIFIER = "ppix-videocoder-windows-v1"

SUPPORTED_CODECS = {
    "hevc": {
        "label": "H.265 / x265 (recommandé)",
        "ffmpeg_v": "libx265",
        "ffmpeg_a": "aac",
        "container": "mp4",
        "tag": "hvc1",
    },
    "h264": {
        "label": "H.264 / x264 (plus compatible)",
        "ffmpeg_v": "libx264",
        "ffmpeg_a": "aac",
        "container": "mp4",
        "tag": "avc1",
    },
}

CODEC_CHOICE_LABELS = {
    "hevc": "H.265 (x265)",
    "h264": "H.264 (x264)",
}

CODEC_RANK = {
    "mpeg1video": 5, "mpeg2video": 10, "mpeg2": 10, "mpeg4": 15,
    "msmpeg4": 12, "msmpeg4v2": 12, "msmpeg4v3": 12,
    "wmv1": 8, "wmv2": 8, "wmv3": 10, "vc1": 12,
    "vp6": 12, "vp6f": 12, "vp8": 25, "vp9": 45, "av1": 70,
    "theora": 15, "flv1": 8, "h263": 8,
    "rv10": 5, "rv20": 5, "rv30": 8, "rv40": 10,
    "rawvideo": 0, "prores": 40, "dnxhd": 35, "cineform": 35,
    "h264": 50, "avc": 50,
    "hevc": 60, "h265": 60,
}

MIN_KEEP_OPTIONS = {
    "h264": "H.264 et mieux (ne pas lister H.264 / H.265)",
    "hevc": "H.265 uniquement (lister tout sauf H.265)",
    "none": "Tout lister (aucun filtre)",
}

NON_OPTIMAL_CODECS = {
    "mpeg2video", "mpeg2", "mpeg4", "msmpeg4", "msmpeg4v2", "msmpeg4v3",
    "wmv1", "wmv2", "wmv3", "vc1", "vp6", "vp6f", "vp8", "theora",
    "flv1", "h263", "rv10", "rv20", "rv30", "rv40", "rawvideo",
    "prores", "dnxhd", "cineform",
}

QUALITY_OPTIONS = [
    (18, "Excellente (fichier plus lourd)"),
    (20, "Très bonne"),
    (22, "Bonne"),
    (24, "Équilibrée (recommandé)"),
    (26, "Correcte (fichier plus léger)"),
    (28, "Économique (fichier léger)"),
]

AUDIO_BITRATE_OPTIONS = [
    ("96k", "96 kb/s — basique"),
    ("128k", "128 kb/s — standard"),
    ("160k", "160 kb/s — bonne"),
    ("192k", "192 kb/s — recommandée"),
    ("256k", "256 kb/s — haute"),
    ("320k", "320 kb/s — maximale"),
]

PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
]

HARDWARE_OPTIONS = {
    "none": "Aucune — processeur seul (compatible partout)",
    "nvenc": "Carte NVIDIA (rapide — drivers récents requis)",
    "qsv": "Puce graphique Intel intégrée (Quick Sync)",
    "amf": "Carte graphique AMD (plus rapide si vous en avez une)",
}

DEFAULT_SETTINGS = {
    "preferred_codec": "hevc",
    "min_keep_codec": "h264",
    "auto_replace": False,
    "crf": 24,
    "preset": "medium",
    "audio_bitrate": "192k",
    "audio_channels": 2,
    "keep_audio_copy": False,
    "hardware_accel": "none",
    "container": "mp4",
    "dry_run": False,
    "max_parallel_encodes": 1,
    "refresh_plex_after": True,
    "server_url": "",
    "token": "",
    "last_server_name": "",
    "network_root": "",
    "plex_prefix": "",
    "path_maps": [],
}


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_config_dir() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_settings_path() -> Path:
    return get_config_dir() / "settings.json"


def get_history_path() -> Path:
    return get_config_dir() / "history.json"


def load_settings() -> dict:
    path = get_settings_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            settings = DEFAULT_SETTINGS.copy()
            settings.update(data)
            return settings
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    path = get_settings_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def load_history() -> list:
    path = get_history_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_history(history: list) -> None:
    path = get_history_path()
    history = history[-200:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def is_codec_acceptable(video_codec: str, min_keep: str) -> bool:
    if min_keep == "none":
        return False
    vc = (video_codec or "").lower()
    rank = CODEC_RANK.get(vc, 0)
    threshold = CODEC_RANK.get(min_keep, 50)
    if vc in ("h265",):
        rank = CODEC_RANK["hevc"]
    if vc in ("avc",):
        rank = CODEC_RANK["h264"]
    return rank >= threshold


def _win_sep(p: str) -> str:
    return (p or "").replace("/", "\\")


def common_path_prefix(paths: list) -> str:
    if not paths:
        return ""
    norms = [_win_sep(p).rstrip("\\") for p in paths if p]
    if not norms:
        return ""
    prefix = norms[0]
    for p in norms[1:]:
        while prefix and not p.lower().startswith(prefix.lower()):
            if "\\" not in prefix:
                prefix = ""
                break
            prefix = prefix.rsplit("\\", 1)[0]
        if not prefix:
            break
    return prefix


def apply_path_maps(
    file_path: str,
    path_maps: list = None,
    network_root: str = "",
    plex_prefix: str = "",
) -> str:
    if not file_path:
        return file_path
    root = (network_root or "").strip()
    prefix = (plex_prefix or "").strip()
    if root:
        src = file_path
        if prefix:
            matched = False
            rest = ""
            for s, pr in (
                (_win_sep(src), _win_sep(prefix)),
                (src.replace("\\", "/"), prefix.replace("\\", "/")),
                (src, prefix),
            ):
                if pr and s.lower().startswith(pr.lower()):
                    rest = s[len(pr):]
                    matched = True
                    break
            if not matched:
                rest = "\\" + _win_sep(src).lstrip("\\")
            rest = rest.replace("/", "\\")
            if rest and not rest.startswith("\\"):
                rest = "\\" + rest
            root_n = root.rstrip("\\/")
            if len(root_n) == 2 and root_n[1] == ":":
                return root_n + "\\" + rest.lstrip("\\")
            return root_n + rest
        name = Path(_win_sep(file_path)).name
        return root.rstrip("\\/") + "\\" + name
    maps = path_maps or []
    ordered = sorted(
        [m for m in maps if m.get("from") and m.get("to")],
        key=lambda m: len(str(m["from"])),
        reverse=True,
    )
    candidates = [file_path, file_path.replace("\\", "/"), file_path.replace("/", "\\")]
    for src in candidates:
        for m in ordered:
            frm = str(m["from"])
            to = str(m["to"])
            if src.lower().startswith(frm.lower()):
                rest = src[len(frm):]
                if "\\" in to or (len(to) >= 2 and to[1] == ":"):
                    rest = rest.replace("/", "\\")
                    if rest and not rest.startswith("\\"):
                        rest = "\\" + rest.lstrip("\\")
                    return to.rstrip("\\/") + rest
                rest = rest.replace("\\", "/")
                return to.rstrip("/") + rest
    return file_path


def path_exists_for_open(path: str) -> bool:
    if not path:
        return False
    try:
        p = Path(path)
        if p.exists():
            return True
        if p.parent.exists():
            return True
    except Exception:
        pass
    return False
