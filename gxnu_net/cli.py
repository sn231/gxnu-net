#!/usr/bin/env python3
"""gxnu-net —— 广西师范大学（GXNU）校园网自动登录 / 掉线自动重连。

用法示例：
    python -m gxnu_net             # 交互式首次配置，然后后台守护
    python -m gxnu_net --once      # 检测一次，掉线就登录，然后退出
    python -m gxnu_net --status    # 只看当前状态
    python -m gxnu_net --logout    # 下线
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from typing import Optional

from . import __version__
from .config import Settings, is_placeholder, load
from .drcom import ISP_CHOICES, DrcomClient
from .logging_setup import setup as setup_logging
from .watchdog import Watchdog

APP = "gxnu-net"


# --------------------------------------------------------------------------- #
# 配置向导
# --------------------------------------------------------------------------- #
def _write_config(path: str, st: Settings) -> None:
    template = f"""; {APP} 配置 —— 由配置向导生成
; 本文件包含你的校园网密码，请勿分享或提交到 Git。

[account]
username = {st.username}
password = {st.password}
isp = {st.isp}

[network]
host = {st.host}
https = {'true' if st.https else 'false'}
probe_urls = {','.join(st.probe_urls)}

[daemon]
interval = {st.interval}
timeout = {st.timeout}
fail_threshold = {st.fail_threshold}
recheck_delay = {st.recheck_delay}

[log]
file = {st.log_file}
level = {st.log_level}
max_bytes = {st.max_bytes}
backup_count = {st.backup_count}
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(template)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 上没有严格的 0600，忽略


def _prompt(question: str, secret: bool = False) -> str:
    """读取一行输入。非交互 / EOF 时抛出 RuntimeError，由调用方给出友好提示。"""
    try:
        value = getpass.getpass(question) if secret else input(question)
    except (EOFError, KeyboardInterrupt):
        raise RuntimeError("non-interactive")
    return (value or "").strip()


def run_wizard(st: Settings, config_path: str) -> Settings:
    """首次运行时交互式采集账号信息。"""
    print(f"\n=== {APP} 首次配置 ===")
    print("账号密码只保存在本机 config.ini，不会上传到任何地方。\n")

    st.username = _prompt("学号（上网账号）: ")
    while not st.username:
        st.username = _prompt("学号不能为空，请重新输入: ")

    st.password = _prompt("密码（输入时不显示）: ", secret=True)
    while not st.password:
        st.password = _prompt("密码不能为空，请重新输入: ", secret=True)

    print("\n运营商 / 出口线路：")
    for key, name in ISP_CHOICES.items():
        mark = "（默认）" if key == "0" else ""
        print(f"  {key} = {name}{mark}")
    isp = _prompt("请选择 [0]: ") or "0"
    st.isp = isp if isp in ISP_CHOICES else "0"

    _write_config(config_path, st)
    print(f"\n已写入 {os.path.abspath(config_path)}\n")
    return st


def ensure_config(st: Settings, args) -> Settings:
    """若配置还是模板，则走向导（非交互环境直接报错）。"""
    if not is_placeholder(st):
        return st

    target = getattr(st, "config_path", None) or os.path.join(os.getcwd(), "config.ini")
    # 模板文件不应该被覆盖，正式配置始终写 config.ini
    if os.path.basename(target).lower().endswith(".example"):
        target = os.path.join(os.path.dirname(target), "config.ini")

    if not _interactive():
        print(
            f"错误：{target} 还没有填写账号密码。\n"
            "请复制 config.ini.example 为 config.ini 并填入学号密码，"
            "或在终端中直接运行以进入配置向导。",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        return run_wizard(st, target)
    except RuntimeError:
        print(
            f"\n错误：无法读取输入（非交互环境）。\n"
            f"请先编辑 {os.path.abspath(target)} 填入学号和密码，再运行本程序。",
            file=sys.stderr,
        )
        sys.exit(2)


def _interactive() -> bool:
    """判断当前是否处于可交互终端。

    某些 Windows 环境下 stdin 被重定向后 isatty() 仍返回 True，
    因此额外用一次 readline 探测来兜底。
    """
    try:
        if not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    try:
        import msvcrt  # type: ignore

        return True
    except ImportError:
        return True


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_status(client: DrcomClient, st: Settings, log) -> int:
    data = client.check_status(st.username)
    result = str(data.get("result"))
    if result == "1":
        uid = data.get("uid", "")
        print("状态：已在线")
        print(f"账号：{uid}")
        print(f"IP  ：{data.get('v4ip', '-')}")
        return 0
    print("状态：离线")
    msga = data.get("msga") or data.get("msg")
    if msga:
        print(f"网关提示：{msga}")
    return 1


def cmd_login(client: DrcomClient, st: Settings, log, force: bool = False) -> int:
    """强制登录一次。

    注意：网关对连续失败有锁定机制（约 10 分钟），
    所以这里在已在线时先提示，避免不必要的认证请求。
    """
    if client.is_online(st.username) and not force:
        print("当前已在线。强制登录会踢掉当前会话，且失败过多会被网关锁定。")
        if _interactive():
            try:
                ans = input("仍要继续吗？[y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans not in ("y", "yes"):
                print("已取消")
                return 0
        else:
            print("非交互环境，已跳过。若确需重登，请加 --force")
            return 0

    res = client.login(st.username, st.password, st.isp)
    if res.ok:
        print("登录成功")
        if log:
            log.info("登录成功")
        return 0
    print(f"登录失败：{res.msg}")
    if log:
        log.error("登录失败：%s", res.msg)
    return 1


def cmd_logout(client: DrcomClient, st: Settings, log) -> int:
    res = client.logout(st.username)
    print("已下线" if res.ok else f"下线失败：{res.msg}")
    return 0 if res.ok else 1


def cmd_once(client: DrcomClient, st: Settings, log) -> int:
    """检测一次，掉线就登录（适合放进计划任务 / cron）。"""
    online = client.is_online(st.username)
    if online is None:
        online = _probe_fallback(st, client)
    if online:
        print("已在线，无需登录")
        return 0
    return cmd_login(client, st, log)


def cmd_daemon(client: DrcomClient, st: Settings, log) -> int:
    wd = Watchdog(
        client=client,
        username=st.username,
        password=st.password,
        isp=st.isp,
        interval=st.interval,
        fail_threshold=st.fail_threshold,
        recheck_delay=st.recheck_delay,
        probe_urls=st.probe_urls,
        logger=log,
    )
    # 启动时先立刻补一次，避免干等一个周期
    if not wd.is_online():
        res = wd.try_login()
        print("登录成功" if res.ok else f"登录失败：{res.msg}")
        if log:
            if res.ok:
                log.info("启动登录：%s", res.msg)
            elif res.status == "rate_limited":
                log.warning("启动登录：%s", res.msg)
            else:
                log.error("启动登录：%s", res.msg)

    print(f"守护运行中（Ctrl+C 退出），检测间隔 {st.interval} 秒...")
    try:
        wd.run_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


def cmd_autostart(action: str) -> int:
    """开机自启的开启 / 关闭 / 查询。"""
    try:
        from .autostart import command_preview, disable, enable, is_enabled
    except Exception as exc:
        print(f"自启功能不可用：{exc}", file=sys.stderr)
        return 1

    if sys.platform != "win32":
        print("开机自启目前仅支持 Windows。Linux 可用 systemd/cron 实现。", file=sys.stderr)
        return 1

    if action == "on":
        ok = enable()
        print("已开启开机自启" if ok else "开启失败（权限不足？）")
        if ok:
            print(f"启动命令：{command_preview()}")
        return 0 if ok else 1

    if action == "off":
        ok = disable()
        print("已关闭开机自启" if ok else "关闭失败")
        return 0 if ok else 1

    # status
    on = is_enabled()
    print(f"开机自启：{'已开启' if on else '未开启'}")
    if on:
        print(f"启动命令：{command_preview()}")
    return 0


def _probe_fallback(st: Settings, client: DrcomClient) -> bool:  # pragma: no cover
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for url in st.probe_urls:
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": APP})
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=ctx),
            )
            with opener.open(req, timeout=client.timeout):
                return True
        except Exception:
            continue
    return False


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=APP,
        description="广西师范大学校园网（Dr.COM）自动登录与掉线重连",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-c", "--config", help="指定配置文件路径")
    p.add_argument("--once", action="store_true", help="只检测并登录一次，然后退出")
    p.add_argument("--status", action="store_true", help="只查询当前在线状态")
    p.add_argument("--login", action="store_true", help="强制发起一次登录（已在线时会先确认）")
    p.add_argument("--force", action="store_true", help="与 --login 配合，跳过在线确认直接登录")
    p.add_argument("--logout", action="store_true", help="下线")
    p.add_argument("--daemon", action="store_true", help="后台守护：持续检测并自动重连")
    p.add_argument("--tray", action="store_true", help="系统托盘常驻（无窗口，需 pywin32）")
    p.add_argument(
        "--autostart",
        nargs="?",
        const="status",
        choices=["on", "off", "status"],
        help="开机自启：on 开启 / off 关闭 / status 查询（默认）",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="安静模式，不打印控制台日志")
    p.add_argument("--interval", type=int, help="覆盖配置中的检测间隔（秒）")
    p.add_argument("-v", "--version", action="version", version=f"{APP} {__version__}")
    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    st = load(args.config)
    if args.interval:
        st.interval = max(5, args.interval)

    # --autostart 是自启管理，不需要账号密码，也不需要网络
    if args.autostart:
        return cmd_autostart(args.autostart)

    # --status 不需要账号密码
    needs_cred = not args.status
    if needs_cred:
        st = ensure_config(st, args)

    log = setup_logging(
        level=st.log_level,
        log_file=st.log_file,
        max_bytes=st.max_bytes,
        backup_count=st.backup_count,
        quiet=args.quiet,
    )

    if args.tray:
        from .tray import main as tray_main

        return tray_main()

    client = DrcomClient(host=st.host, use_https=st.https, timeout=st.timeout)

    try:
        if args.status:
            return cmd_status(client, st, log)
        if args.login:
            return cmd_login(client, st, log, force=args.force)
        if args.logout:
            return cmd_logout(client, st, log)
        if args.once:
            return cmd_once(client, st, log)
        return cmd_daemon(client, st, log)
    except KeyboardInterrupt:
        print("\n已退出")
        return 130
    except Exception as exc:
        print(f"运行出错：{exc}", file=sys.stderr)
        if log:
            log.exception("运行出错")
        return 1


if __name__ == "__main__":
    sys.exit(main())
