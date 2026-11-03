"""Sélection multiple Maj / Ctrl : liste du Scanner et file d'encodage (Xvfb)."""
import os
from types import SimpleNamespace as NS

import pytest

from app.encoder import EncodeJob
from app.jobs import JobQueue
from app.plex_client import VideoInfo
from tests.test_gui_workflow import pump

try:
    import tkinter  # noqa: F401
    tk_ok = True
except Exception:
    tk_ok = False

gui = pytest.mark.skipif(not tk_ok or not os.environ.get("DISPLAY"), reason="pas d'affichage (xvfb-run)")
PLAIN, SHIFT, CTRL = NS(state=0), NS(state=0x0001), NS(state=0x0004)


def test_move_many_keeps_order():
    q = JobQueue()
    jobs = [EncodeJob(f"/m/{i}.avi", plex_path=f"/m/{i}.avi") for i in range(6)]
    for j in jobs:
        q.add(j)
    q.move_many([jobs[2], jobs[3]], -1)
    assert [j.plex_path[-5] for j in q.snapshot()] == list("023145")
    q.move_many([jobs[0], jobs[2]], -1)               # bloc collé en haut : ne bouge pas
    assert [j.plex_path[-5] for j in q.snapshot()] == list("023145")
    q.move_many([jobs[5], jobs[1]], 1)
    assert [j.plex_path[-5] for j in q.snapshot()] == list("023415")


class FakePlex:
    def __init__(self, videos):
        self.server = object()
        self.server_name = "Fake"
        self.token = "t"
        self.scan_errors = 0
        self.last_error = ""
        self._videos = videos

    def scan_libraries(self, progress_cb=None, **kw):
        return list(self._videos)

    def list_sections(self):
        return []

    def web_url_for(self, rk):
        return ""


def _videos():
    out = []
    for s in ("Alpha", "Beta"):
        for e in range(1, 6):
            out.append(VideoInfo(rating_key=f"{s}{e}", title=f"Ep{e}", library="Séries", library_type="show",
                                 series_title=s, season_number=1, episode_number=e,
                                 file_path=f"/m/{s}/E{e}.avi", container="avi", video_codec="mpeg4"))
    return out


@gui
def test_scan_and_queue_multiselect():
    from app.gui import App
    app = App()
    try:
        app.plex = FakePlex(_videos())
        app.settings["network_root_asked"] = True
        app._on_connected()
        app._start_scan()
        assert pump(app, lambda: len(app.results_list.row_boxes) == 10, 30)
        rl = app.results_list
        order = rl._order
        assert [k.split("|")[1] for k in order[:3]] == ["/m/Alpha/E1.avi", "/m/Alpha/E2.avi", "/m/Alpha/E3.avi"]
        v = {k: rl._by_key[k] for k in order}

        def sel():
            return [i for i, k in enumerate(order) if app.selected.get(k)]

        rl.click(v[order[1]], PLAIN)                       # clic simple
        assert sel() == [1] and len(app.queue) == 1
        rl.click(v[order[6]], SHIFT)                       # Maj : plage 1 → 6 (traverse les deux séries)
        assert sel() == [1, 2, 3, 4, 5, 6] and len(app.queue) == 6
        assert rl.row_boxes[order[4]].cget("text") == "✓"
        rl.click(v[order[3]], CTRL)                        # Ctrl : retire un seul fichier
        assert sel() == [1, 2, 4, 5, 6] and len(app.queue) == 5
        rl.click(v[order[8]], SHIFT)                       # plage 3 → 8 cochée
        assert sel() == [1, 2, 3, 4, 5, 6, 7, 8]
        rl.click(v[order[5]], SHIFT)                       # E5 coché → Maj + clic décoche la plage 3 → 5
        assert sel() == [1, 2, 6, 7, 8]
        # clic sur le titre de série : toute la série
        beta_header = next(i for i, (k, t, _) in enumerate(rl._items) if k == "h_series" and t == "Beta")
        rl.toggle_group(beta_header)
        assert sel() == [1, 2, 5, 6, 7, 8, 9]
        rl.toggle_group(beta_header)
        assert sel() == [1, 2]
        # clavier
        app.focus_force()
        app._key_select_all()
        assert sel() == list(range(10)) and len(app.queue) == 10
        app._key_escape()
        assert sel() == [] and len(app.queue) == 0

        # ---------------- file d'encodage ----------------
        app._key_select_all()
        app._show_page("queue")
        assert pump(app, lambda: len(app._queue_widgets) == 10, 10)
        jobs = app.queue.snapshot()
        app._queue_click(jobs[1], PLAIN)
        assert app._selected_jobs() == [jobs[1]]
        app._queue_click(jobs[4], SHIFT)
        assert app._selected_jobs() == jobs[1:5]
        app._queue_click(jobs[2], CTRL)
        assert app._selected_jobs() == [jobs[1], jobs[3], jobs[4]]
        app._queue_click(jobs[8], NS(state=0x0005))        # Ctrl + Maj : ajoute la plage 2 → 8
        assert app._selected_jobs() == jobs[1:9]
        app._queue_click(jobs[0], PLAIN)
        app._queue_click(jobs[1], CTRL)
        assert str(app._queue_widgets[id(jobs[0])]["row"].cget("border_color")) != \
            str(app._queue_widgets[id(jobs[2])]["row"].cget("border_color"))
        app._sel_move(1)                                   # deux lignes descendues d'un cran
        assert app.queue.snapshot()[:3] == [jobs[2], jobs[0], jobs[1]]
        assert pump(app, lambda: len(app._selected_jobs()) == 2, 5)   # sélection gardée après réaffichage
        app._key_delete()
        assert len(app.queue) == 8 and jobs[0] not in app.queue.snapshot()
        assert not app.selected.get(jobs[0].rating_key + "|" + jobs[0].plex_path)   # décoché dans le Scanner
        app._key_select_all()
        assert len(app._selected_jobs()) == 8
        app._key_escape()
        assert app._selected_jobs() == []
        # décisions groupées
        for j in app.queue.snapshot()[:3]:
            j.status, j.output_path = "done", ""
        app._render_queue()
        app._queue_click(app.queue.snapshot()[0], PLAIN)
        app._queue_click(app.queue.snapshot()[2], SHIFT)
        states = [str(b.cget("state")) for b in app._qsel_buttons]
        assert states == ["disabled", "disabled", "disabled", "normal", "normal", "normal"]
        app._sel_decide("discard")
        assert pump(app, lambda: [j.status for j in app.queue.snapshot()[:3]] == ["discarded"] * 3, 20)
    finally:
        app._close()
