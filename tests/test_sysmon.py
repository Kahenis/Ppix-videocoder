import sys

import pytest

from app import sysmon as m


def test_describe_worker():
    gpus = ["Intel(R) UHD Graphics 770", "NVIDIA GeForce RTX 3060"]
    assert m.describe_worker("nvenc", "nvenc", "hevc", False, "i7", gpus) == \
        "Matériel : NVIDIA GeForce RTX 3060 (NVENC)"
    assert m.describe_worker("qsv", "qsv", "hevc", False, "i7", gpus) == \
        "Matériel : Intel(R) UHD Graphics 770 (Quick Sync)"
    assert m.describe_worker("amf", "amf", "hevc", False, "i7", []) == "Matériel : carte AMD (AMF)"
    assert m.describe_worker("none", "none", "h264", False, "Intel Core i7", gpus) == \
        "Logiciel : processeur Intel Core i7 (x264)"
    assert "repli après échec NVENC" in m.describe_worker("none", "nvenc", "hevc", False, "i7", gpus)
    assert m.describe_worker("none", "none", "hevc", True, "i7", gpus).startswith("Logiciel : audio seulement")


def test_format_load():
    assert m.format_load(m.LoadSample(94.2, 88.6, 2.0, 0.0)) == \
        "CPU 94 % (encodage 89 %) · GPU 2 % (encodeur vidéo 0 %)"
    assert m.format_load(m.LoadSample(50, None, None, None)) == "CPU 50 % (encodage …) · GPU n/d"
    assert m.format_load(None) == "CPU … · GPU …"


def test_monitor_measures_process_cpu():
    import subprocess
    import time
    mon = m.LoadMonitor(interval=0.3)
    mon.start()
    p = subprocess.Popen([sys.executable, "-c", "while True: pass"])
    try:
        mon.set_pid(p.pid)
        end = time.time() + 10
        while time.time() < end:
            s = mon.latest
            if s is not None and s.cpu_proc is not None and s.cpu_proc > 5:
                break
            time.sleep(0.2)
        assert s is not None and s.cpu_total is not None
        assert s.cpu_proc is not None and s.cpu_proc > 5
    finally:
        p.kill()
        mon.stop()


def test_cpu_name_not_empty():
    assert m.cpu_name()


@pytest.mark.skipif(sys.platform != "win32", reason="compteurs GPU Windows")
def test_windows_gpu_counters_and_names():
    m.gpu_names()          # ne doit pas lever d'exception
    try:
        g = m._PdhGpu()
    except OSError:
        pytest.skip("pas de compteurs « GPU Engine » sur cette machine")
    import time
    time.sleep(0.5)
    total, enc = g.read()
    g.close()
    assert total is None or 0 <= total <= 100
    assert enc is None or 0 <= enc <= 100
