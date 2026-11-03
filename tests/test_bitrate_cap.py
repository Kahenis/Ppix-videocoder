from types import SimpleNamespace as NS

from app.config import DEFAULT_SETTINGS
from app.encoder import EncodeJob, Encoder, source_video_kbps, video_bitrate_cap
from app.plex_client import PlexClient
from app.repair import ConvertedFile, guess_codec_from_name
from tests.conftest import needs_ffmpeg, probe

INFO = {"format": {"bit_rate": "6000000"},
        "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                     "pix_fmt": "yuv420p", "tags": {"BPS-eng": "4500000"}},
                    {"index": 1, "codec_type": "audio", "codec_name": "eac3", "channels": 6,
                     "tags": {"BPS-eng": "640000"}}]}


def test_source_kbps_and_cap():
    assert source_video_kbps(INFO) == 4500
    v0 = INFO["streams"][0]
    assert video_bitrate_cap(INFO, v0, "hevc") == 2700        # 60 %
    assert video_bitrate_cap(INFO, v0, "h264") == 3825        # 85 %
    no_tag = {"format": {"bit_rate": "6000000"}, "streams": [dict(v0, tags={}), INFO["streams"][1]]}
    assert source_video_kbps(no_tag) == 6000 - 640
    legacy = {"format": {"size": str(700 * 1024 * 1024), "duration": "5400"},
              "streams": [{"index": 0, "codec_type": "video", "codec_name": "mpeg4"},
                          {"index": 1, "codec_type": "audio", "codec_name": "mp3", "channels": 2}]}
    assert video_bitrate_cap(legacy, legacy["streams"][0], "hevc") == source_video_kbps(legacy)


def test_cap_in_every_encoder_command():
    enc = Encoder()
    for hw, needles in (("none", ["vbv-maxrate=2700"]), ("nvenc", ["-maxrate", "2700k"]),
                        ("qsv", ["-maxrate", "2700k"]), ("amf", ["-maxrate", "vbr_peak"])):
        j = EncodeJob("in.mkv", output_path="o.mkv", container="mkv", hardware=hw, codec="hevc", crf=24)
        cmd = " ".join(enc.build_command(j, INFO))
        for n in needles:
            assert n in cmd, (hw, cmd)
        assert any("plafonné à 2700" in p for p in j.plan)
    j = EncodeJob("in.mkv", output_path="o.mkv", container="mkv", hardware="nvenc", codec="hevc", crf=20)
    cmd = enc.build_command(j, INFO)
    assert cmd[cmd.index("-cq") + 1] == "23"                  # calibrage NVENC (+3)
    j = EncodeJob("in.mkv", output_path="o.mkv", container="mkv", hardware="none", codec="h264", crf=24)
    cmd = enc.build_command(j, INFO)
    assert cmd[cmd.index("-maxrate") + 1] == "3825k"


@needs_ffmpeg
def test_real_h264_to_hevc_is_capped(tmp_path):
    src = tmp_path / "Web.mkv"
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=d=6:s=640x360:r=25",
                    "-vf", "noise=alls=40:allf=t", "-c:v", "libx264", "-b:v", "1500k", "-maxrate", "1500k",
                    "-bufsize", "3000k", str(src)], check=True)
    from app.jobs import finalize_job_settings, make_job_for_video
    from app.plex_client import VideoInfo
    s = dict(DEFAULT_SETTINGS, hardware_accel="none", crf=18, preset="ultrafast")
    v = VideoInfo(rating_key="1", title="Web", library="F", library_type="movie", file_path=str(src),
                  container="mkv", video_codec="h264")
    job = make_job_for_video(v, s)
    finalize_job_settings(job, s, "none")
    Encoder().encode(job)
    assert job.status == "done", job.message
    src_k = source_video_kbps(probe(src))
    out = probe(job.output_path)
    out_k = int(float(out["format"]["size"]) * 8 / float(out["format"]["duration"]) / 1000)
    assert out_k < src_k * 0.75, (src_k, out_k)
    assert job.output_size < job.original_size


def test_grown_file_cap_and_guess():
    cf = ConvertedFile(local_path=r"\\NAS\S01E01.x264-FW.mkv", plex_path="/s/S01E01.x264-FW.mkv", exists=True,
                       size=1_640_000_000, original_size=1_100_000_000, duration=2700, audio_kbps=1280,
                       source_codec=guess_codec_from_name("S01E01.1080p.WEB.x264-FW.mkv"))
    assert cf.grew and cf.needs_attention and "+49 %" in cf.size_label
    orig_video = 1_100_000_000 * 8 / 2700 / 1000 - 1280
    assert abs(cf.cap_kbps() - int(orig_video * 0.6)) <= 1


def test_verify_codec_retries_then_ok():
    calls = {"n": 0, "analyze": 0}

    class Item:
        ratingKey = 7
        media = [NS(videoCodec="h264", parts=[NS(file="/m/a.mkv")])]

        def reload(self):
            calls["n"] += 1
            if calls["analyze"]:
                self.media[0].videoCodec = "hevc"

    item = Item()
    pc = PlexClient()
    pc.server = NS(query=lambda *a, **k: calls.__setitem__("analyze", calls["analyze"] + 1),
                   _session=NS(put=None), fetchItem=lambda rk: item)
    pc.find_item_by_file = lambda path, lib="", rk="": item
    t = {"now": 0.0}
    import app.plex_client as m
    real = m.time.time
    m.time.time = lambda: t["now"]
    try:
        res = pc.verify_codec("/m/a.mkv", "Séries", "7", "hevc", timeout=60, poll=10,
                              sleep=lambda s: t.__setitem__("now", t["now"] + s))
    finally:
        m.time.time = real
    assert res == "ok" and calls["analyze"] >= 1
