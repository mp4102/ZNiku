"""执行 AVEnhanceFlow v2.7 专用 automatic 节点。

本模块只接受 Runner 已绑定的普通 Artifact 与 attempt 内受控 output target。所有 FFmpeg/
FFprobe 调用使用 argv 和 ``shell=False``；参数不能注入 executable、filter script 或 shell。
除 SourceProgram 的只读外部引用外，输出与临时 staging 都限制在本 attempt，失败不返回部分
Artifact，也不创建 checkpoint、Evidence、receipt 或第二套运行状态。
"""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO, Final

from zniku.avenhance_v27.planner import (
    FinalSource,
    ProgramChapter,
    SplitSegment,
    parse_final_sources,
    parse_program_chapters,
    parse_split_segments,
)
from zniku.avenhance_v27.probe import (
    Av27MediaError,
    audio_signatures_from_metadata,
    canonical_fraction,
    metadata_frame_count,
    metadata_rate,
    namespace_from_media_info,
    parse_fraction,
    probe_header,
)
from zniku.media.probe import resolve_media_tool
from zniku.runtime import (
    FrameRange,
    ProducedOutput,
    ProgressError,
    ProgressInfrastructureError,
    PythonAdapterContext,
    PythonAdapterResult,
    RunnerInput,
)
from zniku.runtime.runner import OutputTarget

_MIB: Final = 1024 * 1024
_NVENC_FRAME_COLOR_FILTER: Final = (
    "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
)
_NVENC_BT709_LIMITED_BSF: Final = (
    "hevc_metadata=video_full_range_flag=0:colour_primaries=1:"
    "transfer_characteristics=1:matrix_coefficients=1:chroma_sample_loc_type=0"
)


@dataclass(frozen=True, slots=True)
class _ProgressContract:
    """把一个 FFmpeg producer 的机器 frame 计数映射到整节点进度。"""

    total: int
    offset: int = 0
    extent: int | None = None


def source_program(context: PythonAdapterContext) -> PythonAdapterResult:
    """登记同一 Source 的 video/source_media 两个只读 typed binding。"""

    if context.inputs:
        raise Av27MediaError("E_AV27_SOURCE_INPUT", "SourceProgram 不接受 input")
    source_value = _required_text(context.node.parameters, "source_path")
    source = Path(source_value)
    if not source.is_absolute():
        raise Av27MediaError("E_AV27_SOURCE_PATH", "source_path 必须是绝对路径")
    source = _nonempty_file(source)
    declared_outputs = {port.port_id for port in context.definition.output_ports}
    if declared_outputs != {"video", "source_media"}:
        raise Av27MediaError("E_AV27_SOURCE_OUTPUTS", "SourceProgram output shape 无效")
    _append_log(context.stdout_log_path, f"SourceProgram referenced {source}\n")
    return PythonAdapterResult(
        outputs=(
            ProducedOutput("video", source, allow_external=True),
            ProducedOutput("source_media", source, allow_external=True),
        ),
        media_summary={
            "mode": "readonly_reference",
            "source_name": source.name,
            "source_size": source.stat().st_size,
        },
    )


def source_admission(context: PythonAdapterContext) -> PythonAdapterResult:
    """从直接 Source Artifact metadata 生成普通、确定性的 admission gate JSON。"""

    sources = _ordered_inputs(context, "sources")
    mode = _required_enum(context.node.parameters, "source_mode", {"program", "pre_chaptered"})
    declared = _array_of_mappings(context.node.parameters.get("sources"), "sources")
    if len(declared) != len(sources):
        raise Av27MediaError("E_AV27_ADMISSION_SOURCE_COUNT", "声明与绑定 Source 数量不一致")
    if mode == "program" and len(sources) != 1:
        raise Av27MediaError("E_AV27_ADMISSION_PROGRAM_COUNT", "program 模式必须恰好一个 Source")

    summaries: list[dict[str, object]] = []
    common: tuple[object, object, object] | None = None
    first_audio: tuple[dict[str, object], ...] | None = None
    for index, (item, declaration) in enumerate(zip(sources, declared, strict=True)):
        if _strict_ordinal(declaration.get("source_ordinal"), "source_ordinal") != index:
            raise Av27MediaError("E_AV27_ADMISSION_SOURCE_ORDER", "source ordinal 必须从 0 连续")
        metadata = namespace_from_media_info(item.media_info)
        if _strict_ordinal(metadata.get("source_ordinal"), "metadata source_ordinal") != index:
            raise Av27MediaError("E_AV27_ADMISSION_SOURCE_ORDER", "Artifact source ordinal 不匹配")
        rate = metadata.get("frame_rate")
        geometry = metadata.get("geometry")
        signal = metadata.get("signal")
        signature = (rate, geometry, signal)
        if common is None:
            common = signature
        elif signature != common:
            raise Av27MediaError(
                "E_AV27_ADMISSION_MEDIA_MISMATCH",
                "Source FPS/geometry/signal 不一致",
            )
        audio = audio_signatures_from_metadata(item.media_info)
        if mode == "pre_chaptered":
            if first_audio is None:
                first_audio = audio
            elif audio != first_audio:
                raise Av27MediaError(
                    "E_AV27_ADMISSION_AUDIO_MISMATCH",
                    "pre_chaptered logical audio signatures 不一致",
                )
        summaries.append(
            {
                "source_ordinal": index,
                "artifact_id": item.artifact_id,
                "frame_count": metadata_frame_count(item.media_info),
                "frame_rate": canonical_fraction(metadata_rate(item.media_info)),
                "geometry": _plain_json(geometry),
                "signal": _plain_json(signal),
                "audio_tracks": list(audio),
            }
        )
    target = _single_output(context, "gate")
    payload = {
        "schema": "zniku.avenhance.v27.admission/1",
        "source_mode": mode,
        "sources": summaries,
    }
    try:
        target.path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as error:
        raise Av27MediaError("E_AV27_ADMISSION_WRITE", str(error)) from error
    return PythonAdapterResult(
        media_summary={"source_mode": mode, "source_count": len(sources)},
        validation_summary={"barrier": "resolved"},
    )


def atomic_split(context: PythonAdapterContext) -> PythonAdapterResult:
    """按物理 Source 各启动一个连续 segment producer，原子产生全部 FFV1 leaves。"""

    videos = _ordered_inputs(context, "videos")
    gate = _single_input(context, "gate")
    planned_gate = _required_text(context.node.parameters, "planned_admission_artifact_id")
    if gate.artifact_id != planned_gate:
        raise Av27MediaError("E_AV27_PLAN_INPUT_CHANGED", "Admission Artifact 已变化，必须重新规划")
    planned_videos = _array_of_text(
        context.node.parameters.get("planned_effective_video_artifact_ids"),
        "planned_effective_video_artifact_ids",
    )
    actual_videos = tuple(item.artifact_id for item in videos)
    if planned_videos != actual_videos:
        raise Av27MediaError(
            "E_AV27_PLAN_INPUT_CHANGED",
            "effective-video Artifact 已变化，必须重新规划",
        )
    output_ports = tuple(output.port_id for output in context.outputs)
    segments = parse_split_segments(
        context.node.parameters.get("segments"),
        expected_ports=output_ports,
    )
    grouped = _bind_split_plan(videos, segments)
    _preflight_split_capacity(context, videos)

    outputs = _outputs_by_port(context)
    produced: list[ProducedOutput] = []
    producer_metadata: dict[str, dict[str, object]] = {}
    total_frames = sum(metadata_frame_count(item.media_info) for item in videos)
    completed = 0
    try:
        for source, source_segments in grouped:
            source_frames = metadata_frame_count(source.media_info)
            stage_dir = context.work_dir / f"split-source-{source.input_ordinal:04d}"
            stage_dir.mkdir()
            segment_list = stage_dir / "segments.csv"
            pattern = stage_dir / "part-%06d.mkv"
            boundaries = ",".join(str(item.end_frame) for item in source_segments)
            filter_graph = _split_filter(source)
            measured = _run_ffmpeg(
                context,
                [
                    "-xerror",
                    "-fflags",
                    "+genpts",
                    "-i",
                    str(source.path),
                    "-filter_complex",
                    filter_graph,
                    "-map",
                    "[vsegment]",
                    "-an",
                    "-sn",
                    "-dn",
                    "-map_metadata",
                    "-1",
                    "-map_chapters",
                    "-1",
                    "-c:v",
                    "ffv1",
                    "-level:v",
                    "3",
                    "-coder:v",
                    "1",
                    "-context:v",
                    "1",
                    "-g:v",
                    "1",
                    "-slicecrc:v",
                    "1",
                    "-slices:v",
                    "16",
                    "-color_range:v",
                    "tv",
                    "-colorspace:v",
                    "bt709",
                    "-color_trc:v",
                    "bt709",
                    "-color_primaries:v",
                    "bt709",
                    "-chroma_sample_location:v",
                    "left",
                    "-field_order:v",
                    "progressive",
                    "-fps_mode",
                    "passthrough",
                    "-f",
                    "segment",
                    "-segment_format",
                    "matroska",
                    "-reference_stream",
                    "v:0",
                    "-segment_start_number",
                    "1",
                    "-reset_timestamps",
                    "1",
                    "-individual_header_trailer",
                    "1",
                    "-segment_list",
                    str(segment_list),
                    "-segment_list_type",
                    "csv",
                    "-segment_frames",
                    boundaries,
                    str(pattern),
                ],
                progress=_ProgressContract(total_frames, completed, source_frames),
            )
            if measured != source_frames:
                raise Av27MediaError(
                    "E_AV27_SPLIT_FRAME_COUNT",
                    f"Split producer frames={measured!r}，expected={source_frames}",
                )
            partials = _read_segment_list(segment_list, stage_dir, len(source_segments))
            for partial, segment in zip(partials, source_segments, strict=True):
                target = outputs[segment.port_id]
                _promote_attempt_file(partial, target.path, context.work_dir)
                produced.append(
                    ProducedOutput(
                        segment.port_id,
                        target.path,
                        frame_range=FrameRange(
                            start_frame=segment.start_frame,
                            end_frame=segment.end_frame,
                        ),
                    )
                )
                producer_metadata[segment.port_id] = {"output_frames": source_frames}
            completed += source_frames
    except BaseException:
        _cleanup_attempt_outputs(context)
        raise
    return PythonAdapterResult(
        outputs=tuple(produced),
        producer_metadata=producer_metadata,
        media_summary={"source_count": len(videos), "leaf_count": len(segments)},
        validation_summary={"single_producer_per_source": True},
    )


def merge_video(context: PythonAdapterContext) -> PythonAdapterResult:
    """用一个 video-only concat/remux producer 合并一个章节的有序 ProRes leaves。"""

    videos = _ordered_inputs(context, "videos")
    target = _single_output(context, "video")
    expected = _required_positive_int(context.node.parameters, "expected_frames")
    actual_input = sum(metadata_frame_count(item.media_info) for item in videos)
    if actual_input != expected:
        raise Av27MediaError("E_AV27_MERGE_INPUT_FRAMES", "Merge input 帧数与 chapter 计划不一致")
    concat = context.work_dir / "merge.ffconcat"
    _write_ffconcat(concat, ((item.path, None) for item in videos))
    try:
        measured = _run_ffmpeg(
            context,
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-c:v",
                "copy",
                "-fps_mode",
                "passthrough",
                "-f",
                "mov",
                str(target.path),
            ],
            progress=_ProgressContract(expected),
        )
    except BaseException:
        _cleanup_attempt_outputs(context)
        raise
    if measured is not None and measured != expected:
        raise Av27MediaError(
            "E_AV27_MERGE_FRAME_COUNT",
            f"Merge producer frames={measured!r}，expected={expected}",
        )
    # FFmpeg 对纯 stream-copy 在部分平台不输出 progress frame；成功 remux 的 N 由已验证输入和
    # concat 守恒确定。若 producer 实际报告 frame，则上面的精确比较仍然失败关闭。
    output_frames = expected
    return PythonAdapterResult(
        producer_metadata={"video": {"output_frames": output_frames}},
        media_summary={"input_count": len(videos)},
    )


def program_encode(context: PythonAdapterContext) -> PythonAdapterResult:
    """逐章补尾后用唯一连续 FFmpeg 产生 HEVC Main10 Program。"""

    chapters = parse_program_chapters(context.node.parameters.get("chapters"))
    inputs = _ordered_inputs(context, "chapters")
    if len(inputs) != len(chapters):
        raise Av27MediaError("E_AV27_PROGRAM_INPUT_COUNT", "Program chapter 数量不匹配")
    for item, plan in zip(inputs, chapters, strict=True):
        if item.input_ordinal != plan.chapter_ordinal:
            raise Av27MediaError("E_AV27_PROGRAM_INPUT_ORDER", "Program input ordinal 不连续")
        if metadata_frame_count(item.media_info) != plan.expected_fi_frames:
            raise Av27MediaError(
                "E_AV27_PROGRAM_INPUT_FRAMES",
                f"chapter {plan.chapter_id} 不满足 2N-1",
            )
    encoder = _required_enum(context.node.parameters, "encoder", {"cpu", "gpu"})
    rate = parse_fraction(context.node.parameters.get("source_fps")) * 2
    _probe_encoder_capability(context, encoder)
    target = _single_output(context, "video")
    filter_script = context.work_dir / "program-filter.txt"
    try:
        filter_script.write_text(
            _program_filter(chapters, rate, encoder),
            encoding="utf-8",
            newline="\n",
        )
    except OSError as error:
        raise Av27MediaError("E_AV27_PROGRAM_FILTER_WRITE", str(error)) from error
    argv: list[str] = []
    for item in inputs:
        argv.extend(["-i", str(item.path)])
    argv.extend(
        [
            "-filter_complex_script",
            str(filter_script),
            "-map",
            "[program_video]",
            "-an",
            "-sn",
            "-dn",
            "-c:v",
            "libx265" if encoder == "cpu" else "hevc_nvenc",
        ]
    )
    argv.extend(_encoder_options(encoder, rate))
    starts: list[int] = []
    cursor = 0
    for chapter in chapters:
        starts.append(cursor)
        cursor += chapter.encoded_frames
    force_keyframes = ",".join(_fraction_decimal(Fraction(frame, 1) / rate) for frame in starts)
    argv.extend(
        [
            "-force_key_frames",
            force_keyframes,
            "-fps_mode:v",
            "passthrough",
            "-enc_time_base:v",
            f"1/{rate.numerator}",
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
            "-tag:v",
            "hvc1",
            "-video_track_timescale",
            str(rate.numerator),
            "-f",
            "mp4",
            str(target.path),
        ]
    )
    try:
        measured = _run_ffmpeg(context, argv, progress=_ProgressContract(cursor))
    except BaseException:
        _cleanup_attempt_outputs(context)
        raise
    if measured != cursor:
        raise Av27MediaError(
            "E_AV27_PROGRAM_FRAME_COUNT",
            f"Program producer frames={measured!r}，expected={cursor}",
        )
    return PythonAdapterResult(
        producer_metadata={"video": {"output_frames": measured}},
        media_summary={
            "encoder": encoder,
            "chapter_count": len(chapters),
            "frame_rate": canonical_fraction(rate),
        },
        validation_summary={"single_program_producer": True},
    )


def final_mux(context: PythonAdapterContext) -> PythonAdapterResult:
    """把 Program video 与 ordered SourceProgram 原始音轨 stream-copy 为 Final。"""

    program = _single_input(context, "video")
    sources = _ordered_inputs(context, "sources")
    _single_input(context, "gate")
    source_plans = parse_final_sources(context.node.parameters.get("sources"))
    mode = _required_enum(context.node.parameters, "source_mode", {"program", "pre_chaptered"})
    expected = _required_positive_int(context.node.parameters, "expected_program_frames")
    if metadata_frame_count(program.media_info) != expected:
        raise Av27MediaError("E_AV27_FINAL_PROGRAM_FRAMES", "Program Artifact N 已变化")
    if len(sources) != len(source_plans):
        raise Av27MediaError("E_AV27_FINAL_SOURCE_COUNT", "Final Source 数量不匹配")
    if mode == "program" and len(sources) != 1:
        raise Av27MediaError("E_AV27_FINAL_PROGRAM_SOURCE_COUNT", "program 模式只允许一个 Source")
    for item, plan in zip(sources, source_plans, strict=True):
        if item.input_ordinal != plan.source_ordinal:
            raise Av27MediaError("E_AV27_FINAL_SOURCE_ORDER", "Final Source ordinal 不连续")
        namespace = namespace_from_media_info(item.media_info)
        if (
            _strict_ordinal(namespace.get("source_ordinal"), "metadata source_ordinal")
            != plan.source_ordinal
            or metadata_frame_count(item.media_info) != plan.source_frames
            or metadata_rate(item.media_info) != plan.source_fps
        ):
            raise Av27MediaError(
                "E_AV27_FINAL_SOURCE_CHANGED",
                "Source metadata ordinal/N/FPS 与 admitted plan 不一致",
            )

    expected_audio = audio_signatures_from_metadata(sources[0].media_info)
    for item in sources[1:]:
        if audio_signatures_from_metadata(item.media_info) != expected_audio:
            raise Av27MediaError("E_AV27_FINAL_AUDIO_MISMATCH", "Source audio signatures 不一致")
    target = _single_output(context, "media")
    try:
        measured = _execute_final_mux(
            context,
            program,
            sources,
            source_plans,
            expected_audio,
            mode=mode,
            target=target.path,
            expected_frames=expected,
        )
    except BaseException:
        _cleanup_attempt_outputs(context)
        raise
    if measured is not None and measured != expected:
        raise Av27MediaError(
            "E_AV27_FINAL_FRAME_COUNT",
            f"Final producer frames={measured!r}，expected={expected}",
        )
    # Final 只 stream-copy 已验证的 Program video；FFmpeg 某些构建不为 copy 输出 progress frame。
    # 成功退出后沿用 Program exact N，validator 仍独立闭合 header、duration、signal 与音轨。
    output_frames = expected
    return PythonAdapterResult(
        producer_metadata={"media": {"output_frames": output_frames}},
        media_summary={"source_mode": mode, "audio_stream_count": len(expected_audio)},
    )


def _execute_final_mux(
    context: PythonAdapterContext,
    program: RunnerInput,
    sources: tuple[RunnerInput, ...],
    plans: tuple[FinalSource, ...],
    expected_audio: tuple[dict[str, object], ...],
    *,
    mode: str,
    target: Path,
    expected_frames: int,
) -> int | None:
    argv = ["-i", str(program.path)]
    audio_input = False
    if mode == "program":
        argv.extend(["-i", str(sources[0].path)])
        audio_input = True
    elif expected_audio:
        staged: list[tuple[Path, Fraction | None]] = []
        for item, plan in zip(sources, plans, strict=True):
            stage = context.work_dir / f"audio-{plan.source_ordinal:04d}.mka"
            _run_ffmpeg(
                context,
                [
                    "-i",
                    str(item.path),
                    "-map",
                    "0:a",
                    "-map_metadata",
                    "0",
                    *_audio_metadata_options(expected_audio),
                    "-c:a",
                    "copy",
                    "-map_chapters",
                    "-1",
                    "-avoid_negative_ts",
                    "make_zero",
                    "-f",
                    "matroska",
                    str(stage),
                ],
            )
            staged_header = probe_header(stage)
            staged_formats = {
                item.strip().casefold()
                for item in staged_header.format_name.split(",")
                if item.strip()
            }
            if (
                "matroska" not in staged_formats
                or staged_header.videos
                or staged_header.others
                or any(kind != "audio" for kind in staged_header.streams)
                or staged_header.chapter_count
            ):
                raise Av27MediaError(
                    "E_AV27_FINAL_STAGING_STREAMS",
                    "audio staging 必须为无 chapter 的 audio-only Matroska",
                )
            if tuple(audio.signature() for audio in staged_header.audios) != expected_audio:
                raise Av27MediaError(
                    "E_AV27_FINAL_STAGING_AUDIO",
                    "audio staging 改变 header signature",
                )
            staged.append((stage, plan.duration))
        concat = context.work_dir / "audio.ffconcat"
        _write_ffconcat(concat, staged)
        argv.extend(["-f", "concat", "-safe", "0", "-i", str(concat)])
        audio_input = True
    argv.extend(["-map", "0:v:0"])
    if audio_input:
        argv.extend(["-map", "1:a?", "-map_metadata", "1"])
    else:
        argv.extend(["-map_metadata", "-1"])
    argv.extend(
        [
            "-map_chapters",
            "1" if mode == "program" else "-1",
            *_audio_metadata_options(expected_audio),
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-avoid_negative_ts",
            "disabled",
            "-f",
            "matroska",
            str(target),
        ]
    )
    return _run_ffmpeg(context, argv, progress=_ProgressContract(expected_frames))


def _bind_split_plan(
    videos: tuple[RunnerInput, ...],
    segments: tuple[SplitSegment, ...],
) -> tuple[tuple[RunnerInput, tuple[SplitSegment, ...]], ...]:
    grouped: dict[int, list[SplitSegment]] = defaultdict(list)
    for segment in segments:
        grouped[segment.source_ordinal].append(segment)
    if len(grouped) != len(videos):
        raise Av27MediaError("E_AV27_SPLIT_SOURCE_COUNT", "Split plan 与 video inputs 数量不一致")
    result: list[tuple[RunnerInput, tuple[SplitSegment, ...]]] = []
    for index, source in enumerate(videos):
        if source.input_ordinal != index:
            raise Av27MediaError("E_AV27_SPLIT_SOURCE_ORDER", "videos ordinal 必须从 0 连续")
        planned = tuple(grouped.get(index, ()))
        if not planned or any(
            item.planned_effective_video_artifact_id != source.artifact_id for item in planned
        ):
            raise Av27MediaError("E_AV27_PLAN_INPUT_CHANGED", "effective video Artifact 已变化")
        count = metadata_frame_count(source.media_info)
        if planned[0].start_frame != 0 or planned[-1].end_frame != count:
            raise Av27MediaError("E_AV27_SPLIT_COVERAGE", "逐 Source ranges 未完整覆盖 input N")
        result.append((source, planned))
    return tuple(result)


def _preflight_split_capacity(
    context: PythonAdapterContext,
    videos: tuple[RunnerInput, ...],
) -> None:
    duration = Fraction(0, 1)
    for item in videos:
        raw = namespace_from_media_info(item.media_info).get("duration_seconds")
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not float(raw) > 0:
            raise Av27MediaError("E_AV27_SPLIT_DURATION", "effective video duration 缺失或无效")
        duration += Fraction(str(raw))
    required = (max(Fraction(1, 1), duration) * (32 * _MIB)).__ceil__() + 512 * _MIB
    try:
        free = shutil.disk_usage(context.outputs[0].path.parent).free
    except OSError as error:
        raise Av27MediaError("E_AV27_SPLIT_CAPACITY", str(error)) from error
    if free < required:
        raise Av27MediaError(
            "E_AV27_SPLIT_CAPACITY",
            f"attempt volume free={free} 小于 required={required}",
        )


def _split_filter(source: RunnerInput) -> str:
    metadata = namespace_from_media_info(source.media_info)
    geometry = metadata.get("geometry")
    if not isinstance(geometry, Mapping):
        raise Av27MediaError("E_AV27_SPLIT_GEOMETRY", "input geometry metadata 缺失")
    width = geometry.get("width")
    height = geometry.get("height")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or width * 9 != height * 16
    ):
        raise Av27MediaError("E_AV27_SPLIT_GEOMETRY", "input coded geometry 必须精确 16:9")
    sar = metadata.get("sample_aspect_ratio")
    if sar not in {None, "1:1", "1/1"}:
        raise Av27MediaError("E_AV27_SPLIT_SAR", "input 必须为 square SAR")
    scale = ""
    if (width, height) != (1920, 1080):
        scale = "zscale=w=1920:h=1080:filter=spline36:chromalin=left:chromal=left,"
    rate = metadata_rate(source.media_info)
    return (
        "[0:v:0]setparams=range=limited:color_primaries=bt709:"
        "color_trc=bt709:colorspace=bt709,"
        f"{scale}format=pix_fmts=yuv420p10le,setsar=1/1,"
        f"settb=expr={rate.denominator}/{rate.numerator},setpts=N[vsegment]"
    )


def _program_filter(
    chapters: tuple[ProgramChapter, ...],
    rate: Fraction,
    encoder: str,
) -> str:
    branches: list[str] = []
    labels: list[str] = []
    for index, _chapter in enumerate(chapters):
        label = f"chapter_{index}"
        labels.append(f"[{label}]")
        branches.append(
            f"[{index}:v:0]tpad=stop_mode=clone:stop=1,"
            f"settb=expr=1/{rate.numerator},setpts=N*{rate.denominator}[{label}]"
        )
    pixel_format = "yuv420p10le" if encoder == "cpu" else "p010le"
    scale = (
        "scale=iw:ih:flags=spline+accurate_rnd+full_chroma_int:"
        "in_range=tv:out_range=tv:in_color_matrix=bt709:out_color_matrix=bt709"
    )
    tail = (
        "".join(labels)
        + f"concat=n={len(chapters)}:v=1:a=0,settb=expr=1/{rate.numerator},"
        + f"setpts=N*{rate.denominator},{scale},format={pixel_format}"
    )
    if encoder == "gpu":
        tail += f",{_NVENC_FRAME_COLOR_FILTER}"
    tail += "[program_video]"
    branches.append(tail)
    return ";\n".join(branches) + "\n"


def _encoder_options(encoder: str, rate: Fraction) -> list[str]:
    if encoder == "cpu":
        return [
            "-preset",
            "slow",
            "-crf",
            "16",
            "-profile:v",
            "main10",
            "-pix_fmt",
            "yuv420p10le",
            "-forced-idr",
            "1",
            "-x265-params",
            (
                "open-gop=0:keyint=240:min-keyint=24:scenecut=40:"
                f"fps={rate.numerator}/{rate.denominator}:repeat-headers=0:"
                "colorprim=bt709:transfer=bt709:colormatrix=bt709:"
                "range=limited:chromaloc=0"
            ),
        ]
    return [
        "-preset",
        "p7",
        "-tune",
        "uhq",
        "-profile:v",
        "main10",
        "-pix_fmt",
        "p010le",
        "-rc",
        "vbr",
        "-cq",
        "16",
        "-b:v",
        "0",
        "-maxrate",
        "80M",
        "-bufsize",
        "320M",
        "-multipass",
        "fullres",
        "-bf",
        "4",
        "-b_ref_mode",
        "middle",
        "-g",
        "240",
        "-forced-idr",
        "1",
        "-spatial-aq",
        "1",
        "-temporal-aq",
        "1",
        "-aq-strength",
        "8",
        "-lookahead_level",
        "15",
        "-bsf:v",
        _NVENC_BT709_LIMITED_BSF,
    ]


def _probe_encoder_capability(context: PythonAdapterContext, encoder: str) -> None:
    executable = resolve_media_tool("ffmpeg")
    profile = "libx265" if encoder == "cpu" else "hevc_nvenc"
    argv = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:size=256x256:rate=1:duration=1",
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-pix_fmt",
        "yuv420p10le" if encoder == "cpu" else "p010le",
        "-c:v",
        profile,
    ]
    if encoder == "cpu":
        argv.extend(["-preset", "slow", "-crf", "16", "-profile:v", "main10"])
    else:
        argv.extend(
            [
                "-preset",
                "p7",
                "-tune",
                "uhq",
                "-profile:v",
                "main10",
                "-rc",
                "vbr",
                "-cq",
                "16",
                "-b:v",
                "0",
            ]
        )
    argv.extend(["-f", "null", "-"])
    try:
        with context.stderr_log_path.open("ab") as stderr:
            completed = subprocess.run(
                argv,
                cwd=context.work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                shell=False,
                check=False,
            )
    except OSError as error:
        raise Av27MediaError("E_AV27_PROGRAM_CAPABILITY", str(error)) from error
    if completed.returncode != 0:
        raise Av27MediaError(
            "E_AV27_PROGRAM_CAPABILITY",
            f"{encoder} Main10 capability probe 失败；不会 fallback",
        )


def _run_ffmpeg(
    context: PythonAdapterContext,
    argv: Sequence[str],
    *,
    progress: _ProgressContract | None = None,
) -> int | None:
    """执行一个受控 producer，返回最后机器可读正 ``frame``；不从 stderr 猜进度。"""

    executable = resolve_media_tool("ffmpeg")
    command = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-n",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        *argv,
    ]
    process: subprocess.Popen[bytes] | None = None
    last_frame: int | None = None
    try:
        with (
            context.stdout_log_path.open("ab") as stdout_log,
            context.stderr_log_path.open("ab") as stderr_log,
        ):
            process = subprocess.Popen(
                command,
                cwd=context.work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr_log,
                shell=False,
            )
            if process.stdout is None:
                raise Av27MediaError("E_AV27_FFMPEG_PROGRESS", "FFmpeg progress pipe 未建立")
            fields: dict[bytes, bytes] = {}
            with process.stdout:
                for line in process.stdout:
                    stdout_log.write(line)
                    stdout_log.flush()
                    key, separator, value = line.rstrip(b"\r\n").partition(b"=")
                    if not separator:
                        continue
                    if key == b"progress":
                        raw = fields.get(b"frame")
                        if raw is not None:
                            normalized = raw.strip()
                            if not normalized.isdigit():
                                raise Av27MediaError("E_AV27_FFMPEG_PROGRESS", "frame 不是 integer")
                            measured = int(normalized)
                            if measured > 0:
                                last_frame = measured
                                _report_progress(context, progress, measured)
                        fields.clear()
                    elif key in {b"frame", b"out_time_us", b"total_size"}:
                        if key in fields:
                            raise Av27MediaError("E_AV27_FFMPEG_PROGRESS", "progress 字段重复")
                        fields[key] = value
            return_code = process.wait()
    except (ProgressError, ProgressInfrastructureError):
        if process is not None:
            _terminate_process(process)
        raise
    except BaseException:
        if process is not None:
            _terminate_process(process)
        raise
    if return_code != 0:
        raise Av27MediaError("E_AV27_FFMPEG_FAILED", f"FFmpeg 退出码 {return_code}")
    return last_frame


def _report_progress(
    context: PythonAdapterContext,
    contract: _ProgressContract | None,
    measured: int,
) -> None:
    if contract is None or context.progress is None:
        return
    extent = contract.extent if contract.extent is not None else contract.total
    if measured > extent:
        raise Av27MediaError("E_AV27_FFMPEG_PROGRESS", "producer frame 超出 denominator")
    current = contract.offset + measured
    if current > contract.total:
        raise Av27MediaError("E_AV27_FFMPEG_PROGRESS", "node progress 超出 denominator")
    context.progress.report(
        fraction=current / contract.total,
        current=current,
        total=contract.total,
        unit="frames",
    )


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    """确认 producer 终止；无法回收时失败关闭，避免误登记仍在写的输出。"""

    if process.poll() is not None:
        return
    with suppress(OSError):
        process.terminate()
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        with suppress(OSError):
            process.kill()
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Av27MediaError(
                "E_AV27_FFMPEG_CLEANUP",
                "FFmpeg producer 无法确认回收",
            ) from error


def _read_segment_list(path: Path, stage_dir: Path, count: int) -> tuple[Path, ...]:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream))
    except OSError as error:
        raise Av27MediaError("E_AV27_SPLIT_SEGMENT_LIST", str(error)) from error
    if len(rows) != count or any(len(row) != 3 for row in rows):
        raise Av27MediaError("E_AV27_SPLIT_SEGMENT_LIST", "segment list shape 无效")
    result: list[Path] = []
    for index, row in enumerate(rows, start=1):
        expected = f"part-{index:06d}.mkv"
        candidate = Path(row[0])
        if candidate.name != expected:
            raise Av27MediaError("E_AV27_SPLIT_SEGMENT_LIST", "segment filename/order 无效")
        try:
            start_time = float(row[1])
            end_time = float(row[2])
        except ValueError as error:
            raise Av27MediaError(
                "E_AV27_SPLIT_SEGMENT_LIST",
                "segment start/end 必须为有限数值",
            ) from error
        if not math.isfinite(start_time) or not math.isfinite(end_time) or end_time < start_time:
            raise Av27MediaError(
                "E_AV27_SPLIT_SEGMENT_LIST",
                "segment start/end 范围无效",
            )
        resolved = (stage_dir / candidate.name).resolve(strict=True)
        resolved.relative_to(stage_dir.resolve(strict=True))
        if not resolved.is_file() or resolved.stat().st_size <= 0:
            raise Av27MediaError("E_AV27_SPLIT_OUTPUT_MISSING", f"缺少 {expected}")
        result.append(resolved)
    return tuple(result)


def _promote_attempt_file(source: Path, target: Path, work_dir: Path) -> None:
    try:
        resolved_source = source.resolve(strict=True)
        resolved_target = target.resolve(strict=False)
        root = work_dir.resolve(strict=True)
        resolved_source.relative_to(root)
        resolved_target.relative_to(root)
    except (OSError, ValueError) as error:
        raise Av27MediaError("E_AV27_PATH_ESCAPE", "Split promotion 逃逸 attempt") from error
    if resolved_target.exists():
        raise Av27MediaError("E_AV27_OUTPUT_EXISTS", f"输出已存在：{resolved_target}")
    try:
        os.replace(resolved_source, resolved_target)
    except OSError as error:
        raise Av27MediaError("E_AV27_SPLIT_PROMOTE", str(error)) from error


def _write_ffconcat(
    path: Path,
    entries: Iterable[tuple[Path, Fraction | None]],
) -> None:
    materialized: tuple[tuple[Path, Fraction | None], ...] = tuple(entries)
    lines = ["ffconcat version 1.0"]
    for source, duration in materialized:
        lines.append(f"file '{_ffconcat_quote(source)}'")
        if duration is not None:
            lines.append(f"duration {_fraction_decimal(duration)}")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    except OSError as error:
        raise Av27MediaError("E_AV27_FFCONCAT_WRITE", str(error)) from error


def _ffconcat_quote(path: Path) -> str:
    return str(path.resolve(strict=True)).replace("\\", "/").replace("'", "'\\''")


def _fraction_decimal(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 60
        return format(Decimal(value.numerator) / Decimal(value.denominator), ".12f")


def _audio_metadata_options(signatures: tuple[dict[str, object], ...]) -> list[str]:
    options: list[str] = []
    for ordinal, signature in enumerate(signatures):
        for tag in ("language", "title"):
            value = signature.get(tag)
            if isinstance(value, str) and value:
                options.extend([f"-metadata:s:a:{ordinal}", f"{tag}={value}"])
        dispositions: list[str] = []
        if signature.get("default") is True:
            dispositions.append("default")
        if signature.get("forced") is True:
            dispositions.append("forced")
        options.extend(
            [f"-disposition:a:{ordinal}", "+".join(dispositions) if dispositions else "0"]
        )
    return options


def _single_input(context: PythonAdapterContext, port_id: str) -> RunnerInput:
    values = tuple(item for item in context.inputs if item.port_id == port_id)
    if len(values) != 1:
        raise Av27MediaError("E_AV27_INPUT_COUNT", f"{port_id} 必须精确绑定一个 input")
    return values[0]


def _ordered_inputs(context: PythonAdapterContext, port_id: str) -> tuple[RunnerInput, ...]:
    values = tuple(item for item in context.inputs if item.port_id == port_id)
    if not values or any(item.input_ordinal != index for index, item in enumerate(values)):
        raise Av27MediaError("E_AV27_INPUT_ORDER", f"{port_id} 必须从 ordinal 0 连续绑定")
    return values


def _single_output(context: PythonAdapterContext, port_id: str) -> OutputTarget:
    values = tuple(item for item in context.outputs if item.port_id == port_id)
    if len(values) != 1:
        raise Av27MediaError("E_AV27_OUTPUT_COUNT", f"{port_id} 必须精确声明一个 output")
    return values[0]


def _outputs_by_port(context: PythonAdapterContext) -> dict[str, OutputTarget]:
    result = {item.port_id: item for item in context.outputs}
    if len(result) != len(context.outputs):
        raise Av27MediaError("E_AV27_OUTPUT_DUPLICATE", "output port 重复")
    return result


def _required_text(parameters: Mapping[str, object], name: str) -> str:
    value = parameters.get(name)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} 必须是非空 string")
    return value


def _required_enum(parameters: Mapping[str, object], name: str, allowed: set[str]) -> str:
    value = _required_text(parameters, name)
    if value not in allowed:
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} enum 无效")
    return value


def _required_positive_int(parameters: Mapping[str, object], name: str) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} 必须是正 integer")
    return value


def _strict_ordinal(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} 必须是非负 integer")
    return value


def _array_of_mappings(value: object, name: str) -> tuple[Mapping[str, object], ...]:
    if (
        not isinstance(value, list | tuple)
        or not value
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} 必须是非空 object array")
    return tuple(item for item in value if isinstance(item, Mapping))


def _array_of_text(value: object, name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list | tuple)
        or not value
        or any(not isinstance(item, str) or not item or item.strip() != item for item in value)
    ):
        raise Av27MediaError("E_AV27_PARAMETER", f"{name} 必须是非空 string array")
    return tuple(item for item in value if isinstance(item, str))


def _plain_json(value: object) -> object:
    """把 Runner 的递归只读 JSON 视图复制为 adapter 可序列化的普通值。"""

    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise Av27MediaError("E_AV27_METADATA_INVALID", "media metadata key 必须是 string")
            result[key] = _plain_json(item)
        return result
    if isinstance(value, list | tuple):
        return [_plain_json(item) for item in value]
    return value


def _cleanup_attempt_outputs(context: PythonAdapterContext) -> None:
    """只清理 Runner 已验证的 attempt output；外部 Source/上游 Artifact 永不触及。"""

    root = context.work_dir.resolve(strict=True)
    for output in context.outputs:
        with suppress(OSError, ValueError):
            resolved = output.path.resolve(strict=False)
            resolved.relative_to(root)
            if resolved.is_file() or resolved.is_symlink():
                resolved.unlink()


def _append_log(path: Path, message: str) -> None:
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(message)
    except OSError as error:
        raise Av27MediaError("E_AV27_LOG_WRITE", str(error)) from error


def _nonempty_file(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
    except OSError as error:
        raise Av27MediaError("E_AV27_INPUT_UNREADABLE", str(error)) from error
    if not resolved.is_file() or stat.st_size <= 0:
        raise Av27MediaError("E_AV27_INPUT_UNREADABLE", f"不是非空常规文件：{path}")
    return resolved


def _copy_stream(source: BinaryIO, target: BinaryIO) -> None:
    """预留给受控普通文件复制；发现 short write 时失败关闭。"""

    while chunk := source.read(_MIB):
        if target.write(chunk) != len(chunk):
            raise Av27MediaError("E_AV27_WRITE_INCOMPLETE", "输出发生 short write")
