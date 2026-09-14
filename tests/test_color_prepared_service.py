"""以短合成媒体验证新 Service 的真实绑定、CAS、HTTP、停止和不覆盖用户图。"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from authoring_helpers import authoring_command
from test_project_service_host import _request, _serve
from test_source_preparation_kernel import _tool
from test_source_preparation_kernel import media as media
from zniku.desktop.server import build_desktop_application
from zniku.graph import UiPosition
from zniku.project import ProjectStore
from zniku.project_service.models import StatusEnvelope
from zniku.project_service.prepared_color import (
    PREPARED_COLOR_PREFIX,
    ColorPreparedSourceError,
    ColorPreparedSourceFullEnvelope,
    ColorPreparedSourceViewEnvelope,
)
from zniku.project_service.prepared_color_application import dispatch
from zniku.runtime import NodeRunState
from zniku.source_color import definitions
from zniku.source_preparation.progress import check_cancellation


def _unspecified_media(tmp_path: Path) -> Path:
    """现场生成没有三项色彩描述的短样本；不从用户媒体派生。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("合成服务测试需要 FFmpeg/FFprobe")
    path = tmp_path / "unspecified.mkv"
    _tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x48:r=30",
            "-frames:v",
            "12",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "matroska",
            str(path),
        ]
    )
    return path


def test_missing_color_requires_explicit_graph_policy_without_rewriting_source(
    tmp_path: Path,
) -> None:
    source = _unspecified_media(tmp_path)
    original_bytes = source.read_bytes()
    app, path = _create(tmp_path, source)
    report = _run(app)
    assert report.state == "needs_choice", report
    assert report.color_interpretation_required and report.color_interpretation_available
    assert report.interpretation_policy == "declared_only"
    assert report.working_signal_basis is None
    before = path.read_bytes()
    with pytest.raises(ColorPreparedSourceError, match="COLOR_INTERPRETATION_REQUIRED"):
        _choose(app)
    assert path.read_bytes() == before
    _choose(app, interpretation_policy="operator_confirmed_bt709_limited_left")
    graph = ProjectStore.open(path).load().project.graph
    admission = next(n for n in graph.nodes if n.node_id.endswith("-admission"))
    assert admission.parameters["interpretation_policy"] == "operator_confirmed_bt709_limited_left"
    ready = _run(app)
    assert ready.state == "ready", ready
    assert ready.reference_path == str(source)
    assert ready.working_signal_basis and "不是原片实测" in ready.working_signal_basis
    assert source.read_bytes() == original_bytes
    assert all(n.definition_version == "0.3.4-color.1" for n in graph.nodes)


def test_color_wire_does_not_migrate_old_graph_or_accept_browser_override(
    media: Path, tmp_path: Path
) -> None:
    from zniku.project_service.prepared_source_application import dispatch as old_dispatch

    app, path = _create(tmp_path, media)
    _run(app)
    before = path.read_bytes()
    new_request = _request_view(app)
    with pytest.raises(ColorPreparedSourceError):
        dispatch(
            app,
            "choose",
            {
                **new_request,
                "route": "direct",
                "expected_storage_revision": app.inspect().storage_revision,
                "interpretation_policy": "force_all",
                "working_signal": {"color_space": "bt709"},
            },
        )
    assert before == path.read_bytes()
    from zniku.project_service.prepared_source import PreparedSourceError

    with pytest.raises(PreparedSourceError, match="GRAPH_EDITED"):
        old_dispatch(app, "view", {**new_request, "contract_version": "0.3.4"})
    assert before == path.read_bytes()


def _create(tmp_path: Path, media: Path) -> tuple[Any, Path]:
    app = build_desktop_application(tmp_path / "unused")
    path = tmp_path / "synthetic.zniku"
    dispatch(
        app,
        "create",
        {
            "contract_version": "0.3.4-color.1",
            "project_path": str(path),
            "project_id": "synthetic-preparation",
            "project_name": "合成素材准备",
            "source_path": str(media),
        },
    )
    return app, path


def _request_view(app: Any) -> dict[str, Any]:
    status = app.inspect()
    return {
        "contract_version": "0.3.4-color.1",
        "project_session_id": status.project_session_id,
        "run_id": status.active_run_id,
    }


def _run(app: Any) -> ColorPreparedSourceViewEnvelope:
    authoring_command(app, {"operation": "run_all"})
    app.wait_until_idle(timeout=30)
    result = dispatch(app, "view", _request_view(app))
    assert isinstance(result, ColorPreparedSourceViewEnvelope)
    return result


def _choose(app: Any, route: str = "direct", **kwargs: Any) -> Any:
    return dispatch(
        app,
        "choose",
        {
            **_request_view(app),
            "expected_storage_revision": app.inspect().storage_revision,
            "route": route,
            **kwargs,
        },
    )


def test_real_direct_check_admission_preview_expand_reopen(media: Path, tmp_path: Path) -> None:
    app, path = _create(tmp_path, media)
    original = media.read_bytes()
    report = _run(app)
    assert report.state == "needs_choice" and report.source_frame_count == 30
    assert report.available_actions[0].enabled
    _choose(app)
    ready = _run(app)
    assert ready.state == "ready" and ready.reference_path == str(media)
    assert ready.admission_status == "completed"
    assert media.read_bytes() == original
    snapshot = ProjectStore.open(path).load()
    assert len(snapshot.project.graph.nodes) == 3
    request = {
        "contract_version": "0.3.4-color.1",
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
    before = path.read_bytes()
    preview = dispatch(app, "full-preview", request)
    assert isinstance(preview, ColorPreparedSourceFullEnvelope)
    assert path.read_bytes() == before and preview.plan.chapter_count == 1
    result = dispatch(app, "expand", request)
    assert isinstance(result, StatusEnvelope)
    assert result.contract_version == "0.3.0"
    expanded = ProjectStore.open(path).load()
    assert len(expanded.project.graph.nodes) == preview.node_count
    assert any(n.type_id.startswith("zniku.prepared.") for n in expanded.project.graph.nodes)
    with pytest.raises(ColorPreparedSourceError, match="GRAPH_EDITED"):
        dispatch(app, "expand", request)
    app.command({"operation": "open_project", "path": str(path)})
    assert ProjectStore.open(path).load().project.graph == expanded.project.graph


@pytest.mark.parametrize("field", ["project_session_id", "run_id", "expected_storage_revision"])
def test_choose_rejects_stale_identity_without_mutation(
    media: Path, tmp_path: Path, field: str
) -> None:
    app, path = _create(tmp_path, media)
    _run(app)
    request = {
        **_request_view(app),
        "route": "direct",
        "expected_storage_revision": app.inspect().storage_revision,
    }
    request[field] = request[field] + 1 if field == "expected_storage_revision" else str(uuid4())
    before = path.read_bytes()
    with pytest.raises(ColorPreparedSourceError):
        dispatch(app, "choose", request)
    assert before == path.read_bytes()


def test_layout_is_not_execution_identity_but_source_change_is(media: Path, tmp_path: Path) -> None:
    app, path = _create(tmp_path, media)
    _run(app)
    store = ProjectStore.open(path)
    snapshot = store.load()
    graph = snapshot.project.graph
    moved = graph.model_copy(
        update={
            "nodes": tuple(
                n.model_copy(update={"ui_position": UiPosition(x=12.0, y=34.0)})
                for n in graph.nodes
            )
        }
    )
    store.save(snapshot.project.model_copy(update={"graph": moved}), snapshot.definitions)
    moved_view = dispatch(app, "view", _request_view(app))
    assert isinstance(moved_view, ColorPreparedSourceViewEnvelope)
    assert moved_view.state == "needs_choice"
    media.write_bytes(media.read_bytes() + b"changed")
    with pytest.raises(ColorPreparedSourceError, match="INPUT_CHANGED"):
        dispatch(app, "view", _request_view(app))


def test_http_wire_unknown_field_and_cancel_idle_fail_closed(media: Path, tmp_path: Path) -> None:
    app, path = _create(tmp_path, media)
    _run(app)
    request = _request_view(app)
    before = path.read_bytes()
    with _serve(app) as (url, _server):
        status, payload, _ = _request(
            url, PREPARED_COLOR_PREFIX + "/view", method="POST", payload=request
        )
        assert status == 200 and payload["contract_version"] == "0.3.4-color.1"
        Draft202012Validator(ColorPreparedSourceViewEnvelope.model_json_schema()).validate(payload)
        status, payload, _ = _request(
            url,
            PREPARED_COLOR_PREFIX + "/choose",
            method="POST",
            payload={
                **request,
                "expected_storage_revision": app.inspect().storage_revision,
                "route": "direct",
                "shell": "forbidden",
            },
        )
        assert status == 422 and payload["error"]["field_path"] == ["shell"]
        status, payload, _ = _request(
            url, PREPARED_COLOR_PREFIX + "/cancel", method="POST", payload=request
        )
        assert status == 409 and payload["error"]["code"].endswith("NO_ACTIVE_CHECK")
    assert path.read_bytes() == before


def test_cancel_worker_fails_without_artifact_and_releases_busy(
    media: Path, tmp_path: Path
) -> None:
    entered = threading.Event()
    app, _path = _create(tmp_path, media)
    executor = definitions.diagnostics_definition().executor
    assert executor.kind == "python"
    key = executor.adapter
    original_adapter = app._python_adapters[key]

    def wait_for_cancel(context: Any) -> Any:
        entered.set()
        for _ in range(100):
            threading.Event().wait(0.02)
            check_cancellation()
        raise AssertionError("取消信号未到达")

    app._python_adapters[key] = wait_for_cancel
    app._runtime = app._runtime_for(app._store)
    authoring_command(app, {"operation": "run_all"})
    assert entered.wait(5)
    dispatch(app, "cancel", _request_view(app))
    app.wait_until_idle(timeout=10)
    failure = dispatch(app, "view", _request_view(app))
    assert isinstance(failure, ColorPreparedSourceViewEnvelope)
    assert failure.state == "failed" and failure.diagnosis_status == "failed"
    assert failure.error is not None and "CANCELLED" in failure.error.message
    detail = app.inspect_run_detail(failure.run_id)
    diagnostic = next(n for n in detail.run.node_runs if n.node_id.endswith("-diagnostics"))
    assert diagnostic.state == NodeRunState.FAILED and not diagnostic.output_artifact_ids
    assert app.inspect().active_operation is None
    app._python_adapters[key] = original_adapter
