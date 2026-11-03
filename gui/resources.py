"""Programmatic app icon — a headphone glyph drawn with QPainter.

Avoids shipping any Bose artwork; the icon is generated at runtime for the
tray/window and written once as .png/.ico for the PyInstaller build.
"""

import os
import struct

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QIcon, QImage, QPainter, QPen, QBrush, QColor, QPixmap


def _draw_headphone(painter, size):
    """Draw a rounded headband + two ear cups, centered in (0,0,size,size)."""
    pen_w = max(2.0, size * 0.075)
    color = QColor("#e8eef6")
    painter.setPen(QPen(QBrush(color), pen_w,
                        Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)

    # Headband: arc from lower-left up over the top to lower-right.
    rect = QRectF(size * 0.22, size * 0.16, size * 0.56, size * 0.62)
    start = 180 * 16          # left side, pointing down
    span = 180 * 16           # half circle over the top... use 200° for coverage
    painter.drawArc(rect, start, -span)

    # Ear cups: rounded rectangles hanging at both arc ends.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(color))
    cup_w = size * 0.17
    cup_h = size * 0.24
    radius = cup_w * 0.45
    left = QRectF(size * 0.175, size * 0.55, cup_w, cup_h)
    right = QRectF(size * 0.825 - cup_w, size * 0.55, cup_w, cup_h)
    painter.drawRoundedRect(left, radius, radius)
    painter.drawRoundedRect(right, radius, radius)


def renderPixmap(size, dpr=1.0):
    """The app icon pixmap: dark disc + headphone glyph."""
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Background disc with a subtle vertical gradient.
    from PySide6.QtGui import QLinearGradient
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0.0, QColor("#3a4a5f"))
    grad.setColorAt(1.0, QColor("#1d2733"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))

    _draw_headphone(p, size)
    p.end()
    return px


def appIcon():
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(renderPixmap(size))
    return icon


# ── .ico writer for the PyInstaller build ────────────────────────────────────

def writePngs(directory):
    """Write icon_{size}.png files for the packaged build."""
    os.makedirs(directory, exist_ok=True)
    paths = []
    for size in (16, 32, 48, 256):
        path = os.path.join(directory, "icon_%d.png" % size)
        renderPixmap(size).toImage().save(path)
        paths.append((size, path))
    return paths


def writeIco(path, png_paths):
    """Assemble a PNG-in-ICO container (Vista+ format, stdlib only)."""
    entries = []
    for size, png_path in png_paths:
        entries.append((size, open(png_path, "rb").read()))
    header = struct.pack("<HHH", 0, 1, len(entries))
    dir_size = 16 * len(entries)
    offset = 6 + dir_size
    directory = b""
    blobs = b""
    for size, data in entries:
        w = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, "wb") as f:
        f.write(header + directory + blobs)


if __name__ == "__main__":
    # QPixmap needs a QGuiApplication; render headless for the build script.
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication(["resources", "-platform", "offscreen"])
    here = os.path.dirname(os.path.abspath(__file__))
    pngs = writePngs(os.path.join(here, "resources"))
    writeIco(os.path.join(here, "resources", "icon.ico"), pngs)
    print("icon written to", os.path.join(here, "resources"))
