"""Configuration and settings management for Ppix-Videocoder."""
from __future__ import annotations

import json
import sys
from pathlib import Path

APP_NAME = "Ppix-Videocoder"
APP_VERSION = "1.6.0"
CLIENT_IDENTIFIER = "ppix-videocoder-windows-v1"
KEYRING_SERVICE = "Ppix-Videocoder"
KEYRING_USER = "plex-token"

SUPPORTED_CODECS = {
    "hevc": {"label": "H.265 / x265 (recommandé)", "ffmpeg_v": "libx265", "ffmpeg_a": "aac", "container": "mp4", "tag": "hvc1"},
    "h264": {"label": "H.264 / x264 (plus compatible)", "ffmpeg_v": "libx264", "ffmpeg_a": "aac", "container": "mp4", "tag": "avc1"},
}
CODEC_CHOICE_LABELS = {"hevc": "H.265 (x265)", "h264": "H.264 (x264)"}
CODEC_RANK = {
    "mpeg1video": 5, "mpeg2video": 10, "mpeg2": 10, "mpeg4": 15,
    "msmpeg4": 12, "msmpeg4v1": 12, "msmpeg4v2": 12, "msmpeg4v3": 12,
    "wmv1": 8, "wmv2": 8, "wmv3": 10, "vc1": 12,
    "vp6": 12, "vp6f": 12, "vp8": 25, "vp9": 45, "av1": 70,
    "theora": 15, "flv1": 8, "flv": 8, "h263": 8,
    "rv10": 5, "rv20": 5, "rv30": 8, "rv40": 10, "real": 8,
    "rawvideo": 0, "prores": 40, "dnxhd": 35, "cineform": 35,
    "h264": 50, "avc": 50, "avc1": 50, "x264": 50,
    "hevc": 60, "h265": 60, "x265": 60,
}
CODEC_ALIASES = {"h265": "hevc", "x265": "hevc", "avc": "h264", "avc1": "h264", "x264": "h264"}
MIN_KEEP_OPTIONS = {
    "h264": "H.264 et mieux (ne pas lister H.264 / H.265)",
    "hevc": "H.265 uniquement (lister tout sauf H.265)",
    "none": "Tout lister (aucun filtre)",
}
# Seuil "H.264 trop lourd", exprimé en Mb/s pour du 1080p (adapté à la résolution réelle)
HEAVY_H264_OPTIONS = [
    (0, "Désactivé"),
    (8, "Au-dessus de 8 Mb/s (équiv. 1080p)"),
    (12, "Au-dessus de 12 Mb/s (équiv. 1080p)"),
    (20, "Au-dessus de 20 Mb/s (équiv. 1080p)"),
]
QUALITY_OPTIONS = [(18, "Excellente (fichier plus lourd)"), (20, "Très bonne"), (22, "Bonne"), (24, "Équilibrée (recommandé)"), (26, "Correcte (fichier plus léger)"), (28, "Économique (fichier léger)")]
AUDIO_BITRATE_OPTIONS = [("96k", "96 kb/s — basique"), ("128k", "128 kb/s — standard"), ("160k", "160 kb/s — bonne"), ("192k", "192 kb/s — recommandée"), ("256k", "256 kb/s — haute"), ("320k", "320 kb/s — maximale")]
AUDIO_CHANNEL_OPTIONS = [(0, "Conserver les canaux (5.1 reste 5.1)"), (2, "Stéréo (réduit le 5.1 en 2.0)")]
PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
PLEX_SAFE_AUDIO_CODECS = {"aac", "mp3", "ac3", "eac3", "flac", "pcm", "pcm_s16le", "pcm_s24le", "pcm_bluray", "mp2", "mp1"}
# Codecs audio copiables tels quels dans un MP4 (pcm / mp2 refusés ou mal lus)
MP4_COPY_AUDIO_CODECS = {"aac", "mp3", "ac3", "eac3", "flac"}
LEGACY_CONTAINERS = {"avi", "wmv", "asf", "divx", "xvid", "mpg", "mpeg", "vob", "flv", "ts", "m2ts", "mts", "rm", "rmvb", "ogm", "3gp"}
HARDWARE_OPTIONS = {
    "none": "Aucune — processeur seul (compatible partout)",
    "auto": "Auto (utilise le GPU détecté)",
    "nvenc": "Carte NVIDIA (rapide — drivers récents requis)",
    "qsv": "Puce graphique Intel intégrée (Quick Sync)",
    "amf": "Carte graphique AMD (plus rapide si vous en avez une)",
}
HARDWARE_SHORT = {"none": "CPU", "nvenc": "NVIDIA", "qsv": "Intel QSV", "amf": "AMD"}
DEFAULT_SETTINGS = {
    "preferred_codec": "hevc", "min_keep_codec": "h264", "auto_replace": False,
    "crf": 24, "preset": "medium", "audio_bitrate": "192k", "audio_channels": 0,
    "keep_audio_copy": True, "keep_subtitles": True, "deinterlace": True,
    "hardware_accel": "auto", "hw_fallback_cpu": True, "container": "mp4",
    "mkv_for_legacy": True, "dry_run": False, "refresh_plex_after": True,
    "keep_backup": False, "skip_if_bigger": False, "heavy_h264_mbps": 0,
    "low_priority": True, "prevent_sleep": True,
    "network_root": "", "plex_prefix": "", "path_maps": [],
    "network_root_asked": False, "detected_hw": "",
    "server_url": "", "token": "", "token_in_keyring": False, "last_server_name": "",
    "settings_version": 2,
}


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def get_config_dir() -> Path:
    """Dossier persistant utilisateur (survit aux mises à jour de l'EXE).

    Toujours %USERPROFILE%\\.ppix-videocoder — jamais à côté de l'exécutable,
    pour ne pas perdre token / chemins réseau à chaque nouvelle version.
    """
    d = Path.home() / ".ppix-videocoder"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _migrate_legacy_file(name: str) -> None:
    """Copie settings/history depuis le dossier de l'EXE (anciennes versions)."""
    dest = get_config_dir() / name
    if dest.exists():
        return
    try:
        legacy = get_app_dir() / name
        if legacy.is_file():
            dest.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass


def get_settings_path() -> Path:
    _migrate_legacy_file("settings.json")
    return get_config_dir() / "settings.json"


def get_history_path() -> Path:
    _migrate_legacy_file("history.json")
    return get_config_dir() / "history.json"


# ----------------------------------------------------------------------
# Token Plex : stocké dans le Gestionnaire d'identification Windows
# (keyring) plutôt qu'en clair dans settings.json. Repli JSON si keyring
# indisponible.
# ----------------------------------------------------------------------
_keyring_cache: dict = {"value": None}


def _keyring_get() -> str:
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
    except Exception:
        return ""


def _keyring_set(token: str) -> bool:
    if _keyring_cache["value"] == token:
        return True
    try:
        import keyring
        if token:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception:
                pass
        _keyring_cache["value"] = token
        return True
    except Exception:
        return False


def load_settings() -> dict:
    p = get_settings_path()
    out = dict(DEFAULT_SETTINGS)
    if not p.exists():
        return out
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return out
    out.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    # Migration v1.5 → v1.6 : l'ancien défaut "audio_channels=2" écrasait le 5.1
    # sans que l'option existe dans l'interface → on repasse en "conserver".
    if int(data.get("settings_version", 1)) < 2:
        out["audio_channels"] = 0
        out["settings_version"] = 2
    if out.get("token_in_keyring") and not out.get("token"):
        tok = _keyring_get()
        out["token"] = tok
        _keyring_cache["value"] = tok
    return out


def save_settings(settings: dict) -> None:
    p = get_settings_path()
    data = dict(settings)
    token = data.get("token") or ""
    if _keyring_set(token):
        data["token"] = ""
        data["token_in_keyring"] = bool(token)
    else:
        data["token_in_keyring"] = False
    try:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def load_history() -> list:
    p = get_history_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(history: list) -> None:
    p = get_history_path()
    try:
        p.write_text(json.dumps(history[-500:], indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ----------------------------------------------------------------------
# Règles de sélection
# ----------------------------------------------------------------------
def normalize_codec(codec: str) -> str:
    c = (codec or "").lower().strip()
    return CODEC_ALIASES.get(c, c)


def is_codec_acceptable(video_codec: str, min_keep: str) -> bool:
    if min_keep == "none":
        return False
    vc = normalize_codec(video_codec)
    rank = CODEC_RANK.get(vc, 0)
    threshold = CODEC_RANK.get(min_keep, 50)
    return rank >= threshold


def heavy_threshold_kbps(heavy_mbps: float, width: int, height: int) -> float:
    """Seuil de débit adapté à la résolution (référence 1080p, plancher 30 %)."""
    if not heavy_mbps:
        return 0.0
    pixels = max(1, (width or 1920) * (height or 1080))
    ratio = max(0.3, pixels / (1920 * 1080))
    return heavy_mbps * 1000 * ratio


def optimization_reason(video_codec: str, bitrate_kbps: int, width: int, height: int,
                        min_keep: str, heavy_mbps: float = 0) -> str:
    """Retourne la raison pour laquelle le fichier doit être optimisé ('' = rien à faire)."""
    vc = normalize_codec(video_codec)
    if not is_codec_acceptable(vc, min_keep):
        return "codec ancien" if CODEC_RANK.get(vc, 0) < CODEC_RANK["h264"] else "filtre"
    if vc == "h264" and heavy_mbps and bitrate_kbps:
        if bitrate_kbps > heavy_threshold_kbps(heavy_mbps, width, height):
            return "débit élevé"
    return ""


def resolve_hardware(setting: str, detected: str | None) -> str:
    """'auto' → GPU réellement détecté (ou CPU)."""
    s = (setting or "none").lower()
    if s == "auto":
        return detected if detected in ("nvenc", "qsv", "amf") else "none"
    return s if s in ("none", "nvenc", "qsv", "amf") else "none"


def is_legacy_container(cont: str) -> bool:
    return (cont or "").lower().lstrip(".") in LEGACY_CONTAINERS


def resolve_output_container(src_cont: str, settings: dict | None = None) -> str:
    """Choisit le conteneur de sortie de façon cohérente avec la source.

    - AVI/WMV/MPG/TS… → mkv si mkv_for_legacy (recommandé)
    - Source déjà mkv / mp4 / m4v → on conserve
    - Sinon → conteneur préféré des paramètres (défaut mp4)
    """
    settings = settings or {}
    src = (src_cont or "").lower().lstrip(".")
    preferred = (settings.get("container") or "mp4").lower()
    if preferred not in ("mp4", "mkv"):
        preferred = "mp4"
    if settings.get("mkv_for_legacy", True) and src in LEGACY_CONTAINERS:
        return "mkv"
    if src in ("mkv", "webm"):
        return "mkv"
    if src in ("mp4", "m4v", "mov"):
        return "mp4"
    return preferred


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
    prefix = norm[0].rsplit("/", 1)[0] if len(norm) == 1 else norm[0]
    for p in norm[1:]:
        while prefix and not (p == prefix or p.startswith(prefix + "/")):
            prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        if not prefix:
            break
    return prefix.replace("/", "\\") if "\\" in paths[0] else prefix


def _starts_with_dir(path: str, prefix: str) -> bool:
    """Vrai si path est dans le dossier prefix (insensible à la casse et au séparateur)."""
    p = path.replace("/", "\\").lower()
    q = prefix.replace("/", "\\").rstrip("\\").lower()
    return bool(q) and (p == q or p.startswith(q + "\\"))


def apply_path_maps(path: str, path_maps: list | None = None, network_root: str = "", plex_prefix: str = "") -> str:
    """Traduit un chemin vu par Plex (/volume1/Films/x.avi) en chemin Windows (\\\\NAS\\Films\\x.avi)."""
    if not path:
        return path
    for m in path_maps or []:
        src = (m.get("from") or m.get("src") or "").rstrip("\\/")
        dst = (m.get("to") or m.get("dst") or "").rstrip("\\/")
        if src and dst and _starts_with_dir(path, src):
            return _join_rest(dst, path[len(src):])
    nr = (network_root or "").rstrip("\\/")
    pp = (plex_prefix or "").rstrip("\\/")
    if nr and pp and _starts_with_dir(path, pp):
        return _join_rest(nr, path[len(pp):])
    return path


def _join_rest(base: str, rest: str) -> str:
    """Ajoute la fin du chemin avec le séparateur de la destination (\\ par défaut)."""
    sep = "/" if ("/" in base and "\\" not in base) else "\\"
    rest = rest.replace("\\", "/").strip("/")
    return base + (sep + rest.replace("/", sep) if rest else "")


def guess_plex_prefix(network_root: str, locations: list) -> str:
    """Déduit le préfixe Plex correspondant à la racine réseau.

    \\\\NAS\\Media + /volume1/Media/Films → /volume1/Media
    """
    share = (network_root or "").replace("/", "\\").rstrip("\\").split("\\")[-1].lower()
    if not share:
        return ""
    for loc in locations or []:
        parts = [x for x in str(loc).replace("\\", "/").split("/") if x]
        low = [x.lower() for x in parts]
        if share in low:
            idx = low.index(share)
            return "/" + "/".join(parts[: idx + 1])
    return ""


def replace_extension(path: str, new_ext: str) -> str:
    """Change l'extension d'un chemin Windows ou POSIX sans toucher aux séparateurs."""
    if not path:
        return path
    sep = max(path.rfind("/"), path.rfind("\\"))
    dot = path.rfind(".")
    if dot > sep + 1:
        path = path[:dot]
    if not new_ext.startswith("."):
        new_ext = "." + new_ext
    return path + new_ext


def parent_dir(path: str) -> str:
    sep = max(path.rfind("/"), path.rfind("\\"))
    return path[:sep] if sep > 0 else path


def path_exists_for_open(path: str) -> bool:
    try:
        return Path(path).exists()
    except Exception:
        return False
