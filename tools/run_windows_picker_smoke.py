"""显式 opt-in 的 Windows 原生选择器自检，不是通用桌面控制工具。

每种固定 picker 在独立子进程中调用正式 WindowsHostPlatform。观察器只枚举该子进程创建
picker 的线程窗口，核对自身 PID、固定标题和 owner 后读取 TOPMOST，再用 WM_CLOSE 取消
本次测试对话框；不枚举其他进程，不发送输入，不选择文件，也不改变系统焦点策略。
父进程给每个子进程硬超时，失败即停止后续弹窗。普通调用和 pytest 默认绝不创建窗口。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Any, cast

from zniku.project_service.host_bridge import (
    HostCapability,
    HostDialogArguments,
    WindowsHostPlatform,
)

PICKERS: tuple[HostCapability, ...] = (
    "open_file",
    "open_files",
    "select_directory",
    "save_file",
)
CHILD_TIMEOUT_SECONDS = 15
_OBSERVE_SECONDS = 8.0
_TITLE_PREFIX = "ZNIKU 原生选择器自检 / "
_TOPMOST = 0x00000008
_EXSTYLE = -20
_OWNER = 4
_WM_CLOSE = 0x0010


def _is_windows() -> bool:
    """运行时判定平台，使跨平台类型门禁仍检查完整原生实现分支。"""
    return sys.platform == "win32"


@dataclass(frozen=True, slots=True)
class PickerResult:
    """只输出自检布尔结论，不输出用户路径、窗口清单或可复用的窗口句柄。"""

    capability: str
    dialog_observed: bool = False
    own_process_verified: bool = False
    owner_topmost: bool = False
    dialog_topmost: bool = False
    cancelled: bool = False
    lock_released: bool = False
    owner_destroyed: bool = False
    error: str | None = None

    @property
    def passed(self) -> bool:
        """不能把看见任意窗口或仅 owner 置顶当作通过。"""
        return self.error is None and all(
            (
                self.dialog_observed,
                self.own_process_verified,
                self.owner_topmost,
                self.dialog_topmost,
                self.cancelled,
                self.lock_released,
                self.owner_destroyed,
            )
        )


def parse_result(value: object, capability: HostCapability) -> PickerResult:
    """严格解析自身子进程结果；错误类型、未知字段和串 capability 均失败关闭。"""
    names = set(PickerResult.__dataclass_fields__)
    if not isinstance(value, dict) or set(value) != names or value.get("capability") != capability:
        raise ValueError("原生自检子进程返回了无效身份或字段")
    booleans = names - {"capability", "error"}
    if any(type(value[name]) is not bool for name in booleans):
        raise ValueError("原生自检结果必须使用严格 boolean")
    error = value["error"]
    if error is not None and (not isinstance(error, str) or len(error) > 500):
        raise ValueError("原生自检错误字段无效")
    return PickerResult(**value)


class _OwnThreadObserver:
    """仅持有本进程当前线程的观察能力，不接受外部 PID、HWND、标题或窗口动作。"""

    def __init__(self, capability: HostCapability) -> None:
        if capability not in PICKERS or not _is_windows():
            raise ValueError("只允许 Windows 固定 picker 自检")
        # ctypes 动态 FFI 集中在此边界；跨 Windows/Linux 类型检查不假定宿主已提供 WinDLL。
        ffi = cast(Any, ctypes)
        self._user: Any = ffi.WinDLL("user32", use_last_error=True)
        kernel: Any = ffi.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThreadId.argtypes = []
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        self._thread_id = int(kernel.GetCurrentThreadId())
        self._pid = os.getpid()
        self._title = _TITLE_PREFIX + capability
        self._callback_type: Any = ffi.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        self._user.EnumThreadWindows.argtypes = [
            wintypes.DWORD,
            self._callback_type,
            wintypes.LPARAM,
        ]
        self._user.EnumThreadWindows.restype = wintypes.BOOL
        self._user.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user.IsWindowVisible.argtypes = [wintypes.HWND]
        self._user.IsWindowVisible.restype = wintypes.BOOL
        self._user.IsWindow.argtypes = [wintypes.HWND]
        self._user.IsWindow.restype = wintypes.BOOL
        self._user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._user.GetClassNameW.restype = ctypes.c_int
        self._user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._user.GetWindowTextW.restype = ctypes.c_int
        self._user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        self._user.GetWindow.restype = wintypes.HWND
        self._style: Any = (
            self._user.GetWindowLongPtrW
            if ctypes.sizeof(ctypes.c_void_p) == 8
            else self._user.GetWindowLongW
        )
        self._style.argtypes = [wintypes.HWND, ctypes.c_int]
        self._style.restype = ctypes.c_ssize_t
        self._user.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user.PostMessageW.restype = wintypes.BOOL
        self.dialog_observed = False
        self.own_process_verified = False
        self.owner_topmost = False
        self.dialog_topmost = False
        self._observed_owner = 0
        self.error: str | None = None

    def owner_destroyed(self) -> bool:
        """只查询本次已核对的临时 owner 是否销毁，不枚举退出后的桌面窗口。"""
        return self._observed_owner != 0 and not bool(self._user.IsWindow(self._observed_owner))

    def _owned(self, window: int) -> bool:
        process = wintypes.DWORD()
        thread = int(self._user.GetWindowThreadProcessId(window, ctypes.byref(process)))
        return process.value == self._pid and thread == self._thread_id

    def observe_and_cancel(self, finished: threading.Event) -> None:
        """只向再次核对过身份的本次对话框发送关闭；无法确认身份时等待父进程终止自身。"""
        deadline = monotonic() + _OBSERVE_SECONDS
        matched: list[int] = []

        def inspect(window: int, _unused: int) -> bool:
            # 身份检查必须先于标题读取，且不查看同一桌面的其他进程窗口。
            if matched or not self._owned(window) or not self._user.IsWindowVisible(window):
                return True
            kind, title = ctypes.create_unicode_buffer(128), ctypes.create_unicode_buffer(256)
            self._user.GetClassNameW(window, kind, len(kind))
            if kind.value != "#32770":
                return True
            self._user.GetWindowTextW(window, title, len(title))
            if title.value != self._title:
                return True
            owner = int(self._user.GetWindow(window, _OWNER) or 0)
            if not owner or not self._owned(owner):
                return True
            self.dialog_observed = True
            self.own_process_verified = True
            self.owner_topmost = bool(int(self._style(owner, _EXSTYLE)) & _TOPMOST)
            self.dialog_topmost = bool(int(self._style(window, _EXSTYLE)) & _TOPMOST)
            self._observed_owner = owner
            matched.append(window)
            return True

        callback = self._callback_type(inspect)
        try:
            while monotonic() < deadline and not finished.is_set():
                self._user.EnumThreadWindows(self._thread_id, callback, 0)
                if matched:
                    window = matched[0]
                    # 不设置前台/焦点/样式，不点选文件；只请求该自有对话框正常取消。
                    if not self._owned(window) or not self._user.PostMessageW(
                        window, _WM_CLOSE, 0, 0
                    ):
                        self.error = "无法正常取消自身原生对话框"
                    return
                finished.wait(0.025)
            if not finished.is_set():
                self.error = "在限定时间内未发现本线程、固定标题及自有 owner 的原生对话框"
        except Exception as error:
            self.error = f"自身原生窗口观察失败：{type(error).__name__}: {error}"[:500]


def run_native_child(capability: HostCapability) -> PickerResult:
    """一个子进程只创建一个正式 picker；返回后核对取消语义及同一 platform 的锁释放。"""
    observer = _OwnThreadObserver(capability)
    platform = WindowsHostPlatform()
    finished = threading.Event()
    watcher = threading.Thread(target=observer.observe_and_cancel, args=(finished,), daemon=True)
    watcher.start()
    cancelled = False
    error: str | None = None
    try:
        selected = platform.choose_paths(
            capability,
            HostDialogArguments(title=_TITLE_PREFIX + capability),
        )
        cancelled = not selected
        if selected:
            error = "自检不允许选择任何文件；没有记录或使用选中路径"
    except Exception as failure:
        error = f"正式 picker 调用失败：{type(failure).__name__}: {failure}"[:500]
    finally:
        finished.set()
        watcher.join(1)
    released = platform._dialog_lock.acquire(blocking=False)
    if released:
        platform._dialog_lock.release()
    return PickerResult(
        capability,
        observer.dialog_observed,
        observer.own_process_verified,
        observer.owner_topmost,
        observer.dialog_topmost,
        cancelled,
        released,
        observer.owner_destroyed(),
        error or observer.error,
    )


def run_matrix() -> tuple[PickerResult, ...]:
    """严格串行、每项硬超时；只启动和终止自己创建的 Python 测试子进程。"""
    rows = []
    for capability in PICKERS:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--native-windows",
                    "--picker-child",
                    capability,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=CHILD_TIMEOUT_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "PYTHONUTF8": "1"},
            )
            row = parse_result(json.loads(result.stdout), capability)
            if result.returncode != 0 and row.passed:
                row = PickerResult(capability, error="子进程异常退出，不能接受成功声明")
        except subprocess.TimeoutExpired:
            # subprocess.run 已终止并 wait 自身子进程，不能按全局窗口或进程名做清理。
            row = PickerResult(capability, error="原生子进程超过15秒，已终止该测试子进程")
        except (OSError, ValueError, TypeError) as error:
            row = PickerResult(capability, error=f"子进程结果不可用：{error}"[:500])
        rows.append(row)
        if not row.passed:
            break
    return tuple(rows)


def main(argv: Sequence[str] | None = None) -> int:
    """必须显式确认原生 Windows 自检，默认与普通 CI 运行均不会弹出任何窗口。"""
    parser = argparse.ArgumentParser(description="仅测试本进程4种原生选择器置顶并自动取消")
    parser.add_argument("--native-windows", action="store_true", help="允许创建并自动取消测试弹窗")
    parser.add_argument("--picker-child", choices=PICKERS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.native_windows:
        parser.error("需显式 --native-windows 才能创建测试弹窗")
    if not _is_windows():
        parser.error("原生选择器自检只支持 Windows")
    if args.picker_child:
        row = run_native_child(args.picker_child)
        print(json.dumps(asdict(row), ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if row.passed else 1
    rows = run_matrix()
    passed = len(rows) == len(PICKERS) and all(row.passed for row in rows)
    print(
        json.dumps(
            {
                "kind": "native_windows_picker_self_test",
                "status": "passed" if passed else "failed",
                "results": [asdict(row) for row in rows],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
