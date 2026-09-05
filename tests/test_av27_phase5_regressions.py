"""用真实短合成 payload 验证 Phase 5 的失效、重新规划与人工提交边界。

所有完成记录都由正式 Project Service、Runner、媒体 adapter 和 validator 产生；不直接写
Runtime 状态或伪造 Artifact metadata。FFmpeg 仅生成临时目录内的测试媒体，不运行外部 AI。
这些回归验证普通 DAG 的 stale/reuse 和 explicit Submit，不宣称实际 MR 模型或画质已验收。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import zniku.avenhance_v27.adapters as av27_adapters
from authoring_helpers import authoring_command
from zniku.avenhance_v27 import (
    av27_python_adapters,
    av27_validators,
    built_in_av27_definitions,
)
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.project import Project
from zniku.project_service import ProjectServiceApplication, ProjectServiceError
from zniku.runtime import FailureReason, NodeRun, NodeRunState, Run, RunState, StaleReason


@pytest.fixture(scope="module")
def ffmpeg() -> str:
    """只在缺少媒体工具时显式跳过；媒体或合同失败不得退化为 skip。"""

    executable = shutil.which("ffmpeg")
    if executable is None or shutil.which("ffprobe") is None:
        pytest.skip("AV27 Phase 5 合成 service 回归需要 FFmpeg 与 FFprobe")
    return executable


def _make_source(ffmpeg: str, path: Path, *, frames: int = 12) -> None:
    """生成 30 fps、16:9、BT.709、精确 N 帧的极短无音轨 FFV1 fixture。"""

    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=64x36:rate=30",
            "-frames:v",
            str(frames),
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "yuv420p10le",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            "-chroma_sample_location",
            "left",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def _application(
    tmp_path: Path, ffmpeg: str, *, external_mr: bool = False
) -> tuple[ProjectServiceApplication, Path]:
    """通过正式 create command 建立独立 preparation Project。"""

    source = tmp_path / "source.mkv"
    _make_source(ffmpeg, source)
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        definition_catalog=(*built_in_media_definitions(), *built_in_av27_definitions()),
        python_adapters={**media_python_adapters(), **av27_python_adapters()},
        validators={**media_validators(), **av27_validators()},
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )
    authoring_command(
        application,
        {
            "operation": "create_av_enhance_v27",
            "request": {
                "profile_version": "2.7.0",
                "project_path": str((tmp_path / "phase5.zniku").resolve()),
                "project_id": "project.phase5.regression",
                "project_name": "Phase 5 合成回归",
                "source_mode": "program",
                "sources": [{"source_path": str(source.resolve()), "source_ordinal": 0}],
                "mr": (
                    {"mode": "external", "model_name": "synthetic-mr", "model_version": "test-1"}
                    if external_mr
                    else {"mode": "off"}
                ),
            },
        },
    )
    return application, source


def _run(application: ProjectServiceApplication, command: dict[str, Any]) -> Run:
    """等待受控自动执行停止，再返回真实 persisted Run；不推进 waiting 手工节点。"""

    started = authoring_command(application, command)
    assert started.active_run_id is not None
    assert application.wait_until_idle(timeout=30), application.inspect()
    assert application.inspect().error is None
    return application.inspect_run_detail(started.active_run_id).run


def _latest(run: Run, node_id: str) -> NodeRun:
    return max(
        (item for item in run.node_runs if item.node_id == node_id),
        key=lambda item: item.attempt,
    )


def _expansion(tmp_path: Path, preparation_run_id: str) -> dict[str, Any]:
    root = tmp_path / "published"
    root.mkdir(exist_ok=True)
    (root / "Synthetic (2026)").mkdir(exist_ok=True)
    return {
        "profile_version": "2.7.0",
        "preparation_run_id": preparation_run_id,
        "chapter_selector": {"mode": "single"},
        "leaf_duration_minutes": 1,
        "enhancement": {"model_name": "synthetic-enhancement", "actual_scale_factor": 1},
        "frame_interpolation": {"model_name": "synthetic-fi"},
        "program_encode": {"encoder": "cpu"},
        "publication": {
            "output_root": str(root.resolve()),
            "title": "Synthetic",
            "year": "2026",
            "overwrite": False,
        },
    }


def _submit(application: ProjectServiceApplication, waiting: NodeRun) -> Run:
    assert waiting.external_handoff is not None
    return _run(
        application,
        {
            "operation": "submit_external",
            "run_id": waiting.run_id,
            "node_run_id": waiting.node_run_id,
            "handoff_id": waiting.external_handoff.handoff_id,
        },
    )


def _edit_parameters(
    application: ProjectServiceApplication, node_id: str, updates: dict[str, Any]
) -> None:
    """沿 Studio 的 save_project 边界编辑普通节点，不直接设置 stale。"""

    snapshot = application.inspect().snapshot
    assert snapshot is not None
    payload = snapshot.project.model_dump(mode="python")
    for node in payload["graph"]["nodes"]:
        if node["node_id"] == node_id:
            node["parameters"].update(updates)
    authoring_command(
        application,
        {"operation": "save_project", "project": Project.model_validate(payload, strict=True)},
    )


def _completed_split(
    application: ProjectServiceApplication, tmp_path: Path, preparation: Run
) -> tuple[Run, dict[str, Any]]:
    request = _expansion(tmp_path, preparation.run_id)
    authoring_command(application, {"operation": "expand_av_enhance_v27", "request": request})
    run = _run(application, {"operation": "run_to", "node_id": "split.atomic"})
    assert run.state is RunState.COMPLETED, run
    assert _latest(run, "split.atomic").state is NodeRunState.COMPLETED
    return run, request


def test_mr_parameter_edit_stales_its_split_but_preserves_admission_and_history(
    tmp_path: Path, ffmpeg: str
) -> None:
    """MR 配置变化仅使该节点及下游失效；原始 Source、Admission 与历史 snapshot 不回写。"""

    application, source = _application(tmp_path, ffmpeg, external_mr=True)
    waiting_run = _run(application, {"operation": "run_all"})
    mr = _latest(waiting_run, "mr.A")
    assert mr.external_handoff is not None
    shutil.copyfile(source, mr.external_handoff.output_targets[0].path)
    preparation = _submit(application, mr)
    assert preparation.state is RunState.COMPLETED
    split_run, request = _completed_split(application, tmp_path, preparation)
    old_detail = application.inspect_run_detail(split_run.run_id)
    old_heads = {item.node_id: item for item in application.inspect().latest_results}

    _edit_parameters(application, "mr.A", {"model_version": "test-2"})

    heads = {item.node_id: item for item in application.inspect().latest_results}
    assert heads["source.program"] == old_heads["source.program"]
    assert heads["admission"] == old_heads["admission"]
    assert heads["mr.A"].stale
    assert heads["split.atomic"].stale
    assert heads["mr.A"].stale_reason is StaleReason.GRAPH_CHANGED
    assert application.inspect_run_detail(split_run.run_id) == old_detail
    before_rejected_expand = application.inspect().snapshot
    with pytest.raises(ProjectServiceError, match="E_AV27_EXPAND_STALE"):
        authoring_command(application, {"operation": "expand_av_enhance_v27", "request": request})
    assert application.inspect().snapshot == before_rejected_expand

    retried = _run(application, {"operation": "run_to", "node_id": "mr.A"})
    for node_id in ("source.program", "admission"):
        assert _latest(retried, node_id).reused_from_result_id == old_heads[node_id].result_id
    assert _latest(retried, "mr.A").state is NodeRunState.WAITING_EXTERNAL
    assert _latest(retried, "mr.A").node_run_id != mr.node_run_id


def test_new_source_artifacts_fail_old_plan_before_producer_and_reexpand_recovers(
    tmp_path: Path, ffmpeg: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """强制 Source 从头执行后，旧 planned IDs 不可继续；新 admitted Run 可重新展开。"""

    application, _ = _application(tmp_path, ffmpeg)
    preparation = _run(application, {"operation": "run_all"})
    assert preparation.state is RunState.COMPLETED
    split_run, old_request = _completed_split(application, tmp_path, preparation)
    old_detail = application.inspect_run_detail(split_run.run_id)
    old_split = _latest(split_run, "split.atomic")
    producer_calls: list[object] = []

    def reject_producer(*args: object, **kwargs: object) -> None:
        producer_calls.append((args, kwargs))
        raise AssertionError("changed planned Artifact 必须在 producer 之前拒绝")

    with monkeypatch.context() as patch:
        patch.setattr(av27_adapters, "_run_ffmpeg", reject_producer)
        rerun = _run(
            application,
            {
                "operation": "rerun_from_here",
                "run_id": split_run.run_id,
                "node_id": "source.program",
            },
        )
    assert producer_calls == []
    fresh_source = _latest(rerun, "source.program")
    fresh_gate = _latest(rerun, "admission")
    assert (
        fresh_source.output_artifact_ids
        != _latest(preparation, "source.program").output_artifact_ids
    )
    assert fresh_gate.output_artifact_ids != _latest(preparation, "admission").output_artifact_ids
    failed_split = _latest(rerun, "split.atomic")
    assert failed_split.state is NodeRunState.FAILED
    assert failed_split.output_artifact_ids == ()
    assert failed_split.error is not None
    assert "E_AV27_PLAN_INPUT_CHANGED" in failed_split.error.message
    assert list((Path(failed_split.work_dir) / "outputs").rglob("*.mkv")) == []
    assert all(
        item.producer_node_run_id != failed_split.node_run_id
        for item in application.inspect_run_detail(rerun.run_id).artifacts
    )
    heads = {item.node_id: item for item in application.inspect().latest_results}
    assert heads["split.atomic"].stale
    assert application.inspect_run_detail(split_run.run_id) == old_detail
    with pytest.raises(ProjectServiceError, match="E_AV27_EXPAND_STALE"):
        application.preview_av_enhance_v27({"action": "expand", "request": old_request})

    authoring_command(application, {"operation": "abandon_run", "run_id": rerun.run_id})
    admitted = _run(application, {"operation": "run_to", "node_id": "admission"})
    assert admitted.state is RunState.COMPLETED
    assert (
        _latest(admitted, "source.program").output_artifact_ids == fresh_source.output_artifact_ids
    )
    assert _latest(admitted, "admission").output_artifact_ids == fresh_gate.output_artifact_ids
    new_request = {**old_request, "preparation_run_id": admitted.run_id}
    preview = application.preview_av_enhance_v27({"action": "expand", "request": new_request})
    assert preview.profile.status == "expanded-compatible"
    assert set(preview.plan.effective_video_artifact_ids).issubset(fresh_source.output_artifact_ids)
    assert set(preview.plan.effective_video_artifact_ids).isdisjoint(
        _latest(preparation, "source.program").output_artifact_ids
    )
    authoring_command(application, {"operation": "expand_av_enhance_v27", "request": new_request})
    recovered = _run(application, {"operation": "run_to", "node_id": "split.atomic"})
    assert recovered.state is RunState.COMPLETED, recovered
    new_split = _latest(recovered, "split.atomic")
    assert new_split.reused_from_result_id is None
    assert new_split.output_artifact_ids != old_split.output_artifact_ids
    assert (
        _latest(recovered, "source.program").output_artifact_ids == fresh_source.output_artifact_ids
    )
    assert _latest(recovered, "admission").output_artifact_ids == fresh_gate.output_artifact_ids
    assert application.inspect_run_detail(split_run.run_id) == old_detail


@pytest.mark.parametrize("binding", ("gate", "video"))
def test_edited_planned_binding_stales_split_and_fails_without_media_output(
    tmp_path: Path, ffmpeg: str, monkeypatch: pytest.MonkeyPatch, binding: str
) -> None:
    """用户合法编辑普通 planned 参数不会绕过 producer 前实际 input identity gate。"""

    application, _ = _application(tmp_path, ffmpeg)
    preparation = _run(application, {"operation": "run_all"})
    completed, _ = _completed_split(application, tmp_path, preparation)
    old_heads = {item.node_id: item for item in application.inspect().latest_results}
    replacement = str(uuid4())
    if binding == "gate":
        updates: dict[str, Any] = {"planned_admission_artifact_id": replacement}
    else:
        split = next(
            node for node in completed.graph_snapshot.nodes if node.node_id == "split.atomic"
        )
        parameters = split.model_dump(mode="python")["parameters"]
        segments = parameters["segments"]
        for segment in segments:
            segment["planned_effective_video_artifact_id"] = replacement
        updates = {"planned_effective_video_artifact_ids": [replacement], "segments": segments}
    _edit_parameters(application, "split.atomic", updates)
    heads = {item.node_id: item for item in application.inspect().latest_results}
    assert heads["split.atomic"].stale
    assert heads["source.program"] == old_heads["source.program"]
    assert heads["admission"] == old_heads["admission"]
    producer_calls: list[object] = []

    def reject_producer(*args: object, **kwargs: object) -> None:
        producer_calls.append((args, kwargs))
        raise AssertionError("错误 planned binding 不得启动媒体 producer")

    monkeypatch.setattr(av27_adapters, "_run_ffmpeg", reject_producer)
    failed_run = _run(application, {"operation": "run_to", "node_id": "split.atomic"})
    assert producer_calls == []
    failed = _latest(failed_run, "split.atomic")
    assert failed.state is NodeRunState.FAILED
    assert failed.error is not None
    assert "E_AV27_PLAN_INPUT_CHANGED" in failed.error.message
    assert failed.output_artifact_ids == ()
    assert list((Path(failed.work_dir) / "outputs").rglob("*.mkv")) == []
    for node_id in ("source.program", "admission"):
        assert _latest(failed_run, node_id).reused_from_result_id == old_heads[node_id].result_id


def test_mr_submit_rechecks_payload_after_readiness_then_requires_new_attempt(
    tmp_path: Path, ffmpeg: str
) -> None:
    """readiness pass 不推进状态；N+1 使 Submit 失败，旧 target 不作 checkpoint。"""

    application, source = _application(tmp_path, ffmpeg, external_mr=True)
    run = _run(application, {"operation": "run_all"})
    mr = _latest(run, "mr.A")
    assert mr.state is NodeRunState.WAITING_EXTERNAL
    assert mr.external_handoff is not None
    target = Path(mr.external_handoff.output_targets[0].path)
    shutil.copyfile(source, target)
    before_readiness = application.inspect_run_detail(run.run_id)
    ready = application.inspect_external_readiness(
        run_id=run.run_id, node_run_id=mr.node_run_id, probe=True
    )
    assert ready.ready_for_submit, ready
    assert application.inspect_run_detail(run.run_id) == before_readiness
    invalid = tmp_path / "invalid-mr.mkv"
    # 13 帧的 12 个间隔正好是 400 ms，避免极短 MKV 时间基量化先触发 FPS gate。
    _make_source(ffmpeg, invalid, frames=13)
    shutil.copyfile(invalid, target)

    failed_run = _submit(application, mr)

    assert failed_run.run_id == run.run_id
    failed = _latest(failed_run, "mr.A")
    assert failed.state is NodeRunState.FAILED
    assert failed.error is not None
    assert failed.error.reason is FailureReason.EXTERNAL_SUBMISSION_INVALID
    assert "E_AV27_MR_FRAME_COUNT" in failed.error.message
    assert failed.output_artifact_ids == ()
    assert all(
        item.producer_node_run_id != failed.node_run_id
        for item in application.inspect_run_detail(run.run_id).artifacts
    )
    assert target.read_bytes() == invalid.read_bytes()

    retried_run = _run(
        application,
        {"operation": "rerun_from_here", "run_id": run.run_id, "node_id": "mr.A"},
    )
    retry = _latest(retried_run, "mr.A")
    assert retried_run.run_id == run.run_id
    assert retry.attempt == failed.attempt + 1
    assert retry.node_run_id != failed.node_run_id
    assert retry.state is NodeRunState.WAITING_EXTERNAL
    assert retry.external_handoff is not None
    new_target = Path(retry.external_handoff.output_targets[0].path)
    assert new_target != target
    assert not new_target.exists()
    assert retry.input_artifact_ids == failed.input_artifact_ids
    for node_id in ("source.program", "admission"):
        assert _latest(retried_run, node_id) == _latest(run, node_id)
    with pytest.raises(ProjectServiceError, match="E_SERVICE_HANDOFF_SUPERSEDED"):
        _submit(application, mr)

    shutil.copyfile(source, new_target)
    completed = _submit(application, retry)
    assert completed.run_id == run.run_id
    assert completed.state is RunState.COMPLETED
    assert _latest(completed, "mr.A").output_artifact_ids
    assert application.inspect_run_detail(run.run_id).run.node_runs[0] == run.node_runs[0]
