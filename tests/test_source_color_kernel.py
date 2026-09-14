"""仅以合成媒体检验新色彩事实、显式工作解释与保内容边界。"""

from __future__ import annotations

import shutil
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from test_source_preparation_kernel import _input, _tool
from test_source_preparation_kernel import _request as old_request
from test_source_preparation_kernel import media as media
from zniku.graph import ExecutionMode, GraphValidator, ManualExternalExecutorSpec, NodeDefinition
from zniku.media import runner_media_probe
from zniku.media.probe import MediaNodeError
from zniku.runtime import (
    ManualSubmission,
    NodeExecutionRequest,
    NodeRunner,
    RunnerError,
    RunnerInput,
)
from zniku.runtime.runner import OutputPathSpec, RunnerArtifact
from zniku.source_color import (
    admission_definition,
    build_preparation_graph,
    builtin_prepare_definition,
    check_reference_binding,
    diagnostics_definition,
    external_repair_definition,
    register_source_preparation_adapters,
    register_source_preparation_validators,
    source_definition,
    source_preparation_definitions,
)
from zniku.source_color.contracts import read_diagnosis
from zniku.source_color.inspection import inspect_source
from zniku.source_color.models import (
    DiagnosticReport,
    InterpretationPolicy,
    SourceGate,
    summary_for_report,
)
from zniku.source_color.policy import resolve_interpretation
from zniku.source_color.preservation import create_t1_candidate, verify_preservation
from zniku.source_preparation.progress import cancellation_scope


def _runner(path: Path) -> NodeRunner:
    return NodeRunner(
        path,
        python_adapters=register_source_preparation_adapters(),
        validators=register_source_preparation_validators(),
        media_probe=runner_media_probe,
    )


def _request(
    definition: NodeDefinition, parameters: dict[str, Any], inputs: tuple[RunnerInput, ...] = ()
) -> NodeExecutionRequest:
    request = old_request(definition, parameters, inputs)
    return replace(
        request,
        output_paths=tuple(
            OutputPathSpec(item.port_id, item.relative_path)
            for item in definition.executor.output_paths
        ),
    )


def _diagnose(runner: NodeRunner, path: Path) -> tuple[RunnerArtifact, RunnerArtifact]:
    original = runner.run_automatic(
        _request(source_definition(), {"source_path": str(path)})
    ).artifacts[0]
    diagnosis = runner.run_automatic(
        _request(
            diagnostics_definition(),
            {"target_frame_rate": "30/1"},
            (_input("original_media", original),),
        )
    ).artifacts[0]
    return original, diagnosis


def _unknown(media: Path, destination: Path) -> Path:
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-c:v",
            "copy",
            "-bsf:v",
            "h264_metadata=colour_primaries=2:transfer_characteristics=2:matrix_coefficients=2",
            "-color_primaries",
            "unknown",
            "-color_trc",
            "unknown",
            "-colorspace",
            "unknown",
            str(destination),
        ]
    )
    return destination


def test_full_declared_source_observation(media: Path) -> None:
    report = inspect_source(media, str(uuid4()), target_frame_rate="30/1")
    assert report.video.frame_count == 30
    assert not report.findings, report.model_dump_json(indent=2)
    working, basis = resolve_interpretation(report.observed_signal, "declared_only")
    assert working.color_space == "bt709" and len(basis) == 5


def test_missing_color_needs_explicit_work_interpretation(media: Path, tmp_path: Path) -> None:
    path = tmp_path / "unknown.mp4"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-c:v",
            "copy",
            "-bsf:v",
            "h264_metadata=colour_primaries=2:transfer_characteristics=2:matrix_coefficients=2",
            "-color_primaries",
            "unknown",
            "-color_trc",
            "unknown",
            "-colorspace",
            "unknown",
            str(path),
        ]
    )
    report = inspect_source(path, str(uuid4()), target_frame_rate="30/1")
    assert report.observed_signal.missing_fields
    assert not report.observed_signal.unsupported, report.model_dump_json(indent=2)
    with pytest.raises(MediaNodeError, match="COLOR_UNSPECIFIED"):
        resolve_interpretation(report.observed_signal, "declared_only")
    working, basis = resolve_interpretation(
        report.observed_signal, "operator_confirmed_bt709_limited_left"
    )
    assert working.color_space == "bt709"
    assert any(item.source == "operator_confirmation" for item in basis)


def test_explicit_routes_new_exact_and_closed_parameters() -> None:
    from pydantic import ValidationError

    from zniku.source_color.definitions import definition_role
    from zniku.source_preparation import source_preparation_definitions as old_definitions

    definitions = source_preparation_definitions()
    assert len(definitions) == 7
    assert all(item.version == "0.3.4-color.1" for item in definitions)
    assert all(definition_role(item) is None for item in old_definitions())
    for route in ("diagnose", "direct", "builtin", "external"):
        graph = build_preparation_graph("C:/synthetic.mkv", route=route, target_frame_rate="30/1")
        GraphValidator(definitions).validate(graph)
    with pytest.raises(ValidationError):
        build_preparation_graph(
            "C:/synthetic.mkv", interpretation_policy=cast(InterpretationPolicy, "force")
        )


@pytest.mark.parametrize("missing", [False, True])
def test_real_runner_admission_keeps_observation_separate(
    media: Path,
    tmp_path: Path,
    missing: bool,
) -> None:
    source = _unknown(media, tmp_path / "unknown.mkv") if missing else media
    before = source.read_bytes()
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, source)
    report = read_diagnosis(_input("diagnosis", diagnosis))
    assert DiagnosticReport.model_validate_json(report.model_dump_json()) == report
    inputs = (
        _input("original_media", original),
        _input("reference_media", original),
        _input("audio_sources", original, 0),
        _input("diagnosis", diagnosis),
    )
    params: dict[str, Any] = {"audio_policy": "none"}
    if missing:
        with pytest.raises(RunnerError, match="COLOR_UNSPECIFIED"):
            runner.run_automatic(_request(admission_definition(), params, inputs))
        params["interpretation_policy"] = "operator_confirmed_bt709_limited_left"
    result = runner.run_automatic(_request(admission_definition(), params, inputs))
    outputs = {item.producer_port_id: item for item in result.artifacts}
    gate = check_reference_binding(
        _input("video", outputs["video"]), _input("gate", outputs["gate"])
    )
    assert gate.observed_signal == report.observed_signal
    assert gate.working_signal.color_space == "bt709"
    assert bool(gate.observed_signal.missing_fields) == missing
    assert outputs["video"].path == source
    assert source.read_bytes() == before
    assert SourceGate.model_validate_json(gate.model_dump_json()) == gate
    invalid_geometry = gate.model_dump()
    invalid_geometry["geometry"]["width"] += 2
    with pytest.raises(ValueError, match="准入几何"):
        SourceGate.model_validate(invalid_geometry)
    changed = gate.model_dump()
    changed["observed_signal"]["missing_fields"] = () if missing else ("color_space",)
    with pytest.raises((ValueError, MediaNodeError), match="COLOR_REPORT"):
        SourceGate.model_validate(changed)


def test_external_preservation_can_finish_before_work_interpretation(
    media: Path, tmp_path: Path
) -> None:
    source = _unknown(media, tmp_path / "unknown.mkv")
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, source)
    request = _request(
        external_repair_definition("mkv"),
        {"target_frame_rate": "30/1"},
        (
            _input("original_media", original),
            _input("diagnosis", diagnosis),
        ),
    )
    handoff = runner.prepare_manual(request)
    shutil.copyfile(source, handoff.outputs[0].path)
    prepared = runner.submit_manual(request, handoff, ManualSubmission()).artifacts[0]
    inputs = (
        _input("original_media", original),
        _input("reference_media", prepared),
        _input("audio_sources", original, 0),
        _input("diagnosis", diagnosis),
    )
    with pytest.raises(RunnerError, match="COLOR_UNSPECIFIED"):
        runner.run_automatic(_request(admission_definition(), {"audio_policy": "none"}, inputs))
    result = runner.run_automatic(
        _request(
            admission_definition(),
            {
                "audio_policy": "none",
                "interpretation_policy": "operator_confirmed_bt709_limited_left",
            },
            inputs,
        )
    )
    assert len(result.artifacts) == 2
    assert prepared.path.is_file()


@pytest.mark.parametrize("direction", ["unknown_to_known", "known_to_unknown", "limited_to_full"])
def test_same_pixels_do_not_prove_color_preservation(
    media: Path, tmp_path: Path, direction: str
) -> None:
    unknown = _unknown(media, tmp_path / "unknown.mkv")
    source, candidate = (unknown, media) if direction == "unknown_to_known" else (media, unknown)
    if direction == "limited_to_full":
        candidate = tmp_path / "full.mkv"
        _tool(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(media),
                "-c",
                "copy",
                "-bsf:v",
                "h264_metadata=video_full_range_flag=1",
                "-color_range",
                "pc",
                str(candidate),
            ]
        )
    report = inspect_source(source, str(uuid4()), target_frame_rate="30/1")
    with pytest.raises(MediaNodeError, match=r"COLOR_PRESERVATION|CANDIDATE_ADMISSION"):
        verify_preservation(source, candidate, report, target_frame_rate="30/1")


def test_container_and_sps_conflict_is_not_hidden_by_ffprobe(media: Path, tmp_path: Path) -> None:
    path = tmp_path / "container-conflict.mp4"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-c",
            "copy",
            "-colorspace",
            "smpte170m",
            str(path),
        ]
    )
    report = inspect_source(path, str(uuid4()), target_frame_rate="30/1")
    assert "color_space" in report.observed_signal.conflicts
    with pytest.raises(MediaNodeError, match="COLOR_UNSUPPORTED"):
        resolve_interpretation(report.observed_signal, "operator_confirmed_bt709_limited_left")


def test_all_sps_and_frames_detect_middle_change_then_restore(tmp_path: Path) -> None:
    first = tmp_path / "first.h264"
    middle = tmp_path / "middle.h264"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x48:r=30",
            "-frames:v",
            "6",
            "-c:v",
            "libx264",
            "-bf",
            "0",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709:force-cfr=1",
            str(first),
        ]
    )
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(first),
            "-c",
            "copy",
            "-bsf:v",
            "h264_metadata=matrix_coefficients=6",
            str(middle),
        ]
    )
    concatenated = tmp_path / "three.h264"
    concatenated.write_bytes(first.read_bytes() + middle.read_bytes() + first.read_bytes())
    path = tmp_path / "changed.mkv"
    _tool(["ffmpeg", "-v", "error", "-r", "30", "-i", str(concatenated), "-c", "copy", str(path)])
    report = inspect_source(path, str(uuid4()), target_frame_rate="30/1")
    assert report.video.frame_count == 18
    assert "color_space" in report.observed_signal.bitstream.changes
    assert "color_space" in report.observed_signal.frames.changes
    with pytest.raises(MediaNodeError, match="COLOR_UNSUPPORTED"):
        resolve_interpretation(report.observed_signal, "operator_confirmed_bt709_limited_left")


def test_unknown_report_cannot_be_minted_by_manual_definition(media: Path, tmp_path: Path) -> None:
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, media)
    official = diagnostics_definition()
    forged = official.model_copy(
        update={
            "type_id": "synthetic.forged",
            "execution_mode": ExecutionMode.MANUAL_EXTERNAL,
            "executor": ManualExternalExecutorSpec(
                output_paths=official.executor.output_paths, instructions="合成反例"
            ),
        }
    )
    request = _request(forged, {}, (_input("original_media", original),))
    handoff = runner.prepare_manual(request)
    shutil.copyfile(diagnosis.path, handoff.outputs[0].path)
    with pytest.raises(RunnerError, match="DEFINITION"):
        runner.submit_manual(request, handoff, ManualSubmission())


def test_old_t1_promotion_does_not_enable_new_policy(
    media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zniku.source_preparation import models as old_models

    monkeypatch.setattr(old_models, "T1_PROMOTED", True)
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, media)
    with pytest.raises(RunnerError, match="STRATEGY_NOT_PROMOTED"):
        runner.run_automatic(
            _request(
                builtin_prepare_definition(),
                {"target_frame_rate": "30/1"},
                (
                    _input("original_media", original),
                    _input("diagnosis", diagnosis),
                ),
            )
        )
    assert (
        summary_for_report(read_diagnosis(_input("diagnosis", diagnosis)))["available_strategies"]
        == []
    )


def test_t1_synthetic_preserves_unknown_and_runs_in_isolated_test(
    media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zniku.source_color import models
    from zniku.source_preparation.preservation import resolve_mkvmerge

    try:
        resolve_mkvmerge()
    except MediaNodeError:
        pytest.skip("纯合成 T1 专项需要 MKVToolNix")
    unknown = _unknown(media, tmp_path / "unknown.mkv")
    longer = tmp_path / "clock.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-stream_loop",
            "29",
            "-i",
            str(unknown),
            "-c",
            "copy",
            "-bsf:v",
            "setts=pts=PTS*1.0002:dts=DTS*1.0002",
            str(longer),
        ]
    )
    report = inspect_source(longer, str(uuid4()), target_frame_rate="30/1")
    assert report.candidate_strategies == ("t1-clock-quantization/2",)
    candidate = tmp_path / "prepared.mkv"
    create_t1_candidate(longer, candidate, report, target_frame_rate="30/1")
    fresh = verify_preservation(longer, candidate, report, target_frame_rate="30/1")
    assert fresh.observed_signal.missing_fields == report.observed_signal.missing_fields
    # 仅合成用例注入本版本开关，产品字面量和旧版策略均不改；这不是策略晋级证明。
    monkeypatch.setattr(models, "T1_PROMOTED", True)
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, longer)
    result = runner.run_automatic(
        _request(
            builtin_prepare_definition(),
            {"target_frame_rate": "30/1"},
            (
                _input("original_media", original),
                _input("diagnosis", diagnosis),
            ),
        )
    )
    assert len(result.artifacts) == 1
    assert result.artifacts[0].path.is_file()


def test_cancelled_full_scan_never_registers_diagnosis(media: Path, tmp_path: Path) -> None:
    runner = _runner(tmp_path / "attempts")
    original = runner.run_automatic(
        _request(source_definition(), {"source_path": str(media)})
    ).artifacts[0]
    event = threading.Event()
    with cancellation_scope(event):
        event.set()
        with pytest.raises(RunnerError, match="CANCELLED"):
            runner.run_automatic(
                _request(diagnostics_definition(), {}, (_input("original_media", original),))
            )
    assert not list((tmp_path / "attempts").rglob("source-check.json"))
