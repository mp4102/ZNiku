"""封装 Windows 本机进程归属与固定系统启动，不枚举或结束其他进程。

当前 Studio 与自动媒体子进程加入独立 kill-on-close JobObject。浏览器、Explorer 和用户
播放器以明确 breakaway 启动，不属于可被 Studio 结束的处理进程。Job 设置失败直接拒绝启动。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from typing import Any, cast

from zniku.project_service.host_bridge import HostLaunchCommand, WindowsHostPlatform


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def is_windows() -> bool:
    return sys.platform == "win32"


def _kernel32() -> Any:
    if not is_windows():
        raise OSError("ZNIKU Studio 桌面入口只支持 Windows")
    # getattr 保持 Windows/Linux 类型检查都检查完整分支，而不假定 Linux 存在 WinDLL。
    return cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)


def install_process_job() -> int:
    """绑定当前进程；返回故意持有到 OS 退出的句柄，绝不按 PID 清理其他实例。"""

    kernel = _kernel32()
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise OSError("不能建立本实例进程 JobObject，拒绝启动")
    information = _ExtendedLimit()
    information.BasicLimitInformation.LimitFlags = 0x2000 | 0x0800
    if not kernel.SetInformationJobObject(
        handle, 9, ctypes.byref(information), ctypes.sizeof(information)
    ):
        kernel.CloseHandle(handle)
        raise OSError("不能设置本实例进程退出清理，拒绝启动")
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        kernel.CloseHandle(handle)
        raise OSError("宿主进程限制阻止本实例进程归属，拒绝启动")
    # 不调用 CloseHandle：本进程本身也属于 job；OS 在进程终止时关闭最后句柄并结束自动子进程。
    return int(handle)


def launch_external(command: HostLaunchCommand) -> None:
    """系统动作在本实例 job 外运行；固定 argv，失败不回退任意 shell。"""

    if not is_windows():
        raise OSError("系统启动仅支持 Windows")
    subprocess.Popen(
        [command.executable, *command.argv],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
        creationflags=0x01000000 | 0x08000000,
    )


class DesktopWindowsPlatform(WindowsHostPlatform):
    """继续使用已冻结原生 picker，只为系统启动增加明确进程归属。"""

    def launch(self, command: HostLaunchCommand) -> None:
        launch_external(command)


def open_studio_browser(origin: str) -> None:
    """只打开 launcher 自己的确切 loopback URL，不接受媒体或页面提交的 URL。"""

    from zniku.project_service.host_bridge import validate_studio_origin

    validate_studio_origin(origin)
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    launch_external(
        HostLaunchCommand(
            executable=os.path.join(system_root, "System32", "rundll32.exe"),
            argv=("url.dll,FileProtocolHandler", origin + "/"),
        )
    )
