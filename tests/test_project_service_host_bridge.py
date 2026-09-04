"""验证 Phase 2 正式 HostBridge 的授权、路径 authority 与取消边界。

测试只使用临时合成文件、注入平台和 loopback HTTP server；不会弹出原生窗口或启动
Explorer/播放器。Project、Run、Artifact 和路径均不离开 pytest 临时目录。
"""

from __future__ import annotations

import http.client
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
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

import zniku.project_service.host_bridge as host_bridge_module
from zniku.project_service import (
    HOST_CAPABILITIES,
    ArtifactPathReference,
    HandoffPathReference,
    HostBridgeFailure,
    HostBridgeSession,
    HostCapability,
    HostDialogArguments,
    HostLaunchCommand,
    HostUserActionRequest,
    ProjectServiceApplication,
    ProjectServiceHostPathResolver,
    WindowsHostPlatform,
    create_project_service_host_bridge_session,
    make_project_service_handler,
)
from zniku.runtime import NodeRunState

_ORIGIN = "http://127.0.0.1:4173"


class RecordingPlatform:
    """记录宿主调用并返回预置选择，不产生桌面副作用。"""

    def __init__(
        self,
        selections: Mapping[HostCapability, Sequence[str] | None] | None = None,
        *,
        states: Mapping[HostCapability, str | None] | None = None,
    ) -> None:
        self.selections = dict(selections or {})
        self.states = dict(states or dict.fromkeys(HOST_CAPABILITIES))
        self.choose_calls: list[tuple[HostCapability, HostDialogArguments]] = []
        self.launches: list[HostLaunchCommand] = []

    def capability_states(self) -> Mapping[HostCapability, str | None]:
        return self.states

    def choose_paths(
        self,
        capability: HostCapability,
        arguments: HostDialogArguments,
    ) -> Sequence[str] | None:
        self.choose_calls.append((capability, arguments))
        return self.selections.get(capability)

    def launch(self, command: HostLaunchCommand) -> None:
        self.launches.append(command)


class RecordingResolver:
    """把正式 reference 映射到测试路径并记录调用。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.references: list[ArtifactPathReference | HandoffPathReference] = []

    def resolve(self, reference: ArtifactPathReference | HandoffPathReference) -> Path:
        self.references.append(reference)
        return self.path


def _session(
    platform: RecordingPlatform,
    resolver_path: Path,
    *,
    clock: Any | None = None,
) -> HostBridgeSession:
    resolver = RecordingResolver(resolver_path)
    if clock is None:
        return HostBridgeSession(
            platform,
            studio_origin=_ORIGIN,
            path_resolver=resolver,
        )
    return HostBridgeSession(
        platform,
        studio_origin=_ORIGIN,
        path_resolver=resolver,
        clock=clock,
    )


@contextmanager
def _serve(
    application: ProjectServiceApplication,
    session: HostBridgeSession | None,
) -> Iterator[tuple[str, ThreadingHTTPServer]]:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_project_service_handler(application, host_bridge=session),
    )
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
    content_type: str = "application/json",
) -> tuple[int, dict[str, Any] | None, Message]:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["Origin"] = origin
    if token is not None:
        headers["X-ZNIKU-Host-Token"] = token
    data = None
    if payload is not None:
        headers["Content-Type"] = content_type
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read()
        body = cast(dict[str, Any], json.loads(raw)) if raw else None
        return response.status, body, response.headers


def _issue(session: HostBridgeSession, capability: HostCapability) -> str:
    return session.issue_user_action({"capability": capability}).user_action_id


def test_session_token_and_capability_contract_are_closed(tmp_path: Path) -> None:
    first = _session(RecordingPlatform(), tmp_path)
    second = _session(RecordingPlatform(), tmp_path)

    assert first.token != second.token
    assert len(first.token) >= 43
    assert tuple(item.capability for item in first.inspect_capabilities().capabilities) == (
        HOST_CAPABILITIES
    )
    assert all(item.available for item in first.inspect_capabilities().capabilities)
    with pytest.raises(ValidationError):
        HostUserActionRequest.model_validate(
            {"capability": "open_file", "executable": "powershell.exe"},
            strict=True,
        )
    with pytest.raises(HostBridgeFailure) as secret_in_payload:
        first.issue_user_action({"capability": "open_file", "token": first.token})
    assert first.token not in secret_in_payload.value.message
    with pytest.raises(ValueError, match="Origin"):
        HostBridgeSession(
            RecordingPlatform(),
            studio_origin="http://localhost:4173",
            path_resolver=RecordingResolver(tmp_path),
        )


def test_invalid_or_unavailable_platform_state_fails_with_stable_code(tmp_path: Path) -> None:
    missing = RecordingPlatform(states={"open_file": None})
    session = _session(missing, tmp_path)
    with pytest.raises(HostBridgeFailure) as malformed:
        session.inspect_capabilities()
    assert malformed.value.code == "E_HOST_BRIDGE_UNAVAILABLE"

    invalid_reason_states = dict.fromkeys(HOST_CAPABILITIES)
    invalid_reason_states["open_file"] = ""
    invalid_reason = _session(RecordingPlatform(states=invalid_reason_states), tmp_path)
    with pytest.raises(HostBridgeFailure) as invalid_reason_failure:
        invalid_reason.inspect_capabilities()
    assert invalid_reason_failure.value.code == "E_HOST_BRIDGE_UNAVAILABLE"

    states = dict.fromkeys(HOST_CAPABILITIES)
    states["open_file"] = "原生 picker 不可用"
    unavailable = _session(RecordingPlatform(states=states), tmp_path)
    with pytest.raises(HostBridgeFailure) as failure:
        unavailable.issue_user_action({"capability": "open_file"})
    assert failure.value.code == "E_HOST_BRIDGE_CAPABILITY_UNAVAILABLE"


def test_picker_returns_handle_and_system_action_never_accepts_raw_path(tmp_path: Path) -> None:
    source = tmp_path / "synthetic movie.mkv"
    source.write_bytes(b"synthetic")
    platform = RecordingPlatform({"open_file": (str(source),)})
    session = _session(platform, tmp_path)

    selected = session.invoke(
        {
            "user_action_id": _issue(session, "open_file"),
            "capability": "open_file",
            "arguments": {"title": "选择视频", "extensions": [".MKV", ".mp4"]},
        }
    )

    assert selected.status == "selected"
    assert len(selected.selections) == 1
    assert selected.selections[0].path == str(source.resolve())
    assert platform.choose_calls[0][1].extensions == (".mkv", ".mp4")

    raw_ticket = _issue(session, "reveal_in_file_manager")
    with pytest.raises(HostBridgeFailure) as raw_path:
        session.invoke(
            {
                "user_action_id": raw_ticket,
                "capability": "reveal_in_file_manager",
                "arguments": {"path": str(source)},
            }
        )
    assert raw_path.value.code == "E_HOST_BRIDGE_ARGUMENTS"
    with pytest.raises(HostBridgeFailure) as replay:
        session.invoke(
            {
                "user_action_id": raw_ticket,
                "capability": "reveal_in_file_manager",
                "arguments": {
                    "reference": {
                        "kind": "picker_selection",
                        "selection_handle": selected.selections[0].selection_handle,
                    }
                },
            }
        )
    assert replay.value.code == "E_HOST_BRIDGE_ACTION_REQUIRED"

    launched = session.invoke(
        {
            "user_action_id": _issue(session, "reveal_in_file_manager"),
            "capability": "reveal_in_file_manager",
            "arguments": {
                "reference": {
                    "kind": "picker_selection",
                    "selection_handle": selected.selections[0].selection_handle,
                }
            },
        }
    )
    assert launched.status == "launched"
    assert launched.selections == ()
    assert platform.launches == [
        HostLaunchCommand("explorer.exe", ("/select,", str(source.resolve())))
    ]
    assert platform.launches[0].shell is False


def test_cancel_and_selected_save_do_not_create_target(tmp_path: Path) -> None:
    target = tmp_path / "future.zniku"
    cancelled_platform = RecordingPlatform({"save_file": None})
    cancelled_session = _session(cancelled_platform, tmp_path)
    cancelled = cancelled_session.invoke(
        {
            "user_action_id": _issue(cancelled_session, "save_file"),
            "capability": "save_file",
            "arguments": {"suggested_name": target.name, "extensions": [".zniku"]},
        }
    )
    assert cancelled.status == "cancelled"
    assert cancelled.selections == ()
    assert not target.exists()

    selected_platform = RecordingPlatform({"save_file": (str(target),)})
    selected_session = _session(selected_platform, tmp_path)
    selected = selected_session.invoke(
        {
            "user_action_id": _issue(selected_session, "save_file"),
            "capability": "save_file",
            "arguments": {"suggested_name": target.name, "extensions": [".zniku"]},
        }
    )
    assert selected.status == "selected"
    assert selected.selections[0].path == str(target.resolve())
    assert not target.exists()
    assert selected_platform.launches == []


def test_dialog_result_count_and_path_kinds_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic")
    too_many = tuple(str(source) for _ in range(257))
    session = _session(RecordingPlatform({"open_files": too_many}), tmp_path)
    with pytest.raises(HostBridgeFailure) as count:
        session.invoke(
            {
                "user_action_id": _issue(session, "open_files"),
                "capability": "open_files",
                "arguments": {},
            }
        )
    assert count.value.code == "E_HOST_BRIDGE_DIALOG_RESULT"

    relative = _session(RecordingPlatform({"open_file": ("relative.mkv",)}), tmp_path)
    with pytest.raises(HostBridgeFailure) as path:
        relative.invoke(
            {
                "user_action_id": _issue(relative, "open_file"),
                "capability": "open_file",
                "arguments": {},
            }
        )
    assert path.value.code in {"E_HOST_BRIDGE_PATH", "E_HOST_BRIDGE_DIALOG_RESULT"}


def test_ticket_is_capability_bound_single_use_expiring_and_bounded(tmp_path: Path) -> None:
    now = [100.0]
    session = _session(RecordingPlatform(), tmp_path, clock=lambda: now[0])
    mismatch_id = _issue(session, "open_file")
    with pytest.raises(HostBridgeFailure) as mismatch:
        session.invoke(
            {
                "user_action_id": mismatch_id,
                "capability": "save_file",
                "arguments": {},
            }
        )
    assert mismatch.value.code == "E_HOST_BRIDGE_ACTION_MISMATCH"
    with pytest.raises(HostBridgeFailure) as replay:
        session.invoke(
            {
                "user_action_id": mismatch_id,
                "capability": "open_file",
                "arguments": {},
            }
        )
    assert replay.value.code == "E_HOST_BRIDGE_ACTION_REQUIRED"

    expired_id = _issue(session, "open_file")
    now[0] += 6
    with pytest.raises(HostBridgeFailure) as expired:
        session.invoke(
            {
                "user_action_id": expired_id,
                "capability": "open_file",
                "arguments": {},
            }
        )
    assert expired.value.code == "E_HOST_BRIDGE_ACTION_EXPIRED"

    for _ in range(64):
        _issue(session, "open_file")
    with pytest.raises(HostBridgeFailure) as bounded:
        _issue(session, "open_file")
    assert bounded.value.code == "E_HOST_BRIDGE_ACTION_LIMIT"
    now[0] += 6
    assert _issue(session, "open_file")


def test_artifact_reference_is_resolved_before_fixed_player_launch(tmp_path: Path) -> None:
    media = tmp_path / "result.mkv"
    media.write_bytes(b"synthetic")
    resolver = RecordingResolver(media)
    platform = RecordingPlatform()
    validated_paths: list[Path] = []
    session = HostBridgeSession(
        platform,
        studio_origin=_ORIGIN,
        path_resolver=resolver,
        player_media_validator=lambda path: validated_paths.append(path),
    )
    run_id = str(uuid4())
    artifact_id = str(uuid4())

    session.invoke(
        {
            "user_action_id": _issue(session, "open_with_system_player"),
            "capability": "open_with_system_player",
            "arguments": {
                "reference": {
                    "kind": "artifact",
                    "run_id": run_id,
                    "artifact_id": artifact_id,
                }
            },
        }
    )

    assert resolver.references == [
        ArtifactPathReference(kind="artifact", run_id=run_id, artifact_id=artifact_id)
    ]
    assert validated_paths == [media.resolve()]
    assert platform.launches == [
        HostLaunchCommand(
            "rundll32.exe",
            ("url.dll,FileProtocolHandler", str(media.resolve())),
        )
    ]


@pytest.mark.parametrize(
    "suffix",
    [".exe", ".bat", ".cmd", ".ps1", ".lnk", ".url", ".msi", ".txt"],
)
def test_player_rejects_active_or_unknown_suffix_before_probe(
    tmp_path: Path,
    suffix: str,
) -> None:
    target = tmp_path / f"payload{suffix}"
    target.write_bytes(b"synthetic")
    platform = RecordingPlatform()
    validated_paths: list[Path] = []
    session = HostBridgeSession(
        platform,
        studio_origin=_ORIGIN,
        path_resolver=RecordingResolver(target),
        player_media_validator=lambda path: validated_paths.append(path),
    )
    action_id = _issue(session, "open_with_system_player")

    with pytest.raises(HostBridgeFailure) as rejected:
        session.invoke(
            {
                "user_action_id": action_id,
                "capability": "open_with_system_player",
                "arguments": {
                    "reference": {
                        "kind": "artifact",
                        "run_id": str(uuid4()),
                        "artifact_id": str(uuid4()),
                    }
                },
            }
        )

    assert rejected.value.code == "E_HOST_BRIDGE_PLAYER_MEDIA"
    assert validated_paths == []
    assert platform.launches == []


def test_player_rejects_alternate_data_stream_shape() -> None:
    with pytest.raises(HostBridgeFailure) as rejected:
        host_bridge_module._validate_player_media_path(Path(r"C:\media\movie.mkv:payload.mp4"))

    assert rejected.value.code == "E_HOST_BRIDGE_PLAYER_MEDIA"


def test_player_default_validator_requires_recognizable_media_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "masquerading.mkv"
    media.write_bytes(b"synthetic")
    platform = RecordingPlatform()
    probe_calls: list[tuple[Path, bool]] = []

    def fake_probe(path: Path, *, count_frames: bool) -> SimpleNamespace:
        probe_calls.append((path, count_frames))
        return SimpleNamespace(video_streams=(), audio_streams=())

    monkeypatch.setattr(host_bridge_module, "probe_media", fake_probe)
    session = HostBridgeSession(
        platform,
        studio_origin=_ORIGIN,
        path_resolver=RecordingResolver(media),
    )
    action_id = _issue(session, "open_with_system_player")
    request = {
        "user_action_id": action_id,
        "capability": "open_with_system_player",
        "arguments": {
            "reference": {
                "kind": "artifact",
                "run_id": str(uuid4()),
                "artifact_id": str(uuid4()),
            }
        },
    }

    with pytest.raises(HostBridgeFailure) as rejected:
        session.invoke(request)

    assert rejected.value.code == "E_HOST_BRIDGE_PLAYER_MEDIA"
    assert probe_calls == [(media.resolve(), False)]
    assert platform.launches == []
    with pytest.raises(HostBridgeFailure) as replay:
        session.invoke(request)
    assert replay.value.code == "E_HOST_BRIDGE_ACTION_REQUIRED"


def test_project_service_resolver_requires_run_and_latest_handoff_binding(tmp_path: Path) -> None:
    artifact_id = str(uuid4())
    run_id = str(uuid4())
    node_run_id = str(uuid4())
    handoff_id = str(uuid4())
    artifact_path = tmp_path / "input.mkv"
    artifact_path.write_bytes(b"input")
    work_dir = tmp_path / "work" / run_id / node_run_id
    work_dir.mkdir(parents=True)
    handoff = SimpleNamespace(
        handoff_id=handoff_id,
        input_artifact_ids=(artifact_id,),
        output_targets=(
            SimpleNamespace(port_id="video", ordinal=None, path=str(work_dir / "out.mkv")),
        ),
    )
    node_run = SimpleNamespace(
        node_run_id=node_run_id,
        node_id="external",
        state=NodeRunState.WAITING_EXTERNAL,
        attempt=1,
        external_handoff=handoff,
        work_dir=str(work_dir),
    )
    detail = SimpleNamespace(
        run=SimpleNamespace(node_runs=(node_run,)),
        artifacts=(SimpleNamespace(artifact_id=artifact_id, path=str(artifact_path)),),
    )
    application = SimpleNamespace(
        work_root=tmp_path / "work",
        inspect_run_detail=lambda requested: detail if requested == run_id else None,
    )
    resolver = ProjectServiceHostPathResolver(application)

    assert (
        resolver.resolve(
            ArtifactPathReference(kind="artifact", run_id=run_id, artifact_id=artifact_id)
        )
        == artifact_path.resolve()
    )
    assert (
        resolver.resolve(
            HandoffPathReference.model_validate(
                {
                    "kind": "handoff",
                    "run_id": run_id,
                    "node_run_id": node_run_id,
                    "handoff_id": handoff_id,
                    "selector": {"role": "work_directory"},
                },
                strict=True,
            )
        )
        == work_dir.resolve()
    )

    node_run.state = NodeRunState.FAILED
    with pytest.raises(HostBridgeFailure) as stale:
        resolver.resolve(
            HandoffPathReference.model_validate(
                {
                    "kind": "handoff",
                    "run_id": run_id,
                    "node_run_id": node_run_id,
                    "handoff_id": handoff_id,
                    "selector": {"role": "work_directory"},
                },
                strict=True,
            )
        )
    assert stale.value.code == "E_HOST_BRIDGE_REFERENCE"


def test_http_capabilities_preflight_and_exact_authentication(tmp_path: Path) -> None:
    platform = RecordingPlatform()
    session = _session(platform, tmp_path)
    application = ProjectServiceApplication(work_root=tmp_path / "attempts")
    with _serve(application, session) as (base_url, _):
        status, payload, headers = _request(
            base_url,
            "/api/host-bridge/capabilities",
            method="OPTIONS",
        )
        assert status == 204
        assert payload is None
        assert headers["Access-Control-Allow-Origin"] == _ORIGIN
        assert headers["Access-Control-Allow-Methods"] == "GET, OPTIONS"
        assert "X-ZNIKU-Host-Token" in headers["Access-Control-Allow-Headers"]

        status, payload, headers = _request(
            base_url,
            "/api/host-bridge/capabilities",
            method="GET",
            token=session.token,
        )
        assert status == 200
        assert payload is not None
        assert payload["contract_version"] == "0.3.0"
        assert [item["capability"] for item in payload["capabilities"]] == list(HOST_CAPABILITIES)
        assert headers["Cache-Control"] == "no-store"

        for origin, token, code in (
            (None, session.token, "E_HOST_BRIDGE_ORIGIN"),
            ("http://localhost:4173", session.token, "E_HOST_BRIDGE_ORIGIN"),
            (_ORIGIN, None, "E_HOST_BRIDGE_SESSION"),
            (_ORIGIN, "x" * 43, "E_HOST_BRIDGE_SESSION"),
        ):
            status, failure, headers = _request(
                base_url,
                "/api/host-bridge/capabilities",
                method="GET",
                origin=origin,
                token=token,
            )
            assert status == 403
            assert failure is not None
            assert failure["error"]["code"] == code
            if origin != _ORIGIN:
                assert headers.get("Access-Control-Allow-Origin") is None


def test_formal_factory_binds_project_service_without_persisting_token(tmp_path: Path) -> None:
    application = ProjectServiceApplication(work_root=tmp_path / "attempts")
    session = create_project_service_host_bridge_session(
        application,
        studio_origin=_ORIGIN,
        platform=RecordingPlatform(),
    )
    assert len(session.token) >= 43
    with _serve(application, session) as (base_url, _):
        status, payload, _ = _request(
            base_url,
            "/api/host-bridge/capabilities",
            method="GET",
            token=session.token,
        )
    assert status == 200
    assert payload is not None
    assert session.token not in json.dumps(payload, ensure_ascii=False)
    assert list(application.work_root.iterdir()) == []


def test_http_two_step_routes_are_post_only_strict_and_single_use(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic")
    platform = RecordingPlatform({"open_file": (str(source),)})
    session = _session(platform, tmp_path)
    application = ProjectServiceApplication(work_root=tmp_path / "attempts")
    with _serve(application, session) as (base_url, _):
        status, ticket, headers = _request(
            base_url,
            "/api/host-bridge/user-actions",
            method="POST",
            token=session.token,
            payload={"capability": "open_file"},
        )
        assert status == 201
        assert ticket is not None
        assert ticket["expires_in_seconds"] == 5
        assert headers["Access-Control-Allow-Methods"] == "POST, OPTIONS"
        action_id = cast(str, ticket["user_action_id"])

        status, selected, _ = _request(
            base_url,
            "/api/host-bridge/invoke",
            method="POST",
            token=session.token,
            payload={
                "user_action_id": action_id,
                "capability": "open_file",
                "arguments": {},
            },
        )
        assert status == 200
        assert selected is not None
        assert selected["status"] == "selected"
        assert selected["selections"][0]["path"] == str(source.resolve())

        status, replay, _ = _request(
            base_url,
            "/api/host-bridge/invoke",
            method="POST",
            token=session.token,
            payload={
                "user_action_id": action_id,
                "capability": "open_file",
                "arguments": {},
            },
        )
        assert status == 409
        assert replay is not None
        assert replay["error"]["code"] == "E_HOST_BRIDGE_ACTION_REQUIRED"

        for path in ("/api/host-bridge/user-actions", "/api/host-bridge/invoke"):
            status, failure, _ = _request(
                base_url,
                path,
                method="GET",
                token=session.token,
            )
            assert status == 405
            assert failure is not None
            assert failure["error"]["code"] == "E_HOST_BRIDGE_METHOD"


def test_http_rejects_query_unknown_route_content_type_and_duplicate_headers(
    tmp_path: Path,
) -> None:
    session = _session(RecordingPlatform(), tmp_path)
    application = ProjectServiceApplication(work_root=tmp_path / "attempts")
    with _serve(application, session) as (base_url, server):
        status, failure, _ = _request(
            base_url,
            "/api/host-bridge/capabilities?token=secret",
            method="GET",
            token=session.token,
        )
        assert status == 404
        assert failure is not None
        assert failure["error"]["code"] == "E_HOST_BRIDGE_ROUTE"

        status, failure, _ = _request(
            base_url,
            "/api/host-bridge/unknown",
            method="POST",
            token=session.token,
            payload={},
        )
        assert status == 404
        assert failure is not None
        assert failure["error"]["code"] == "E_HOST_BRIDGE_ROUTE"

        status, failure, _ = _request(
            base_url,
            "/api/host-bridge/user-actions",
            method="POST",
            token=session.token,
            payload={"capability": "open_file"},
            content_type="text/plain",
        )
        assert status == 415
        assert failure is not None
        assert failure["error"]["code"] == "E_HOST_BRIDGE_CONTENT_TYPE"

        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.putrequest("GET", "/api/host-bridge/capabilities")
            connection.putheader("Origin", _ORIGIN)
            connection.putheader("X-ZNIKU-Host-Token", session.token)
            connection.putheader("X-ZNIKU-Host-Token", session.token)
            connection.endheaders()
            response = connection.getresponse()
            duplicate = cast(dict[str, Any], json.loads(response.read()))
        finally:
            connection.close()
        assert response.status == 403
        assert duplicate["error"]["code"] == "E_HOST_BRIDGE_SESSION"

        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.putrequest("GET", "/api/host-bridge/capabilities")
            connection.putheader("Origin", _ORIGIN)
            connection.putheader("Origin", _ORIGIN)
            connection.putheader("X-ZNIKU-Host-Token", session.token)
            connection.endheaders()
            response = connection.getresponse()
            duplicate = cast(dict[str, Any], json.loads(response.read()))
        finally:
            connection.close()
        assert response.status == 403
        assert duplicate["error"]["code"] == "E_HOST_BRIDGE_ORIGIN"

        oversized = b"x" * (64 * 1024 + 1)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.putrequest("POST", "/api/host-bridge/user-actions")
            connection.putheader("Origin", _ORIGIN)
            connection.putheader("X-ZNIKU-Host-Token", session.token)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(oversized)))
            connection.endheaders(oversized)
            response = connection.getresponse()
            too_large = cast(dict[str, Any], json.loads(response.read()))
        finally:
            connection.close()
        assert response.status == 413
        assert too_large["error"]["code"] == "E_HOST_BRIDGE_BODY_SIZE"


def test_unconfigured_host_bridge_is_explicit_and_has_no_cors(tmp_path: Path) -> None:
    application = ProjectServiceApplication(work_root=tmp_path / "attempts")
    with _serve(application, None) as (base_url, _):
        status, failure, headers = _request(
            base_url,
            "/api/host-bridge/capabilities",
            method="GET",
            token="x" * 43,
        )
    assert status == 503
    assert failure is not None
    assert failure["error"]["code"] == "E_HOST_BRIDGE_UNAVAILABLE"
    assert headers.get("Access-Control-Allow-Origin") is None


def test_lan_peer_is_rejected_before_any_host_action(tmp_path: Path) -> None:
    session = _session(RecordingPlatform(), tmp_path)
    with pytest.raises(HostBridgeFailure) as failure:
        session.authorize(
            client_host="192.168.1.20",
            origin=_ORIGIN,
            token=session.token,
        )
    assert failure.value.code == "E_HOST_BRIDGE_LAN_FORBIDDEN"


@pytest.mark.skipif(sys.platform != "win32", reason="v0.3.0 正式目标是 Windows")
def test_windows_platform_keeps_system_actions_when_tk_picker_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[object, dict[str, object]]] = []

    def fake_popen(command: object, **options: object) -> object:
        observed.append((command, options))
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    platform = WindowsHostPlatform()
    states = platform.capability_states()
    assert states["reveal_in_file_manager"] is None
    assert states["open_with_system_player"] is None
    platform.launch(HostLaunchCommand("explorer.exe", (r"C:\synthetic movie.mkv",)))
    assert observed[0][0] == ["explorer.exe", r"C:\synthetic movie.mkv"]
    assert observed[0][1]["shell"] is False


def test_windows_picker_concurrency_fails_busy_without_opening_window() -> None:
    platform = WindowsHostPlatform()
    assert platform._dialog_lock.acquire(blocking=False)
    try:
        with pytest.raises(HostBridgeFailure) as failure:
            platform.choose_paths("open_file", HostDialogArguments())
    finally:
        platform._dialog_lock.release()
    assert failure.value.code == "E_HOST_BRIDGE_DIALOG_BUSY"
