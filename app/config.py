"""Configuration and settings management for Ppix-Videocoder."""
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "Ppix-Videocoder"
APP_VERSION = "1.0.0"
CLIENT_IDENTIFIER = "ppix-videocoder-windows-v1"

# Default optimized codecs (preferred order)
DEFAULT_PREFERRED_CODEC = "hevc"  # H.265 / x265
SUPPORTED_CODECS = {
    "hevc": {
        "label": "H.265 / HEVC (x265)",
        "ffmpeg_v": "libx265",
        "ffmpeg_a": "aac",
        "container": "mp4",
        "tag": "hvc1",
    },
    "h264": {
        "label": "H.264 / AVC (x264)",
        "ffmpeg_v": "libx264",
        "ffmpeg_a": "aac",
        "container": "mp4",
        "tag": "avc1",
    },
}

# Codecs considered "not optimized" by default (force transcoding on many clients)
NON_OPTIMAL_CODECS = {
    "mpeg2video", "mpeg2", "mpeg4", "msmpeg4", "msmpeg4v2", "msmpeg4v3",
    "wmv1", "wmv2", "wmv3", "vc1", "vp6", "vp6f", "vp8", "theora",
    "flv1", "h263", "rv10", "rv20", "rv30", "rv40", "rawvideo",
    "prores", "dnxhd", "cineform",
}

DEFAULT_SETTINGS = {
    "preferred_codec": "hevc",
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
}

PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow"
]

HARDWARE_OPTIONS = {
    "none": "Logiciel (CPU)",
    "nvenc": "NVIDIA NVENC (hevc_nvenc / h264_nvenc)",
    "qsv": "Intel Quick Sync (hevc_qsv / h264_qsv)",
    "amf": "AMD AMF (hevc_amf / h264_amf)",
}


def get_app_dir() -> Path:
    """Directory of the running app (works for frozen PyInstaller exe)."""
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
