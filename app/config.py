"""Configuration and settings management for Ppix-Videocoder."""
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "Ppix-Videocoder"
APP_VERSION = "1.5.6"
CLIENT_IDENTIFIER = "ppix-videocoder-windows-v1"

SUPPORTED_CODECS = {
    "hevc": {"label": "H.265 / x265 (recommandé)", "ffmpeg_v": "libx265", "ffmpeg_a": "aac", "container": "mp4", "tag": "hvc1"},
    "h264": {"label": "H.264 / x264 (plus compatible)", "ffmpeg_v": "libx264", "ffmpeg_a": "aac", "container": "mp4", "tag": "avc1"},
}
CODEC_CHOICE_LABELS = {"hevc": "H.265 (x265)", "h264": "H.264 (x264)"}
CODEC_RANK = {
    "mpeg1video": 5, "mpeg2video": 10, "mpeg2": 10, "mpeg4": 15,
    "msmpeg4": 12, "msmpeg4v2": 12, "msmpeg4v3": 12,
    "wmv1": 8, "wmv2": 8, "wmv3": 10, "vc1": 12,
    "vp6": 12, "vp6f": 12, "vp8": 25, "vp9": 45, "av1": 70,
    "theora": 15, "flv1": 8, "h263": 8,
    "rv10": 5, "rv20": 5, "rv30": 8, "rv40": 10,
    "rawvideo": 0, "prores": 40, "dnxhd": 35, "cineform": 35,
    "h264": 50, "avc": 50, "hevc": 60, "h265": 60,
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
QUALITY_OPTIONS = [(18, "Excellente (fichier plus lourd)"), (20, "Très bonne"), (22, "Bonne"), (24, "Équilibrée (recommandé)"), (26, "Correcte (fichier plus léger)"), (28, "Économique (fichier léger)")]
AUDIO_BITRATE_OPTIONS = [("96k", "96 kb/s — basique"), ("128k", "128 kb/s — standard"), ("160k", "160 kb/s — bonne"), ("192k", "192 kb/s — recommandée"), ("256k", "256 kb/s — haute"), ("320k", "320 kb/s — maximale")]
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
PLEX_SAFE_AUDIO_CODECS = {"aac", "mp3", "ac3", "eac3", "flac", "pcm", "pcm_s16le", "pcm_s24le", "pcm_bluray", "mp2", "mp1"}
LEGACY_CONTAINERS = {"avi", "wmv", "asf", "divx", "xvid", "mpg", "mpeg", "vob", "flv"}
HARDWARE_OPTIONS = {
    "none": "Aucune — processeur seul (compatible partout)",
    "auto": "Auto (détecter GPU)",
    "nvenc": "Carte NVIDIA (rapide — drivers récents requis)",
    "qsv": "Puce graphique Intel intégrée (Quick Sync)",
    "amf": "Carte graphique AMD (plus rapide si vous en avez une)",
}
DEFAULT_SETTINGS = {
    "preferred_codec": "hevc", "min_keep_codec": "h264", "auto_replace": False,
    "crf": 24, "preset": "medium", "audio_bitrate": "192k", "audio_channels": 2,
    "keep_audio_copy": False, "hardware_accel": "auto", "container": "mp4",
    "mkv_for_legacy": True, "dry_run": False, "refresh_plex_after": True,
    "network_root": "", "plex_prefix": "", "path_maps": [],
    "server_url": "", "token": "", "hw_detected": False, "last_server_name": "",
}

def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def get_config_dir() -> Path:
    d = Path.home() / ".ppix-videocoder"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_settings_path() -> Path:
    if getattr(sys, "frozen", False):
        return get_app_dir() / "settings.json"
    return get_config_dir() / "settings.json"

def get_history_path() -> Path:
    if getattr(sys, "frozen", False):
        return get_app_dir() / "history.json"
    return get_config_dir() / "history.json"

def load_settings() -> dict:
    p = get_settings_path()
    if not p.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        out = dict(DEFAULT_SETTINGS)
        out.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS or k in ("path_maps", "server_url", "token", "last_server_name", "hw_detected")})
        return out
    except Exception:
        return dict(DEFAULT_SETTINGS)

def save_settings(settings: dict) -> None:
    p = get_settings_path()
    try:
        p.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def load_history() -> list:
    p = get_history_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_history(history: list) -> None:
    p = get_history_path()
    try:
        p.write_text(json.dumps(history[-200:], indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

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

def is_legacy_container(cont: str) -> bool:
    return (cont or "").lower().lstrip(".") in LEGACY_CONTAINERS

def is_audio_safe_for_plex(codec: str) -> bool:
    c = (codec or "").lower().strip()
    if not c:
        return False
    if c in ("mp2", "mpa"):
        return True
    return c in PLEX_SAFE_AUDIO_CODECS or c.startswith("pcm")

def common_path_prefix(paths: list) -> str:
    if not paths:
        return ""
    norm = [p.replace("\\", "/").rstrip("/") for p in paths if p]
    if not norm:
        return ""
    prefix = norm[0]
    for p in norm[1:]:
        while prefix and not p.startswith(prefix):
            prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        if not prefix:
            break
    return prefix.replace("/", "\\") if paths and "\\" in paths[0] else prefix

def apply_path_maps(path: str, path_maps: list | None = None, network_root: str = "", plex_prefix: str = "") -> str:
    if not path:
        return path
    mapped = path
    for m in path_maps or []:
        src = (m.get("from") or m.get("src") or "").rstrip("\\/")
        dst = (m.get("to") or m.get("dst") or "").rstrip("\\/")
        if src and dst and (mapped.startswith(src) or mapped.replace("/", "\\").startswith(src.replace("/", "\\"))):
            rest = mapped[len(src):].lstrip("\\/")
            mapped = str(Path(dst) / rest) if rest else dst
            break
    nr = (network_root or "").rstrip("\\/")
    pp = (plex_prefix or "").rstrip("\\/")
    if nr and pp:
        cand = mapped.replace("/", "\\")
        pp_n = pp.replace("/", "\\")
        if cand.lower().startswith(pp_n.lower()):
            rest = cand[len(pp_n):].lstrip("\\")
            mapped = str(Path(nr) / rest) if rest else nr
    return mapped

def path_exists_for_open(path: str) -> bool:
    try:
        return Path(path).exists()
    except Exception:
        return False
