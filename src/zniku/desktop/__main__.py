"""Windows 双击入口：启动同一 production Studio，健康后打开浏览器并保持本实例服务。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from zniku.project_service import native_picker

from .instance import InstanceLock, InstanceRecord, check_health
from .server import DesktopServer, build_desktop_application
from .windows import DesktopWindowsPlatform, install_process_job, is_windows, open_studio_browser


def application_data_root() -> Path:
    """候选采用独立通道，避免二次启动发现并打开操作者仍在验收的v0.3.1服务。

    这是0.3.3候选隔离位置，不迁移旧偏好/最近工程/产物；正式升级策略另行验收。
    """

    base = os.environ.get("LOCALAPPDATA")
    if not base or not Path(base).is_absolute():
        raise RuntimeError("找不到当前用户的 Windows 本机应用数据目录")
    return Path(base) / "ZNIKU" / "Studio-v0.3.3-candidate"


def bundled_assets() -> Path:
    """打包只读资源与源码开发共用 production build，不启动 npm 或 Vite。"""

    bundle = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle, str):
        return Path(bundle) / "studio"
    return Path(__file__).resolve().parents[3] / "apps" / "studio" / "dist"


def configure_bundled_media_tools() -> None:
    """只把当前包内固定工具目录加到本进程 PATH，绝不改系统设置。"""

    bundle = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle, str):
        tools_root = Path(bundle) / "media-tools"
        if any(not (tools_root / name).is_file() for name in ("ffmpeg.exe", "ffprobe.exe")):
            raise RuntimeError("桌面包缺少 FFmpeg/FFprobe，请重新完整解压本地候选包")
        os.environ["PATH"] = str(tools_root) + os.pathsep + os.environ.get("PATH", "")


def main() -> int:
    """二次启动只打开现有实例；取消原生 picker 不会影响此处生命周期。"""

    # 固定私有标记只走一次性管道协议，先于单实例、浏览器、媒体工具与服务初始化。
    # 非法请求直接返回非零，不进入桌面启动错误窗口，也不继承 Project 或 host token。
    if sys.argv[1:] == [native_picker.HELPER_ARGUMENT]:
        return native_picker.main()
    lock: InstanceLock | None = None
    server: DesktopServer | None = None
    try:
        if not is_windows():
            raise RuntimeError("此桌面候选仅支持 Windows")
        if len(sys.argv) != 1:
            raise RuntimeError("桌面入口不接受命令、URL 或路径参数；请在界面选择工程")
        root = application_data_root()
        lock = InstanceLock(root)
        if not lock.acquire():
            record = lock.existing()
            open_studio_browser(record.origin)
            return 0
        configure_bundled_media_tools()
        # 只有新实例绑定 job。系统浏览器和播放器明确脱离该 job，不能被应用退出误杀。
        install_process_job()
        server = DesktopServer(
            build_desktop_application(root / "attempts"),
            bundled_assets(),
            platform=DesktopWindowsPlatform(),
            data_root=root,
        )
        server.start()
        record = InstanceRecord(instance_id=server.instance_id, origin=server.origin)
        if not check_health(record):
            raise RuntimeError("Studio 服务未通过启动健康检查；没有打开浏览器")
        lock.publish(record)
        open_studio_browser(record.origin)
        server.wait()
        return 0
    except Exception as error:
        # 不把请求 token 或完整环境输出到日志；双击用户直接得到明确本机错误窗口。
        import tkinter as tk
        from tkinter import messagebox

        window = tk.Tk()
        window.withdraw()
        try:
            messagebox.showerror("ZNIKU Studio 无法启动", str(error), parent=window)
        finally:
            window.destroy()
        return 1
    finally:
        if server is not None:
            server.close()
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
