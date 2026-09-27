"""Configuration and settings management for Ppix-Videocoder."""
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "Ppix-Videocoder"
APP_VERSION = "1.5.3"
CLIENT_IDENTIFIER = "ppix-videocoder-windows-v1"

# Codecs d'encodage supportés (clés internes)
SUPPORTED_CODECS = {
    "hevc": {
        "label": "H.265 / x265 (recommandé)",
        "ffmpeg_v": "libx265",
        "ffmpeg_a": "aac",
        "container": "mp4",
        "tag": "hvc1",
    },
    "h264": {
        "label": "H.264 / x264",
        "ffmpeg_v": "libx264",
        "ffmpeg_a": "aac",
        "container": "mp4",
        "tag": "avc1",
    },
}

CODEC_CHOICE_LABELS = {
    "hevc": "H.265",
    "h264": "H.264",
}

# Options affichées pour le filtre "ne pas lister les fichiers déjà en…"
MIN_KEEP_OPTIONS = {
    "none": "Tout lister (aucun filtre)",
    "h264": "H.264 ou mieux (ne pas lister H.264/H.265)",
    "hevc": "H.265 uniquement (ne pas lister H.265)",
}

PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]

HARDWARE_OPTIONS = {
    "none": "CPU (logiciel)",
    "auto": "Auto (détecter GPU)",
    "nvenc": "NVIDIA NVENC",
    "qsv": "Intel Quick Sync",
    "amf": "AMD AMF",
}

QUALITY_OPTIONS = [
    (18, "Très haute qualité (gros fichiers)"),
    (20, "Haute qualité"),
    (22, "Bonne qualité"),
    (24, "Équilibré (recommandé)"),
    (26, "Plus petit"),
    (28, "Petit (qualité réduite)"),
]

AUDIO_BITRATE_OPTIONS = ["128k", "160k", "192k", "256k", "320k"]

# Conteneurs considérés comme « legacy » → forcer MKV si option active
LEGACY_CONTAINERS = {"avi", "wmv", "mpeg", "mpg", "vob", "flv", "divx", "xvid", "asf", "rm", "rmvb"}

# Codecs audio sûrs pour Plex Direct Play (pas de transcodage côté serveur)
PLEX_SAFE_AUDIO_CODECS = {"aac", "mp3", "ac3", "eac3", "flac", "opus", "pcm", "truehd", "dts"}

DEFAULT_SETTINGS = {
    "preferred_codec": "hevc",
    "min_keep_codec": "h264",
    "auto_replace": False,
    "crf": 24,
    "preset": "medium",
    "audio_bitrate": "192k",
    "audio_channels": 2,
    "keep_audio_copy": True,
    "hardware_accel": "auto",
    "container": "mp4",
    "mkv_for_legacy": True,
    "dry_run": False,
    "refresh_plex_after": True,
    "network_root": "",
    "plex_prefix": "",
    "path_maps": [],
    "server_url": "",
    "token": "",
}


def _settings_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path.home() / ".ppix-videocoder"
    base.mkdir(parents=True, exist_ok=True)
    return base / "settings.json"


def _history_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path.home() / ".ppix-videocoder"
    base.mkdir(parents=True, exist_ok=True)
    return base / "history.json"


def load_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        out = dict(DEFAULT_SETTINGS)
        out.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS or k in ("path_maps", "server_url", "token")})
        return out
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    p = _settings_path()
    try:
        p.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_history() -> list:
    p = _history_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(history: list) -> None:
    p = _history_path()
    try:
        # garder les 200 derniers
        p.write_text(json.dumps(history[-200:], indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def is_legacy_container(cont: str) -> bool:
    return (cont or "").lower().lstrip(".") in LEGACY_CONTAINERS


def is_audio_safe_for_plex(codec: str) -> bool:
    c = (codec or "").lower().strip()
    if not c:
        return False
    # normaliser quelques variantes
    if c in ("mp2", "mpa"):
        return True
    return c in PLEX_SAFE_AUDIO_CODECS or c.startswith("pcm")


def common_path_prefix(paths: list) -> str:
    """Préfixe commun le plus long des chemins (séparateurs normalisés)."""
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
    return prefix.replace("/", "\\") if "\\" in paths[0] else prefix


def apply_path_maps(
    path: str,
    path_maps: list | None = None,
    network_root: str = "",
    plex_prefix: str = "",
) -> str:
    """Applique les correspondances de chemins (NAS → Windows)."""
    if not path:
        return path
    mapped = path
    # 1) maps explicites
    for m in path_maps or []:
        src = (m.get("from") or m.get("src") or "").rstrip("\\/")
        dst = (m.get("to") or m.get("dst") or "").rstrip("\\/")
        if src and dst and (mapped.startswith(src) or mapped.replace("/", "\\").startswith(src.replace("/", "\\"))):
            rest = mapped[len(src):].lstrip("\\/")
            mapped = str(Path(dst) / rest) if rest else dst
            break
    # 2) racine réseau + préfixe Plex
    nr = (network_root or "").rstrip("\\/")
    pp = (plex_prefix or "").rstrip("\\/")
    if nr and pp:
        # si le chemin commence encore par le préfixe Plex, le remplacer
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
