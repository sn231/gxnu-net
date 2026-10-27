"""广西师范大学（GXNU）校园网 Dr.COM 认证客户端。

协议逆向自 https://yc.gxnu.edu.cn/ 门户前端（a41.js / a40.js），要点：

* 状态查询 ``GET /drcom/chkstatus``      -> JSONP，判断 result 是否为 1
* 登录      ``GET /drcom/login``         -> JSONP，Dr.COM 4.x 经典表单参数
* 注销      ``GET /drcom/logout``        -> JSONP

后端页面配置（loadConfig）中：
    login_method   = 0   -> 走 default_login（Dr.COM 自有协议，非 Portal 协议）
    en_md5         = 0   -> 密码明文提交，不做 MD5(PID + pwd + CALG)
    account_prefix = 1   -> PC 端账号需要前缀 ",0,"，移动端为 ",1,"
    account_suffix = ""  -> 无需运营商后缀（如 @cmcc）

账号最终形态：``,0,<学号>``
"""

from __future__ import annotations

import json
import random
import re
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

__all__ = ["DrcomClient", "LoginResult", "Status", "ISP_CHOICES"]

# 门户表单 R3 字段 -> 运营商名称
ISP_CHOICES: Dict[str, str] = {
    "0": "校园网",
    "1": "中国电信",
    "2": "中国联通",
    "3": "中国移动",
    "4": "校园网(专)",
}

# 账号前缀。门户后端 account_prefix=1，前端会拼 ",0,"（PC）/ ",1,"（移动）。
# 但 GXNU 实测：带前缀会返回 userid error1（账号不存在），裸学号才返回
# userid error2（密码错误，说明账号已被找到）。因此这里默认留空，
# 若学校后续改回前缀模式，把 DEFAULT_PREFIX 改回 ",0," 即可。
ACCOUNT_PREFIX_PC = ""
ACCOUNT_PREFIX_MOBILE = ",1,"
DEFAULT_PREFIX = ACCOUNT_PREFIX_PC

_JSONP_RE = re.compile(r"^\s*\w+\s*\((.*)\)\s*;?\s*$", re.DOTALL)


class Status:
    """在线状态常量。"""

    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass
class LoginResult:
    """登录 / 注销的统一返回。"""

    ok: bool
    status: str  # "ok" / "fail" / "error"
    msg: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - 仅用于打印
        return f"{self.status}: {self.msg}" if self.msg else self.status


class DrcomClient:
    """Dr.COM 认证客户端。

    :param host: 门户域名，如 ``yc.gxnu.edu.cn``
    :param use_https: 是否走 https（门户 enable_https=1，建议 True）
    :param timeout: 单次请求超时秒数
    """

    def __init__(
        self,
        host: str = "yc.gxnu.edu.cn",
        use_https: bool = True,
        timeout: float = 8.0,
        en_md5: bool = False,
    ) -> None:
        self.host = host.rstrip("/")
        self.use_https = use_https
        self.timeout = timeout
        # 门户后端 en_md5=0 -> 密码明文。若学校改为 1，密码需
        # md5(PID + pwd + CALG) + CALG + PID，见 a40.js default_login。
        self.en_md5 = en_md5
        self.base = f"{'https' if use_https else 'http'}://{self.host}"

        # 门户使用自签证书，这里放宽校验但不禁用，避免整站明文传输。
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

        # 模拟浏览器，避免被网关按 UA 拒绝
        self._ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )


    # ------------------------------------------------------------------ #
    # 底层请求
    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发起 JSONP 请求并解析出其中的 JSON 对象。"""
        query = urllib.parse.urlencode(params, encoding="utf-8")
        url = f"{self.base}{path}?{query}"
        req = urllib.request.Request(url)

        # 关键：禁用代理。urllib 默认读 HTTP_PROXY/HTTPS_PROXY 环境变量，
        # 若用户开着 Clash 等代理软件，认证请求会被发往 127.0.0.1:7890，
        # 而开机时代理软件往往还没就绪 -> 请求全部失败（托盘灰色、连不上）。
        # 校园网网关是内网地址，本就不该走代理。
        https_handler = urllib.request.HTTPSHandler(context=self._ssl_ctx)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), https_handler)
        opener.addheaders = [
            ("User-Agent", self._ua),
            ("Referer", self.base + "/"),
            ("Accept", "*/*"),
        ]
        with opener.open(req, timeout=self.timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")

        return self._parse_jsonp(text)

    @staticmethod
    def _parse_jsonp(text: str) -> Dict[str, Any]:
        """把 ``dr1001({...});`` 这样的响应解析成 dict。"""
        text = text.strip()
        match = _JSONP_RE.match(text)
        if match:
            text = match.group(1)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"result": 0, "msg": f"无法解析响应：{text[:120]}"}
        return data if isinstance(data, dict) else {"result": 0, "msg": str(data)}

    @staticmethod
    def _callback() -> str:
        """生成随机的 JSONP callback 名（门户前端用 dr1000 + 自增）。"""
        return f"dr{random.randint(1000, 99999)}"

    # ------------------------------------------------------------------ #
    # 业务接口
    # ------------------------------------------------------------------ #
    def check_status(self, username: str = "", password: str = "") -> Dict[str, Any]:
        """查询当前终端的认证状态。

        返回原始 dict。关键字段：

        * ``result``  1=在线，0=离线
        * ``uid``     在线账号（含前缀，如 ``,0,2099000001``）
        * ``v4ip``    网关认定的本机 IPv4
        * ``msga``    失败原因（离线时才有意义）
        """
        params = {
            "callback": self._callback(),
            "jsVersion": "4.X",
            "v": random.randint(500, 9999),
            "lang": "zh",
        }
        # 门户前端在已登录时会带上账号；带上它可以让网关返回更完整的信息。
        if username:
            params["user_account"] = username
        return self._get("/drcom/chkstatus", params)

    def is_online(self, username: str = "", password: str = "") -> Optional[bool]:
        """是否在线。无法判定时返回 ``None``。"""
        try:
            data = self.check_status(username, password)
        except Exception:
            return None
        if "result" not in data:
            return None
        try:
            return int(data["result"]) == 1
        except (TypeError, ValueError):
            return str(data["result"]) in ("1", "ok")

    def _build_login_params(self, username: str, password: str, isp: str,
                            prefix: str = DEFAULT_PREFIX) -> Dict[str, Any]:
        """构造登录参数（抽成方法便于离线单测）。"""
        return {
            "callback": self._callback(),
            "DDDDD": f"{prefix}{username}",
            "upass": self._encode_password(password),
            "0MKKey": "123456",
            "R1": "0",
            "R2": "1" if self.en_md5 else "",  # en_md5=0 时留空
            "R3": str(isp),
            "R6": "0",
            "para": "00",
            "v4ip": "",  # 由网关自行识别
            "v6ip": "",
            "terminal_type": "1",  # 1 = PC
            "lang": "zh",
            "jsVersion": "4.X",
            "v": random.randint(500, 9999),
        }

    def login(
        self,
        username: str,
        password: str,
        isp: str = "0",
        prefix: str = DEFAULT_PREFIX,
    ) -> LoginResult:
        """登录校园网。

        :param username: 学号（不含前缀）
        :param password: 密码明文（门户 en_md5=0，不做 MD5）
        :param isp: 运营商，见 :data:`ISP_CHOICES`
        :param prefix: 账号前缀，GXNU 实测为空
        """
        params = self._build_login_params(username, password, isp, prefix)

        try:
            data = self._get("/drcom/login", params)
        except Exception as exc:  # 网络层异常
            return LoginResult(False, "error", f"请求失败：{exc}")

        if self._parse_result_ok(data):
            return LoginResult(True, "ok", "登录成功", data)

        msg = str(data.get("msga") or data.get("msg") or "未知错误")
        return LoginResult(False, "fail", self._humanize(msg), data)

    @staticmethod
    def _parse_result_ok(data: Dict[str, Any]) -> bool:
        """判断网关返回是否表示成功。"""
        return str(data.get("result")) in ("1", "ok")

    def logout(self, username: str = "") -> LoginResult:
        """注销（下线）。"""
        params = {
            "callback": self._callback(),
            "jsVersion": "4.X",
            "v": random.randint(500, 9999),
            "lang": "zh",
        }
        if username:
            params["user_account"] = f"{DEFAULT_PREFIX}{username}"
        try:
            data = self._get("/drcom/logout", params)
        except Exception as exc:
            return LoginResult(False, "error", f"请求失败：{exc}")

        ok = str(data.get("result")) in ("1", "ok")
        msg = str(data.get("msg") or ("注销成功" if ok else "注销失败"))
        return LoginResult(ok, "ok" if ok else "fail", msg, data)

    def _encode_password(self, password: str) -> str:
        """按 en_md5 决定密码形态。

        Dr.COM 的 MD5 模式：``md5(PID + 密码 + CALG) + CALG + PID``
        PID/CALG 取自前端（GXNU 当前 PID='1'、CALG='12345678'）。
        """
        if not self.en_md5:
            return password
        import hashlib

        pid, calg = "1", "12345678"
        digest = hashlib.md5(f"{pid}{password}{calg}".encode("utf-8")).hexdigest()
        return f"{digest}{calg}{pid}"

    # ------------------------------------------------------------------ #
    @staticmethod
    def _humanize(msg: str) -> str:
        """把网关常见的错误码翻译成中文提示。"""
        table = {
            "userid error1": "账号不存在，请检查学号",
            "userid error2": "账号或密码错误",
            "passwd_error": "密码错误",
            "password error": "密码错误",
            "userid error3": "账号已被停用或欠费",
            "user lock": "账号已被网关锁定，请等待 10 分钟后重试",
            "time out": "认证超时，请稍后重试",
            "limit": "在线设备数已达上限，请先在其它设备下线",
            "flow": "流量已用尽",
            "arp": "IP 或 MAC 绑定校验失败",
        }
        low = msg.lower()
        for key, text in table.items():
            if key in low:
                return f"{text}（{msg}）"
        return msg
