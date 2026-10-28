"""Parcours complet de l'interface (Xvfb) avec un faux serveur Plex et de vrais fichiers."""
import os
import time

import pytest

from tests.conftest import make_video, needs_ffmpeg

tk_ok = True
try:
    import tkinter  # noqa: F401
except Exception:
    tk_ok = False

pytestmark = [needs_ffmpeg, pytest.mark.skipif(not tk_ok or not os.environ.get("DISPLAY"),
                                               reason="pas d'affichage (lancer avec xvfb-run)")]


class FakeSection:
    def __init__(self, title, locations):
        self.title, self.type, self.locations = title, "movie", locations


class FakePlex:
    def __init__(self, files, root):
        from app.plex_client import VideoInfo
        self.server = object()
        self.server_name = "FakeNAS"
        self.token = "tok"
        self.scan_errors = 0
        self.last_error = ""
        self.refreshed = []
        self._root = root
        self._videos = [
            VideoInfo(rating_key=str(i), title=f.stem, library="Films", library_type="movie",
                      file_path="/volume1/Media/Films/" + f.name, file_size=f.stat().st_size,
                      container=f.suffix.lstrip("."), video_codec="mpeg4", duration_ms=3000,
                      width=320, height=240, reason="codec ancien")
            for i, f in enumerate(files)
        ]

    def scan_libraries(self, progress_cb=None, stop_event=None, **kw):
        if progress_cb:
            progress_cb(1, 1, "ok")
        return list(self._videos)

    def list_sections(self):
        return [FakeSection("Films", ["/volume1/Media/Films"])]

    def refresh_after_replace(self, rk, lib, folder, ext_changed):
        self.refreshed.append((rk, lib, folder, ext_changed))
        return "scan du dossier" if ext_changed else "analyse"

    def web_url_for(self, rk):
        return ""

    def detect_path_mapping(self):
        return {"network_root": "", "plex_prefix": "", "message": "test", "candidates": []}


def pump(app, cond, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_full_workflow(tmp_path):
    from app.gui import App

    media = tmp_path / "nas" / "Media" / "Films"
    media.mkdir(parents=True)
    files = [make_video(media / f"Film{i}.avi") for i in range(4)]

    app = App()
    try:
        app.plex = FakePlex(files, media)
        app.encoder.detect_hardware = lambda: "none"
        app.settings.update(network_root=str(tmp_path / "nas" / "Media").replace("\\", "/"),
                            plex_prefix="", hardware_accel="auto", auto_replace=False)
        # sur Linux le mapping produit des « \\ » : on utilise une table de correspondance
        app.settings["path_maps"] = [{"from": "/volume1/Media", "to": str(tmp_path / "nas" / "Media")}]
        app.settings["network_root"] = ""
        app.settings["network_root_asked"] = True
        app._on_connected()
        assert app._page == "scan"
        app._start_scan()
        assert pump(app, lambda: len(app.videos) == 4)
        assert pump(app, lambda: bool(app.results_list.row_vars))
        app._select_all()
        assert len(app.queue) == 4
        # changement de codec après coche → répercuté sur le job
        app._on_codec_change(app.videos[3], "h264")
        assert app.queue.snapshot()[3].codec == "h264"
        # décocher retire de la file
        app._on_result_select(app.videos[2], False)
        assert len(app.queue) == 3

        app._show_page("queue")
        app._start_queue()
        assert pump(app, lambda: not app.queue_running and app.queue.count("done") == 3, 240)
        jobs = app.queue.snapshot()
        names = [os.path.basename(j.final_path) for j in jobs]
        assert names == ["Film0.mkv", "Film1.mkv", "Film3.mkv"]

        app._decide(jobs[0], "replace")
        app._decide(jobs[1], "keep")
        app._decide(jobs[2], "discard")
        assert pump(app, lambda: [j.status for j in jobs] == ["replaced", "kept", "discarded"])
        assert sorted(p.name for p in media.iterdir()) == [
            "Film0.mkv", "Film1.avi", "Film1.optimized.mkv", "Film2.avi", "Film3.avi"]
        assert app.plex.refreshed == [("0", "Films", "/volume1/Media/Films", True)]
        assert [v.title for v in app.videos] == ["Film2", "Film3"]   # retirés du scan
        assert {h["status"] for h in app.history} == {"replaced", "kept", "discarded"}

        # Remplacement automatique + mode essai
        app.settings["auto_replace"] = True
        app._show_page("scan")
        assert pump(app, lambda: bool(app.results_list.row_vars))
        app._on_result_select(app.videos[0], True)       # Film2
        app._show_page("queue")
        app._start_queue()
        assert pump(app, lambda: not app.queue_running and app.queue.count("replaced") == 2, 120)
        assert (media / "Film2.mkv").exists() and not (media / "Film2.avi").exists()

        app.settings["dry_run"] = True
        app.queue.add(__import__("app.jobs", fromlist=["x"]).make_job_for_video(app.videos[-1], app.settings))
        app._start_queue()
        assert pump(app, lambda: not app.queue_running and app.queue.count("simulated") == 1)
        assert (media / "Film3.avi").exists()

        # Arrêt en cours d'encodage : job annulé, fichier partiel supprimé, relance possible
        app.settings.update(dry_run=False, preset="veryslow")
        long = make_video(media / "Long.avi", dur=60, size="1280x720")
        from app.plex_client import VideoInfo
        from app.jobs import make_job_for_video
        v = VideoInfo(rating_key="99", title="Long", library="Films", library_type="movie",
                      file_path="/volume1/Media/Films/Long.avi", container="avi", video_codec="mpeg4")
        job = make_job_for_video(v, app.settings)
        app.queue.add(job)
        app._start_queue()
        assert pump(app, lambda: job.status == "running" and job.progress > 0, 60)
        app._stop_queue()
        assert pump(app, lambda: job.status == "cancelled" and not app._worker.is_alive(), 60)
        assert not list(media.glob(".ppix-tmp-*")) and long.exists()
        assert app.queue.retry_failed() >= 1 and job.status == "pending"

        # Dialogues : paramètres, historique, racine réseau, quitter
        app._show_settings()
        app.update()
        app._settings_win.destroy()
        app._show_history()
        app.update()
        from app.dialogs import ask_network_root_before_scan
        ask_network_root_before_scan(app)
        app.update()
        assert not list(media.glob(".ppix-tmp-*"))
    finally:
        app._close()
