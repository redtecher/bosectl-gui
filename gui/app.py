"""Application wiring — tray icon, main window, device service."""

import sys

from PySide6.QtCore import QObject, QThread
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import __version__
from .resources import appIcon
from .service import DeviceService
from .main_window import MainWindow

# Quick CNC presets offered from the tray (0 = strongest ANC).
TRAY_CNC_STEPS = (0, 2, 4, 6, 8, 10)


class TrayController(QObject):
    # Must be a QObject: its slots are connected to DeviceService signals
    # emitted from the worker thread. Only a QObject receiver gets queued
    # (cross-thread) delivery — plain-Python receivers execute directly on
    # the worker thread, where touching QMenu/QAction deadlocks.
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.service = DeviceService()
        self.window = MainWindow(self.service)

        self.thread = QThread()
        self.service.moveToThread(self.thread)
        self.thread.started.connect(self.service.start)

        # service → UI
        self.service.stateChanged.connect(self._on_state)
        self.service.snapshotReady.connect(self.window.onSnapshot)
        self.service.stateChanged.connect(self.window.onState)
        self.service.failed.connect(self.window.onError)
        self.service.failed.connect(self._on_error)
        self.service.commandFinished.connect(self.window.onCommandFinished)
        self.service.snapshotReady.connect(self._on_snapshot)

        # window → hide to tray
        self.window.closeRequested.connect(self.window.hide)

        # ── tray ──────────────────────────────────────────────────────────
        self.icon = QSystemTrayIcon(appIcon())
        self.icon.setToolTip("bosectl — 正在连接…")
        menu = QMenu()

        self.status_action = QAction("未连接")
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addSeparator()

        self.cnc_menu = menu.addMenu("降噪强度")
        self._cnc_actions = {}
        for step in TRAY_CNC_STEPS:
            act = QAction("降噪 %d%s" % (step, "（最强）" if step == 0 else ""))
            act.triggered.connect(lambda _=False, lv=step: self.service.applyCnc(lv))
            self.cnc_menu.addAction(act)
            self._cnc_actions[step] = act

        self.mode_menu = menu.addMenu("模式")
        menu.addSeparator()

        show_action = QAction("打开主窗口")
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)
        menu.addSeparator()

        quit_action = QAction("退出")
        quit_action.triggered.connect(app.quit)
        menu.addAction(quit_action)

        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_tray_activated)
        self.icon.show()

        self.window.show()
        self.thread.start()

    # ── callbacks (UI thread) ─────────────────────────────────────────────

    def _show_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def _on_state(self, state):
        if state == "connected":
            self.icon.setToolTip("bosectl — 已连接")
            self.icon.showMessage("bosectl", "耳机已连接", QSystemTrayIcon.MessageIcon.Information, 3000)
        elif state == "connecting":
            self.icon.setToolTip("bosectl — 正在连接…")
        else:
            self.status_action.setText("未连接")
            self.icon.setToolTip("bosectl — 未连接")

    def _on_error(self, message):
        self.icon.showMessage("bosectl", message, QSystemTrayIcon.MessageIcon.Warning, 4000)

    def _on_snapshot(self, snap):
        name = snap["name"] or snap["model"] or "Bose"
        battery = snap["battery"]
        self.status_action.setText("%s · %d%%" % (name, battery) if battery is not None else name)
        self.icon.setToolTip("bosectl — %s · %d%%" % (name, battery) if battery is not None else "bosectl — %s" % name)

        # Mark the current tray CNC step.
        current = snap["cnc"]
        for step, act in self._cnc_actions.items():
            act.setText("降噪 %d%s%s" % (
                step,
                "（最强）" if step == 0 else "",
                "  ✓" if step == current else ""))

        # Rebuild the mode submenu from the snapshot.
        self.mode_menu.clear()
        current_idx = snap["mode_idx"]
        for name_, meta in snap["preset_modes"].items():
            act = QAction(name_, self.mode_menu)
            act.setChecked(current_idx == meta["idx"])
            act.triggered.connect(lambda _=False, n=name_: self.service.applyMode(n))
            self.mode_menu.addAction(act)

    # ── shutdown ──────────────────────────────────────────────────────────

    def shutdown(self):
        # Hide the tray icon FIRST: otherwise Windows keeps a ghost entry in
        # the notification area until the mouse sweeps over it, which looks
        # like "quit didn't work".
        self.icon.hide()
        self.window.hide()
        # End the worker's event loop directly. Do NOT queue service.stop()
        # and wait for it: BmapConnection.close() can block for seconds on a
        # live RFCOMM link, and a worker mid-Bluetooth-call can't service
        # queued calls at all. The OS reclaims the socket as the process
        # exits; terminate() only fires if the worker is wedged inside a
        # multi-second connect/select.
        self.thread.quit()
        if not self.thread.wait(1500):
            self.thread.terminate()
            self.thread.wait(500)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("bosectl-gui")
    app.setOrganizationName("bosectl")
    app.setApplicationVersion(__version__)

    ctrl = TrayController(app)
    app.aboutToQuit.connect(ctrl.shutdown)
    sys.exit(app.exec())
