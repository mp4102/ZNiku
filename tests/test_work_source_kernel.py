"""普通工作源仅用合成媒体验证：单次扫描、显式转换、新参考与失败关闭。"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from test_source_preparation_kernel import _input, _tool
from test_source_preparation_kernel import _request as old_request
from test_source_preparation_kernel import media as media
from zniku.avenhance_v27.probe import probe_header
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
from zniku.source_preparation import work_inspection
from zniku.source_preparation.inspection import read_header
from zniku.source_preparation.process import stream_process
from zniku.source_preparation.progress import cancellation_scope
from zniku.source_preparation.work_contracts import (
    check_reference_binding,
    read_diagnosis,
    read_reference_report,
)
from zniku.source_preparation.work_definitions import (
    admission_definition,
    builtin_prepare_definition,
    definition_role,
    diagnostics_definition,
    external_repair_definition,
    register_source_preparation_adapters,
    register_source_preparation_validators,
    source_definition,
    source_preparation_definitions,
)
from zniku.source_preparation.work_inspection import inspect_source
from zniku.source_preparation.work_models import (
    AdmissionParameters,
    PrepareParameters,
    WorkDiagnosticReport,
    WorkSourceGate,
    observed_rate_matches,
    resolve_interpretation,
)
from zniku.source_preparation.work_template import build_preparation_graph


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
    return replace(
        old_request(definition, parameters, inputs),
        output_paths=tuple(
            OutputPathSpec(o.port_id, o.relative_path) for o in definition.executor.output_paths
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


def _admit(
    runner: NodeRunner,
    original: RunnerArtifact,
    diagnosis: RunnerArtifact,
    reference: RunnerArtifact | None = None,
    *,
    external: bool = False,
) -> WorkSourceGate:
    ref = original if reference is None else reference
    result = runner.run_automatic(
        _request(
            admission_definition(),
            {
                "target_frame_rate": "30/1",
                "audio_policy": "reference" if external else "original",
                "interpretation_policy": "operator_confirmed_bt709_limited_left",
            },
            (
                _input("original_media", original),
                _input("reference_media", ref),
                _input("diagnosis", diagnosis),
                _input("audio_sources", ref if external else original, 0),
            ),
        )
    )
    outputs = {a.producer_port_id: a for a in result.artifacts}
    return check_reference_binding(
        _input("video", outputs["video"]), _input("gate", outputs["gate"])
    )


def test_direct_one_full_scan_no_copy(
    media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    original_process = stream_process

    def spy(argv: list[str], **kwargs: Any) -> bool:
        calls.append(argv)
        return original_process(argv, **kwargs)

    monkeypatch.setattr(work_inspection, "stream_process", spy)
    runner = _runner(tmp_path / "attempts")
    before = media.read_bytes()
    original, diagnosis = _diagnose(runner, media)
    report = read_diagnosis(_input("diagnosis", diagnosis))
    assert report.status == "direct", report.model_dump_json(indent=2)
    assert report.video is not None and report.video.frame_count == 30
    gate = _admit(runner, original, diagnosis)
    assert gate.reference_media_artifact_id == original.artifact_id
    assert gate.signal == gate.working_signal
    assert WorkSourceGate.model_validate_json(gate.model_dump_json()) == gate
    assert len(calls) == 1 and "-show_frames" in calls[0] and "-show_packets" in calls[0]
    assert media.read_bytes() == before


def test_drift_explicit_ffv1_keeps_frames(
    media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drift = tmp_path / "drift.mkv"
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
            "setts=pts=PTS*1.1:dts=DTS*1.1",
            str(drift),
        ]
    )
    calls: list[list[str]] = []
    original_process = stream_process

    def spy(argv: list[str], **kwargs: Any) -> bool:
        calls.append(argv)
        return original_process(argv, **kwargs)

    monkeypatch.setattr(work_inspection, "stream_process", spy)
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, drift)
    report = read_diagnosis(_input("diagnosis", diagnosis))
    assert report.status == "preparation_required", report.model_dump_json(indent=2)
    prepared = runner.run_automatic(
        _request(
            builtin_prepare_definition(),
            {"target_frame_rate": "30/1", "retime_confirmed": True},
            (_input("original_media", original), _input("diagnosis", diagnosis)),
        )
    ).artifacts[0]
    fresh = read_reference_report(_input("reference_media", prepared))
    assert fresh.video is not None and fresh.video.frame_count == 30 and fresh.video.codec == "ffv1"
    gate = _admit(runner, original, diagnosis, prepared)
    assert gate.preparation_strategy == "frame-retime-ffv1/1"
    assert len(calls) == 2, "原件及候选各扫一次，Admission 不再完整扫描"
    assert gate.source_frame_count == 30


def test_external_new_reference_changes_count_and_carrier(media: Path, tmp_path: Path) -> None:
    candidate = tmp_path / "short.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-frames:v",
            "15",
            "-c:v",
            "ffv1",
            str(candidate),
        ]
    )
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, media)
    request = _request(
        external_repair_definition(),
        {"target_frame_rate": "30/1", "reference_change_confirmed": True},
        (_input("original_media", original), _input("diagnosis", diagnosis)),
    )
    handoff = runner.prepare_manual(request)
    shutil.copyfile(candidate, handoff.outputs[0].path)
    prepared = runner.submit_manual(request, handoff, ManualSubmission()).artifacts[0]
    gate = _admit(runner, original, diagnosis, prepared, external=True)
    assert gate.source_frame_count == 15
    assert gate.audio_bindings[0].artifact_id == prepared.artifact_id
    assert gate.preparation_strategy == "external-new-reference/1"
    assert gate.audio_video_start == gate.reference_start
    assert not gate.audio_bindings[0].tracks


def test_unknown_is_explicit_probe_interpretation(media: Path, tmp_path: Path) -> None:
    candidate = tmp_path / "unknown.mkv"
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
            str(candidate),
        ]
    )
    report = inspect_source(candidate, str(uuid4()), target_frame_rate="30/1")
    assert report.status == "direct" and report.observed_signal is not None
    with pytest.raises(MediaNodeError, match="COLOR_UNSPECIFIED"):
        resolve_interpretation(report.observed_signal, "declared_only")
    _, basis = resolve_interpretation(
        report.observed_signal, "operator_confirmed_bt709_limited_left"
    )
    assert any(b.source == "operator_confirmation" for b in basis)
    assert WorkDiagnosticReport.model_validate_json(report.model_dump_json()) == report
    assert "bitstream" not in report.model_dump_json()


def test_parameters_routes_and_old_exact_unchanged() -> None:
    from zniku.source_color import source_preparation_definitions as color_definitions
    from zniku.source_preparation import source_preparation_definitions as old_definitions

    for definition in (*old_definitions(), *color_definitions()):
        assert definition_role(definition) is None
    for route in ("diagnose", "direct", "builtin", "external"):
        graph = build_preparation_graph(
            "C:/synthetic.mkv",
            route=route,
            target_frame_rate="30/1",
            retime_confirmed=True,
            reference_change_confirmed=True,
        )
        GraphValidator(source_preparation_definitions()).validate(graph)
    with pytest.raises(ValueError):
        PrepareParameters(target_frame_rate="30/1", retime_confirmed=False)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AdmissionParameters.model_validate({"target_frame_rate": "29.97"})
    with pytest.raises(ValueError):
        build_preparation_graph("C:/synthetic.mkv", route="builtin", target_frame_rate="30/1")
    graph = build_preparation_graph(
        "C:/synthetic.mkv",
        route="direct",
        target_frame_rate="30/1",
        diagnosis_target_frame_rate=None,
    )
    assert graph.nodes[1].parameters["target_frame_rate"] is None


def test_forged_validator_cannot_mint_observation(media: Path, tmp_path: Path) -> None:
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, media)
    definition = diagnostics_definition()
    forged = definition.model_copy(
        update={
            "type_id": "synthetic.forged",
            "execution_mode": ExecutionMode.MANUAL_EXTERNAL,
            "executor": ManualExternalExecutorSpec(
                output_paths=definition.executor.output_paths, instructions="合成假输入"
            ),
        }
    )
    request = _request(forged, {}, (_input("original_media", original),))
    handoff = runner.prepare_manual(request)
    shutil.copyfile(diagnosis.path, handoff.outputs[0].path)
    with pytest.raises(RunnerError, match="DEFINITION"):
        runner.submit_manual(request, handoff, ManualSubmission())


def test_cancelled_scan_registers_no_report(media: Path, tmp_path: Path) -> None:
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


@pytest.mark.parametrize("target_rate", ["24/1", "60000/1001"])
def test_explicit_rate_change_retimes_pts_and_declares_selected_rate(
    media: Path,
    tmp_path: Path,
    target_rate: str,
) -> None:
    """目标不同于源 header 时，N/PTS 与输出 FPS 声明必须分别检查，不能互相代替。"""
    source_header = probe_header(media).video
    assert source_header.frame_rate == Fraction(30)
    assert read_header(media)["streams"][0]["has_b_frames"] > 0
    runner = _runner(tmp_path / "attempts")
    original = runner.run_automatic(
        _request(source_definition(), {"source_path": str(media)})
    ).artifacts[0]
    diagnosis = runner.run_automatic(
        _request(
            diagnostics_definition(),
            {"target_frame_rate": target_rate},
            (_input("original_media", original),),
        )
    ).artifacts[0]
    report = read_diagnosis(_input("diagnosis", diagnosis))
    assert report.status == "preparation_required" and report.target_frame_rate == target_rate
    prepared = runner.run_automatic(
        _request(
            builtin_prepare_definition(),
            {"target_frame_rate": target_rate, "retime_confirmed": True},
            (_input("original_media", original), _input("diagnosis", diagnosis)),
        )
    ).artifacts[0]
    fresh = read_reference_report(_input("reference_media", prepared))
    assert fresh.video is not None and fresh.video.frame_count == 30
    assert fresh.target_frame_rate == target_rate and fresh.status == "direct"
    result = runner.run_automatic(
        _request(
            admission_definition(),
            {
                "target_frame_rate": target_rate,
                "audio_policy": "original",
                "interpretation_policy": "operator_confirmed_bt709_limited_left",
            },
            (
                _input("original_media", original),
                _input("reference_media", prepared),
                _input("diagnosis", diagnosis),
                _input("audio_sources", original, 0),
            ),
        )
    )
    outputs = {a.producer_port_id: a for a in result.artifacts}
    gate = check_reference_binding(
        _input("video", outputs["video"]), _input("gate", outputs["gate"])
    )
    assert gate.frame_rate == target_rate and gate.source_frame_count == 30
    actual = probe_header(prepared.path).video
    declarations = (actual.frame_rate, actual.avg_frame_rate, actual.r_frame_rate)
    assert all(observed_rate_matches(Fraction(target_rate), value) for value in declarations), (
        f"target={gate.frame_rate}, N={gate.source_frame_count}, "
        f"max_error={fresh.video.max_clock_error}, time_base={fresh.video.time_base}, "
        f"frame/avg/r={declarations}, first={fresh.video.first_pts}, last={fresh.video.last_pts}"
    )

    def hashes(path: Path) -> tuple[bytes, ...]:
        # 只作为测试 oracle 证明本修复未补删/换序；不是产品默认的像素审计。
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
                "-fps_mode",
                "passthrough",
                "-c:v",
                "rawvideo",
                "-f",
                "framehash",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=60,
        )
        return tuple(
            line.rsplit(b",", 1)[-1].strip()
            for line in result.stdout.splitlines()
            if line and not line.startswith(b"#")
        )

    original_hashes, prepared_hashes = hashes(media), hashes(prepared.path)
    assert len(original_hashes) == 30 and prepared_hashes == original_hashes
