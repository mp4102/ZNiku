"""验证原生选择器的闭合 IPC、固定子进程入口及 windowed 标准管道边界。

这里只使用合成响应或 fake Tk，不打开用户媒体。真实 HWND 置顶和候选包重复点击需由
桌面验收另行证明；Python 异常测试不能伪装成已复现 Windows 原生崩溃栈。
"""

from __future__ import annotations

import builtins
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from zniku.desktop import __main__ as desktop_main
from zniku.project_service import host_bridge, native_picker
from zniku.project_service.host_bridge import HostDialogArguments, WindowsHostPlatform


def test_source_and_frozen_commands_have_only_fixed_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert native_picker._picker_command() == [
        sys.executable,
        "-I",
        str(Path(native_picker.__file__).resolve()),
    ]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert native_picker._picker_command() == [sys.executable, "--zniku-picker-helper"]


def test_environment_allowlist_excludes_secrets_and_python_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZNIKU_HOST_TOKEN", "synthetic-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret")
    monkeypatch.setenv("PYTHONPATH", "synthetic-injection")
    monkeypatch.setenv("TCL_LIBRARY", "synthetic-injection")
    monkeypatch.setenv("PATH", "synthetic-injection")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "synthetic-own-package")
    monkeypatch.setenv("_PYI_UNDECLARED", "synthetic-injection")
    environment = native_picker._picker_environment()
    assert not {
        "ZNIKU_HOST_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "PYTHONPATH",
        "TCL_LIBRARY",
        "PATH",
        "_PYI_UNDECLARED",
    }.intersection(environment)
    assert environment["_PYI_ARCHIVE_FILE"] == "synthetic-own-package"


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b"null",
        b"bad-json",
        b"\xff",
        b'{"version":1,"version":1,"paths":[]}',
        b'{"version":true,"paths":[]}',
        b'{"version":2,"paths":[]}',
        b'{"version":1,"paths":[],"extra":true}',
        b'{"version":1}',
        b'{"version":1,"paths":null}',
        b'{"version":1,"paths":[1]}',
        b'{"version":1,"paths":[""]}',
        b'{"version":1,"paths":["bad\\u0000path"]}',
        json.dumps({"version": 1, "paths": ["x"] * 257}).encode(),
        json.dumps({"version": 1, "paths": ["x" * 32768]}).encode(),
    ],
    ids=lambda payload: f"bytes-{len(payload)}-{payload[:36]!r}",
)
def test_invalid_response_fails_closed(payload: bytes) -> None:
    with pytest.raises((ValueError, UnicodeError)):
        native_picker._validate_response(payload, "open_files")


def test_response_cancel_unicode_single_selection_and_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert native_picker._validate_response(b'{"version":1,"paths":[]}', "open_file") is None
    payload = json.dumps({"version": 1, "paths": [r"C:\synthetic\结果.mov"]}).encode()
    assert native_picker._validate_response(payload, "open_file") == (r"C:\synthetic\结果.mov",)
    with pytest.raises(ValueError, match="单选"):
        native_picker._validate_response(b'{"version":1,"paths":["a","b"]}', "open_file")
    monkeypatch.setattr(native_picker, "MAX_RESPONSE_BYTES", 8)
    with pytest.raises(ValueError, match="大小"):
        native_picker._validate_response(payload, "open_file")


def test_desktop_helper_dispatch_never_enters_service_or_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["synthetic.exe", "--zniku-picker-helper"])
    monkeypatch.setattr(native_picker, "main", lambda: 17)
    monkeypatch.setattr(desktop_main, "application_data_root", lambda: pytest.fail("进入服务"))
    monkeypatch.setattr(desktop_main, "open_studio_browser", lambda _: pytest.fail("打开浏览器"))
    assert desktop_main.main() == 17


def test_native_primitive_refuses_request_worker_before_importing_tk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__
    errors: list[Exception] = []

    def reject_tk(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("tkinter"):
            pytest.fail("HTTP worker 不应导入 Tk")
        return original_import(name, *args, **kwargs)

    def run() -> None:
        try:
            native_picker.choose_paths_native("open_file", HostDialogArguments())
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(builtins, "__import__", reject_tk)
    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(errors) == 1 and "主线程" in str(errors[0])


def test_parent_capability_and_dispatch_do_not_import_tk(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def reject_tk(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("tkinter"):
            pytest.fail("父进程不应导入 Tk")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_tk)
    monkeypatch.setattr(host_bridge, "_running_on_windows", lambda: True)
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr(host_bridge, "run_picker_process", lambda *_: None)
    platform = WindowsHostPlatform()
    assert all(reason is None for reason in platform.capability_states().values())
    assert platform.choose_paths("open_file", HostDialogArguments()) is None


class RetainedBytes(io.BytesIO):
    """保留合成管道内容，避免 helper 的 with 清理妨碍断言。"""

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    "request_payload",
    [
        b"{}",
        b'{"version":true,"capability":"open_file","args":{}}',
        b'{"version":2,"capability":"open_file","args":{}}',
        b'{"version":1,"capability":"execute","args":{}}',
        b'{"version":1,"capability":"open_file","args":{"command":"bad"}}',
        b'{"version":1,"capability":"open_file","args":{"title":4}}',
        b'{"version":1,"capability":"open_file","args":{"suggested_name":"x"}}',
        b'{"version":1,"capability":"select_directory","args":{"extensions":[".mov"]}}',
        b'{"version":1,"capability":"open_file","args":{},"token":"bad"}',
        b'{"version":1,"version":1,"capability":"open_file","args":{}}',
        b"x" * (native_picker.MAX_REQUEST_BYTES + 1),
    ],
    ids=lambda payload: f"bytes-{len(payload)}-{payload[:50]!r}",
)
def test_helper_rejects_invalid_request_without_native_action(
    monkeypatch: pytest.MonkeyPatch,
    request_payload: bytes,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    output = RetainedBytes()
    monkeypatch.setattr(
        native_picker,
        "_pipe_stream",
        lambda *, reading: RetainedBytes(request_payload) if reading else output,
    )
    monkeypatch.setattr(native_picker, "choose_paths_native", lambda *_: pytest.fail("创建窗口"))
    assert native_picker.main() == 1
    assert output.getvalue() == b""


@pytest.mark.parametrize("selected", [None, (r"C:\synthetic\结果.mov",)])
def test_helper_main_emits_bounded_selection_or_cancel(
    monkeypatch: pytest.MonkeyPatch,
    selected: tuple[str, ...] | None,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    request = b'{"version":1,"capability":"open_file","args":{"title":"synthetic"}}'
    output = RetainedBytes()
    monkeypatch.setattr(
        native_picker,
        "_pipe_stream",
        lambda *, reading: RetainedBytes(request) if reading else output,
    )
    monkeypatch.setattr(native_picker, "choose_paths_native", lambda *_: selected)
    assert native_picker.main() == 0
    assert json.loads(output.getvalue()) == {"version": 1, "paths": list(selected or ())}


def test_helper_keeps_json_extension_lists_and_save_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    arguments = HostDialogArguments(
        title="保存合成工程", extensions=(".mov", ".mkv"), suggested_name="合成结果.mov"
    )
    request = json.dumps(
        {"version": 1, "capability": "save_file", "args": arguments.model_dump(mode="json")}
    ).encode()
    output = RetainedBytes()
    monkeypatch.setattr(
        native_picker,
        "_pipe_stream",
        lambda *, reading: RetainedBytes(request) if reading else output,
    )
    observed: list[HostDialogArguments] = []

    def native(capability: str, selected_arguments: HostDialogArguments) -> None:
        assert capability == "save_file"
        observed.append(selected_arguments)

    monkeypatch.setattr(native_picker, "choose_paths_native", native)
    assert native_picker.main() == 0
    assert observed == [arguments]
    assert json.loads(output.getvalue()) == {"version": 1, "paths": []}


def test_spawn_failure_releases_parent_mutex(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("synthetic spawn failure")

    monkeypatch.setattr(subprocess, "Popen", fail)
    platform = WindowsHostPlatform()
    with pytest.raises(OSError, match="synthetic"):
        platform.choose_paths("open_file", HostDialogArguments())
    assert platform._dialog_lock.acquire(blocking=False)
    platform._dialog_lock.release()


def test_parent_uses_fixed_pipes_environment_and_bounded_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[object, dict[str, object]]] = []
    input_stream = RetainedBytes()
    output_stream = RetainedBytes(b'{"version":1,"paths":[]}')
    process = SimpleNamespace(
        stdin=input_stream,
        stdout=output_stream,
        wait=lambda: 0,
        poll=lambda: 0,
        kill=lambda: pytest.fail("正常取消不应 kill 已结束 helper"),
    )

    def start(command: object, **options: object) -> object:
        commands.append((command, options))
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    monkeypatch.setenv("ZNIKU_HOST_TOKEN", "synthetic-not-inherited")
    arguments = HostDialogArguments(title="合成目录选择")
    assert native_picker.run_picker_process("select_directory", arguments) is None
    assert commands[0][0] == native_picker._picker_command()
    options = commands[0][1]
    assert options["shell"] is False and options["close_fds"] is True
    assert options["stdin"] == subprocess.PIPE and options["stdout"] == subprocess.PIPE
    assert options["stderr"] == subprocess.DEVNULL
    assert options["creationflags"] == (0x08000000 if sys.platform == "win32" else 0)
    assert isinstance(options["env"], dict) and "ZNIKU_HOST_TOKEN" not in options["env"]
    assert json.loads(input_stream.getvalue()) == {
        "version": 1,
        "capability": "select_directory",
        "args": arguments.model_dump(mode="json"),
    }


def test_oversized_helper_output_kills_only_owned_process_and_releases_mutex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_picker, "MAX_RESPONSE_BYTES", 32)
    events: list[str] = []
    exit_code: int | None = None

    def kill() -> None:
        nonlocal exit_code
        events.append("kill-owned")
        exit_code = -1

    def wait() -> int:
        events.append("wait-owned")
        assert exit_code is not None
        return exit_code

    process = SimpleNamespace(
        stdin=RetainedBytes(),
        stdout=RetainedBytes(b"x" * 33),
        wait=wait,
        poll=lambda: exit_code,
        kill=kill,
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_, **__: process)
    platform = WindowsHostPlatform()
    with pytest.raises(ValueError, match="大小"):
        platform.choose_paths("open_file", HostDialogArguments())
    assert events == ["kill-owned", "wait-owned"]
    assert platform._dialog_lock.acquire(blocking=False)
    platform._dialog_lock.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 windowed 标准管道桥接")
@pytest.mark.parametrize("handle", [None, 0, -1])
def test_windowed_invalid_standard_handle_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    handle: int | None,
) -> None:
    import ctypes

    def standard_handle(_: int) -> int | None:
        return ctypes.c_void_p(handle).value

    kernel = SimpleNamespace(GetStdHandle=standard_handle)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_, **__: kernel)
    with pytest.raises(OSError, match="标准管道"):
        native_picker._windows_pipe_stream(reading=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 windowed 标准管道桥接")
@pytest.mark.parametrize("reading", [True, False])
def test_windowed_handle_transfers_once_to_owned_binary_stream(
    monkeypatch: pytest.MonkeyPatch,
    reading: bool,
) -> None:
    import ctypes
    import msvcrt

    calls: list[tuple[object, ...]] = []

    def standard_handle(value: int) -> int:
        calls.append(("handle", value))
        return 123

    def descriptor(handle: int, flags: int) -> int:
        calls.append(("descriptor", handle, flags))
        return 99

    stream = RetainedBytes()

    def fdopen(fd: int, mode: str, *, closefd: bool) -> RetainedBytes:
        calls.append(("open", fd, mode, closefd))
        return stream

    kernel = SimpleNamespace(GetStdHandle=standard_handle)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_, **__: kernel)
    monkeypatch.setattr(msvcrt, "open_osfhandle", descriptor)
    monkeypatch.setattr(os, "fdopen", fdopen)
    assert native_picker._windows_pipe_stream(reading=reading) is stream
    assert calls == [
        ("handle", -10 if reading else -11),
        ("descriptor", 123, (os.O_RDONLY if reading else os.O_WRONLY) | cast(Any, os).O_BINARY),
        ("open", 99, "rb" if reading else "wb", True),
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows pythonw 标准管道集成")
def test_real_windowed_interpreter_uses_inherited_binary_pipes() -> None:
    """无控制台进程模拟 frozen 的 None 标准流；真实继承管道不创建窗口或用户文件。"""

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("当前解释器未安装 pythonw.exe；仍需 frozen candidate 标准管道验收")
    source_root = str(Path(native_picker.__file__).resolve().parents[2])
    child = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from zniku.project_service.native_picker import _pipe_stream; "
        "sys.stdin = None; sys.stdout = None; "
        "i = _pipe_stream(reading=True); data = i.read(128); i.close(); "
        "o = _pipe_stream(reading=False); o.write(data); o.close()"
    )
    result = subprocess.run(
        [str(pythonw), "-I", "-c", child, source_root],
        input="合成 binary IPC".encode(),
        capture_output=True,
        env=native_picker._picker_environment(),
        shell=False,
        close_fds=True,
        creationflags=0x08000000,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "合成 binary IPC".encode()
