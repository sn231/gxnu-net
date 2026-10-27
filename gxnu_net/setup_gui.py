"""首次运行的图形化配置窗口（tkinter）。

打包成 pythonw / PyInstaller --windowed 后没有控制台，
命令行里的 input() 无法使用，因此用 tkinter 做一个简单的输入窗口。

仅在配置缺失（或用户主动要求）时弹出。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from .config import Settings
from .drcom import ISP_CHOICES

_ENTRY_WIDTH = 32


class ConfigDialog:
    """账号配置窗口。``show()`` 返回 True 表示用户已保存配置。"""

    def __init__(self, settings: Settings, config_path: str) -> None:
        self.st = settings
        self.path = config_path
        self.saved = False
        self.root: Optional[tk.Tk] = None
        self._pwd_entry: Optional[ttk.Entry] = None

    # ------------------------------------------------------------------ #
    def show(self) -> bool:
        self.root = tk.Tk()
        self.root.title("gxnu-net 校园网自动登录 — 首次配置")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text="填写你的校园网账号（仅保存在本机）",
            font=("Microsoft YaHei", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # --- 学号 ---
        ttk.Label(frame, text="学号：").grid(row=1, column=0, sticky="e", pady=5)
        self.user_var = tk.StringVar(value=self._current_username())
        ttk.Entry(frame, textvariable=self.user_var, width=_ENTRY_WIDTH).grid(
            row=1, column=1, sticky="w", pady=5
        )

        # --- 密码 ---
        ttk.Label(frame, text="密码：").grid(row=2, column=0, sticky="e", pady=5)
        self.pwd_var = tk.StringVar()
        self._pwd_entry = ttk.Entry(
            frame, textvariable=self.pwd_var, show="*", width=_ENTRY_WIDTH
        )
        self._pwd_entry.grid(row=2, column=1, sticky="w", pady=5)

        self.show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="显示密码", variable=self.show_var, command=self._toggle_pwd
        ).grid(row=3, column=1, sticky="w")

        # --- 运营商 ---
        ttk.Label(frame, text="运营商：").grid(row=4, column=0, sticky="e", pady=5)
        isp = self.st.isp if self.st.isp in ISP_CHOICES else "0"
        self.isp_options = [f"{k} - {v}" for k, v in ISP_CHOICES.items()]
        self.isp_var = tk.StringVar(value=f"{isp} - {ISP_CHOICES[isp]}")
        ttk.Combobox(
            frame,
            textvariable=self.isp_var,
            values=self.isp_options,
            state="readonly",
            width=_ENTRY_WIDTH - 3,
        ).grid(row=4, column=1, sticky="w", pady=5)

        ttk.Label(
            frame,
            text="提示：密码结尾的点号等特殊字符请原样输入",
            foreground="#888888",
            font=("Microsoft YaHei", 8),
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        # --- 按钮 ---
        btns = ttk.Frame(frame)
        btns.grid(row=6, column=0, columnspan=2, pady=(16, 0))
        ttk.Button(btns, text="保存并启动", command=self._on_save, width=14).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(btns, text="取消", command=self._on_cancel, width=10).pack(side=tk.LEFT)

        self.root.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._center()
        self.root.mainloop()
        return self.saved

    # ------------------------------------------------------------------ #
    def _center(self) -> None:
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _toggle_pwd(self) -> None:
        if self._pwd_entry is not None:
            self._pwd_entry.configure(show="" if self.show_var.get() else "*")

    def _current_username(self) -> str:
        v = self.st.username
        return "" if (not v) or v in ("你的学号", "your_id") else v

    def _on_save(self) -> None:
        user = self.user_var.get().strip()
        pwd = self.pwd_var.get()
        isp = self.isp_var.get().split(" - ")[0].strip()
        if isp not in ISP_CHOICES:
            isp = "0"

        if not user:
            messagebox.showwarning("缺少学号", "请填写学号", parent=self.root)
            return
        if not pwd:
            messagebox.showwarning("缺少密码", "请填写密码", parent=self.root)
            return

        self.st.username = user
        self.st.password = pwd
        self.st.isp = isp
        try:
            self._write()
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法写入配置文件：{exc}", parent=self.root)
            return

        self.saved = True
        self.root.destroy()

    def _on_cancel(self) -> None:
        self.saved = False
        if self.root is not None:
            self.root.destroy()

    def _write(self) -> None:
        content = f"""; gxnu-net 配置 - 由配置向导生成
; 本文件含密码，请勿分享或提交到 Git。

[account]
username = {self.st.username}
password = {self.st.password}
isp = {self.st.isp}

[network]
host = {self.st.host}
https = {'true' if self.st.https else 'false'}
probe_urls = {','.join(self.st.probe_urls)}

[daemon]
interval = {self.st.interval}
timeout = {self.st.timeout}
fail_threshold = {self.st.fail_threshold}

[log]
file = {self.st.log_file}
level = {self.st.log_level}
max_bytes = {self.st.max_bytes}
backup_count = {self.st.backup_count}
"""
        d = os.path.dirname(os.path.abspath(self.path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(content)


def show_config_dialog(settings: Settings, config_path: str) -> bool:
    """弹出配置窗口。无 GUI 环境（无显示器 / 无 tkinter）时返回 False。"""
    try:
        return ConfigDialog(settings, config_path).show()
    except Exception:
        return False
