"""复核准备 Service 的会话竞态、停止边界及只读轮询不触发全片检查。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from test_prepared_source_service import _choose, _create, _request_view, _run
from test_source_preparation_kernel import media as media
from zniku.project import ProjectStore
from zniku.project_service.prepared_source import PreparedSourceError
from zniku.project_service.prepared_source_application import dispatch
from zniku.project_service.service import ProjectServiceError
from zniku.source_preparation import inspection


def test_readiness_rejects_project_switch_before_inspection_claim(
    media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """即使切换恰好发生在目标探测后，也不能把旧 Runtime 设为新工程的当前检查。"""
    app, _ = _create(tmp_path / "first", media)
    _run(app)
    _choose(app, "external", target_frame_rate="30/1")
    waiting = _run(app)
    assert waiting.handoff is not None
    _, runtime = app._require_session()
    node_run = runtime.inspect_external_handoff(waiting.run_id, waiting.handoff.node_run_id)
    assert node_run.external_handoff is not None
    target = Path(node_run.external_handoff.output_targets[0].path)
    shutil.copyfile(media, target)
    _, second_path = _create(tmp_path / "second", media)
    real_inspect = runtime.inspect_external_handoff
    switched = False

    def switch_after_resolving_handoff(*args: Any, **kwargs: Any) -> Any:
        nonlocal switched
        result = real_inspect(*args, **kwargs)
        if not switched:
            switched = True
            app.command({"operation": "open_project", "path": str(second_path)})
        return result

    monkeypatch.setattr(runtime, "inspect_external_handoff", switch_after_resolving_handoff)
    with pytest.raises(ProjectServiceError, match="SESSION_CONFLICT"):
        app.inspect_external_readiness(
            run_id=waiting.run_id, node_run_id=waiting.handoff.node_run_id, probe=True
        )
    assert switched
    assert app.inspect().active_operation is None
    assert app.inspect().active_run_id is None
    assert target.read_bytes() == media.read_bytes()


def test_old_run_cannot_cancel_current_external_inspection(media: Path, tmp_path: Path) -> None:
    """停止请求绑定当前图、Run 和事件；旧诊断请求不能停止新外部检查。"""
    app, _ = _create(tmp_path, media)
    _run(app)
    old_request = _request_view(app)
    _choose(app, "external", target_frame_rate="30/1")
    waiting = _run(app)
    assert waiting.handoff is not None
    _, runtime = app._require_session()
    with app._preparation_inspection(
        waiting.run_id, waiting.handoff.node_run_id, expected_runtime=runtime
    ):
        event = app._preparation_cancel
        assert event is not None and not event.is_set()
        with pytest.raises(PreparedSourceError):
            dispatch(app, "cancel", old_request)
        assert not event.is_set()
        dispatch(app, "cancel", _request_view(app))
        assert event.is_set()
        assert app.inspect().active_operation == "import_external"
    assert app.inspect().active_operation is None


def test_active_preparation_can_stop_after_authoring_graph_changes(
    media: Path, tmp_path: Path
) -> None:
    """编辑当前草稿不改变正在检查的 snapshot，不能因此丢失原任务停止能力。"""
    app, path = _create(tmp_path, media)
    _run(app)
    _choose(app, "external", target_frame_rate="30/1")
    waiting = _run(app)
    assert waiting.handoff is not None
    _, runtime = app._require_session()
    request = _request_view(app)
    with app._preparation_inspection(
        waiting.run_id, waiting.handoff.node_run_id, expected_runtime=runtime
    ):
        store = ProjectStore.open(path)
        snapshot = store.load()
        changed = snapshot.project.graph.model_copy(
            update={
                "nodes": tuple(
                    node.model_copy(
                        update={"parameters": {"source_path": str(tmp_path / "next.mkv")}}
                    )
                    if node.node_id.endswith("-source")
                    else node
                    for node in snapshot.project.graph.nodes
                )
            }
        )
        store.save(snapshot.project.model_copy(update={"graph": changed}), snapshot.definitions)
        event = app._preparation_cancel
        assert event is not None and not event.is_set()
        dispatch(app, "cancel", request)
        assert event.is_set()
    assert app.inspect().active_operation is None


def test_report_polling_does_not_repeat_media_scan(
    media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """轮询只读已登记报告和日志，不能再次执行全片媒体扫描。"""
    app, _ = _create(tmp_path, media)
    _run(app)

    def forbidden_scan(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("只读轮询不得重新扫描媒体")

    monkeypatch.setattr(inspection, "inspect_source", forbidden_scan)
    monkeypatch.setattr(inspection, "read_header", forbidden_scan)
    for _ in range(3):
        report = dispatch(app, "view", _request_view(app))
        assert report.model_dump()["source_frame_count"] == 30
