from types import SimpleNamespace as NS

from app import plex_client as pc


def _ep(i, codec="mpeg4", bitrate=1500, w=720, h=576):
    part = NS(file=f"/volume1/Series/Show/S01/ep{i}.avi", size=700_000_000, duration=2_400_000,
              container="avi", id=1000 + i)
    media = NS(videoCodec=codec, bitrate=bitrate, width=w, height=h, parts=[part], duration=2_400_000,
               audioCodec="mp3", audioChannels=2, container="avi", videoProfile="", id=500 + i)
    return NS(ratingKey=i, title=f"Episode {i}", grandparentTitle="Show", parentTitle="Saison 1",
              parentIndex=1, index=i, year=2001, media=[media])


class FakeSection:
    type = "show"
    title = "Séries"

    def __init__(self, items):
        self.items = items
        self.calls = []

    def totalViewSize(self, libtype=None, includeCollections=True):
        assert libtype == "episode"
        return len(self.items)

    def search(self, libtype=None, container_start=0, container_size=0, maxresults=0):
        self.calls.append(container_start)
        return self.items[container_start:container_start + maxresults]


def test_scan_paginates_and_filters(monkeypatch):
    monkeypatch.setattr(pc, "PAGE_SIZE", 10)
    items = [_ep(i) for i in range(1, 24)] + [_ep(30, "h264", 4000, 1920, 1080), _ep(31, "h264", 25000, 1920, 1080)]
    sec = FakeSection(items)
    client = pc.PlexClient()
    client.server = NS(library=NS(sections=lambda: [sec]))
    progress = []
    res = client.scan_libraries(min_keep_codec="h264", heavy_mbps=12,
                                progress_cb=lambda d, t, m: progress.append((d, t)))
    assert sec.calls == [0, 10, 20]
    assert len(res) == 24   # 23 anciens codecs + 1 H.264 trop lourd
    heavy = [v for v in res if v.reason == "débit élevé"]
    assert len(heavy) == 1 and heavy[0].recommended_codec == "hevc"
    v = res[0]
    assert v.series_title == "Show" and v.season_number == 1 and v.episode_number == 1
    assert v.display_title == "Show – S01E01 – Episode 1"
    assert progress[-1][0] == progress[-1][1]


def test_scan_cancel():
    import threading
    import pytest
    ev = threading.Event()
    ev.set()
    client = pc.PlexClient()
    client.server = NS(library=NS(sections=lambda: [FakeSection([_ep(1)])]))
    with pytest.raises(pc.ScanCancelled):
        client.scan_libraries(stop_event=ev)
