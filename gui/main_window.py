"""Main control window — 状态 + 降噪 + 模式 + EQ."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSlider,
    QVBoxLayout, QWidget, QButtonGroup, QMessageBox,
)

CNC_LABELS = {0: "最强降噪", 10: "全通透"}


def _cnc_text(value, maximum):
    hint = CNC_LABELS.get(value, "")
    return "%d / %d%s" % (value, maximum, (" · " + hint) if hint else "")


class MainWindow(QMainWindow):
    closeRequested = Signal()   # hide-to-tray instead of quitting

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self._service = service
        self._updating = False   # suppress slider signal feedback loops
        self.setWindowTitle("bosectl")
        self.setMinimumWidth(420)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 状态卡 ────────────────────────────────────────────────────────
        status = QGroupBox("设备状态")
        sv = QVBoxLayout(status)
        self.name_label = QLabel("未连接")
        self.name_label.setStyleSheet("font-size:16px; font-weight:600;")
        self.sub_label = QLabel("正在搜索已配对的 Bose 设备…")
        self.sub_label.setStyleSheet("color:#666;")
        self.battery_label = QLabel("")
        self.battery_label.setStyleSheet("font-size:22px; font-weight:700;")
        sv.addWidget(self.name_label)
        sv.addWidget(self.sub_label)
        sv.addWidget(self.battery_label)
        layout.addWidget(status)

        # ── 降噪 CNC ──────────────────────────────────────────────────────
        self.cnc_box = QGroupBox("降噪强度（0 = 最强降噪）")
        cv = QVBoxLayout(self.cnc_box)
        self.cnc_slider = QSlider(Qt.Orientation.Horizontal)
        self.cnc_slider.setRange(0, 10)
        self.cnc_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.cnc_slider.setTickInterval(1)
        self.cnc_value = QLabel("—")
        self.cnc_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cv.addWidget(self.cnc_slider)
        cv.addWidget(self.cnc_value)
        self.cnc_slider.valueChanged.connect(self._on_cnc_preview)
        self.cnc_slider.sliderReleased.connect(self._on_cnc_apply)
        layout.addWidget(self.cnc_box)

        # ── ANR 模式（QC35 等旧设备）─────────────────────────────────────
        self.anr_box = QGroupBox("降噪模式")
        av = QHBoxLayout(self.anr_box)
        self._anr_group = QButtonGroup(self)
        for level in ("off", "low", "high", "wind"):
            btn = QPushButton({"off": "关闭", "low": "低", "high": "高",
                               "wind": "防风"}[level])
            btn.setCheckable(True)
            self._anr_group.addButton(btn)
            av.addWidget(btn)
            btn.clicked.connect(lambda _=False, lv=level: self._service.applyAnr(lv))
        layout.addWidget(self.anr_box)

        # ── 模式 ─────────────────────────────────────────────────────────
        self.mode_box = QGroupBox("模式")
        mv = QVBoxLayout(self.mode_box)
        self.mode_hint = QLabel("")
        self.mode_hint.setStyleSheet("color:#666;")
        self.mode_hint.hide()
        mv.addWidget(self.mode_hint)
        self.mode_row = QHBoxLayout()
        mv.addLayout(self.mode_row)
        layout.addWidget(self.mode_box)

        # ── EQ ───────────────────────────────────────────────────────────
        self.eq_box = QGroupBox("均衡器")
        ev = QVBoxLayout(self.eq_box)
        self._eq_sliders = {}
        for key, label in (("bass", "低音"), ("mid", "中音"), ("treble", "高音")):
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(36)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(-10, 10)
            value = QLabel("0")
            value.setMinimumWidth(32)
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            slider.valueChanged.connect(lambda v, l=value: l.setText(str(v)))
            row.addWidget(name)
            row.addWidget(slider)
            row.addWidget(value)
            ev.addLayout(row)
            self._eq_sliders[key] = (slider, value)
        self.eq_apply = QPushButton("应用 EQ")
        self.eq_apply.clicked.connect(self._on_eq_apply)
        ev.addWidget(self.eq_apply)
        layout.addWidget(self.eq_box)

        layout.addStretch(1)

    # ── slots ─────────────────────────────────────────────────────────────

    def onSnapshot(self, snap):
        self._updating = True
        try:
            self._apply_snapshot(snap)
        finally:
            self._updating = False

    def onState(self, state):
        if state != "connected":
            self.name_label.setText("未连接")
            self.sub_label.setText(
                "正在连接…" if state == "connecting" else "设备未连接，正在重试…")
            self.battery_label.setText("")

    def onError(self, message):
        self.statusBar().showMessage(message, 8000)

    def onCommandFinished(self, name):
        self.statusBar().showMessage("已应用", 2000)

    # ── internals ─────────────────────────────────────────────────────────

    def _apply_snapshot(self, snap):
        self.name_label.setText(snap["name"] or snap["model"] or "Bose 设备")
        sub = " · ".join(x for x in (snap["model"], snap["firmware"]) if x)
        self.sub_label.setText(sub)
        self.battery_label.setText("%d%%" % snap["battery"] if snap["battery"] is not None else "")
        comps = snap["battery_components"]
        readings = snap["battery_readings"]
        if comps and readings:
            parts = []
            for comp_id, level in readings:
                label = comps.get(comp_id)
                if label and 0 <= level <= 100 and comp_id != snap.get("battery"):
                    parts.append("%s %d%%" % (label, level))
            # Battery aggregate shown big; components appended to subtitle.
            if parts:
                self.sub_label.setText(sub + "　" + "　".join(parts))

        feat = snap["features"]
        self.cnc_box.setVisible(feat["cnc"])
        if feat["cnc"]:
            self.cnc_slider.setMaximum(snap["cnc_max"] or 10)
            self.cnc_slider.setValue(snap["cnc"])
            self.cnc_value.setText(_cnc_text(snap["cnc"], snap["cnc_max"] or 10))

        self.anr_box.setVisible(feat["anr"])
        if feat["anr"]:
            names = {"off": "关闭", "low": "低", "high": "高", "wind": "防风"}
            for btn in self._anr_group.buttons():
                btn.setChecked(names.get(snap["anr"], "") == btn.text())

        self.mode_box.setVisible(bool(snap["preset_modes"]))
        self._rebuild_modes(snap)

        self.eq_box.setVisible(feat["eq"])
        if feat["eq"] and snap["eq"]:
            for (name, current, lo, hi), key in zip(
                    snap["eq"], ("bass", "mid", "treble")):
                slider, _ = self._eq_sliders[key]
                slider.setRange(lo, hi)
                slider.setValue(current)

    def _rebuild_modes(self, snap):
        while self.mode_row.count():
            item = self.mode_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        presets = snap["preset_modes"]
        # Current mode: highlight by preset idx if the current slot is one,
        # otherwise show it as a custom profile via the hint label.
        current_idx = snap["mode_idx"]
        preset_idx = {m["idx"]: name for name, m in presets.items()}
        custom = current_idx is not None and current_idx not in preset_idx
        self.mode_hint.setVisible(custom)
        if custom:
            current_mode = snap["mode"]
            self.mode_hint.setText("当前为自定义模式：%s" % (current_mode or "未命名"))
        for name, meta in presets.items():
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(not custom and current_idx == meta["idx"])
            btn.setToolTip(meta.get("description", ""))
            btn.clicked.connect(lambda _=False, n=name: self._service.applyMode(n))
            self.mode_row.addWidget(btn)

    # ── UI events ─────────────────────────────────────────────────────────

    def _on_cnc_preview(self, value):
        if not self._updating:
            self.cnc_value.setText(_cnc_text(value, self.cnc_slider.maximum()))

    def _on_cnc_apply(self):
        if not self._updating:
            self._service.applyCnc(self.cnc_slider.value())

    def _on_eq_apply(self):
        bass = self._eq_sliders["bass"][0].value()
        mid = self._eq_sliders["mid"][0].value()
        treble = self._eq_sliders["treble"][0].value()
        self._service.applyEq(bass, mid, treble)

    # ── window behaviour ──────────────────────────────────────────────────

    def closeEvent(self, event):
        event.ignore()
        self.closeRequested.emit()
