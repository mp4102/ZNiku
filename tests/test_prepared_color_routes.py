"""三条新 exact 路线运行真实短合成 Graph 到 Final；不晋级 T1、不调用真实 AI。"""

from __future__ import annotations

import json
import runpy
import shutil
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import pytest

from test_source_preparation_kernel import _tool
from zniku.avenhance_v27.probe import probe_header
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.prepared_color.definitions import (
    built_in_overlap_definitions,
    external_definition,
    overlap_python_adapters,
    overlap_validators,
)
from zniku.prepared_color.node_contracts import OVERLAP_NAMESPACE, OverlapMetadata
from zniku.prepared_source.audio import verify_audio_content
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.prepared_color_application import choose, create, full, view
from zniku.runtime import NodeRunState, RunState
from zniku.source_color import models
from zniku.source_color.definitions import (
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


def fixture_source(output: Path, route: str) -> tuple[Path, Path]:
    """缺少三项声明的短原件，保留规范范围和音频；修复只改时钟、不补颜色。"""
    encoded, audio, reference = output / "v.mkv", output / "a.aac", output / "reference.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30",
            "-frames:v",
            "9",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-bf",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-x264-params",
            "colorprim=undef:transfer=undef:colormatrix=undef:range=limited:chromaloc=0",
            str(encoded),
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
            "sine=frequency=330:sample_rate=48000",
            "-t",
            "0.3",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(audio),
        ]
    )
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(encoded),
            "-i",
            str(audio),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c",
            "copy",
            str(reference),
        ]
    )
    source = reference
    if route != "direct":
        source = output / "source.mkv"
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
                "setts=pts=PTS*1.01:dts=DTS*1.01",
                str(source),
            ]
        )
    return source, reference


@pytest.mark.parametrize("route", ["direct", "builtin", "external"])
def test_synthetic_new_color_route_to_final(route: str, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "tools"))
    helpers = runpy.run_path(str(root / "tools/run_source_aligned_smoke.py"))
    _artifact, _command, _latest = (helpers[name] for name in ("_artifact", "_command", "_latest"))

    output = root / "build" / f"pc-{route[0]}-{uuid4().hex[:6]}"
    output.mkdir(parents=True)
    source, reference = fixture_source(output, route)
    original = source.read_bytes()
    assert models.T1_PROMOTED is False
    if route == "builtin":
        monkeypatch.setattr(models, "T1_PROMOTED", True)
    app = application(output)
    create(
        app,
        {
            "contract_version": "0.3.4-color.1",
            "project_path": str(output / "c.zniku"),
            "project_id": "synthetic.color",
            "project_name": "合成色彩链",
            "source_path": str(source),
        },
    )
    diagnosis = _command(app, {"operation": "run_all"})
    assert diagnosis.run.state is RunState.COMPLETED
    state = app.inspect()
    view_request = {
        "contract_version": "0.3.4-color.1",
        "project_session_id": state.project_session_id,
        "run_id": diagnosis.run.run_id,
    }
    summary = view(app, view_request)
    assert summary.color_interpretation_required and summary.color_interpretation_available
    choose(
        app,
        {
            **view_request,
            "expected_storage_revision": state.storage_revision,
            "route": route,
            "target_frame_rate": "30/1",
            "interpretation_policy": "operator_confirmed_bt709_limited_left",
        },
    )
    prepared = _command(app, {"operation": "run_all"})
    if route == "external":
        waiting = _latest(prepared)["source-preparation-prepare"]
        assert (
            waiting.state is NodeRunState.WAITING_EXTERNAL and waiting.external_handoff is not None
        )
        shutil.copyfile(reference, waiting.external_handoff.output_targets[0].path)
        prepared = _command(
            app,
            {
                "operation": "submit_external",
                "run_id": prepared.run.run_id,
                "node_run_id": waiting.node_run_id,
                "handoff_id": waiting.external_handoff.handoff_id,
            },
        )
    assert prepared.run.state is RunState.COMPLETED, prepared.model_dump_json(indent=2)
    admitted = _artifact(prepared, "source-preparation-admission")
    assert (Path(admitted.path) == source) is (route == "direct")
    state = app.inspect()
    full(
        app,
        {
            "contract_version": "0.3.4-color.1",
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
                "title": "Synthetic color",
                "year": "2026",
                "overwrite": False,
                "layout": "title_subdirectory",
            },
        },
        expand=True,
    )
    detail = _command(app, {"operation": "run_all"})
    for _ in range(6):
        waiting = next(
            (
                node
                for node in _latest(detail).values()
                if node.state is NodeRunState.WAITING_EXTERNAL
            ),
            None,
        )
        assert waiting is not None and waiting.external_handoff is not None, detail.model_dump_json(
            indent=2
        )
        input_artifact = next(
            item
            for item in detail.artifacts
            if item.artifact_id in waiting.input_artifact_ids and item.kind == "VideoFile"
        )
        metadata = OverlapMetadata.model_validate_json(
            json.dumps(input_artifact.model_dump(mode="json")["media_info"][OVERLAP_NAMESPACE])
        )
        target = waiting.external_handoff.output_targets[0].path
        rate = Fraction(metadata.frame_rate)
        if waiting.node_id.startswith("overlap.enhance."):
            args = ["-i", input_artifact.path, "-an"]
        else:
            assert metadata.context is not None
            rate *= 2
            args = [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=s=1920x1080:r={rate}",
                "-frames:v",
                str(metadata.context.raw_fi_frame_count),
            ]
        # 人工替身不带色彩标签；现场raw观察允许继承，但不会写成实测709。
        _tool(
            [
                "ffmpeg",
                "-v",
                "error",
                *args,
                "-vf",
                "setparams=range=limited:color_primaries=unknown:color_trc=unknown:colorspace=unknown,format=yuv422p10le,setsar=1/1",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv422p10le",
                "-color_primaries",
                "unknown",
                "-color_trc",
                "unknown",
                "-colorspace",
                "unknown",
                "-video_track_timescale",
                str(rate.numerator),
                target,
            ]
        )
        detail = _command(
            app,
            {
                "operation": "submit_external",
                "run_id": detail.run.run_id,
                "node_run_id": waiting.node_run_id,
                "handoff_id": waiting.external_handoff.handoff_id,
            },
        )
    assert detail.run.state is RunState.COMPLETED, detail.model_dump_json(indent=2)
    final = _artifact(detail, "overlap.final", "media")
    header = probe_header(final.path, count_frames=True)
    assert header.video.frame_count == 18 and len(header.audios) == 1
    verify_audio_content(source, Path(final.path))
    assert source.read_bytes() == original
    assert (
        _artifact(detail, "overlap.program").media_info[OVERLAP_NAMESPACE]["interpretation"][
            "interpretation_policy"
        ]
        == "operator_confirmed_bt709_limited_left"
    )
    reused = _command(app, {"operation": "run_all"})
    assert reused.run.state is RunState.COMPLETED
    assert all(node.reused_from_result_id is not None for node in _latest(reused).values())
