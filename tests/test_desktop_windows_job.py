"""在隔离 Windows helper 中验证真正 JobObject，不把测试进程或用户应用纳入清理。"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, cast

import pytest

from zniku.desktop.windows import is_windows
from zniku.runtime import process_window


@pytest.mark.parametrize("platform, expected", [("win32", 0x08000000), ("linux", 0), ("darwin", 0)])
def test_background_flag_only_changes_console_visibility(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    expected: int,
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    assert process_window.background_creation_flags() == expected


@pytest.mark.skipif(not is_windows(), reason="Windows JobObject 真实进程归属门禁")
def test_job_exit_cleans_automatic_child_but_keeps_external_child(tmp_path: Path) -> None:
    """只有合成自动 child 随 helper 退出；合成 external 用 stop 文件自己退出。"""

    stop = tmp_path / "stop-external"
    external_pid = tmp_path / "external-pid"
    child_script = (
        "import os,pathlib,sys,time; "
        "pathlib.Path(sys.argv[2]).write_text(str(os.getpid())); "
        "deadline=time.monotonic()+20; "
        "exec('while time.monotonic()<deadline and not pathlib.Path(sys.argv[1]).exists():"
        "\\n time.sleep(0.05)')"
    )
    helper = r"""
import json, pathlib, subprocess, sys, time
from zniku.desktop.windows import install_process_job, launch_external
from zniku.project_service.host_bridge import HostLaunchCommand
install_process_job()
automatic = subprocess.Popen(
    [sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    creationflags=0x08000000,
)
launch_external(HostLaunchCommand(
    executable=sys.executable, argv=('-c', sys.argv[1], sys.argv[2], sys.argv[4]),
))
deadline=time.monotonic()+5
while not pathlib.Path(sys.argv[4]).exists() and time.monotonic()<deadline:
    time.sleep(0.05)
print(json.dumps([automatic.pid, int(pathlib.Path(sys.argv[4]).read_text())]), flush=True)
sys.stdin.readline()
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            helper,
            child_script,
            str(stop),
            str(tmp_path / "automatic-pid"),
            str(external_pid),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        creationflags=0x08000000,
    )
    kernel = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles: list[int] = []
    try:
        assert process.stdout is not None and process.stdin is not None
        line = process.stdout.readline()
        assert line, process.communicate(timeout=5)
        pids = json.loads(line)
        for pid in pids:
            handle = kernel.OpenProcess(0x00100000, False, pid)
            assert handle, "不能观察刚创建的合成进程"
            handles.append(int(handle))
        process.stdin.write("close\n")
        process.stdin.flush()
        assert process.wait(timeout=5) == 0
        assert kernel.WaitForSingleObject(handles[0], 5000) == 0
        assert kernel.WaitForSingleObject(handles[1], 100) == 258
        stop.touch()
        assert kernel.WaitForSingleObject(handles[1], 5000) == 0
    finally:
        stop.touch()
        if process.poll() is None:
            process.communicate(input="close\n", timeout=5)
        for handle in handles:
            kernel.CloseHandle(handle)
