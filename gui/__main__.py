"""python -m gui / bosectl-gui.exe entry point.

`--smoke` runs the headless device smoke test (exit code 0 = pass); no flag
starts the tray app.
"""
import sys

if "--smoke" in sys.argv[1:]:
    from .smoke_test import main as smoke_main
    sys.exit(smoke_main())

from .app import main

if __name__ == "__main__":
    sys.exit(main())
