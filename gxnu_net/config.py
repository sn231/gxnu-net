"""配置文件读写（configparser，兼容 Python 3.8+）。"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass
from typing import List

DEFAULT_CONFIG_NAMES = ("config.ini", "config.ini.example")


@dataclass
class Settings:
    """运行配置。"""

    username: str = ""
    password: str = ""
    isp: str = "0"
    host: str = "yc.gxnu.edu.cn"
    https: bool = True
    probe_urls: List[str] = None  # type: ignore[assignment]
    interval: int = 30
    timeout: float = 8.0
    fail_threshold: int = 2
    recheck_delay: int = 10
    log_file: str = "gxnu-net.log"
    log_level: str = "INFO"
    max_bytes: int = 1024 * 1024
    backup_count: int = 3

    def __post_init__(self) -> None:
        if self.probe_urls is None:
            self.probe_urls = []

    @property
    def base_url(self) -> str:
        return f"{'https' if self.https else 'http'}://{self.host}"


def find_config(explicit: str | None = None, search_dirs: List[str] | None = None) -> str | None:
    """定位配置文件。优先显式路径，其次脚本目录 / 上级目录 / 可执行文件旁。"""
    if explicit:
        return explicit if os.path.isfile(explicit) else None

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs = search_dirs or [os.getcwd(), here, os.path.dirname(here)]

    # PyInstaller 打包后：优先读 exe 所在目录（用户会把 config.ini 放旁边）
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        dirs.insert(0, exe_dir)
        # onefile 模式下还会解压到 _MEIPASS，那里可能有随包的模板
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)

    for d in dirs:
        for name in DEFAULT_CONFIG_NAMES:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                return path
    return None


def user_config_path() -> str:
    """返回"应该写入"的配置文件路径。

    打包后必须放在 exe 旁边，否则用户找不到，且每次重启可能会丢。
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "config.ini")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "config.ini")


def load(path: str | None = None) -> Settings:
    """加载配置。文件缺失时返回默认配置（仍能用于 --check 这类只读操作）。"""
    resolved = find_config(path)
    st = Settings()

    if not resolved:
        return st

    cp = configparser.ConfigParser(interpolation=None)
    # 保留键名大小写（DDDDD/upass 这类字段需要，虽然本脚本不直接用）
    cp.optionxform = str  # type: ignore[assignment]
    try:
        cp.read(resolved, encoding="utf-8")
    except configparser.Error as exc:
        raise ValueError(f"配置文件 {resolved} 解析失败：{exc}") from exc

    def get(section: str, key: str, fallback: str = "") -> str:
        return cp.get(section, key, fallback=fallback).strip()

    if cp.has_section("account"):
        st.username = get("account", "username")
        st.password = get("account", "password")
        st.isp = get("account", "isp", "0") or "0"

    if cp.has_section("network"):
        st.host = get("network", "host", st.host) or st.host
        st.https = get("network", "https", "true").lower() in ("1", "true", "yes", "on")
        raw = get("network", "probe_urls")
        st.probe_urls = [u.strip() for u in raw.split(",") if u.strip()]

    if cp.has_section("daemon"):
        st.interval = _as_int(get("daemon", "interval"), st.interval, minimum=5)
        st.timeout = _as_float(get("daemon", "timeout"), st.timeout, minimum=1.0)
        st.fail_threshold = _as_int(get("daemon", "fail_threshold"), st.fail_threshold, minimum=1)
        st.recheck_delay = _as_int(get("daemon", "recheck_delay"), st.recheck_delay, minimum=3)

    if cp.has_section("log"):
        st.log_file = get("log", "file", st.log_file)
        st.log_level = get("log", "level", st.log_level).upper()
        st.max_bytes = _as_int(get("log", "max_bytes"), st.max_bytes, minimum=1024)
        st.backup_count = _as_int(get("log", "backup_count"), st.backup_count, minimum=0)

    st.config_path = resolved  # type: ignore[attr-defined]
    return st


def _as_int(raw: str, fallback: int, minimum: int = 0) -> int:
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    return max(val, minimum)


def _as_float(raw: str, fallback: float, minimum: float = 0.0) -> float:
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    return max(val, minimum)


def is_placeholder(st: Settings) -> bool:
    """判断配置是否仍是未填写的模板。"""
    return (not st.username) or st.username in ("你的学号", "your_id") or (
        (not st.password) or st.password in ("你的密码", "your_password")
    )
