from app.config import DEFAULT_SETTINGS
from app.encoder import EncodeJob, Encoder
from app.jobs import default_local_destination, finalize_job_settings, make_local_job
from tests.conftest import make_video, needs_ffmpeg, probe

pytestmark = needs_ffmpeg


def audio(path):
    return [s for s in probe(path)["streams"] if s["codec_type"] == "audio"]


def run_local(src, dest, codec="hevc", target="copy", layout="keep", delete=False):
    job = make_local_job(str(src), codec, target, layout, str(dest), delete_original=delete,
                         info=Encoder().probe(str(src)))
    finalize_job_settings(job, dict(DEFAULT_SETTINGS, hardware_accel="none"), "none")
    Encoder().encode(job)
    assert job.status == "done", job.message
    assert Encoder.save_local(job), job.message
    return job


def test_default_destination():
    assert default_local_destination(r"D:\Vidéos\a.mkv") == r"D:\Vidéos\a.mkv"
    assert default_local_destination(r"\\NAS\v\a.avi") == r"\\NAS\v\a.mkv"
    assert default_local_destination("/x/b.mp4") == "/x/b.mp4"


def test_same_file_replaced_safely(tmp_path):
    src = make_video(tmp_path / "Vacances.mkv", audio=(("ac3", 6, "fre"),))
    job = run_local(src, src, codec="h264", target="aac", layout="stereo")
    assert job.status == "replaced"
    v = [s for s in probe(src)["streams"] if s["codec_type"] == "video"][0]
    assert v["codec_name"] == "h264"
    a = audio(src)[0]
    assert a["codec_name"] == "aac" and a["channels"] == 2   # (MKV : pas de débit par piste)
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "home") == ["Vacances.mkv"]


def test_avi_to_mkv_same_name_deletes_original(tmp_path):
    src = make_video(tmp_path / "Old.avi")
    dest = tmp_path / "Old.mkv"
    job = run_local(src, dest, target="ac3", delete=True)
    assert job.status == "replaced" and dest.exists() and not src.exists()
    assert audio(dest)[0]["codec_name"] == "ac3"


def test_other_folder_keeps_original(tmp_path):
    src = make_video(tmp_path / "Clip.mkv", audio=(("ac3", 6, "fre"), ("aac", 2, "eng")))
    out = tmp_path / "out"
    out.mkdir()
    dest = out / "Clip.mp4"
    job = run_local(src, dest, target="eac3", layout="5.1")
    assert job.status == "saved" and src.exists() and dest.exists()
    a = audio(dest)
    assert [x["codec_name"] for x in a] == ["eac3", "eac3"]
    assert [x["channels"] for x in a] == [6, 2]           # stéréo non « gonflée » en 5.1
    assert not (out / ".ppix-travail").exists()


def test_audio_target_rules():
    enc = Encoder()
    info = {"streams": [
        {"index": 0, "codec_type": "video", "codec_name": "mpeg4", "width": 640, "height": 480, "pix_fmt": "yuv420p"},
        {"index": 1, "codec_type": "audio", "codec_name": "truehd", "channels": 8, "sample_rate": "96000"},
        {"index": 2, "codec_type": "audio", "codec_name": "dts", "channels": 6, "sample_rate": "48000"}]}

    def cmd(target, layout, cont="mkv"):
        j = EncodeJob("in.mkv", output_path=f"o.{cont}", container=cont, audio_target=target, audio_layout=layout)
        return enc.build_command(j, info), j

    c, _ = cmd("ac3", "keep")
    assert c[c.index("-c:a:0") + 1] == "ac3" and c[c.index("-ac:a:0") + 1] == "6"   # 7.1 → 5.1
    assert c[c.index("-b:a:0") + 1] == "640k" and c[c.index("-ar:a:0") + 1] == "48000"
    c, _ = cmd("mp3", "keep")
    assert c[c.index("-c:a:1") + 1] == "libmp3lame" and c[c.index("-ac:a:1") + 1] == "2"
    c, _ = cmd("aac", "stereo")
    assert c[c.index("-b:a:0") + 1] == "192k" and c[c.index("-ac:a:0") + 1] == "2"
    c, _ = cmd("copy", "keep", "mkv")
    assert c[c.index("-c:a:0") + 1] == "copy" and c[c.index("-c:a:1") + 1] == "copy"
    c, j = cmd("copy", "keep", "mp4")                    # TrueHD/DTS interdits en MP4 → AAC
    assert c[c.index("-c:a:0") + 1] == "aac" and c[c.index("-c:a:1") + 1] == "aac"
    assert any("impossible en MP4" in p for p in j.plan)
