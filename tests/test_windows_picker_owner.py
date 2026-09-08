"""用纯 fake Tk 验证四种原生选择器的临时置顶 owner，不创建真实窗口。

这里证明统一入口的调用顺序、参数及清理边界；Chrome 前台时的真实 Z-order 仍属于
Windows 桌面验收，不能用这些替身测试宣称已经获得操作系统的前台焦点。
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from types import ModuleType

import pytest

from zniku.project_service.host_bridge import (
    HostBridgeFailure,
    HostCapability,
    HostDialogArguments,
    WindowsHostPlatform,
)

_PICKERS: dict[HostCapability, str] = {
    "open_file": "askopenfilename",
    "open_files": "askopenfilenames",
    "select_directory": "askdirectory",
    "save_file": "asksaveasfilename",
}


class FakeTclError(Exception):
    """区分允许降级的一次性 focus 失败与其他宿主错误。"""


class FakeRoot:
    """记录 owner 的生命周期，并在指定步骤模拟 Tcl 或系统异常。"""

    def __init__(self, failures: dict[str, Exception]) -> None:
        self.failures = failures
        self.events: list[tuple[str, tuple[object, ...]]] = []

    def _record(self, name: str, *arguments: object) -> None:
        self.events.append((name, arguments))
        failure_key = name
        if name == "attributes":
            failure_key = f"attributes:{arguments[0]}:{arguments[1]}"
        if failure_key in self.failures:
            raise self.failures[failure_key]

    def withdraw(self) -> None:
        self._record("withdraw")

    def title(self, value: str) -> None:
        self._record("title", value)

    def winfo_screenwidth(self) -> int:
        self._record("winfo_screenwidth")
        return 1920

    def winfo_screenheight(self) -> int:
        self._record("winfo_screenheight")
        return 1080

    def geometry(self, value: str) -> None:
        self._record("geometry", value)

    def attributes(self, name: str, value: object) -> None:
        self._record("attributes", name, value)

    def deiconify(self) -> None:
        self._record("deiconify")

    def update_idletasks(self) -> None:
        self._record("update_idletasks")

    def lift(self) -> None:
        self._record("lift")

    def focus_force(self) -> None:
        self._record("focus_force")

    def destroy(self) -> None:
        self._record("destroy")


class PickerHarness:
    """按 capability 注入原生返回值，且不导入真实 tkinter。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.failures: dict[str, Exception] = {}
        self.roots: list[FakeRoot] = []
        self.calls: list[tuple[HostCapability, dict[str, object]]] = []
        self.result: object = r"C:\synthetic\结果.mov"
        self.factory_failure: Exception | None = None
        self.during_picker: Callable[[], None] | None = None
        tk = ModuleType("tkinter")
        dialogs = ModuleType("tkinter.filedialog")
        monkeypatch.setattr(tk, "Tk", self.make_root, raising=False)
        monkeypatch.setattr(tk, "TclError", FakeTclError, raising=False)
        monkeypatch.setattr(tk, "filedialog", dialogs, raising=False)
        for capability, name in _PICKERS.items():
            monkeypatch.setattr(dialogs, name, self._picker(capability), raising=False)
        monkeypatch.setitem(sys.modules, "tkinter", tk)
        monkeypatch.setitem(sys.modules, "tkinter.filedialog", dialogs)

    def make_root(self) -> FakeRoot:
        if self.factory_failure is not None:
            raise self.factory_failure
        root = FakeRoot(self.failures)
        self.roots.append(root)
        return root

    def _picker(self, capability: HostCapability) -> Callable[..., object]:
        def choose(**options: object) -> object:
            self.calls.append((capability, options))
            parent = options.get("parent")
            assert parent is self.roots[-1]
            parent._record("picker", capability)
            if self.during_picker is not None:
                self.during_picker()
            return self.result

        return choose


def _assert_mutex_available(platform: WindowsHostPlatform) -> None:
    assert platform._dialog_lock.acquire(blocking=False)
    platform._dialog_lock.release()


def _assert_cleanup(root: FakeRoot) -> None:
    assert root.events[-2:] == [
        ("attributes", ("-topmost", False)),
        ("destroy", ()),
    ]
    assert sum(name == "destroy" for name, _arguments in root.events) == 1


@pytest.mark.parametrize("capability", tuple(_PICKERS))
@pytest.mark.parametrize("cancelled", [False, True])
def test_all_pickers_use_one_prepared_owner_and_keep_dialog_parameters(
    monkeypatch: pytest.MonkeyPatch, capability: HostCapability, cancelled: bool
) -> None:
    harness = PickerHarness(monkeypatch)
    if cancelled:
        harness.result = () if capability == "open_files" else ""
    elif capability == "open_files":
        harness.result = (r"C:\synthetic\甲.mov", r"C:\synthetic\乙.mov")
    platform = WindowsHostPlatform()
    arguments = HostDialogArguments(
        title="为当前外部任务选择文件",
        extensions=None if capability == "select_directory" else (".mov", ".mkv"),
        suggested_name="结果.mov" if capability == "save_file" else None,
    )
    result = platform.choose_paths(capability, arguments)
    expected = (
        None if cancelled else (harness.result if capability == "open_files" else (harness.result,))
    )
    assert result == expected
    assert len(harness.roots) == 1
    root = harness.roots[0]
    expected_options: dict[str, object] = {"parent": root, "title": arguments.title}
    if capability == "select_directory":
        expected_options["mustexist"] = True
    else:
        expected_options["filetypes"] = [("允许的文件", "*.mov *.mkv")]
    if capability == "save_file":
        expected_options["initialfile"] = "结果.mov"
    assert harness.calls == [(capability, expected_options)]
    # 不依赖 screen 查询相对于 title 的细节，但必须先映射并准备 owner，再显示选择器。
    events = [event for event in root.events if not event[0].startswith("winfo_")]
    geometry = next(arguments[0] for name, arguments in events if name == "geometry")
    assert isinstance(geometry, str)
    match = re.fullmatch(r"1x1\+(\d+)\+(\d+)", geometry)
    assert match is not None
    assert int(match[1]) in (959, 960) and int(match[2]) in (539, 540)
    assert events == [
        ("withdraw", ()),
        ("title", ("ZNIKU Studio",)),
        ("geometry", (geometry,)),
        ("attributes", ("-alpha", 0.0)),
        ("attributes", ("-toolwindow", True)),
        ("attributes", ("-topmost", True)),
        ("deiconify", ()),
        ("update_idletasks", ()),
        ("lift", ()),
        ("focus_force", ()),
        ("picker", (capability,)),
        ("attributes", ("-topmost", False)),
        ("destroy", ()),
    ]
    _assert_cleanup(root)
    _assert_mutex_available(platform)


def test_default_file_filter_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = PickerHarness(monkeypatch)
    WindowsHostPlatform().choose_paths("open_file", HostDialogArguments())
    assert harness.calls[0][1]["filetypes"] == [("所有文件", "*.*")]
    assert harness.calls[0][1]["title"] is None


def test_focus_denied_once_still_shows_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = PickerHarness(monkeypatch)
    harness.failures["focus_force"] = FakeTclError("合成前台请求被拒绝")
    platform = WindowsHostPlatform()
    assert platform.choose_paths("open_file", HostDialogArguments()) == (harness.result,)
    assert sum(name == "focus_force" for name, _arguments in harness.roots[0].events) == 1
    assert len(harness.calls) == 1
    _assert_cleanup(harness.roots[0])
    _assert_mutex_available(platform)


@pytest.mark.parametrize(
    "step",
    [
        "withdraw",
        "title",
        "winfo_screenwidth",
        "winfo_screenheight",
        "geometry",
        "attributes:-alpha:0.0",
        "attributes:-toolwindow:True",
        "attributes:-topmost:True",
        "deiconify",
        "update_idletasks",
        "lift",
    ],
)
def test_owner_preparation_failure_never_opens_picker_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    harness = PickerHarness(monkeypatch)
    failure = FakeTclError(f"synthetic failure at {step}")
    harness.failures[step] = failure
    platform = WindowsHostPlatform()
    with pytest.raises(FakeTclError) as observed:
        platform.choose_paths("open_file", HostDialogArguments())
    assert observed.value is failure
    assert harness.calls == []
    _assert_cleanup(harness.roots[0])
    _assert_mutex_available(platform)


def test_non_tcl_focus_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = PickerHarness(monkeypatch)
    harness.failures["focus_force"] = RuntimeError("synthetic programming error")
    platform = WindowsHostPlatform()
    with pytest.raises(RuntimeError, match="synthetic programming error"):
        platform.choose_paths("open_file", HostDialogArguments())
    assert harness.calls == []
    _assert_cleanup(harness.roots[0])
    _assert_mutex_available(platform)


@pytest.mark.parametrize("step", ["picker", "attributes:-topmost:False", "destroy"])
def test_picker_and_cleanup_errors_always_attempt_destroy_and_release_mutex(
    monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    harness = PickerHarness(monkeypatch)
    harness.failures[step] = FakeTclError(f"synthetic failure at {step}")
    platform = WindowsHostPlatform()
    with pytest.raises(FakeTclError, match="synthetic failure"):
        platform.choose_paths("open_file", HostDialogArguments())
    assert len(harness.calls) == 1
    _assert_cleanup(harness.roots[0])
    _assert_mutex_available(platform)
    # 清理异常不把整个桌面会话永久锁住，下一次显式请求仍可创建新的 owner。
    harness.failures.clear()
    assert platform.choose_paths("open_file", HostDialogArguments()) == (harness.result,)
    assert len(harness.roots) == 2


def test_tk_construction_error_releases_mutex(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = PickerHarness(monkeypatch)
    harness.factory_failure = FakeTclError("synthetic Tk unavailable")
    platform = WindowsHostPlatform()
    with pytest.raises(FakeTclError, match="synthetic Tk unavailable"):
        platform.choose_paths("open_file", HostDialogArguments())
    assert harness.roots == [] and harness.calls == []
    _assert_mutex_available(platform)


def test_reentrant_picker_request_fails_busy_without_second_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = PickerHarness(monkeypatch)
    platform = WindowsHostPlatform()

    def concurrent_click() -> None:
        # 原生模态调用尚未返回时，模拟另一 HTTP 请求进入相同入口。
        with pytest.raises(HostBridgeFailure) as error:
            platform.choose_paths("select_directory", HostDialogArguments())
        assert error.value.code == "E_HOST_BRIDGE_DIALOG_BUSY"
        assert len(harness.roots) == 1

    harness.during_picker = concurrent_click
    assert platform.choose_paths("open_file", HostDialogArguments()) == (harness.result,)
    assert len(harness.calls) == 1
    _assert_cleanup(harness.roots[0])
    _assert_mutex_available(platform)
