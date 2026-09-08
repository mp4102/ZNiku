"""提供受控后台进程的纯显示标志，不改变 argv、shell、归属、退出码或验证语义。"""

import sys


def background_creation_flags() -> int:
    """Windows 隐藏工具控制台；其他平台传 0，保留原有进程执行行为。"""

    return 0x08000000 if sys.platform == "win32" else 0
