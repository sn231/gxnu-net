"""托盘模式启动脚本（供 pythonw.exe 使用，无控制台窗口）。

用法：
    pythonw.exe tray.pyw

为什么不直接 `pythonw -m gxnu_net.tray`：
pythonw 启动时的工作目录不一定是项目目录，导致找不到 gxnu_net 包。
本脚本先把自身所在目录加入 sys.path，再启动托盘，路径问题一次性解决。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.chdir(HERE)  # 保证能读到同目录的 config.ini

from gxnu_net.tray import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
