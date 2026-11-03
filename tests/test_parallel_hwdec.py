import os
import subprocess
import sys
import time

import pytest

from app import sysmon
from app.config import DEFAULT_SETTINGS
from app.encoder import EncodeJob, Encoder, hw_decoder_for
from app.jobs import finalize_job_settings, make_job_for_video
from app.plex_client import VideoInfo
from tests.conftest import make_video, needs_ffmpeg

V_H264 = {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
          "pix_fmt": "yuv420p"}


def test_hw_decoder_choice(monkeypatch):
    assert hw_decoder_for("nvenc", V_H264) == "cuda"
    assert hw_decoder_for("nvenc", dict(V_H264, codec_name="hevc", pix_fmt="yuv420p10le")) == "cuda"
    assert hw_decoder_for("nvenc", dict(V_H264, pix_fmt="yuv420p10le")) == ""     # H.264 10 bits
    assert hw_decoder_for("nvenc", dict(V_H264, codec_name="mpeg4")) == ""        # Xvid/DivX
    assert hw_decoder_for("none", V_H264) == ""
    import app.encoder as e
    monkeypatch.setattr(e, "_WIN", True)
    assert hw_decoder_for("qsv", V_H264) == "d3d11va"
    assert hw_decoder_for("amf", dict(V_H264, codec_name="mpeg2video")) == "d3d11va"


def test_hwaccel_in_command():
    enc = Encoder()
    info = {"streams": [V_H264, {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2}]}
    j = EncodeJob("in.mkv", output_path="o.mkv", container="mkv", hardware="nvenc")
    cmd = enc.build_command(j, info)
    assert cmd.index("-hwaccel") < cmd.index("-i") and cmd[cmd.index("-hwaccel") + 1] == "cuda"
    assert j.hwdec_used == "cuda" and any("NVDEC" in p for p in j.plan)
    j2 = EncodeJob("in.mkv", output_path="o.mkv", container="mkv", hardware="nvenc", hw_decode=False)
    assert "-hwaccel" not in enc.build_command(j2, info)
    j3 = EncodeJob("in.mkv", output_path="o.mkv", container="mkv", hardware="nvenc", audio_only=True)
    assert "-hwaccel" not in enc.build_command(j3, info)


@needs_ffmpeg
@pytest.mark.parametrize("vcodec,expected_runs", [("libx264", 3), ("mpeg4", 2)])
def test_fallback_chain(tmp_path, monkeypatch, vcodec, expected_runs):
    """Sans carte graphique : GPU + décodage → GPU seul → processeur (le 2e essai est sauté si
    la source ne se décode de toute façon pas sur la carte)."""
    src = make_video(tmp_path / f"S_{vcodec}.mkv", vcodec=vcodec, dur=2, pix_fmt="yuv420p")
    s = dict(DEFAULT_SETTINGS, hardware_accel="nvenc")
    v = VideoInfo(rating_key="1", title="S", library="F", library_type="movie", file_path=str(src),
                  container="mkv", video_codec="h264")
    job = make_job_for_video(v, s)
    finalize_job_settings(job, s, "nvenc")
    enc = Encoder()
    calls = []
    real = enc._run_ffmpeg

    def spy(job_, cmd, d, cb):
        calls.append(("-hwaccel" in cmd, job_.hardware))
        return real(job_, cmd, d, cb)
    monkeypatch.setattr(enc, "_run_ffmpeg", spy)
    enc.encode(job)
    assert job.status == "done", job.message
    assert len(calls) == expected_runs
    assert calls[-1] == (False, "none") and job.hw_used == "none"
    if expected_runs == 3:
        assert calls[0] == (True, "nvenc") and calls[1] == (False, "nvenc")


def test_monitor_sums_processes():
    mon = sysmon.LoadMonitor(interval=0.3)
    mon.start()
    procs = [subprocess.Popen([sys.executable, "-c", "while True: pass"]) for _ in range(2)]
    try:
        mon.set_pids([p.pid for p in procs])
        end = time.time() + 10
        val = None
        while time.time() < end:
            s = mon.latest
            if s and s.cpu_proc is not None and s.cpu_proc > 0:
                val = s.cpu_proc
                if val > 60:
                    break
            time.sleep(0.2)
        ncpu = os.cpu_count() or 1
        assert val is not None and val > min(90, 150 / ncpu)   # deux processus comptés ensemble
    finally:
        for p in procs:
            p.kill()
        mon.stop()
