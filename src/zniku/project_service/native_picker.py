"""把原生路径选择限制在一次性子进程的主线程，隔离 Tcl/Windows 原生崩溃。

父进程只传递闭合提示和接收有界路径 JSON，不传 Project、token、命令或环境配置。
标准管道不落盘；取消没有文件副作用。请求线程从不创建或销毁 Tk 对象；异常只结束
自己创建的子进程，正常等待不设倒计时。此内部入口不是 HostBridge 新 capability。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast

if TYPE_CHECKING:
    from zniku.project_service.host_bridge import HostCapability, HostDialogArguments

HELPER_ARGUMENT = "--zniku-picker-helper"
MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 34 * 1024 * 1024
_PICKERS = frozenset({"open_file", "open_files", "select_directory", "save_file"})
_ENVIRONMENT_KEYS = frozenset(
    {
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "COMSPEC",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        # PyInstaller 的固定 bootloader 状态允许同包子进程复用资源；不继承任意 _PYI_*。
        "_PYI_ARCHIVE_FILE",
        "_PYI_APPLICATION_HOME_DIR",
        "_PYI_PARENT_PROCESS_LEVEL",
        "_PYI_SPLASH_IPC",
    }
)


def _picker_command() -> list[str]:
    """固定同一包的内部入口；原生提示和路径绝不拼入 argv。"""

    if getattr(sys, "frozen", False):
        return [sys.executable, HELPER_ARGUMENT]
    return [sys.executable, "-I", str(Path(__file__).resolve())]


def _running_on_windows() -> bool:
    """跨平台类型检查继续覆盖 Windows-only 管道实现，不裁剪运行时分支。"""

    return sys.platform == "win32"


def _picker_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key.upper() in _ENVIRONMENT_KEYS}


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("picker IPC 不允许重复字段")
        result[key] = value
    return result


def _decode_object(payload: bytes) -> dict[str, object]:
    value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    if not isinstance(value, dict):
        raise ValueError("picker IPC 必须是 JSON object")
    return cast(dict[str, object], value)


def _validate_response(payload: bytes, capability: HostCapability) -> tuple[str, ...] | None:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("picker IPC 响应超过大小限制")
    result = _decode_object(payload)
    if set(result) != {"version", "paths"} or type(result["version"]) is not int:
        raise ValueError("picker IPC 响应字段无效")
    values = result["paths"]
    if result["version"] != 1 or not isinstance(values, list) or len(values) > 256:
        raise ValueError("picker IPC 响应版本或路径数量无效")
    if capability != "open_files" and len(values) > 1:
        raise ValueError("picker IPC 单选不能返回多个路径")
    if any(
        not isinstance(value, str) or not 1 <= len(value) <= 32767 or "\x00" in value
        for value in values
    ):
        raise ValueError("picker IPC 路径文本无效")
    # 文件存在性、路径种类、重复结果仍由同一个 HostBridge authority 校验。
    return tuple(values) or None


def run_picker_process(
    capability: HostCapability, arguments: HostDialogArguments
) -> Sequence[str] | None:
    """等待自己创建的 helper；原生崩溃或非法 IPC 不会终止工程服务。"""

    if capability not in _PICKERS:
        raise ValueError("非 picker capability 不能启动原生 helper")
    request = json.dumps(
        {"version": 1, "capability": capability, "args": arguments.model_dump(mode="json")},
        ensure_ascii=False,
    ).encode("utf-8")
    if len(request) > MAX_REQUEST_BYTES:
        raise ValueError("picker IPC 请求超过大小限制")
    process = subprocess.Popen(
        _picker_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
        env=_picker_environment(),
        creationflags=0x08000000 if _running_on_windows() else 0,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(request)
        process.stdin.close()
        # 不用 communicate 积累任意大小的输出。超过上限立即退出并收回这个 helper。
        response = process.stdout.read(MAX_RESPONSE_BYTES + 1)
        if len(response) > MAX_RESPONSE_BYTES:
            raise ValueError("picker IPC 响应超过大小限制")
        if process.wait() != 0:
            raise RuntimeError("原生路径选择窗口意外退出；工程服务仍可用，请重新选择")
        return _validate_response(response, capability)
    finally:
        # 包括 BrokenPipe、响应校验失败和调用中断；绝不按名称或全局 PID 枚举杀进程。
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def choose_paths_native(
    capability: HostCapability, arguments: HostDialogArguments
) -> Sequence[str] | None:
    """仅 helper 主线程创建临时置顶 owner；真实 Windows Z-order 另由打包验收证明。"""

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("原生选择器只能在 helper 主线程运行")
    if capability not in _PICKERS:
        raise ValueError("非 picker capability 进入原生选择器")
    import tkinter as tk
    from tkinter import filedialog

    filetypes: list[tuple[str, str]] = [("所有文件", "*.*")]
    if arguments.extensions:
        filetypes = [("允许的文件", " ".join(f"*{item}" for item in arguments.extensions))]
    root = tk.Tk()
    try:
        root.withdraw()
        root.title("ZNIKU Studio")
        root.geometry(
            f"1x1+{max(0, root.winfo_screenwidth() // 2)}+{max(0, root.winfo_screenheight() // 2)}"
        )
        root.attributes("-alpha", 0.0)
        root.attributes("-toolwindow", True)
        root.attributes("-topmost", True)
        root.deiconify()
        root.update_idletasks()
        root.lift()
        # Windows 可拒绝一次前台请求；不循环抢焦点，不修改系统策略。
        with suppress(tk.TclError):
            root.focus_force()
        if capability == "open_file":
            value = filedialog.askopenfilename(
                parent=root, title=arguments.title, filetypes=filetypes
            )
            return (value,) if value else None
        if capability == "open_files":
            return (
                tuple(
                    filedialog.askopenfilenames(
                        parent=root, title=arguments.title, filetypes=filetypes
                    )
                )
                or None
            )
        if capability == "select_directory":
            value = filedialog.askdirectory(parent=root, title=arguments.title, mustexist=True)
            return (value,) if value else None
        value = filedialog.asksaveasfilename(
            parent=root,
            title=arguments.title,
            initialfile=arguments.suggested_name,
            filetypes=filetypes,
        )
        return (value,) if value else None
    finally:
        try:
            root.attributes("-topmost", False)
        finally:
            root.destroy()


def _windows_pipe_stream(*, reading: bool) -> BinaryIO:
    """windowed 包没有 sys.stdin/out，但 Popen 仍显式传入标准管道句柄。

    open_osfhandle 将本 helper 继承的句柄所有权交给文件对象；父进程端点属于另一进程，
    不受这里关闭影响。非法或缺失句柄失败关闭，不创建控制台或退回文件 IPC。
    """

    import msvcrt

    kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    handle = kernel.GetStdHandle(-10 if reading else -11)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise OSError("原生 helper 缺少标准管道")
    flags = (os.O_RDONLY if reading else os.O_WRONLY) | cast(int, cast(Any, os).O_BINARY)
    descriptor = cast(int, cast(Any, msvcrt).open_osfhandle(handle, flags))
    return os.fdopen(descriptor, "rb" if reading else "wb", closefd=True)


def _pipe_stream(*, reading: bool) -> BinaryIO:
    stream = sys.stdin if reading else sys.stdout
    if stream is not None:
        return stream.buffer
    if not _running_on_windows():
        raise OSError("原生 helper 缺少标准管道")
    return _windows_pipe_stream(reading=reading)


def main() -> int:
    """内部一次性进程入口；非法请求或 Tk 异常仅以非零退出，不弹出第二个错误窗口。"""

    try:
        if not getattr(sys, "frozen", False):
            # -I 禁止 PYTHONPATH/用户 site 注入；仅加入与当前固定脚本配套的包根目录。
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from zniku.project_service.host_bridge import HostDialogArguments

        with _pipe_stream(reading=True) as input_stream:
            payload = input_stream.read(MAX_REQUEST_BYTES + 1)
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("picker IPC 请求超过大小限制")
        request = _decode_object(payload)
        if (
            set(request) != {"version", "capability", "args"}
            or type(request["version"]) is not int
            or request["version"] != 1
            or not isinstance(request["capability"], str)
            or request["capability"] not in _PICKERS
        ):
            raise ValueError("picker IPC 请求字段无效")
        capability = cast("HostCapability", request["capability"])
        arguments = HostDialogArguments.model_validate(request["args"], strict=True)
        if capability == "select_directory" and (
            arguments.extensions is not None or arguments.suggested_name is not None
        ):
            raise ValueError("目录选择器仅接受 title")
        if capability in {"open_file", "open_files"} and arguments.suggested_name is not None:
            raise ValueError("打开选择器不接受 suggested_name")
        selected = choose_paths_native(capability, arguments)
        response = json.dumps(
            {"version": 1, "paths": list(selected or ())}, ensure_ascii=False
        ).encode("utf-8")
        _validate_response(response, capability)
        with _pipe_stream(reading=False) as output_stream:
            output_stream.write(response)
        return 0
    except Exception:
        # 不输出请求、路径、环境或异常堆栈；父进程负责统一可恢复的错误说明。
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
