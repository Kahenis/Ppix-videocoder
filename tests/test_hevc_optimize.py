import os
from types import SimpleNamespace as NS

import pytest

from app.config import DEFAULT_SETTINGS, is_light_hevc
from app.encoder import EncodeJob, Encoder, video_bitrate_cap
from app.jobs import make_job_for_video
from app.plex_client import VideoInfo
from tests.conftest import make_video, needs_ffmpeg, probe

HEVC = {"format": {"bit_rate": "12000000"},
        "streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
                     "pix_fmt": "yuv420p", "tags": {"BPS": "11000000"}},
                    {"index": 1, "codec_type": "audio", "codec_name": "eac3", "channels": 6,
                     "tags": {"BPS": "640000"}}]}


def test_cap_optimize_hevc():
    v0 = HEVC["streams"][0]
    assert video_bitrate_cap(HEVC, v0, "hevc") == 9900                       # prudent : 90 %
    assert video_bitrate_cap(HEVC, v0, "hevc", optimize_hevc=True) == 6600   # optimisé : 60 %


def test_never_hevc_to_h264():
    v = VideoInfo(rating_key="1", title="x", library="F", library_type="movie", file_path="/x.mkv",
                  video_codec="hevc", container="mkv")
    assert make_job_for_video(v, dict(DEFAULT_SETTINGS, preferred_codec="h264"), "h264").codec == "hevc"
    j = EncodeJob("in.mkv", output_path="o.mkv", container="mkv", codec="h264", optimize_hevc=True)
    cmd = Encoder().build_command(j, HEVC)
    assert j.codec == "hevc" and cmd[cmd.index("-c:v") + 1] == "libx265"
    assert "vbv-maxrate=6600" in " ".join(cmd)
    assert any("pas de passage en H.264" in p for p in j.plan)


def test_light_hevc():
    assert is_light_hevc(2000, 1920, 1080)
    assert not is_light_hevc(8000, 1920, 1080)
    assert not is_light_hevc(0, 1920, 1080)


try:
    import tkinter  # noqa: F401
    tk_ok = True
except Exception:
    tk_ok = False
gui = pytest.mark.skipif(not tk_ok or not os.environ.get("DISPLAY"), reason="pas d'affichage (xvfb-run)")


@needs_ffmpeg
@gui
def test_local_wizard_hevc_and_queue_confirm(tmp_path, monkeypatch):
    import subprocess
    from app import local_convert
    from app.gui import App
    from tests.test_gui_workflow import pump

    src = tmp_path / "Telephone.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=d=5:s=640x360:r=25",
                    "-vf", "noise=alls=30:allf=t", "-c:v", "libx265", "-pix_fmt", "yuv420p",
                    "-x265-params", "log-level=error:bitrate=3000", "-tag:v", "hvc1", str(src)], check=True)
    app = App()
    try:
        app.settings.update(hardware_accel="none", preferred_codec="h264", preset="ultrafast")
        monkeypatch.setattr(local_convert.filedialog, "askopenfilename", lambda **k: str(src))
        local_convert.ask_local_file(app)
        dlg = app._local_dialog
        assert pump(app, lambda: str(dlg.btn_next.cget("state")) == "normal", 30)
        assert dlg.src_hevc and dlg.codec == "hevc"
        assert str(dlg.video_buttons["h264"].cget("state")) == "disabled"
        assert "Voulez-vous l'optimiser" in dlg.lbl_optimize.cget("text")
        assert "Taille estimée" in dlg.lbl_optimize.cget("text")
        assert dlg.btn_next.cget("text") == "Optimiser →"
        dlg.next()
        dlg.next()
        dlg.next()
        job = app.queue.snapshot()[0]
        assert job.optimize_hevc and job.codec == "hevc"
        ok = pump(app, lambda: job.status in ("replaced", "discarded", "error") and not app.queue_running, 120)
        assert ok, (job.status, job.message, app.queue_running, [j.status for j in app.queue.snapshot()])
        assert job.status == "replaced", job.message
        out = probe(src)
        assert [s["codec_name"] for s in out["streams"]][0] == "hevc"
        assert any("plafonné" in p for p in job.plan)

        # Scanner : H.265 coché → question au lancement ; « Non » retire de la file
        app.settings["preferred_codec"] = "h264"
        vids = [VideoInfo(rating_key=str(i), title=f"H{i}", library="F", library_type="movie",
                          file_path=str(tmp_path / f"H{i}.mkv"), video_codec="hevc", container="mkv")
                for i in range(2)]
        vids.append(VideoInfo(rating_key="9", title="Old", library="F", library_type="movie",
                              file_path=str(tmp_path / "Old.avi"), video_codec="mpeg4", container="avi"))
        app.queue.clear_finished()
        for v in vids:
            app.selected[v.key] = True
            app.queue.add(make_job_for_video(v, app.settings, "h264"))
        assert [j.codec for j in app.queue.snapshot()] == ["hevc", "hevc", "h264"]
        app.settings["dry_run"] = True
        app._start_queue()
        assert not app.queue_running and app._hevc_dialog.winfo_exists()
        app._hevc_dialog.no()
        # (Old.avi n'existe pas : il finit en erreur ; seul compte qu'il soit le seul traité)
        assert pump(app, lambda: not app.queue_running and app.queue.count("pending") == 0, 30)
        assert [j.plex_path for j in app.queue.snapshot()] == [str(tmp_path / "Old.avi")]
        assert not app.selected[vids[0].key]
        # « Optimiser » : encodés en H.265 optimisé
        app.queue.clear_finished()
        for v in vids[:2]:
            app.queue.add(make_job_for_video(v, app.settings))
        app._start_queue()
        app._hevc_dialog.yes()
        assert all(j.optimize_hevc for j in app.queue.snapshot())
        assert pump(app, lambda: not app.queue_running, 30)
    finally:
        app._close()
