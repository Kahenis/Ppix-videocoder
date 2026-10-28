import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe absent")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Config, journal et keyring isolés dans un HOME temporaire."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    import keyring
    from keyring.backend import KeyringBackend

    class MemKeyring(KeyringBackend):
        priority = 1
        store = {}

        def get_password(self, s, u):
            return self.store.get((s, u))

        def set_password(self, s, u, p):
            self.store[(s, u)] = p

        def delete_password(self, s, u):
            self.store.pop((s, u), None)

    keyring.set_keyring(MemKeyring())
    from app import config
    config._keyring_cache["value"] = None
    yield home


def make_video(path: Path, *, vcodec="mpeg4", size="320x240", dur=3, audio=(("mp3", 2, "fre"),),
               subs=False, interlaced=False, pix_fmt=None, extra=()):
    """Crée un petit fichier vidéo de test."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=d={dur}:s={size}:r=25"]
    for i, _ in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=f={400 + 100 * i}:d={dur}"]
    srt = None
    if subs:
        srt = path.with_suffix(".srt")
        srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nBonjour\n", encoding="utf-8")
        cmd += ["-i", str(srt)]
    cmd += ["-map", "0:v"]
    for i in range(len(audio)):
        cmd += ["-map", f"{i + 1}:a"]
    if subs:
        cmd += ["-map", f"{len(audio) + 1}:s", "-c:s", "srt" if path.suffix == ".mkv" else "mov_text"]
    cmd += ["-c:v", vcodec]
    if pix_fmt:
        cmd += ["-pix_fmt", pix_fmt]
    if interlaced:
        cmd += ["-vf", "setfield=tff", "-flags", "+ilme+ildct"]
    for i, (ac, ch, lang) in enumerate(audio):
        cmd += [f"-c:a:{i}", ac, f"-ac:a:{i}", str(ch), f"-metadata:s:a:{i}", f"language={lang}"]
    cmd += list(extra) + [str(path)]
    cmd[2] = "warning"
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("Génération du fichier de test impossible : " + proc.stderr.strip().replace("\n", " | "))
    if srt:
        srt.unlink()
    return path


def probe(path):
    import json
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)
