"""实现 Phase 4 媒体节点自己的轻量 validators。

validator 只判定当前 attempt 的声明输出是否满足节点局部合同。默认 VideoTransform 不做 digest、full
decode、packet scan 或输入输出 exact；只有参数明确要求，或 Split/Merge 的帧守恒业务合同需要时，才
执行精确帧计数。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from fractions import Fraction
from pathlib import Path

from zniku.media.adapters import parse_segments
from zniku.media.probe import MediaNodeError, MediaProbeInfo, probe_media, require_media_kind
from zniku.runtime import FrameRange, NodeValidatorContext, NodeValidatorResult
from zniku.runtime.runner import ValidatedOutput


def validate_video_transform(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证 Transform 的可选 resolution/FPS/frame relation 约束。"""

    return _validated(lambda: _validate_video_transform(context))


def validate_split_video(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证每段命名顺序、half-open 长度与全部输出帧数守恒。"""

    return _validated(lambda: _validate_split_video(context))


def validate_merge_video(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证有序输入可 probe 且输出帧数等于输入总和。"""

    return _validated(lambda: _validate_merge_video(context))


def validate_encode_video(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证编码器声明与可选帧数不变约束。"""

    return _validated(lambda: _validate_encode_video(context))


def validate_mux_media(context: NodeValidatorContext) -> NodeValidatorResult:
    """验证 Mux 输出包含视频及与输入数量一致的有序音轨。"""

    return _validated(lambda: _validate_mux_media(context))


def transform_frame_relation(parameters: Mapping[str, object], type_id: str) -> str:
    """解析 Transform 的既有帧关系，供正式 validator 与只读交付说明共同使用。"""

    value = parameters.get("frame_relation")
    if value is None:
        return "double" if type_id.endswith(".fi.external") else "any"
    if isinstance(value, str) and value in {"any", "equal", "double"}:
        return value
    raise MediaNodeError("E_MEDIA_TRANSFORM_FRAME_RELATION", "frame_relation 无效")


def _validate_video_transform(context: NodeValidatorContext) -> Mapping[str, object]:
    output = _single_output(context, "video")
    output_info = probe_media(output.path)
    require_media_kind(output_info, "VideoFile")
    output_video = output_info.video_streams[0]
    parameters = context.request.node.parameters

    expected_width = _optional_integer(parameters, "expected_width")
    expected_height = _optional_integer(parameters, "expected_height")
    expected_rate = _optional_fraction(parameters, "expected_frame_rate")
    operation = parameters.get("operation")
    if operation == "scale":
        expected_width = _required_integer(parameters, "width")
        expected_height = _required_integer(parameters, "height")
    elif operation == "frame_rate":
        expected_rate = _required_fraction(parameters, "frame_rate")
    elif operation not in (None, "identity"):
        raise MediaNodeError("E_MEDIA_TRANSFORM_OPERATION_UNKNOWN", "operation 无效")

    if expected_width is not None and output_video.width != expected_width:
        raise MediaNodeError("E_MEDIA_TRANSFORM_WIDTH", "输出 width 不符合节点约束")
    if expected_height is not None and output_video.height != expected_height:
        raise MediaNodeError("E_MEDIA_TRANSFORM_HEIGHT", "输出 height 不符合节点约束")
    if expected_rate is not None and output_video.frame_rate != expected_rate:
        raise MediaNodeError("E_MEDIA_TRANSFORM_FPS", "输出 frame rate 不符合节点约束")

    relation = transform_frame_relation(parameters, context.request.definition.type_id)
    relation_summary: dict[str, object] = {"relation": relation}
    if relation != "any":
        source = _single_input_path(context, "video")
        input_info = probe_media(source, count_frames=True)
        counted_output = probe_media(output.path, count_frames=True)
        input_count = _first_frame_count(input_info)
        output_count = _first_frame_count(counted_output)
        expected_count = input_count if relation == "equal" else input_count * 2
        if output_count != expected_count:
            raise MediaNodeError(
                "E_MEDIA_TRANSFORM_FRAME_RELATION",
                f"输出帧数 {output_count} 不满足 {relation} 关系",
            )
        relation_summary.update(input_frame_count=input_count, output_frame_count=output_count)
    return {
        "video": output_video.to_summary(),
        "constraints": relation_summary,
    }


def _validate_split_video(context: NodeValidatorContext) -> Mapping[str, object]:
    source = _single_input_path(context, "video")
    input_count = _first_frame_count(probe_media(source, count_frames=True))
    segments = parse_segments(
        context.request.node.parameters.get("segments"),
        expected_ports=tuple(output.port_id for output in context.outputs),
        input_frames=input_count,
    )
    actual: dict[str, int] = {}
    outputs = {output.port_id: output for output in context.outputs}
    for segment in segments:
        output = outputs[segment.port_id]
        expected_range = FrameRange(
            start_frame=segment.start_frame,
            end_frame=segment.end_frame,
        )
        if output.frame_range != expected_range:
            raise MediaNodeError(
                "E_MEDIA_SPLIT_FRAME_RANGE",
                f"{segment.port_id} Artifact frame_range 与 segment 不一致",
            )
        count = _first_frame_count(probe_media(output.path, count_frames=True))
        if count != segment.frame_count:
            raise MediaNodeError(
                "E_MEDIA_SPLIT_OUTPUT_COUNT",
                f"{segment.port_id} 实际帧数与 frame_range 不一致",
            )
        actual[segment.port_id] = count
    if sum(actual.values()) != input_count:
        raise MediaNodeError("E_MEDIA_SPLIT_CONSERVATION", "Split 总帧数不守恒")
    return {
        "input_frame_count": input_count,
        "output_frame_counts": actual,
        "frame_count_conserved": True,
    }


def _validate_merge_video(context: NodeValidatorContext) -> Mapping[str, object]:
    sources = tuple(item.path for item in context.request.inputs if item.port_id == "videos")
    if not sources:
        raise MediaNodeError("E_MEDIA_MERGE_INPUT_EMPTY", "MergeVideo 输入为空")
    input_counts = [_first_frame_count(probe_media(path, count_frames=True)) for path in sources]
    output = _single_output(context, "video")
    output_count = _first_frame_count(probe_media(output.path, count_frames=True))
    if output_count != sum(input_counts):
        raise MediaNodeError("E_MEDIA_MERGE_CONSERVATION", "Merge 总帧数不守恒")
    return {
        "input_frame_counts": input_counts,
        "output_frame_count": output_count,
        "frame_count_conserved": True,
    }


def _validate_encode_video(context: NodeValidatorContext) -> Mapping[str, object]:
    output = _single_output(context, "video")
    info = probe_media(output.path)
    require_media_kind(info, "VideoFile")
    expected_codec = context.request.node.parameters.get("codec", "libx264")
    codec_names = {"libx264": "h264", "libx265": "hevc", "ffv1": "ffv1"}
    if not isinstance(expected_codec, str) or expected_codec not in codec_names:
        raise MediaNodeError("E_MEDIA_ENCODE_CODEC_UNKNOWN", "codec 无效")
    actual_codec = info.video_streams[0].codec
    if actual_codec != codec_names[expected_codec]:
        raise MediaNodeError("E_MEDIA_ENCODE_CODEC", "输出 codec 与节点声明不一致")
    expected_pixel_format = context.request.node.parameters.get("pixel_format", "yuv420p")
    if not isinstance(expected_pixel_format, str) or expected_pixel_format not in {
        "yuv420p",
        "yuv420p10le",
        "yuv422p10le",
    }:
        raise MediaNodeError("E_MEDIA_ENCODE_PIXEL_FORMAT_UNKNOWN", "pixel_format 无效")
    actual_pixel_format = info.video_streams[0].pixel_format
    if actual_pixel_format != expected_pixel_format:
        raise MediaNodeError(
            "E_MEDIA_ENCODE_PIXEL_FORMAT",
            "输出 pixel_format 与节点声明不一致",
        )
    summary: dict[str, object] = {
        "codec": actual_codec,
        "pixel_format": actual_pixel_format,
    }
    require_equal = context.request.node.parameters.get("require_frame_count_equal", False)
    if type(require_equal) is not bool:
        raise MediaNodeError(
            "E_MEDIA_ENCODE_FRAME_POLICY", "require_frame_count_equal 必须是 boolean"
        )
    if require_equal:
        input_count = _first_frame_count(
            probe_media(_single_input_path(context, "video"), count_frames=True)
        )
        output_count = _first_frame_count(probe_media(output.path, count_frames=True))
        if output_count != input_count:
            raise MediaNodeError("E_MEDIA_ENCODE_FRAME_COUNT", "编码输出帧数发生变化")
        summary.update(input_frame_count=input_count, output_frame_count=output_count)
    return summary


def _validate_mux_media(context: NodeValidatorContext) -> Mapping[str, object]:
    output = _single_output(context, "media")
    info = probe_media(output.path)
    require_media_kind(info, "MediaFile")
    if not info.video_streams:
        raise MediaNodeError("E_MEDIA_MUX_VIDEO_MISSING", "Mux 输出缺少视频流")
    audio_inputs = tuple(item for item in context.request.inputs if item.port_id == "audio")
    for item in audio_inputs:
        require_media_kind(probe_media(item.path), "AudioFile")
    if len(info.audio_streams) != len(audio_inputs):
        raise MediaNodeError("E_MEDIA_MUX_AUDIO_COUNT", "Mux 输出音轨数与有序输入不一致")
    return {
        "video_stream_count": len(info.video_streams),
        "audio_stream_count": len(info.audio_streams),
    }


def _validated(action: Callable[[], Mapping[str, object]]) -> NodeValidatorResult:
    try:
        return NodeValidatorResult(passed=True, summary=action())
    except MediaNodeError as error:
        return NodeValidatorResult(
            passed=False,
            summary={"code": error.code},
            message=str(error),
        )


def _single_output(context: NodeValidatorContext, port_id: str) -> ValidatedOutput:
    values = tuple(output for output in context.outputs if output.port_id == port_id)
    if len(values) != 1:
        raise MediaNodeError("E_MEDIA_OUTPUT_COUNT", f"{port_id} 输出数量必须为 1")
    return values[0]


def _single_input_path(context: NodeValidatorContext, port_id: str) -> Path:
    values = tuple(item.path for item in context.request.inputs if item.port_id == port_id)
    if len(values) != 1:
        raise MediaNodeError("E_MEDIA_INPUT_COUNT", f"{port_id} 输入数量必须为 1")
    return values[0]


def _first_frame_count(info: MediaProbeInfo) -> int:
    if not info.video_streams or info.video_streams[0].frame_count is None:
        raise MediaNodeError("E_MEDIA_FRAME_COUNT_UNKNOWN", "无法取得精确视频帧数")
    return info.video_streams[0].frame_count


def _required_integer(parameters: Mapping[str, object], name: str) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MediaNodeError("E_MEDIA_PARAMETER_INVALID", f"{name} 必须是正 integer")
    return value


def _optional_integer(parameters: Mapping[str, object], name: str) -> int | None:
    if name not in parameters:
        return None
    return _required_integer(parameters, name)


def _required_fraction(parameters: Mapping[str, object], name: str) -> Fraction:
    value = parameters.get(name)
    if not isinstance(value, str):
        raise MediaNodeError("E_MEDIA_PARAMETER_INVALID", f"{name} 必须是 string")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise MediaNodeError("E_MEDIA_PARAMETER_INVALID", f"{name} 不是合法帧率") from error
    if result <= 0:
        raise MediaNodeError("E_MEDIA_PARAMETER_INVALID", f"{name} 必须为正数")
    return result


def _optional_fraction(parameters: Mapping[str, object], name: str) -> Fraction | None:
    if name not in parameters:
        return None
    return _required_fraction(parameters, name)
