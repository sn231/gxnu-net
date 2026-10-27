"""开机自启：写入 HKCU 注册表 Run 项。

相比启动文件夹快捷方式，注册表 Run 项更可靠（不受快捷方式路径变更影响），
且配合 pythonw.exe 启动时完全无窗口。
"""

from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "gxnu-net"

try:
    import winreg
except ImportError:  # pragma: no cover - 非 Windows
    winreg = None  # type: ignore[assignment]


def _exe_and_args() -> tuple[str, str]:
    """返回用于自启的 (程序路径, 参数)。

    用 pythonw.exe 而不是 python.exe：前者不创建控制台窗口。
    启动 tray.pyw 而不是 -m gxnu_net.tray，避免工作目录导致找不到包。
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(here, "tray.pyw")

    exe = sys.executable or ""
    # python.exe -> pythonw.exe（同目录）
    base, ext = os.path.splitext(exe)
    pythonw = base + "w" + ext
    if os.path.isfile(pythonw):
        exe = pythonw

    return exe, f'"{script}"'


def _command() -> str:
    exe, args = _exe_and_args()
    return f'"{exe}" {args}'


def enable() -> bool:
    """写入注册表开启自启。成功返回 True。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
        return True
    except OSError:
        return False


def disable() -> bool:
    """删除注册表自启项。成功返回 True。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return True  # 本来就没有，视为成功
    except OSError:
        return False


def is_enabled() -> bool:
    """查询当前是否已开启自启。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def command_preview() -> str:
    """返回将要写入的命令行（用于展示/调试）。"""
    return _command()
