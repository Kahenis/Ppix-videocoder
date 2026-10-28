from app import config as c


def test_codec_acceptable():
    assert c.is_codec_acceptable("h264", "h264")
    assert c.is_codec_acceptable("hevc", "h264")
    assert c.is_codec_acceptable("avc1", "h264")
    assert not c.is_codec_acceptable("mpeg4", "h264")
    assert not c.is_codec_acceptable("h264", "hevc")
    assert not c.is_codec_acceptable("hevc", "none")


def test_optimization_reason_heavy_h264():
    assert c.optimization_reason("mpeg4", 1500, 720, 576, "h264") == "codec ancien"
    assert c.optimization_reason("h264", 5000, 1920, 1080, "h264", 8) == ""
    assert c.optimization_reason("h264", 15000, 1920, 1080, "h264", 8) == "débit élevé"
    # 720p : seuil réduit proportionnellement
    assert c.optimization_reason("h264", 5000, 1280, 720, "h264", 8) == "débit élevé"
    assert c.optimization_reason("hevc", 40000, 1920, 1080, "h264", 8) == ""


def test_resolve_hardware():
    assert c.resolve_hardware("auto", "nvenc") == "nvenc"
    assert c.resolve_hardware("auto", None) == "none"
    assert c.resolve_hardware("auto", "none") == "none"
    assert c.resolve_hardware("qsv", "nvenc") == "qsv"
    assert c.resolve_hardware("bogus", None) == "none"


def test_path_mapping_unc():
    m = c.apply_path_maps("/volume1/Media/Films/A (2001)/a.avi",
                          network_root=r"\\NAS\Media", plex_prefix="/volume1/Media")
    assert m == r"\\NAS\Media\Films\A (2001)\a.avi"
    # préfixe qui n'est qu'un début de nom de dossier : pas de mapping
    assert c.apply_path_maps("/volume1/MediaX/a.avi", network_root=r"\\NAS\Media",
                             plex_prefix="/volume1/Media") == "/volume1/MediaX/a.avi"
    assert c.apply_path_maps("/x/a.avi") == "/x/a.avi"
    maps = [{"from": "/data/films", "to": r"Z:\Films"}]
    assert c.apply_path_maps("/data/films/b.mkv", path_maps=maps) == r"Z:\Films\b.mkv"


def test_guess_prefix():
    assert c.guess_plex_prefix(r"\\NAS\Media", ["/volume1/Media/Films"]) == "/volume1/Media"
    assert c.guess_plex_prefix(r"\\192.168.1.2\films\\", ["/share/CACHEDEV1_DATA/Films"]) == "/share/CACHEDEV1_DATA/Films"
    assert c.guess_plex_prefix(r"\\NAS\Autre", ["/volume1/Media"]) == ""


def test_replace_extension():
    assert c.replace_extension(r"\\NAS\M\A.b\film.avi", "mkv") == r"\\NAS\M\A.b\film.mkv"
    assert c.replace_extension("/x/film", "mp4") == "/x/film.mp4"
    assert c.replace_extension("/x/.hidden", ".mkv") == "/x/.hidden.mkv"


def test_output_container():
    assert c.resolve_output_container("avi", {"mkv_for_legacy": True}) == "mkv"
    assert c.resolve_output_container("avi", {"mkv_for_legacy": False, "container": "mp4"}) == "mp4"
    assert c.resolve_output_container("mp4", {}) == "mp4"
    assert c.resolve_output_container("mkv", {}) == "mkv"
    assert c.resolve_output_container("ts", {}) == "mkv"


def test_settings_roundtrip_token_in_keyring():
    s = c.load_settings()
    assert s["audio_channels"] == 0
    s["token"] = "SECRET-TOKEN"
    s["network_root"] = r"\\NAS\Media"
    c.save_settings(s)
    raw = c.get_settings_path().read_text(encoding="utf-8")
    assert "SECRET-TOKEN" not in raw
    c._keyring_cache["value"] = None
    s2 = c.load_settings()
    assert s2["token"] == "SECRET-TOKEN"
    assert s2["network_root"] == r"\\NAS\Media"


def test_migration_v15_audio_channels():
    import json
    c.get_settings_path().write_text(json.dumps({"audio_channels": 2, "crf": 22}), encoding="utf-8")
    s = c.load_settings()
    assert s["audio_channels"] == 0 and s["crf"] == 22 and s["settings_version"] == 2
