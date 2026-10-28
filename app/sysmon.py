"""Mesure de la charge pendant l'encodage : CPU (total + FFmpeg) et GPU.

- CPU : psutil (total du PC + part du processus FFmpeg, ramenée à 100 %).
- GPU : compteurs de performance Windows « GPU Engine » (ceux du Gestionnaire
  des tâches), lus via PDH avec les noms anglais pour fonctionner sur un
  Windows en français. Valable pour NVIDIA, Intel et AMD (Windows 10 1709+).
  Repli nvidia-smi pour les cartes NVIDIA si les compteurs sont absents.
- Noms du processeur et des cartes graphiques pour indiquer « qui travaille ».
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .logger import log

_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000


@dataclass
class LoadSample:
    cpu_total: Optional[float] = None    # % du PC entier
    cpu_proc: Optional[float] = None     # % pris par FFmpeg (ramené au PC entier)
    gpu_total: Optional[float] = None    # % moteur GPU le plus chargé (comme le Gestionnaire des tâches)
    gpu_enc: Optional[float] = None      # % encodeur vidéo dédié (NVENC / Quick Sync / VCN)


def _run(cmd: List[str], timeout: float = 10) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=_NO_WINDOW if _WIN else 0)
        return p.stdout or ""
    except Exception:
        return ""


# ----------------------------------------------------------------------
# Noms du matériel
# ----------------------------------------------------------------------
def cpu_name() -> str:
    name = ""
    if _WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
        except Exception:
            pass
    elif os.path.exists("/proc/cpuinfo"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        name = line.split(":", 1)[1]
                        break
        except Exception:
            pass
    name = name or platform.processor() or "processeur"
    name = re.sub(r"\((R|TM|tm|r)\)", "", name)
    name = re.sub(r"\s*(CPU|Processor)?\s*@.*$", "", name)   # « … CPU @ 3.60GHz »
    name = re.sub(r"\s+\d+-Core Processor$", "", name)  # « AMD Ryzen 7 5800X 8-Core Processor »
    return re.sub(r"\s{2,}", " ", name).strip()


def gpu_names() -> List[str]:
    names: List[str] = []
    if _WIN:
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"], timeout=15)
        names = [n.strip() for n in out.splitlines() if n.strip()]
    if shutil.which("nvidia-smi"):
        out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        for n in out.splitlines():
            n = n.strip()
            if n and n not in names:
                names.append(n)
    return [n for n in names if "basic" not in n.lower() and "remote" not in n.lower()]


_VENDOR_KEYS = {"nvenc": ("nvidia", "geforce", "quadro", "rtx"),
                "qsv": ("intel",),
                "amf": ("amd", "radeon")}
_VENDOR_DEFAULT = {"nvenc": "carte NVIDIA", "qsv": "puce graphique Intel", "amf": "carte AMD"}
_ENGINE_LABEL = {"nvenc": "NVENC", "qsv": "Quick Sync", "amf": "AMF"}


def gpu_for(hw: str, names: List[str]) -> str:
    keys = _VENDOR_KEYS.get(hw, ())
    for n in names:
        if any(k in n.lower() for k in keys):
            return n
    return _VENDOR_DEFAULT.get(hw, "carte graphique")


def describe_worker(hw_used: str, hw_requested: str, video_codec: str, audio_only: bool,
                    cpu: str, gpus: List[str]) -> str:
    """Texte « qui travaille » affiché sous le fichier."""
    if audio_only:
        return f"Logiciel : audio seulement (TrueHD → AC3), vidéo copiée — processeur {cpu}"
    if hw_used in _ENGINE_LABEL:
        return f"Matériel : {gpu_for(hw_used, gpus)} ({_ENGINE_LABEL[hw_used]})"
    enc = "x265" if video_codec == "hevc" else "x264"
    txt = f"Logiciel : processeur {cpu} ({enc})"
    if hw_requested in _ENGINE_LABEL:
        txt += f" — repli après échec {_ENGINE_LABEL[hw_requested]}"
    return txt


def format_load(s: Optional[LoadSample]) -> str:
    if s is None:
        return "CPU … · GPU …"

    def pct(v: Optional[float]) -> str:
        return "n/d" if v is None else f"{v:.0f} %"
    proc = "…" if s.cpu_proc is None and s.cpu_total is not None else pct(s.cpu_proc)
    txt = f"CPU {pct(s.cpu_total)} (encodage {proc})"
    if s.gpu_total is None and s.gpu_enc is None:
        return txt + " · GPU n/d"
    txt += f" · GPU {pct(s.gpu_total)}"
    if s.gpu_enc is not None:
        txt += f" (encodeur vidéo {pct(s.gpu_enc)})"
    return txt


# ----------------------------------------------------------------------
# Compteurs GPU Windows (PDH)
# ----------------------------------------------------------------------
class _PdhGpu:
    """Lecture de « \\GPU Engine(*)\\Utilization Percentage » via ctypes."""
    PDH_FMT_DOUBLE = 0x00000200
    PDH_MORE_DATA = 0x800007D2

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        self.pdh = ctypes.WinDLL("pdh")

        class FmtValue(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

        class Item(ctypes.Structure):
            _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", FmtValue)]

        self.Item = Item
        self.query = wintypes.HANDLE()
        self.counter = wintypes.HANDLE()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
            raise OSError("PdhOpenQuery")
        st = self.pdh.PdhAddEnglishCounterW(self.query, r"\GPU Engine(*)\Utilization Percentage",
                                            None, ctypes.byref(self.counter))
        if st != 0:
            self.close()
            raise OSError(f"PdhAddEnglishCounter {st:#x}")
        self.pdh.PdhCollectQueryData(self.query)

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        ct = self.ct
        if self.pdh.PdhCollectQueryData(self.query) != 0:
            return None, None
        size = ct.c_ulong(0)
        count = ct.c_ulong(0)
        st = self.pdh.PdhGetFormattedCounterArrayW(self.counter, self.PDH_FMT_DOUBLE,
                                                  ct.byref(size), ct.byref(count), None)
        if st & 0xFFFFFFFF != self.PDH_MORE_DATA or not size.value:
            return None, None
        buf = (ct.c_byte * size.value)()
        st = self.pdh.PdhGetFormattedCounterArrayW(self.counter, self.PDH_FMT_DOUBLE,
                                                  ct.byref(size), ct.byref(count), buf)
        if st != 0:
            return None, None
        items = ct.cast(buf, ct.POINTER(self.Item))
        per_engine: Dict[Tuple[str, str], float] = {}
        for i in range(count.value):
            it = items[i]
            if it.FmtValue.CStatus not in (0, 1):
                continue
            name = (it.szName or "").lower()
            m = re.search(r"luid_(0x[0-9a-f]+_0x[0-9a-f]+).*engtype_(.+)$", name)
            if not m:
                continue
            key = (m.group(1), m.group(2).strip())
            per_engine[key] = per_engine.get(key, 0.0) + max(0.0, it.FmtValue.doubleValue)
        if not per_engine:
            return 0.0, None
        total = min(100.0, max(per_engine.values()))
        enc_vals = [v for (_, eng), v in per_engine.items()
                    if "video" in eng and ("encode" in eng or "codec" in eng)]
        enc = min(100.0, max(enc_vals)) if enc_vals else None
        return total, enc

    def close(self):
        try:
            self.pdh.PdhCloseQuery(self.query)
        except Exception:
            pass


def _nvidia_smi() -> Tuple[Optional[float], Optional[float]]:
    out = _run(["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder",
                "--format=csv,noheader,nounits"], timeout=5)
    best: Tuple[Optional[float], Optional[float]] = (None, None)
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            g, e = float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            continue
        if best[0] is None or g > best[0]:
            best = (g, e)
    return best


# ----------------------------------------------------------------------
# Moniteur
# ----------------------------------------------------------------------
class LoadMonitor:
    """Échantillonne la charge toutes les `interval` secondes dans un thread."""

    def __init__(self, interval: float = 1.5):
        self.interval = interval
        self.latest: Optional[LoadSample] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pid: Optional[int] = None
        self._lock = threading.Lock()

    def set_pid(self, pid: Optional[int]) -> None:
        with self._lock:
            self._pid = pid

    def start(self) -> None:
        """(Re)démarre la mesure. Chaque thread a son propre signal d'arrêt : un
        redémarrage rapide (arrêt puis relance de la file) ne perd plus la mesure."""
        old = self._thread
        self._stop.set()
        if old and old.is_alive():
            old.join(timeout=5)
        self._stop = threading.Event()
        self.latest = None
        self._thread = threading.Thread(target=self._loop, args=(self._stop,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self, stop: threading.Event) -> None:
        try:
            import psutil
        except Exception:
            psutil = None  # type: ignore
        gpu = None
        if _WIN:
            try:
                gpu = _PdhGpu()
            except Exception as e:
                log(f"Compteurs GPU indisponibles : {e}", level="DEBUG")
        use_smi = gpu is None and bool(shutil.which("nvidia-smi"))
        ncpu = (psutil.cpu_count() if psutil else os.cpu_count()) or 1
        proc = None
        proc_pid = None
        warned = False
        if psutil:
            psutil.cpu_percent(None)
        refresh = 0
        try:
            while not stop.wait(self.interval):
                s = LoadSample()
                if psutil:
                    s.cpu_total = psutil.cpu_percent(None)
                    with self._lock:
                        pid = self._pid
                    if pid != proc_pid:
                        proc_pid, proc, warned = pid, None, False
                    if pid and proc is None:
                        # (re)tentative à chaque cycle : le processus peut ne pas être encore visible
                        try:
                            proc = psutil.Process(pid)
                            proc.cpu_percent(None)
                        except Exception as e:
                            if not warned:
                                log(f"Mesure CPU du processus {pid} impossible : {e!r}", level="DEBUG")
                                warned = True
                            proc = None
                    elif proc is not None:
                        try:
                            s.cpu_proc = min(100.0, proc.cpu_percent(None) / ncpu)
                        except Exception:
                            proc = None
                if gpu is not None:
                    # les instances (une par processus) changent : on recrée la requête de temps en temps
                    refresh += 1
                    if refresh % 20 == 0:
                        try:
                            gpu.close()
                            gpu = _PdhGpu()
                        except Exception:
                            gpu = None
                    if gpu is not None:
                        s.gpu_total, s.gpu_enc = gpu.read()
                elif use_smi:
                    s.gpu_total, s.gpu_enc = _nvidia_smi()
                if not stop.is_set():
                    self.latest = s
        finally:
            if gpu is not None:
                gpu.close()
