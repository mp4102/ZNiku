"""经真实隔离 HTTP 门禁验证整理、索引、重定位及缺盘维修会话，不启动用户宿主。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from authoring_helpers import authoring_command
from test_handoff_inbox import InboxSetup, _inbox_setup
from test_project_data_host import _complete
from test_project_service_host_bridge import _request, _serve


def _selection(value: InboxSetup, directory: Path) -> dict[str, Any]:
    value.base.platform.selections["select_directory"] = (str(directory),)
    action = value.base.session.issue_user_action({"capability": "select_directory"})
    selection = value.base.session.invoke(
        {"capability": "select_directory", "user_action_id": action.user_action_id, "arguments": {}}
    )
    state = value.base.application.inspect()
    return {
        "contract_version": "0.3.0",
        "project_session_id": state.project_session_id,
        "expected_storage_revision": state.storage_revision,
        "selection_handle": selection.selections[0].selection_handle,
    }


def _post(value: InboxSetup, base: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    status, body, _ = _request(
        base,
        f"/api/studio/storage/{action}",
        method="POST",
        token=value.base.session.token,
        payload=payload,
    )
    assert status == 200, body
    assert body is not None
    return body


@pytest.mark.parametrize("missing_source", [False, True])
def test_http_organize_index_and_restore_preserve_manual_bindings(
    tmp_path: Path, missing_source: bool
) -> None:
    value = _inbox_setup(tmp_path)
    _complete(value)
    original = value.base.target()
    historical = Path(value.base.nodes["a"].work_dir) / "handoff.json"
    historical_bytes = ('{"historical_target":' + repr(str(original)) + "}").encode("utf-8")
    historical.write_bytes(historical_bytes)
    destination = tmp_path / "disk"
    destination.mkdir()
    with _serve(value.base.application, value.base.session) as (base, _):
        request = _selection(value, destination)
        preview = _post(value, base, "organize-preview", request)
        assert preview["operation"] == "organize" and preview["target"]["layout"] == "readable"
        assert len(preview["path_mappings"]) == 2
        target_root = Path(preview["target"]["data_root"])
        assert not target_root.exists()
        confirmed = _post(
            value,
            base,
            "confirm",
            {key: val for key, val in request.items() if key != "selection_handle"}
            | {"ticket_id": preview["ticket_id"]},
        )
        assert confirmed["storage"]["layout"] == "readable" and original.read_bytes() == b"899"
        moved_a = next(
            item for item in preview["path_mappings"] if item["source"] == str(historical.parent)
        )
        assert (Path(moved_a["target"]) / "handoff.json").read_bytes() == historical_bytes
        original_run = value.base.application.inspect_run_detail(value.base.nodes["a"].run_id).run
        relocated_a = next(item for item in original_run.node_runs if item.node_id == "a")
        assert relocated_a.external_handoff is not None
        assert Path(relocated_a.external_handoff.output_targets[0].path).is_relative_to(target_root)
        state = value.base.application.inspect()
        index = _post(
            value,
            base,
            "index",
            {
                "contract_version": "0.3.0",
                "project_session_id": state.project_session_id,
                "expected_storage_revision": state.storage_revision,
            },
        )
        assert Path(index["path"]).is_file() and index["artifact_count"] == 2
        action = value.base.session.issue_user_action({"capability": "reveal_in_file_manager"})
        revealed = value.base.session.invoke(
            {
                "capability": "reveal_in_file_manager",
                "user_action_id": action.user_action_id,
                "arguments": {
                    "reference": {
                        "kind": "storage_index",
                        "project_session_id": state.project_session_id,
                    }
                },
            }
        )
        assert revealed.status == "launched"
        copied = tmp_path / "copied.data"
        shutil.copytree(target_root, copied)
        if missing_source:
            target_root.rename(tmp_path / "kept-original.data")
            # 实际重新打开缺盘工程：页面可进入维修，但未确认新位置前不能创建 Run。
            value.base.application.command(
                {"operation": "open_project", "path": str(value.base.project_path)}
            )
            assert value.base.application.inspect().error
            status, _, _ = _request(
                base,
                "/api/studio/storage/inspect",
                method="POST",
                token=value.base.session.token,
                payload={
                    "contract_version": "0.3.0",
                    "project_session_id": value.base.application.inspect().project_session_id,
                },
            )
            assert status == 200
        request = _selection(value, copied)
        restore = _post(value, base, "restore-preview", request)
        assert restore["operation"] == "restore"
        restored = _post(
            value,
            base,
            "confirm",
            {key: val for key, val in request.items() if key != "selection_handle"}
            | {"ticket_id": restore["ticket_id"]},
        )
        assert restored["storage"]["data_root"] == str(copied) and not restored["missing"]
        assert not value.base.application.inspect().error
    rerun = authoring_command(value.base.application, {"operation": "run_all"})
    assert value.base.application.wait_until_idle() and rerun.active_run_id
    latest = value.base.application.inspect_run_detail(rerun.active_run_id).run
    assert all(node.reused_from_result_id for node in latest.node_runs)
    for node in latest.node_runs:
        assert Path(node.work_dir).is_relative_to(copied)


def test_new_storage_actions_reject_raw_paths_and_wrong_cas_without_files(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    _complete(value)
    destination = tmp_path / "disk"
    destination.mkdir()
    request = _selection(value, destination)
    before = value.base.project_path.read_bytes()
    with _serve(value.base.application, value.base.session) as (base, _):
        for action in ("organize-preview", "restore-preview"):
            status, _, _ = _request(
                base,
                f"/api/studio/storage/{action}",
                method="POST",
                token=value.base.session.token,
                payload={**request, "data_root": str(destination)},
            )
            assert status == 422
        status, _, _ = _request(
            base,
            "/api/studio/storage/organize-preview",
            method="POST",
            token=value.base.session.token,
            payload={**request, "expected_storage_revision": 999},
        )
        assert status == 409
        status, _, _ = _request(
            base,
            "/api/studio/storage/restore-preview",
            method="POST",
            token=value.base.session.token,
            payload={**request, "selection_handle": None},
        )
        assert status == 422
    assert value.base.project_path.read_bytes() == before and not list(destination.iterdir())
