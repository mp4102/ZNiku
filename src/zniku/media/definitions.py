"""声明 ZNIKU 0.2.0 Phase 4 首批媒体 NodeDefinition 与 preset。

本模块是 Python 媒体节点目录的唯一字段权威。Studio 可以投影这些定义，但不得另写一份端口或参数
合同。SourceMedia 与 OutputFile 以 typed preset 表达 ``VideoFile``、``AudioFile`` 和 ``MediaFile``；
它们仍共享相同 adapter 与 Runtime 语义。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal, cast

from pydantic import JsonValue

from zniku.graph import (
    Cardinality,
    ExecutionMode,
    ManualExternalExecutorSpec,
    NodeDefinition,
    PortSpec,
    PythonExecutorSpec,
    ValidatorSpec,
)
from zniku.runtime import NodeValidator, PythonAdapter

MEDIA_NODE_VERSION: Literal["0.2.0"] = "0.2.0"

SOURCE_ADAPTER = "zniku.media.adapters:source_media"
VIDEO_TRANSFORM_ADAPTER = "zniku.media.adapters:video_transform"
SPLIT_ADAPTER = "zniku.media.adapters:split_video"
MERGE_ADAPTER = "zniku.media.adapters:merge_video"
ENCODE_ADAPTER = "zniku.media.adapters:encode_video"
MUX_ADAPTER = "zniku.media.adapters:mux_media"
OUTPUT_ADAPTER = "zniku.media.adapters:output_file"

VIDEO_TRANSFORM_VALIDATOR = "zniku.media.validators:video_transform"
SPLIT_VALIDATOR = "zniku.media.validators:split_video"
MERGE_VALIDATOR = "zniku.media.validators:merge_video"
ENCODE_VALIDATOR = "zniku.media.validators:encode_video"
MUX_VALIDATOR = "zniku.media.validators:mux_media"

MediaKind = Literal["MediaFile", "VideoFile", "AudioFile"]
ExternalPreset = Literal["mr", "enhancement", "fi"]


def _object_schema(
    properties: Mapping[str, JsonValue],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    schema: dict[str, JsonValue] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def source_media_definition(kind: MediaKind = "VideoFile") -> NodeDefinition:
    """创建只读导入一个本地媒体文件的 typed SourceMedia preset。"""

    suffix = {"MediaFile": "media", "VideoFile": "video", "AudioFile": "audio"}[kind]
    return NodeDefinition(
        type_id=f"zniku.media.source.{suffix}",
        version=MEDIA_NODE_VERSION,
        output_ports=(PortSpec(port_id="out", data_type=kind),),
        parameter_schema=_object_schema(
            {
                "source_path": {
                    "type": "string",
                    "minLength": 1,
                    "description": "操作者明确选择的本地绝对媒体路径。",
                }
            },
            required=("source_path",),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=SOURCE_ADAPTER),
    )


def automatic_video_transform_definition() -> NodeDefinition:
    """创建由闭合 operation 配置驱动的 automatic VideoTransform。"""

    parameter_schema = _object_schema(
        {
            "operation": {
                "type": "string",
                "enum": ["identity", "scale", "frame_rate"],
                "default": "identity",
            },
            "width": {"type": "integer", "minimum": 2, "maximum": 16384},
            "height": {"type": "integer", "minimum": 2, "maximum": 16384},
            "frame_rate": {
                "type": "string",
                "pattern": "^[1-9][0-9]*/[1-9][0-9]*$",
            },
        }
    )
    parameter_schema["allOf"] = [
        {
            "if": {
                "properties": {"operation": {"const": "scale"}},
                "required": ["operation"],
            },
            "then": {"required": ["width", "height"]},
        },
        {
            "if": {
                "properties": {"operation": {"const": "frame_rate"}},
                "required": ["operation"],
            },
            "then": {"required": ["frame_rate"]},
        },
    ]
    return NodeDefinition(
        type_id="zniku.media.video_transform.automatic",
        version=MEDIA_NODE_VERSION,
        input_ports=(PortSpec(port_id="video", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=parameter_schema,
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=VIDEO_TRANSFORM_ADAPTER),
        validator=ValidatorSpec(adapter=VIDEO_TRANSFORM_VALIDATOR),
    )


def external_video_transform_definition(preset: ExternalPreset) -> NodeDefinition:
    """创建 MR、Enhancement 或 FI 的 manual_external 普通节点 preset。"""

    labels = {
        "mr": "Mosaic Restoration",
        "enhancement": "Enhancement",
        "fi": "Frame Interpolation",
    }
    relation = "double" if preset == "fi" else "any"
    return NodeDefinition(
        type_id=f"zniku.media.video_transform.{preset}.external",
        version=MEDIA_NODE_VERSION,
        input_ports=(PortSpec(port_id="video", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=_object_schema(
            {
                "tool": {"type": "string", "minLength": 1, "maxLength": 256},
                "model": {"type": "string", "minLength": 1, "maxLength": 256},
                "tool_version": {"type": "string", "minLength": 1, "maxLength": 128},
                "expected_width": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 16384,
                },
                "expected_height": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 16384,
                },
                "expected_frame_rate": {
                    "type": "string",
                    "pattern": "^[1-9][0-9]*/[1-9][0-9]*$",
                },
                "frame_relation": {
                    "type": "string",
                    "enum": ["any", "equal", "double"],
                    "default": relation,
                },
            },
            required=("tool", "model", "tool_version"),
        ),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            instructions=(
                f"使用操作者声明的 {labels[preset]} 工具从完整输入开始处理；"
                "将最终单一视频写入目标路径后再 Submit。"
            )
        ),
        validator=ValidatorSpec(adapter=VIDEO_TRANSFORM_VALIDATOR),
    )


def split_video_definition(
    segment_ports: tuple[str, ...] = ("A", "B"),
    *,
    type_id: str | None = None,
) -> NodeDefinition:
    """创建设计时已知命名输出端口的 SplitVideo 定义。

    内建目录固定提供 A/B 两段。自定义端口时调用方必须给出新的稳定 ``type_id``，避免相同
    type/version 指向不同定义形状。
    """

    if not segment_ports or len(segment_ports) != len(set(segment_ports)):
        raise ValueError("E_MEDIA_SPLIT_PORTS: segment port 必须非空且唯一")
    if type_id is None:
        if segment_ports != ("A", "B"):
            raise ValueError("E_MEDIA_SPLIT_TYPE_ID_REQUIRED: 自定义 segment 必须提供 type_id")
        type_id = "zniku.media.split_video.2"
    prefix_items = [
        {
            "type": "object",
            "properties": {
                "port_id": {"const": port_id},
                "start_frame": {"type": "integer", "minimum": 0},
                "end_frame": {"type": "integer", "minimum": 1},
            },
            "required": ["port_id", "start_frame", "end_frame"],
            "additionalProperties": False,
        }
        for port_id in segment_ports
    ]
    return NodeDefinition(
        type_id=type_id,
        version=MEDIA_NODE_VERSION,
        input_ports=(PortSpec(port_id="video", data_type="VideoFile", required=True),),
        output_ports=tuple(
            PortSpec(port_id=port_id, data_type="VideoFile") for port_id in segment_ports
        ),
        parameter_schema=_object_schema(
            {
                "segments": {
                    "type": "array",
                    "prefixItems": cast(list[JsonValue], prefix_items),
                    "items": False,
                    "minItems": len(segment_ports),
                    "maxItems": len(segment_ports),
                }
            },
            required=("segments",),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=SPLIT_ADAPTER),
        validator=ValidatorSpec(adapter=SPLIT_VALIDATOR),
    )


def merge_video_definition() -> NodeDefinition:
    """创建按 Edge ordinal 消费全部输入的 MergeVideo。"""

    return NodeDefinition(
        type_id="zniku.media.merge_video",
        version=MEDIA_NODE_VERSION,
        input_ports=(
            PortSpec(
                port_id="videos",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=MERGE_ADAPTER),
        validator=ValidatorSpec(adapter=MERGE_VALIDATOR),
    )


def encode_video_definition() -> NodeDefinition:
    """创建使用显式 codec/preset 参数的 EncodeVideo。"""

    return NodeDefinition(
        type_id="zniku.media.encode_video",
        version=MEDIA_NODE_VERSION,
        input_ports=(PortSpec(port_id="video", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=_object_schema(
            {
                "codec": {
                    "type": "string",
                    "enum": ["libx264", "libx265", "ffv1"],
                    "default": "libx264",
                },
                "preset": {
                    "type": "string",
                    "enum": [
                        "ultrafast",
                        "superfast",
                        "veryfast",
                        "faster",
                        "fast",
                        "medium",
                        "slow",
                    ],
                    "default": "medium",
                },
                "crf": {"type": "integer", "minimum": 0, "maximum": 51, "default": 18},
                "pixel_format": {
                    "type": "string",
                    "enum": ["yuv420p", "yuv420p10le", "yuv422p10le"],
                    "default": "yuv420p",
                },
                "require_frame_count_equal": {"type": "boolean", "default": False},
            }
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=ENCODE_ADAPTER),
        validator=ValidatorSpec(adapter=ENCODE_VALIDATOR),
    )


def mux_media_definition() -> NodeDefinition:
    """创建视频加可选有序音轨的 MuxMedia。"""

    return NodeDefinition(
        type_id="zniku.media.mux_media",
        version=MEDIA_NODE_VERSION,
        input_ports=(
            PortSpec(port_id="video", data_type="VideoFile", required=True),
            PortSpec(
                port_id="audio",
                data_type="AudioFile",
                cardinality=Cardinality.ORDERED_MANY,
            ),
        ),
        output_ports=(PortSpec(port_id="media", data_type="MediaFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=MUX_ADAPTER),
        validator=ValidatorSpec(adapter=MUX_VALIDATOR),
    )


def output_file_definition(kind: MediaKind = "VideoFile") -> NodeDefinition:
    """创建只复制、不移动上游 Artifact 的 typed OutputFile 终端节点。"""

    suffix = {"MediaFile": "media", "VideoFile": "video", "AudioFile": "audio"}[kind]
    parameter_schema = _object_schema(
        {
            "target_path": {
                "type": "string",
                "minLength": 1,
                "description": "copy 模式明确选择的本地绝对输出路径。",
            },
            "mode": {
                "type": "string",
                "enum": ["copy", "reference"],
                "default": "copy",
                "description": "copy 发布到 target_path；reference 保留上游路径。",
            },
            "overwrite": {
                "type": "boolean",
                "default": False,
                "description": "必须显式为 true 才允许覆盖已有文件。",
            },
            "output_root": {
                "type": "string",
                "minLength": 1,
                "description": "可选发布边界：必须是现有绝对目录，目标只能位于本目录或直属子目录。",
            },
            "create_parent": {
                "type": "boolean",
                "default": False,
                "description": "显式允许发布时创建 output_root 内一个直属父目录；不递归创建。",
            },
            "protected_paths": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "uniqueItems": True,
                "description": "不能被目标覆盖的额外绝对源路径；包括同路径与硬链接。",
            },
        },
        required=("mode", "overwrite"),
    )
    parameter_schema["allOf"] = [
        {
            "if": {"properties": {"mode": {"const": "copy"}}, "required": ["mode"]},
            "then": {"required": ["target_path"]},
        },
        {
            "if": {"properties": {"create_parent": {"const": True}}, "required": ["create_parent"]},
            "then": {"required": ["output_root"], "properties": {"mode": {"const": "copy"}}},
        },
        {
            "if": {"properties": {"mode": {"const": "reference"}}, "required": ["mode"]},
            "then": {
                "properties": {"create_parent": {"const": False}},
                "not": {"required": ["output_root"]},
            },
        },
    ]
    return NodeDefinition(
        type_id=f"zniku.media.output_file.{suffix}",
        version=MEDIA_NODE_VERSION,
        input_ports=(PortSpec(port_id="in", data_type=kind, required=True),),
        output_ports=(PortSpec(port_id="published", data_type=kind),),
        parameter_schema=parameter_schema,
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=OUTPUT_ADAPTER),
    )


def legacy_output_file_definition(kind: MediaKind = "VideoFile") -> NodeDefinition:
    """构造已发布旧 shape 的闭合兼容定义；只供识别，不改写持久工程。"""

    document = output_file_definition(kind).model_dump(mode="python")
    schema = document["parameter_schema"]
    for key in ("output_root", "create_parent", "protected_paths"):
        del schema["properties"][key]
    schema["allOf"] = schema["allOf"][:1]
    return NodeDefinition.model_validate(document, strict=True)


def is_supported_output_file_definition(definition: NodeDefinition) -> bool:
    """仅接受当前与已发布旧 OutputFile 的完整精确 shape，不容忍任意 Schema 漂移。"""

    kinds: tuple[MediaKind, ...] = ("MediaFile", "VideoFile", "AudioFile")
    return any(
        definition in (output_file_definition(kind), legacy_output_file_definition(kind))
        for kind in kinds
    )


def built_in_media_definitions() -> tuple[NodeDefinition, ...]:
    """返回 Phase 4 内建媒体节点目录；每次调用都构造独立严格模型。"""

    return (
        source_media_definition("VideoFile"),
        source_media_definition("AudioFile"),
        source_media_definition("MediaFile"),
        automatic_video_transform_definition(),
        external_video_transform_definition("mr"),
        external_video_transform_definition("enhancement"),
        external_video_transform_definition("fi"),
        split_video_definition(),
        merge_video_definition(),
        encode_video_definition(),
        mux_media_definition(),
        output_file_definition("VideoFile"),
        output_file_definition("AudioFile"),
        output_file_definition("MediaFile"),
    )


def media_python_adapters() -> Mapping[str, PythonAdapter]:
    """返回 Runner 可直接注册的 Python adapter mapping。"""

    from zniku.media.adapters import (
        encode_video,
        merge_video,
        mux_media,
        output_file,
        source_media,
        split_video,
        video_transform,
    )

    return {
        SOURCE_ADAPTER: source_media,
        VIDEO_TRANSFORM_ADAPTER: video_transform,
        SPLIT_ADAPTER: split_video,
        MERGE_ADAPTER: merge_video,
        ENCODE_ADAPTER: encode_video,
        MUX_ADAPTER: mux_media,
        OUTPUT_ADAPTER: output_file,
    }


def media_validators() -> Mapping[str, NodeValidator]:
    """返回 Runner 可直接注册的节点级轻量 validator mapping。"""

    from zniku.media.validators import (
        validate_encode_video,
        validate_merge_video,
        validate_mux_media,
        validate_split_video,
        validate_video_transform,
    )

    return {
        VIDEO_TRANSFORM_VALIDATOR: validate_video_transform,
        SPLIT_VALIDATOR: validate_split_video,
        MERGE_VALIDATOR: validate_merge_video,
        ENCODE_VALIDATOR: validate_encode_video,
        MUX_VALIDATOR: validate_mux_media,
    }


# 显式约束 mapping 返回的 callable 类型，避免实现漂移成可序列化命令字符串。
_AdapterFactory = Callable[[], Mapping[str, PythonAdapter]]
_ValidatorFactory = Callable[[], Mapping[str, NodeValidator]]
_adapter_factory: _AdapterFactory = media_python_adapters
_validator_factory: _ValidatorFactory = media_validators
