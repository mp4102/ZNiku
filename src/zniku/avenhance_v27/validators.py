"""验证 AVEnhanceFlow v2.7 专用节点的局部媒体合同。

validator 只消费当前 attempt 的直接 ``RunnerInput``、已声明输出与受控 probe 结果；不会读取
Project、NodeResult sidecar 或 AVEnhanceFlow task/state。automatic producer 的原始帧计数必须先与
计划和 header 闭合，随后才通过 ``media_info_extensions`` 写入统一 namespace。任何未知字段、
模糊帧数、流布局、颜色或顺序异常都失败关闭，且失败结果不携带可合并 extension。文件名只接受
当前请求正式 output path 的精确 basename；未覆盖的端口继续使用旧固定名称，不接受任意来件名。
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PureWindowsPath
from typing import Final, cast

from zniku.avenhance_v27.planner import (
    FinalSource,
    SplitSegment,
    parse_final_sources,
    parse_program_chapters,
    parse_split_segments,
)
from zniku.avenhance_v27.probe import (
    AV27_NAMESPACE,
    Av27MediaError,
    Av27MediaHeader,
    Av27VideoHeader,
    audio_signatures_from_metadata,
    canonical_fraction,
    metadata_frame_count,
    metadata_rate,
    namespace_from_media_info,
    parse_fraction,
    probe_header,
    probe_source_timeline,
    rates_equivalent,
    require_only_av_streams,
    require_progressive_zero_rotation,
    require_square_sar,
    resolve_bt709_signal,
    source_namespace_summary,
)
from zniku.runtime import FrameRange, NodeValidatorContext, NodeValidatorResult, RunnerInput
from zniku.runtime.runner import ValidatedOutput

_PRORES_CODECS: Final = frozenset({"prores", "prores_ks", "prores_aw"})
_TIMECODE_CODECS: Final = frozenset({"tmcd", "timecode"})
_EXTERNAL_COUNT_ALLOWLIST: Final = frozenset(
    {
        ("matroska", "ffv1"),
        ("matroska", "h264"),
        ("matroska", "hevc"),
        ("matroska", "prores"),
        ("matroska", "prores_ks"),
        ("mov", "prores"),
        ("mov", "prores_ks"),
        ("mov", "prores_aw"),
    }
)
_FI_RATE_TOLERANCE: Final = Fraction(1, 500_000)
_FINAL_MATROSKA_PERIOD_TOLERANCE: Final = Fraction(1, 1_000_000_000)
_CANONICAL_SIGNAL_KEYS: Final = (
    "color_range",
    "color_space",
    "color_transfer",
    "color_primaries",
    "chroma_location",
    "field_order",
    "rotation",
)


@dataclass(frozen=True, slots=True)
class _Validation:
    """保存一个全部通过后才可交给 Runner 的 validator 结果。"""

    summary: Mapping[str, object]
    extensions: Mapping[str, Mapping[str, Mapping[str, object]]]
    warnings: tuple[str, ...] = ()


def validate_source_program(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证 Source header 与唯一完整 timeline traversal，并登记两路同源 summary。"""

    return _validated(lambda: _validate_source_program(context))


def validate_source_admission(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证 admission gate 与全部 Source metadata 的精确闭合。"""

    return _validated(lambda: _validate_source_admission(context))


def validate_mosaic_restoration(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证 MR 的 ``N -> N``、容器、几何、信号与人工模型声明。"""

    return _validated(lambda: _validate_mosaic_restoration(context))


def validate_atomic_split(context: NodeValidatorContext) -> NodeValidatorResult:
    """按物理 Source 闭合 producer count，并从 half-open plan 派生 leaf 帧数。"""

    return _validated(lambda: _validate_atomic_split(context))


def validate_enhancement(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证 Enhancement 的整数等比 ProRes 422 HQ ``N -> N`` 输出。"""

    return _validated(lambda: _validate_enhancement(context))


def validate_merge_video(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证单章一次 Merge 的输入顺序、ProRes header 与帧数守恒。"""

    return _validated(lambda: _validate_merge_video(context))


def validate_frame_interpolation(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证 FI 的唯一 video-only ProRes 输出、``2N-1`` 与冻结 FPS 容差。"""

    return _validated(lambda: _validate_frame_interpolation(context))


def validate_program_encode(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证 Program 的逐章关系、单一 producer count 与 Main10/hvc1 header。"""

    return _validated(lambda: _validate_program_encode(context))


def validate_final_mux(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证 Final 的 Program 视频、原始音轨顺序、时长与 producer count。"""

    return _validated(lambda: _validate_final_mux(context))


def _validate_source_program(context: NodeValidatorContext) -> _Validation:
    if context.request.inputs:
        raise Av27MediaError("E_AV27_SOURCE_INPUT", "SourceProgram 不接受 input")
    outputs = _outputs(context, {"video", "source_media"})
    for output in outputs.values():
        _require_no_producer_metadata(output)
    source_value = _required_text(context.request.node.parameters, "source_path")
    source = Path(source_value)
    if not source.is_absolute():
        raise Av27MediaError("E_AV27_SOURCE_PATH", "source_path 必须是绝对路径")
    try:
        resolved_source = source.resolve(strict=True)
        resolved_outputs = {item.path.resolve(strict=True) for item in outputs.values()}
    except OSError as error:
        raise Av27MediaError("E_AV27_SOURCE_PATH", str(error)) from error
    if resolved_outputs != {resolved_source}:
        raise Av27MediaError("E_AV27_SOURCE_BINDING", "两路 Source output 必须引用同一 source_path")
    source_ordinal = _nonnegative_int(
        context.request.node.parameters.get("source_ordinal"),
        "source_ordinal",
    )

    # header gate 必须先于唯一完整 traversal，避免坏输入产生昂贵媒体读取。
    media = probe_header(resolved_source)
    _ = media.video
    require_only_av_streams(media, role="SourceProgram")
    if media.chapter_count:
        raise Av27MediaError("E_AV27_SOURCE_CHAPTERS", "SourceProgram 不接受内置 chapters")
    timeline = probe_source_timeline(resolved_source, header=media)
    namespace, warnings = source_namespace_summary(
        media,
        timeline,
        source_ordinal=source_ordinal,
    )
    namespace["signal"] = _canonical_signal(namespace.get("signal"), role="SourceProgram")
    extensions = {
        port_id: {AV27_NAMESPACE: dict(namespace)} for port_id in ("video", "source_media")
    }
    return _Validation(
        summary={
            "source_ordinal": source_ordinal,
            "frame_count": timeline.frame_count,
            "frame_rate": namespace["frame_rate"],
            "audio_stream_count": len(media.audios),
            "timeline_authority": timeline.authority,
        },
        extensions=extensions,
        warnings=warnings,
    )


def _validate_source_admission(context: NodeValidatorContext) -> _Validation:
    sources = _ordered_inputs(context, "sources", kind="MediaFile", producer_port="source_media")
    output = _single_output(context, "gate")
    _require_fixed_name(context, output, "admission.json")
    _require_no_producer_metadata(output)
    parameters = context.request.node.parameters
    mode = _enum(parameters.get("source_mode"), "source_mode", {"program", "pre_chaptered"})
    if mode == "program" and len(sources) != 1:
        raise Av27MediaError("E_AV27_ADMISSION_PROGRAM_COUNT", "program 模式必须恰好一个 Source")
    declarations = _mapping_array(parameters.get("sources"), "sources")
    if len(declarations) != len(sources):
        raise Av27MediaError("E_AV27_ADMISSION_SOURCE_COUNT", "Source 声明与 inputs 数量不一致")

    expected_sources: list[dict[str, object]] = []
    common: tuple[object, object, object] | None = None
    common_audio: tuple[dict[str, object], ...] | None = None
    for ordinal, (source, declaration) in enumerate(zip(sources, declarations, strict=True)):
        if _nonnegative_int(declaration.get("source_ordinal"), "source_ordinal") != ordinal:
            raise Av27MediaError("E_AV27_ADMISSION_SOURCE_ORDER", "Source 声明 ordinal 不连续")
        namespace = namespace_from_media_info(source.media_info)
        if _nonnegative_int(namespace.get("source_ordinal"), "metadata source_ordinal") != ordinal:
            raise Av27MediaError("E_AV27_ADMISSION_SOURCE_ORDER", "Source metadata ordinal 不匹配")
        signature = (
            namespace.get("frame_rate"),
            namespace.get("geometry"),
            namespace.get("signal"),
        )
        if common is None:
            common = signature
        elif signature != common:
            raise Av27MediaError(
                "E_AV27_ADMISSION_MEDIA_MISMATCH",
                "Source FPS/geometry/signal 不一致",
            )
        audio = audio_signatures_from_metadata(source.media_info)
        if mode == "pre_chaptered":
            if common_audio is None:
                common_audio = audio
            elif audio != common_audio:
                raise Av27MediaError(
                    "E_AV27_ADMISSION_AUDIO_MISMATCH",
                    "pre_chaptered logical audio signatures 不一致",
                )
        expected_sources.append(
            {
                "source_ordinal": ordinal,
                "artifact_id": source.artifact_id,
                "frame_count": metadata_frame_count(source.media_info),
                "frame_rate": canonical_fraction(metadata_rate(source.media_info)),
                "geometry": namespace.get("geometry"),
                "signal": namespace.get("signal"),
                "audio_tracks": list(audio),
            }
        )
    expected = {
        "schema": "zniku.avenhance.v27.admission/1",
        "source_mode": mode,
        "sources": expected_sources,
    }
    if _load_strict_json(output.path) != expected:
        raise Av27MediaError("E_AV27_ADMISSION_CONTENT", "admission.json 与 inputs metadata 不一致")
    return _Validation(
        summary={"source_mode": mode, "source_count": len(sources), "barrier": "resolved"},
        extensions={},
    )


def _validate_mosaic_restoration(context: NodeValidatorContext) -> _Validation:
    source = _single_input(context, "video", kind="VideoFile")
    _single_input(context, "gate", kind="DataFile", producer_port="gate")
    output = _single_output(context, "video")
    _require_fixed_name(context, output, "mr.mkv")
    _require_no_producer_metadata(output)
    expected = _metadata_contract(source)
    media = probe_header(output.path)
    _require_formats(media, {"matroska"}, role="Mosaic Restoration")
    _require_mr_layout(media)
    count, count_authority = _external_frame_count(media)
    if count != expected.frame_count:
        raise Av27MediaError("E_AV27_MR_FRAME_COUNT", "MR 输出不满足 N -> N")
    warnings = _validate_video_against_contract(
        media.video,
        expected,
        role="Mosaic Restoration",
        exact_rate=True,
    )
    model_name = _required_text(context.request.node.parameters, "model_name")
    model_version = _required_text(context.request.node.parameters, "model_version")
    namespace = _output_namespace(
        media,
        count,
        stage={
            "kind": "mosaic_restoration",
            "model_name": model_name,
            "model_version": model_version,
            "operator_declared": True,
        },
    )
    return _media_validation(
        output.port_id,
        namespace,
        warnings=warnings,
        summary={"frame_count": count, "frame_count_authority": count_authority},
    )


def _validate_atomic_split(context: NodeValidatorContext) -> _Validation:
    videos = _ordered_inputs(context, "videos", kind="VideoFile", producer_port="video")
    gate = _single_input(context, "gate", kind="DataFile", producer_port="gate")
    planned_gate = _required_text(
        context.request.node.parameters,
        "planned_admission_artifact_id",
    )
    if gate.artifact_id != planned_gate:
        raise Av27MediaError("E_AV27_PLAN_INPUT_CHANGED", "Admission Artifact 已变化")
    output_order = tuple(output.port_id for output in context.outputs)
    if len(set(output_order)) != len(output_order):
        raise Av27MediaError("E_AV27_OUTPUT_DUPLICATE", "Split output port 重复")
    segments = parse_split_segments(
        context.request.node.parameters.get("segments"),
        expected_ports=output_order,
    )
    outputs = {item.port_id: item for item in context.outputs}
    if len(outputs) != len(segments):
        raise Av27MediaError("E_AV27_SPLIT_OUTPUT_COUNT", "Split output shape 不匹配")
    grouped: dict[int, list[SplitSegment]] = defaultdict(list)
    for segment in segments:
        grouped[segment.source_ordinal].append(segment)
    if tuple(sorted(grouped)) != tuple(range(len(videos))):
        raise Av27MediaError("E_AV27_SPLIT_SOURCE_COUNT", "Split source group 不连续")

    extensions: dict[str, dict[str, dict[str, object]]] = {}
    warnings: list[str] = []
    source_counts: list[int] = []
    for source_ordinal, source in enumerate(videos):
        source_contract = _metadata_contract(source)
        source_segments = tuple(grouped[source_ordinal])
        if (
            source_segments[0].start_frame != 0
            or source_segments[-1].end_frame != source_contract.frame_count
            or any(
                segment.planned_effective_video_artifact_id != source.artifact_id
                for segment in source_segments
            )
        ):
            raise Av27MediaError(
                "E_AV27_PLAN_INPUT_CHANGED",
                "Split ranges 或 planned Artifact ID 与 current input 不一致",
            )
        producer_count = _split_producer_count(
            source,
            source_segments,
            outputs,
            expected=source_contract.frame_count,
        )
        source_counts.append(producer_count)
        for segment in source_segments:
            output = outputs[segment.port_id]
            _require_fixed_name(context, output, f"{segment.port_id}.mkv")
            if output.frame_range != FrameRange(
                start_frame=segment.start_frame,
                end_frame=segment.end_frame,
            ):
                raise Av27MediaError(
                    "E_AV27_SPLIT_FRAME_RANGE",
                    f"{segment.port_id} Artifact frame_range 与 plan 不一致",
                )
            media = probe_header(output.path)
            _require_formats(media, {"matroska"}, role="AtomicSplit")
            _require_video_only(media, role="AtomicSplit", chapters_forbidden=True)
            video = media.video
            if video.codec != "ffv1" or video.pixel_format != "yuv420p10le":
                raise Av27MediaError(
                    "E_AV27_SPLIT_CODEC",
                    "Split leaf 必须为 FFV1 yuv420p10le",
                )
            if (video.width, video.height) != (1920, 1080):
                raise Av27MediaError("E_AV27_SPLIT_GEOMETRY", "Split leaf 必须为 1920x1080")
            require_square_sar(video, role="AtomicSplit")
            require_progressive_zero_rotation(video, role="AtomicSplit")
            signal, signal_warnings = _resolved_signal(
                video,
                role="AtomicSplit",
                require_left=True,
                require_explicit=True,
            )
            warnings.extend(signal_warnings)
            if video.frame_rate != source_contract.frame_rate:
                raise Av27MediaError("E_AV27_SPLIT_FPS", "Split leaf FPS 与 Source 不一致")
            namespace = _output_namespace(
                media,
                segment.frame_count,
                signal=signal,
                stage={
                    "kind": "atomic_split",
                    "source_ordinal": source_ordinal,
                    "chapter_id": segment.chapter_id,
                    "chapter_ordinal": segment.chapter_ordinal,
                    "leaf_id": segment.leaf_id,
                    "leaf_ordinal": segment.leaf_ordinal,
                    "start_frame": segment.start_frame,
                    "end_frame": segment.end_frame,
                },
            )
            extensions[segment.port_id] = {AV27_NAMESPACE: namespace}
    return _Validation(
        summary={
            "source_count": len(videos),
            "leaf_count": len(segments),
            "producer_frame_counts": source_counts,
        },
        extensions=extensions,
        warnings=tuple(warnings),
    )


def _validate_enhancement(context: NodeValidatorContext) -> _Validation:
    source = _single_input(context, "video", kind="VideoFile")
    output = _single_output(context, "video")
    _require_fixed_name(context, output, "enhancement.mov")
    _require_no_producer_metadata(output)
    input_contract = _metadata_contract(source)
    parameters = context.request.node.parameters
    expected_count = _positive_int(parameters.get("expected_frames"), "expected_frames")
    expected_rate = parse_fraction(parameters.get("expected_fps"))
    if expected_count != input_contract.frame_count or expected_rate != input_contract.frame_rate:
        raise Av27MediaError("E_AV27_ENHANCEMENT_PLAN", "Enhancement 参数与 input metadata 不一致")
    input_geometry = _geometry_parameter(parameters.get("expected_input_geometry"))
    output_geometry = _geometry_parameter(parameters.get("expected_output_geometry"))
    if input_geometry[:2] != input_contract.geometry:
        raise Av27MediaError("E_AV27_ENHANCEMENT_GEOMETRY", "expected input geometry 已陈旧")

    media = probe_header(output.path)
    _require_formats(media, {"mov"}, role="Enhancement")
    _require_enhancement_layout(media)
    video = media.video
    _require_prores(video, role="Enhancement")
    require_square_sar(video, role="Enhancement")
    require_progressive_zero_rotation(video, role="Enhancement")
    signal, warnings = _resolved_signal(video, role="Enhancement")
    _compare_signal(signal, input_contract.signal, role="Enhancement")
    if video.frame_rate != expected_rate:
        raise Av27MediaError("E_AV27_ENHANCEMENT_FPS", "Enhancement FPS 与 input 不一致")
    if (video.width, video.height) != output_geometry[:2]:
        raise Av27MediaError("E_AV27_ENHANCEMENT_GEOMETRY", "Enhancement output geometry 不匹配")
    measured_scale = _integer_scale(input_contract.geometry, (video.width, video.height))
    raw_scale = parameters.get("actual_scale_factor")
    if raw_scale is None:
        if measured_scale > 1:
            raise Av27MediaError(
                "E_AV27_ENHANCEMENT_SCALE_CONFIRMATION",
                "超分输出必须声明 actual_scale_factor",
            )
        scale = 1
    else:
        scale = _positive_int(raw_scale, "actual_scale_factor")
        if measured_scale != scale:
            raise Av27MediaError("E_AV27_ENHANCEMENT_SCALE", "实际整数缩放与声明不一致")
    count, authority = _external_frame_count(media)
    if count != expected_count:
        raise Av27MediaError(
            "E_AV27_ENHANCEMENT_FRAME_COUNT",
            f"增强结果帧数不符：预期 {expected_count} 帧，实际 {count} 帧。"
            "请确认是否选错分段，或外部工具是否改变了帧数。",
        )
    namespace = _output_namespace(
        media,
        count,
        signal=signal,
        stage=_operator_stage(parameters, "enhancement", scale=scale),
    )
    return _media_validation(
        output.port_id,
        namespace,
        warnings=warnings,
        summary={"frame_count": count, "frame_count_authority": authority, "scale_factor": scale},
    )


def _validate_merge_video(context: NodeValidatorContext) -> _Validation:
    inputs = _ordered_inputs(context, "videos", kind="VideoFile", producer_port="video")
    output = _single_output(context, "video")
    _require_fixed_name(context, output, "merge.mov")
    parameters = context.request.node.parameters
    contracts = tuple(_metadata_contract(item) for item in inputs)
    expected = _positive_int(parameters.get("expected_frames"), "expected_frames")
    if sum(item.frame_count for item in contracts) != expected:
        raise Av27MediaError("E_AV27_MERGE_INPUT_FRAMES", "Merge input 总帧数与计划不一致")
    rate = parse_fraction(parameters.get("expected_fps"))
    geometry = _geometry_parameter(parameters.get("expected_geometry"))[:2]
    _require_uniform_contracts(contracts, rate=rate, geometry=geometry, role="Merge")
    stages = tuple(
        _operator_declaration(
            _stage_from_metadata(item.media_info),
            kind="enhancement",
            include_scale=True,
        )
        for item in inputs
    )
    expected_stage = {
        "kind": "enhancement",
        "model_name": _required_text(parameters, "model_name"),
        "model_version": _optional_text(parameters.get("model_version"), "model_version"),
        "operator_declared": True,
        "actual_scale_factor": _positive_int(
            parameters.get("actual_scale_factor"),
            "actual_scale_factor",
        ),
    }
    if any(dict(stage) != expected_stage for stage in stages):
        raise Av27MediaError("E_AV27_MERGE_DECLARATION", "Enhancement model/scale 声明不一致")

    producer_count = _required_producer_count(output, expected)
    media = probe_header(output.path)
    _require_formats(media, {"mov"}, role="Merge")
    _require_video_only(media, role="Merge", chapters_forbidden=True)
    video = media.video
    _require_prores(video, role="Merge")
    require_square_sar(video, role="Merge")
    require_progressive_zero_rotation(video, role="Merge")
    signal, warnings = _resolved_signal(video, role="Merge")
    _compare_signal(signal, contracts[0].signal, role="Merge")
    _require_header_contract(video, rate=rate, geometry=geometry, role="Merge")
    _optional_header_count_matches(video, expected, role="Merge")
    namespace = _output_namespace(
        media,
        producer_count,
        signal=signal,
        stage={
            "kind": "merge_video",
            "chapter_id": _required_text(parameters, "chapter_id"),
            "chapter_ordinal": _nonnegative_int(
                parameters.get("chapter_ordinal"), "chapter_ordinal"
            ),
            "enhancement": stages[0],
        },
    )
    return _media_validation(
        output.port_id,
        namespace,
        warnings=warnings,
        summary={"input_count": len(inputs), "frame_count": producer_count},
    )


def _validate_frame_interpolation(context: NodeValidatorContext) -> _Validation:
    source = _single_input(context, "video", kind="VideoFile", producer_port="video")
    output = _single_output(context, "video")
    _require_fixed_name(context, output, "fi.mov")
    _require_no_producer_metadata(output)
    input_contract = _metadata_contract(source)
    parameters = context.request.node.parameters
    source_fps = parse_fraction(parameters.get("source_fps"))
    expected_input = _positive_int(parameters.get("expected_input_frames"), "expected_input_frames")
    expected_output = _positive_int(
        parameters.get("expected_output_frames"),
        "expected_output_frames",
    )
    if (
        source_fps != input_contract.frame_rate
        or expected_input != input_contract.frame_count
        or expected_output != expected_input * 2 - 1
    ):
        raise Av27MediaError("E_AV27_FI_PLAN", "FI 参数不满足 input N/FPS 与 2N-1")
    geometry = _geometry_parameter(parameters.get("expected_geometry"))[:2]
    if geometry != input_contract.geometry:
        raise Av27MediaError("E_AV27_FI_GEOMETRY", "FI expected geometry 已陈旧")
    expected_signal = _canonical_signal(
        parameters.get("expected_signal"),
        role="FI expected_signal",
    )
    _compare_signal(input_contract.signal, expected_signal, role="FI input")

    media = probe_header(output.path)
    _require_formats(media, {"mov"}, role="Frame interpolation")
    _require_video_only(media, role="Frame interpolation", chapters_forbidden=True)
    video = media.video
    _require_prores(video, role="Frame interpolation")
    require_square_sar(video, role="Frame interpolation")
    require_progressive_zero_rotation(video, role="Frame interpolation")
    signal, warnings = _resolved_signal(video, role="Frame interpolation")
    _compare_signal(signal, input_contract.signal, role="Frame interpolation")
    if (video.width, video.height) != geometry:
        raise Av27MediaError("E_AV27_FI_GEOMETRY", "FI geometry 与 master 不一致")
    expected_rate = source_fps * 2
    if not rates_equivalent(video.frame_rate, expected_rate, tolerance=_FI_RATE_TOLERANCE):
        raise Av27MediaError(
            "E_AV27_FI_FPS",
            "FI observed FPS 必须在相对 2e-6 内等价 source FPS x2",
        )
    count, authority = _external_frame_count(media)
    if count != expected_output:
        code = "E_AV27_FI_DOUBLE_COUNT" if count == expected_input * 2 else "E_AV27_FI_FRAME_COUNT"
        raise Av27MediaError(code, "FI 输出必须精确为 2N-1")
    namespace = _output_namespace(
        media,
        count,
        signal=signal,
        canonical_rate=expected_rate,
        stage=_operator_stage(parameters, "frame_interpolation"),
    )
    return _media_validation(
        output.port_id,
        namespace,
        warnings=warnings,
        summary={
            "frame_count": count,
            "frame_count_authority": authority,
            "observed_frame_rate": canonical_fraction(video.frame_rate),
            "canonical_frame_rate": canonical_fraction(expected_rate),
        },
    )


def _validate_program_encode(context: NodeValidatorContext) -> _Validation:
    inputs = _ordered_inputs(context, "chapters", kind="VideoFile", producer_port="video")
    output = _single_output(context, "video")
    _require_fixed_name(context, output, "program.mp4")
    parameters = context.request.node.parameters
    chapters = parse_program_chapters(parameters.get("chapters"))
    if len(inputs) != len(chapters):
        raise Av27MediaError("E_AV27_PROGRAM_INPUT_COUNT", "Program chapter 数量不匹配")
    contracts = tuple(_metadata_contract(item) for item in inputs)
    for item, contract, chapter in zip(inputs, contracts, chapters, strict=True):
        if (
            item.input_ordinal != chapter.chapter_ordinal
            or contract.frame_count != chapter.expected_fi_frames
        ):
            raise Av27MediaError(
                "E_AV27_PROGRAM_INPUT_FRAMES",
                f"chapter {chapter.chapter_id} 不满足 2N-1",
            )
    source_rate = parse_fraction(parameters.get("source_fps"))
    output_rate = source_rate * 2
    geometry = _geometry_parameter(parameters.get("expected_geometry"))[:2]
    _require_uniform_contracts(contracts, rate=output_rate, geometry=geometry, role="Program")
    expected_signal = _canonical_signal(
        parameters.get("expected_signal"),
        role="Program expected_signal",
    )
    _compare_signal(contracts[0].signal, expected_signal, role="Program input")
    stages = tuple(
        _operator_declaration(
            _stage_from_metadata(item.media_info),
            kind="frame_interpolation",
            include_scale=False,
        )
        for item in inputs
    )
    if any(stage != stages[0] for stage in stages[1:]):
        raise Av27MediaError("E_AV27_PROGRAM_DECLARATION", "FI model 声明不一致")
    expected = sum(item.encoded_frames for item in chapters)
    producer_count = _required_producer_count(output, expected)

    media = probe_header(output.path)
    _require_formats(media, {"mov", "mp4"}, role="Program")
    _require_video_only(media, role="Program", chapters_forbidden=True)
    video = media.video
    normalized_profile = _normalized_profile(video.profile)
    if (
        video.codec != "hevc"
        or normalized_profile != "main10"
        or video.pixel_format != "yuv420p10le"
        or (video.codec_tag_string or "").casefold() != "hvc1"
    ):
        raise Av27MediaError(
            "E_AV27_PROGRAM_CODEC",
            "Program 必须为 HEVC Main10 yuv420p10le hvc1",
        )
    require_square_sar(video, role="Program")
    require_progressive_zero_rotation(video, role="Program")
    signal, warnings = _resolved_signal(
        video,
        role="Program",
        require_left=True,
        require_explicit=True,
    )
    _require_header_contract(video, rate=output_rate, geometry=geometry, role="Program")
    if video.time_base != Fraction(1, output_rate.numerator):
        raise Av27MediaError("E_AV27_PROGRAM_TIME_BASE", "Program track timescale 不精确")
    _optional_header_count_matches(video, expected, role="Program")
    namespace = _output_namespace(
        media,
        producer_count,
        signal=signal,
        canonical_rate=output_rate,
        stage={
            "kind": "program_encode",
            "encoder": _enum(parameters.get("encoder"), "encoder", {"cpu", "gpu"}),
            "chapter_count": len(chapters),
        },
    )
    return _media_validation(
        output.port_id,
        namespace,
        warnings=warnings,
        summary={"chapter_count": len(chapters), "frame_count": producer_count},
    )


def _validate_final_mux(context: NodeValidatorContext) -> _Validation:
    program = _single_input(context, "video", kind="VideoFile", producer_port="video")
    sources = _ordered_inputs(context, "sources", kind="MediaFile", producer_port="source_media")
    _single_input(context, "gate", kind="DataFile", producer_port="gate")
    output = _single_output(context, "media")
    _require_fixed_name(context, output, "final.mkv")
    parameters = context.request.node.parameters
    mode = _enum(parameters.get("source_mode"), "source_mode", {"program", "pre_chaptered"})
    if mode == "program" and len(sources) != 1:
        raise Av27MediaError("E_AV27_FINAL_PROGRAM_SOURCE_COUNT", "program 模式只允许一个 Source")
    plans = parse_final_sources(parameters.get("sources"))
    if len(plans) != len(sources):
        raise Av27MediaError("E_AV27_FINAL_SOURCE_COUNT", "Final Source 数量不匹配")
    _validate_final_source_bindings(sources, plans)
    program_contract = _metadata_contract(program)
    expected = _positive_int(parameters.get("expected_program_frames"), "expected_program_frames")
    if program_contract.frame_count != expected:
        raise Av27MediaError("E_AV27_FINAL_PROGRAM_FRAMES", "Program Artifact N 已变化")
    expected_geometry = _geometry_parameter(parameters.get("expected_geometry"))[:2]
    if program_contract.geometry != expected_geometry:
        raise Av27MediaError("E_AV27_FINAL_PROGRAM_GEOMETRY", "Program geometry 与参数不一致")
    expected_signal = _canonical_signal(
        parameters.get("expected_signal"),
        role="Final expected_signal",
    )
    _compare_signal(program_contract.signal, expected_signal, role="Final Program")
    expected_audio = audio_signatures_from_metadata(sources[0].media_info)
    for source in sources[1:]:
        if audio_signatures_from_metadata(source.media_info) != expected_audio:
            raise Av27MediaError("E_AV27_FINAL_AUDIO_MISMATCH", "Source audio signatures 不一致")
    producer_count = _required_producer_count(output, expected)

    media = probe_header(output.path)
    _require_formats(media, {"matroska"}, role="Final")
    require_only_av_streams(media, role="Final")
    if len(media.videos) != 1 or media.chapter_count:
        raise Av27MediaError("E_AV27_FINAL_LAYOUT", "Final 必须唯一视频且 chapter_count=0")
    video = media.video
    if (
        video.codec != "hevc"
        or _normalized_profile(video.profile) != "main10"
        or video.pixel_format != "yuv420p10le"
    ):
        raise Av27MediaError(
            "E_AV27_FINAL_VIDEO",
            "Final 视频必须保持 Program HEVC Main10 yuv420p10le",
        )
    if tuple(audio.signature() for audio in media.audios) != expected_audio:
        raise Av27MediaError(
            "E_AV27_FINAL_AUDIO_MISMATCH",
            "Final 音轨数量、顺序或 header signature 与 Source 不一致",
        )
    require_square_sar(video, role="Final")
    require_progressive_zero_rotation(video, role="Final")
    signal, warnings = _resolved_signal(
        video,
        role="Final",
        require_left=True,
        require_explicit=True,
    )
    _compare_signal(signal, program_contract.signal, role="Final")
    _require_final_header_contract(
        video,
        canonical_rate=program_contract.frame_rate,
        geometry=program_contract.geometry,
    )
    _optional_header_count_matches(video, expected, role="Final")
    _validate_final_duration(media, program_contract)
    namespace = _output_namespace(
        media,
        producer_count,
        signal=signal,
        canonical_rate=program_contract.frame_rate,
        stage={"kind": "final_mux", "source_mode": mode},
    )
    namespace["audio_tracks"] = [audio.to_summary() for audio in media.audios]
    return _media_validation(
        output.port_id,
        namespace,
        warnings=warnings,
        summary={
            "frame_count": producer_count,
            "source_mode": mode,
            "audio_stream_count": len(media.audios),
        },
    )


@dataclass(frozen=True, slots=True)
class _MediaContract:
    """从直接 input namespaced metadata 读取的最小媒体合同。"""

    frame_count: int
    frame_rate: Fraction
    geometry: tuple[int, int]
    signal: Mapping[str, object]
    duration_seconds: float | None


def _metadata_contract(item: RunnerInput) -> _MediaContract:
    namespace = namespace_from_media_info(item.media_info)
    geometry = namespace.get("geometry")
    if not isinstance(geometry, Mapping):
        raise Av27MediaError("E_AV27_METADATA_GEOMETRY", "metadata geometry 缺失")
    width = _positive_int(geometry.get("width"), "geometry.width")
    height = _positive_int(geometry.get("height"), "geometry.height")
    signal = namespace.get("signal")
    if not isinstance(signal, Mapping):
        raise Av27MediaError("E_AV27_METADATA_SIGNAL", "metadata signal 缺失")
    normalized_signal = _canonical_signal(signal, role="input metadata")
    duration_raw = namespace.get("duration_seconds")
    duration: float | None
    if duration_raw is None:
        duration = None
    elif (
        isinstance(duration_raw, bool)
        or not isinstance(duration_raw, int | float)
        or not math.isfinite(float(duration_raw))
        or float(duration_raw) <= 0
    ):
        raise Av27MediaError("E_AV27_METADATA_DURATION", "duration_seconds 无效")
    else:
        duration = float(duration_raw)
    return _MediaContract(
        frame_count=metadata_frame_count(item.media_info),
        frame_rate=metadata_rate(item.media_info),
        geometry=(width, height),
        signal=normalized_signal,
        duration_seconds=duration,
    )


def _output_namespace(
    media: Av27MediaHeader,
    frame_count: int,
    *,
    signal: Mapping[str, object] | None = None,
    canonical_rate: Fraction | None = None,
    stage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    video = media.video
    resolved_signal, _ = resolve_bt709_signal(video, role="output")
    result: dict[str, object] = {
        "frame_count": _positive_int(frame_count, "frame_count"),
        "frame_rate": canonical_fraction(canonical_rate or video.frame_rate),
        "geometry": {"width": video.width, "height": video.height},
        "sample_aspect_ratio": video.sample_aspect_ratio,
        "field_order": video.field_order,
        "rotation": video.rotation,
        "signal": dict(signal or _canonical_signal(resolved_signal, role="output")),
        "chroma_location": video.chroma_location,
        "duration_seconds": video.duration_seconds or media.duration_seconds,
        "container": {"format_name": media.format_name, "chapter_count": media.chapter_count},
        "video": video.to_summary(),
    }
    if stage is not None:
        result["stage"] = dict(stage)
    return result


def _media_validation(
    port_id: str,
    namespace: Mapping[str, object],
    *,
    summary: Mapping[str, object],
    warnings: tuple[str, ...] = (),
) -> _Validation:
    return _Validation(
        summary=summary,
        extensions={port_id: {AV27_NAMESPACE: dict(namespace)}},
        warnings=warnings,
    )


def _validated(action: Callable[[], _Validation]) -> NodeValidatorResult:
    try:
        result = action()
    except Av27MediaError as error:
        return NodeValidatorResult(
            passed=False,
            summary={"code": error.code},
            message=str(error),
        )
    return NodeValidatorResult(
        passed=True,
        summary=result.summary,
        warnings=result.warnings,
        media_info_extensions=result.extensions,
    )


def _single_input(
    context: NodeValidatorContext,
    port_id: str,
    *,
    kind: str,
    producer_port: str | None = None,
) -> RunnerInput:
    values = tuple(item for item in context.request.inputs if item.port_id == port_id)
    if len(values) != 1:
        raise Av27MediaError("E_AV27_INPUT_COUNT", f"{port_id} 必须精确绑定一个 input")
    value = values[0]
    _require_input_binding(value, kind=kind, producer_port=producer_port)
    if value.input_ordinal is not None:
        raise Av27MediaError("E_AV27_INPUT_ORDER", f"one input {port_id} 不得携带 ordinal")
    return value


def _ordered_inputs(
    context: NodeValidatorContext,
    port_id: str,
    *,
    kind: str,
    producer_port: str | None = None,
) -> tuple[RunnerInput, ...]:
    values = tuple(item for item in context.request.inputs if item.port_id == port_id)
    if not values or any(item.input_ordinal != ordinal for ordinal, item in enumerate(values)):
        raise Av27MediaError("E_AV27_INPUT_ORDER", f"{port_id} 必须从 ordinal 0 连续绑定")
    for value in values:
        _require_input_binding(value, kind=kind, producer_port=producer_port)
    return values


def _require_input_binding(
    item: RunnerInput,
    *,
    kind: str,
    producer_port: str | None,
) -> None:
    if item.kind != kind:
        raise Av27MediaError("E_AV27_INPUT_KIND", f"{item.port_id} input kind 不匹配")
    if producer_port is not None and item.producer_port_id != producer_port:
        raise Av27MediaError(
            "E_AV27_INPUT_PRODUCER",
            f"{item.port_id} 必须直接来自 {producer_port!r} output",
        )


def _single_output(context: NodeValidatorContext, port_id: str) -> ValidatedOutput:
    values = tuple(item for item in context.outputs if item.port_id == port_id)
    if len(values) != 1:
        raise Av27MediaError("E_AV27_OUTPUT_COUNT", f"{port_id} 输出数量必须为 1")
    return values[0]


def _outputs(
    context: NodeValidatorContext,
    expected_ports: set[str],
) -> dict[str, ValidatedOutput]:
    values = {item.port_id: item for item in context.outputs}
    if len(values) != len(context.outputs) or set(values) != expected_ports:
        raise Av27MediaError("E_AV27_OUTPUT_COUNT", "output port shape 无效")
    return values


def _require_fixed_name(
    context: NodeValidatorContext, output: ValidatedOutput, expected: str
) -> None:
    """名称服从当前 attempt 的正式路径覆盖；路径 confinement 仍由 Runner 强制。

    不从输出自身或节点参数提取预期名称，否则任意来件都会自我满足验收。重复覆盖失败关闭，
    其他端口的覆盖不得改变本端口合同；旧请求没有覆盖时保留原固定名。
    """

    overrides = tuple(
        item for item in context.request.output_paths if item.port_id == output.port_id
    )
    if len(overrides) > 1:
        raise Av27MediaError("E_AV27_OUTPUT_PATH", "output port 的正式路径覆盖不唯一")
    if overrides:
        expected = PureWindowsPath(overrides[0].relative_path).name
        if not expected or expected in {".", ".."}:
            raise Av27MediaError("E_AV27_OUTPUT_PATH", "output 正式路径缺少文件名")
    if output.path.name != expected:
        raise Av27MediaError("E_AV27_OUTPUT_PATH", f"output 文件名必须为 {expected!r}")


def _require_no_producer_metadata(output: ValidatedOutput) -> None:
    if output.producer_metadata:
        raise Av27MediaError(
            "E_AV27_PRODUCER_METADATA",
            f"{output.port_id} 不允许 producer_metadata",
        )


def _required_producer_count(output: ValidatedOutput, expected: int) -> int:
    metadata = output.producer_metadata
    if set(metadata) != {"output_frames"}:
        raise Av27MediaError(
            "E_AV27_PRODUCER_METADATA",
            f"{output.port_id} producer_metadata 必须只有 output_frames",
        )
    count = _positive_int(metadata.get("output_frames"), "output_frames")
    if count != expected:
        raise Av27MediaError(
            "E_AV27_PRODUCER_FRAME_COUNT",
            f"{output.port_id} producer frame count 与 expected 不一致",
        )
    return count


def _split_producer_count(
    source: RunnerInput,
    segments: Sequence[SplitSegment],
    outputs: Mapping[str, ValidatedOutput],
    *,
    expected: int,
) -> int:
    values = tuple(outputs[item.port_id].producer_metadata for item in segments)
    if all(not value for value in values):
        # fallback 只打开对应 effective-video 一次；绝不逐 leaf 计数。
        header = probe_header(source.path)
        count = probe_source_timeline(source.path, header=header).frame_count
        if count != expected:
            raise Av27MediaError(
                "E_AV27_SPLIT_SOURCE_COUNT",
                "fallback Source count 与 metadata 冲突",
            )
        return count
    if any(not value for value in values):
        raise Av27MediaError("E_AV27_PRODUCER_METADATA", "同 Source producer metadata 不完整")
    counts = tuple(_required_producer_count(outputs[item.port_id], expected) for item in segments)
    if len(set(counts)) != 1:
        raise Av27MediaError("E_AV27_PRODUCER_FRAME_COUNT", "同 Source producer count 不一致")
    return counts[0]


def _external_frame_count(media: Av27MediaHeader) -> tuple[int, str]:
    video = media.video
    formats = _format_names(media)
    if (
        video.frame_count is not None
        and any((name, video.codec) in _EXTERNAL_COUNT_ALLOWLIST for name in formats)
        and _plausible_count(media, video.frame_count)
    ):
        return video.frame_count, "header:nb_frames"
    timeline = probe_source_timeline(media.path, header=media)
    return timeline.frame_count, timeline.authority


def _plausible_count(media: Av27MediaHeader, count: int) -> bool:
    duration = media.video.duration_seconds or media.duration_seconds
    if duration is None or duration <= 0:
        return False
    tolerance = max(2 / float(media.video.frame_rate), 0.05)
    return abs(count / float(media.video.frame_rate) - duration) <= tolerance


def _require_formats(media: Av27MediaHeader, allowed: set[str], *, role: str) -> None:
    if not (_format_names(media) & allowed):
        raise Av27MediaError("E_AV27_CONTAINER", f"{role} 容器不符合合同")


def _format_names(media: Av27MediaHeader) -> set[str]:
    return {item.strip().casefold() for item in media.format_name.split(",") if item.strip()}


def _require_video_only(
    media: Av27MediaHeader,
    *,
    role: str,
    chapters_forbidden: bool,
) -> None:
    if len(media.videos) != 1 or media.streams != ("video",):
        raise Av27MediaError("E_AV27_STREAM_LAYOUT", f"{role} 必须为唯一 video-only")
    if chapters_forbidden and media.chapter_count:
        raise Av27MediaError("E_AV27_CHAPTERS", f"{role} 不允许 chapters")


def _require_mr_layout(media: Av27MediaHeader) -> None:
    require_only_av_streams(media, role="Mosaic Restoration")
    if len(media.videos) != 1 or media.chapter_count:
        raise Av27MediaError(
            "E_AV27_MR_LAYOUT",
            "Mosaic Restoration 必须唯一视频且不含 chapters",
        )


def _require_enhancement_layout(media: Av27MediaHeader) -> None:
    if len(media.videos) != 1:
        raise Av27MediaError("E_AV27_ENHANCEMENT_LAYOUT", "Enhancement 必须恰好一条视频")
    allowed = {"video", "audio", "subtitle", "data"}
    if any(kind not in allowed for kind in media.streams):
        raise Av27MediaError("E_AV27_ENHANCEMENT_LAYOUT", "Enhancement 含未知 stream")
    if any(item.codec.casefold() == "unknown" for item in media.videos) or any(
        item.codec.casefold() == "unknown" for item in media.audios
    ):
        raise Av27MediaError("E_AV27_ENHANCEMENT_LAYOUT", "Enhancement 含 unknown codec")
    for descriptor in media.others:
        codec_identities = {
            descriptor.codec.casefold(),
            (descriptor.codec_tag_string or "").casefold(),
        }
        if descriptor.kind == "subtitle" and descriptor.codec.casefold() == "unknown":
            raise Av27MediaError("E_AV27_ENHANCEMENT_LAYOUT", "Enhancement subtitle codec 未知")
        if descriptor.kind == "data" and codec_identities.isdisjoint(_TIMECODE_CODECS):
            raise Av27MediaError(
                "E_AV27_ENHANCEMENT_LAYOUT",
                "Enhancement data stream 只能是 timecode",
            )


def _require_prores(video: Av27VideoHeader, *, role: str) -> None:
    if (
        video.codec not in _PRORES_CODECS
        or _normalized_profile(video.profile) != "hq"
        or video.pixel_format != "yuv422p10le"
    ):
        raise Av27MediaError(
            "E_AV27_PRORES_PROFILE",
            f"{role} 必须为 ProRes 422 HQ yuv422p10le",
        )


def _normalized_profile(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _resolved_signal(
    video: Av27VideoHeader,
    *,
    role: str,
    require_left: bool = False,
    require_explicit: bool = False,
) -> tuple[dict[str, object], tuple[str, ...]]:
    if video.hdr_side_data:
        raise Av27MediaError("E_AV27_HDR_UNSUPPORTED", f"{role} 含 HDR side data")
    signal, raw_warnings = resolve_bt709_signal(video, role=role)
    if require_explicit and raw_warnings:
        raise Av27MediaError(
            "E_AV27_SIGNAL_MISSING",
            f"{role} 必须显式声明完整 BT.709 limited signal",
        )
    warnings = list(raw_warnings)
    chroma = video.chroma_location
    if chroma is None:
        if require_left:
            raise Av27MediaError("E_AV27_CHROMA_LOCATION", f"{role} 必须显式声明 left chroma")
        warnings.append(f"{role} chroma_location 缺失，按 left 解析")
    elif chroma.casefold() != "left":
        raise Av27MediaError("E_AV27_CHROMA_LOCATION", f"{role} chroma_location 必须为 left")
    return _canonical_signal(signal, role=role), tuple(warnings)


def _canonical_signal(value: object, *, role: str) -> dict[str, object]:
    """移除独立的 SAR 字段，把 signal 规范为冻结的七字段形状。"""

    if not isinstance(value, Mapping):
        raise Av27MediaError("E_AV27_METADATA_SIGNAL", f"{role} signal 不是 object")
    canonical: dict[str, object] = {}
    for key in _CANONICAL_SIGNAL_KEYS:
        item = value.get(key)
        if key == "rotation":
            if isinstance(item, bool) or item != 0:
                raise Av27MediaError("E_AV27_METADATA_SIGNAL", f"{role} signal.rotation 无效")
        elif not isinstance(item, str) or not item:
            raise Av27MediaError("E_AV27_METADATA_SIGNAL", f"{role} signal.{key} 无效")
        canonical[key] = item
    return canonical


def _compare_signal(
    actual: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    role: str,
) -> None:
    if dict(actual) != dict(expected):
        raise Av27MediaError("E_AV27_SIGNAL_CHANGED", f"{role} signal 与 input 不一致")


def _validate_video_against_contract(
    video: Av27VideoHeader,
    expected: _MediaContract,
    *,
    role: str,
    exact_rate: bool,
) -> tuple[str, ...]:
    require_square_sar(video, role=role)
    require_progressive_zero_rotation(video, role=role)
    signal, warnings = _resolved_signal(video, role=role)
    _compare_signal(signal, expected.signal, role=role)
    if (video.width, video.height) != expected.geometry:
        raise Av27MediaError("E_AV27_GEOMETRY_CHANGED", f"{role} geometry 与 input 不一致")
    if exact_rate and video.frame_rate != expected.frame_rate:
        raise Av27MediaError("E_AV27_FPS_CHANGED", f"{role} FPS 与 input 不一致")
    return warnings


def _require_header_contract(
    video: Av27VideoHeader,
    *,
    rate: Fraction,
    geometry: tuple[int, int],
    role: str,
) -> None:
    if video.frame_rate != rate:
        raise Av27MediaError("E_AV27_FPS_CHANGED", f"{role} FPS 不匹配")
    if (video.width, video.height) != geometry:
        raise Av27MediaError("E_AV27_GEOMETRY_CHANGED", f"{role} geometry 不匹配")


def _require_final_header_contract(
    video: Av27VideoHeader,
    *,
    canonical_rate: Fraction,
    geometry: tuple[int, int],
) -> None:
    """仅容纳 Final Matroska 整数 ns 与 FFprobe rational reduction 的表示量化。

    DefaultDuration 使用整数纳秒；例如 exact 60000/1001 经 header 表示后可读为
    19001/317。三个 observed rate 的帧周期都必须与 Program canonical 周期相差不超过
    1 ns；不能只检查 FFprobe 首选值而忽略 avg/r 冲突。此界不复用 FI 的相对容差，
    不修改 payload、Program 精确时间轴或其他节点规则，canonical metadata 仍由 Program 决定。
    """

    canonical_period = 1 / canonical_rate
    observed_rates = (
        ("frame_rate", video.frame_rate),
        ("avg_frame_rate", video.avg_frame_rate),
        ("r_frame_rate", video.r_frame_rate),
    )
    for field, observed in observed_rates:
        if observed <= 0 or abs(1 / observed - canonical_period) > _FINAL_MATROSKA_PERIOD_TOLERANCE:
            raise Av27MediaError(
                "E_AV27_FPS_CHANGED",
                f"Final {field} 帧周期与 Program canonical FPS 相差超过 1 ns",
            )
    if (video.width, video.height) != geometry:
        raise Av27MediaError("E_AV27_GEOMETRY_CHANGED", "Final geometry 不匹配")


def _optional_header_count_matches(
    video: Av27VideoHeader,
    expected: int,
    *,
    role: str,
) -> None:
    if video.frame_count is not None and video.frame_count != expected:
        raise Av27MediaError("E_AV27_HEADER_FRAME_COUNT", f"{role} header count 与 producer 冲突")


def _require_uniform_contracts(
    contracts: Sequence[_MediaContract],
    *,
    rate: Fraction,
    geometry: tuple[int, int],
    role: str,
) -> None:
    first = contracts[0]
    if first.frame_rate != rate or first.geometry != geometry:
        raise Av27MediaError("E_AV27_INPUT_CONTRACT", f"{role} input 与 expected 不一致")
    for contract in contracts[1:]:
        if (
            contract.frame_rate != first.frame_rate
            or contract.geometry != first.geometry
            or dict(contract.signal) != dict(first.signal)
        ):
            raise Av27MediaError("E_AV27_INPUT_CONTRACT", f"{role} inputs 不一致")


def _stage_from_metadata(media_info: Mapping[str, object]) -> Mapping[str, object]:
    value = namespace_from_media_info(media_info).get("stage")
    if not isinstance(value, Mapping):
        raise Av27MediaError("E_AV27_STAGE_METADATA", "input stage declaration 缺失")
    return value


def _operator_declaration(
    stage: Mapping[str, object],
    *,
    kind: str,
    include_scale: bool,
) -> dict[str, object]:
    """严格读取 external stage declaration，拒绝任意 Mapping 冒充已验证模型。"""

    expected_fields = {"kind", "model_name", "model_version", "operator_declared"}
    if include_scale:
        expected_fields.add("actual_scale_factor")
    if set(stage) != expected_fields or stage.get("kind") != kind:
        raise Av27MediaError("E_AV27_STAGE_METADATA", f"input stage 必须为 exact {kind}")
    if stage.get("operator_declared") is not True:
        raise Av27MediaError("E_AV27_STAGE_METADATA", "operator_declared 必须为 true")
    result: dict[str, object] = {
        "kind": kind,
        "model_name": _required_text(stage, "model_name"),
        "model_version": _optional_text(stage.get("model_version"), "model_version"),
        "operator_declared": True,
    }
    if include_scale:
        result["actual_scale_factor"] = _positive_int(
            stage.get("actual_scale_factor"),
            "actual_scale_factor",
        )
    return result


def _operator_stage(
    parameters: Mapping[str, object],
    kind: str,
    *,
    scale: int | None = None,
) -> dict[str, object]:
    stage: dict[str, object] = {
        "kind": kind,
        "model_name": _required_text(parameters, "model_name"),
        "model_version": _optional_text(parameters.get("model_version"), "model_version"),
        "operator_declared": True,
    }
    if scale is not None:
        stage["actual_scale_factor"] = scale
    return stage


def _integer_scale(source: tuple[int, int], target: tuple[int, int]) -> int:
    width_scale, width_remainder = divmod(target[0], source[0])
    height_scale, height_remainder = divmod(target[1], source[1])
    if width_remainder or height_remainder or width_scale != height_scale or width_scale <= 0:
        raise Av27MediaError("E_AV27_ENHANCEMENT_SCALE", "Enhancement 必须正整数等比例缩放")
    return width_scale


def _geometry_parameter(value: object) -> tuple[int, int, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "width",
        "height",
        "sample_aspect_ratio",
    }:
        raise Av27MediaError("E_AV27_PARAMETER", "geometry 字段集合无效")
    width = _positive_int(value.get("width"), "geometry.width")
    height = _positive_int(value.get("height"), "geometry.height")
    sar = value.get("sample_aspect_ratio")
    if sar not in {"1/1", "1:1"}:
        raise Av27MediaError("E_AV27_PARAMETER", "geometry SAR 必须为 1/1")
    return width, height, str(sar)


def _validate_final_source_bindings(
    sources: Sequence[RunnerInput],
    plans: Sequence[FinalSource],
) -> None:
    for ordinal, (source, plan) in enumerate(zip(sources, plans, strict=True)):
        namespace = namespace_from_media_info(source.media_info)
        if (
            source.input_ordinal != ordinal
            or plan.source_ordinal != ordinal
            or _nonnegative_int(namespace.get("source_ordinal"), "metadata source_ordinal")
            != ordinal
            or metadata_frame_count(source.media_info) != plan.source_frames
            or metadata_rate(source.media_info) != plan.source_fps
        ):
            raise Av27MediaError("E_AV27_FINAL_SOURCE_CHANGED", "Source N/FPS/ordinal 已变化")


def _validate_final_duration(media: Av27MediaHeader, program: _MediaContract) -> None:
    output_duration = media.video.duration_seconds or media.duration_seconds
    if program.duration_seconds is None or output_duration is None:
        raise Av27MediaError("E_AV27_FINAL_DURATION", "Final/Program duration header 缺失")
    tolerance = 1 / float(program.frame_rate)
    if abs(output_duration - program.duration_seconds) > tolerance:
        raise Av27MediaError("E_AV27_FINAL_DURATION", "Final 与 Program duration 相差超过一帧")


def _load_strict_json(path: Path) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON key {key!r} 重复")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"不允许 JSON 常量 {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise Av27MediaError("E_AV27_ADMISSION_CONTENT", str(error)) from error


def _mapping_array(value: object, name: str) -> tuple[Mapping[str, object], ...]:
    if (
        not isinstance(value, list | tuple)
        or not value
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} 必须是非空 object array")
    return cast(tuple[Mapping[str, object], ...], tuple(value))


def _required_text(parameters: Mapping[str, object], name: str) -> str:
    value = _optional_text(parameters.get(name), name, required=True)
    assert value is not None
    return value


def _optional_text(value: object, name: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} 必须是非空 string")
    return value


def _enum(value: object, name: str, allowed: set[str]) -> str:
    text = _optional_text(value, name, required=True)
    assert text is not None
    if text not in allowed:
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} enum 无效")
    return text


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} 必须是非负 integer")
    return value


def _positive_int(value: object, name: str) -> int:
    result = _nonnegative_int(value, name)
    if result <= 0:
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} 必须是正 integer")
    return result
