"""Top-level entry for the PyInstaller build (and direct `python gui/run.py`).

Lives at gui/run.py — NOT inside the gui package as __main__.py — because
PyInstaller runs the entry script as a top-level module where package-relative
imports don't resolve.
"""
import sys

if "--smoke" in sys.argv[1:]:
    from gui.smoke_test import main as smoke_main
    sys.exit(smoke_main())

from gui.app import main

if __name__ == "__main__":
    sys.exit(main())
