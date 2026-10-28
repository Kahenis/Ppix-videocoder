from types import SimpleNamespace as NS

from app import posters
from app.config import DEFAULT_SETTINGS, reverse_path_map
from app.encoder import Encoder
from app.repair import analyze_converted, fix_poster, history_files, video_info_for
from tests.conftest import make_video, needs_ffmpeg

pytestmark = needs_ffmpeg


def _loki_like(path):
    """Clip qui commence et finit au noir, une seule image-clé (comme loki2.mp4)."""
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=7:s=640x360:r=24",
                    "-vf", "fade=in:0:30,fade=out:140:28", "-c:v", "libx264", "-g", "1000",
                    "-sc_threshold", "0", str(path)], check=True)
    return path


def test_extract_poster_avoids_black(tmp_path):
    clip = _loki_like(tmp_path / "loki.mp4")
    jpg = tmp_path / "p.jpg"
    assert posters.extract_poster("ffmpeg", str(clip), str(jpg), 7.0)
    assert posters.is_black_image(str(jpg)) is False


def test_is_black_image(tmp_path):
    from PIL import Image
    Image.new("RGB", (64, 64), (5, 5, 5)).save(tmp_path / "b.png")
    Image.new("RGB", (64, 64), (120, 90, 60)).save(tmp_path / "c.png")
    assert posters.is_black_image(str(tmp_path / "b.png")) is True
    assert posters.is_black_image(str(tmp_path / "c.png")) is False


def test_reverse_path_map():
    s = dict(DEFAULT_SETTINGS, network_root=r"\\NAS\Media", plex_prefix="/volume1/Media")
    assert reverse_path_map(r"\\NAS\Media\Perso\loki2.mp4", s) == "/volume1/Media/Perso/loki2.mp4"
    s2 = dict(DEFAULT_SETTINGS, path_maps=[{"from": "/data", "to": r"Z:\Data"}])
    assert reverse_path_map(r"Z:\Data\a\b.mkv", s2) == "/data/a/b.mkv"


class FakeItem:
    def __init__(self, rk, path, lib="Perso"):
        self.ratingKey = rk
        self.type = "movie"
        self.librarySectionTitle = lib
        self.media = [NS(parts=[NS(file=path)])]
        self.uploaded = []

    def uploadPoster(self, filepath=None):
        from app.posters import is_black_image
        self.uploaded.append(is_black_image(filepath))

    def lockPoster(self):
        self.locked = True


class FakePlex:
    def __init__(self, items, personal=("Perso",)):
        from app.plex_client import PlexClient
        self.server = object()
        self.items = items
        self.personal = personal
        self._norm = PlexClient._norm
        self.upload_poster = PlexClient.upload_poster

    def build_file_index(self, stop_event=None):
        return {self._norm(i.media[0].parts[0].file): i for i in self.items}

    def lookup(self, index, path):
        return index.get(self._norm(path))

    def is_personal_library(self, lib):
        return lib in self.personal

    def find_item_by_file(self, path, library="", rk=""):
        return next((i for i in self.items if self._norm(i.media[0].parts[0].file) == self._norm(path)), None)


def test_history_files_dedup():
    h = [{"file": "a", "status": "replaced"}, {"file": "b", "status": "error"},
         {"file": "A", "status": "done"}, {"file": "c", "status": "kept"}]
    assert [e["file"] for e in history_files(h)] == ["A"]


def test_analyze_and_fix(tmp_path):
    clip = _loki_like(tmp_path / "loki2.mp4")
    good = make_video(tmp_path / "ok.mkv", vcodec="libx264", dur=8, extra=["-g", "48"])
    item = FakeItem(5, "/volume1/Perso/loki2.mp4")
    item2 = FakeItem(6, "/volume1/Films/ok.mkv", lib="Films")
    plex = FakePlex([item, item2])
    settings = dict(DEFAULT_SETTINGS, path_maps=[{"from": "/volume1/Perso", "to": str(tmp_path)}])
    history = [{"file": str(clip), "status": "done"},
               {"file": str(good), "status": "replaced", "plex_path": "/volume1/Films/ok.mkv"}]
    files = analyze_converted(plex, Encoder(), settings, history)
    by = {f.name: f for f in files}
    assert by["loki2.mp4"].needs_reencode and by["loki2.mp4"].personal and by["loki2.mp4"].in_plex
    assert not by["ok.mkv"].needs_reencode and not by["ok.mkv"].personal
    v = video_info_for(by["loki2.mp4"])
    assert v.file_path == "/volume1/Perso/loki2.mp4" and v.rating_key == "5" and v.container == "mp4"

    assert fix_poster(plex, Encoder(), str(clip), "/volume1/Perso/loki2.mp4", "Perso", "5") == "affiche mise à jour"
    assert item.uploaded == [False]            # une vraie image, pas du noir
    # bibliothèque avec affiches officielles : jamais modifiée
    assert fix_poster(plex, Encoder(), str(good), "/volume1/Films/ok.mkv", "Films", "6") == ""
    assert item2.uploaded == []
