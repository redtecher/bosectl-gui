"""Headless smoke test: tray controller + service against a real device.

Sequence (auto-quits):
    connect → snapshot #1 → set CNC 3 → snapshot #2 (expect 3)
            → set CNC 0 → snapshot #3 (expect 0) → exit 0
Any failure or 60s timeout exits non-zero. Prints every signal received.

The probe is a QObject living on the main thread so its slots are queued
from the worker thread (same rule TrayController relies on).
"""

import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from gui.app import TrayController


class Probe(QObject):
    printed = Signal(str)

    def __init__(self, ctrl, result, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.result = result

    def log(self, line):
        print(line, flush=True)

    def fail(self, reason):
        self.result["reason"] = reason
        print("FAIL:", reason, flush=True)
        QApplication.quit()

    def on_state(self, state):
        print("STATE:", state, flush=True)

    def on_failed(self, msg):
        print("FAILED-SIGNAL:", msg, flush=True)

    def on_finished(self, name):
        print("COMMAND-FINISHED:", name, flush=True)

    def on_snapshot(self, snap):
        r = self.result
        p = r["phase"]
        print("SNAPSHOT #%d: name=%r model=%r fw=%r battery=%s cnc=%s/%s "
              "mode=%r anr=%r eq=%s features=%s" % (
                  p, snap["name"], snap["model"], snap["firmware"],
                  snap["battery"], snap["cnc"], snap["cnc_max"],
                  snap["mode"], snap["anr"], [b for b, *_ in snap["eq"]] or "-",
                  {k: v for k, v in snap["features"].items() if v} or "-"),
              flush=True)
        if p == 0:
            if snap["battery"] is None:
                self.fail("snapshot has no battery reading")
                return
            r["phase"] = 1
            self.ctrl.service.applyCnc(3)
        elif p == 1:
            if snap["cnc"] != 3:
                self.fail("CNC write did not stick: got %r, want 3" % snap["cnc"])
                return
            r["phase"] = 2
            self.ctrl.service.applyCnc(0)          # restore max ANC
        elif p == 2:
            if snap["cnc"] != 0:
                self.fail("CNC restore did not stick: got %r, want 0" % snap["cnc"])
                return
            r["ok"] = True
            print("quitting…", flush=True)
            QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ctrl = TrayController(app)

    result = {"phase": 0, "ok": False, "reason": "timeout"}
    probe = Probe(ctrl, result)          # main-thread QObject → queued slots

    ctrl.service.stateChanged.connect(probe.on_state)
    ctrl.service.snapshotReady.connect(probe.on_snapshot)
    ctrl.service.failed.connect(probe.on_failed)
    ctrl.service.commandFinished.connect(probe.on_finished)

    QTimer.singleShot(60_000, lambda: probe.fail("60s timeout (phase %d)" % result["phase"]))
    app.aboutToQuit.connect(lambda: print("ABOUT-TO-QUIT", flush=True))
    ctrl.thread.finished.connect(lambda: print("THREAD-FINISHED", flush=True))
    app.aboutToQuit.connect(ctrl.shutdown)
    print("EXEC-ENTER", flush=True)
    app.exec()
    print("EXEC-RETURNED", flush=True)

    print("RESULT:", "PASS" if result["ok"] else "FAIL — " + result["reason"], flush=True)
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
