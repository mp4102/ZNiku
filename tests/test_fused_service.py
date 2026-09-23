"""融合候选独立 API、真实合成三章链和失败边界；不是外部 Aion 能力验收。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from authoring_helpers import authoring_command
from test_project_service_host import _request, _serve
from test_source_admitted_service import analyze, create, full_request
from zniku.avenhance_v27.probe import Av27MediaError, probe_header
from zniku.chapter_batch import definitions, final_publish, fused
from zniku.chapter_batch.contracts import NAMESPACE as BATCH_NAMESPACE
from zniku.chapter_batch.contracts import BatchMetadata
from zniku.project import ProjectStore
from zniku.project_service.fused_application import (
    EXPAND_ROUTE,
    PREVIEW_ROUTE,
    FusedFullEnvelope,
    dispatch,
)
from zniku.project_service.models import StatusEnvelope
from zniku.project_service.service import ProjectServiceApplication
from zniku.project_service.source_admitted import (
    FULL_PREVIEW_ROUTE as OLD_PREVIEW_ROUTE,
)
from zniku.project_service.source_admitted import (
    REPLACE_ROUTE,
    SourceAdmittedError,
    SourceAdmittedFullEnvelope,
)
from zniku.project_service.source_admitted_application import dispatch as old_dispatch
from zniku.runtime import (
    FailureReason,
    NodeRunState,
    PythonAdapterContext,
    RunnerCancelled,
    RunState,
)

TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def ready(tmp_path: Path) -> tuple[ProjectServiceApplication, Path, Path, str]:
    app, _placeholder, path = create(tmp_path)
    source = tmp_path / "synthetic-reference.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x36:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=0.4",
            "-frames:v",
            "12",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-video_track_timescale",
            "30",
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
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    status = app.inspect()
    old_dispatch(
        app,
        REPLACE_ROUTE,
        {
            "contract_version": "0.3.5",
            "project_session_id": status.project_session_id,
            "expected_storage_revision": status.storage_revision,
            "source_path": str(source),
            "reference_change_confirmed": True,
        },
    )
    return app, source, path, analyze(app)


@pytest.mark.skipif(not TOOLS, reason="需要FFmpeg/FFprobe")
def test_preview_http_expand_and_old_default_remain_separate(tmp_path: Path) -> None:
    app, source, path, run_id = ready(tmp_path)
    old_request = full_request(app, run_id, tmp_path)
    old = old_dispatch(app, OLD_PREVIEW_ROUTE, old_request)
    assert isinstance(old, SourceAdmittedFullEnvelope)
    before, before_source = path.read_bytes(), source.read_bytes()
    request = {**old_request, "contract_version": "0.3.6"}
    with _serve(app) as (url, _server):
        status, body, _ = _request(url, PREVIEW_ROUTE, method="POST", payload=request)
        assert status == 200
        assert body["profile_id"] == fused.TEMPLATE_ID and body["contract_version"] == "0.3.6"
        assert body["node_count"] == old.node_count - 3
        assert not body["export_cropped_chapters"]
        assert path.read_bytes() == before and source.read_bytes() == before_source
        status, error, _ = _request(
            url, PREVIEW_ROUTE, method="POST", payload={**request, "unknown": True}
        )
        assert status == 422 and error["contract_version"] == "0.3.5"
        status, expanded, _ = _request(url, EXPAND_ROUTE, method="POST", payload=request)
        assert status == 200 and expanded["contract_version"] == "0.3.0"
    snapshot = ProjectStore.open(path).load()
    assert any(node.type_id == fused.PROGRAM_TYPE for node in snapshot.project.graph.nodes)
    assert not any(
        node.type_id == definitions.definition("crop").type_id
        for node in snapshot.project.graph.nodes
    )
    assert source.read_bytes() == before_source


@pytest.mark.skipif(not TOOLS, reason="需要FFmpeg/FFprobe")
@pytest.mark.parametrize(
    "mutation", ("version", "unknown", "export", "session", "revision", "route")
)
def test_bad_candidate_request_cannot_mutate_project(tmp_path: Path, mutation: str) -> None:
    app, source, path, run_id = ready(tmp_path)
    request = {**full_request(app, run_id, tmp_path), "contract_version": "0.3.6"}
    route = EXPAND_ROUTE
    if mutation == "version":
        request["contract_version"] = "0.3.5"
    elif mutation == "unknown":
        request["silent_upgrade"] = True
    elif mutation == "export":
        request["export_cropped_chapters"] = 1
    elif mutation == "session":
        request["project_session_id"] = str(uuid4())
    elif mutation == "revision":
        request["expected_storage_revision"] = 999
    else:
        route = "/unknown"
    before, before_source = path.read_bytes(), source.read_bytes()
    with pytest.raises(SourceAdmittedError):
        dispatch(app, route, request)
    assert path.read_bytes() == before and source.read_bytes() == before_source


@pytest.mark.skipif(not TOOLS, reason="需要FFmpeg/FFprobe")
@pytest.mark.parametrize(
    "export,outcome", ((False, "complete"), (True, "complete"), (False, "fail"), (False, "cancel"))
)
def test_real_synthetic_three_chapter_fused_chain_and_optional_crop_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, export: bool, outcome: str
) -> None:
    app, source, path, run_id = ready(tmp_path)
    request = {
        **full_request(app, run_id, tmp_path),
        "contract_version": "0.3.6",
        "export_cropped_chapters": export,
    }
    request["processing"]["fi_profile"] = {"left_context_frames": 2, "right_context_frames": 2}
    preview = dispatch(app, PREVIEW_ROUTE, request)
    assert isinstance(preview, FusedFullEnvelope) and preview.export_cropped_chapters == export
    assert isinstance(dispatch(app, EXPAND_ROUTE, request), StatusEnvelope)
    source_before = source.read_bytes()
    partials: list[Path] = []
    if outcome != "complete":

        def fail_or_cancel(context: PythonAdapterContext, *_args: Any, **_kwargs: Any) -> int:
            """显式故障注入只验证 Runtime 收口，不冒充真实 FFmpeg 用户取消测试。"""
            partial = context.outputs[0].path
            partial.write_bytes(b"synthetic-partial-not-media")
            partials.append(partial)
            if outcome == "cancel":
                raise RunnerCancelled("synthetic fused cancellation")
            raise RuntimeError("synthetic fused failure")

        monkeypatch.setattr(fused, "encode_fused", fail_or_cancel)
    started = authoring_command(app, {"operation": "run_all"})
    assert started.active_run_id is not None
    _, runtime = app._require_session()
    for _ in range(12):
        assert app.wait_until_idle(timeout=120)
        run = runtime.repository.get_run(started.active_run_id)
        if outcome != "complete" and any(
            node.state is NodeRunState.FAILED for node in run.node_runs
        ):
            break
        assert not any(node.state is NodeRunState.FAILED for node in run.node_runs), [
            (node.node_id, node.error) for node in run.node_runs if node.error
        ]
        if run.state is RunState.COMPLETED:
            break
        waiting = [node for node in run.node_runs if node.state is NodeRunState.WAITING_EXTERNAL]
        assert waiting
        node = waiting[0]
        handoff = node.external_handoff
        assert handoff is not None and len(handoff.output_targets) == 1
        input_artifact = next(
            runtime.repository.get_artifact(identity)
            for identity in node.input_artifact_ids
            if runtime.repository.get_artifact(identity).kind == "VideoFile"
        )
        header = probe_header(Path(input_artifact.path), count_frames=True).video
        assert header.frame_count is not None
        count, rate = header.frame_count, header.frame_rate
        if node.node_id.startswith("overlap.fi."):
            count, rate = 2 * count - 1, 2 * rate
        target = Path(handoff.output_targets[0].path)
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                input_artifact.path,
                "-map",
                "0:v:0",
                "-vf",
                f"fps={rate.numerator}/{rate.denominator}",
                "-frames:v",
                str(count),
                "-an",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-pix_fmt",
                "yuv422p10le",
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
                str(target),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        app.command(
            {
                "operation": "submit_external",
                "run_id": run.run_id,
                "node_run_id": node.node_run_id,
                "handoff_id": handoff.handoff_id,
            }
        )
    else:
        pytest.fail("融合三章链没有在有界步骤内收敛")
    program_node = next(node for node in run.node_runs if node.node_id == "overlap.program")
    if outcome != "complete":
        assert program_node.state is NodeRunState.FAILED and not program_node.output_artifact_ids
        assert runtime.repository.get_latest("overlap.program") is None
        assert runtime.repository.get_latest("overlap.final") is None
        assert program_node.error is not None
        expected_reason = (
            FailureReason.CANCELLED if outcome == "cancel" else FailureReason.EXECUTION_ERROR
        )
        assert program_node.error.reason is expected_reason
        assert len(partials) == 1 and partials[0].read_bytes() == b"synthetic-partial-not-media"
        assert source.read_bytes() == source_before
        raw_nodes = [node for node in run.node_runs if node.node_id.startswith("overlap.fi.")]
        assert len(raw_nodes) == 3 and all(
            node.state is NodeRunState.COMPLETED for node in raw_nodes
        )
        assert all(
            Path(runtime.repository.get_artifact(node.output_artifact_ids[0]).path).is_file()
            for node in raw_nodes
        )
        return
    program = runtime.repository.get_artifact(program_node.output_artifact_ids[0])
    assert fused.Metadata.model_validate(program.media_info[fused.NAMESPACE]).frame_count == 24
    assert probe_header(Path(program.path), count_frames=True).video.frame_count == 24
    assert not list(Path(program_node.work_dir).rglob("*.mov"))
    crop_nodes = [node for node in run.node_runs if node.node_id.startswith("overlap.crop.")]
    assert len(crop_nodes) == (3 if export else 0)
    for node in crop_nodes:
        assert len(node.output_artifact_ids) == 1
        artifact = runtime.repository.get_artifact(node.output_artifact_ids[0])
        metadata = BatchMetadata.model_validate(artifact.media_info[BATCH_NAMESPACE])
        assert metadata.role == "crop" and Path(artifact.path).is_file()
        assert (
            probe_header(Path(artifact.path), count_frames=True).video.frame_count
            == metadata.frame_count
        )
    final = runtime.repository.get_artifact(
        next(node for node in run.node_runs if node.node_id == "overlap.final").output_artifact_ids[
            0
        ]
    )
    assert (
        fused.Metadata.model_validate(final.media_info[fused.NAMESPACE]).producer_version == "0.3.6"
    )
    final_header = probe_header(Path(final.path), count_frames=True)
    assert final_header.video.frame_count == 24 and len(final_header.audios) == 1
    final_attempt = next(node for node in run.node_runs if node.node_id == "overlap.final")
    final_request = runtime._execution_request(run, final_attempt)
    with pytest.raises(Av27MediaError):
        final_publish.preflight("final", final_request.inputs, final_request.node.parameters)
    assert source.read_bytes() == source_before
    reopened = ProjectStore.open(path).load()
    assert any(node.type_id == fused.PROGRAM_TYPE for node in reopened.project.graph.nodes)
    reused = runtime.run_until_blocked(runtime.create_run().run_id)
    assert reused.state is RunState.COMPLETED
    assert all(node.reused_from_result_id is not None for node in reused.node_runs)
