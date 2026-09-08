"""以纯合成 header 覆盖 AVEnhanceFlow v2.7 九类节点 validator。"""

from __future__ import annotations

import json
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import zniku.avenhance_v27.validators as validators
from zniku.avenhance_v27 import (
    atomic_split_definition,
    enhancement_definition,
    final_mux_definition,
    frame_interpolation_definition,
    merge_video_definition,
    mosaic_restoration_definition,
    program_encode_definition,
    source_admission_definition,
    source_program_definition,
)
from zniku.avenhance_v27.probe import (
    AV27_NAMESPACE,
    Av27AudioHeader,
    Av27MediaHeader,
    Av27OtherStreamHeader,
    Av27VideoHeader,
    SourceTimeline,
)
from zniku.graph import NodeDefinition, NodeInstance
from zniku.runtime import (
    FrameRange,
    NodeExecutionRequest,
    NodeValidatorContext,
    RunnerInput,
)
from zniku.runtime.runner import ValidatedOutput

_SOURCE_RATE = Fraction(30000, 1001)
_OUTPUT_RATE = Fraction(60000, 1001)
_SIGNAL = {
    "color_range": "tv",
    "color_space": "bt709",
    "color_transfer": "bt709",
    "color_primaries": "bt709",
    "chroma_location": "left",
    "field_order": "progressive",
    "rotation": 0,
}


def _video(
    *,
    codec: str = "h264",
    profile: str | None = "High",
    codec_tag: str | None = None,
    width: int = 1920,
    height: int = 1080,
    pixel_format: str = "yuv420p",
    rate: Fraction = _SOURCE_RATE,
    time_base: Fraction | None = None,
    frames: int | None = 100,
    duration: float | None = None,
    chroma: str | None = "left",
    hdr: tuple[str, ...] = (),
) -> Av27VideoHeader:
    return Av27VideoHeader(
        index=0,
        codec=codec,
        profile=profile,
        codec_tag_string=codec_tag,
        width=width,
        height=height,
        pixel_format=pixel_format,
        frame_rate=rate,
        avg_frame_rate=rate,
        r_frame_rate=rate,
        time_base=time_base or Fraction(1, rate.numerator),
        frame_count=frames,
        sample_aspect_ratio="1:1",
        field_order="progressive",
        rotation=0,
        color_range="tv",
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
        chroma_location=chroma,
        hdr_side_data=hdr,
        duration_seconds=(
            duration if duration is not None else (None if frames is None else frames / float(rate))
        ),
    )


def _audio(
    index: int,
    *,
    codec: str = "aac",
    language: str | None = "jpn",
    title: str | None = None,
    default: bool = False,
) -> Av27AudioHeader:
    return Av27AudioHeader(
        index=index,
        codec=codec,
        profile="LC",
        extradata_hash=f"SHA256:{index:064x}",
        sample_rate=48000,
        channels=2,
        channel_layout="stereo",
        language=language,
        title=title,
        default=default,
        forced=False,
    )


def _media(
    path: Path,
    video: Av27VideoHeader,
    *,
    format_name: str = "matroska,webm",
    audios: tuple[Av27AudioHeader, ...] = (),
    others: tuple[Av27OtherStreamHeader, ...] = (),
    chapters: int = 0,
    duration: float | None = None,
) -> Av27MediaHeader:
    stream_kinds = ("video", *("audio" for _ in audios), *(item.kind for item in others))
    return Av27MediaHeader(
        path=path,
        format_name=format_name,
        duration_seconds=duration if duration is not None else video.duration_seconds,
        streams=stream_kinds,
        videos=(video,),
        audios=audios,
        others=others,
        chapter_count=chapters,
    )


def _namespace(
    *,
    frames: int,
    rate: Fraction = _SOURCE_RATE,
    width: int = 1920,
    height: int = 1080,
    duration: float | None = None,
    audios: tuple[Av27AudioHeader, ...] = (),
    source_ordinal: int | None = None,
    stage: dict[str, object] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "frame_count": frames,
        "frame_rate": f"{rate.numerator}/{rate.denominator}",
        "geometry": {"width": width, "height": height},
        "sample_aspect_ratio": "1:1",
        "field_order": "progressive",
        "rotation": 0,
        "signal": dict(_SIGNAL),
        "chroma_location": "left",
        "duration_seconds": duration if duration is not None else frames / float(rate),
        "audio_tracks": [item.to_summary() for item in audios],
    }
    if source_ordinal is not None:
        value["source_ordinal"] = source_ordinal
    if stage is not None:
        value["stage"] = stage
    return value


def _input(
    tmp_path: Path,
    port_id: str,
    *,
    kind: str,
    producer_port: str,
    artifact_id: str,
    ordinal: int | None = None,
    namespace: dict[str, object] | None = None,
) -> RunnerInput:
    path = tmp_path / f"{artifact_id}.bin"
    path.write_bytes(b"synthetic")
    return RunnerInput(
        port_id=port_id,
        artifact_id=artifact_id,
        kind=kind,
        path=path,
        ordinal=ordinal,
        producer_node_run_id=str(uuid4()),
        producer_port_id=producer_port,
        artifact_ordinal=ordinal,
        media_info={} if namespace is None else {AV27_NAMESPACE: namespace},
        size=path.stat().st_size,
        mtime_ns=path.stat().st_mtime_ns,
    )


def _output(
    tmp_path: Path,
    port_id: str,
    filename: str,
    *,
    kind: str = "VideoFile",
    producer: dict[str, object] | None = None,
    frame_range: FrameRange | None = None,
) -> ValidatedOutput:
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic-output")
    stat = path.stat()
    return ValidatedOutput(
        port_id=port_id,
        kind=kind,
        path=path,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        media_info={"format_name": "synthetic"},
        producer_metadata=producer or {},
        frame_range=frame_range,
    )


def _context(
    tmp_path: Path,
    definition: NodeDefinition,
    parameters: dict[str, Any],
    *,
    inputs: tuple[RunnerInput, ...] = (),
    outputs: tuple[ValidatedOutput, ...],
) -> NodeValidatorContext:
    node = NodeInstance(
        node_id="node-under-test",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=parameters,
    )
    request = NodeExecutionRequest(
        node_run_id=str(uuid4()),
        attempt=1,
        definition=definition,
        node=node,
        inputs=inputs,
    )
    return NodeValidatorContext(request=request, work_dir=tmp_path, outputs=outputs)


def _geometry(width: int = 1920, height: int = 1080) -> dict[str, object]:
    return {"width": width, "height": height, "sample_aspect_ratio": "1/1"}


def _full_signal() -> dict[str, object]:
    return dict(_SIGNAL)


def test_source_program_runs_one_timeline_traversal_and_extends_both_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    audio = (_audio(1, default=True), _audio(2, language="eng"))
    media = _media(source, _video(frames=None, duration=100 / float(_SOURCE_RATE)), audios=audio)
    calls = {"header": 0, "timeline": 0}

    def header(_path: Path) -> Av27MediaHeader:
        calls["header"] += 1
        return media

    def timeline(_path: Path, *, header: Av27MediaHeader) -> SourceTimeline:
        assert header is media
        calls["timeline"] += 1
        return SourceTimeline(100, "high", 1.0, 1.0, "packet_dts")

    monkeypatch.setattr(validators, "probe_header", header)
    monkeypatch.setattr(validators, "probe_source_timeline", timeline)
    context = _context(
        tmp_path,
        source_program_definition(),
        {"source_path": str(source.resolve()), "source_ordinal": 0},
        outputs=(
            _output(tmp_path, "video", "source.mkv"),
            _output(tmp_path, "source_media", "source.mkv", kind="MediaFile"),
        ),
    )
    # 两个 output 必须引用 source_path；替换 helper 生成的 output 路径。
    stat = source.stat()
    context = NodeValidatorContext(
        request=context.request,
        work_dir=tmp_path,
        outputs=tuple(
            ValidatedOutput(
                port_id=item.port_id,
                kind=item.kind,
                path=source,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                media_info={},
            )
            for item in context.outputs
        ),
    )

    result = validators.validate_source_program(context)

    assert result.passed is True
    assert calls == {"header": 1, "timeline": 1}
    assert set(result.media_info_extensions) == {"video", "source_media"}
    assert result.media_info_extensions["video"][AV27_NAMESPACE]["frame_count"] == 100

    assert result.media_info_extensions["source_media"][AV27_NAMESPACE]["audio_tracks"] == [
        item.to_summary() for item in audio
    ]


def test_source_program_rejects_unknown_stream_before_timeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"source")
    bad = _media(
        source,
        _video(),
        others=(Av27OtherStreamHeader(1, "subtitle", "subrip", None),),
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: bad)
    monkeypatch.setattr(
        validators,
        "probe_source_timeline",
        lambda *_args, **_kwargs: pytest.fail("header gate 失败后不得 traversal"),
    )
    stat = source.stat()
    outputs = tuple(
        ValidatedOutput(port_id, kind, source, stat.st_size, stat.st_mtime_ns, {})
        for port_id, kind in (("video", "VideoFile"), ("source_media", "MediaFile"))
    )
    context = _context(
        tmp_path,
        source_program_definition(),
        {"source_path": str(source.resolve()), "source_ordinal": 0},
        outputs=outputs,
    )

    result = validators.validate_source_program(context)

    assert result.passed is False
    assert result.summary["code"] == "E_AV27_STREAM_UNSUPPORTED"
    assert result.media_info_extensions == {}


def test_admission_gate_round_trip_and_prechaptered_audio_fail_closed(tmp_path: Path) -> None:
    audio = (_audio(1, default=True), _audio(2, language="eng"))
    sources = tuple(
        _input(
            tmp_path,
            "sources",
            kind="MediaFile",
            producer_port="source_media",
            artifact_id=f"source-{ordinal}",
            ordinal=ordinal,
            namespace=_namespace(frames=100, audios=audio, source_ordinal=ordinal),
        )
        for ordinal in range(2)
    )
    gate = _output(tmp_path, "gate", "admission.json", kind="DataFile")
    payload = {
        "schema": "zniku.avenhance.v27.admission/1",
        "source_mode": "pre_chaptered",
        "sources": [
            {
                "source_ordinal": ordinal,
                "artifact_id": source.artifact_id,
                "frame_count": 100,
                "frame_rate": "30000/1001",
                "geometry": {"width": 1920, "height": 1080},
                "signal": dict(_SIGNAL),
                "audio_tracks": [item.signature() for item in audio],
            }
            for ordinal, source in enumerate(sources)
        ],
    }
    gate.path.write_text(json.dumps(payload), encoding="utf-8")
    parameters = {
        "source_mode": "pre_chaptered",
        "sources": [
            {"source_ordinal": 0, "source_node_id": "source-0001"},
            {"source_ordinal": 1, "source_node_id": "source-0002"},
        ],
    }
    valid = validators.validate_source_admission(
        _context(
            tmp_path,
            source_admission_definition(),
            parameters,
            inputs=sources,
            outputs=(gate,),
        )
    )
    assert valid.passed is True
    assert valid.media_info_extensions == {}

    mismatched = list(sources)
    mismatched[1] = _input(
        tmp_path,
        "sources",
        kind="MediaFile",
        producer_port="source_media",
        artifact_id="source-bad",
        ordinal=1,
        namespace=_namespace(frames=100, audios=(_audio(1),), source_ordinal=1),
    )
    invalid = validators.validate_source_admission(
        _context(
            tmp_path,
            source_admission_definition(),
            parameters,
            inputs=tuple(mismatched),
            outputs=(gate,),
        )
    )
    assert invalid.passed is False
    assert invalid.summary["code"] == "E_AV27_ADMISSION_AUDIO_MISMATCH"


def test_mosaic_restoration_closes_n_to_n_and_operator_declaration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _input(
        tmp_path,
        "video",
        kind="VideoFile",
        producer_port="video",
        artifact_id="source-video",
        namespace=_namespace(frames=100),
    )
    gate = _input(
        tmp_path,
        "gate",
        kind="DataFile",
        producer_port="gate",
        artifact_id="admission",
    )
    output = _output(tmp_path, "video", "mr.mkv")
    monkeypatch.setattr(validators, "probe_header", lambda _path: _media(output.path, _video()))
    result = validators.validate_mosaic_restoration(
        _context(
            tmp_path,
            mosaic_restoration_definition(),
            {"model_name": "Jasna", "model_version": "1.0"},
            inputs=(source, gate),
            outputs=(output,),
        )
    )

    assert result.passed is True
    namespace = result.media_info_extensions["video"][AV27_NAMESPACE]
    assert namespace["frame_count"] == 100
    assert namespace["stage"] == {
        "kind": "mosaic_restoration",
        "model_name": "Jasna",
        "model_version": "1.0",
        "operator_declared": True,
    }

    monkeypatch.setattr(
        validators,
        "probe_header",
        lambda _path: _media(output.path, _video(frames=99)),
    )
    invalid = validators.validate_mosaic_restoration(
        _context(
            tmp_path,
            mosaic_restoration_definition(),
            {"model_name": "Jasna", "model_version": "1.0"},
            inputs=(source, gate),
            outputs=(output,),
        )
    )
    assert invalid.passed is False
    assert invalid.summary["code"] == "E_AV27_MR_FRAME_COUNT"


def test_atomic_split_uses_group_producer_count_without_leaf_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _input(
        tmp_path,
        "videos",
        kind="VideoFile",
        producer_port="video",
        artifact_id="effective-video",
        ordinal=0,
        namespace=_namespace(frames=100),
    )
    gate = _input(
        tmp_path,
        "gate",
        kind="DataFile",
        producer_port="gate",
        artifact_id="admission",
    )
    outputs = (
        _output(
            tmp_path,
            "leaf-0001",
            "leaf-0001.mkv",
            producer={"output_frames": 100},
            frame_range=FrameRange(start_frame=0, end_frame=40),
        ),
        _output(
            tmp_path,
            "leaf-0002",
            "leaf-0002.mkv",
            producer={"output_frames": 100},
            frame_range=FrameRange(start_frame=40, end_frame=100),
        ),
    )
    leaf_header = _video(codec="ffv1", profile=None, pixel_format="yuv420p10le", frames=None)
    monkeypatch.setattr(validators, "probe_header", lambda path: _media(path, leaf_header))
    monkeypatch.setattr(
        validators,
        "probe_source_timeline",
        lambda *_args, **_kwargs: pytest.fail("有 producer count 时不得 traversal"),
    )
    parameters = {
        "planned_admission_artifact_id": "admission",
        "segments": [
            {
                "port_id": "leaf-0001",
                "source_ordinal": 0,
                "planned_effective_video_artifact_id": "effective-video",
                "chapter_id": "chapter-0001",
                "chapter_ordinal": 0,
                "leaf_id": "leaf-id-0001",
                "leaf_ordinal": 0,
                "start_frame": 0,
                "end_frame": 40,
            },
            {
                "port_id": "leaf-0002",
                "source_ordinal": 0,
                "planned_effective_video_artifact_id": "effective-video",
                "chapter_id": "chapter-0001",
                "chapter_ordinal": 0,
                "leaf_id": "leaf-id-0002",
                "leaf_ordinal": 1,
                "start_frame": 40,
                "end_frame": 100,
            },
        ],
    }
    result = validators.validate_atomic_split(
        _context(
            tmp_path,
            atomic_split_definition(2),
            parameters,
            inputs=(source, gate),
            outputs=outputs,
        )
    )

    assert result.passed is True
    assert result.media_info_extensions["leaf-0001"][AV27_NAMESPACE]["frame_count"] == 40
    assert result.media_info_extensions["leaf-0002"][AV27_NAMESPACE]["frame_count"] == 60

    wrong = tuple(
        ValidatedOutput(
            item.port_id,
            item.kind,
            item.path,
            item.size,
            item.mtime_ns,
            item.media_info,
            {"output_frames": 99},
            item.frame_range,
        )
        for item in outputs
    )
    invalid = validators.validate_atomic_split(
        _context(
            tmp_path,
            atomic_split_definition(2),
            parameters,
            inputs=(source, gate),
            outputs=wrong,
        )
    )
    assert invalid.passed is False
    assert invalid.summary["code"] == "E_AV27_PRODUCER_FRAME_COUNT"


def test_atomic_split_missing_metadata_falls_back_once_per_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _input(
        tmp_path,
        "videos",
        kind="VideoFile",
        producer_port="video",
        artifact_id="effective-video",
        ordinal=0,
        namespace=_namespace(frames=10),
    )
    gate = _input(
        tmp_path,
        "gate",
        kind="DataFile",
        producer_port="gate",
        artifact_id="admission",
    )
    output = _output(
        tmp_path,
        "leaf-0001",
        "leaf-0001.mkv",
        frame_range=FrameRange(start_frame=0, end_frame=10),
    )
    leaf = _media(
        output.path,
        _video(codec="ffv1", profile=None, pixel_format="yuv420p10le", frames=None),
    )
    source_header = _media(source.path, _video(frames=None))
    header_calls: list[Path] = []

    def header(path: Path) -> Av27MediaHeader:
        header_calls.append(path)
        return source_header if path == source.path else leaf

    timeline_calls = 0

    def timeline(path: Path, *, header: Av27MediaHeader) -> SourceTimeline:
        nonlocal timeline_calls
        assert path == source.path and header is source_header
        timeline_calls += 1
        return SourceTimeline(10, "high", 1.0, 1.0, "packet_dts")

    monkeypatch.setattr(validators, "probe_header", header)
    monkeypatch.setattr(validators, "probe_source_timeline", timeline)
    parameters = {
        "planned_admission_artifact_id": "admission",
        "segments": [
            {
                "port_id": "leaf-0001",
                "source_ordinal": 0,
                "planned_effective_video_artifact_id": "effective-video",
                "chapter_id": "chapter-0001",
                "chapter_ordinal": 0,
                "leaf_id": "leaf-id-0001",
                "leaf_ordinal": 0,
                "start_frame": 0,
                "end_frame": 10,
            }
        ],
    }
    result = validators.validate_atomic_split(
        _context(
            tmp_path,
            atomic_split_definition(1),
            parameters,
            inputs=(source, gate),
            outputs=(output,),
        )
    )
    assert result.passed is True
    assert timeline_calls == 1
    assert header_calls.count(source.path) == 1
    assert header_calls.count(output.path) == 1


def test_enhancement_accepts_declared_integer_scale_and_rejects_hdr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _input(
        tmp_path,
        "video",
        kind="VideoFile",
        producer_port="video",
        artifact_id="leaf",
        namespace=_namespace(frames=100),
    )
    output = _output(tmp_path, "video", "enhancement.mov")
    parameters = {
        "model_name": "Starlight Precise",
        "model_version": "2.6",
        "actual_scale_factor": 2,
        "expected_input_geometry": _geometry(),
        "expected_output_geometry": _geometry(3840, 2160),
        "expected_frames": 100,
        "expected_fps": "30000/1001",
        "chapter_id": "chapter-0001",
        "chapter_ordinal": 0,
        "leaf_id": "leaf-0001",
        "leaf_ordinal": 0,
    }
    valid_header = _media(
        output.path,
        _video(
            codec="prores",
            profile="HQ",
            width=3840,
            height=2160,
            pixel_format="yuv422p10le",
        ),
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: valid_header)
    result = validators.validate_enhancement(
        _context(
            tmp_path,
            enhancement_definition(),
            parameters,
            inputs=(source,),
            outputs=(output,),
        )
    )
    assert result.passed is True
    assert result.media_info_extensions["video"][AV27_NAMESPACE]["frame_count"] == 100

    # 错分段必须保持原有失败语义，同时让创作者看到服务端实际比较的数值。
    with monkeypatch.context() as frame_patch:
        frame_patch.setattr(validators, "_external_frame_count", lambda _media: (99, "header"))
        wrong_frames = validators.validate_enhancement(
            _context(
                tmp_path,
                enhancement_definition(),
                parameters,
                inputs=(source,),
                outputs=(output,),
            )
        )
        assert wrong_frames.passed is False
        assert wrong_frames.summary["code"] == "E_AV27_ENHANCEMENT_FRAME_COUNT"
        assert "预期 100 帧，实际 99 帧" in str(wrong_frames.message)

    bad_video = _video(
        codec="prores",
        profile="HQ",
        width=3840,
        height=2160,
        pixel_format="yuv422p10le",
        hdr=("Mastering display metadata",),
    )
    monkeypatch.setattr(
        validators,
        "probe_header",
        lambda _path: _media(output.path, bad_video, format_name="mov"),
    )
    invalid = validators.validate_enhancement(
        _context(
            tmp_path,
            enhancement_definition(),
            parameters,
            inputs=(source,),
            outputs=(output,),
        )
    )
    assert invalid.passed is False
    assert invalid.summary["code"] == "E_AV27_HDR_UNSUPPORTED"

    unknown_subtitle = _media(
        output.path,
        valid_header.video,
        format_name="mov",
        others=(Av27OtherStreamHeader(1, "subtitle", "unknown", None),),
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: unknown_subtitle)
    invalid = validators.validate_enhancement(
        _context(
            tmp_path,
            enhancement_definition(),
            parameters,
            inputs=(source,),
            outputs=(output,),
        )
    )
    assert invalid.passed is False
    assert invalid.summary["code"] == "E_AV27_ENHANCEMENT_LAYOUT"

    timecode = _media(
        output.path,
        valid_header.video,
        format_name="mov",
        others=(Av27OtherStreamHeader(1, "data", "unknown", "tmcd"),),
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: timecode)
    assert validators.validate_enhancement(
        _context(
            tmp_path,
            enhancement_definition(),
            parameters,
            inputs=(source,),
            outputs=(output,),
        )
    ).passed


def test_enhancement_scale_one_does_not_require_declaration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _input(
        tmp_path,
        "video",
        kind="VideoFile",
        producer_port="video",
        artifact_id="leaf",
        namespace=_namespace(frames=10),
    )
    output = _output(tmp_path, "video", "enhancement.mov")
    header = _media(
        output.path,
        _video(codec="prores", profile="HQ", pixel_format="yuv422p10le", frames=10),
        format_name="mov",
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: header)
    parameters = {
        "model_name": "Starlight Precise",
        "expected_input_geometry": _geometry(),
        "expected_output_geometry": _geometry(),
        "expected_frames": 10,
        "expected_fps": "30000/1001",
        "chapter_id": "chapter-0001",
        "chapter_ordinal": 0,
        "leaf_id": "leaf-0001",
        "leaf_ordinal": 0,
    }
    result = validators.validate_enhancement(
        _context(
            tmp_path,
            enhancement_definition(),
            parameters,
            inputs=(source,),
            outputs=(output,),
        )
    )
    assert result.passed is True
    stage = result.media_info_extensions["video"][AV27_NAMESPACE]["stage"]
    assert isinstance(stage, dict)
    assert stage["actual_scale_factor"] == 1


def test_merge_closes_ordered_input_sum_and_exact_producer_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = {
        "kind": "enhancement",
        "model_name": "Starlight Precise",
        "model_version": "2.6",
        "operator_declared": True,
        "actual_scale_factor": 1,
    }
    inputs = tuple(
        _input(
            tmp_path,
            "videos",
            kind="VideoFile",
            producer_port="video",
            artifact_id=f"enhancement-{ordinal}",
            ordinal=ordinal,
            namespace=_namespace(frames=frames, stage=stage),
        )
        for ordinal, frames in enumerate((40, 60))
    )
    output = _output(
        tmp_path,
        "video",
        "merge.mov",
        producer={"output_frames": 100},
    )
    header = _media(
        output.path,
        _video(codec="prores", profile="HQ", pixel_format="yuv422p10le"),
        format_name="mov",
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: header)
    parameters = {
        "model_name": "Starlight Precise",
        "model_version": "2.6",
        "chapter_id": "chapter-0001",
        "chapter_ordinal": 0,
        "expected_frames": 100,
        "expected_fps": "30000/1001",
        "expected_geometry": _geometry(),
        "actual_scale_factor": 1,
    }
    result = validators.validate_merge_video(
        _context(
            tmp_path,
            merge_video_definition(),
            parameters,
            inputs=inputs,
            outputs=(output,),
        )
    )
    assert result.passed is True
    assert result.media_info_extensions["video"][AV27_NAMESPACE]["frame_count"] == 100

    malformed = ValidatedOutput(
        output.port_id,
        output.kind,
        output.path,
        output.size,
        output.mtime_ns,
        output.media_info,
        {"output_frames": 100, "path": "forbidden"},
    )
    invalid = validators.validate_merge_video(
        _context(
            tmp_path,
            merge_video_definition(),
            parameters,
            inputs=inputs,
            outputs=(malformed,),
        )
    )
    assert invalid.passed is False
    assert invalid.summary["code"] == "E_AV27_PRODUCER_METADATA"


@pytest.mark.parametrize(
    ("rate", "frames", "expected_pass", "code"),
    [
        (Fraction(2997, 50), 199, True, None),
        (Fraction(60, 1), 199, False, "E_AV27_FI_FPS"),
        (_OUTPUT_RATE, 200, False, "E_AV27_FI_DOUBLE_COUNT"),
    ],
)
def test_fi_rate_tolerance_and_two_n_minus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate: Fraction,
    frames: int,
    expected_pass: bool,
    code: str | None,
) -> None:
    source = _input(
        tmp_path,
        "video",
        kind="VideoFile",
        producer_port="video",
        artifact_id="merge",
        namespace=_namespace(frames=100),
    )
    output = _output(tmp_path, "video", "fi.mov")
    header = _media(
        output.path,
        _video(
            codec="prores",
            profile="HQ",
            pixel_format="yuv422p10le",
            rate=rate,
            frames=frames,
            duration=frames / float(rate),
        ),
        format_name="mov",
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: header)
    parameters = {
        "model_name": "Aion",
        "model_version": "1.0",
        "chapter_id": "chapter-0001",
        "chapter_ordinal": 0,
        "expected_input_frames": 100,
        "expected_output_frames": 199,
        "source_fps": "30000/1001",
        "expected_geometry": _geometry(),
        "expected_signal": _full_signal(),
    }
    result = validators.validate_frame_interpolation(
        _context(
            tmp_path,
            frame_interpolation_definition(),
            parameters,
            inputs=(source,),
            outputs=(output,),
        )
    )
    assert result.passed is expected_pass
    if code is not None:
        assert result.summary["code"] == code


@pytest.mark.parametrize("missing_signal_field", ["color_space", "sample_aspect_ratio"])
def test_program_two_chapters_closes_main10_hvc1_and_total_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_signal_field: str,
) -> None:
    """Program 必须显式声明完整 signal；外部 FI 可缺失的 SAR 也不能在此放宽。"""

    fi_stage = {
        "kind": "frame_interpolation",
        "model_name": "Aion",
        "model_version": "1.0",
        "operator_declared": True,
    }
    inputs = tuple(
        _input(
            tmp_path,
            "chapters",
            kind="VideoFile",
            producer_port="video",
            artifact_id=f"fi-{ordinal}",
            ordinal=ordinal,
            namespace=_namespace(frames=frames, rate=_OUTPUT_RATE, stage=fi_stage),
        )
        for ordinal, frames in enumerate((119, 279))
    )
    output = _output(
        tmp_path,
        "video",
        "program.mp4",
        producer={"output_frames": 400},
    )
    header = _media(
        output.path,
        _video(
            codec="hevc",
            profile="Main 10",
            codec_tag="hvc1",
            pixel_format="yuv420p10le",
            rate=_OUTPUT_RATE,
            time_base=Fraction(1, 60000),
            frames=400,
            duration=400 / float(_OUTPUT_RATE),
        ),
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: header)
    parameters = {
        "encoder": "cpu",
        "source_fps": "30000/1001",
        "chapters": [
            {
                "chapter_id": "chapter-0001",
                "chapter_ordinal": 0,
                "source_frames": 60,
                "expected_fi_frames": 119,
                "encoded_frames": 120,
            },
            {
                "chapter_id": "chapter-0002",
                "chapter_ordinal": 1,
                "source_frames": 140,
                "expected_fi_frames": 279,
                "encoded_frames": 280,
            },
        ],
        "expected_geometry": _geometry(),
        "expected_signal": _full_signal(),
    }
    result = validators.validate_program_encode(
        _context(
            tmp_path,
            program_encode_definition(),
            parameters,
            inputs=inputs,
            outputs=(output,),
        )
    )

    assert result.passed is True
    namespace = result.media_info_extensions["video"][AV27_NAMESPACE]
    assert namespace["frame_count"] == 400
    assert namespace["frame_rate"] == "60000/1001"

    malformed_stage_input = _input(
        tmp_path,
        "chapters",
        kind="VideoFile",
        producer_port="video",
        artifact_id="fi-malformed-stage",
        ordinal=0,
        namespace=_namespace(frames=119, rate=_OUTPUT_RATE, stage={}),
    )
    chapter_parameters = parameters["chapters"]
    assert isinstance(chapter_parameters, list)
    malformed_stage = validators.validate_program_encode(
        _context(
            tmp_path,
            program_encode_definition(),
            {**parameters, "chapters": chapter_parameters[:1]},
            inputs=(malformed_stage_input,),
            outputs=(
                _output(
                    tmp_path,
                    "video",
                    "program.mp4",
                    producer={"output_frames": 120},
                ),
            ),
        )
    )
    assert malformed_stage.passed is False
    assert malformed_stage.summary["code"] == "E_AV27_STAGE_METADATA"

    if missing_signal_field == "color_space":
        missing_signal_video = replace(header.video, color_space=None)
    else:
        assert missing_signal_field == "sample_aspect_ratio"
        missing_signal_video = replace(header.video, sample_aspect_ratio=None)
    missing_signal_header = _media(output.path, missing_signal_video, format_name="mp4")
    monkeypatch.setattr(validators, "probe_header", lambda _path: missing_signal_header)
    missing_signal = validators.validate_program_encode(
        _context(
            tmp_path,
            program_encode_definition(),
            parameters,
            inputs=inputs,
            outputs=(output,),
        )
    )
    assert missing_signal.passed is False
    assert missing_signal.summary["code"] == "E_AV27_SIGNAL_MISSING"

    bad_header = _media(
        output.path,
        _video(
            codec="hevc",
            profile="Main 10",
            codec_tag="hev1",
            pixel_format="yuv420p10le",
            rate=_OUTPUT_RATE,
            time_base=Fraction(1, 60000),
            frames=400,
        ),
        format_name="mp4",
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: bad_header)
    invalid = validators.validate_program_encode(
        _context(
            tmp_path,
            program_encode_definition(),
            parameters,
            inputs=inputs,
            outputs=(output,),
        )
    )
    assert invalid.passed is False
    assert invalid.summary["code"] == "E_AV27_PROGRAM_CODEC"


def test_final_keeps_two_audio_tracks_in_order_and_duration_within_one_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = (
        _audio(1, codec="aac", language="jpn", default=True),
        _audio(2, codec="ac3", language="eng", title="Commentary"),
    )
    program_duration = 400 / float(_OUTPUT_RATE)
    program = _input(
        tmp_path,
        "video",
        kind="VideoFile",
        producer_port="video",
        artifact_id="program",
        namespace=_namespace(
            frames=400,
            rate=_OUTPUT_RATE,
            duration=program_duration,
            stage={"kind": "program_encode", "encoder": "cpu", "chapter_count": 1},
        ),
    )
    source = _input(
        tmp_path,
        "sources",
        kind="MediaFile",
        producer_port="source_media",
        artifact_id="source",
        ordinal=0,
        namespace=_namespace(frames=200, audios=audio, source_ordinal=0),
    )
    gate = _input(
        tmp_path,
        "gate",
        kind="DataFile",
        producer_port="gate",
        artifact_id="admission",
    )
    output = _output(
        tmp_path,
        "media",
        "final.mkv",
        kind="MediaFile",
        producer={"output_frames": 400},
    )
    final_video = _video(
        codec="hevc",
        profile="Main 10",
        pixel_format="yuv420p10le",
        rate=_OUTPUT_RATE,
        frames=None,
        duration=program_duration + 0.5 / float(_OUTPUT_RATE),
    )
    final_header = _media(
        output.path,
        final_video,
        audios=audio,
        duration=program_duration + 0.5 / float(_OUTPUT_RATE),
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: final_header)
    parameters = {
        "source_mode": "program",
        "sources": [{"source_ordinal": 0, "source_frames": 200, "source_fps": "30000/1001"}],
        "expected_program_frames": 400,
        "expected_geometry": _geometry(),
        "expected_signal": _full_signal(),
        "mr_mode": "off",
    }
    context = _context(
        tmp_path,
        final_mux_definition(),
        parameters,
        inputs=(program, source, gate),
        outputs=(output,),
    )
    result = validators.validate_final_mux(context)

    assert result.passed is True
    assert result.summary["audio_stream_count"] == 2
    namespace = result.media_info_extensions["media"][AV27_NAMESPACE]
    assert namespace["frame_count"] == 400
    assert namespace["audio_tracks"] == [item.to_summary() for item in audio]

    wrong_ordinal_source = _input(
        tmp_path,
        "sources",
        kind="MediaFile",
        producer_port="source_media",
        artifact_id="wrong-ordinal-source",
        ordinal=0,
        namespace=_namespace(frames=200, audios=audio, source_ordinal=1),
    )
    wrong_ordinal = validators.validate_final_mux(
        _context(
            tmp_path,
            final_mux_definition(),
            parameters,
            inputs=(program, wrong_ordinal_source, gate),
            outputs=(output,),
        )
    )
    assert wrong_ordinal.passed is False
    assert wrong_ordinal.summary["code"] == "E_AV27_FINAL_SOURCE_CHANGED"

    swapped = _media(
        output.path,
        final_video,
        audios=tuple(reversed(audio)),
        duration=program_duration,
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: swapped)
    invalid = validators.validate_final_mux(context)
    assert invalid.passed is False
    assert invalid.summary["code"] == "E_AV27_FINAL_AUDIO_MISMATCH"


@pytest.mark.parametrize(
    "mutation",
    [
        {"producer": {"output_frames": 399}},
        {"chapters": 1},
        {"duration_delta_frames": 2.0},
    ],
)
def test_final_rejects_producer_chapter_and_duration_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, object],
) -> None:
    audio = (_audio(1), _audio(2, codec="ac3"))
    duration = 400 / float(_OUTPUT_RATE)
    program = _input(
        tmp_path,
        "video",
        kind="VideoFile",
        producer_port="video",
        artifact_id="program",
        namespace=_namespace(frames=400, rate=_OUTPUT_RATE, duration=duration),
    )
    source = _input(
        tmp_path,
        "sources",
        kind="MediaFile",
        producer_port="source_media",
        artifact_id="source",
        ordinal=0,
        namespace=_namespace(frames=200, audios=audio, source_ordinal=0),
    )
    gate = _input(
        tmp_path,
        "gate",
        kind="DataFile",
        producer_port="gate",
        artifact_id="admission",
    )
    producer = mutation.get("producer", {"output_frames": 400})
    assert isinstance(producer, dict)
    output = _output(
        tmp_path,
        "media",
        "final.mkv",
        kind="MediaFile",
        producer=producer,
    )
    delta = mutation.get("duration_delta_frames", 0.0)
    assert isinstance(delta, float)
    chapter_count = mutation.get("chapters", 0)
    assert isinstance(chapter_count, int) and not isinstance(chapter_count, bool)
    final_duration = duration + delta / float(_OUTPUT_RATE)
    media = _media(
        output.path,
        _video(
            codec="hevc",
            profile="Main 10",
            pixel_format="yuv420p10le",
            rate=_OUTPUT_RATE,
            frames=None,
            duration=final_duration,
        ),
        audios=audio,
        chapters=chapter_count,
        duration=final_duration,
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: media)
    result = validators.validate_final_mux(
        _context(
            tmp_path,
            final_mux_definition(),
            {
                "source_mode": "program",
                "sources": [
                    {
                        "source_ordinal": 0,
                        "source_frames": 200,
                        "source_fps": "30000/1001",
                    }
                ],
                "expected_program_frames": 400,
                "expected_geometry": _geometry(),
                "expected_signal": _full_signal(),
                "mr_mode": "off",
            },
            inputs=(program, source, gate),
            outputs=(output,),
        )
    )
    assert result.passed is False
    assert result.media_info_extensions == {}


def _final_rate_context(tmp_path: Path) -> NodeValidatorContext:
    """建立 canonical 60000/1001 的 Final 合同，不从待测 header 反推期望。"""

    program = _input(
        tmp_path,
        "video",
        kind="VideoFile",
        producer_port="video",
        artifact_id="program-rate",
        namespace=_namespace(frames=400, rate=_OUTPUT_RATE),
    )
    source = _input(
        tmp_path,
        "sources",
        kind="MediaFile",
        producer_port="source_media",
        artifact_id="source-rate",
        ordinal=0,
        namespace=_namespace(frames=200, source_ordinal=0),
    )
    gate = _input(
        tmp_path,
        "gate",
        kind="DataFile",
        producer_port="gate",
        artifact_id="admission-rate",
    )
    return _context(
        tmp_path,
        final_mux_definition(),
        {
            "source_mode": "program",
            "sources": [{"source_ordinal": 0, "source_frames": 200, "source_fps": "30000/1001"}],
            "expected_program_frames": 400,
            "expected_geometry": _geometry(),
            "expected_signal": _full_signal(),
            "mr_mode": "off",
        },
        inputs=(program, source, gate),
        outputs=(
            _output(
                tmp_path,
                "media",
                "final.mkv",
                kind="MediaFile",
                producer={"output_frames": 400},
            ),
        ),
    )


@pytest.mark.parametrize(
    ("observed", "expected_pass"),
    [
        (_OUTPUT_RATE, True),
        (Fraction(19001, 317), True),
        (Fraction(1_000_000_000, 16_683_333), True),
        (1 / (1 / _OUTPUT_RATE + Fraction(1, 1_000_000_000)), True),
        (1 / (1 / _OUTPUT_RATE - Fraction(1, 1_000_000_000)), True),
        (1 / (1 / _OUTPUT_RATE + Fraction(1001, 1_000_000_000_000)), False),
        (Fraction(2997, 50), False),
        (Fraction(60), False),
    ],
)
def test_final_accepts_only_one_ns_period_quantization_and_preserves_canonical_rate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observed: Fraction,
    expected_pass: bool,
) -> None:
    """容器表示的 <=1 ns 界不扩成 FI 容差，Artifact canonical 与 observed 分别保留。"""

    context = _final_rate_context(tmp_path)
    video = _video(
        codec="hevc",
        profile="Main 10",
        pixel_format="yuv420p10le",
        rate=observed,
        frames=400,
        duration=400 / float(_OUTPUT_RATE),
    )
    header = _media(context.outputs[0].path, video)
    monkeypatch.setattr(validators, "probe_header", lambda _path: header)

    result = validators.validate_final_mux(context)

    assert result.passed is expected_pass
    if expected_pass:
        namespace = result.media_info_extensions["media"][AV27_NAMESPACE]
        assert namespace["frame_rate"] == "60000/1001"
        assert namespace["video"] == video.to_summary()
        assert namespace["frame_count"] == 400
    else:
        assert result.summary["code"] == "E_AV27_FPS_CHANGED"
        assert result.media_info_extensions == {}


@pytest.mark.parametrize("field", ("frame_rate", "avg_frame_rate", "r_frame_rate"))
def test_final_rejects_each_conflicting_observed_rate_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """另外两个 rate 正确不能遮蔽一个独立 observed header 冲突。"""

    context = _final_rate_context(tmp_path)
    video = _video(
        codec="hevc",
        profile="Main 10",
        pixel_format="yuv420p10le",
        rate=_OUTPUT_RATE,
        frames=400,
    )
    mutation: dict[str, Any] = {field: Fraction(2997, 50)}
    header = _media(context.outputs[0].path, replace(video, **mutation))
    monkeypatch.setattr(validators, "probe_header", lambda _path: header)

    result = validators.validate_final_mux(context)

    assert result.passed is False
    assert result.summary["code"] == "E_AV27_FPS_CHANGED"
    assert field in (result.message or "")
    assert result.media_info_extensions == {}


def test_final_quantized_rate_does_not_relax_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _final_rate_context(tmp_path)
    video = _video(
        codec="hevc",
        profile="Main 10",
        pixel_format="yuv420p10le",
        rate=Fraction(19001, 317),
        width=1280,
        height=720,
        frames=400,
        duration=400 / float(_OUTPUT_RATE),
    )
    header = _media(context.outputs[0].path, video)
    monkeypatch.setattr(validators, "probe_header", lambda _path: header)

    result = validators.validate_final_mux(context)

    assert result.passed is False
    assert result.summary["code"] == "E_AV27_GEOMETRY_CHANGED"
    assert result.media_info_extensions == {}


@pytest.mark.parametrize("observed", (Fraction(19001, 317), Fraction(1_000_000_000, 16_683_333)))
def test_program_mp4_still_rejects_nonexact_header_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, observed: Fraction
) -> None:
    """Final 专用表示量化不允许传入 Program，MP4 的 canonical FPS 仍要求 exact。"""

    fi = _input(
        tmp_path,
        "chapters",
        kind="VideoFile",
        producer_port="video",
        artifact_id="fi-rate",
        ordinal=0,
        namespace=_namespace(
            frames=399,
            rate=_OUTPUT_RATE,
            stage={
                "kind": "frame_interpolation",
                "model_name": "Aion",
                "model_version": None,
                "operator_declared": True,
            },
        ),
    )
    output = _output(tmp_path, "video", "program.mp4", producer={"output_frames": 400})
    header = _media(
        output.path,
        _video(
            codec="hevc",
            profile="Main 10",
            codec_tag="hvc1",
            pixel_format="yuv420p10le",
            rate=observed,
            time_base=Fraction(1, 60000),
            frames=400,
            duration=400 / float(_OUTPUT_RATE),
        ),
        format_name="mp4",
    )
    monkeypatch.setattr(validators, "probe_header", lambda _path: header)
    result = validators.validate_program_encode(
        _context(
            tmp_path,
            program_encode_definition(),
            {
                "encoder": "cpu",
                "source_fps": "30000/1001",
                "chapters": [
                    {
                        "chapter_id": "chapter-0001",
                        "chapter_ordinal": 0,
                        "source_frames": 200,
                        "expected_fi_frames": 399,
                        "encoded_frames": 400,
                    }
                ],
                "expected_geometry": _geometry(),
                "expected_signal": _full_signal(),
            },
            inputs=(fi,),
            outputs=(output,),
        )
    )
    assert result.passed is False
    assert result.summary["code"] == "E_AV27_FPS_CHANGED"
    assert result.media_info_extensions == {}
