# gxnu-net

广西师范大学校园网（Dr.COM）自动认证与掉线重连工具。使用 Python 实现，支持 Windows 系统托盘、首次运行配置窗口、命令行和开机自启。

## 使用方法

需要 Python 3.10 或更新版本。在 Windows 下执行：

```powershell
python -m pip install -r requirements.txt
python -m gxnu_net --tray
```

也可以双击 `start.bat`。首次运行时填写自己的校园网账号、密码和运营商，程序会将配置保存到本机 `config.ini`。配置模板见 `config.ini.example`。

```powershell
# 查看状态
python -m gxnu_net --status
# 检测并在离线时登录一次
python -m gxnu_net --once
# 手动登录或注销
python -m gxnu_net --login
python -m gxnu_net --logout
# 开启或关闭开机自启
python -m gxnu_net --autostart on
python -m gxnu_net --autostart off
```

托盘提供查看状态、立即重连、打开日志和退出等操作。开机自启使用当前用户的 Windows 注册表 Run 项。

## 项目思路

程序周期查询校园网门户状态，连续确认离线后才使用用户配置的账号重新认证。网关无响应时，使用外网探测作为备用依据。登录限流和错误冷却减少重复认证；后台线程处理请求，托盘负责展示状态和接收操作。

默认每 30 秒检测一次，首次离线后 10 秒复查，连续 2 次离线触发重连。两次登录至少间隔 30 秒，5 分钟内最多尝试 3 次。密码错误和账号锁定会进一步触发冷却。

模块职责、接口参数、流程图和实现边界见 [项目逻辑与设计](docs/ARCHITECTURE.md)。

## 隐私与安全

- 本仓库仅包含源码、虚构测试数据和配置模板。真实账号配置、日志、原始压缩包和打包程序不入库。
- `config.ini` 明文保存账号密码，请勿提交、分享或截图公开。
- 认证时账号密码会发送给校园网门户，登录接口使用 GET 参数。当前实现关闭 HTTPS 证书及主机名校验，存在中间人攻击风险。
- 门户响应和异常信息可能含账号、IP 或请求信息，分享日志前需要脱敏。
- 发布前的检查范围及处理见 [发布检查](docs/PUBLICATION_REVIEW.md)。

## 测试与打包

```powershell
python -m pip install -r requirements-dev.txt
python tests/test_smoke.py
python -m pytest tests -q
python -m PyInstaller gxnu_net.spec
```

测试使用虚构账号和密码，不进行真实校园网认证。打包产物位于 `dist/`。发布时只复制程序、配置模板和使用说明，不要复制本机 `config.ini` 或日志。

## 目录结构

| 文件 | 用途 |
| --- | --- |
| `gxnu_net/drcom.py` | Dr.COM 请求、JSONP 解析与错误解释 |
| `gxnu_net/watchdog.py` | 掉线确认、自动重连与状态依据 |
| `gxnu_net/ratelimit.py` | 滑动窗口限流与冷却 |
| `gxnu_net/tray.py` | Windows 托盘与后台检测线程 |
| `gxnu_net/setup_gui.py` | 首次运行配置窗口 |
| `gxnu_net/config.py` | 配置加载、模板识别与路径选择 |
| `gxnu_net/autostart.py` | 当前用户开机自启 |
| `gxnu_net/logging_setup.py` | 控制台与轮转日志 |
| `gxnu_net/cli.py` | 命令行入口与各模式调度 |

门户协议或校园网策略变化后，可能需要调整实现。

