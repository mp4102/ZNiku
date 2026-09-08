"""提供 Windows 双击入口与 production HTTP 生命周期，不定义 Graph 或 Runtime 语义。"""

from .server import DesktopServer, build_desktop_application

__all__ = ["DesktopServer", "build_desktop_application"]
