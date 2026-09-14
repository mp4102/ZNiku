"""普通工作源三条路线的短合成全链路；测试用恒等 oracle 不进入产品默认检查。

不读取用户媒体、不晋级旧 T1，也不把人工生成的 FI 替身说成模型能力验收。
"""

from __future__ import annotations

import json
import runpy
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from test_prepared_color_routes import fixture_source
from test_source_preparation_kernel import _tool
from zniku.avenhance_v27.probe import probe_header
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.prepared_source import audio as strict_audio
from zniku.prepared_source.audio import verify_audio_content
from zniku.prepared_source.work_contracts import OVERLAP_NAMESPACE, WorkOverlapMetadata
from zniku.prepared_source.work_definitions import (
    built_in_overlap_definitions,
    external_definition,
    overlap_python_adapters,
    overlap_validators,
)
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.work_application import choose, create, full, view
from zniku.runtime import NodeRunState, RunState
from zniku.source_color.models import T1_PROMOTED as COLOR_T1_PROMOTED
from zniku.source_preparation import work_inspection
from zniku.source_preparation.models import T1_PROMOTED
from zniku.source_preparation.process import stream_process
from zniku.source_preparation.work_definitions import (
    register_source_preparation_adapters,
    register_source_preparation_validators,
    source_preparation_definitions,
)


def application(output: Path) -> ProjectServiceApplication:
    return ProjectServiceApplication(
        work_root=output / "unused",
        project_data_default=True,
        definition_catalog=(
            *built_in_media_definitions(),
            *source_preparation_definitions(),
            *built_in_overlap_definitions(),
            external_definition(),
        ),
        python_adapters={
            **media_python_adapters(),
            **register_source_preparation_adapters(),
            **overlap_python_adapters(),
        },
        validators={
            **media_validators(),
            **register_source_preparation_validators(),
            **overlap_validators(),
        },
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )


def pixels(path: Path) -> tuple[str, ...]:
    """测试独立比较解码帧的顺序及像素；忽略被明确改写的 PTS。"""
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-an",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "passthrough",
            "-f",
            "framehash",
            "-",
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return tuple(
        line.rsplit(",", 1)[-1].strip()
        for line in result.stdout.decode().splitlines()
        if line and not line.startswith("#")
    )


@pytest.mark.parametrize("route", ["direct", "builtin", "external"])
def test_work_route_to_final_without_promoting_strict_t1(
    route: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "tools"))
    helpers = runpy.run_path(str(root / "tools/run_source_aligned_smoke.py"))
    artifact, command, latest = (helpers[name] for name in ("_artifact", "_command", "_latest"))
    output = root / "build" / f"work-{route[0]}-{uuid4().hex[:6]}"
    output.mkdir(parents=True)
    source, reference = fixture_source(output, "direct")
    if route == "builtin":
        source = output / "retime-source.mkv"
        _tool(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(reference),
                "-map",
                "0",
                "-c",
                "copy",
                "-bsf:v",
                "setts=pts=PTS*1.1:dts=DTS*1.1",
                str(source),
            ]
        )
    if route == "external":
        # 新外部参考故意 12 帧而非原件 9 帧；新计划和音频必须跟随参考，不能偷用原件 N。
        reference = output / "new-reference.mkv"
        _tool(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=320x180:r=30",
                "-t",
                "0.4",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                "-color_range",
                "tv",
                "-chroma_sample_location",
                "left",
                str(output / "new-video.mkv"),
            ]
        )
        _tool(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=700:sample_rate=48000",
                "-t",
                "0.4",
                "-c:a",
                "aac",
                str(output / "new-audio.aac"),
            ]
        )
        _tool(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(output / "new-video.mkv"),
                "-i",
                str(output / "new-audio.aac"),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c",
                "copy",
                str(reference),
            ]
        )
    original = source.read_bytes()
    scans: list[list[str]] = []

    def track(argv: list[str], **kwargs: Any) -> bool:
        scans.append(argv)
        return stream_process(argv, **kwargs)

    def forbidden_identity_audit(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("普通工作链不应调用旧全 PCM 恒等审计")

    monkeypatch.setattr(work_inspection, "stream_process", track)
    monkeypatch.setattr(strict_audio, "verify_audio_content", forbidden_identity_audit)
    assert T1_PROMOTED is False and COLOR_T1_PROMOTED is False
    app = application(output)
    create(
        app,
        {
            "contract_version": "0.3.4-work.1",
            "project_path": str(output / "w.zniku"),
            "project_id": "synthetic.work",
            "project_name": "合成普通工作链",
            "source_path": str(source),
        },
    )
    diagnosis = command(app, {"operation": "run_all"})
    assert diagnosis.run.state is RunState.COMPLETED, diagnosis.model_dump_json(indent=2)
    state = app.inspect()
    request = {
        "contract_version": "0.3.4-work.1",
        "project_session_id": state.project_session_id,
        "run_id": diagnosis.run.run_id,
    }
    inspected = view(app, request)
    assert inspected.decision == ("preparation_required" if route == "builtin" else "direct")
    assert inspected.inspection_scope == "frames_eof"
    assert inspected.color_interpretation_required
    confirmations = ["color_interpretation", "target_frame_rate"]
    if route == "builtin":
        confirmations.append("retime")
    if route == "external":
        confirmations = ["target_frame_rate", "external_reference"]
    choose(
        app,
        {
            **request,
            "expected_storage_revision": state.storage_revision,
            "route": route,
            "target_frame_rate": "30/1",
            "interpretation_policy": "declared_only"
            if route == "external"
            else "operator_confirmed_bt709_limited_left",
            "confirmations": confirmations,
        },
    )
    prepared = command(app, {"operation": "run_all"})
    if route == "external":
        waiting = latest(prepared)["source-preparation-prepare"]
        assert waiting.state is NodeRunState.WAITING_EXTERNAL and waiting.external_handoff
        shutil.copyfile(reference, waiting.external_handoff.output_targets[0].path)
        app.command(
            {
                "operation": "submit_external",
                "run_id": prepared.run.run_id,
                "node_run_id": waiting.node_run_id,
                "handoff_id": waiting.external_handoff.handoff_id,
            }
        )
        assert app.wait_until_idle(timeout=60)
        submitted = app.inspect_run_detail(prepared.run.run_id)
        failed = latest(submitted)["source-preparation-admission"]
        assert failed.state is NodeRunState.FAILED
        assert latest(submitted)["source-preparation-prepare"].state is NodeRunState.COMPLETED
        # 外部来件先观察，只有读到其缺失项后才能确认解释；重试仅从准入开始，不重交副本。
        state = app.inspect()
        candidate_view = view(app, {**request, "run_id": prepared.run.run_id})
        assert candidate_view.color_interpretation_required
        choose(
            app,
            {
                **request,
                "run_id": prepared.run.run_id,
                "expected_storage_revision": state.storage_revision,
                "route": "external",
                "target_frame_rate": "30/1",
                "interpretation_policy": "operator_confirmed_bt709_limited_left",
                "confirmations": [
                    "external_reference",
                    "color_interpretation",
                    "target_frame_rate",
                ],
            },
        )
        prepared = command(
            app,
            {
                "operation": "rerun_from_here",
                "run_id": prepared.run.run_id,
                "node_id": failed.node_id,
            },
        )
        assert (
            latest(prepared)["source-preparation-prepare"].output_artifact_ids
            == latest(submitted)["source-preparation-prepare"].output_artifact_ids
        )
    assert prepared.run.state is RunState.COMPLETED, prepared.model_dump_json(indent=2)
    if route == "external":
        assert view(app, {**request, "run_id": prepared.run.run_id}).source_frame_count == 12
    assert sum(argv[-1] == str(source) for argv in scans) == 1
    assert len(scans) == (1 if route == "direct" else 2)
    admitted = artifact(prepared, "source-preparation-admission")
    assert (Path(admitted.path) == source) is (route == "direct")
    if route == "builtin":
        assert pixels(source) == pixels(Path(admitted.path))
    state = app.inspect()
    full(
        app,
        {
            "contract_version": "0.3.4-work.1",
            "project_session_id": state.project_session_id,
            "expected_storage_revision": state.storage_revision,
            "preparation_run_id": prepared.run.run_id,
            "processing": {
                "mr": {"mode": "off"},
                "settings": {
                    "chapter_selector": {"mode": "average", "count": 3},
                    "leaf_max_minutes": 5,
                },
                "enhancement": {"model_name": "synthetic-no-ai", "actual_scale_factor": 1},
                "program_encode": {"encoder": "cpu"},
            },
            "publication": {
                "output_root": str(output),
                "title": "Synthetic work",
                "year": "2026",
                "overwrite": False,
                "layout": "title_subdirectory",
            },
        },
        expand=True,
    )
    detail = command(app, {"operation": "run_all"})
    for _ in range(6):
        waiting = next(
            (n for n in latest(detail).values() if n.state is NodeRunState.WAITING_EXTERNAL), None
        )
        assert waiting is not None and waiting.external_handoff, detail.model_dump_json(indent=2)
        incoming = next(
            a
            for a in detail.artifacts
            if a.artifact_id in waiting.input_artifact_ids and a.kind == "VideoFile"
        )
        metadata = WorkOverlapMetadata.model_validate_json(
            json.dumps(incoming.media_info[OVERLAP_NAMESPACE])
        )
        rate = Fraction(metadata.frame_rate)
        if waiting.node_id.startswith("overlap.enhance."):
            args = ["-i", incoming.path, "-an"]
        else:
            assert metadata.context
            rate *= 2
            args = [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=s=1920x1080:r={rate}",
                "-frames:v",
                str(metadata.context.raw_fi_frame_count),
            ]
        _tool(
            [
                "ffmpeg",
                "-v",
                "error",
                *args,
                "-vf",
                "format=yuv422p10le,setsar=1/1",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv422p10le",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                "-color_range",
                "tv",
                "-chroma_sample_location",
                "left",
                "-video_track_timescale",
                str(rate.numerator),
                waiting.external_handoff.output_targets[0].path,
            ]
        )
        detail = command(
            app,
            {
                "operation": "submit_external",
                "run_id": detail.run.run_id,
                "node_run_id": waiting.node_run_id,
                "handoff_id": waiting.external_handoff.handoff_id,
            },
        )
    assert detail.run.state is RunState.COMPLETED, detail.model_dump_json(indent=2)
    final = artifact(detail, "overlap.final", "media")
    header = probe_header(final.path, count_frames=True)
    assert header.video.frame_count == (24 if route == "external" else 18)
    assert len(header.audios) == 1
    verify_audio_content(reference if route == "external" else source, Path(final.path))
    assert source.read_bytes() == original
    reused = command(app, {"operation": "run_all"})
    assert reused.run.state is RunState.COMPLETED
    assert all(n.reused_from_result_id is not None for n in latest(reused).values())
