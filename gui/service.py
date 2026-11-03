"""DeviceService — pybmap wrapped in a Qt worker thread.

Every Bluetooth operation blocks for hundreds of milliseconds to seconds,
so nothing in the library may run on the UI thread. This QObject lives in
its own QThread (movetoThread pattern): the UI calls its slots through
queued connections and receives plain-dict snapshots back.

State machine:  disconnected → connecting → connected → (error) → disconnected
A poll timer refreshes the snapshot while connected; a retry timer
reconnects after a drop.
"""

import os
import sys
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot, QThread

# pybmap lives at the repo root in the source tree; PyInstaller bundles it.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isdir(os.path.join(_REPO_ROOT, "pybmap")) and _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pybmap
from pybmap.errors import BmapError, BmapConnectionError, BmapNotFoundError

POLL_INTERVAL_MS = 60_000     # status refresh while connected
RETRY_INTERVAL_MS = 15_000    # reconnect attempt after a drop


class DeviceService(QObject):
    # stateChanged: "disconnected" | "connecting" | "connected"
    stateChanged = Signal(str)
    # Full status snapshot (dict, see _snapshot()). Emitted on connect,
    # every poll, and after each applied command.
    snapshotReady = Signal(dict)
    # Human-readable problem for the tray bubble / status line.
    failed = Signal(str)
    # A command finished; command name for UI re-enabling.
    commandFinished = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dev = None            # BmapConnection
        self._state = "disconnected"
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_INTERVAL_MS)
        self._poll.timeout.connect(self.refresh)
        self._retry = QTimer(self)
        self._retry.setInterval(RETRY_INTERVAL_MS)
        self._retry.timeout.connect(self._connect)
        self._last_snapshot = None

    # ── lifecycle (run in worker thread) ─────────────────────────────────

    @Slot()
    def start(self):
        """Kick off the first connection attempt (thread.started slot)."""
        self._connect()

    @Slot()
    def stop(self):
        self._poll.stop()
        self._retry.stop()
        self._close()

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self.stateChanged.emit(state)

    def _close(self):
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None

    # ── connection handling ──────────────────────────────────────────────

    def _connect(self):
        if self._dev is not None:
            return
        self._set_state("connecting")
        try:
            dev = pybmap.connect()
        except BmapNotFoundError:
            # Nothing paired/powered — keep looking quietly.
            self._set_state("disconnected")
            if not self._retry.isActive():
                self._retry.start()
            return
        except BmapError as e:
            self.failed.emit("连接失败: %s" % e)
            self._set_state("disconnected")
            if not self._retry.isActive():
                self._retry.start()
            return
        self._dev = dev
        self._retry.stop()
        self._set_state("connected")
        self.refresh()
        self._poll.start()

    # ── snapshot ─────────────────────────────────────────────────────────

    def _snapshot(self):
        dev = self._dev
        st = dev.status()
        model = ""
        info = getattr(dev._device, "DEVICE_INFO", {})
        model = info.get("name", "")
        presets = dict(getattr(dev._device, "PRESET_MODES", {}))
        try:
            modes = {i: {"name": m.name, "cnc": m.cnc_level, "editable": m.editable}
                     for i, m in dev.modes().items()}
        except BmapError:
            modes = {}
        anr = None
        if dev.has_feature("anr"):
            try:
                anr = dev.anr()
            except BmapError:
                anr = None
        return {
            "name": st.name,
            "model": model,
            "firmware": st.firmware,
            "battery": st.battery,
            "battery_readings": list(st.battery_readings or ()),
            "battery_components": dict(dev.battery_components) if dev.battery_components else {},
            "mode": st.mode,
            "mode_idx": st.mode_idx,
            "modes": modes,
            "preset_modes": presets,
            "cnc": st.cnc_level,
            "cnc_max": st.cnc_max,
            "eq": [(b.name, b.current, b.min_val, b.max_val) for b in (st.eq or ())],
            "anr": anr,
            "features": {
                "cnc": dev.has_feature("cnc"),
                "anr": dev.has_feature("anr"),
                "eq": dev.has_feature("eq"),
                "mode_config": dev.has_feature("mode_config"),
                "current_mode": dev.has_feature("current_mode"),
            },
            "ts": time.time(),
        }

    def _guard(self, fn):
        """Run fn(); on link errors, tear down and schedule a retry."""
        if self._dev is None:
            self._connect()
            if self._dev is None:
                return None
        try:
            return fn()
        except (BmapConnectionError, BmapNotFoundError) as e:
            self.failed.emit("连接中断: %s" % e)
            self._close()
            self._poll.stop()
            self._set_state("disconnected")
            self._retry.start()
        except BmapError as e:
            self.failed.emit("设备错误: %s" % e)
        except OSError as e:
            self.failed.emit("通信错误: %s" % e)
        return None

    def _publish(self, name=""):
        snap = self._guard(self._snapshot)
        if snap is not None:
            self._last_snapshot = snap
            self.snapshotReady.emit(snap)
        if name:
            self.commandFinished.emit(name)

    # ── slots invoked by the UI (queued into this thread) ────────────────

    @Slot()
    def refresh(self):
        self._publish()

    @Slot(int)
    def applyCnc(self, level):
        def run():
            self._dev.set_cnc(level)
        self._guard(run)
        self._publish("cnc")

    @Slot(str)
    def applyAnr(self, level):
        def run():
            self._dev.set_anr(level)
        self._guard(run)
        self._publish("anr")

    @Slot(str)
    def applyMode(self, name):
        def run():
            self._dev.set_mode(name)
        self._guard(run)
        self._publish("mode")

    @Slot(int, int, int)
    def applyEq(self, bass, mid, treble):
        def run():
            self._dev.set_eq(bass, mid, treble)
        self._guard(run)
        self._publish("eq")

    @Slot()
    def reconnect(self):
        """User-triggered: drop and re-establish the link."""
        self._close()
        self._poll.stop()
        self._connect()
