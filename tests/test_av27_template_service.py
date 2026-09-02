"""验证 AVEnhanceFlow v2.7 template 的 Project Service authority 与原子失败语义。

测试只在临时 SQLite 中登记合成 Run/NodeResult/Artifact，不执行媒体 I/O、FFmpeg 或外部 GUI。
重点锁定 preview 无副作用、create no-replace、显式 preparation Run binding、stale 检测、
expanded Graph divergence 拒绝与关闭重开后的普通 Project 持久化。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import JsonValue

from zniku.avenhance_v27 import (
    SOURCE_ADMISSION_TYPE_ID,
    SOURCE_PROGRAM_TYPE_ID,
)
from zniku.graph import Graph, NodeInstance
from zniku.project import Project, ProjectStore
from zniku.project_service import ProjectServiceApplication, ProjectServiceError
from zniku.runtime import (
    Artifact,
    NodeResult,
    NodeRun,
    NodeRunState,
    Run,
    RunState,
    RuntimeRepository,
    StaleReason,
)


def _id() -> str:
    return str(uuid4())


def _prepare_payload(tmp_path: Path) -> dict[str, Any]:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"synthetic source")
    return {
        "profile_version": "2.7.0",
        "project_path": str((tmp_path / "workflow.zniku").resolve()),
        "project_id": "project.av27.service",
        "project_name": "AV27 Service 合成工程",
        "source_mode": "program",
        "sources": [
            {
                "source_path": str(source.resolve()),
                "source_ordinal": 0,
            }
        ],
        "mr": {"mode": "off"},
    }


def _expand_payload(tmp_path: Path, run_id: str) -> dict[str, Any]:
    output_root = tmp_path / "published"
    output_root.mkdir(exist_ok=True)
    (output_root / "Example (2026)").mkdir(exist_ok=True)
    return {
        "profile_version": "2.7.0",
        "preparation_run_id": run_id,
        "chapter_selector": {"mode": "exact_frames", "frames": [1800]},
        "leaf_duration_minutes": 1,
        "enhancement": {
            "model_name": "Starlight Precise",
            "model_version": "1",
            "actual_scale_factor": 1,
        },
        "frame_interpolation": {"model_name": "Aion", "model_version": "1"},
        "program_encode": {"encoder": "cpu"},
        "publication": {
            "output_root": str(output_root.resolve()),
            "title": "Example",
            "year": "2026",
            "overwrite": False,
        },
    }


def _media_info(*, frames: int = 3600) -> dict[str, JsonValue]:
    return {
        "zniku.avenhance.v27": {
            "frame_count": frames,
            "frame_rate": "30/1",
            "duration_seconds": frames / 30,
            "source_ordinal": 0,
            "geometry": {"width": 1280, "height": 720},
            "sample_aspect_ratio": "1/1",
            "signal": {
                "color_primaries": "bt709",
                "color_transfer": "bt709",
                "color_space": "bt709",
                "color_range": "tv",
                "chroma_location": "left",
                "field_order": "progressive",
                "rotation": 0,
            },
            "audio_tracks": [],
        }
    }


def _seed_completed_preparation(path: Path, tmp_path: Path) -> tuple[str, tuple[str, str]]:
    """用 Repository 公共事务 API 建立一个 completed MR-off preparation Run。"""

    store = ProjectStore.open(path)
    snapshot = store.load()
    repository = RuntimeRepository(store)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    run = Run.pending(
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
        created_at=base,
    )
    attempts = tuple(
        NodeRun.pending(
            run_id=run.run_id,
            node_id=node.node_id,
            definition_version=node.definition_version,
            attempt=1,
            input_artifact_ids=(),
            work_dir=str((tmp_path / "attempts" / node.node_id).resolve()),
            created_at=base + timedelta(seconds=1),
        )
        for node in snapshot.project.graph.nodes
    )
    repository.start_run(run, attempts, started_at=base + timedelta(seconds=2))
    attempts_by_node = {item.node_id: item for item in attempts}

    source_node = next(
        node for node in snapshot.project.graph.nodes if node.type_id == SOURCE_PROGRAM_TYPE_ID
    )
    source_attempt = attempts_by_node[source_node.node_id]
    repository.transition_node_run(
        source_attempt.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=base + timedelta(seconds=3),
    )
    source_path = Path(cast(str, source_node.parameters["source_path"]))
    video_id = _id()
    source_media_id = _id()
    source_outputs = (
        Artifact(
            artifact_id=video_id,
            kind="VideoFile",
            path=str(source_path),
            producer_node_run_id=source_attempt.node_run_id,
            producer_port_id="video",
            media_info=_media_info(),
            size=source_path.stat().st_size,
            mtime_ns=source_path.stat().st_mtime_ns,
        ),
        Artifact(
            artifact_id=source_media_id,
            kind="MediaFile",
            path=str(source_path),
            producer_node_run_id=source_attempt.node_run_id,
            producer_port_id="source_media",
            media_info=_media_info(),
            size=source_path.stat().st_size,
            mtime_ns=source_path.stat().st_mtime_ns,
        ),
    )
    repository.register_result(
        NodeResult(
            result_id=_id(),
            node_run_id=source_attempt.node_run_id,
            outputs=source_outputs,
            validation_summary={"passed": True},
            created_at=base + timedelta(seconds=4),
        ),
        ended_at=base + timedelta(seconds=5),
    )

    admission_node = next(
        node for node in snapshot.project.graph.nodes if node.type_id == SOURCE_ADMISSION_TYPE_ID
    )
    admission_attempt = attempts_by_node[admission_node.node_id]
    repository.bind_inputs(admission_attempt.node_run_id, (source_media_id,))
    repository.transition_node_run(
        admission_attempt.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=base + timedelta(seconds=6),
    )
    admission_work_dir = Path(admission_attempt.work_dir)
    admission_work_dir.mkdir(parents=True, exist_ok=True)
    gate_path = admission_work_dir / "admission.json"
    gate_path.write_text("{}", encoding="utf-8")
    gate_id = _id()
    repository.register_result(
        NodeResult(
            result_id=_id(),
            node_run_id=admission_attempt.node_run_id,
            outputs=(
                Artifact(
                    artifact_id=gate_id,
                    kind="DataFile",
                    path=str(gate_path),
                    producer_node_run_id=admission_attempt.node_run_id,
                    producer_port_id="gate",
                    size=gate_path.stat().st_size,
                    mtime_ns=gate_path.stat().st_mtime_ns,
                ),
            ),
            validation_summary={"passed": True},
            created_at=base + timedelta(seconds=7),
        ),
        ended_at=base + timedelta(seconds=8),
    )
    repository.transition_run(
        run.run_id,
        RunState.COMPLETED,
        occurred_at=base + timedelta(seconds=9),
    )
    return run.run_id, (gate_id, video_id)


def _create_application(tmp_path: Path) -> tuple[ProjectServiceApplication, dict[str, Any]]:
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    request = _prepare_payload(tmp_path)
    application.command({"operation": "create_av_enhance_v27", "request": request})
    return application, request


def test_prepare_preview_is_strict_and_has_no_project_side_effect(tmp_path: Path) -> None:
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    request = _prepare_payload(tmp_path)
    target = Path(cast(str, request["project_path"]))

    preview = application.preview_av_enhance_v27({"action": "prepare", "request": request})

    assert preview.contract_version == "0.2.1"
    assert preview.phase == "preparation"
    assert preview.profile.status == "preparation-compatible"
    assert [node.node_id for node in preview.project.graph.nodes] == [
        "source.program",
        "admission",
    ]
    assert not target.exists()
    assert application.inspect().snapshot is None

    invalid = {"action": "prepare", "request": {**request, "definitions": []}}
    with pytest.raises(ProjectServiceError) as captured:
        application.preview_av_enhance_v27(invalid)
    assert captured.value.code == "E_AV27_TEMPLATE_REQUEST_INVALID"
    assert not target.exists()


def test_template_create_is_atomic_no_replace_and_reopens(tmp_path: Path) -> None:
    application, request = _create_application(tmp_path)
    path = Path(cast(str, request["project_path"]))
    original = path.read_bytes()

    snapshot = application.inspect().snapshot
    assert snapshot is not None
    assert [node.node_id for node in snapshot.project.graph.nodes] == [
        "source.program",
        "admission",
    ]
    assert tuple(path.parent.glob(".zniku-create-*.tmp.zniku")) == ()

    with pytest.raises(ProjectServiceError) as captured:
        application.command({"operation": "create_av_enhance_v27", "request": request})
    assert captured.value.code == "E_AV27_CREATE_EXISTS"
    assert path.read_bytes() == original

    reopened = ProjectServiceApplication(work_root=tmp_path / "reopened-work")
    reopened.command({"operation": "open_project", "path": str(path)})
    assert reopened.inspect().snapshot == snapshot


def test_expand_preview_and_command_use_exact_run_artifacts_and_persist(
    tmp_path: Path,
) -> None:
    application, prepare = _create_application(tmp_path)
    path = Path(cast(str, prepare["project_path"]))
    run_id, (gate_id, video_id) = _seed_completed_preparation(path, tmp_path)
    request = _expand_payload(tmp_path, run_id)
    before = ProjectStore.open(path).load()

    preview = application.preview_av_enhance_v27({"action": "expand", "request": request})

    assert preview.phase == "expanded"
    assert preview.profile.status == "expanded-compatible"
    assert preview.plan.preparation_run_id == run_id
    assert preview.plan.chapter_count == 2
    assert preview.plan.leaf_count == 2
    assert preview.plan.effective_video_artifact_ids == (video_id,)
    split = next(node for node in preview.project.graph.nodes if node.node_id == "split.atomic")
    assert split.parameters["planned_admission_artifact_id"] == gate_id
    assert split.parameters["planned_effective_video_artifact_ids"] == [video_id]
    assert ProjectStore.open(path).load() == before

    application.command({"operation": "expand_av_enhance_v27", "request": request})
    saved = ProjectStore.open(path).load()
    assert saved.project == preview.project
    assert saved.definitions == preview.definitions

    # 已持久化 expanded Graph 必须能用同一显式 Run 重新 preview/replan；Project Service
    # 此时从普通 Output target 恢复发布根并重新采集当前文件系统 facts，不能因缺 facts 永久降级。
    repeated = application.preview_av_enhance_v27({"action": "expand", "request": request})
    assert repeated.profile.status == "expanded-compatible"
    application.command({"operation": "expand_av_enhance_v27", "request": request})
    assert ProjectStore.open(path).load() == saved

    reopened = ProjectServiceApplication(work_root=tmp_path / "work-reopened")
    reopened.command({"operation": "open_project", "path": str(path)})
    reopened_status = reopened.inspect()
    assert reopened_status.snapshot == saved
    assert any(summary.run_id == run_id for summary in reopened_status.run_summaries)


def test_expand_rejects_stale_latest_without_mutating_project(tmp_path: Path) -> None:
    application, prepare = _create_application(tmp_path)
    path = Path(cast(str, prepare["project_path"]))
    run_id, _ = _seed_completed_preparation(path, tmp_path)
    request = _expand_payload(tmp_path, run_id)
    repository = RuntimeRepository.open(path)
    before = ProjectStore.open(path).load()
    repository.mark_latest_stale(
        ("source.program",),
        StaleReason.OUTPUT_MISSING,
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    with pytest.raises(ProjectServiceError) as captured:
        application.command({"operation": "expand_av_enhance_v27", "request": request})

    assert captured.value.code == "E_AV27_EXPAND_STALE"
    assert ProjectStore.open(path).load() == before


def test_expand_rechecks_publication_target_after_preview_without_mutation(
    tmp_path: Path,
) -> None:
    application, prepare = _create_application(tmp_path)
    path = Path(cast(str, prepare["project_path"]))
    run_id, _ = _seed_completed_preparation(path, tmp_path)
    request = _expand_payload(tmp_path, run_id)
    preview = application.preview_av_enhance_v27({"action": "expand", "request": request})
    target = Path(cast(str, preview.plan.output_target_path))
    before = ProjectStore.open(path).load()

    target.write_bytes(b"operator-owned publication")
    with pytest.raises(ProjectServiceError) as captured:
        application.command({"operation": "expand_av_enhance_v27", "request": request})

    assert captured.value.code == "E_AV27_NAMING_EXISTS"
    assert target.read_bytes() == b"operator-owned publication"
    assert ProjectStore.open(path).load() == before


def test_reexpand_rejects_freely_edited_graph_without_partial_save(tmp_path: Path) -> None:
    application, prepare = _create_application(tmp_path)
    path = Path(cast(str, prepare["project_path"]))
    run_id, _ = _seed_completed_preparation(path, tmp_path)
    request = _expand_payload(tmp_path, run_id)
    application.command({"operation": "expand_av_enhance_v27", "request": request})
    store = ProjectStore.open(path)
    expanded = store.load()

    changed_nodes: list[NodeInstance] = []
    for node in expanded.project.graph.nodes:
        if node.node_id == "output":
            parameters = dict(node.parameters)
            parameters["target_path"] = str((tmp_path / "operator-choice.mkv").resolve())
            node = NodeInstance(
                node_id=node.node_id,
                type_id=node.type_id,
                definition_version=node.definition_version,
                parameters=parameters,
                ui_position=node.ui_position,
            )
        changed_nodes.append(node)
    edited = Project(
        project_id=expanded.project.project_id,
        name=expanded.project.name,
        graph=Graph(nodes=tuple(changed_nodes), edges=expanded.project.graph.edges),
    )
    store.save(edited, expanded.definitions)
    before_failure = store.load()

    with pytest.raises(ProjectServiceError) as captured:
        application.command({"operation": "expand_av_enhance_v27", "request": request})

    assert captured.value.code == "E_AV27_EXPAND_GRAPH_DIVERGED"
    assert store.load() == before_failure
