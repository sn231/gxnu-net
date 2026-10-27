"""日志封装：同时输出到控制台和（可选的）轮转日志文件。"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

_CONSOLE_HANDLER = "__console__"


def setup(
    level: str = "INFO",
    log_file: str = "",
    max_bytes: int = 1024 * 1024,
    backup_count: int = 3,
    quiet: bool = False,
) -> logging.Logger:
    """配置并返回名为 ``gxnu-net`` 的 logger。

    重复调用不会叠加 handler；日志文件路径变化时会自动重建文件 handler。
    """
    logger = logging.getLogger("gxnu-net")
    logger.setLevel(_LEVELS.get(str(level).upper(), logging.INFO))
    logger.propagate = False

    # --- 控制台 ---
    console = None
    for h in logger.handlers:
        if getattr(h, "name", "") == _CONSOLE_HANDLER:
            console = h
            break
    if console is None:
        console = logging.StreamHandler(sys.stdout)
        console.name = _CONSOLE_HANDLER
        console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))
        logger.addHandler(console)
    console.setLevel(logging.CRITICAL if quiet else logger.level)

    # --- 文件（轮转）---
    wanted = os.path.abspath(log_file) if log_file else ""
    for h in list(logger.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            current = getattr(h, "_gxnu_logfile", "")
            if current != wanted:
                logger.removeHandler(h)
                h.close()
            else:
                h.setLevel(logger.level)
                return logger

    if wanted:
        try:
            os.makedirs(os.path.dirname(wanted) or ".", exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                wanted, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            fh._gxnu_logfile = wanted  # type: ignore[attr-defined]
            fh.setFormatter(
                logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
            )
            logger.addHandler(fh)
        except OSError as exc:
            logger.warning("无法创建日志文件 %s：%s", wanted, exc)

    return logger


def get() -> logging.Logger:
    return logging.getLogger("gxnu-net")
