"""Capture the main window into docs/screenshot-main.png (for the README).

Runs the real UI against the paired device, waits for the first snapshot,
grabs the window and exits. Usage (repo root):

    python -X utf8 -m gui.screenshot
"""

import os
import sys

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from gui.app import TrayController


class _Shot(QObject):
    def __init__(self, ctrl, out_path, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.out_path = out_path

    def on_snapshot(self, _snap):
        # Let the window finish laying out, then grab.
        QTimer.singleShot(300, self._grab)

    def _grab(self):
        os.makedirs(os.path.dirname(self.out_path), exist_ok=True)
        px = self.ctrl.window.grab()
        ok = px.save(self.out_path)
        print("%s %s (%dx%d)" % (
            "saved:" if ok else "FAILED:", self.out_path, px.width(), px.height()),
            flush=True)
        QApplication.quit()


def main():
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "screenshot-main.png")
    if len(sys.argv) > 1:
        out = os.path.abspath(sys.argv[1])

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ctrl = TrayController(app)

    shot = _Shot(ctrl, out)
    ctrl.service.snapshotReady.connect(shot.on_snapshot)
    QTimer.singleShot(60_000, QApplication.quit)   # bail out: no device
    app.aboutToQuit.connect(ctrl.shutdown)
    app.exec()
    sys.exit(0)


if __name__ == "__main__":
    main()
