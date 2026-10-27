"""gxnu-net 打包入口（PyInstaller 用）。

打包为单文件窗口程序：
    pyinstaller gxnu_net.spec

运行时无控制台窗口，右下角托盘常驻。
首次运行若没有 config.ini 会弹出图形化配置窗口。
"""

import os
import sys

# PyInstaller 打包后，工作目录可能不是 exe 所在目录
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))
else:
    # 源码模式：保证能 import gxnu_net
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    os.chdir(here)

from gxnu_net.tray import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
