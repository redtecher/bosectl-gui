# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for bosectl-gui.exe
# Build:  cd gui && pyinstaller bosectl-gui.spec
import os

here = os.path.abspath(".")

a = Analysis(
    ["run.py"],
    pathex=[here, os.path.dirname(here)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "gui",
        "gui.app",
        "gui.service",
        "gui.main_window",
        "gui.resources",
        "gui.smoke_test",
        "pybmap",
        "pybmap.catalog",
        "pybmap.connection",
        "pybmap.constants",
        "pybmap.errors",
        "pybmap.protocol",
        "pybmap.transport",
        "pybmap.discovery",
        "pybmap.types",
        "pybmap.devices",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim the PySide6 payload: anything Qt pulls in that the tray app
        # never touches.
        "tkinter",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtXml",
        "PySide6.QtDesigner",
        "PySide6.QtPdf",
        "PySide6.QtSvgWidgets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        "PySide6.QtSensors",
        "PySide6.QtPositioning",
        "PySide6.QtLocation",
        "PySide6.QtBluetooth",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="bosectl-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(here, "resources", "icon.ico"),
)
