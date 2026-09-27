"""FFmpeg-based video transcoder with progress reporting."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, List

from .config import SUPPORTED_CODECS, get_app_dir
from .logger import log


@dataclass
class EncodeJob:
    video_path: str
    output_path: str
    codec: str = "hevc"
    crf: int = 24
    preset: str = "medium"
    audio_bitrate: str = "192k"
    audio_channels: int = 2
    keep_audio_copy: bool = False
    hardware: str = "none"
    container: str = "mp4"
    dry_run: bool = False
    progress: float = 0.0
    status: str = "pending"
    message: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    original_size: int = 0
    output_size: int = 0


class Encoder:
    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg_path = self._find_ffmpeg(ffmpeg_path)
        self.current_job: Optional[EncodeJob] = None
        self._stop_requested = False

    def _find_ffmpeg(self, preferred: str) -> str:
        candidates: List[Path] = []
        if preferred and preferred != "ffmpeg":
            candidates.append(Path(preferred))
        app_dir = get_app_dir()
        candidates.append(app_dir / "ffmpeg" / "ffmpeg.exe")
        candidates.append(app_dir / "ffmpeg.exe")
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            meipass = Path(sys._MEIPASS)
            candidates.append(meipass / "ffmpeg" / "ffmpeg.exe")
            candidates.append(meipass / "ffmpeg.exe")
        candidates.append(Path(__file__).resolve().parent.parent / "ffmpeg" / "ffmpeg.exe")
        candidates.append(Path(__file__).resolve().parent.parent / "ffmpeg" / "ffmpeg")
        which = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if which:
            candidates.append(Path(which))
        for c in candidates:
            if c and c.exists() and c.is_file():
                return str(c)
        return "ffmpeg"

    def build_command(self, job: EncodeJob) -> List[str]:
        codec_info = SUPPORTED_CODECS.get(job.codec, SUPPORTED_CODECS["hevc"])
        vcodec = codec_info["ffmpeg_v"]
        acodec = codec_info["ffmpeg_a"]
        tag = codec_info.get("tag", "")
        if job.hardware == "nvenc":
            vcodec = "hevc_nvenc" if job.codec == "hevc" else "h264_nvenc"
        elif job.hardware == "qsv":
            vcodec = "hevc_qsv" if job.codec == "hevc" else "h264_qsv"
        elif job.hardware == "amf":
            vcodec = "hevc_amf" if job.codec == "hevc" else "h264_amf"
        cmd = [self.ffmpeg_path, "-y", "-hide_banner", "-i", job.video_path, "-c:v", vcodec]
        if job.hardware == "none":
            cmd += ["-crf", str(job.crf), "-preset", job.preset]
            if job.codec == "hevc":
                cmd += ["-x265-params", "log-level=error"]
        else:
            cmd += ["-cq", str(job.crf)]
            if job.hardware == "nvenc":
                cmd += ["-preset", "p4"]
            else:
                cmd += ["-preset", "medium"]
        if tag and job.container == "mp4":
            cmd += ["-tag:v", tag]
        if job.keep_audio_copy:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", acodec, "-b:a", job.audio_bitrate, "-ac", str(job.audio_channels)]
        cmd += ["-progress", "pipe:1", "-nostats", job.output_path]
        return cmd

    def encode(self, job: EncodeJob, progress_cb: Optional[Callable[[float, str], None]] = None) -> EncodeJob:
        self._stop_requested = False
        self.current_job = job
        job.status = "running"
        job.start_time = time.time()
        job.original_size = os.path.getsize(job.video_path) if os.path.exists(job.video_path) else 0

        log(f"Encode start: {job.video_path}")
        log(f"  -> output: {job.output_path}")
        log(f"  codec={job.codec} crf={job.crf} preset={job.preset} hw={job.hardware} dry={job.dry_run}")
        log(f"  ffmpeg binary: {self.ffmpeg_path}")
        log(f"  source exists: {os.path.exists(job.video_path)} size={job.original_size}")

        if not os.path.exists(job.video_path):
            job.status = "error"
            job.message = f"Fichier source introuvable: {job.video_path}"
            job.end_time = time.time()
            log(job.message, level="ERROR")
            if progress_cb:
                progress_cb(0.0, job.message)
            return job

        if job.dry_run:
            job.progress = 100.0
            job.status = "done"
            job.message = "Dry-run (aucune écriture)"
            job.end_time = time.time()
            log(job.message)
            if progress_cb:
                progress_cb(100.0, job.message)
            return job

        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        cmd = self.build_command(job)
        log(f"  cmd: {' '.join(cmd)}")

        ffmpeg_tail: List[str] = []
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            job.process = proc

            duration_s = 0.0
            for line in proc.stdout:
                if self._stop_requested:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    job.status = "cancelled"
                    job.message = "Annulé par l'utilisateur"
                    log(job.message, level="WARN")
                    break

                line = line.strip()
                if line:
                    ffmpeg_tail.append(line)
                    if len(ffmpeg_tail) > 80:
                        ffmpeg_tail = ffmpeg_tail[-80:]

                if line.startswith("out_time_ms="):
                    try:
                        out_ms = int(line.split("=")[1]) / 1_000_000
                        if duration_s > 0:
                            pct = min(99.0, (out_ms / duration_s) * 100)
                            job.progress = pct
                            if progress_cb:
                                progress_cb(pct, f"{pct:.1f}%")
                    except ValueError:
                        pass
                elif "Duration:" in line:
                    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", line)
                    if m:
                        h, m_, s = m.groups()
                        duration_s = int(h) * 3600 + int(m_) * 60 + float(s)

            ret = proc.wait()
            if job.status != "cancelled":
                if ret == 0 and os.path.exists(job.output_path):
                    job.progress = 100.0
                    job.status = "done"
                    job.output_size = os.path.getsize(job.output_path)
                    job.message = "Terminé"
                    log(f"Encode OK ({job.output_size} bytes)")
                else:
                    job.status = "error"
                    tail = "\n".join(ffmpeg_tail[-30:])
                    log(f"Encode FAIL exit={ret}", level="ERROR")
                    log(f"FFmpeg last lines:\n{tail}", level="ERROR")
                    low = tail.lower()
                    if "driver does not support" in low or "nvenc api" in low or (
                        "nvenc" in low and ("required" in low or "found:" in low)
                    ):
                        job.message = (
                            "Échec NVIDIA/NVENC : pilote trop ancien pour ce FFmpeg. "
                            "→ Mettez à jour le pilote NVIDIA au plus récent, "
                            "ou dans Paramètres choisissez « Aucune — processeur seul »."
                        )
                    elif "no such file" in low or "cannot find" in low:
                        job.message = f"FFmpeg exit code {ret} — fichier introuvable"
                    elif "permission" in low:
                        job.message = f"FFmpeg exit code {ret} — permission refusée"
                    elif "qsv" in low or "amf" in low:
                        job.message = (
                            f"Échec accélération matérielle ({job.hardware}). "
                            "→ Dans Paramètres, choisissez « Aucune — processeur seul »."
                        )
                    elif "invalid" in low and "encoder" in low:
                        job.message = (
                            f"FFmpeg exit code {ret} — encodeur invalide. "
                            "Si vous utilisez NVIDIA/Intel/AMD, essayez « processeur seul » dans Paramètres."
                        )
                    else:
                        job.message = f"FFmpeg exit code {ret}"
        except Exception as e:
            job.status = "error"
            job.message = str(e)
            log(f"Encode exception: {e}", level="ERROR")
            if ffmpeg_tail:
                log("\n".join(ffmpeg_tail[-20:]), level="ERROR")
        finally:
            job.end_time = time.time()
            job.process = None
            self.current_job = None
            if progress_cb and job.status != "cancelled":
                progress_cb(job.progress, job.message)
        return job

    def stop(self):
        self._stop_requested = True
        if self.current_job and self.current_job.process:
            try:
                self.current_job.process.terminate()
            except Exception:
                pass

    def replace_original(self, job: EncodeJob) -> bool:
        if job.status != "done" or not os.path.exists(job.output_path):
            return False
        try:
            original = Path(job.video_path)
            tmp_backup = original.with_suffix(original.suffix + ".bak")
            shutil.move(str(original), str(tmp_backup))
            shutil.move(job.output_path, str(original))
            tmp_backup.unlink(missing_ok=True)
            return True
        except Exception as e:
            job.message = f"Erreur remplacement: {e}"
            log(job.message, level="ERROR")
            return False
