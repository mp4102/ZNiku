"""声明独立的 0.3.4 重叠 FI 节点，旧 AV27 定义与 exact 行为完全不变。

参数来自严格 Python 模型；仅在组装 Runtime 时延迟加载真实 adapter，不以字符串占位注册
未实现能力。人工 FI 明确处于待真实验收状态，定义本身不证明模型或相位。
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib import import_module
from typing import cast

from pydantic import JsonValue

from zniku.graph import (
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

from .node_contracts import (
    ATOMIC_SPLIT_TYPE_PREFIX,
    EXTERNAL_TYPE_PREFIX,
    OVERLAP_NODE_VERSION,
    PARAMETER_MODELS,
    ROLE_TYPES,
    DeclaredContainer,
    ExternalParameters,
)

ENHANCEMENT_TYPE_ID = ROLE_TYPES["enhancement"]
MERGE_VIDEO_TYPE_ID = ROLE_TYPES["merge"]
FI_CONTEXT_TYPE_ID = ROLE_TYPES["context"]
FRAME_INTERPOLATION_TYPE_ID = ROLE_TYPES["fi"]
FI_CROP_TYPE_ID = ROLE_TYPES["crop"]
PROGRAM_ENCODE_TYPE_ID = ROLE_TYPES["program"]
FINAL_MUX_TYPE_ID = ROLE_TYPES["final"]

_NAMES = {
    "split": "atomic_split",
    "enhancement": "enhancement",
    "merge": "merge_video",
    "context": "fi_context",
    "fi": "frame_interpolation",
    "crop": "fi_crop",
    "program": "program_encode",
    "final": "final_mux",
}
_PATHS = {
    "enhancement": "enhancement.mov",
    "merge": "merge.mov",
    "context": "context.mov",
    "fi": "fi.raw.mov",
    "crop": "fi.crop.mov",
    "program": "program.mp4",
    "final": "final.mkv",
}


def atomic_split_port_ids(count: int) -> tuple[str, ...]:
    """显式 output shape 受 planner 资源预算限制，不把这个预算当作 Core 限制。"""
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError("E_OVERLAP_SPLIT_COUNT: leaf count 必须为 1..10000 严格整数")
    return tuple(f"leaf-{ordinal:04d}" for ordinal in range(1, count + 1))


def atomic_split_type_id(count: int) -> str:
    atomic_split_port_ids(count)
    return f"{ATOMIC_SPLIT_TYPE_PREFIX}{count}"


def _port(name: str, *, many: bool = False, kind: str = "VideoFile") -> PortSpec:
    return PortSpec(
        port_id=name,
        data_type=kind,
        required=True,
        cardinality=Cardinality.ORDERED_MANY if many else Cardinality.ONE,
    )


def _definition(role: str, count: int = 1) -> NodeDefinition:
    # bool 与 1 的相等关系不得穿透缓存；只接受规范的已校验 shape 键。
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError("E_OVERLAP_SPLIT_COUNT: count 必须为 1..10000 严格整数")
    return _cached_definition(role, count)


@lru_cache(maxsize=32)
def _cached_definition(role: str, count: int) -> NodeDefinition:
    inputs: tuple[PortSpec, ...]
    schema = PARAMETER_MODELS[role].model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    if role == "split":
        ports = atomic_split_port_ids(count)
        inputs = (_port("videos", many=True), _port("gate", kind="DataFile"))
        outputs = tuple(PortSpec(port_id=p, data_type="VideoFile") for p in ports)
        paths = tuple(
            ExecutorOutputPathSpec(port_id=p, relative_path=f"leaves/{p}.mkv") for p in ports
        )
        schema["$defs"]["ChapterLeafPlan"]["properties"]["leaf_count"]["const"] = count
    else:
        ports = ("media" if role == "final" else "video",)
        inputs = {
            "merge": (_port("videos", many=True),),
            "context": (_port("chapters", many=True),),
            "program": (_port("chapters", many=True),),
            "final": (
                _port("video"),
                _port("sources", many=True, kind="MediaFile"),
                _port("gate", kind="DataFile"),
            ),
        }.get(role, (_port("video"),))
        outputs = (
            PortSpec(port_id=ports[0], data_type="MediaFile" if role == "final" else "VideoFile"),
        )
        paths = (ExecutorOutputPathSpec(port_id=ports[0], relative_path=_PATHS[role]),)
    manual = role in {"enhancement", "fi"}
    executor = (
        ManualExternalExecutorSpec(
            output_paths=paths,
            instructions=(
                "使用声明模型处理当前完整叶，保持帧数与帧率；完成后显式检查并提交。"
                if role == "enhancement"
                else "候选 Aion / 软件 v1.0：处理带上下文输入，2x 输出必须为 2M-1。保留 raw 来件；"
                "偶数位置对应输入帧是待真实验收假设，帧数通过不能证明模型、相位或画质。"
            ),
        )
        if manual
        else PythonExecutorSpec(
            adapter=f"zniku.prepared_source.adapters:{_NAMES[role]}", output_paths=paths
        )
    )
    return NodeDefinition(
        type_id=atomic_split_type_id(count) if role == "split" else ROLE_TYPES[role],
        version=OVERLAP_NODE_VERSION,
        input_ports=inputs,
        output_ports=outputs,
        parameter_schema=cast(dict[str, JsonValue], schema),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL if manual else ExecutionMode.AUTOMATIC,
        executor=executor,
        validator=ValidatorSpec(
            adapter=f"zniku.prepared_source.validators:validate_{_NAMES[role]}"
        ),
    )


def atomic_split_definition(count: int) -> NodeDefinition:
    """由严格新 planner 投影绑定动态 FFV1 输出 shape。"""
    return _definition("split", count)


def enhancement_definition() -> NodeDefinition:
    return _definition("enhancement")


def merge_video_definition() -> NodeDefinition:
    return _definition("merge")


def fi_context_definition() -> NodeDefinition:
    return _definition("context")


def frame_interpolation_definition() -> NodeDefinition:
    return _definition("fi")


def fi_crop_definition() -> NodeDefinition:
    return _definition("crop")


def program_encode_definition() -> NodeDefinition:
    return _definition("program")


def final_mux_definition() -> NodeDefinition:
    return _definition("final")


def built_in_overlap_definitions(leaf_count: int = 1) -> tuple[NodeDefinition, ...]:
    """返回八类真实节点；是否纳入 Registry 由应用显式组装。"""
    return tuple(_definition(role, leaf_count) for role in _NAMES)


def definition_role(definition: NodeDefinition) -> str | None:
    """识别完整 exact 定义，不允许同名第三方 validator/Schema 冒认内建媒体合同。"""
    if definition.version != OVERLAP_NODE_VERSION:
        return None
    if definition.type_id.startswith(ATOMIC_SPLIT_TYPE_PREFIX):
        suffix = definition.type_id.removeprefix(ATOMIC_SPLIT_TYPE_PREFIX)
        count = len(definition.output_ports)
        if suffix != str(count) or not 1 <= count <= 10000:
            return None
        return "split" if definition == _definition("split", count) else None
    for container in ("mp4", "mov", "mkv"):
        if definition == external_definition(container):
            return "external"
    for role, type_id in ROLE_TYPES.items():
        if definition.type_id == type_id:
            return role if definition == _definition(role) else None
    return None


def overlap_python_adapters() -> Mapping[str, PythonAdapter]:
    """只在实际模块具备 callable 实现时注册六个自动执行器。"""
    module = import_module("zniku.prepared_source.adapters")
    result: dict[str, PythonAdapter] = {}
    for role, name in _NAMES.items():
        if role in {"enhancement", "fi"}:
            continue
        value = getattr(module, name)
        if not callable(value):
            raise TypeError(f"E_OVERLAP_ADAPTER: {name} 不可调用")
        result[f"zniku.prepared_source.adapters:{name}"] = cast(PythonAdapter, value)
    return result


def overlap_validators() -> Mapping[str, NodeValidator]:
    module = import_module("zniku.prepared_source.validators")
    result: dict[str, NodeValidator] = {}
    for name in _NAMES.values():
        value = getattr(module, f"validate_{name}")
        if not callable(value):
            raise TypeError(f"E_OVERLAP_VALIDATOR: {name} 不可调用")
        result[f"zniku.prepared_source.validators:validate_{name}"] = cast(NodeValidator, value)
    result["zniku.prepared_source.validators:validate_external"] = cast(
        NodeValidator, module.validate_external
    )
    return result


def external_type_id(container: DeclaredContainer = "mp4") -> str:
    if container not in {"mp4", "mov", "mkv"}:
        raise ValueError("E_PREPARED_SOURCE_CONTAINER: 不支持的容器")
    return EXTERNAL_TYPE_PREFIX + container


def external_definition(container: DeclaredContainer = "mp4") -> NodeDefinition:
    """三种静态目标后缀对应独立 exact 定义，不依赖 Runtime 理解媒体参数。"""
    external_type_id(container)
    return _cached_external_definition(container)


@lru_cache(maxsize=3)
def _cached_external_definition(container: DeclaredContainer) -> NodeDefinition:
    type_id = external_type_id(container)
    schema = ExternalParameters.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["properties"]["declared_container"] = {
        "type": "string",
        "const": container,
        "default": container,
    }
    if "declared_container" not in schema["required"]:
        schema["required"].append("declared_container")
    return NodeDefinition(
        type_id=type_id,
        version=OVERLAP_NODE_VERSION,
        input_ports=(_port("video"), _port("gate", kind="DataFile")),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=cast(dict[str, JsonValue], schema),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            output_paths=(
                ExecutorOutputPathSpec(port_id="video", relative_path=f"restoration.{container}"),
            ),
            instructions="对已准入工作参考进行马赛克修复，保持 N→N、帧序、精确帧率及几何/色彩；"
            "不得裁剪、变速、补丢帧或补帧。提交实际声明容器的文件。时间轴检查不证明逐帧内容或模型。",
        ),
        validator=ValidatorSpec(adapter="zniku.prepared_source.validators:validate_external"),
    )


newdefinition_role = definition_role
