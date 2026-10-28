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
        self.posters = []
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

    # --- affiches (vidéos perso) ---
    def is_personal_library(self, lib):
        return True

    def find_item_by_file(self, path, library="", rk=""):
        from types import SimpleNamespace as NS
        return NS(ratingKey=rk or "0", path=path, librarySectionTitle=library)

    def upload_poster(self, item, jpg):
        from app.posters import is_black_image
        self.posters.append((item.path, is_black_image(jpg)))
        return True

    def build_file_index(self, stop_event=None):
        from types import SimpleNamespace as NS
        idx = {}
        for v in self._videos:
            idx[v.file_path.lower()] = NS(ratingKey=v.rating_key, type="movie", librarySectionTitle="Films",
                                          media=[NS(parts=[NS(file=v.file_path)])])
        return idx

    def lookup(self, index, path):
        return index.get(path.replace("\\", "/").lower())

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
        assert pump(app, lambda: len(app.plex.posters) >= 1, 60)
        assert app.plex.posters[0] == ("/volume1/Media/Films/Film0.mkv", False)   # vraie image, pas du noir
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
        assert not (media / ".ppix-travail").exists() and long.exists()
        assert app.queue.retry_failed() >= 1 and job.status == "pending"

        # Ligne « qui travaille » + charge CPU/GPU pendant l'encodage
        from app.sysmon import LoadSample
        app.queue.clear_finished()
        app.queue.remove(job)
        job = make_job_for_video(v, app.settings)
        app.queue.add(job)
        app._show_page("queue")
        app._start_queue()
        assert pump(app, lambda: job.status == "running" and job.progress > 0, 60)
        # mesure réelle : le % CPU de l'encodage apparaît
        assert pump(app, lambda: "(encodage " in app._queue_widgets[id(job)]["load"].cget("text")
                    and "CPU …" not in app._queue_widgets[id(job)]["load"].cget("text"), 30)
        assert app._queue_widgets[id(job)]["load"].cget("text").startswith("Logiciel : processeur")
        # valeurs GPU simulées (pas de GPU ici) : vérifie le format affiché
        app.monitor.stop()
        app.monitor._thread.join(5)
        app.monitor.latest = LoadSample(90, 85, 3, 0)
        assert pump(app, lambda: "CPU 90 % (encodage 85 %) · GPU 3 % (encodeur vidéo 0 %)" in
                    app._queue_widgets[id(job)]["load"].cget("text"), 20)
        app._stop_queue()
        assert pump(app, lambda: not app._worker.is_alive(), 60)

        # Mode « Audio seule » : case sur la page Scanner + conversion TrueHD → AC3
        from tests.conftest import make_truehd, probe
        app.settings.update(preset="medium", auto_replace=False)
        app._show_page("scan")
        app.audio_only_var.set(True)
        app._on_audio_only_toggle()
        assert app.settings["audio_only_truehd"] and app.videos == []
        thd = make_truehd(media / "Atmos.mkv")
        va = VideoInfo(rating_key="77", title="Atmos", library="Films", library_type="movie",
                       file_path="/volume1/Media/Films/Atmos.mkv", container="mkv", video_codec="h264",
                       audio_codec="truehd", reason="audio TrueHD", audio_only=True)
        app.plex._videos = [va]
        app._start_scan()
        assert pump(app, lambda: bool(app.results_list.row_vars))
        app._select_all()
        aj = app.queue.find_by_plex_path(va.file_path)
        assert aj is not None and aj.audio_only
        app._show_page("queue")
        app._start_queue()
        assert pump(app, lambda: aj.status == "done", 120), aj.message
        app._decide(aj, "replace")
        assert pump(app, lambda: aj.status == "replaced")
        codecs = [s["codec_name"] for s in probe(thd)["streams"] if s["codec_type"] == "audio"]
        assert codecs == ["ac3", "aac"]

        # Fichiers déjà convertis : clip avec une seule image-clé (ancienne version)
        import subprocess
        old = media / "loki2.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=8:s=320x240:r=24",
                        "-c:v", "libx264", "-g", "1000", "-sc_threshold", "0", str(old)], check=True)
        app.plex._videos = [VideoInfo(rating_key="55", title="loki2", library="Films", library_type="movie",
                                      file_path="/volume1/Media/Films/loki2.mp4")]
        app.history.append({"file": str(old), "status": "done"})
        app.queue.clear_finished()
        app._show_repair()
        assert pump(app, lambda: not app._repair_state["busy"] and app._repair_state["files"], 60)
        lf = next(f for f in app._repair_state["files"] if f.name == "loki2.mp4")
        assert lf.needs_reencode and lf.in_plex and lf.plex_path == "/volume1/Media/Films/loki2.mp4"
        assert app._repair_state["vars"][lf.local_path].get()       # coché d'office
        for f in app._repair_state["files"]:
            if f is not lf:
                app._repair_state["vars"][f.local_path].set(False)
        n_before = len(app.plex.posters)
        app._repair_actions["posters"]()
        assert pump(app, lambda: len(app.plex.posters) > n_before and not app._repair_state["busy"], 60)
        assert app.plex.posters[-1] == ("/volume1/Media/Films/loki2.mp4", False)
        app._repair_actions["reencode"]()
        rj = app.queue.find_by_plex_path("/volume1/Media/Films/loki2.mp4")
        assert rj is not None and rj.status == "pending"
        app.settings.update(auto_replace=True, dry_run=False, preset="ultrafast")
        app._start_queue()
        assert pump(app, lambda: rj.status == "replaced", 120), rj.message
        from app.encoder import Encoder
        assert Encoder().keyframe_max_gap(str(old), 8.0) <= 2.1
        app._repair_win.destroy()

        # Dialogues : paramètres, historique, racine réseau, quitter
        app._show_settings()
        app.update()
        app._settings_win.destroy()
        app._show_history()
        app.update()
        from app.dialogs import ask_network_root_before_scan
        ask_network_root_before_scan(app)
        app.update()
        assert not (media / ".ppix-travail").exists()
    finally:
        app._close()
