"""Windows 系统托盘常驻版：无控制台窗口，只在右下角显示托盘图标。

用 pythonw.exe 启动即可完全无窗口：
    pythonw.exe -m gxnu_net.tray

托盘图标菜单：
    左键双击 / 右键菜单 -> 查看状态、立即重连、打开日志、退出

依赖：pywin32（win32api / win32gui / win32con），已在本机可用。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Optional

try:
    import win32api
    import win32con
    import win32event
    import win32gui
except ImportError:  # pragma: no cover - 非 Windows 或缺少依赖
    win32api = win32con = win32event = win32gui = None  # type: ignore[assignment]

from .config import Settings, is_placeholder, load
from .drcom import ISP_CHOICES, DrcomClient
from .logging_setup import setup as setup_logging
from .watchdog import Watchdog

APP_NAME = "gxnu-net"

# 托盘图标状态 -> (提示文字, 颜色)
STATE_ONLINE = "online"
STATE_OFFLINE = "offline"
STATE_UNKNOWN = "unknown"

_COLORS = {
    STATE_ONLINE: (0x2E, 0xCC, 0x71),    # 绿
    STATE_OFFLINE: (0xE7, 0x4C, 0x3C),   # 红
    STATE_UNKNOWN: (0x95, 0xA5, 0xA6),   # 灰
}


class TrayApp:
    """系统托盘应用。"""

    def __init__(self, settings: Settings, log) -> None:
        self.st = settings
        self.log = log
        self.client = DrcomClient(host=settings.host, use_https=settings.https, timeout=settings.timeout)
        self.watchdog = Watchdog(
            client=self.client,
            username=settings.username,
            password=settings.password,
            isp=settings.isp,
            interval=settings.interval,
            fail_threshold=settings.fail_threshold,
            recheck_delay=settings.recheck_delay,
            probe_urls=settings.probe_urls,
            logger=log,
        )

        self.state = STATE_UNKNOWN
        self.last_event = "启动中..."
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._hwnd = None
        self._taskbar_created = None
        self._quitting = False
        self._icons: dict = {}
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # 托盘图标绘制
    # ------------------------------------------------------------------ #
    def _make_icon(self, state: str):
        """生成状态图标（绿=在线，红=离线，灰=未知）。

        做法：用 PIL 画出 PNG，再转成 .ico 交给 LoadImage 加载。
        比手写 GDI 位图稳定得多，也不需要额外的图片资源文件。
        """
        from PIL import Image, ImageDraw

        r, g, b = _COLORS.get(state, _COLORS[STATE_UNKNOWN])
        size = 64  # 用 64px 源图，系统会自动缩放到托盘尺寸

        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((0, 0, size - 1, size - 1), fill=(r, g, b, 255))
        draw.ellipse((22, 22, 41, 41), fill=(255, 255, 255, 255))

        path = self._icon_path(state)
        img.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])

        hinst = win32api.GetModuleHandle(None)
        icon = win32gui.LoadImage(
            hinst, path, win32con.IMAGE_ICON, 0, 0, win32con.LR_LOADFROMFILE
        )
        return icon

    def _icon_path(self, state: str) -> str:
        """图标缓存路径（放在临时目录，避免污染项目目录）。"""
        import tempfile

        cache_dir = os.path.join(tempfile.gettempdir(), APP_NAME + "-icons")
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"{state}.ico")

    def _ensure_icons(self) -> None:
        if not self._icons:
            for st in _COLORS:
                self._icons[st] = self._make_icon(st)

    # ------------------------------------------------------------------ #
    # 窗口与消息循环
    # ------------------------------------------------------------------ #
    def _create_window(self) -> None:
        self._taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "GxnuNetTray"
        wc.lpfnWndProc = self._wnd_proc
        try:
            win32gui.RegisterClass(wc)
        except win32gui.error:
            pass  # 已注册

        self._hwnd = win32gui.CreateWindow(
            wc.lpszClassName, APP_NAME, 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
        )
        win32gui.UpdateWindow(self._hwnd)

    def _add_tray(self) -> None:
        self._ensure_icons()
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (self._hwnd, 0, flags, WM_TRAY, self._icons[self.state], self._tooltip())
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

    def _update_tray(self) -> None:
        """在窗口线程刷新图标及提示文字。"""
        if self._hwnd is None or self._stop_event.is_set():
            return
        with self._state_lock:
            flags = win32gui.NIF_ICON | win32gui.NIF_TIP
            nid = (self._hwnd, 0, flags, WM_TRAY, self._icons[self.state], self._tooltip())
        win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)

    def _remove_tray(self) -> None:
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self._hwnd, 0))
        except Exception:
            pass

    def _tooltip(self) -> str:
        text = {
            STATE_ONLINE: "校园网已连接",
            STATE_OFFLINE: "校园网已断开",
            STATE_UNKNOWN: "校园网状态未知",
        }[self.state]
        return f"{APP_NAME} - {text}"

    # ------------------------------------------------------------------ #
    # 菜单
    # ------------------------------------------------------------------ #
    def _show_menu(self) -> None:
        menu = win32gui.CreatePopupMenu()

        status_text = {
            STATE_ONLINE: "状态：已连接",
            STATE_OFFLINE: "状态：已断开",
            STATE_UNKNOWN: "状态：未知",
        }[self.state]
        win32gui.AppendMenu(menu, win32con.MF_STRING | win32con.MF_DISABLED, 0, status_text)
        win32gui.AppendMenu(menu, win32con.MF_STRING | win32con.MF_DISABLED, 0, self.last_event)
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")

        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_RECONNECT, "立即重连")
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_STATUS, "刷新状态")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")

        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_LOG, "打开日志文件")
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_AUTOSTART, "开机自启：切换")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_EXIT, "退出")

        pos = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self._hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, pos[0], pos[1], 0, self._hwnd, None)
        win32gui.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_UPDATE_TRAY:
            try:
                self._update_tray()
            except Exception:
                self.log.exception("刷新托盘图标失败")
            return 0
        if self._taskbar_created is not None and msg == self._taskbar_created:
            try:
                self._add_tray()
            except Exception:
                self.log.exception("恢复托盘图标失败")
            return 0
        if msg == WM_TRAY:
            if lparam == win32con.WM_LBUTTONDBLCLK:
                self._on_reconnect()
            elif lparam == win32con.WM_RBUTTONUP:
                self._show_menu()
            return 0
        if msg == win32con.WM_COMMAND:
            cmd = win32api.LOWORD(wparam)
            if cmd == ID_EXIT:
                self.quit()
            elif cmd == ID_RECONNECT:
                self._on_reconnect()
            elif cmd == ID_STATUS:
                self._on_refresh()
            elif cmd == ID_LOG:
                self._on_open_log()
            elif cmd == ID_AUTOSTART:
                self._on_toggle_autostart()
            return 0
        if msg == win32con.WM_DESTROY:
            self.quit()
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    # ------------------------------------------------------------------ #
    # 菜单动作
    # ------------------------------------------------------------------ #
    def _set_state(self, state: str, event: str) -> None:
        with self._state_lock:
            self.state = state
            self.last_event = event
        if self._hwnd is not None and not self._stop_event.is_set():
            try:
                win32gui.PostMessage(self._hwnd, WM_UPDATE_TRAY, 0, 0)
            except Exception:
                if not self._stop_event.is_set():
                    self.log.exception("无法发送托盘刷新消息")

    def _on_refresh(self) -> None:
        def work():
            self._set_state(STATE_UNKNOWN, "正在查询...")
            try:
                online = self.watchdog.is_online()
            except Exception:
                self._set_state(STATE_UNKNOWN, "查询失败，稍后重试")
                self.log.exception("手动查询失败")
                return

            if online:
                self._set_state(STATE_ONLINE, f"已连接 · {time.strftime('%H:%M:%S')}")
                self.log.info("手动刷新：在线")
            else:
                self._set_state(STATE_OFFLINE, f"已断开 · {time.strftime('%H:%M:%S')}")
                self.log.warning("手动刷新：离线（%s）", self.watchdog.last_reason)

        threading.Thread(target=work, daemon=True).start()

    def _on_reconnect(self) -> None:
        def work():
            self._set_state(STATE_UNKNOWN, "正在重连...")
            try:
                res = self.watchdog.try_login()
            except Exception:
                self._set_state(STATE_UNKNOWN, "重连异常，请查看日志")
                self.log.exception("手动重连异常")
                return
            if res.ok:
                self._set_state(STATE_ONLINE, f"重连成功 · {time.strftime('%H:%M:%S')}")
                self.log.info("手动重连成功")
            else:
                self._set_state(STATE_OFFLINE, f"重连失败：{res.msg}")
                self.log.error("手动重连失败：%s", res.msg)

        threading.Thread(target=work, daemon=True).start()

    def _on_open_log(self) -> None:
        path = os.path.abspath(self.st.log_file or "gxnu-net.log")
        if os.path.isfile(path):
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                return
            except Exception:
                pass
            webbrowser.open(path)
        else:
            self._set_state(self.state, "日志文件不存在")

    def _on_toggle_autostart(self) -> None:
        try:
            from .autostart import is_enabled, disable, enable

            if is_enabled():
                ok = disable()
                self._set_state(self.state, "已关闭开机自启" if ok else "关闭自启失败")
            else:
                ok = enable()
                self._set_state(self.state, "已开启开机自启" if ok else "开启自启失败")
        except Exception as exc:
            self._set_state(self.state, f"自启操作失败：{exc}")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _monitor_loop(self) -> None:
        """后台监控循环：复用 Watchdog 的判定逻辑，但自己控制节奏以便更新图标。"""
        while not self._stop_event.is_set():
            try:
                self._monitor_once()
            except Exception:
                self.log.exception("后台检测异常，将在下一周期重试")
                self._set_state(STATE_UNKNOWN, "检测异常，稍后重试")

            self._stop_event.wait(self.watchdog.next_delay())

    def _monitor_once(self) -> None:
        """执行一轮检测；检测与图标更新异常均不会终止后台循环。"""
        online = self.watchdog.is_online()
        if online:
            if self.state != STATE_ONLINE:
                self.log.info("网络已连接")
            self.watchdog._miss = 0
            self._set_state(STATE_ONLINE, f"已连接 · {time.strftime('%H:%M:%S')}")
        else:
            self.watchdog._miss += 1
            self._set_state(STATE_OFFLINE, f"断开中（{self.watchdog._miss}）")
            self.log.warning(
                "检测到离线（%d/%d）：%s",
                self.watchdog._miss,
                self.watchdog.fail_threshold,
                self.watchdog.last_reason,
            )
            if self.watchdog._miss >= self.watchdog.fail_threshold:
                res = self.watchdog.try_login()
                if res.ok:
                    self._set_state(STATE_ONLINE, f"已重连 · {time.strftime('%H:%M:%S')}")
                    self.log.info("自动重连成功")
                else:
                    self._set_state(STATE_OFFLINE, f"重连失败：{res.msg}")
                    self.log.error("自动重连失败：%s", res.msg)

    def run(self) -> int:
        if win32gui is None:
            msg = (
                "托盘模式需要 pywin32，请执行：pip install pywin32\n"
                "或使用无窗口守护模式（不显示图标）。"
            )
            self.log.error(msg)
            print(msg, file=sys.stderr)
            return 1

        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            msg = "托盘图标需要 Pillow，请执行：pip install Pillow"
            self.log.error(msg)
            print(msg, file=sys.stderr)
            return 1

        self._create_window()
        self._add_tray()
        self.log.info(
            "托盘程序已启动（检测间隔 %d 秒，离线后 %d 秒复查）",
            self.st.interval,
            min(self.watchdog.recheck_delay, self.st.interval),
        )

        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

        win32gui.PumpMessages()
        return 0

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._stop_event.set()
        self._remove_tray()
        self.log.info("托盘程序已退出")
        try:
            win32gui.DestroyWindow(self._hwnd)
        except Exception:
            pass
        win32gui.PostQuitMessage(0)


# 菜单命令 ID
ID_RECONNECT = 1001
ID_STATUS = 1002
ID_LOG = 1003
ID_AUTOSTART = 1004
ID_EXIT = 1005
WM_TRAY = win32con.WM_USER + 20 if win32con else 0x0400 + 20
WM_UPDATE_TRAY = win32con.WM_APP + 20 if win32con else 0x8000 + 20


def _query_processes() -> dict:
    """返回 {pid: ppid}。优先 PowerShell，wmic 在新版 Windows 已弃用。"""
    ps = (
        "Get-CimInstance Win32_Process | "
        "ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"
    )
    for cmd, args in (
        ("powershell", ["-NoProfile", "-Command", ps]),
        ("wmic", ["process", "get", "ProcessId,ParentProcessId"]),
    ):
        try:
            out = subprocess.run(
                [cmd] + args, capture_output=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.decode("utf-8", errors="replace")
            result = {}
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        result[int(parts[-2])] = int(parts[-1])
                    except ValueError:
                        continue
            if result:
                return result
        except Exception:
            continue
    return {}


def _my_process_tree(all_proc: dict) -> set:
    """「我自己 + 我的所有子孙进程」的 PID 集合。

    PyInstaller 单文件 exe 会 fork 出父子两个进程，它们在同一棵树内，
    不算重复实例；用户再次双击产生的进程在另一棵树上。
    """
    tree = {os.getpid()}
    changed = True
    while changed:
        changed = False
        for pid, ppid in all_proc.items():
            if ppid in tree and pid not in tree:
                tree.add(pid)
                changed = True
    return tree


def _already_running() -> bool:
    """是否已有另一个*独立*实例在运行。

    踩过的坑（都实测过，别改回去）：
    1. 纯 Mutex 无效 —— PyInstaller fork 的子进程 CreateMutex 会复用
       继承句柄，GetLastError 恒为 0，感知不到父进程的锁。
    2. 纯进程树判断有竞态 —— 父进程启动时子进程还没创建完，
       tasklist 里的 PID 不在自己树中，会误杀自己。
    3. 在 exe 内部用 subprocess 调 PowerShell 会挂死 —— 无控制台环境
       下管道不可靠，实测阻塞 >120 秒。
    4. 只比启动时间也不行 —— PyInstaller 父子进程相差约 1 秒，
       阈值设 3 秒仍可能误判（实测把自己拦掉了）。

    最终方案（psutil 纯内存遍历，不启子进程）：
    A. 先排除「我的祖先和后代」—— 用 ppid 链双向遍历，父子进程天然豁免。
    B. 剩下的同名进程里，若启动时间早于我 10 秒以上，才是重复实例。
       10 秒远大于 fork 耗时（约 1 秒），又远小于用户重复双击的间隔。

    :return: True 表示已有另一个独立实例在跑（本次应退出）
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        return False  # 无 psutil 则不保护，保证程序一定能起来

    try:
        me = psutil.Process(os.getpid())
        my_start = me.create_time()
        my_name = me.name().lower()
    except Exception:
        return False

    # --- A. 收集我的祖先 ---
    relatives = {os.getpid()}
    try:
        cur = me
        for _ in range(10):  # 最多向上 10 层，防环
            ppid = cur.ppid()
            if ppid in relatives:
                break
            relatives.add(ppid)
            cur = psutil.Process(ppid)
    except Exception:
        pass

    # --- B. 收集我的后代 ---
    try:
        all_proc = list(psutil.process_iter(["pid", "ppid", "name", "create_time"]))
    except Exception:
        return False

    changed = True
    while changed:
        changed = False
        for info in all_proc:
            pid = info.info.get("pid")
            ppid = info.info.get("ppid")
            if pid is not None and ppid in relatives and pid not in relatives:
                relatives.add(pid)
                changed = True

    # --- C. 判断是否另有独立实例 ---
    for info in all_proc:
        try:
            if (info.info.get("name") or "").lower() != my_name:
                continue
            pid = info.info.get("pid")
            if pid is None or pid in relatives:
                continue
            ct = info.info.get("create_time")
            if ct is None:
                continue
            if my_start - ct > 10:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 已经有一个实例在跑 -> 直接退出，避免重复托盘 / 重复登录互相踢
    if _already_running():
        return 0

    # 自启时传入 --chdir，保证能找到同目录的 config.ini
    if "--chdir" in argv:
        idx = argv.index("--chdir")
        if idx + 1 < len(argv):
            os.chdir(argv[idx + 1])
            del argv[idx : idx + 2]

    # 打包成 exe 后，工作目录可能不是 exe 所在目录，统一切过去
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))

    st = load()
    log = setup_logging(
        level=st.log_level,
        log_file=st.log_file,
        max_bytes=st.max_bytes,
        backup_count=st.backup_count,
        quiet=True,  # 无窗口，不往控制台写
    )

    # 配置没填 -> 弹图形化配置窗口（exe 无控制台，不能用 input）
    if is_placeholder(st):
        from .config import user_config_path
        from .setup_gui import show_config_dialog

        target = user_config_path()
        ok = show_config_dialog(st, target)
        if not ok:
            log.info("用户取消配置，程序退出")
            return 0
        st = load()  # 重新读取刚写入的配置
        log.info("已保存配置，账号：%s", st.username)

    if not st.username or not st.password:
        log.error("config.ini 未填写账号密码，托盘程序退出")
        return 2

    app = TrayApp(st, log)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
