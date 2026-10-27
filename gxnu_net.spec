# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单个 exe，无控制台窗口。"""

from PyInstaller.utils.hooks import collect_submodules

# tkinter 主题插件（ttk 需要）
tk_datas = []
try:
    import tkinter
    import os as _os

    _tk_dir = _os.path.join(_os.path.dirname(tkinter.__file__))
    for _sub in ("ttk", "ttk\\themes"):
        pass
    tk_datas = [
        (_os.path.join(_tk_dir, "ttk"), "tkinter/ttk"),
    ]
except Exception:
    pass

hiddenimports = [
    "psutil",
    "win32api",
    "win32con",
    "win32gui",
    "winreg",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "gxnu_net",
    "gxnu_net.tray",
    "gxnu_net.cli",
    "gxnu_net.config",
    "gxnu_net.drcom",
    "gxnu_net.logging_setup",
    "gxnu_net.ratelimit",
    "gxnu_net.watchdog",
    "gxnu_net.autostart",
    "gxnu_net.setup_gui",
]

a = Analysis(
    ["gxnu_net_app.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("config.ini.example", "."),
        ("README.md", "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "numpy",
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "notebook",
        "PyQt5",
        "PySide2",
        "PySide6",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gxnu-net",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 关键：无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
