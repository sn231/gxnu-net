"""登录频率限制，避免连续失败触发网关的暴力破解锁定。

GXNU 网关实测：短时间内多次登录失败会返回 ``user lock ... 600 s``，
直接锁定账号 10 分钟。因此这里做两层保护：

* ``min_interval`` —— 两次登录之间的最小间隔，防止循环里疯狂重试。
* ``max_attempts`` —— 一个时间窗口内最多允许的登录次数，超了就暫停。
"""

from __future__ import annotations

import time


class RateLimiter:
    """简单的滑动窗口限流器。"""

    def __init__(self, min_interval: float = 30.0, max_attempts: int = 3, window: float = 300.0) -> None:
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self.window = window
        self._last: float = 0.0
        self._history: list[float] = []

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        self._history = [t for t in self._history if t > cutoff]

    def wait_time(self) -> float:
        """距离「允许下一次登录」还需要等待的秒数，0 表示可以立即登录。"""
        now = time.time()
        self._prune(now)

        if len(self._history) >= self.max_attempts:
            # 窗口内次数用尽，等到最老的一次滑出窗口
            return max(0.0, self._history[0] + self.window - now)
        return max(0.0, self._last + self.min_interval - now)

    def acquire(self, wait: bool = False) -> bool:
        """尝试获取一次登录许可。

        :param wait: 为 True 时阻塞等待到可登录为止。
        :return: 是否获得许可；``wait=False`` 且需要等待时返回 False。
        """
        delay = self.wait_time()
        if delay <= 0:
            now = time.time()
            self._last = now
            self._history.append(now)
            return True
        if wait:
            time.sleep(delay)
            return self.acquire(wait=False)
        return False

    def penalize(self, seconds: float) -> None:
        """登录失败后额外冷却（例如密码错误时加大间隔）。"""
        self._last = max(self._last, time.time() + seconds)
