# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")

a = Analysis(
    ["launcher.py"],
    pathex=[".."],
    binaries=webview_binaries,
    datas=[
        ("../templates", "templates"),
        ("../static", "static"),
        *webview_datas,
    ],
    hiddenimports=webview_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="AlicePro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
