"""验证 Phase 0 HostBridge 候选 B 的授权、路径与无副作用边界。

测试只使用合成临时路径和可注入宿主，不弹出原生对话框、不启动外部程序，也不接触 Project、Graph、
Runtime 或真实媒体。
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from host_bridge_prototype import (
        HOST_CAPABILITIES,
        HostBridgeFailure,
        HostBridgeSession,
        HostCapability,
        LaunchCommand,
        WindowsTkPlatform,
        probe_windows_tk_without_window,
        serve_host_bridge,
    )
else:
    _PROTOTYPE_PATH = Path(__file__).parents[1] / "tools" / "host_bridge_prototype.py"
    _SPEC = importlib.util.spec_from_file_location(
        "zniku_phase0_host_bridge_prototype",
        _PROTOTYPE_PATH,
    )
    if _SPEC is None or _SPEC.loader is None:
        raise RuntimeError("无法加载 HostBridge Phase 0 prototype")
    _MODULE: ModuleType = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = _MODULE
    _SPEC.loader.exec_module(_MODULE)
    HOST_CAPABILITIES = _MODULE.HOST_CAPABILITIES
    HostBridgeFailure = _MODULE.HostBridgeFailure
    HostBridgeSession = _MODULE.HostBridgeSession
    HostCapability = _MODULE.HostCapability
    LaunchCommand = _MODULE.LaunchCommand
    WindowsTkPlatform = _MODULE.WindowsTkPlatform
    probe_windows_tk_without_window = _MODULE.probe_windows_tk_without_window
    serve_host_bridge = _MODULE.serve_host_bridge

_ORIGIN = "http://127.0.0.1:4173"


class RecordingPlatform:
    """记录已验证的宿主调用，避免测试产生桌面副作用。"""

    def __init__(
        self,
        selections: Mapping[HostCapability, Sequence[str] | None] | None = None,
    ) -> None:
        self.selections = dict(selections or {})
        self.choose_calls: list[tuple[HostCapability, dict[str, object]]] = []
        self.launches: list[LaunchCommand] = []

    def choose_paths(
        self,
        capability: HostCapability,
        arguments: Mapping[str, object],
    ) -> Sequence[str] | None:
        self.choose_calls.append((capability, dict(arguments)))
        return self.selections.get(capability)

    def launch(self, command: LaunchCommand) -> None:
        self.launches.append(command)


@contextmanager
def _serve(
    session: HostBridgeSession,
) -> Iterator[tuple[str, ThreadingHTTPServer]]:
    server = serve_host_bridge(session)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    base_url: str,
    path: str,
    *,
    method: str,
    origin: str | None = _ORIGIN,
    token: str | None = None,
    payload: object | None = None,
) -> tuple[int, dict[str, Any] | None, Message]:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["Origin"] = origin
    if token is not None:
        headers["X-ZNIKU-Host-Token"] = token
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        parsed = cast(dict[str, Any], json.loads(raw)) if raw else None
        return response.status, parsed, response.headers


def _issue(base_url: str, session: HostBridgeSession, capability: HostCapability) -> str:
    status, payload, _ = _request(
        base_url,
        "/api/host-bridge/user-actions",
        method="POST",
        token=session.token,
        payload={"capability": capability},
    )
    assert status == 201
    assert payload is not None
    return cast(str, payload["user_action_id"])


def _invoke(
    base_url: str,
    session: HostBridgeSession,
    capability: HostCapability,
    user_action_id: str,
    arguments: object,
) -> tuple[int, dict[str, Any] | None, Message]:
    return _request(
        base_url,
        "/api/host-bridge/invoke",
        method="POST",
        token=session.token,
        payload={
            "user_action_id": user_action_id,
            "capability": capability,
            "arguments": arguments,
        },
    )


def test_capability_set_and_random_session_token_are_closed() -> None:
    first = HostBridgeSession(RecordingPlatform(), studio_origin=_ORIGIN)
    second = HostBridgeSession(RecordingPlatform(), studio_origin=_ORIGIN)

    assert {
        "open_file",
        "open_files",
        "select_directory",
        "save_file",
        "reveal_in_file_manager",
        "open_with_system_player",
    } == HOST_CAPABILITIES
    assert first.token != second.token
    assert len(first.token) >= 43
    assert len(second.token) >= 43
    assert all(not character.isspace() for character in first.token)


@pytest.mark.parametrize(
    "origin",
    (
        "http://localhost:4173",
        "http://127.0.0.1:4173/",
        "http://127.0.0.1",
        "https://127.0.0.1:4173",
        "http://127.0.0.1:80",
        "http://192.168.1.20:4173",
    ),
)
def test_configured_studio_origin_must_be_exact_ipv4_loopback(origin: str) -> None:
    with pytest.raises(ValueError, match="Studio Origin"):
        HostBridgeSession(RecordingPlatform(), studio_origin=origin)


def test_http_rejects_missing_or_inexact_origin_and_session_token() -> None:
    session = HostBridgeSession(RecordingPlatform(), studio_origin=_ORIGIN)
    with _serve(session) as (base_url, _):
        cases = (
            {"origin": None, "token": session.token, "code": "E_HOST_BRIDGE_ORIGIN"},
            {
                "origin": "http://localhost:4173",
                "token": session.token,
                "code": "E_HOST_BRIDGE_ORIGIN",
            },
            {"origin": _ORIGIN, "token": None, "code": "E_HOST_BRIDGE_SESSION"},
            {"origin": _ORIGIN, "token": "x" * 43, "code": "E_HOST_BRIDGE_SESSION"},
        )
        for case in cases:
            status, payload, headers = _request(
                base_url,
                "/api/host-bridge/user-actions",
                method="POST",
                origin=case["origin"],
                token=case["token"],
                payload={"capability": "open_file"},
            )
            assert status == 403
            assert payload is not None
            assert payload["error"]["code"] == case["code"]
            if case["origin"] == _ORIGIN:
                assert headers.get("Access-Control-Allow-Origin") == _ORIGIN


def test_http_allows_only_authenticated_post_and_exact_preflight() -> None:
    session = HostBridgeSession(RecordingPlatform(), studio_origin=_ORIGIN)
    with _serve(session) as (base_url, _):
        status, payload, headers = _request(
            base_url,
            "/api/host-bridge/user-actions",
            method="OPTIONS",
        )
        assert status == 204
        assert payload is None
        assert headers.get("Access-Control-Allow-Origin") == _ORIGIN
        assert headers.get("Access-Control-Allow-Methods") == "POST, OPTIONS"
        assert "X-ZNIKU-Host-Token" in headers.get("Access-Control-Allow-Headers", "")

        status, payload, _ = _request(
            base_url,
            "/api/host-bridge/user-actions",
            method="GET",
            token=session.token,
        )
        assert status == 405
        assert payload is not None
        assert payload["error"]["code"] == "E_HOST_BRIDGE_METHOD"

        status, payload, _ = _request(
            base_url,
            "/api/host-bridge/user-actions",
            method="OPTIONS",
            origin="http://127.0.0.1:4174",
        )
        assert status == 403
        assert payload is not None
        assert payload["error"]["code"] == "E_HOST_BRIDGE_ORIGIN"


def test_http_open_file_returns_only_an_absolute_selected_path(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.mkv"
    source.write_bytes(b"synthetic")
    platform = RecordingPlatform({"open_file": (str(source),)})
    session = HostBridgeSession(platform, studio_origin=_ORIGIN)

    with _serve(session) as (base_url, _):
        action_id = _issue(base_url, session, "open_file")
        status, payload, headers = _invoke(
            base_url,
            session,
            "open_file",
            action_id,
            {"title": "选择输入", "extensions": [".MKV", ".mp4"]},
        )

    assert status == 200
    assert payload == {"paths": [str(source.resolve())], "status": "selected"}
    assert headers.get("Cache-Control") == "no-store"
    assert platform.choose_calls == [
        ("open_file", {"title": "选择输入", "extensions": (".mkv", ".mp4")})
    ]


def test_cancelled_save_does_not_create_or_mutate_the_target(tmp_path: Path) -> None:
    target = tmp_path / "not-created.zniku"
    platform = RecordingPlatform({"save_file": None})
    session = HostBridgeSession(platform, studio_origin=_ORIGIN)

    with _serve(session) as (base_url, _):
        action_id = _issue(base_url, session, "save_file")
        status, payload, _ = _invoke(
            base_url,
            session,
            "save_file",
            action_id,
            {"suggested_name": target.name, "extensions": [".zniku"]},
        )

    assert status == 200
    assert payload == {"paths": [], "status": "cancelled"}
    assert not target.exists()
    assert platform.launches == []


def test_save_selection_returns_path_without_creating_output(tmp_path: Path) -> None:
    target = tmp_path / "future-output.mkv"
    platform = RecordingPlatform({"save_file": (str(target),)})
    session = HostBridgeSession(platform, studio_origin=_ORIGIN)
    action_id, _ = session.issue_user_action("save_file")

    result = session.invoke(
        action_id=action_id,
        capability_value="save_file",
        arguments_value={"suggested_name": target.name},
    )

    assert result.status == "selected"
    assert result.paths == (str(target.resolve()),)
    assert not target.exists()


def test_open_files_and_select_directory_cover_remaining_dialog_capabilities(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    folder = tmp_path / "outputs"
    folder.mkdir()
    platform = RecordingPlatform(
        {
            "open_files": (str(first), str(second)),
            "select_directory": (str(folder),),
        }
    )
    session = HostBridgeSession(platform, studio_origin=_ORIGIN)

    files_id, _ = session.issue_user_action("open_files")
    files = session.invoke(
        action_id=files_id,
        capability_value="open_files",
        arguments_value={"extensions": [".mkv", ".mp4"]},
    )
    directory_id, _ = session.issue_user_action("select_directory")
    directory = session.invoke(
        action_id=directory_id,
        capability_value="select_directory",
        arguments_value={"title": "选择输出目录"},
    )

    assert files.paths == (str(first.resolve()), str(second.resolve()))
    assert directory.paths == (str(folder.resolve()),)


def test_user_action_ticket_is_capability_bound_single_use_and_short_lived() -> None:
    now = [100.0]
    session = HostBridgeSession(
        RecordingPlatform(),
        studio_origin=_ORIGIN,
        clock=lambda: now[0],
    )
    mismatch_id, _ = session.issue_user_action("open_file")
    with pytest.raises(HostBridgeFailure) as mismatch:
        session.invoke(
            action_id=mismatch_id,
            capability_value="save_file",
            arguments_value={},
        )
    assert mismatch.value.code == "E_HOST_BRIDGE_ACTION_MISMATCH"

    with pytest.raises(HostBridgeFailure) as replay:
        session.invoke(
            action_id=mismatch_id,
            capability_value="open_file",
            arguments_value={},
        )
    assert replay.value.code == "E_HOST_BRIDGE_ACTION_REQUIRED"

    expired_id, _ = session.issue_user_action("open_file")
    now[0] += 6
    with pytest.raises(HostBridgeFailure) as expired:
        session.invoke(
            action_id=expired_id,
            capability_value="open_file",
            arguments_value={},
        )
    assert expired.value.code == "E_HOST_BRIDGE_ACTION_EXPIRED"


def test_user_action_tickets_are_bounded_and_expired_entries_are_pruned() -> None:
    now = [100.0]
    session = HostBridgeSession(
        RecordingPlatform(),
        studio_origin=_ORIGIN,
        clock=lambda: now[0],
    )
    for _ in range(64):
        session.issue_user_action("open_file")

    with pytest.raises(HostBridgeFailure) as limited:
        session.issue_user_action("open_file")
    assert limited.value.code == "E_HOST_BRIDGE_ACTION_LIMIT"
    assert limited.value.http_status == 429

    now[0] += 6
    action_id, capability = session.issue_user_action("save_file")
    assert action_id
    assert capability == "save_file"


def test_unknown_capability_and_arbitrary_command_fields_fail_closed(tmp_path: Path) -> None:
    media = tmp_path / "synthetic.mkv"
    media.write_bytes(b"synthetic")
    session = HostBridgeSession(RecordingPlatform(), studio_origin=_ORIGIN)

    with pytest.raises(HostBridgeFailure) as unknown:
        session.issue_user_action("run_shell")
    assert unknown.value.code == "E_HOST_BRIDGE_CAPABILITY"

    action_id, _ = session.issue_user_action("open_with_system_player")
    with pytest.raises(HostBridgeFailure) as arbitrary:
        session.invoke(
            action_id=action_id,
            capability_value="open_with_system_player",
            arguments_value={
                "path": str(media),
                "executable": "powershell.exe",
                "argv": ["-Command", "Remove-Item"],
            },
        )
    assert arbitrary.value.code == "E_HOST_BRIDGE_FIELDS"


def test_relative_dialog_result_and_system_path_are_rejected(tmp_path: Path) -> None:
    platform = RecordingPlatform({"open_file": ("relative.mkv",)})
    session = HostBridgeSession(platform, studio_origin=_ORIGIN)
    action_id, _ = session.issue_user_action("open_file")
    with pytest.raises(HostBridgeFailure) as dialog_error:
        session.invoke(
            action_id=action_id,
            capability_value="open_file",
            arguments_value={},
        )
    assert dialog_error.value.code == "E_HOST_BRIDGE_DIALOG_RESULT"

    action_id, _ = session.issue_user_action("reveal_in_file_manager")
    with pytest.raises(HostBridgeFailure) as path_error:
        session.invoke(
            action_id=action_id,
            capability_value="reveal_in_file_manager",
            arguments_value={"path": str(Path("relative"))},
        )
    assert path_error.value.code == "E_HOST_BRIDGE_PATH"
    assert list(tmp_path.iterdir()) == []


def test_duplicate_dialog_results_and_player_directory_fail_closed(tmp_path: Path) -> None:
    media = tmp_path / "synthetic.mkv"
    media.write_bytes(b"synthetic")
    platform = RecordingPlatform({"open_files": (str(media), str(media))})
    session = HostBridgeSession(platform, studio_origin=_ORIGIN)

    files_id, _ = session.issue_user_action("open_files")
    with pytest.raises(HostBridgeFailure) as duplicate:
        session.invoke(
            action_id=files_id,
            capability_value="open_files",
            arguments_value={},
        )
    assert duplicate.value.code == "E_HOST_BRIDGE_DIALOG_RESULT"

    player_id, _ = session.issue_user_action("open_with_system_player")
    with pytest.raises(HostBridgeFailure) as wrong_kind:
        session.invoke(
            action_id=player_id,
            capability_value="open_with_system_player",
            arguments_value={"path": str(tmp_path)},
        )
    assert wrong_kind.value.code == "E_HOST_BRIDGE_PATH_KIND"
    assert platform.launches == []


def test_system_actions_generate_fixed_argv_without_shell(tmp_path: Path) -> None:
    media = tmp_path / "synthetic movie.mkv"
    media.write_bytes(b"synthetic")
    platform = RecordingPlatform()
    session = HostBridgeSession(platform, studio_origin=_ORIGIN)

    reveal_id, _ = session.issue_user_action("reveal_in_file_manager")
    reveal = session.invoke(
        action_id=reveal_id,
        capability_value="reveal_in_file_manager",
        arguments_value={"path": str(media)},
    )
    player_id, _ = session.issue_user_action("open_with_system_player")
    player = session.invoke(
        action_id=player_id,
        capability_value="open_with_system_player",
        arguments_value={"path": str(media)},
    )

    assert reveal.status == player.status == "launched"
    assert platform.launches == [
        LaunchCommand("explorer.exe", ("/select,", str(media.resolve()))),
        LaunchCommand(
            "rundll32.exe",
            ("url.dll,FileProtocolHandler", str(media.resolve())),
        ),
    ]
    assert all(command.shell is False for command in platform.launches)


def test_windows_platform_passes_a_list_and_shell_false_to_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[object, dict[str, object]]] = []

    def fake_popen(command: object, **options: object) -> object:
        observed.append((command, options))
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    platform = WindowsTkPlatform()
    platform.launch(LaunchCommand("explorer.exe", ("C:\\synthetic movie.mkv",)))

    assert observed[0][0] == ["explorer.exe", "C:\\synthetic movie.mkv"]
    assert observed[0][1]["shell"] is False


def test_loopback_binding_and_lan_client_rejection() -> None:
    session = HostBridgeSession(RecordingPlatform(), studio_origin=_ORIGIN)
    server = serve_host_bridge(session)
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert server.server_address[1] > 0
    finally:
        server.server_close()

    with pytest.raises(HostBridgeFailure) as rejected:
        session.authorize(
            client_host="192.168.1.50",
            origin=_ORIGIN,
            token=session.token,
        )
    assert rejected.value.code == "E_HOST_BRIDGE_LAN_FORBIDDEN"


@pytest.mark.skipif(sys.platform != "win32", reason="v0.3.0 正式目标是 Windows")
def test_tk_dependency_probe_does_not_create_a_window() -> None:
    evidence = probe_windows_tk_without_window()

    assert evidence["windows"] is True
    assert evidence["filedialog_importable"] is True
    assert evidence["window_manager_loaded"] is False
    assert str(evidence["tk_version"]).startswith("8.6")
    assert str(evidence["tcl_patchlevel"]).startswith("8.6")


def test_duplicate_or_unknown_http_fields_fail_closed() -> None:
    session = HostBridgeSession(RecordingPlatform(), studio_origin=_ORIGIN)
    with _serve(session) as (base_url, server):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/host-bridge/user-actions",
                body=b'{"capability":"open_file","capability":"save_file"}',
                headers={
                    "Content-Type": "application/json",
                    "Origin": _ORIGIN,
                    "X-ZNIKU-Host-Token": session.token,
                },
            )
            response = connection.getresponse()
            duplicate = cast(dict[str, Any], json.loads(response.read()))
        finally:
            connection.close()
        assert response.status == 400
        assert duplicate["error"]["code"] == "E_HOST_BRIDGE_JSON"

        status, payload, _ = _request(
            base_url,
            "/api/host-bridge/user-actions",
            method="POST",
            token=session.token,
            payload={"capability": "open_file", "unknown": True},
        )
        assert status == 400
        assert payload is not None
        assert payload["error"]["code"] == "E_HOST_BRIDGE_FIELDS"
