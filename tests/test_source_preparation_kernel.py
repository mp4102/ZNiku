"""以合成短媒体验证素材检查、保内容修复和显式工作参考绑定。

不读取真实素材。测试容器、帧序、取消和失败反例，合成通过不等于真实策略晋级。
"""

from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from zniku.graph import Graph, GraphValidator, NodeDefinition, NodeInstance
from zniku.media import runner_media_probe
from zniku.media.probe import MediaNodeError
from zniku.runtime import (
    ManualSubmission,
    NodeExecutionRequest,
    NodeRunner,
    RunnerError,
    RunnerInput,
)
from zniku.runtime.runner import RunnerArtifact
from zniku.source_preparation import (
    admission_definition,
    build_preparation_graph,
    builtin_prepare_definition,
    check_reference_binding,
    diagnostics_definition,
    external_repair_definition,
    read_diagnosis,
    register_source_preparation_adapters,
    register_source_preparation_validators,
    source_definition,
    source_preparation_definitions,
)
from zniku.source_preparation.contracts import read_document, validate_audio_sources
from zniku.source_preparation.definitions import definition_role
from zniku.source_preparation.inspection import checked_file, findings_for, inspect_source
from zniku.source_preparation.models import (
    NAMESPACE,
    DiagnosticParameters,
    DiagnosticReport,
    SourceGate,
    summary_for_report,
)
from zniku.source_preparation.preservation import (
    create_t1_candidate,
    resolve_mkvmerge,
    verify_preservation,
)
from zniku.source_preparation.process import MAX_LINE, fail, stream_process
from zniku.source_preparation.progress import cancellation_scope, progress_log, stage


def _tool(argv: list[str]) -> None:
    result = subprocess.run(argv, capture_output=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


@pytest.fixture
def media(tmp_path: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("短合成媒体测试需要 FFmpeg/FFprobe")
    path = tmp_path / "synthetic.mkv"
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
            "30",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-f",
            "matroska",
            str(path),
        ]
    )
    return path


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
    return NodeExecutionRequest(
        node_run_id=str(uuid4()),
        attempt=1,
        definition=definition,
        node=NodeInstance(
            node_id="test",
            type_id=definition.type_id,
            definition_version=definition.version,
            parameters=parameters,
        ),
        inputs=inputs,
    )


def _input(port: str, artifact: RunnerArtifact, ordinal: int | None = None) -> RunnerInput:
    return RunnerInput(
        port,
        artifact.artifact_id,
        artifact.kind,
        artifact.path,
        ordinal,
        media_info=artifact.media_info,
        size=artifact.size,
        mtime_ns=artifact.mtime_ns,
    )


def _diagnose(runner: NodeRunner, media: Path) -> tuple[RunnerArtifact, RunnerArtifact]:
    source = runner.run_automatic(_request(source_definition(), {"source_path": str(media)}))
    original = source.artifacts[0]
    result = runner.run_automatic(
        _request(diagnostics_definition(), {}, (_input("original_media", original),))
    )
    return original, result.artifacts[0]


def test_exact_catalog_and_explicit_routes() -> None:
    definitions = source_preparation_definitions()
    assert len(definitions) == 7
    assert all(item.version == "0.3.4" for item in definitions)
    assert [definition_role(item) for item in definitions] == [
        "source",
        "diagnostics",
        "builtin",
        "external",
        "external",
        "external",
        "admission",
    ]
    for route in ("diagnose", "direct", "builtin", "external"):
        graph = build_preparation_graph("C:/synthetic.mkv", route=route, target_frame_rate="30/1")
        GraphValidator(definitions).validate(graph)
        assert not any("command" in item.parameters for item in graph.nodes)


@pytest.mark.parametrize("value", ["30", "60/2", "0/1", "241/1", True, 30, "NaN"])
def test_parameters_fail_closed(value: object) -> None:
    with pytest.raises(ValidationError):
        DiagnosticParameters.model_validate({"target_frame_rate": value})
    with pytest.raises(ValidationError):
        DiagnosticParameters.model_validate({"command": "anything"})


def test_graph_rejects_unknown_fields() -> None:
    definition = diagnostics_definition()
    node = NodeInstance(
        node_id="check",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters={"unknown": "x"},
    )
    assert "E_PARAMETERS_INVALID" in {
        item.code for item in GraphValidator((definition,)).inspect(Graph(nodes=(node,)))
    }


def test_full_diagnostics_and_no_copy_admission(media: Path, tmp_path: Path) -> None:
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, media)
    report = read_diagnosis(_input("diagnosis", diagnosis))
    assert report.video.frame_count == report.video.packet_count == 30
    assert not report.findings
    assert summary_for_report(report)["status"] == "compatible"
    result = runner.run_automatic(
        _request(
            admission_definition(),
            {"audio_policy": "none"},
            (
                _input("original_media", original),
                _input("reference_media", original),
                _input("audio_sources", original, 0),
                _input("diagnosis", diagnosis),
            ),
        )
    )
    outputs = {item.producer_port_id: item for item in result.artifacts}
    assert outputs["video"].path == media
    gate = check_reference_binding(
        _input("video", outputs["video"]), _input("gate", outputs["gate"])
    )
    assert gate.reference_media_artifact_id == original.artifact_id
    assert gate.reference_media_artifact_id != outputs["video"].artifact_id
    assert gate.audio_bindings[0].tracks == ()
    validate_audio_sources(gate, [_input("audio_sources", original, 0)])
    log = diagnosis.path.parents[1] / "logs" / "stdout.log"
    lines = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("ZNIKU_SOURCE_PROGRESS ")
    ]
    assert lines
    assert any(json.loads(line.split(" ", 1)[1])["current"] == 30 for line in lines)


def test_modified_report_cannot_bypass_diagnosis_metadata(media: Path, tmp_path: Path) -> None:
    runner = _runner(tmp_path / "attempts")
    _, diagnosis = _diagnose(runner, media)
    data = json.loads(diagnosis.path.read_text(encoding="utf-8"))
    data["video"]["frame_count"] += 1
    diagnosis.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(MediaNodeError, match="INPUT_BINDING"):
        read_diagnosis(_input("diagnosis", diagnosis))


@pytest.mark.parametrize("suffix", [".m3u8", ".strm", ".mpd", ".txt"])
def test_playlist_rejected_before_probe(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"source{suffix}"
    path.write_text("https://example.invalid/media", encoding="utf-8")
    with pytest.raises(MediaNodeError, match="SOURCE_PATH"):
        checked_file(path)


def test_wrong_container_suffix_fails(media: Path, tmp_path: Path) -> None:
    fake = tmp_path / "renamed.mp4"
    shutil.copyfile(media, fake)
    with pytest.raises(MediaNodeError):
        inspect_source(fake, str(uuid4()))


def test_unknown_or_duplicate_report_fields_fail(tmp_path: Path) -> None:
    path = tmp_path / "diagnosis.json"
    for content in ('{"unknown":true}', '{"schema_version":1,"schema_version":1}', '{"a":NaN}'):
        path.write_text(content, encoding="utf-8")
        with pytest.raises(MediaNodeError, match="REPORT_INVALID"):
            read_document(path, DiagnosticReport)


def test_t1_unpromoted_refuses_before_candidate(media: Path, tmp_path: Path) -> None:
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
    assert not list((tmp_path / "attempts").rglob("staging"))


def test_complete_pixel_comparison_accepts_copy_and_rejects_changed_pixels(
    media: Path,
    tmp_path: Path,
) -> None:
    report = inspect_source(media, str(uuid4()))
    copied = tmp_path / "copy.mkv"
    shutil.copyfile(media, copied)
    assert (
        verify_preservation(media, copied, report, target_frame_rate="30/1").video.frame_count == 30
    )
    other = tmp_path / "other.mkv"
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    _tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=64x48:r=30",
            "-frames:v",
            "30",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            str(other),
        ]
    )
    with pytest.raises(MediaNodeError, match="VIDEO_CONTENT_CHANGED"):
        verify_preservation(media, other, report, target_frame_rate="30/1")


def test_full_scan_pts_problem_is_completed_report(media: Path, tmp_path: Path) -> None:
    target = tmp_path / "different-rate.mkv"
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    _tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(media),
            "-c",
            "copy",
            "-bsf:v",
            "setts=pts=PTS*1.001:dts=DTS*1.001",
            str(target),
        ]
    )
    report = inspect_source(target, str(uuid4()), target_frame_rate="30/1")
    assert report.complete is True
    assert report.video.frame_count == 30


def test_process_timeout_output_budget_and_error_detection() -> None:
    with pytest.raises(MediaNodeError, match="TIMEOUT"):
        stream_process(
            [sys.executable, "-c", "import time;time.sleep(2)"], consume=lambda _: None, timeout=0.1
        )
    with pytest.raises(MediaNodeError, match="OUTPUT_BUDGET"):
        stream_process([sys.executable, "-c", f"print('x'*{MAX_LINE + 1})"], consume=lambda _: None)
    assert stream_process(
        [sys.executable, "-c", "import sys;print('decode error',file=sys.stderr)"],
        consume=lambda _: None,
        permit_media_errors=True,
    )
    with pytest.raises(MediaNodeError, match="DECODE_ERRORS"):
        stream_process(
            [sys.executable, "-c", "import sys;print('error',file=sys.stderr)"],
            consume=lambda _: None,
        )


def test_process_cancellation_reaps_child() -> None:
    class Cancel:
        def report(self, fraction: float, **kwargs: object) -> None:
            raise fail("CANCELLED", "synthetic cancellation")

    with pytest.raises(MediaNodeError, match="CANCELLED"):
        stream_process(
            [sys.executable, "-c", "import time;time.sleep(5)"],
            consume=lambda _: None,
            progress=Cancel(),
        )


def test_rate_and_gate_are_strict() -> None:
    with pytest.raises(ValidationError):
        SourceGate.model_validate({"schema_version": "zniku.source.prepared.admission/2"})
    assert Fraction("30000/1001") != Fraction("30/1")


def test_cooperative_event_cancels_actual_child_without_artifacts(tmp_path: Path) -> None:
    event = threading.Event()
    timer = threading.Timer(0.3, event.set)
    timer.start()
    try:
        with pytest.raises(MediaNodeError, match="CANCELLED"), cancellation_scope(event):
            stream_process(
                [sys.executable, "-c", "import time;time.sleep(30)"], consume=lambda _: None
            )
    finally:
        timer.cancel()
    assert not list(tmp_path.iterdir())


def test_cancel_before_process_and_stage_exception_is_not_masked(tmp_path: Path) -> None:
    event = threading.Event()
    with (
        cancellation_scope(event),
        progress_log(tmp_path / "progress.log"),
        pytest.raises(ValueError, match="original failure"),
        stage("test"),
    ):
        event.set()
        raise ValueError("original failure")
    with pytest.raises(MediaNodeError, match="CANCELLED"), cancellation_scope(event):
        stream_process(
            [sys.executable, "-c", "raise AssertionError('must not start')"], consume=lambda _: None
        )


def test_external_single_output_submission_is_explicit(media: Path, tmp_path: Path) -> None:
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, media)
    request = _request(
        external_repair_definition("mkv"),
        {"target_frame_rate": "30/1"},
        (
            _input("original_media", original),
            _input("diagnosis", diagnosis),
        ),
    )
    handoff = runner.prepare_manual(request)
    assert len(handoff.outputs) == 1
    target = Path(handoff.outputs[0].path)
    shutil.copyfile(media, target)
    result = runner.submit_manual(request, handoff, ManualSubmission())
    prepared = result.artifacts[0]
    assert prepared.media_info[NAMESPACE]["role"] == "prepared"  # type: ignore[index]
    admitted = runner.run_automatic(
        _request(
            admission_definition(),
            {"audio_policy": "none"},
            (
                _input("original_media", original),
                _input("reference_media", prepared),
                _input("audio_sources", original, 0),
                _input("diagnosis", diagnosis),
            ),
        )
    )
    assert next(item for item in admitted.artifacts if item.kind == "VideoFile").path == target


def test_t1_real_synthetic_remux_and_content_gate(
    media: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        resolve_mkvmerge()
    except MediaNodeError:
        pytest.skip("候选生成专项需要 MKVToolNix")
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    longer = tmp_path / "longer.mkv"
    _tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-stream_loop",
            "29",
            "-i",
            str(media),
            "-c",
            "copy",
            "-bsf:v",
            "setts=pts=PTS*1.0002:dts=DTS*1.0002",
            str(longer),
        ]
    )
    report = inspect_source(longer, str(uuid4()), target_frame_rate="30/1")
    assert report.video.frame_count == 900
    assert report.candidate_strategies == ("t1-clock-quantization/1",)
    candidate = tmp_path / "prepared.mkv"
    create_t1_candidate(longer, candidate, report, target_frame_rate="30/1")
    verified = verify_preservation(longer, candidate, report, target_frame_rate="30/1")
    assert verified.video.frame_count == 900
    assert not verified.findings
    # 只在合成测试中模拟发布晋级，Graph 不暴露此开关；证明真实 adapter+validator 均可运行。
    from zniku.source_preparation import models

    monkeypatch.setattr(models, "T1_PROMOTED", True)
    runner = _runner(tmp_path / "automatic-attempts")
    source_result = runner.run_automatic(
        _request(source_definition(), {"source_path": str(longer)})
    )
    original = source_result.artifacts[0]
    diagnosis_result = runner.run_automatic(
        _request(
            diagnostics_definition(),
            {"target_frame_rate": "30/1"},
            (_input("original_media", original),),
        )
    )
    diagnostic_artifact = diagnosis_result.artifacts[0]
    result = runner.run_automatic(
        _request(
            builtin_prepare_definition(),
            {"target_frame_rate": "30/1"},
            (_input("original_media", original), _input("diagnosis", diagnostic_artifact)),
        )
    )
    repaired = result.artifacts[0]
    admitted = runner.run_automatic(
        _request(
            admission_definition(),
            {"audio_policy": "none"},
            (
                _input("original_media", original),
                _input("reference_media", repaired),
                _input("audio_sources", original, 0),
                _input("diagnosis", diagnostic_artifact),
            ),
        )
    )
    assert len(admitted.artifacts) == 2


def test_aac_priming_is_diagnosed_not_silently_trimmed(media: Path, tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    target = tmp_path / "priming.mp4"
    _tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(media),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=600:sample_rate=48000:duration=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(target),
        ]
    )
    report = inspect_source(target, str(uuid4()), target_frame_rate="30/1")
    assert report.complete and len(report.audio) == 1
    assert report.audio[0].skip_samples > 0
    assert "E_SOURCE_PREPARATION_AUDIO_PRIMING_UNSUPPORTED" in {
        item.code for item in report.findings
    }
    assert not report.candidate_strategies


def test_original_aac_binding_preserves_relative_start(media: Path, tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    target = tmp_path / "audio.mkv"
    # 合成 ADTS 本身没有 edit-list/skip 标记；检查整条解码序列，不暗中裁样本。
    audio = tmp_path / "synthetic.aac"
    _tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=600:sample_rate=48000:duration=0.98",
            "-c:a",
            "aac",
            "-f",
            "adts",
            str(audio),
        ]
    )
    _tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(media),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            str(target),
        ]
    )
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, target)
    report = read_diagnosis(_input("diagnosis", diagnosis))
    assert not report.findings
    result = runner.run_automatic(
        _request(
            admission_definition(),
            {"audio_policy": "original"},
            (
                _input("original_media", original),
                _input("reference_media", original),
                _input("audio_sources", original, 0),
                _input("diagnosis", diagnosis),
            ),
        )
    )
    artifacts = {item.producer_port_id: item for item in result.artifacts}
    gate = check_reference_binding(
        _input("video", artifacts["video"]), _input("gate", artifacts["gate"])
    )
    track = gate.audio_bindings[0].tracks[0]
    assert Fraction(track.relative_start) == Fraction(track.start_time) - Fraction(
        gate.original_video_start
    )
    assert track.sample_count == report.audio[0].sample_count
    validate_audio_sources(gate, [_input("audio_sources", original, 0)])
    # 保内容不仅是解码样本相同；换掉音轨 title 同样违反原件音轨属性映射。
    renamed_audio = tmp_path / "changed-audio-title.mkv"
    _tool(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(target),
            "-map",
            "0",
            "-c",
            "copy",
            "-metadata:s:a:0",
            "title=Changed synthetic title",
            str(renamed_audio),
        ]
    )
    with pytest.raises(MediaNodeError, match="AUDIO_CONTENT_CHANGED"):
        verify_preservation(target, renamed_audio, report, target_frame_rate="30/1")
    with pytest.raises(RunnerError, match="AUDIO_POLICY"):
        runner.run_automatic(
            _request(
                admission_definition(),
                {"audio_policy": "none"},
                (
                    _input("original_media", original),
                    _input("reference_media", original),
                    _input("audio_sources", original, 0),
                    _input("diagnosis", diagnosis),
                ),
            )
        )


def test_prepared_source_corpus_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "tools"))
    module = runpy.run_path(str(root / "tools/generate_prepared_source_studio_corpus.py"))
    document = module["corpus_document"]()
    assert len(document["definitions"]) == 18
    assert len(document["presentation"]["nodes"]) == 18
    corpus = root / "docs/architecture/studio-prepared-source-schema-corpus.json"
    assert (
        corpus.read_text(encoding="utf-8")
        == json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def test_studio_fixture_has_real_problem_and_preserving_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "tools"))
    module = runpy.run_path(str(root / "tools/studio_prepared_source_fixture.py"))
    normal, problem, repaired = module["generate_sources"](tmp_path)
    healthy = inspect_source(normal, str(uuid4()), target_frame_rate="30/1")
    report = inspect_source(problem, str(uuid4()), target_frame_rate="30/1")
    assert not healthy.findings
    assert {item.code for item in report.findings} == {"E_SOURCE_PREPARATION_CLOCK_NOT_CFR"}
    assert report.video.frame_count == 120
    assert not verify_preservation(problem, repaired, report, target_frame_rate="30/1").findings


@pytest.mark.parametrize(
    "key,value",
    [
        ("color_space", "unknown"),
        ("color_primaries", "unknown"),
        ("color_transfer", "unknown"),
        ("color_range", "unknown"),
        ("chroma_location", "unknown"),
        ("color_transfer", "arib-std-b67"),
        ("color_primaries", "bt2020"),
        ("hdr_side_data", True),
        ("sample_aspect_ratio", "8001:8000"),
        ("rotation", "90"),
    ],
)
def test_unknown_signal_hdr_and_geometry_fail_closed(media: Path, key: str, value: object) -> None:
    report = inspect_source(media, str(uuid4()))
    variant = report.model_copy(update={"video": report.video.model_copy(update={key: value})})
    assert "E_SOURCE_PREPARATION_VIDEO_PROFILE_UNSUPPORTED" in {
        item.code for item in findings_for(variant)
    }


@pytest.mark.parametrize("key", ["extra_streams", "chapter_count"])
def test_extra_stream_and_chapter_preservation_are_not_assumed(media: Path, key: str) -> None:
    report = inspect_source(media, str(uuid4()))
    variant = report.model_copy(update={key: 1})
    assert "E_SOURCE_PREPARATION_STREAM_LAYOUT" in {item.code for item in findings_for(variant)}
