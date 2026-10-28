import os
import threading
import time


from app.encoder import EncodeJob, Encoder
from app.jobs import finalize_job_settings, make_job_for_video, temp_output_path
from app.plex_client import VideoInfo
from app.config import DEFAULT_SETTINGS
from tests.conftest import make_video, needs_ffmpeg, probe

pytestmark = needs_ffmpeg


def _job(src, **settings):
    s = dict(DEFAULT_SETTINGS, hardware_accel="none", **settings)
    ext = src.suffix.lstrip(".")
    v = VideoInfo(rating_key="1", title=src.stem, library="Films", library_type="movie",
                  file_path=str(src), container=ext, video_codec="mpeg4")
    job = make_job_for_video(v, s, s.get("preferred_codec"))
    finalize_job_settings(job, s, "none")
    return job


def streams(path, kind):
    return [s for s in probe(path)["streams"] if s["codec_type"] == kind]


def test_avi_two_audio_tracks_to_mkv_and_replace(tmp_path):
    src = make_video(tmp_path / "Film.avi", audio=(("mp3", 2, "fre"), ("mp3", 2, "eng")))
    job = _job(src)
    assert job.output_path.endswith(".ppix-tmp-Film.avi.mkv")
    assert job.final_path.endswith("Film.mkv")
    Encoder().encode(job)
    assert job.status == "done", job.message
    assert streams(job.output_path, "video")[0]["codec_name"] == "hevc"
    a = streams(job.output_path, "audio")
    assert len(a) == 2   # (l'AVI ne stocke pas les langues)
    assert Encoder.replace_original(job)
    assert job.status == "replaced"
    assert not src.exists() and (tmp_path / "Film.mkv").exists()
    assert not os.path.exists(job.output_path)
    assert list(tmp_path.glob("*.ppix-bak")) == []


def test_odd_dimensions_rounded(tmp_path):
    src = make_video(tmp_path / "Odd.mkv", vcodec="ffv1", size="321x241", pix_fmt="yuv444p")
    job = _job(src)
    Encoder().encode(job)
    assert job.status == "done", job.message
    v = streams(job.output_path, "video")[0]
    assert (v["width"], v["height"]) == (320, 240)


def test_interlaced_51_subs_mkv(tmp_path):
    src = make_video(tmp_path / "Ep.mkv", vcodec="mpeg2video", size="720x576", interlaced=True,
                     audio=(("ac3", 6, "fre"), ("aac", 2, "eng")), subs=True)
    job = _job(src, preferred_codec="h264")
    Encoder().encode(job)
    assert job.status == "done", job.message
    v = streams(job.output_path, "video")[0]
    assert v["codec_name"] == "h264"
    assert v.get("field_order", "progressive") == "progressive"
    a = streams(job.output_path, "audio")
    assert [x["channels"] for x in a] == [6, 2]           # 5.1 conservé
    assert [x["codec_name"] for x in a] == ["ac3", "aac"]  # copie
    assert [x["tags"]["language"] for x in a] == ["fre", "eng"]  # langues conservées
    assert len(streams(job.output_path, "subtitle")) == 1
    assert any("désentrelacement" in p for p in job.plan)


def test_stereo_downmix_and_mp4_audio_reencode(tmp_path):
    src = make_video(tmp_path / "Mp4.mp4", vcodec="mpeg4", audio=(("ac3", 6, "fre"),), subs=True)
    job = _job(src, audio_channels=2)
    Encoder().encode(job)
    assert job.status == "done", job.message
    a = streams(job.output_path, "audio")[0]
    assert a["codec_name"] == "aac" and a["channels"] == 2
    assert streams(job.output_path, "subtitle")[0]["codec_name"] == "mov_text"
    v = streams(job.output_path, "video")[0]
    assert v["codec_tag_string"] == "hvc1"


def test_10bit_kept_with_hevc(tmp_path):
    src = make_video(tmp_path / "Hdr.mkv", vcodec="libx264", pix_fmt="yuv420p10le")
    job = _job(src)
    Encoder().encode(job)
    assert job.status == "done", job.message
    assert streams(job.output_path, "video")[0]["pix_fmt"] == "yuv420p10le"


def test_dry_run_writes_nothing(tmp_path):
    src = make_video(tmp_path / "Dry.avi")
    job = _job(src, dry_run=True)
    Encoder().encode(job)
    assert job.status == "simulated"
    assert not os.path.exists(job.output_path)
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "home") == ["Dry.avi"]


def test_missing_source(tmp_path):
    job = _job(tmp_path / "absent.avi")
    Encoder().encode(job)
    assert job.status == "error" and "introuvable" in job.message


def test_cancel_removes_partial(tmp_path):
    src = make_video(tmp_path / "Long.avi", dur=60, size="1280x720")
    job = _job(src, preset="veryslow")
    enc = Encoder()
    t = threading.Thread(target=enc.encode, args=(job,))
    t.start()
    deadline = time.time() + 30
    while time.time() < deadline and not (job.process and os.path.exists(job.output_path)):
        time.sleep(0.1)
    time.sleep(0.5)
    enc.stop()
    t.join(30)
    assert job.status == "cancelled"
    assert not os.path.exists(job.output_path)
    assert src.exists()


def test_corrupt_source_error(tmp_path):
    src = tmp_path / "Bad.avi"
    src.write_bytes(b"RIFF" + os.urandom(5000))
    job = _job(src)
    Encoder().encode(job)
    assert job.status == "error"
    assert not os.path.exists(job.output_path)
    assert src.exists()


def test_validation_rejects_truncated_output(tmp_path, monkeypatch):
    src = make_video(tmp_path / "Trunc.avi", dur=6)
    job = _job(src)
    enc = Encoder()
    real_build = enc.build_command

    def short(job_, info=None):
        cmd = real_build(job_, info)
        return cmd[:-1] + ["-t", "2", cmd[-1]]   # sortie coupée à 2 s
    monkeypatch.setattr(enc, "build_command", short)
    enc.encode(job)
    assert job.status == "error" and "durée" in job.message
    assert not os.path.exists(job.output_path) and src.exists()


def test_keep_both_and_replace_conflicts(tmp_path):
    src = make_video(tmp_path / "Both.avi")
    job = _job(src)
    Encoder().encode(job)
    assert Encoder.keep_both(job)
    assert job.status == "kept"
    assert (tmp_path / "Both.optimized.mkv").exists() and src.exists()

    src2 = make_video(tmp_path / "Conf.avi")
    (tmp_path / "Conf.mkv").write_bytes(b"x")       # le nom final existe déjà
    job2 = _job(src2)
    Encoder().encode(job2)
    assert not Encoder.replace_original(job2)
    assert src2.exists() and job2.status == "done"


def test_replace_keep_backup(tmp_path):
    src = make_video(tmp_path / "Bak.mkv")
    job = _job(src)
    Encoder().encode(job)
    assert Encoder.replace_original(job, keep_backup=True)
    assert (tmp_path / "Bak.mkv").exists() and (tmp_path / "Bak.mkv.ppix-bak").exists()


def test_hw_fallback_to_cpu(tmp_path):
    src = make_video(tmp_path / "Hw.avi")
    job = _job(src)
    job.hardware = "nvenc"      # pas de GPU ici : doit retomber sur le CPU
    Encoder().encode(job)
    assert job.status == "done", job.message
    assert job.hw_used == "none"


def test_temp_output_windows_path():
    assert temp_output_path(r"\\NAS\Films\A.b\Film.avi", "mkv") == r"\\NAS\Films\A.b\.ppix-tmp-Film.avi.mkv"


def test_hw_commands_contain_quality_params():
    enc = Encoder()
    info = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "mpeg4", "width": 640,
                         "height": 480, "pix_fmt": "yuv420p"},
                        {"index": 1, "codec_type": "audio", "codec_name": "mp3", "channels": 2}]}
    for hw, needle in (("nvenc", "-cq"), ("qsv", "-global_quality"), ("amf", "-qp_i"), ("none", "-crf")):
        j = EncodeJob("in.avi", output_path="out.mkv", hardware=hw, container="mkv", crf=24)
        cmd = enc.build_command(j, info)
        assert needle in cmd, (hw, cmd)
        assert "-map" in cmd


def test_same_stem_sources_do_not_share_temp(tmp_path):
    a = make_video(tmp_path / "Film.avi", dur=2)
    b = make_video(tmp_path / "Film.mkv", dur=4)
    ja, jb = _job(a), _job(b)
    assert ja.output_path != jb.output_path
    Encoder().encode(ja)
    Encoder().encode(jb)
    assert ja.status == jb.status == "done"
    assert abs(float(probe(ja.output_path)["format"]["duration"]) - 2) < 0.5
    # Film.avi → Film.mkv refusé car Film.mkv existe déjà (autre fichier)
    assert not Encoder.replace_original(ja)
    assert Encoder.replace_original(jb)


def _audio_job(src, **settings):
    s = dict(DEFAULT_SETTINGS, hardware_accel="nvenc", keep_subtitles=False, **settings)
    v = VideoInfo(rating_key="5", title=src.stem, library="Films", library_type="movie",
                  file_path=str(src), container=src.suffix.lstrip("."), video_codec="h264",
                  audio_codec="truehd", reason="audio TrueHD", audio_only=True)
    job = make_job_for_video(v, s)
    finalize_job_settings(job, s, "nvenc")
    return job


def test_audio_only_truehd_to_ac3(tmp_path):
    from tests.conftest import make_truehd
    src = make_truehd(tmp_path / "Movie.mkv")
    src_video = streams(src, "video")[0]
    job = _audio_job(src)
    assert job.audio_only
    Encoder().encode(job)
    assert job.status == "done", job.message
    assert job.hw_used == "none"                       # jamais de GPU pour l'audio
    v = streams(job.output_path, "video")[0]
    assert v["codec_name"] == src_video["codec_name"] and v["width"] == src_video["width"]
    a = streams(job.output_path, "audio")
    assert a[0]["codec_name"] == "ac3" and a[0]["channels"] == 6
    assert int(a[0]["sample_rate"]) == 48000 and int(a[0]["bit_rate"]) == 640000
    assert a[0]["tags"]["language"] == "eng" and a[0]["tags"]["title"] == "AC3 5.1"
    assert a[1]["codec_name"] == "aac"                 # autre piste copiée
    assert len(streams(job.output_path, "subtitle")) == 1   # sous-titres gardés même si l'option est décochée
    assert any("vidéo copiée" in p for p in job.plan)
    assert Encoder.replace_original(job)
    assert (tmp_path / "Movie.mkv").exists() and job.final_path.endswith("Movie.mkv")
    assert streams(tmp_path / "Movie.mkv", "audio")[0]["codec_name"] == "ac3"


def test_audio_only_without_truehd_is_error(tmp_path):
    src = make_video(tmp_path / "NoThd.mkv", audio=(("aac", 2, "fre"),))
    job = _audio_job(src)
    Encoder().encode(job)
    assert job.status == "error" and "TrueHD" in job.message
    assert src.exists() and not os.path.exists(job.output_path)
