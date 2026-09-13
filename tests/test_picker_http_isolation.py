"""以真实桌面 HTTP、真实合成子进程验证 picker 崩溃隔离，不调用任何原生 UI。

仅测试内 monkeypatch 固定 helper 命令；票据、Origin、token、线程请求、父进程的管道与
返回解析均使用正式实现。不能用这些测试代替 Windows 原生窗口和最终包真实点击验收。
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import zniku.project_service.native_picker as native_picker
from zniku.desktop.server import DesktopServer
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.host_bridge import (
    HOST_CAPABILITIES,
    HOST_TOKEN_HEADER,
    WindowsHostPlatform,
)

_FIXTURE = Path(__file__).with_name("picker_process_fixture.py")


@pytest.fixture
def picker_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DesktopServer]:
    """只绕过 OS 能力探测，保留正式平台 choose_paths 与完整桌面服务实现。"""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text(
        '<html><head><script src="/main.js"></script></head><body></body></html>',
        encoding="utf-8",
    )
    platform = WindowsHostPlatform()
    monkeypatch.setattr(platform, "capability_states", lambda: dict.fromkeys(HOST_CAPABILITIES))
    server = DesktopServer(
        ProjectServiceApplication(work_root=tmp_path / "attempts"),
        assets,
        platform=platform,
        data_root=tmp_path / "settings",
    )
    server.start()
    try:
        yield server
    finally:
        server.close()


def _request(desktop: DesktopServer, path: str, payload: object | None = None) -> tuple[int, Any]:
    """只访问本测试新建服务，不打印、持久化或外传 session token。"""
    headers = {"Origin": desktop.origin, HOST_TOKEN_HEADER: desktop.host_bridge.token}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(desktop.origin + path, headers=headers, data=data)
    try:
        response = urlopen(request, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read())


def _ticket(desktop: DesktopServer) -> str:
    status, body = _request(
        desktop, "/api/host-bridge/user-actions", {"capability": "select_directory"}
    )
    assert status == 201
    action_id: str = body["user_action_id"]
    return action_id


def _invoke(desktop: DesktopServer, action_id: str | None = None) -> tuple[int, Any]:
    return _request(
        desktop,
        "/api/host-bridge/invoke",
        {
            "user_action_id": action_id or _ticket(desktop),
            "capability": "select_directory",
            "arguments": {"title": "合成 picker 隔离测试"},
        },
    )


def _assert_health(desktop: DesktopServer) -> None:
    status, body = _request(desktop, "/api/desktop/health")
    assert status == 200
    assert body["status"] == "ready" and body["instance_id"] == desktop.instance_id


@pytest.mark.parametrize("failure", ["nonzero", "bad_json", "extra_field", "wrong_version"])
def test_failed_real_helper_leaves_http_healthy_and_next_picker_available(
    picker_desktop: DesktopServer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    modes = iter([failure, "cancel"])
    commands: list[list[str]] = []

    def command() -> list[str]:
        args = [sys.executable, "-I", str(_FIXTURE), next(modes)]
        commands.append(args)
        return args

    monkeypatch.setattr(native_picker, "_picker_command", command)
    _assert_health(picker_desktop)
    ticket = _ticket(picker_desktop)
    status, body = _invoke(picker_desktop, ticket)
    assert status == 500 and body["error"]["code"] == "E_HOST_BRIDGE_DIALOG"
    _assert_health(picker_desktop)
    # 失败也消费票据；服务不会重放同一次点击、创建第二个弹窗或偷偷重试选择。
    replay_status, replay = _invoke(picker_desktop, ticket)
    assert replay_status == 409
    assert replay["error"]["code"] == "E_HOST_BRIDGE_ACTION_REQUIRED"
    status, body = _invoke(picker_desktop)
    assert status == 200 and body["status"] == "cancelled" and body["selections"] == []
    _assert_health(picker_desktop)
    assert len(commands) == 2
    assert not picker_desktop.host_bridge._selections
    assert not list(tmp_path.rglob("*.zniku"))


def test_active_helper_keeps_health_available_and_concurrent_picker_fails_busy(
    picker_desktop: DesktopServer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ready, release = tmp_path / "helper-ready", tmp_path / "helper-release"
    commands: list[list[str]] = []

    def command() -> list[str]:
        mode = ["wait", str(ready), str(release)] if not commands else ["cancel"]
        args = [sys.executable, "-I", str(_FIXTURE), *mode]
        commands.append(args)
        return args

    monkeypatch.setattr(native_picker, "_picker_command", command)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_invoke, picker_desktop)
        try:
            deadline = time.monotonic() + 4
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), "合成 helper 未进入受控等待"
            _assert_health(picker_desktop)
            status, body = _invoke(picker_desktop)
            assert status == 409 and body["error"]["code"] == "E_HOST_BRIDGE_DIALOG_BUSY"
            assert len(commands) == 1
        finally:
            release.touch()
        status, body = future.result(timeout=5)
    assert status == 200 and body["status"] == "cancelled"
    assert _invoke(picker_desktop)[0] == 200
    assert len(commands) == 2
    _assert_health(picker_desktop)


def test_http_cannot_choose_helper_program_or_enable_failure_mode(
    picker_desktop: DesktopServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> list[str]:
        pytest.fail("无效 HTTP 参数不得到达 helper 命令入口")

    monkeypatch.setattr(native_picker, "_picker_command", forbidden)
    status, body = _request(
        picker_desktop,
        "/api/host-bridge/invoke",
        {
            "user_action_id": _ticket(picker_desktop),
            "capability": "select_directory",
            "arguments": {"title": "合成", "executable": "unknown", "mode": "nonzero"},
        },
    )
    assert status == 422 and body["error"]["code"] == "E_HOST_BRIDGE_ARGUMENTS"
    _assert_health(picker_desktop)
