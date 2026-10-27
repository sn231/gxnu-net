"""离线单元测试：不发起真实网络请求，避免触发网关风控。

运行：
    python -m pytest tests/ -v
或直接：
    python tests/test_smoke.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gxnu_net.config import Settings, is_placeholder, load  # noqa: E402
from gxnu_net.drcom import DrcomClient, ISP_CHOICES  # noqa: E402
from gxnu_net.ratelimit import RateLimiter  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
def test_jsonp_parse() -> None:
    print("\n[JSONP 解析]")
    cases = [
        ('dr1001({"result":1,"uid":"2099000001"});', 1),
        ('  dr999({"result":0,"msga":"userid error2"})  ', 0),
        ('dr1({"result":0,"msg":"user lock ... 600 s"});', 0),
        ("not json at all", 0),
    ]
    for raw, expect in cases:
        data = DrcomClient._parse_jsonp(raw)
        check(f"解析 result={expect}: {raw[:40]}", int(data.get("result", -1)) == expect)


def test_login_success_detection() -> None:
    print("\n[登录结果判定]")
    ok_cases = [{"result": 1}, {"result": "1"}, {"result": "ok"}]
    for c in ok_cases:
        check(f"成功判定 {c}", DrcomClient._parse_result_ok(c))
    bad_cases = [{"result": 0}, {"result": "0"}, {"msga": "userid error2"}, {}]
    for c in bad_cases:
        check(f"失败判定 {c}", not DrcomClient._parse_result_ok(c))


def test_humanize() -> None:
    print("\n[错误码翻译]")
    table = {
        "userid error1": "账号不存在",
        "userid error2": "密码错误",
        "user lock ... 600 s": "锁定",
    }
    for raw, keyword in table.items():
        msg = DrcomClient._humanize(raw)
        check(f"{raw} -> 含「{keyword}」", keyword in msg, msg)


def test_account_prefix() -> None:
    """GXNU 实测：账号不加 ,0, 前缀。"""
    print("\n[账号格式]")
    from gxnu_net.drcom import DEFAULT_PREFIX

    check("默认前缀为空", DEFAULT_PREFIX == "", repr(DEFAULT_PREFIX))
    # 构造参数时用默认前缀，账号应原样
    client = DrcomClient()
    params = client._build_login_params("2099000001", "pwd", "2")
    check("DDDDD 不带前缀", params["DDDDD"] == "2099000001", params["DDDDD"])
    check("upass 明文（en_md5=0）", params["upass"] == "pwd")
    check("R3 = 2（联通）", params["R3"] == "2")
    check("0MKKey = 123456", params["0MKKey"] == "123456")
    check("R2 为空（en_md5=0）", params["R2"] == "", repr(params["R2"]))


def test_md5_mode() -> None:
    """若学校开启 en_md5，密码应为 md5(PID+pwd+CALG)+CALG+PID。"""
    print("\n[MD5 模式]")
    import hashlib

    client = DrcomClient(en_md5=True)
    params = client._build_login_params("2099000001", "ExamplePwd!.", "2")
    expect = hashlib.md5("1ExamplePwd!.12345678".encode()).hexdigest() + "12345678" + "1"
    check("MD5 密码正确", params["upass"] == expect)
    check("R2 = 1（en_md5=1）", params["R2"] == "1")


def test_isp_choices() -> None:
    print("\n[运营商映射]")
    check("0/1/2/3/4 齐全", sorted(ISP_CHOICES) == ["0", "1", "2", "3", "4"])
    check("2 = 中国联通", ISP_CHOICES["2"] == "中国联通")


def test_ratelimit() -> None:
    print("\n[频率限制]")
    rl = RateLimiter(min_interval=0.3, max_attempts=2, window=2.0)
    check("首次允许", rl.acquire())
    check("间隔内拒绝", not rl.acquire())
    time.sleep(0.35)
    check("间隔后允许", rl.acquire())
    check("超窗口次数拒绝", not rl.acquire())
    check("拒绝对外暴露等待时间", rl.wait_time() > 0)

    rl2 = RateLimiter(min_interval=0.0)
    rl2.acquire()
    rl2.penalize(0.5)
    check("penalize 生效", rl2.wait_time() > 0.3, str(rl2.wait_time()))


def test_config() -> None:
    print("\n[配置加载]")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    st = load(os.path.join(here, "config.ini.example"))
    check("模板被识别为未填写", is_placeholder(st))
    check("isp 默认 0", st.isp == "0")
    check("host 默认正确", st.host == "yc.gxnu.edu.cn")
    check("https 默认开", st.https is True)

    st2 = Settings(username="2099000001", password="ExamplePwd!.", isp="2")
    check("已填写配置不误判", not is_placeholder(st2))
    check("密码带点号保留", st2.password.endswith("."))


def test_config_password_special_chars() -> None:
    """密码含 % 等字符时不能被插值破坏。"""
    print("\n[密码特殊字符]")
    import configparser

    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string("[account]\nusername = 1\npassword = p@ss%word.\nisp = 2\n")
    check("百分号不丢失", cp.get("account", "password") == "p@ss%word.")


def test_recheck_delay() -> None:
    """首次离线后应快速复查，而不是干等一个完整周期。"""
    print("\n[离线复查节奏]")
    from gxnu_net.watchdog import Watchdog

    wd = Watchdog(
        client=DrcomClient(), username="x", password="y",
        interval=60, fail_threshold=2, recheck_delay=10,
    )
    check("在线时等待完整周期", wd.next_delay() == 60, str(wd.next_delay()))
    wd._miss = 1
    check("首次离线后 10 秒复查", wd.next_delay() == 10, str(wd.next_delay()))
    wd._miss = 2
    check("达到阈值后回到正常周期", wd.next_delay() == 60, str(wd.next_delay()))

    wd2 = Watchdog(
        client=DrcomClient(), username="x", password="y",
        interval=5, fail_threshold=2, recheck_delay=10,
    )
    wd2._miss = 1
    check("复查间隔不超过正常周期", wd2.next_delay() == 5, str(wd2.next_delay()))


def main() -> int:
    print("=" * 60)
    print("gxnu-net 离线单元测试")
    print("=" * 60)

    test_jsonp_parse()
    test_login_success_detection()
    test_humanize()
    test_account_prefix()
    test_md5_mode()
    test_isp_choices()
    test_ratelimit()
    test_recheck_delay()
    test_config()
    test_config_password_special_chars()

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
