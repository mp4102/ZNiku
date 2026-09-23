"""以临时 DataFile 验证多输出人工节点的分批收件、整节点检查和显式提交。

测试不操作真实工程、GUI 或媒体；同名错件、旧会话、文件换代与发布失败都应保持
Runtime 等待状态且保留用户原件。只有正式 Submit 才一次登记完整 NodeResult。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from authoring_helpers import authoring_command
from test_handoff_import import Setup, _persistent_state, _setup
from test_project_service_host_bridge import _request, _serve
from zniku.project_service import HostBridgeFailure, ProjectServiceError, handoff_batch
from zniku.project_service.handoff_batch import (
    HandoffBatchCheckEnvelope,
    HandoffBatchManager,
    HandoffBatchPreviewEnvelope,
)
from zniku.project_service.host_bridge import HostCapability
from zniku.runtime import NodeValidatorContext, NodeValidatorResult, RunnerError
from zniku.runtime.runner import RunnerFailureReason


def _validate(context: NodeValidatorContext) -> NodeValidatorResult:
    assert len(context.outputs) == len(context.request.definition.output_ports)
    for output in context.outputs:
        if output.path.read_text(encoding="utf-8") != context.request.node.parameters["expected"]:
            raise RunnerError(
                "E_BATCH_SYNTHETIC",
                RunnerFailureReason.VALIDATOR_FAILED,
                "合成来件不符合本节点要求",
            )
    return NodeValidatorResult(passed=True)


def _batch(tmp_path: Path, **kwargs: Any) -> Setup:
    return _setup(
        tmp_path, multiple_outputs=True, validator=kwargs.pop("validator", _validate), **kwargs
    )


def _binding(value: Setup, node_id: str = "a") -> dict[str, Any]:
    node = value.nodes[node_id]
    assert node.external_handoff
    return {
        "contract_version": "0.3.0",
        "project_session_id": value.application.inspect().project_session_id,
        "run_id": node.run_id,
        "node_run_id": node.node_run_id,
        "handoff_id": node.external_handoff.handoff_id,
    }


def _sources(value: Setup, root: Path, *, content: str = "899") -> tuple[Path, ...]:
    root.mkdir(exist_ok=True)
    handoff = value.nodes["a"].external_handoff
    assert handoff
    paths = tuple(root / Path(target.path).name for target in handoff.output_targets)
    for path in paths:
        path.write_text(content, encoding="utf-8")
    return paths


def _preview(
    manager: HandoffBatchManager, value: Setup, paths: tuple[Path, ...], *, directory: bool = False
) -> HandoffBatchPreviewEnvelope:
    capability: HostCapability = "select_directory" if directory else "open_files"
    handles = []
    if paths:
        value.platform.selections[capability] = tuple(str(path) for path in paths)
        action = value.session.issue_user_action({"capability": capability})
        selected = value.session.invoke(
            {"capability": capability, "user_action_id": action.user_action_id, "arguments": {}}
        )
        handles = [item.selection_handle for item in selected.selections]
    return manager.preview(
        {**_binding(value), "selection_handles": handles},
        session=value.session,
        application=value.application,
    )


def _confirm(
    manager: HandoffBatchManager,
    value: Setup,
    preview: HandoffBatchPreviewEnvelope,
    *,
    overwrite: bool = False,
) -> Any:
    return manager.confirm(
        {
            "contract_version": "0.3.0",
            "batch_id": preview.batch_id,
            "items": [
                {
                    "port_id": match.port_id,
                    "candidate_handle": match.candidate_handle,
                    "overwrite": overwrite,
                }
                for match in preview.matches
                if match.candidate_handle
            ],
        },
        session=value.session,
        application=value.application,
    )


def _check(
    manager: HandoffBatchManager, value: Setup, *, overwrite: tuple[str, ...] = ()
) -> HandoffBatchCheckEnvelope:
    return manager.check(
        {**_binding(value), "overwrite_ports": list(overwrite)},
        session=value.session,
        application=value.application,
    )


def test_reselecting_exact_received_file_reports_no_op_without_overwrite(tmp_path: Path) -> None:
    """相同收件原位选回仅展示无操作，不要求假覆盖，也不重新复制文件。"""
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    received = _confirm(
        manager, value, _preview(manager, value, _sources(value, tmp_path / "external"))
    )
    path = Path(received.rows[0].incoming_path)
    before = path.stat()
    preview = _preview(manager, value, (path,))
    candidate = preview.candidates[0]
    assert candidate.path == str(path)
    assert candidate.unchanged_port_ids == (received.rows[0].port_id,)
    assert preview.rows[0].display_label
    _confirm(manager, value, preview)
    assert path.stat().st_ino == before.st_ino
    assert path.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize("readable", [False, True])
def test_partial_collection_stays_waiting_and_full_check_precedes_single_submit(
    tmp_path: Path, readable: bool
) -> None:
    value = _batch(tmp_path, readable=readable)
    manager = HandoffBatchManager()
    sources = _sources(value, tmp_path / "external")
    before = _persistent_state(value)
    first = _confirm(manager, value, _preview(manager, value, sources[:1]))
    assert not first.complete
    assert sum(row.collected for row in first.rows) == 1
    assert all(not Path(row.target_path).exists() for row in first.rows)
    missing = _check(manager, value)
    assert not missing.published and missing.readiness is None
    assert missing.validation_error and missing.validation_error.code == "E_HANDOFF_BATCH_MISSING"
    assert _persistent_state(value) == before
    second = _confirm(manager, value, _preview(manager, value, sources[1:]))
    assert second.complete
    checked = _check(manager, value)
    assert checked.published and checked.readiness and checked.readiness.ready_for_submit
    assert all(row.target_exists and not row.collected for row in checked.rows)
    assert all(path.read_text(encoding="utf-8") == "899" for path in sources)
    assert _persistent_state(value) == before
    binding = _binding(value)
    value.application.command(
        {
            "operation": "submit_external",
            **{key: binding[key] for key in ("run_id", "node_run_id", "handoff_id")},
        }
    )
    assert value.application.wait_until_idle()
    node = next(
        item
        for item in value.application.inspect_run_detail(binding["run_id"]).run.node_runs
        if item.node_id == "a"
    )
    assert node.state.value == "completed" and len(node.output_artifact_ids) == 2


def test_directory_and_shared_inbox_preview_are_read_only_and_ignore_subdirectories(
    tmp_path: Path,
) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    sources = _sources(value, tmp_path / "external")
    (sources[0].parent / "nested").mkdir()
    (sources[0].parent / "nested" / sources[0].name).write_text("wrong", encoding="utf-8")
    before = _persistent_state(value)
    preview = _preview(manager, value, (sources[0].parent,), directory=True)
    assert len(preview.candidates) == 2 and all(
        match.state == "matched" for match in preview.matches
    )
    assert all(not row.collected and not row.target_exists for row in preview.rows)
    _sources(value, Path(preview.inbox_path))
    inbox = _preview(manager, value, ())
    assert len(inbox.candidates) == 2
    assert _persistent_state(value) == before
    _confirm(manager, value, inbox)
    assert all(candidate.action == "move" for candidate in inbox.candidates)
    assert all(not (Path(inbox.inbox_path) / source.name).exists() for source in sources)


def test_exact_names_only_and_duplicate_names_need_explicit_mapping(tmp_path: Path) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    sources = _sources(value, tmp_path / "external")
    other = tmp_path / "copy" / sources[0].name
    other.parent.mkdir()
    other.write_text("899", encoding="utf-8")
    preview = _preview(manager, value, (sources[0], other, value.source))
    assert preview.matches[0].state == "ambiguous" and preview.matches[0].candidate_handle is None
    assert preview.matches[1].state == "missing"
    result = manager.confirm(
        {
            "contract_version": "0.3.0",
            "batch_id": preview.batch_id,
            "items": [
                {
                    "port_id": preview.rows[0].port_id,
                    "candidate_handle": preview.candidates[0].candidate_handle,
                    "overwrite": False,
                }
            ],
        },
        session=value.session,
        application=value.application,
    )
    assert result.rows[0].collected and not result.complete


def test_duplicate_source_or_mapping_and_unknown_path_fields_fail_closed(tmp_path: Path) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    sources = _sources(value, tmp_path / "external")
    with pytest.raises(HostBridgeFailure):
        _preview(manager, value, (sources[0], sources[0]))
    preview = _preview(manager, value, sources)
    item = {
        "port_id": preview.rows[0].port_id,
        "candidate_handle": preview.candidates[0].candidate_handle,
        "overwrite": False,
    }
    with pytest.raises(HostBridgeFailure) as failure:
        manager.confirm(
            {"contract_version": "0.3.0", "batch_id": preview.batch_id, "items": [item, item]},
            session=value.session,
            application=value.application,
        )
    assert failure.value.code == "E_HANDOFF_BATCH_DUPLICATE"
    for field in ("path", "source_path", "inbox_path", "target_path"):
        with pytest.raises(HostBridgeFailure) as failure:
            manager.observe(
                {**_binding(value), field: str(tmp_path)},
                session=value.session,
                application=value.application,
            )
        assert failure.value.code == "E_HANDOFF_BATCH_REQUEST"


def test_invalid_full_batch_retains_all_incoming_and_old_outputs(tmp_path: Path) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    sources = _sources(value, tmp_path / "external", content="wrong")
    imported = _confirm(manager, value, _preview(manager, value, sources))
    for row in imported.rows:
        Path(row.target_path).write_text("old", encoding="utf-8")
    before = _persistent_state(value)
    failure = _check(manager, value, overwrite=tuple(row.port_id for row in imported.rows))
    assert not failure.published and failure.validation_error and failure.readiness is None
    assert all(
        Path(row.incoming_path).read_text(encoding="utf-8") == "wrong" for row in failure.rows
    )
    assert all(Path(row.target_path).read_text(encoding="utf-8") == "old" for row in failure.rows)
    assert _persistent_state(value) == before
    assert not list(Path(value.nodes["a"].work_dir).glob(".handoff-batch-*"))


def test_overwrite_incoming_and_outputs_need_separate_explicit_permissions(tmp_path: Path) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    sources = _sources(value, tmp_path / "external")
    initial = _confirm(manager, value, _preview(manager, value, sources))
    with pytest.raises(HostBridgeFailure) as failure:
        _confirm(manager, value, _preview(manager, value, sources))
    assert failure.value.code == "E_HANDOFF_BATCH_OVERWRITE_REQUIRED"
    _confirm(manager, value, _preview(manager, value, sources), overwrite=True)
    for row in initial.rows:
        Path(row.target_path).write_text("old", encoding="utf-8")
    with pytest.raises(HostBridgeFailure) as failure:
        _check(manager, value)
    assert failure.value.code == "E_HANDOFF_BATCH_OVERWRITE_REQUIRED"
    result = _check(manager, value, overwrite=tuple(row.port_id for row in initial.rows))
    assert result.published
    assert all(Path(row.target_path).read_text(encoding="utf-8") == "899" for row in result.rows)
    assert not list(Path(value.nodes["a"].work_dir).glob(".handoff-batch-*"))


@pytest.mark.parametrize("change", ["source", "incoming", "target"])
def test_changed_files_reject_confirmation_without_overwriting(tmp_path: Path, change: str) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    sources = _sources(value, tmp_path / "external")
    preview = _preview(manager, value, sources)
    changed = (
        sources[0]
        if change == "source"
        else Path(
            preview.rows[0].incoming_path if change == "incoming" else preview.rows[0].target_path
        )
    )
    changed.write_text("changed", encoding="utf-8")
    with pytest.raises(HostBridgeFailure) as failure:
        _confirm(manager, value, preview)
    assert failure.value.code == "E_HANDOFF_BATCH_CHANGED"
    assert changed.read_text(encoding="utf-8") == "changed"


def test_expiry_reopen_and_latest_attempt_binding(tmp_path: Path) -> None:
    value = _batch(tmp_path)
    clock = [0.0]
    manager = HandoffBatchManager(clock=lambda: clock[0])
    sources = _sources(value, tmp_path / "external")
    preview = _preview(manager, value, sources)
    clock[0] = 301.0
    with pytest.raises(HostBridgeFailure) as failure:
        _confirm(manager, value, preview)
    assert failure.value.code == "E_HANDOFF_BATCH_EXPIRED"
    preview = _preview(manager, value, sources)
    value.application.command({"operation": "open_project", "path": str(value.project_path)})
    with pytest.raises(ProjectServiceError, match="SESSION_CONFLICT"):
        _confirm(manager, value, preview)
    for field in ("run_id", "node_run_id", "handoff_id", "project_session_id"):
        with pytest.raises(ProjectServiceError):
            manager.observe(
                {**_binding(value), field: str(uuid4())},
                session=value.session,
                application=value.application,
            )


def test_publication_failure_rolls_back_old_outputs_and_keeps_incoming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    imported = _confirm(
        manager, value, _preview(manager, value, _sources(value, tmp_path / "external"))
    )
    for row in imported.rows:
        Path(row.target_path).write_text("old", encoding="utf-8")
    original = handoff_batch._move_no_replace
    failing = Path(imported.rows[1].target_path)

    def broken(source: Any, target: Any, identity: Any) -> None:
        if Path(target) == failing:
            raise OSError("simulated publication failure")
        original(source, target, identity)

    monkeypatch.setattr(handoff_batch, "_move_no_replace", broken)
    with pytest.raises(HostBridgeFailure) as failure:
        _check(manager, value, overwrite=tuple(row.port_id for row in imported.rows))
    assert failure.value.code == "E_HANDOFF_BATCH_IO"
    assert all(Path(row.target_path).read_text(encoding="utf-8") == "old" for row in imported.rows)
    assert all(
        Path(row.incoming_path).read_text(encoding="utf-8") == "899" for row in imported.rows
    )


def test_full_validator_runs_once_and_status_remains_readable(tmp_path: Path) -> None:
    started, finish = threading.Event(), threading.Event()
    calls = []

    def validator(context: NodeValidatorContext) -> NodeValidatorResult:
        calls.append(len(context.outputs))
        started.set()
        assert finish.wait(timeout=5)
        return _validate(context)

    value = _batch(tmp_path, validator=validator)
    manager = HandoffBatchManager()
    _confirm(manager, value, _preview(manager, value, _sources(value, tmp_path / "external")))
    results = []
    worker = threading.Thread(target=lambda: results.append(_check(manager, value)))
    worker.start()
    try:
        assert started.wait(timeout=5)
        assert value.application.inspect().active_operation == "import_external"
        with pytest.raises(ProjectServiceError, match="BUSY"):
            manager.observe(_binding(value), session=value.session, application=value.application)
    finally:
        finish.set()
        worker.join(timeout=5)
    assert calls == [2] and results[0].published


def test_batch_http_uses_host_authorization_and_full_flow(tmp_path: Path) -> None:
    value = _batch(tmp_path)
    with _serve(value.application, value.session) as (base, _):
        for action in ("observe", "preview", "confirm", "check"):
            route = f"/api/studio/handoff-batch/{action}"
            assert _request(base, route, method="POST", payload={})[0] == 403
            assert _request(base, route, method="GET", token=value.session.token)[0] == 405
            assert (
                _request(
                    base, route + "?path=x", method="POST", token=value.session.token, payload={}
                )[0]
                == 404
            )
        route = "/api/studio/handoff-batch/observe"
        status, observed, _ = _request(
            base, route, method="POST", token=value.session.token, payload=_binding(value)
        )
        assert status == 200 and observed and not observed["complete"]
        _sources(value, Path(observed["inbox_path"]))
        status, preview, _ = _request(
            base,
            "/api/studio/handoff-batch/preview",
            method="POST",
            token=value.session.token,
            payload={**_binding(value), "selection_handles": []},
        )
        assert status == 200 and preview
        status, result, _ = _request(
            base,
            "/api/studio/handoff-batch/confirm",
            method="POST",
            token=value.session.token,
            payload={
                "contract_version": "0.3.0",
                "batch_id": preview["batch_id"],
                "items": [
                    {
                        "port_id": row["port_id"],
                        "candidate_handle": row["candidate_handle"],
                        "overwrite": False,
                    }
                    for row in preview["matches"]
                ],
            },
        )
        assert status == 200 and result and result["complete"]
        status, checked, _ = _request(
            base,
            "/api/studio/handoff-batch/check",
            method="POST",
            token=value.session.token,
            payload={**_binding(value), "overwrite_ports": []},
        )
        assert (
            status == 200
            and checked
            and checked["published"]
            and checked["readiness"]["ready_for_submit"]
        )


def test_shared_inbox_reveal_is_bound_to_current_handoff(tmp_path: Path) -> None:
    value = _batch(tmp_path)
    binding = _binding(value)
    reference = {
        "kind": "handoff",
        **{key: binding[key] for key in ("run_id", "node_run_id", "handoff_id")},
        "selector": {"role": "batch_incoming_directory"},
    }
    action = value.session.issue_user_action({"capability": "reveal_in_file_manager"})
    value.session.invoke(
        {
            "capability": "reveal_in_file_manager",
            "user_action_id": action.user_action_id,
            "arguments": {"reference": reference},
        }
    )
    assert value.platform.launches[-1].argv == (str(Path(value.nodes["a"].work_dir) / "incoming"),)


def test_shared_inbox_collection_does_not_copy_large_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    observed = manager.observe(
        _binding(value), session=value.session, application=value.application
    )
    sources = _sources(value, Path(observed.inbox_path))
    original_inodes = [path.stat().st_ino for path in sources]

    def no_copy(*args: object, **kwargs: object) -> None:
        raise AssertionError("同任务收件根必须原位收纳，不能复制第二份大文件")

    monkeypatch.setattr("zniku.project_service.handoff_batch.shutil.copyfileobj", no_copy)
    result = _confirm(manager, value, _preview(manager, value, ()))
    assert result.complete and all(not path.exists() for path in sources)
    assert [Path(row.incoming_path).stat().st_ino for row in result.rows] == original_inodes
    assert _check(manager, value).published


def test_copy_failure_keeps_originals_and_reports_each_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    sources = _sources(value, tmp_path / "external")
    before = _persistent_state(value)
    preview = _preview(manager, value, sources)

    def failed_copy(*args: object, **kwargs: object) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr("zniku.project_service.handoff_batch.shutil.copyfileobj", failed_copy)
    result = _confirm(manager, value, preview)
    assert all(item.status == "failed" for item in result.results)
    assert not result.complete and all(
        path.read_text(encoding="utf-8") == "899" for path in sources
    )
    assert _persistent_state(value) == before
    assert not list(Path(value.nodes["a"].work_dir).glob(".handoff-batch-*"))


def test_validator_file_change_is_rejected_and_preserves_changed_incoming(
    tmp_path: Path,
) -> None:
    def changing(context: NodeValidatorContext) -> NodeValidatorResult:
        context.outputs[0].path.write_text("changed-during-validation", encoding="utf-8")
        return NodeValidatorResult(passed=True)

    value = _batch(tmp_path, validator=changing)
    manager = HandoffBatchManager()
    received = _confirm(
        manager, value, _preview(manager, value, _sources(value, tmp_path / "external"))
    )
    before = _persistent_state(value)
    with pytest.raises(HostBridgeFailure) as failure:
        _check(manager, value)
    assert failure.value.code == "E_HANDOFF_BATCH_CHANGED"
    assert (
        Path(received.rows[0].incoming_path).read_text(encoding="utf-8")
        == "changed-during-validation"
    )
    assert all(not Path(row.target_path).exists() for row in received.rows)
    assert not list(Path(value.nodes["a"].work_dir).glob(".handoff-batch-*"))
    assert _persistent_state(value) == before


def test_superseded_attempt_cannot_accept_pending_batch(tmp_path: Path) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    preview = _preview(manager, value, _sources(value, tmp_path / "external"))
    authoring_command(
        value.application,
        {"operation": "rerun_from_here", "run_id": value.nodes["a"].run_id, "node_id": "a"},
    )
    assert value.application.wait_until_idle()
    with pytest.raises(ProjectServiceError):
        _confirm(manager, value, preview)
    assert all(not Path(row.incoming_path).exists() for row in preview.rows)


def test_one_output_batch_and_rechecking_published_files_use_same_workflow(tmp_path: Path) -> None:
    value = _setup(tmp_path, validator=_validate)
    manager = HandoffBatchManager()
    sources = _sources(value, tmp_path / "external")
    received = _confirm(manager, value, _preview(manager, value, sources))
    assert len(received.rows) == 1 and received.complete
    assert _check(manager, value).published
    again = _check(manager, value)
    assert again.published and again.readiness and again.readiness.ready_for_submit
    assert not again.rows[0].collected and again.rows[0].target_exists


def test_formal_submit_failure_fails_whole_node_without_partial_artifacts(tmp_path: Path) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    _confirm(manager, value, _preview(manager, value, _sources(value, tmp_path / "external")))
    result = _check(manager, value)
    Path(result.rows[1].target_path).write_text("invalidated-after-check", encoding="utf-8")
    binding = _binding(value)
    value.application.command(
        {
            "operation": "submit_external",
            **{key: binding[key] for key in ("run_id", "node_run_id", "handoff_id")},
        }
    )
    assert value.application.wait_until_idle()
    node = next(
        item
        for item in value.application.inspect_run_detail(binding["run_id"]).run.node_runs
        if item.node_id == "a"
    )
    assert node.state.value == "failed" and not node.output_artifact_ids


@pytest.mark.skipif(os.name != "nt", reason="验证 Windows 原生 rename 的 no-replace 合同")
@pytest.mark.parametrize("shared_inbox", [False, True])
def test_windows_batch_works_when_filesystem_rejects_hardlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared_inbox: bool
) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    observed = manager.observe(
        _binding(value), session=value.session, application=value.application
    )
    root = Path(observed.inbox_path) if shared_inbox else tmp_path / "external"
    sources = _sources(value, root)

    def unsupported_link(*args: object, **kwargs: object) -> None:
        raise OSError("filesystem does not support hard links")

    monkeypatch.setattr(os, "link", unsupported_link)
    result = _confirm(manager, value, _preview(manager, value, () if shared_inbox else sources))
    assert result.complete and all(item.status == "collected" for item in result.results)
    assert _check(manager, value).published
    assert all(path.exists() != shared_inbox for path in sources)


def test_publication_never_overwrites_target_appearing_after_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _batch(tmp_path)
    manager = HandoffBatchManager()
    imported = _confirm(
        manager, value, _preview(manager, value, _sources(value, tmp_path / "external"))
    )
    destination = Path(imported.rows[0].target_path)
    original = handoff_batch._move_no_replace

    def competing_target(source: Path, target: Path, identity: Any) -> None:
        if target == destination:
            target.write_text("concurrent-file", encoding="utf-8")
        original(source, target, identity)

    monkeypatch.setattr(handoff_batch, "_move_no_replace", competing_target)
    with pytest.raises(HostBridgeFailure):
        _check(manager, value)
    assert destination.read_text(encoding="utf-8") == "concurrent-file"
    assert all(
        Path(row.incoming_path).read_text(encoding="utf-8") == "899" for row in imported.rows
    )
