"""短合成工作源的版本路由、显式确认、准入重试和单次诊断成本回归。"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from authoring_helpers import authoring_command
from test_color_prepared_service import _unspecified_media
from test_project_service_host import _request, _serve
from test_source_preparation_kernel import _tool
from test_source_preparation_kernel import media as media
from zniku.desktop.server import build_desktop_application
from zniku.project import ProjectStore
from zniku.project_service.prepared_color import ColorPreparedSourceError
from zniku.project_service.prepared_color_application import dispatch
from zniku.project_service.work import (
    WorkError,
    WorkFullEnvelope,
    WorkOperationEnvelope,
    WorkViewEnvelope,
)
from zniku.runtime import NodeRunState, RunState
from zniku.source_preparation import work_definitions
from zniku.source_preparation.progress import check_cancellation


def _create(tmp_path: Path, source: Path) -> tuple[Any, Path]:
    app = build_desktop_application(tmp_path / "unused")
    path = tmp_path / "work.zniku"
    dispatch(
        app,
        "create",
        {
            "contract_version": "0.3.4-work.1",
            "project_path": str(path),
            "project_id": "synthetic-work",
            "project_name": "合成普通工作源",
            "source_path": str(source),
        },
    )
    return app, path


def _view_request(app: Any) -> dict[str, Any]:
    status = app.inspect()
    return {
        "contract_version": "0.3.4-work.1",
        "project_session_id": status.project_session_id,
        "run_id": status.active_run_id,
    }


def _view(app: Any) -> WorkViewEnvelope:
    result = dispatch(app, "view", _view_request(app))
    assert isinstance(result, WorkViewEnvelope)
    return result


def _run(app: Any) -> WorkViewEnvelope:
    authoring_command(app, {"operation": "run_all"})
    app.wait_until_idle(timeout=40)
    return _view(app)


def _choose(app: Any, route: str = "direct", **values: Any) -> None:
    dispatch(
        app,
        "choose",
        {
            **_view_request(app),
            "expected_storage_revision": app.inspect().storage_revision,
            "route": route,
            **values,
        },
    )


def test_direct_reuses_single_diagnostic_and_expands_same_graph(
    media: Path, tmp_path: Path
) -> None:
    widescreen = tmp_path / "wide.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-vf",
            "scale=128:72,setsar=1/1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            str(widescreen),
        ]
    )
    app, path = _create(tmp_path, widescreen)
    report = _run(app)
    assert report.decision == "direct" and report.inspection_scope == "frames_eof", report
    assert report.source_frame_count == 30 and report.state == "needs_choice"
    impacts = {item.id: item.description for item in report.impacts}
    assert "1.000 秒" in impacts["timing"] and "30/1 FPS" in impacts["timing"]
    assert "GiB" in impacts["storage"] and "未压缩参考预算" in impacts["storage"]
    assert "不是实际 FFV1 体积" in impacts["storage"]
    diagnostic = next(
        n
        for n in ProjectStore.open(path).load().project.graph.nodes
        if n.node_id.endswith("diagnostics")
    )
    assert diagnostic.parameters["target_frame_rate"] is None
    _choose(app)
    ready = _run(app)
    assert ready.state == "ready", ready
    assert ready.current_settings.target_frame_rate == "30/1"
    detail = app.inspect_run_detail(ready.run_id)
    diagnosis_attempt = next(n for n in detail.run.node_runs if n.node_id.endswith("diagnostics"))
    assert diagnosis_attempt.reused_from_result_id is not None
    request = {
        **_view_request(app),
        "preparation_run_id": ready.run_id,
        "expected_storage_revision": app.inspect().storage_revision,
        "processing": {
            "enhancement": {"model_name": "Synthetic"},
            "program_encode": {"encoder": "cpu"},
        },
        "publication": {
            "output_root": str(tmp_path),
            "title": "Example",
            "year": "2026",
            "overwrite": False,
        },
    }
    request.pop("run_id")
    before = path.read_bytes()
    preview = dispatch(app, "full-preview", request)
    assert isinstance(preview, WorkFullEnvelope) and preview.plan.chapter_count == 1
    assert path.read_bytes() == before
    dispatch(app, "expand", request)
    snapshot = ProjectStore.open(path).load()
    assert len(snapshot.project.graph.nodes) == preview.node_count
    assert all(
        n.definition_version == "0.3.4-work.1"
        for n in snapshot.project.graph.nodes
        if n.node_id != "output"
    )
    with pytest.raises(WorkError, match="GRAPH_EDITED"):
        dispatch(app, "view", _view_request(app))


def test_generic_four_three_reference_is_valid_but_creator_preset_rejects(
    media: Path, tmp_path: Path
) -> None:
    """源合同不被预设绑死，预览拒绝发生在写入下游图之前。"""
    app, path = _create(tmp_path, media)
    _run(app)
    _choose(app)
    ready = _run(app)
    assert ready.state == "ready"
    before = path.read_bytes()
    request = {
        "contract_version": "0.3.4-work.1",
        "project_session_id": app.inspect().project_session_id,
        "expected_storage_revision": app.inspect().storage_revision,
        "preparation_run_id": ready.run_id,
        "processing": {
            "enhancement": {"model_name": "Synthetic"},
            "program_encode": {"encoder": "cpu"},
        },
        "publication": {
            "output_root": str(tmp_path),
            "title": "Example",
            "year": "2026",
            "overwrite": False,
        },
    }
    with pytest.raises(WorkError, match="PRESET_GEOMETRY"):
        dispatch(app, "full-preview", request)
    with pytest.raises(WorkError, match="PRESET_GEOMETRY"):
        dispatch(app, "expand", request)
    assert path.read_bytes() == before


def test_missing_signal_requires_real_confirmation_and_no_source_rewrite(tmp_path: Path) -> None:
    source = _unspecified_media(tmp_path)
    before = source.read_bytes()
    app, path = _create(tmp_path, source)
    report = _run(app)
    assert report.decision == "direct" and report.color_interpretation_available
    assert any(
        c.id == "color_interpretation" and "direct" in c.routes
        for c in report.required_confirmations
    )
    project_before = path.read_bytes()
    with pytest.raises(WorkError, match="CONFIRMATION_REQUIRED"):
        _choose(app, interpretation_policy="operator_confirmed_bt709_limited_left")
    with pytest.raises(WorkError, match="COLOR_INTERPRETATION_REQUIRED"):
        _choose(app, confirmations=["color_interpretation"])
    assert project_before == path.read_bytes()
    _choose(
        app,
        interpretation_policy="operator_confirmed_bt709_limited_left",
        confirmations=["color_interpretation"],
    )
    assert _run(app).state == "ready"
    assert source.read_bytes() == before


def test_external_new_reference_missing_signal_retries_only_admission(
    media: Path, tmp_path: Path
) -> None:
    small_candidate = _unspecified_media(tmp_path)
    candidate = tmp_path / "new-wide-reference.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(small_candidate),
            "-vf",
            "scale=128:72,setsar=1/1,setparams=color_primaries=unknown:color_trc=unknown:colorspace=unknown",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(candidate),
        ]
    )
    app, _path = _create(tmp_path, media)
    _run(app)
    _choose(app, route="external", confirmations=["external_reference"], external_format="mkv")
    waiting_view = _run(app)
    assert waiting_view.state == "waiting_external"
    detail = app.inspect_run_detail(waiting_view.run_id)
    waiting = next(n for n in detail.run.node_runs if n.state is NodeRunState.WAITING_EXTERNAL)
    assert waiting.external_handoff is not None
    shutil.copyfile(candidate, waiting.external_handoff.output_targets[0].path)
    authoring_command(
        app,
        {
            "operation": "submit_external",
            "run_id": waiting_view.run_id,
            "node_run_id": waiting.node_run_id,
            "handoff_id": waiting.external_handoff.handoff_id,
        },
    )
    app.wait_until_idle(timeout=40)
    failed = _view(app)
    assert failed.state == "failed" and failed.admission_status == "failed", failed
    assert failed.retry_target is not None and failed.retry_target.node_id.endswith("admission")
    assert failed.color_interpretation_available and failed.color_interpretation_required
    assert app.inspect_run_detail(failed.run_id).run.state is RunState.RUNNING
    _choose(
        app,
        route="external",
        confirmations=["external_reference", "color_interpretation"],
        external_format="mkv",
        interpretation_policy="operator_confirmed_bt709_limited_left",
    )
    # 仅选择新参数不放弃旧批次；替代创建 CAS 失败同样不能提前结束旧历史。
    assert app.inspect_run_detail(failed.run_id).run.state is RunState.RUNNING
    from zniku.project_service import ProjectServiceError

    with pytest.raises(ProjectServiceError):
        app.command(
            {
                "operation": "rerun_from_here",
                "project_session_id": app.inspect().project_session_id,
                "expected_storage_revision": app.inspect().storage_revision + 1,
                "node_id": failed.retry_target.node_id,
                "run_id": failed.run_id,
            }
        )
    assert app.inspect_run_detail(failed.run_id).run.state is RunState.RUNNING
    authoring_command(
        app,
        {
            "operation": "rerun_from_here",
            "node_id": failed.retry_target.node_id,
            "run_id": failed.retry_target.run_id,
        },
    )
    app.wait_until_idle(timeout=40)
    ready = _view(app)
    assert ready.state == "ready" and ready.source_frame_count == 12, ready
    assert ready.current_settings.audio_source == "reference"
    detail = app.inspect_run_detail(ready.run_id)
    prepare = next(n for n in detail.run.node_runs if n.node_id == waiting.node_id)
    assert prepare.state is NodeRunState.COMPLETED and prepare.reused_from_result_id is not None
    retired = app.inspect_run_detail(failed.run_id)
    assert retired.run.state is RunState.FAILED
    assert (
        next(n for n in retired.run.node_runs if n.node_id == waiting.node_id).output_artifact_ids
        == prepare.output_artifact_ids
    )
    assert all(Path(a.path).exists() for a in retired.artifacts)
    dispatch(
        app,
        "expand",
        {
            "contract_version": "0.3.4-work.1",
            "project_session_id": app.inspect().project_session_id,
            "expected_storage_revision": app.inspect().storage_revision,
            "preparation_run_id": ready.run_id,
            "processing": {
                "enhancement": {"model_name": "Synthetic"},
                "program_encode": {"encoder": "cpu"},
            },
            "publication": {
                "output_root": str(tmp_path),
                "title": "Example",
                "year": "2026",
                "overwrite": False,
            },
        },
    )
    authoring_command(app, {"operation": "run_to", "node_id": "overlap.split"})
    app.wait_until_idle(timeout=40)
    split_run = app.inspect_run_detail(app.inspect().active_run_id)
    assert split_run.run.state is RunState.COMPLETED, split_run.run.error
    assert (
        next(n for n in split_run.run.node_runs if n.node_id == "overlap.split").state
        is NodeRunState.COMPLETED
    )


@pytest.mark.parametrize("change", [{"external_format": "mov"}, {"target_frame_rate": "60/1"}])
def test_changed_external_candidate_cannot_inherit_previous_color_interpretation(
    media: Path, tmp_path: Path, change: dict[str, str]
) -> None:
    """解释只覆盖已观察的候选；改变格式或率必须先以默认政策取得新候选。"""
    candidate = _unspecified_media(tmp_path)
    app, path = _create(tmp_path, media)
    _run(app)
    _choose(app, route="external", confirmations=["external_reference"])
    waiting_view = _run(app)
    detail = app.inspect_run_detail(waiting_view.run_id)
    waiting = next(n for n in detail.run.node_runs if n.state is NodeRunState.WAITING_EXTERNAL)
    assert waiting.external_handoff is not None
    shutil.copyfile(candidate, waiting.external_handoff.output_targets[0].path)
    authoring_command(
        app,
        {
            "operation": "submit_external",
            "run_id": waiting_view.run_id,
            "node_run_id": waiting.node_run_id,
            "handoff_id": waiting.external_handoff.handoff_id,
        },
    )
    app.wait_until_idle(timeout=40)
    failed = _view(app)
    assert failed.admission_status == "failed" and failed.retry_target is not None
    _choose(
        app,
        route="external",
        interpretation_policy="operator_confirmed_bt709_limited_left",
        confirmations=["external_reference", "color_interpretation"],
    )
    authoring_command(
        app,
        {
            "operation": "rerun_from_here",
            "run_id": failed.retry_target.run_id,
            "node_id": failed.retry_target.node_id,
        },
    )
    app.wait_until_idle(timeout=40)
    ready = _view(app)
    assert ready.state == "ready" and ready.current_settings.external_format == "mkv"
    before, revision = path.read_bytes(), ready.storage_revision
    with pytest.raises(WorkError, match="COLOR_INTERPRETATION_REBIND"):
        _choose(
            app,
            route="external",
            interpretation_policy="operator_confirmed_bt709_limited_left",
            confirmations=["external_reference", "color_interpretation"],
            **change,
        )
    assert path.read_bytes() == before
    assert app.inspect().storage_revision == revision
    assert app.inspect().active_run_id == ready.run_id
    # declared_only 必须允许换候选，不能又被旧候选 A 的颜色确认要求阻塞。
    _choose(app, route="external", confirmations=["external_reference"], **change)
    snapshot = ProjectStore.open(path).load()
    admission = next(n for n in snapshot.project.graph.nodes if n.node_id.endswith("admission"))
    external = next(n for n in snapshot.project.graph.nodes if n.node_id.endswith("prepare"))
    assert admission.parameters["interpretation_policy"] == "declared_only"
    assert external.type_id.endswith(change.get("external_format", "mkv"))
    assert external.parameters["target_frame_rate"] == change.get("target_frame_rate", "30/1")
    assert app.inspect().active_run_id == ready.run_id  # Choose 仍不隐式创建/运行任务。
    next_view = _run(app)
    assert next_view.state == "waiting_external"
    next_detail = app.inspect_run_detail(next_view.run_id)
    new_waiting = next(
        n for n in next_detail.run.node_runs if n.state is NodeRunState.WAITING_EXTERNAL
    )
    assert new_waiting.node_run_id != waiting.node_run_id
    assert new_waiting.reused_from_result_id is None
    assert new_waiting.external_handoff is not None
    assert new_waiting.external_handoff.handoff_id != waiting.external_handoff.handoff_id


@pytest.mark.parametrize("bad", ["project_session_id", "run_id", "expected_storage_revision"])
def test_identity_cas_and_exact_wire_fail_closed(media: Path, tmp_path: Path, bad: str) -> None:
    app, path = _create(tmp_path, media)
    _run(app)
    before = path.read_bytes()
    payload = {
        **_view_request(app),
        "route": "direct",
        "expected_storage_revision": app.inspect().storage_revision,
    }
    payload[bad] = payload[bad] + 1 if bad == "expected_storage_revision" else str(uuid4())
    with pytest.raises(WorkError):
        dispatch(app, "choose", payload)
    with pytest.raises(ColorPreparedSourceError, match="GRAPH_EDITED"):
        dispatch(app, "view", {**_view_request(app), "contract_version": "0.3.4-color.1"})
    assert path.read_bytes() == before


def test_same_http_route_new_wire_and_unknown_input(media: Path, tmp_path: Path) -> None:
    app, _path = _create(tmp_path, media)
    _run(app)
    with _serve(app) as (url, _server):
        status, body, _ = _request(
            url,
            "/api/studio/templates/prepared-color/view",
            method="POST",
            payload=_view_request(app),
        )
        assert status == 200 and body["contract_version"] == "0.3.4-work.1"
        Draft202012Validator(WorkViewEnvelope.model_json_schema()).validate(body)
        status, body, _ = _request(
            url,
            "/api/studio/templates/prepared-color/view",
            method="POST",
            payload={**_view_request(app), "unsafe": "anything"},
        )
        assert status == 422 and body["contract_version"] == "0.3.4-work.1"
        assert body["error"]["field_path"] == ["unsafe"]


def test_cancel_tracks_actual_work_attempt_and_exposes_retry(media: Path, tmp_path: Path) -> None:
    entered = threading.Event()
    app, _path = _create(tmp_path, media)
    executor = work_definitions.diagnostics_definition().executor
    assert executor.kind == "python"

    def block(context: Any) -> Any:
        entered.set()
        for _ in range(100):
            threading.Event().wait(0.02)
            check_cancellation()
        raise AssertionError("停止未到达")

    app._python_adapters[executor.adapter] = block
    app._runtime = app._runtime_for(app._store)
    authoring_command(app, {"operation": "run_all"})
    assert entered.wait(5)
    dispatch(app, "cancel", _view_request(app))
    app.wait_until_idle(timeout=10)
    failed = _view(app)
    assert failed.state == "failed" and failed.retry_target is not None
    assert failed.error is not None and "CANCELLED" in failed.error.message
    with pytest.raises(WorkError, match="NO_ACTIVE_CHECK"):
        dispatch(app, "cancel", _view_request(app))


def test_operation_cancel_on_freely_named_graph_binds_actual_attempt(
    media: Path, tmp_path: Path
) -> None:
    """停止不是向导小图特权；错 attempt 不能取消另一任务。"""
    app, path = _create(tmp_path, media)
    store = ProjectStore.open(path)
    current = store.load()
    names = {n.node_id: f"custom-{index}" for index, n in enumerate(current.project.graph.nodes)}
    graph = current.project.graph.model_copy(
        update={
            "nodes": tuple(
                n.model_copy(update={"node_id": names[n.node_id]})
                for n in current.project.graph.nodes
            ),
            "edges": tuple(
                e.model_copy(
                    update={
                        "source_node_id": names[e.source_node_id],
                        "target_node_id": names[e.target_node_id],
                    }
                )
                for e in current.project.graph.edges
            ),
        }
    )
    store.save(current.project.model_copy(update={"graph": graph}), current.definitions)
    entered = threading.Event()
    executor = work_definitions.diagnostics_definition().executor
    assert executor.kind == "python"

    def block(context: Any) -> Any:
        entered.set()
        for _ in range(200):
            threading.Event().wait(0.02)
            check_cancellation()
        raise AssertionError("停止未到达")

    app._python_adapters[executor.adapter] = block
    app._runtime = app._runtime_for(app._store)
    authoring_command(app, {"operation": "run_all"})
    assert entered.wait(5)
    run_id = app.inspect().active_run_id
    detail = app.inspect_run_detail(run_id)
    active = next(a for a in detail.run.node_runs if a.state is NodeRunState.RUNNING)
    source = next(a for a in detail.run.node_runs if a.state is NodeRunState.COMPLETED)
    request = {**_view_request(app), "node_run_id": active.node_run_id}
    operation = dispatch(app, "operation-view", request)
    assert isinstance(operation, WorkOperationEnvelope) and operation.active
    with pytest.raises(WorkError, match="NO_ACTIVE_CHECK"):
        dispatch(app, "operation-cancel", {**request, "node_run_id": source.node_run_id})
    assert app._preparation_cancel is not None and not app._preparation_cancel.is_set()
    dispatch(app, "operation-cancel", request)
    app.wait_until_idle(timeout=10)
    detail = app.inspect_run_detail(run_id)
    failure = next(a for a in detail.run.node_runs if a.node_run_id == active.node_run_id)
    assert failure.state is NodeRunState.FAILED and not failure.output_artifact_ids
    with pytest.raises(WorkError, match="GRAPH_EDITED"):
        _view(app)
