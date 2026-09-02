"""声明 AVEnhanceFlow v2.7.0 profile 的专用 NodeDefinition。

本模块只冻结专用节点的身份、typed ports、参数 Schema 与受控输出路径。它不生成工程或流程模板，
不解释媒体内容，也不向通用 Runtime 注入业务分支。所有可执行 adapter 与 validator 都只通过仓库内
稳定引用登记；用户参数不能携带命令、代码或工具可执行声明。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from typing import Final, Literal, cast

from pydantic import JsonValue

from zniku.graph import (
    JSON_SCHEMA_DIALECT,
    Cardinality,
    ExecutionMode,
    ExecutorOutputPathSpec,
    ManualExternalExecutorSpec,
    NodeDefinition,
    PortSpec,
    PythonExecutorSpec,
    ValidatorSpec,
)
from zniku.runtime import NodeValidator, PythonAdapter

AV27_NODE_VERSION: Literal["0.2.1"] = "0.2.1"
AV27_PROFILE_VERSION: Literal["2.7.0"] = "2.7.0"
AV27_MEDIA_INFO_NAMESPACE: Literal["zniku.avenhance.v27"] = "zniku.avenhance.v27"

SOURCE_PROGRAM_TYPE_ID: Final = "zniku.avenhance.v27.source_program"
SOURCE_ADMISSION_TYPE_ID: Final = "zniku.avenhance.v27.source_admission"
MOSAIC_RESTORATION_TYPE_ID: Final = "zniku.avenhance.v27.mosaic_restoration.external"
ATOMIC_SPLIT_TYPE_PREFIX: Final = "zniku.avenhance.v27.atomic_split.leaves."
ENHANCEMENT_TYPE_ID: Final = "zniku.avenhance.v27.enhancement.external"
MERGE_VIDEO_TYPE_ID: Final = "zniku.avenhance.v27.merge_video"
FRAME_INTERPOLATION_TYPE_ID: Final = "zniku.avenhance.v27.frame_interpolation.external"
PROGRAM_ENCODE_TYPE_ID: Final = "zniku.avenhance.v27.program_encode"
FINAL_MUX_TYPE_ID: Final = "zniku.avenhance.v27.final_mux"

SOURCE_PROGRAM_ADAPTER: Final = "zniku.avenhance_v27.adapters:source_program"
SOURCE_ADMISSION_ADAPTER: Final = "zniku.avenhance_v27.adapters:source_admission"
ATOMIC_SPLIT_ADAPTER: Final = "zniku.avenhance_v27.adapters:atomic_split"
MERGE_VIDEO_ADAPTER: Final = "zniku.avenhance_v27.adapters:merge_video"
PROGRAM_ENCODE_ADAPTER: Final = "zniku.avenhance_v27.adapters:program_encode"
FINAL_MUX_ADAPTER: Final = "zniku.avenhance_v27.adapters:final_mux"

SOURCE_PROGRAM_VALIDATOR: Final = "zniku.avenhance_v27.validators:validate_source_program"
SOURCE_ADMISSION_VALIDATOR: Final = "zniku.avenhance_v27.validators:validate_source_admission"
MOSAIC_RESTORATION_VALIDATOR: Final = "zniku.avenhance_v27.validators:validate_mosaic_restoration"
ATOMIC_SPLIT_VALIDATOR: Final = "zniku.avenhance_v27.validators:validate_atomic_split"
ENHANCEMENT_VALIDATOR: Final = "zniku.avenhance_v27.validators:validate_enhancement"
MERGE_VIDEO_VALIDATOR: Final = "zniku.avenhance_v27.validators:validate_merge_video"
FRAME_INTERPOLATION_VALIDATOR: Final = "zniku.avenhance_v27.validators:validate_frame_interpolation"
PROGRAM_ENCODE_VALIDATOR: Final = "zniku.avenhance_v27.validators:validate_program_encode"
FINAL_MUX_VALIDATOR: Final = "zniku.avenhance_v27.validators:validate_final_mux"

_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
_RATIONAL_PATTERN: Final = r"^[1-9][0-9]*/[1-9][0-9]*$"
_ABSOLUTE_PATH_PATTERN: Final = r"^(?:[A-Za-z]:[\\/]|\\\\|/)"

type SchemaObject = dict[str, JsonValue]


def _object_schema(
    properties: Mapping[str, JsonValue],
    *,
    required: tuple[str, ...] = (),
) -> SchemaObject:
    schema: SchemaObject = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _root_schema(
    properties: Mapping[str, JsonValue],
    *,
    required: tuple[str, ...] = (),
) -> SchemaObject:
    schema = _object_schema(properties, required=required)
    schema["$schema"] = JSON_SCHEMA_DIALECT
    return schema


def _positive_integer() -> SchemaObject:
    return {"type": "integer", "minimum": 1}


def _ordinal() -> SchemaObject:
    return {"type": "integer", "minimum": 0}


def _identifier() -> SchemaObject:
    return {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _IDENTIFIER_PATTERN}


def _rational() -> SchemaObject:
    return {"type": "string", "pattern": _RATIONAL_PATTERN}


def _geometry() -> SchemaObject:
    return _object_schema(
        {
            "width": {"type": "integer", "minimum": 2, "maximum": 16384},
            "height": {"type": "integer", "minimum": 2, "maximum": 16384},
            "sample_aspect_ratio": _rational(),
        },
        required=("width", "height", "sample_aspect_ratio"),
    )


def _signal() -> SchemaObject:
    return _object_schema(
        {
            "color_primaries": {"const": "bt709"},
            "color_transfer": {"const": "bt709"},
            "color_space": {"const": "bt709"},
            "color_range": {"const": "tv"},
            "chroma_location": {"const": "left"},
            "field_order": {"const": "progressive"},
            "rotation": {"const": 0},
        },
        required=(
            "color_primaries",
            "color_transfer",
            "color_space",
            "color_range",
            "chroma_location",
            "field_order",
            "rotation",
        ),
    )


def _model_properties(*, version_required: bool) -> tuple[SchemaObject, tuple[str, ...]]:
    properties: SchemaObject = {
        "model_name": {"type": "string", "minLength": 1, "maxLength": 256},
        "model_version": {"type": "string", "minLength": 1, "maxLength": 128},
    }
    required = ("model_name", "model_version") if version_required else ("model_name",)
    return properties, required


def _output_path(port_id: str, relative_path: str) -> ExecutorOutputPathSpec:
    return ExecutorOutputPathSpec(port_id=port_id, relative_path=relative_path)


def source_program_definition() -> NodeDefinition:
    """返回只读接纳一个物理 Source 的定义；它不声明 attempt 内输出路径。"""

    return NodeDefinition(
        type_id=SOURCE_PROGRAM_TYPE_ID,
        version=AV27_NODE_VERSION,
        output_ports=(
            PortSpec(port_id="video", data_type="VideoFile"),
            PortSpec(port_id="source_media", data_type="MediaFile"),
        ),
        parameter_schema=_root_schema(
            {
                "source_path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 32767,
                    "pattern": _ABSOLUTE_PATH_PATTERN,
                },
                "source_ordinal": _ordinal(),
                "label": {"type": "string", "minLength": 1, "maxLength": 256},
            },
            required=("source_path", "source_ordinal"),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=SOURCE_PROGRAM_ADAPTER),
        validator=ValidatorSpec(adapter=SOURCE_PROGRAM_VALIDATOR),
    )


def source_admission_definition() -> NodeDefinition:
    """返回按 Source ordinal 建立普通 DataFile barrier 的定义。"""

    source_identity = _object_schema(
        {
            "source_ordinal": _ordinal(),
            "source_node_id": _identifier(),
        },
        required=("source_ordinal", "source_node_id"),
    )
    return NodeDefinition(
        type_id=SOURCE_ADMISSION_TYPE_ID,
        version=AV27_NODE_VERSION,
        input_ports=(
            PortSpec(
                port_id="sources",
                data_type="MediaFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
        output_ports=(PortSpec(port_id="gate", data_type="DataFile"),),
        parameter_schema=_root_schema(
            {
                "source_mode": {"type": "string", "enum": ["program", "pre_chaptered"]},
                "sources": {
                    "type": "array",
                    "items": source_identity,
                    "minItems": 1,
                    "uniqueItems": True,
                },
            },
            required=("source_mode", "sources"),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(
            adapter=SOURCE_ADMISSION_ADAPTER,
            output_paths=(_output_path("gate", "admission.json"),),
        ),
        validator=ValidatorSpec(adapter=SOURCE_ADMISSION_VALIDATOR),
    )


def mosaic_restoration_definition() -> NodeDefinition:
    """返回固定目标 ``mr.mkv`` 的人工 Mosaic Restoration 定义。"""

    properties, required = _model_properties(version_required=True)
    return NodeDefinition(
        type_id=MOSAIC_RESTORATION_TYPE_ID,
        version=AV27_NODE_VERSION,
        input_ports=(
            PortSpec(port_id="video", data_type="VideoFile", required=True),
            PortSpec(port_id="gate", data_type="DataFile", required=True),
        ),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=_root_schema(properties, required=required),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            instructions=(
                "使用声明的 Mosaic Restoration 模型从完整输入开始处理，"
                "并将唯一最终媒体写入固定目标。"
            ),
            output_paths=(_output_path("video", "mr.mkv"),),
        ),
        validator=ValidatorSpec(adapter=MOSAIC_RESTORATION_VALIDATOR),
    )


def atomic_split_port_ids(count: int) -> tuple[str, ...]:
    """按全节目顺序产生稳定 leaf port；bool、零和负数均失败关闭。"""

    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("E_AV27_SPLIT_COUNT: leaf count 必须是正整数")
    return tuple(f"leaf-{ordinal:04d}" for ordinal in range(1, count + 1))


def atomic_split_type_id(count: int) -> str:
    """返回只由无前导零十进制 count 决定的稳定 Split type identity。"""

    atomic_split_port_ids(count)
    return f"{ATOMIC_SPLIT_TYPE_PREFIX}{count}"


def _segment_schema(port_id: str) -> SchemaObject:
    return _object_schema(
        {
            "port_id": {"const": port_id},
            "source_ordinal": _ordinal(),
            "planned_effective_video_artifact_id": _identifier(),
            "chapter_id": _identifier(),
            "chapter_ordinal": _ordinal(),
            "leaf_id": _identifier(),
            "leaf_ordinal": _ordinal(),
            "start_frame": _ordinal(),
            "end_frame": _positive_integer(),
        },
        required=(
            "port_id",
            "source_ordinal",
            "planned_effective_video_artifact_id",
            "chapter_id",
            "chapter_ordinal",
            "leaf_id",
            "leaf_ordinal",
            "start_frame",
            "end_frame",
        ),
    )


def atomic_split_definition(count: int) -> NodeDefinition:
    """返回 output shape 与 count 一一绑定的 AtomicSplit 定义。"""

    port_ids = atomic_split_port_ids(count)
    segment_schemas = [_segment_schema(port_id) for port_id in port_ids]
    return NodeDefinition(
        type_id=atomic_split_type_id(count),
        version=AV27_NODE_VERSION,
        input_ports=(
            PortSpec(
                port_id="videos",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
            PortSpec(port_id="gate", data_type="DataFile", required=True),
        ),
        output_ports=tuple(
            PortSpec(port_id=port_id, data_type="VideoFile") for port_id in port_ids
        ),
        parameter_schema=_root_schema(
            {
                "planned_admission_artifact_id": _identifier(),
                "segments": {
                    "type": "array",
                    "prefixItems": cast(list[JsonValue], segment_schemas),
                    "items": False,
                    "minItems": count,
                    "maxItems": count,
                },
            },
            required=("planned_admission_artifact_id", "segments"),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(
            adapter=ATOMIC_SPLIT_ADAPTER,
            output_paths=tuple(
                _output_path(port_id, f"leaves/{port_id}.mkv") for port_id in port_ids
            ),
        ),
        validator=ValidatorSpec(adapter=ATOMIC_SPLIT_VALIDATOR),
    )


def enhancement_definition() -> NodeDefinition:
    """返回固定目标 ``enhancement.mov`` 的逐 leaf 人工增强定义。"""

    properties, model_required = _model_properties(version_required=False)
    properties.update(
        {
            "actual_scale_factor": _positive_integer(),
            "expected_input_geometry": _geometry(),
            "expected_output_geometry": _geometry(),
            "expected_frames": _positive_integer(),
            "expected_fps": _rational(),
            "chapter_id": _identifier(),
            "chapter_ordinal": _ordinal(),
            "leaf_id": _identifier(),
            "leaf_ordinal": _ordinal(),
        }
    )
    # k=1 可由 validator 从 geometry 自动接受；只有 k>1 才要求操作者显式声明。
    required = (
        *model_required,
        "expected_input_geometry",
        "expected_output_geometry",
        "expected_frames",
        "expected_fps",
        "chapter_id",
        "chapter_ordinal",
        "leaf_id",
        "leaf_ordinal",
    )
    return NodeDefinition(
        type_id=ENHANCEMENT_TYPE_ID,
        version=AV27_NODE_VERSION,
        input_ports=(PortSpec(port_id="video", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=_root_schema(properties, required=required),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            instructions=(
                "使用声明的 Enhancement 模型从完整 leaf 开始处理，并将唯一最终媒体写入固定目标。"
            ),
            output_paths=(_output_path("video", "enhancement.mov"),),
        ),
        validator=ValidatorSpec(adapter=ENHANCEMENT_VALIDATOR),
    )


def merge_video_definition() -> NodeDefinition:
    """返回按 leaf ordinal 合并单章视频的定义。"""

    properties, model_required = _model_properties(version_required=False)
    properties.update(
        {
            "chapter_id": _identifier(),
            "chapter_ordinal": _ordinal(),
            "expected_frames": _positive_integer(),
            "expected_fps": _rational(),
            "expected_geometry": _geometry(),
            "actual_scale_factor": _positive_integer(),
        }
    )
    return NodeDefinition(
        type_id=MERGE_VIDEO_TYPE_ID,
        version=AV27_NODE_VERSION,
        input_ports=(
            PortSpec(
                port_id="videos",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=_root_schema(
            properties,
            required=(
                *model_required,
                "chapter_id",
                "chapter_ordinal",
                "expected_frames",
                "expected_fps",
                "expected_geometry",
                "actual_scale_factor",
            ),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(
            adapter=MERGE_VIDEO_ADAPTER,
            output_paths=(_output_path("video", "merge.mov"),),
        ),
        validator=ValidatorSpec(adapter=MERGE_VIDEO_VALIDATOR),
    )


def frame_interpolation_definition() -> NodeDefinition:
    """返回固定目标 ``fi.mov`` 且要求 ``N→2N-1`` 的人工 FI 定义。"""

    properties, model_required = _model_properties(version_required=False)
    properties.update(
        {
            "chapter_id": _identifier(),
            "chapter_ordinal": _ordinal(),
            "expected_input_frames": _positive_integer(),
            "expected_output_frames": _positive_integer(),
            "source_fps": _rational(),
            "expected_geometry": _geometry(),
            "expected_signal": _signal(),
        }
    )
    return NodeDefinition(
        type_id=FRAME_INTERPOLATION_TYPE_ID,
        version=AV27_NODE_VERSION,
        input_ports=(PortSpec(port_id="video", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=_root_schema(
            properties,
            required=(
                *model_required,
                "chapter_id",
                "chapter_ordinal",
                "expected_input_frames",
                "expected_output_frames",
                "source_fps",
                "expected_geometry",
                "expected_signal",
            ),
        ),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            instructions=(
                "使用声明的 Frame Interpolation 模型从完整 chapter 开始处理，"
                "并将唯一最终媒体写入固定目标。"
            ),
            output_paths=(_output_path("video", "fi.mov"),),
        ),
        validator=ValidatorSpec(adapter=FRAME_INTERPOLATION_VALIDATOR),
    )


def _program_chapter_schema() -> SchemaObject:
    return _object_schema(
        {
            "chapter_id": _identifier(),
            "chapter_ordinal": _ordinal(),
            "source_frames": _positive_integer(),
            "expected_fi_frames": _positive_integer(),
            "encoded_frames": _positive_integer(),
        },
        required=(
            "chapter_id",
            "chapter_ordinal",
            "source_frames",
            "expected_fi_frames",
            "encoded_frames",
        ),
    )


def program_encode_definition() -> NodeDefinition:
    """返回消费全部 chapter 并连续编码一个 Program 的定义。"""

    return NodeDefinition(
        type_id=PROGRAM_ENCODE_TYPE_ID,
        version=AV27_NODE_VERSION,
        input_ports=(
            PortSpec(
                port_id="chapters",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=_root_schema(
            {
                "encoder": {"type": "string", "enum": ["gpu", "cpu"]},
                "source_fps": _rational(),
                "chapters": {
                    "type": "array",
                    "items": _program_chapter_schema(),
                    "minItems": 1,
                },
                "expected_geometry": _geometry(),
                "expected_signal": _signal(),
            },
            required=(
                "encoder",
                "source_fps",
                "chapters",
                "expected_geometry",
                "expected_signal",
            ),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(
            adapter=PROGRAM_ENCODE_ADAPTER,
            output_paths=(_output_path("video", "program.mp4"),),
        ),
        validator=ValidatorSpec(adapter=PROGRAM_ENCODE_VALIDATOR),
    )


def _final_source_schema() -> SchemaObject:
    return _object_schema(
        {
            "source_ordinal": _ordinal(),
            "source_frames": _positive_integer(),
            "source_fps": _rational(),
        },
        required=("source_ordinal", "source_frames", "source_fps"),
    )


def final_mux_definition() -> NodeDefinition:
    """返回保留原始 Source 音轨并产生 attempt 内 ``final.mkv`` 的定义。"""

    return NodeDefinition(
        type_id=FINAL_MUX_TYPE_ID,
        version=AV27_NODE_VERSION,
        input_ports=(
            PortSpec(port_id="video", data_type="VideoFile", required=True),
            PortSpec(
                port_id="sources",
                data_type="MediaFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
            PortSpec(port_id="gate", data_type="DataFile", required=True),
        ),
        output_ports=(PortSpec(port_id="media", data_type="MediaFile"),),
        parameter_schema=_root_schema(
            {
                "source_mode": {"type": "string", "enum": ["program", "pre_chaptered"]},
                "sources": {
                    "type": "array",
                    "items": _final_source_schema(),
                    "minItems": 1,
                },
                "expected_program_frames": _positive_integer(),
                "expected_geometry": _geometry(),
                "expected_signal": _signal(),
                "mr_mode": {"type": "string", "enum": ["off", "external"]},
            },
            required=(
                "source_mode",
                "sources",
                "expected_program_frames",
                "expected_geometry",
                "expected_signal",
                "mr_mode",
            ),
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(
            adapter=FINAL_MUX_ADAPTER,
            output_paths=(_output_path("media", "final.mkv"),),
        ),
        validator=ValidatorSpec(adapter=FINAL_MUX_VALIDATOR),
    )


def built_in_av27_definitions(leaf_count: int = 1) -> tuple[NodeDefinition, ...]:
    """返回九类专用定义；动态 Split 的 shape 只由显式 ``leaf_count`` 决定。"""

    return (
        source_program_definition(),
        source_admission_definition(),
        mosaic_restoration_definition(),
        atomic_split_definition(leaf_count),
        enhancement_definition(),
        merge_video_definition(),
        frame_interpolation_definition(),
        program_encode_definition(),
        final_mux_definition(),
    )


def _load_callable(reference: str) -> Callable[..., object]:
    """只解析模块内冻结的 adapter/validator 引用，并拒绝非 callable 漂移。"""

    module_name, separator, attribute_name = reference.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError(f"E_AV27_REFERENCE_INVALID: {reference!r} 不是 module:attribute")
    value: object = getattr(import_module(module_name), attribute_name)
    if not callable(value):
        raise TypeError(f"E_AV27_REFERENCE_NOT_CALLABLE: {reference!r} 不是 callable")
    return cast(Callable[..., object], value)


def av27_python_adapters() -> Mapping[str, PythonAdapter]:
    """返回专用 automatic adapter 注册表；导入延迟到实际 Runtime 组装时。"""

    return {
        reference: cast(PythonAdapter, _load_callable(reference))
        for reference in (
            SOURCE_PROGRAM_ADAPTER,
            SOURCE_ADMISSION_ADAPTER,
            ATOMIC_SPLIT_ADAPTER,
            MERGE_VIDEO_ADAPTER,
            PROGRAM_ENCODE_ADAPTER,
            FINAL_MUX_ADAPTER,
        )
    }


def av27_validators() -> Mapping[str, NodeValidator]:
    """返回九类节点的 validator 注册表；manual 与 automatic 共用普通 Runner 接口。"""

    return {
        reference: cast(NodeValidator, _load_callable(reference))
        for reference in (
            SOURCE_PROGRAM_VALIDATOR,
            SOURCE_ADMISSION_VALIDATOR,
            MOSAIC_RESTORATION_VALIDATOR,
            ATOMIC_SPLIT_VALIDATOR,
            ENHANCEMENT_VALIDATOR,
            MERGE_VIDEO_VALIDATOR,
            FRAME_INTERPOLATION_VALIDATOR,
            PROGRAM_ENCODE_VALIDATOR,
            FINAL_MUX_VALIDATOR,
        )
    }


# 锁定公开 factory 的类型，避免后续误改为序列化命令或惰性字符串列表。
_AdapterFactory = Callable[[], Mapping[str, PythonAdapter]]
_ValidatorFactory = Callable[[], Mapping[str, NodeValidator]]
_adapter_factory: _AdapterFactory = av27_python_adapters
_validator_factory: _ValidatorFactory = av27_validators
