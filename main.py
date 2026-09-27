#!/usr/bin/env python3
"""Ppix-Videocoder – Application Windows."""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    try:
        from app.gui import App
        app = App()
        app.mainloop()
    except Exception:
        err = traceback.format_exc()
        print(err)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Ppix-Videocoder – Erreur", err[:1500])
            root.destroy()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
