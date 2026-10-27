"""掉线检测 + 自动重连守护。"""

from __future__ import annotations

import time
from typing import Optional

from .drcom import DrcomClient, LoginResult
from .ratelimit import RateLimiter


class Watchdog:
    """周期检测网络连通性，掉线时自动重新登录。

    判定策略（按顺序）：
    1. 先问网关 ``chkstatus``：``result=1`` 直接认为在线，最省事也最准。
    2. 网关不可达时，退回 HTTP 探测 ``probe_urls``。
    3. 连续 ``fail_threshold`` 次判定离线才触发登录，避免偶发抖动导致频繁重登。
    4. 第一次判定离线后不干等一个完整周期，而是 ``recheck_delay`` 秒后复查，
       把“断网到重连”的延迟从最长两个周期压到约一个复查间隔。
    """

    def __init__(
        self,
        client: DrcomClient,
        username: str,
        password: str,
        isp: str = "0",
        interval: int = 30,
        fail_threshold: int = 2,
        recheck_delay: int = 10,
        probe_urls: Optional[list] = None,
        logger=None,
    ) -> None:
        self.client = client
        self.username = username
        self.password = password
        self.isp = isp
        self.interval = interval
        self.fail_threshold = max(1, fail_threshold)
        self.recheck_delay = recheck_delay
        self.probe_urls = list(probe_urls or [])
        self.log = logger
        self.last_reason = ""

        # 防暴力破解：默认两次登录至少间隔 30 秒，5 分钟内最多 3 次
        self.limiter = RateLimiter(min_interval=30.0, max_attempts=3, window=300.0)

        self._miss = 0
        self._stop = False

    # ------------------------------------------------------------------ #
    def _probe_http(self) -> bool:
        """用外网地址探测。任一可达即认为在线。"""
        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        for url in self.probe_urls:
            try:
                req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "gxnu-net"})
                # 同样禁用代理（原因见 drcom.py 中的说明）
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}),
                    urllib.request.HTTPSHandler(context=ctx),
                )
                with opener.open(req, timeout=self.client.timeout) as _r:
                    return True
            except Exception:
                continue
        return False

    def is_online(self) -> bool:
        """综合判断是否在线；``last_reason`` 记录本次判定依据。"""
        state = self.client.is_online(self.username)
        if state is not None:
            self.last_reason = "网关判定在线" if state else "网关返回离线"
            return state
        # 网关没回应 -> 用外网探测兜底
        online = self._probe_http()
        self.last_reason = "网关无响应，外网可达" if online else "网关无响应，外网也不可达"
        return online

    def next_delay(self) -> float:
        """本轮检测后的等待时间：离线待确认时快速复查，其余按正常间隔。"""
        if 0 < self._miss < self.fail_threshold:
            return min(self.recheck_delay, self.interval)
        return self.interval

    # ------------------------------------------------------------------ #
    def try_login(self) -> LoginResult:
        """执行一次登录并重置计数。带频率保护与失败冷却。"""
        delay = self.limiter.wait_time()
        if delay > 0:
            msg = f"触发保护，{delay:.0f} 秒后才允许再次登录"
            if self.log:
                self.log.warning(msg)
            return LoginResult(False, "rate_limited", msg)

        self.limiter.acquire()
        res = self.client.login(self.username, self.password, self.isp)

        if res.ok:
            self._miss = 0
            return res

        # 账号被锁定 / 密码错误：加长冷却，避免继续撞网关风控
        raw = str(res.raw.get("msga") or res.raw.get("msg") or "").lower()
        if "lock" in raw:
            if self.log:
                self.log.error("账号已被网关锁定，暂停登录 10 分钟：%s", res.msg)
            self.limiter.penalize(600)
        elif "error2" in raw or "passwd" in raw or "password" in raw:
            if self.log:
                self.log.error("密码错误，请检查 config.ini 中的 password（暂停 5 分钟）")
            self.limiter.penalize(300)
        return res

    def run_forever(self) -> None:
        """阻塞式守护循环，直到 :meth:`stop` 被调用或收到 KeyboardInterrupt。"""
        if self.log:
            self.log.info(
                "守护已启动：每 %d 秒检测一次，离线后 %d 秒复查，连续 %d 次离线则重新登录",
                self.interval,
                min(self.recheck_delay, self.interval),
                self.fail_threshold,
            )

        while not self._stop:
            try:
                online = self.is_online()
            except Exception as exc:
                online = False
                if self.log:
                    self.log.debug("检测异常：%s", exc)

            if online:
                if self._miss and self.log:
                    self.log.debug("网络恢复，离线计数归零")
                self._miss = 0
            else:
                self._miss += 1
                if self.log:
                    self.log.warning(
                        "检测到离线（%d/%d）：%s", self._miss, self.fail_threshold, self.last_reason
                    )
                if self._miss >= self.fail_threshold:
                    res = self.try_login()
                    if self.log:
                        if res.ok:
                            self.log.info("已重新登录：%s", res.msg)
                        elif res.status == "rate_limited":
                            self.log.warning("跳过本次登录：%s", res.msg)
                        else:
                            self.log.error("重新登录失败：%s", res.msg)

            self._sleep(self.next_delay())

    def stop(self) -> None:
        self._stop = True

    def _sleep(self, seconds: int) -> None:
        """可中断的 sleep，保证 Ctrl+C 能及时退出。"""
        end = time.time() + seconds
        while not self._stop and time.time() < end:
            time.sleep(min(0.5, max(0.0, end - time.time())))
