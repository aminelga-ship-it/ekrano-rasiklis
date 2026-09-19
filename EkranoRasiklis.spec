# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all

spec_dir = os.path.dirname(os.path.abspath(SPEC))
icon_path = os.path.join(spec_dir, "ekrano_rasiklis.ico")
keyboard_datas, keyboard_binaries, keyboard_hidden = collect_all("keyboard")

a = Analysis(
    [os.path.join(spec_dir, "whiteboard_tool.py")],
    pathex=[spec_dir],
    binaries=keyboard_binaries,
    datas=[
        (os.path.join(spec_dir, "guest.html"), "."),
        (os.path.join(spec_dir, "assets", "stickers"), os.path.join("assets", "stickers")),
    ] + keyboard_datas,
    hiddenimports=[
        "PyQt5",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "PyQt5.QtNetwork",
        "keyboard",
        "collab",
        "shortcuts",
        "annotate_overlay",
        "app_paths",
        "license_gate",
    ] + keyboard_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pandas", "PIL", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EkranoRasiklis",
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
    icon=icon_path if os.path.isfile(icon_path) else None,
)
