"""Parcours « Parcourir local… » dans l'interface (Xvfb), sans Plex."""
import os

import pytest

from tests.conftest import make_video, needs_ffmpeg, probe
from tests.test_gui_workflow import pump

try:
    import tkinter  # noqa: F401
    tk_ok = True
except Exception:
    tk_ok = False

pytestmark = [needs_ffmpeg, pytest.mark.skipif(not tk_ok or not os.environ.get("DISPLAY"),
                                               reason="pas d'affichage (lancer avec xvfb-run)")]


def test_local_wizard(tmp_path, monkeypatch):
    from app import local_convert
    from app.gui import App

    src = make_video(tmp_path / "Mariage.avi", audio=(("ac3", 6, "fre"),), dur=4)
    out_dir = tmp_path / "convertis"
    out_dir.mkdir()
    app = App()
    try:
        app.encoder.detect_hardware = lambda: "none"
        app.settings.update(hardware_accel="none", auto_replace=False)
        # pas de connexion Plex : la page Scanner reste accessible
        app._show_page("scan")
        assert str(app.btn_nav_scan.cget("state")) == "normal"

        monkeypatch.setattr(local_convert.filedialog, "askopenfilename", lambda **k: str(src))
        local_convert.ask_local_file(app)
        dlg = app._local_dialog
        assert pump(app, lambda: str(dlg.btn_next.cget("state")) == "normal", 30)
        assert "mpeg4" in dlg.lbl_info.cget("text") and "5.1" in dlg.lbl_info.cget("text")

        # Étape 1 : H.264
        dlg.video_buttons["h264"].invoke()
        dlg.next()
        # Étape 2 : AC3 + stéréo ; « conserver » bloque le choix des canaux
        dlg.audio_buttons["copy"].invoke()
        assert str(dlg.layout_buttons["stereo"].cget("state")) == "disabled"
        dlg.audio_buttons["ac3"].invoke()
        dlg.layout_buttons["stereo"].invoke()
        assert "224" in dlg.rate_label.cget("text")
        dlg.next()
        # Étape 3 : destination par défaut = même nom en .mkv, suppression de l'original proposée
        assert dlg.dest_entry.get() == str(tmp_path / "Mariage.mkv")
        assert dlg.del_var.get() is True
        # autre dossier choisi avec « Parcourir… » → l'original est gardé
        dest = out_dir / "Mariage.mp4"
        monkeypatch.setattr(local_convert.filedialog, "asksaveasfilename", lambda **k: str(dest))
        dlg._browse_dest()
        assert dlg.del_var.get() is False
        dlg.next()                                   # « Lancer la conversion »
        assert app._page == "queue" and app.queue_running
        job = app.queue.snapshot()[0]
        assert job.local
        assert pump(app, lambda: job.status in ("saved", "error") and not app.queue_running, 120), job.message
        assert job.status == "saved", job.message
        assert src.exists() and dest.exists()
        st = probe(dest)["streams"]
        assert [s["codec_name"] for s in st] == ["h264", "ac3"]
        assert [s for s in st if s["codec_type"] == "audio"][0]["channels"] == 2
        assert app.history[-1]["local"] is True

        # Destination par défaut (même fichier) : remplacement sécurisé
        src2 = make_video(tmp_path / "Anniv.mkv", dur=3)
        monkeypatch.setattr(local_convert.filedialog, "askopenfilename", lambda **k: str(src2))
        local_convert.ask_local_file(app)
        d2 = app._local_dialog
        assert pump(app, lambda: str(d2.btn_next.cget("state")) == "normal", 30)
        d2.next()
        d2.next()
        assert "remplacé" in d2.dest_note.cget("text")
        d2.next()
        j2 = app.queue.snapshot()[-1]
        assert pump(app, lambda: j2.status in ("replaced", "error") and not app.queue_running, 120), j2.message
        assert j2.status == "replaced"
        assert [s["codec_name"] for s in probe(src2)["streams"] if s["codec_type"] == "video"] == ["hevc"]
        assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["Anniv.mkv", "Mariage.avi"]
    finally:
        app._close()


def test_bigger_local_result_discarded_and_single_claim(tmp_path):
    from app.gui import App
    from app.jobs import make_local_job
    from app.encoder import EncodeJob
    import gc

    src = tmp_path / "Petit.mp4"
    src.write_bytes(b"x" * 1000)
    work = tmp_path / ".ppix-travail"
    work.mkdir()
    out = work / "Petit.mp4.mp4"
    out.write_bytes(b"y" * 5000)
    app = App()
    try:
        assert not gc.isenabled()                     # ramasse-miettes réservé au thread de l'interface
        assert app.settings["skip_if_bigger"] is True
        job = make_local_job(str(src), "hevc", "copy", "keep", str(src))
        job.status, job.output_path, job.original_size, job.output_size = "done", str(out), 1000, 5000
        app._after_encode(job)
        assert job.status == "discarded" and src.read_bytes() == b"x" * 1000 and not out.exists()

        # deux décisions simultanées : une seule est exécutée
        j2 = EncodeJob(str(src), output_path=str(out), final_path=str(src), status="done")
        assert app._claim(j2) is True
        assert app._claim(j2) is False
        app._decide(j2, "discard")                    # déjà pris : ignoré
        assert j2.status == "done"
    finally:
        app._close()
    assert gc.isenabled()
