"""以隔离 HTTP 宿主和合成工程验证收件/数据维护的精确授权与失败关闭。

测试只监听临时 loopback 端口，原生选择与资源管理器均注入 RecordingPlatform；不读取真实
媒体、不运行用户服务。检查、预览、确认各自有明确边界，未知字段不接受任意磁盘路径。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from authoring_helpers import authoring_command
from test_handoff_import import _persistent_state
from test_handoff_inbox import InboxSetup, _inbox_setup, _put
from test_project_service_host_bridge import _request, _serve
from zniku.project_service import HostBridgeFailure

_INBOX = "/api/studio/handoff-inbox"
_STORAGE = "/api/studio/storage"
_ROUTES = tuple(f"{_INBOX}/{action}" for action in ("observe", "preview", "confirm")) + tuple(
    f"{_STORAGE}/{action}" for action in ("inspect", "configure", "preview", "confirm")
)


def test_data_routes_require_exact_origin_token_post_and_plain_route(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    before = _persistent_state(value.base)
    with _serve(value.base.application, value.base.session) as (base, _):
        for route in _ROUTES:
            status, _, _ = _request(base, route, method="POST", payload={})
            assert status == 403
            status, _, headers = _request(
                base,
                route,
                method="POST",
                payload={},
                token=value.base.session.token,
                origin="http://127.0.0.1:4174",
            )
            assert status == 403 and headers.get("Access-Control-Allow-Origin") is None
            status, _, _ = _request(
                base,
                route,
                method="POST",
                payload={},
                token="x" * 43,
            )
            assert status == 403
            status, _, _ = _request(base, route, method="GET", token=value.base.session.token)
            assert status == 405
            status, _, _ = _request(
                base,
                f"{route}?path=anything",
                method="POST",
                payload={},
                token=value.base.session.token,
            )
            assert status == 404
        status, _, _ = _request(
            base,
            f"{_INBOX}/unknown",
            method="POST",
            payload={},
            token=value.base.session.token,
        )
        assert status == 404
    assert _persistent_state(value.base) == before


@pytest.mark.parametrize("session", [None, "wrong"])
def test_data_routes_fail_without_launcher_session_or_current_project_session(
    tmp_path: Path,
    session: str | None,
) -> None:
    value = _inbox_setup(tmp_path)
    supplied = None if session is None else value.base.session
    with _serve(value.base.application, supplied) as (base, _):
        for route, payload in (
            (f"{_INBOX}/observe", {**value.binding(), "project_session_id": str(uuid4())}),
            (
                f"{_STORAGE}/inspect",
                {"contract_version": "0.3.0", "project_session_id": str(uuid4())},
            ),
        ):
            status, body, _ = _request(
                base,
                route,
                method="POST",
                payload=payload,
                token=value.base.session.token,
            )
            assert status == (503 if session is None else 409)
            assert body and body["error"]["code"] == (
                "E_HOST_BRIDGE_UNAVAILABLE" if session is None else "E_PROJECT_SESSION_CONFLICT"
            )


def test_inbox_http_moves_only_explicit_candidate_and_never_submits(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    before = _persistent_state(value.base)
    with _serve(value.base.application, value.base.session) as (base, _):
        status, _, _ = _request(
            base,
            f"{_INBOX}/observe",
            method="POST",
            token=value.base.session.token,
            payload={**value.binding(), "inbox_path": str(value.inbox())},
        )
        assert status == 422
        status, observed, _ = _request(
            base,
            f"{_INBOX}/observe",
            method="POST",
            token=value.base.session.token,
            payload=value.binding(),
        )
        assert status == 200 and observed and len(observed["candidates"]) == 1
        assert source.exists() and not value.base.target().exists()
        status, preview, _ = _request(
            base,
            f"{_INBOX}/preview",
            method="POST",
            token=value.base.session.token,
            payload={
                **value.binding(),
                "candidate_handle": observed["candidates"][0]["candidate_handle"],
            },
        )
        assert status == 200 and preview and preview["action"] == "move"
        confirm = {"contract_version": "0.3.0", "inbox_id": preview["inbox_id"], "overwrite": False}
        status, _, _ = _request(
            base,
            f"{_INBOX}/confirm",
            method="POST",
            token=value.base.session.token,
            payload={**confirm, "target_path": str(value.base.target("b"))},
        )
        assert status == 422 and source.exists()
        status, result, _ = _request(
            base,
            f"{_INBOX}/confirm",
            method="POST",
            token=value.base.session.token,
            payload=confirm,
        )
        assert status == 200 and result and result["status"] == "collected"
        status, failure, _ = _request(
            base,
            f"{_INBOX}/confirm",
            method="POST",
            token=value.base.session.token,
            payload=confirm,
        )
        assert status == 409 and failure and failure["error"]["code"] == "E_HANDOFF_INBOX_EXPIRED"
    assert not source.exists() and value.base.target().read_bytes() == b"899"
    assert not value.base.target("b").exists() and _persistent_state(value.base) == before


def test_incoming_directory_system_reference_requires_known_port_and_latest_handoff(
    tmp_path: Path,
) -> None:
    value = _inbox_setup(tmp_path)
    bound = value.binding()
    reference = {
        "kind": "handoff",
        "run_id": bound["run_id"],
        "node_run_id": bound["node_run_id"],
        "handoff_id": bound["handoff_id"],
        "selector": {"role": "incoming_directory", "port_id": "video", "ordinal": None},
    }
    action = value.base.session.issue_user_action({"capability": "reveal_in_file_manager"})
    result = value.base.session.invoke(
        {
            "capability": "reveal_in_file_manager",
            "user_action_id": action.user_action_id,
            "arguments": {"reference": reference},
        }
    )
    assert result.status == "launched" and len(value.base.platform.launches) == 1
    assert value.base.platform.launches[0].argv == (str(value.inbox()),)
    for selector in (
        {"role": "incoming_directory", "port_id": "other", "ordinal": None},
        {"role": "incoming_directory", "port_id": "video", "ordinal": 0},
        {"role": "incoming_directory", "port_id": "video", "path": str(tmp_path)},
    ):
        action = value.base.session.issue_user_action({"capability": "reveal_in_file_manager"})
        with pytest.raises(HostBridgeFailure) as failure:
            value.base.session.invoke(
                {
                    "capability": "reveal_in_file_manager",
                    "user_action_id": action.user_action_id,
                    "arguments": {"reference": {**reference, "selector": selector}},
                }
            )
        assert failure.value.code.startswith("E_HOST_BRIDGE_")
    assert len(value.base.platform.launches) == 1


def _complete(value: InboxSetup) -> None:
    for key, count in (("a", b"899"), ("b", b"902")):
        value.base.target(key).write_bytes(count)
        bound = value.binding(key)
        authoring_command(
            value.base.application,
            {
                "operation": "submit_external",
                "run_id": bound["run_id"],
                "node_run_id": bound["node_run_id"],
                "handoff_id": bound["handoff_id"],
            },
        )
        assert value.base.application.wait_until_idle()


def test_storage_http_readonly_check_and_explicit_migration_retain_originals(
    tmp_path: Path,
) -> None:
    value = _inbox_setup(tmp_path)
    _complete(value)
    destination = tmp_path / "archive-disk"
    destination.mkdir()
    value.base.platform.selections["select_directory"] = (str(destination),)
    action = value.base.session.issue_user_action({"capability": "select_directory"})
    selection = value.base.session.invoke(
        {
            "capability": "select_directory",
            "user_action_id": action.user_action_id,
            "arguments": {},
        }
    )
    request = {
        "contract_version": "0.3.0",
        "project_session_id": value.binding()["project_session_id"],
        "expected_storage_revision": value.base.application.inspect().storage_revision,
        "selection_handle": selection.selections[0].selection_handle,
    }
    old_target = value.base.target()
    with _serve(value.base.application, value.base.session) as (base, _):
        status, inspection, _ = _request(
            base,
            f"{_STORAGE}/inspect",
            method="POST",
            token=value.base.session.token,
            payload={
                "contract_version": "0.3.0",
                "project_session_id": request["project_session_id"],
            },
        )
        assert status == 200 and inspection and inspection["attempt_count"] == 2
        status, _, _ = _request(
            base,
            f"{_STORAGE}/configure",
            method="POST",
            token=value.base.session.token,
            payload=request,
        )
        assert status == 409  # 已运行工程不得直接切定位，必须走迁移。
        status, _, _ = _request(
            base,
            f"{_STORAGE}/preview",
            method="POST",
            token=value.base.session.token,
            payload={**request, "data_root": str(destination)},
        )
        assert status == 422
        status, preview, _ = _request(
            base,
            f"{_STORAGE}/preview",
            method="POST",
            token=value.base.session.token,
            payload=request,
        )
        assert status == 200 and preview and preview["originals_retained"]
        new_root = Path(preview["target"]["data_root"])
        assert not new_root.exists()
        confirm = {
            "contract_version": "0.3.0",
            "project_session_id": request["project_session_id"],
            "expected_storage_revision": request["expected_storage_revision"],
            "ticket_id": preview["ticket_id"],
        }
        status, migrated, _ = _request(
            base,
            f"{_STORAGE}/confirm",
            method="POST",
            token=value.base.session.token,
            payload=confirm,
        )
        assert status == 200 and migrated and migrated["storage"]["data_root"] == str(new_root)
        status, _, _ = _request(
            base,
            f"{_STORAGE}/confirm",
            method="POST",
            token=value.base.session.token,
            payload=confirm,
        )
        assert status >= 400
    assert old_target.read_bytes() == b"899"
    assert (
        new_root
        / "attempts"
        / value.base.nodes["a"].node_run_id.replace("-", "")
        / "outputs"
        / "enhancement.mov"
    ).read_bytes() == b"899"
