"""FFmpeg-based video transcoder with progress reporting.

Principes (v1.6) :
- ffprobe d'abord : toutes les pistes audio et sous-titres sont conservées
  (VF/VO/sous-titres), le 5.1 reste 5.1 sauf choix « stéréo ».
- Encodage dans un sous-dossier caché .ppix-travail (avec .plexignore) à
  côté de l'original, pour que Plex ne voie jamais un fichier incomplet.
- Une image-clé toutes les 2 s (vignettes Plex, avance rapide).
- Validation de la sortie (durée, pistes) avant tout remplacement.
- Remplacement avec sauvegarde temporaire et retour arrière si erreur,
  extension corrigée (film.avi → film.mkv).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import (
    SUPPORTED_CODECS, MP4_COPY_AUDIO_CODECS, HARDWARE_SHORT,
    get_app_dir, is_audio_safe_for_plex,
)
from .logger import log

AC3_BITRATE = "640k"
KEYFRAME_SECONDS = 2.0          # une image-clé toutes les 2 s
KEYFRAME_MAX_GAP = 5.0          # au-delà : fichier refusé (vignettes noires, avance rapide lente)
WORK_DIR = ".ppix-travail"      # dossier de travail ignoré par Plex (.plexignore)
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
BITMAP_SUBS_MKV = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"}
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
INTERLACED_ORDERS = {"tt", "bb", "tb", "bt"}

_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW
_BELOW_NORMAL = 0x00004000       # BELOW_NORMAL_PRIORITY_CLASS


@dataclass
class EncodeJob:
    video_path: str                     # chemin source accessible depuis ce PC
    output_path: str = ""               # fichier temporaire d'encodage
    final_path: str = ""                # nom définitif après remplacement
    codec: str = "hevc"
    crf: int = 24
    preset: str = "medium"
    audio_bitrate: str = "192k"
    audio_channels: int = 0             # 0 = conserver, 2 = stéréo
    keep_audio_copy: bool = True
    keep_subtitles: bool = True
    deinterlace: bool = True
    hardware: str = "none"
    hw_fallback: bool = True
    audio_only: bool = False            # mode « Audio seule » : TrueHD → AC3, vidéo copiée
    low_priority: bool = True
    container: str = "mp4"
    dry_run: bool = False
    source_audio_codec: str = ""
    source_container: str = ""
    # --- Plex ---
    plex_path: str = ""
    rating_key: str = ""
    library: str = ""
    # --- état ---
    audio_mode: str = ""
    hw_used: str = ""
    hw_requested: str = ""
    plan: List[str] = field(default_factory=list)
    progress: float = 0.0
    speed: float = 0.0
    eta_s: float = 0.0
    status: str = "pending"   # pending running done simulated replaced kept discarded error cancelled
    message: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    original_size: int = 0
    output_size: int = 0

    @property
    def is_finished(self) -> bool:
        return self.status in ("done", "simulated", "replaced", "kept", "discarded", "error", "cancelled")

    @property
    def awaiting_decision(self) -> bool:
        return self.status == "done"

    @property
    def gain_ratio(self) -> float:
        if self.original_size and self.output_size:
            return 1.0 - self.output_size / self.original_size
        return 0.0


def _popen_flags(low_priority: bool = False) -> int:
    if not _WIN:
        return 0
    flags = _NO_WINDOW
    if low_priority:
        flags |= _BELOW_NORMAL
    return flags


def is_hdr_stream(v: Dict[str, Any]) -> bool:
    """HDR10/HLG : courbe PQ/HLG, ou BT.2020 en 10 bits (étiquette de courbe parfois absente)."""
    if (v.get("color_transfer") or "") in HDR_TRANSFERS:
        return True
    pix = (v.get("pix_fmt") or "").lower()
    ten = any(t in pix for t in ("10le", "10be", "12le", "12be", "p010"))
    return ten and (v.get("color_primaries") or "") == "bt2020"


def video_fps(v0: Dict[str, Any]) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = str(v0.get(key) or "")
        try:
            if "/" in val:
                num, den = val.split("/", 1)
                fps = float(num) / float(den) if float(den) else 0.0
            else:
                fps = float(val or 0)
        except ValueError:
            fps = 0.0
        if 1 <= fps <= 240:
            return fps
    return 25.0


def keyframe_interval(v0: Dict[str, Any], seconds: float = KEYFRAME_SECONDS) -> int:
    """Nombre d'images entre deux images-clés (≈ 2 s)."""
    return max(12, min(240, int(round(video_fps(v0) * seconds))))


def _fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}"
    if m:
        return f"{m} min"
    return f"{s} s"


def _bitrate_k(value: str) -> int:
    try:
        return int(str(value).lower().rstrip("k"))
    except ValueError:
        return 192


def prepare_work_dir(output_path: str) -> None:
    """Crée le dossier de travail caché avec un .plexignore : Plex ne voit jamais
    un fichier en cours d'écriture (sinon : vignette noire, élément fantôme)."""
    d = Path(output_path).parent
    if d.name != WORK_DIR:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        ign = d / ".plexignore"
        if not ign.exists():
            ign.write_text("*\n", encoding="utf-8")
        if _WIN:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(d), 0x02)  # type: ignore[attr-defined]
    except Exception as e:
        log(f"Dossier de travail {d}: {e}", level="WARN")


def cleanup_work_dir(path_in_dir: str) -> None:
    """Supprime le dossier de travail s'il ne contient plus que le .plexignore."""
    d = Path(path_in_dir).parent
    if d.name != WORK_DIR or not d.is_dir():
        return
    try:
        rest = [p for p in d.iterdir() if p.name != ".plexignore"]
        if not rest:
            (d / ".plexignore").unlink(missing_ok=True)
            d.rmdir()
    except Exception as e:
        log(f"Nettoyage {d}: {e}", level="DEBUG")


class Encoder:
    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg_path = self._find_ffmpeg(ffmpeg_path)
        self.ffprobe_path = self._find_ffprobe()
        self.current_job: Optional[EncodeJob] = None
        self._stop_requested = False

    # ------------------------------------------------------------------
    # Localisation des binaires
    # ------------------------------------------------------------------
    @staticmethod
    def _candidate_dirs() -> List[Path]:
        dirs: List[Path] = []
        app_dir = get_app_dir()
        dirs += [app_dir / "ffmpeg", app_dir]
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            meipass = Path(sys._MEIPASS)  # type: ignore[attr-defined]
            dirs += [meipass / "ffmpeg", meipass]
        dirs.append(Path(__file__).resolve().parent.parent / "ffmpeg")
        return dirs

    def _find_ffmpeg(self, preferred: str) -> str:
        if preferred and preferred != "ffmpeg" and Path(preferred).is_file():
            return preferred
        for d in self._candidate_dirs():
            for name in ("ffmpeg.exe", "ffmpeg"):
                c = d / name
                if c.is_file():
                    return str(c)
        return shutil.which("ffmpeg") or "ffmpeg"

    def _find_ffprobe(self) -> str:
        ff = Path(self.ffmpeg_path)
        for name in ("ffprobe.exe", "ffprobe"):
            c = ff.with_name(name)
            if c.is_file():
                return str(c)
        return shutil.which("ffprobe") or "ffprobe"

    # ------------------------------------------------------------------
    # Détection matérielle : on teste un vrai encodage, car le FFmpeg
    # embarqué liste NVENC/QSV/AMF même sans la carte correspondante.
    # ------------------------------------------------------------------
    def test_hw_encoder(self, hw: str) -> bool:
        enc = {"nvenc": "hevc_nvenc", "qsv": "hevc_qsv", "amf": "hevc_amf"}.get(hw)
        if not enc:
            return False
        pix = "nv12"
        cmd = [self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", "color=c=black:s=640x360:r=25:d=0.4",
               "-pix_fmt", pix, "-c:v", enc, "-f", "null", "-"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=25, creationflags=_popen_flags())
            ok = proc.returncode == 0
            if not ok:
                log(f"Test {enc} échoué: {(proc.stderr or '').strip()[-300:]}", level="DEBUG")
            return ok
        except Exception as e:
            log(f"Test {enc}: {e}", level="DEBUG")
            return False

    def detect_hardware(self) -> str:
        for hw in ("nvenc", "qsv", "amf"):
            if self.test_hw_encoder(hw):
                return hw
        return "none"

    # ------------------------------------------------------------------
    # Analyse de la source
    # ------------------------------------------------------------------
    def probe(self, path: str) -> Dict[str, Any]:
        cmd = [self.ffprobe_path, "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=120, creationflags=_popen_flags())
            if proc.returncode != 0:
                log(f"ffprobe exit {proc.returncode}: {(proc.stderr or '').strip()[-300:]}", level="WARN")
                return {}
            return json.loads(proc.stdout or "{}")
        except Exception as e:
            log(f"ffprobe: {e}", level="WARN")
            return {}

    @staticmethod
    def probe_duration(info: Dict[str, Any]) -> float:
        try:
            d = float((info.get("format") or {}).get("duration") or 0)
        except (TypeError, ValueError):
            d = 0.0
        if d <= 0:
            for s in info.get("streams") or []:
                try:
                    d = max(d, float(s.get("duration") or 0))
                except (TypeError, ValueError):
                    pass
        return d

    # ------------------------------------------------------------------
    # Construction de la commande
    # ------------------------------------------------------------------
    def build_command(self, job: EncodeJob, info: Optional[Dict[str, Any]] = None) -> List[str]:
        info = info or {}
        streams = info.get("streams") or []
        plan: List[str] = []
        _v = next((s for s in streams if s.get("codec_type") == "video"
                   and not (s.get("disposition") or {}).get("attached_pic")), {})
        if not job.audio_only and job.codec != "hevc" and is_hdr_stream(_v):
            # H.264 ne garde pas le HDR : l'image serait affichée trop sombre → H.265 imposé
            job.codec = "hevc"
            plan.append("source HDR : H.265 imposé pour conserver le HDR (H.264 l'assombrirait)")
        codec_info = SUPPORTED_CODECS.get(job.codec, SUPPORTED_CODECS["hevc"])
        hw = job.hardware if job.hardware in ("nvenc", "qsv", "amf") else "none"
        is_hevc = job.codec == "hevc"
        mp4 = job.container == "mp4"

        videos = [s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")]
        audios = [s for s in streams if s.get("codec_type") == "audio"]
        subs = [s for s in streams if s.get("codec_type") == "subtitle"]
        v0 = videos[0] if videos else {}

        cmd = [self.ffmpeg_path, "-y", "-hide_banner", "-nostdin", "-loglevel", "warning",
               "-analyzeduration", "50M", "-probesize", "50M",
               "-i", job.video_path]

        # ---------------- mapping ----------------
        if job.audio_only:
            cmd += ["-map", "0:v?"]
        else:
            cmd += ["-map", f"0:{v0['index']}" if v0 else "0:v:0"]
        if audios:
            cmd += ["-map", "0:a"]
        kept_subs: List[Dict[str, Any]] = []
        if job.keep_subtitles or job.audio_only:   # en mode audio seule, rien d'autre ne change
            for s in subs:
                sc = (s.get("codec_name") or "").lower()
                if sc in TEXT_SUBS or (not mp4 and sc in BITMAP_SUBS_MKV):
                    kept_subs.append(s)
                    cmd += ["-map", f"0:{s['index']}"]
                else:
                    plan.append(f"sous-titre #{s.get('index')} ({sc or '?'}) ignoré : incompatible {job.container.upper()}")
        if not mp4 and any(s.get("codec_type") == "attachment" for s in streams):
            cmd += ["-map", "0:t?", "-c:t", "copy"]   # polices des sous-titres ASS
        cmd += ["-map_metadata", "0", "-map_chapters", "0"]

        # ---------------- vidéo ----------------
        if job.audio_only:
            hw = "none"
            cmd += ["-c:v", "copy"]
            vc = (v0.get("codec_name") or "").lower()
            if mp4 and vc == "hevc":
                cmd += ["-tag:v", "hvc1"]
            w, h = int(v0.get("width") or 0), int(v0.get("height") or 0)
            plan.insert(0, f"vidéo copiée sans réencodage ({vc or '?'} {w}x{h})")
        else:
            hw = self._video_args(cmd, plan, job, v0, hw, is_hevc, mp4, codec_info)
        self._audio_args(cmd, plan, job, audios, streams, mp4)

        # ---------------- sous-titres ----------------
        for i, s in enumerate(kept_subs):
            sc = (s.get("codec_name") or "").lower()
            if mp4:
                cmd += [f"-c:s:{i}", "mov_text"]
            elif sc == "mov_text":
                cmd += [f"-c:s:{i}", "srt"]
            else:
                cmd += [f"-c:s:{i}", "copy"]
        if kept_subs:
            plan.append(f"{len(kept_subs)} sous-titre(s) conservé(s)")

        # ---------------- sortie ----------------
        cmd += ["-max_muxing_queue_size", "4096"]
        if mp4:
            cmd += ["-movflags", "+faststart", "-f", "mp4"]
        else:
            cmd += ["-f", "matroska"]
        cmd += ["-progress", "pipe:1", "-nostats", job.output_path]
        job.plan = plan
        job.hw_used = hw
        return cmd

    @staticmethod
    def _video_args(cmd: List[str], plan: List[str], job: EncodeJob, v0: Dict[str, Any], hw: str,
                    is_hevc: bool, mp4: bool, codec_info: Dict[str, str]) -> str:
        pix_src = (v0.get("pix_fmt") or "").lower()
        ten_bit = any(t in pix_src for t in ("10le", "10be", "12le", "12be", "p010"))
        hdr = is_hdr_stream(v0)
        vfilters: List[str] = []
        if job.deinterlace and (v0.get("field_order") or "") in INTERLACED_ORDERS:
            vfilters.append("bwdif=mode=send_frame:deint=interlaced")
            plan.append("vidéo entrelacée → désentrelacement")
        w, h = int(v0.get("width") or 0), int(v0.get("height") or 0)
        if (w % 2) or (h % 2):
            vfilters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
            plan.append(f"dimensions impaires {w}x{h} → arrondies")
        if vfilters:
            cmd += ["-vf", ",".join(vfilters)]

        # Image-clé toutes les ~2 s : vignettes Plex, avance rapide (ExoPlayer…)
        gop = keyframe_interval(v0)

        if hw == "nvenc":
            vcodec = "hevc_nvenc" if is_hevc else "h264_nvenc"
        elif hw == "qsv":
            vcodec = "hevc_qsv" if is_hevc else "h264_qsv"
        elif hw == "amf":
            vcodec = "hevc_amf" if is_hevc else "h264_amf"
        else:
            vcodec = codec_info["ffmpeg_v"]
        cmd += ["-c:v", vcodec, "-g", str(gop)]

        keep_10bit = ten_bit and is_hevc
        if ten_bit and not is_hevc:
            plan.append("source 10 bits → 8 bits (H.264)" + (" — HDR perdu, préférez H.265" if hdr else ""))
        if hw == "none":
            pix_out = "yuv420p10le" if keep_10bit else "yuv420p"
        else:
            pix_out = "p010le" if keep_10bit else "nv12"
        cmd += ["-pix_fmt", pix_out]

        q = int(job.crf)
        if hw == "none":
            crf = q if is_hevc else max(0, q - 4)   # x264 donne une qualité équivalente ~4 points plus bas
            cmd += ["-crf", str(crf), "-preset", job.preset or "medium"]
            if is_hevc:
                # GOP fermé : chaque image-clé est un vrai point de départ (vieux décodeurs TV)
                cmd += ["-x265-params", f"log-level=error:keyint={gop}:min-keyint={max(1, gop // 2)}:open-gop=0"]
            else:
                cmd += ["-profile:v", "high", "-keyint_min", str(max(1, gop // 2))]
        elif hw == "nvenc":
            cq = q if is_hevc else max(0, q - 2)
            cmd += ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(cq),
                    "-b:v", "0", "-spatial-aq", "1"]
            if keep_10bit:
                cmd += ["-profile:v", "main10"]
        elif hw == "qsv":
            cmd += ["-global_quality", str(q), "-preset", "medium"]
        elif hw == "amf":
            cmd += ["-quality", "balanced", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q + 2)]
            if not is_hevc:
                cmd += ["-qp_b", str(q + 4)]

        for key, opt in (("color_primaries", "-color_primaries"),
                         ("color_transfer", "-color_trc"),
                         ("color_space", "-colorspace")):
            val = v0.get(key)
            if val and val not in ("unknown", "reserved"):
                cmd += [opt, val]
        if mp4:
            cmd += ["-tag:v", codec_info.get("tag", "")] if codec_info.get("tag") else []
        plan.insert(0, f"vidéo : {v0.get('codec_name', '?')} {w}x{h} → {vcodec}"
                       f" ({'10' if keep_10bit else '8'} bits{', HDR' if hdr and keep_10bit else ''},"
                       f" image-clé / {gop} images)")
        return hw

    @staticmethod
    def _audio_args(cmd: List[str], plan: List[str], job: EncodeJob, audios: List[Dict[str, Any]],
                    streams: List[Dict[str, Any]], mp4: bool) -> None:
        modes = []
        base_k = _bitrate_k(job.audio_bitrate)
        for i, a in enumerate(audios):
            ac = (a.get("codec_name") or "").lower()
            ch = int(a.get("channels") or 2)
            lang = (a.get("tags") or {}).get("language", "")
            if job.audio_only:
                if ac == "truehd":
                    out_ch = min(ch, 6)          # AC3 : 5.1 maximum (7.1 → 5.1)
                    layout = {1: "1.0", 2: "2.0", 6: "5.1"}.get(out_ch, f"{out_ch}ch")
                    cmd += [f"-c:a:{i}", "ac3", f"-b:a:{i}", AC3_BITRATE, f"-ac:a:{i}", str(out_ch),
                            f"-ar:a:{i}", "48000", f"-metadata:s:a:{i}", f"title=AC3 {layout}"]
                    modes.append("ac3")
                    plan.append(f"audio #{i} {lang} TrueHD {ch}ch → AC3 {layout} {AC3_BITRATE}".replace("  ", " "))
                else:
                    cmd += [f"-c:a:{i}", "copy"]
                    modes.append("copy")
                    plan.append(f"audio #{i} {lang} {ac} {ch}ch → copie".replace("  ", " "))
                continue
            want_stereo = job.audio_channels == 2 and ch > 2
            copy_ok = (job.keep_audio_copy and is_audio_safe_for_plex(ac) and not want_stereo
                       and (ac in MP4_COPY_AUDIO_CODECS if mp4 else True))
            if copy_ok:
                cmd += [f"-c:a:{i}", "copy"]
                modes.append("copy")
                plan.append(f"audio #{i} {lang or ''} {ac} {ch}ch → copie".replace("  ", " "))
            else:
                out_ch = 2 if want_stereo else min(ch, 8)
                kbps = base_k if out_ch <= 2 else min(640, int(base_k * out_ch / 2))
                cmd += [f"-c:a:{i}", "aac", f"-b:a:{i}", f"{kbps}k", f"-ac:a:{i}", str(out_ch)]
                modes.append("aac")
                plan.append(f"audio #{i} {lang or ''} {ac or '?'} {ch}ch → AAC {out_ch}ch {kbps}k".replace("  ", " "))
        if not audios and not streams and not job.audio_only:
            # Pas de ffprobe : comportement minimal, toutes pistes audio en AAC
            cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", job.audio_bitrate]
            modes.append("aac")
        job.audio_mode = "/".join(sorted(set(modes))) or "aucun"

    # ------------------------------------------------------------------
    # Encodage
    # ------------------------------------------------------------------
    def encode(self, job: EncodeJob, progress_cb: Optional[Callable[[float, str], None]] = None) -> EncodeJob:
        self._stop_requested = False
        self.current_job = job
        job.status = "running"
        job.message = "Analyse…"
        job.start_time = time.time()
        try:
            self._encode_inner(job, progress_cb)
        except Exception as e:  # filet de sécurité
            job.status = "error"
            job.message = f"Erreur inattendue : {e}"
            log(f"Encode exception: {e}", level="ERROR")
        finally:
            if job.status in ("error", "cancelled"):
                self.discard_output(job, quiet=True)
            job.end_time = time.time()
            job.process = None
            self.current_job = None
            if progress_cb:
                progress_cb(job.progress, job.message)
        return job

    def _encode_inner(self, job: EncodeJob, progress_cb) -> None:
        log(f"Encode start: {job.video_path}")
        if not os.path.exists(job.video_path):
            job.status = "error"
            job.message = f"Fichier source introuvable depuis ce PC : {job.video_path} — vérifiez la racine réseau."
            log(job.message, level="ERROR")
            return
        job.original_size = os.path.getsize(job.video_path)
        if not (Path(self.ffprobe_path).is_file() or shutil.which(self.ffprobe_path)):
            job.status = "error"
            job.message = "ffprobe introuvable (doit être à côté de ffmpeg.exe) — réinstallez l'application."
            log(job.message, level="ERROR")
            return
        info = self.probe(job.video_path)
        duration = self.probe_duration(info)
        if info and not any(s.get("codec_type") == "video" for s in info.get("streams") or []):
            job.status = "error"
            job.message = "Aucune piste vidéo détectée par ffprobe (fichier corrompu ?)"
            return

        if job.audio_only:
            job.hardware = "none"
        job.hw_requested = job.hardware
        if job.audio_only and info and not any(
                (s.get("codec_name") or "").lower() == "truehd"
                for s in info.get("streams") or [] if s.get("codec_type") == "audio"):
            job.status = "error"
            job.message = "Aucune piste TrueHD dans ce fichier (rien à convertir)."
            return
        attempts = [job.hardware]
        if job.hardware != "none" and job.hw_fallback:
            attempts.append("none")
        for n, hw in enumerate(attempts):
            job.hardware = hw
            cmd = self.build_command(job, info)
            log(f"  plan: {' | '.join(job.plan)}")
            log(f"  cmd: {subprocess.list2cmdline(cmd)}")
            if job.dry_run:
                job.progress = 100.0
                job.status = "simulated"
                job.message = "Simulation : " + " · ".join(job.plan[:4])
                return
            prepare_work_dir(job.output_path)   # à chaque essai : un échec précédent l'a nettoyé
            if n == 0 and not self._check_space(job):
                return
            ok = self._run_ffmpeg(job, cmd, duration, progress_cb)
            if job.status == "cancelled":
                return
            if ok:
                break
            if n + 1 < len(attempts):
                log(f"Échec {HARDWARE_SHORT.get(hw, hw)} — nouvel essai en CPU", level="WARN")
                self.discard_output(job, quiet=True)
                job.progress = 0.0
                if progress_cb:
                    progress_cb(0.0, f"Échec {HARDWARE_SHORT.get(hw, hw)} → reprise en CPU")
        else:
            return  # toutes les tentatives ont échoué (message déjà posé)

        # ---------------- validation ----------------
        if progress_cb:
            progress_cb(99.0, "Vérification du fichier encodé…")
        problem = self._validate_output(job, info, duration)
        if problem:
            job.status = "error"
            job.message = f"Fichier encodé invalide : {problem} — original conservé."
            log(job.message, level="ERROR")
            return
        job.output_size = os.path.getsize(job.output_path)
        job.progress = 100.0
        job.status = "done"
        gain = job.gain_ratio * 100
        fb = " (repli CPU)" if job.hw_used == "none" and attempts[0] != "none" else ""
        job.message = f"Terminé en {_fmt_duration(time.time() - job.start_time)}{fb} · " + (
            f"−{gain:.0f} %" if gain >= 0 else f"+{-gain:.0f} % (plus gros)")
        log(f"Encode OK {job.output_size} bytes ({gain:.1f} % gagnés)")

    def _check_space(self, job: EncodeJob) -> bool:
        try:
            free = shutil.disk_usage(str(Path(job.output_path).parent)).free
            need = int(job.original_size * 1.1) + 200 * 1024 * 1024
            if free < need:
                job.status = "error"
                job.message = (f"Espace disque insuffisant : {free / 1024**3:.1f} Go libres, "
                               f"~{need / 1024**3:.1f} Go nécessaires.")
                log(job.message, level="ERROR")
                return False
        except Exception:
            pass
        return True

    def _run_ffmpeg(self, job: EncodeJob, cmd: List[str], duration: float, progress_cb) -> bool:
        tail: List[str] = []
        job.status = "running"
        job.progress = 0.0
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_popen_flags(job.low_priority),
        )
        job.process = proc
        out_s = 0.0
        last_cb = 0.0
        assert proc.stdout is not None
        for raw in proc.stdout:
            if self._stop_requested:
                break
            line = raw.strip()
            if not line:
                continue
            if "=" in line and not line.startswith("["):
                key, _, val = line.partition("=")
                if key in ("out_time_us", "out_time_ms"):
                    try:
                        out_s = int(val) / 1_000_000
                    except ValueError:
                        pass
                    continue
                if key == "speed":
                    try:
                        job.speed = float(val.rstrip("x"))
                    except ValueError:
                        pass
                    continue
                if key == "progress":
                    if duration > 0:
                        job.progress = min(99.0, out_s / duration * 100)
                        if job.speed > 0:
                            job.eta_s = max(0.0, (duration - out_s) / job.speed)
                    now = time.time()
                    if progress_cb and (now - last_cb > 0.5 or val == "end"):
                        last_cb = now
                        msg = f"{job.progress:.1f} %"
                        if job.speed:
                            msg += f" · {job.speed:.1f}x"
                        if job.eta_s:
                            msg += f" · reste {_fmt_duration(job.eta_s)}"
                        progress_cb(job.progress, msg)
                    continue
                if re.match(r"^[a-z_0-9]+$", key):
                    continue  # autres clés -progress (bitrate, frame…)
            tail.append(line)
            if len(tail) > 80:
                tail = tail[-80:]
        if self._stop_requested:
            self._terminate(proc)
            job.status = "cancelled"
            job.message = "Annulé par l'utilisateur"
            log(job.message, level="WARN")
            return False
        ret = proc.wait()
        if ret == 0 and os.path.exists(job.output_path):
            return True
        job.status = "error"
        text = "\n".join(tail[-30:])
        log(f"Encode FAIL exit={ret} hw={job.hardware}\nFFmpeg:\n{text}", level="ERROR")
        job.message = self._explain_failure(ret, text, job.hardware)
        return False

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

    @staticmethod
    def _explain_failure(ret: int, text: str, hw: str) -> str:
        low = text.lower()
        if "driver does not support" in low or "nvenc api" in low or "minimum required nvidia driver" in low:
            return ("Échec NVIDIA/NVENC : pilote trop ancien pour ce FFmpeg. Mettez à jour le pilote "
                    "NVIDIA ou choisissez « processeur seul » dans Paramètres.")
        if hw != "none" and any(k in low for k in ("nvenc", "qsv", "amf", "hwupload", "device")):
            return f"Échec de l'accélération {HARDWARE_SHORT.get(hw, hw)}. Essayez « processeur seul » dans Paramètres."
        if "no space left" in low:
            return "Disque plein pendant l'encodage."
        if "permission denied" in low or "access is denied" in low:
            return f"FFmpeg ({ret}) : permission refusée — droits d'écriture sur le partage ?"
        if "no such file" in low:
            return f"FFmpeg ({ret}) : fichier introuvable"
        if "invalid data found" in low:
            return f"FFmpeg ({ret}) : fichier source endommagé ou illisible"
        last = text.strip().splitlines()[-1][:160] if text.strip() else ""
        return f"FFmpeg exit {ret}" + (f" — {last}" if last else "") + " (détails dans le journal)"

    def _validate_output(self, job: EncodeJob, src_info: Dict[str, Any], src_duration: float) -> str:
        if not os.path.exists(job.output_path) or os.path.getsize(job.output_path) < 1024:
            return "fichier absent ou vide"
        out = self.probe(job.output_path)
        if not out:
            return "illisible par ffprobe"
        ostreams = out.get("streams") or []
        if not any(s.get("codec_type") == "video" for s in ostreams):
            return "pas de piste vidéo"
        src_a = sum(1 for s in src_info.get("streams") or [] if s.get("codec_type") == "audio")
        out_a = sum(1 for s in ostreams if s.get("codec_type") == "audio")
        if src_info and out_a < src_a:
            return f"{out_a} piste(s) audio au lieu de {src_a}"
        out_d = self.probe_duration(out)
        if src_duration > 0 and out_d > 0:
            tol = max(2.0, src_duration * 0.01)
            if abs(out_d - src_duration) > tol:
                return f"durée {out_d:.0f}s au lieu de {src_duration:.0f}s"
        if job.audio_only:
            return ""   # vidéo copiée telle quelle : images-clés et images identiques à la source
        dur = out_d or src_duration
        gap = self.keyframe_max_gap(job.output_path, dur)
        if gap is not None and gap > KEYFRAME_MAX_GAP:
            return f"images-clés trop espacées ({gap:.0f} s) — vignettes Plex noires, avance rapide lente"
        black = self.black_output_check(job.video_path, job.output_path, dur)
        if black:
            return black
        return ""

    # ------------------------------------------------------------------
    # Contrôles d'images
    # ------------------------------------------------------------------
    def keyframe_times(self, path: str, read_interval: str = "") -> List[float]:
        cmd = [self.ffprobe_path, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0"]
        if read_interval:
            cmd += ["-read_intervals", read_interval]
        cmd.append(path)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=600, creationflags=_popen_flags())
        except Exception as e:
            log(f"Lecture des images-clés impossible : {e}", level="WARN")
            return []
        times = []
        for line in (proc.stdout or "").splitlines():
            parts = line.strip().split(",")
            if len(parts) >= 2 and "K" in parts[1]:
                try:
                    times.append(float(parts[0]))
                except ValueError:
                    pass
        return sorted(times)

    def keyframe_max_gap(self, path: str, duration: float) -> Optional[float]:
        """Plus grand écart entre images-clés (échantillons début + milieu pour les longs fichiers).

        None = mesure impossible ou fichier trop court pour être significatif.
        """
        if duration <= KEYFRAME_MAX_GAP + 1:
            return None
        windows = [(0.0, duration)] if duration <= 120 else [(0.0, 60.0), (duration / 2, 60.0)]
        worst = 0.0
        for start, length in windows:
            whole = length >= duration
            interval = "" if whole else f"{start:.0f}%+{length:.0f}"
            times = [t for t in self.keyframe_times(path, interval) if start - 1 <= t <= start + length + 1]
            if not times:
                return None if whole else length
            gaps = [b - a for a, b in zip(times, times[1:])]
            if start == 0:
                gaps.append(times[0])
            if whole or start + length >= duration - 0.5:
                gaps.append(duration - times[-1])
            if len(times) < 2 and not whole:
                gaps.append(length)
            worst = max(worst, max(gaps) if gaps else 0.0)
        return worst

    def frame_luma(self, path: str, t: float) -> Optional[float]:
        """Luminosité moyenne (0-255, noir vidéo ≈ 16) de l'image à l'instant t."""
        cmd = [self.ffmpeg_path, "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", path,
               "-frames:v", "1", "-vf",
               "scale=96:-2,format=gray,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
               "-f", "null", "-"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=120, creationflags=_popen_flags())
        except Exception:
            return None
        m = re.search(r"YAVG=([0-9.]+)", proc.stdout or "")
        return float(m.group(1)) if m else None

    def black_output_check(self, src: str, out: str, duration: float) -> str:
        """Refuse un fichier encodé noir là où l'original montre une image."""
        if duration < 2:
            return ""
        for frac in (0.25, 0.5, 0.75):
            t = duration * frac
            o = self.frame_luma(out, t)
            if o is None or o >= 22:
                continue
            s = self.frame_luma(src, t)
            if s is not None and s > 40:
                return f"image noire à {t:.0f} s alors que l'original est visible"
        return ""

    def stop(self) -> None:
        self._stop_requested = True
        job = self.current_job
        if job and job.process:
            try:
                job.process.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Après encodage
    # ------------------------------------------------------------------
    @staticmethod
    def discard_output(job: EncodeJob, quiet: bool = False) -> bool:
        try:
            if job.output_path and os.path.exists(job.output_path):
                os.remove(job.output_path)
                if not quiet:
                    log(f"Fichier encodé supprimé : {job.output_path}")
            if job.output_path:
                cleanup_work_dir(job.output_path)
            return True
        except Exception as e:
            log(f"Suppression impossible {job.output_path}: {e}", level="WARN")
            return False

    @staticmethod
    def keep_both(job: EncodeJob) -> bool:
        """Garde l'original et rend visible le fichier encodé : Film.optimized.mkv."""
        if not job.output_path or not os.path.exists(job.output_path):
            job.message = "Fichier encodé absent."
            return False
        final = Path(job.final_path or job.video_path)
        target = final.with_name(f"{final.stem}.optimized{final.suffix}")
        n = 2
        while target.exists():
            target = final.with_name(f"{final.stem}.optimized{n}{final.suffix}")
            n += 1
        try:
            os.replace(job.output_path, target)
        except Exception as e:
            job.message = f"Renommage impossible : {e}"
            log(job.message, level="ERROR")
            return False
        cleanup_work_dir(job.output_path)
        job.output_path = str(target)
        job.status = "kept"
        job.message = f"Les deux gardés → {target.name}"
        log(job.message)
        return True

    @staticmethod
    def replace_original(job: EncodeJob, keep_backup: bool = False) -> bool:
        """Remplace l'original par le fichier encodé, avec retour arrière en cas d'erreur.

        film.avi + .ppix-travail/film.avi.mkv → film.mkv (l'extension suit le conteneur réel).
        """
        if job.status != "done" or not os.path.exists(job.output_path):
            job.message = "Rien à remplacer (fichier encodé absent)."
            return False
        original = Path(job.video_path)
        final = Path(job.final_path or job.video_path)
        backup = original.with_name(original.name + ".ppix-bak")
        same_name = os.path.normcase(str(final)) == os.path.normcase(str(original))
        if not same_name and final.exists():
            job.message = f"Remplacement annulé : « {final.name} » existe déjà."
            log(job.message, level="ERROR")
            return False
        if backup.exists():
            job.message = f"Remplacement annulé : sauvegarde « {backup.name} » déjà présente."
            log(job.message, level="ERROR")
            return False
        try:
            os.replace(original, backup)
        except Exception as e:
            job.message = f"Erreur remplacement (original verrouillé ?) : {e}"
            log(job.message, level="ERROR")
            return False
        try:
            os.replace(job.output_path, final)
            cleanup_work_dir(job.output_path)
        except Exception as e:
            try:
                os.replace(backup, original)
            except Exception as e2:
                log(f"RETOUR ARRIÈRE IMPOSSIBLE : original sauvegardé sous {backup} ({e2})", level="ERROR")
            job.message = f"Erreur remplacement, original restauré : {e}"
            log(job.message, level="ERROR")
            return False
        if not keep_backup:
            try:
                backup.unlink()
            except Exception as e:
                log(f"Sauvegarde non supprimée {backup}: {e}", level="WARN")
        job.status = "replaced"
        job.message = f"Remplacé → {final.name}" + (" (original gardé en .ppix-bak)" if keep_backup else "")
        log(f"Remplacé: {original} → {final}")
        return True
