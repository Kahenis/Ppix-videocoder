#!/usr/bin/env python3
"""
Ppix-Videocoder – Application Windows
Optimise les codecs vidéo des bibliothèques Plex (H.265 / H.264).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.gui import App


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
