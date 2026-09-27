"""Application log file (diagnostique encodage / connexion)."""
from __future__ import annotations

import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import get_config_dir, APP_NAME, APP_VERSION

_LOG_NAME = "ppix-videocoder.log"
_MAX_BYTES = 2 * 1024 * 1024


def get_log_path() -> Path:
    return get_config_dir() / _LOG_NAME


def _ensure_header(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"=== {APP_NAME} v{APP_VERSION} log ===\n")
        f.write(f"Started {datetime.now().isoformat(timespec='seconds')}\n")
        f.write("=" * 50 + "\n")


def log(message: str, level: str = "INFO") -> None:
    path = get_log_path()
    try:
        _ensure_header(path)
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            text = path.read_text(encoding="utf-8", errors="replace")
            path.write_text(text[-_MAX_BYTES // 2 :], encoding="utf-8")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {message}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def log_exception(prefix: str = "Exception") -> None:
    log(f"{prefix}:\n{traceback.format_exc()}", level="ERROR")


def read_log(max_chars: int = 200_000) -> str:
    path = get_log_path()
    if not path.exists():
        return "(journal vide)"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[-max_chars:]
        return text
    except Exception as e:
        return f"(lecture impossible: {e})"


def clear_log() -> None:
    path = get_log_path()
    try:
        if path.exists():
            path.unlink()
        _ensure_header(path)
        log("Journal efface par l'utilisateur")
    except Exception:
        pass


def open_log_folder() -> Optional[str]:
    path = get_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_header(path)
    folder = str(path.parent)
    try:
        if os.name == "nt":
            os.startfile(folder)  # type: ignore
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
    except Exception:
        pass
    return str(path)
