"""注册一套 0.3.5 exact 媒体定义，参数与端口复用，不覆盖旧定义或执行器。"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib import import_module
from typing import cast

from zniku.avenhance_v27 import definitions as av27
from zniku.graph import NodeDefinition
from zniku.runtime import NodeValidator, PythonAdapter
from zniku.source_aligned import definitions as aligned
from zniku.source_aligned.node_contracts import ATOMIC_SPLIT_TYPE_PREFIX, DeclaredContainer

from .contracts import VERSION

NAMES = {
    "split": "atomic_split",
    "enhancement": "enhancement",
    "merge": "merge_video",
    "context": "fi_context",
    "fi": "frame_interpolation",
    "crop": "fi_crop",
    "program": "program_encode",
    "final": "final_mux",
    "source": "source_program",
    "admission": "source_admission",
    "external": "external",
}


def _versioned(old: NodeDefinition, role: str) -> NodeDefinition:
    document = old.model_dump()
    document["version"] = VERSION
    if old.execution_mode == "automatic":
        document["executor"]["adapter"] = f"zniku.source_admission.adapters:{NAMES[role]}"
    document["validator"]["adapter"] = f"zniku.source_admission.validators:validate_{NAMES[role]}"
    if role == "source":
        document["parameter_schema"]["properties"]["source_ordinal"] = {
            "type": "integer",
            "const": 0,
        }
    if role == "admission":
        properties = document["parameter_schema"]["properties"]
        properties["source_mode"] = {"type": "string", "const": "program"}
        properties["sources"]["maxItems"] = 1
        properties["sources"]["items"]["properties"]["source_ordinal"] = {
            "type": "integer",
            "const": 0,
        }
    if role == "external":
        document["executor"]["instructions"] = (
            "处理完整参考视频并保持 N→N、帧序、等价 FPS、几何和色彩；可提交声明的 MP4/MOV/MKV。"
            "本步骤是可选马赛克修复，不是源故障修复；输出须显式检查提交。"
        )
    return NodeDefinition.model_validate(document)


def definition(role: str, count: int = 1) -> NodeDefinition:
    aligned.atomic_split_port_ids(count)
    return _definition(role, count)


@lru_cache(maxsize=32)
def _definition(role: str, count: int) -> NodeDefinition:
    if role == "source":
        return _versioned(av27.source_program_definition(), role)
    if role == "admission":
        return _versioned(av27.source_admission_definition(), role)
    if role not in NAMES or role == "external":
        raise ValueError("未知 source-admitted 节点职责")
    return _versioned(aligned._definition(role, count), role)


def source_program_definition() -> NodeDefinition:
    return definition("source")


def source_admission_definition() -> NodeDefinition:
    return definition("admission")


def atomic_split_definition(count: int) -> NodeDefinition:
    aligned.atomic_split_port_ids(count)
    return definition("split", count)


@lru_cache(maxsize=3)
def external_definition(container: DeclaredContainer = "mp4") -> NodeDefinition:
    return _versioned(aligned.external_definition(container), "external")


def built_in_definitions(leaf_count: int = 1) -> tuple[NodeDefinition, ...]:
    return (
        *(
            definition(role, leaf_count if role == "split" else 1)
            for role in NAMES
            if role != "external"
        ),
        *(external_definition(container) for container in ("mp4", "mov", "mkv")),
    )


def definition_role(value: NodeDefinition) -> str | None:
    if value.version != VERSION:
        return None
    if value.type_id.startswith(ATOMIC_SPLIT_TYPE_PREFIX):
        count = len(value.output_ports)
        if not 1 <= count <= 10000:
            return None
        return "split" if value == atomic_split_definition(count) else None
    for container in ("mp4", "mov", "mkv"):
        if value == external_definition(container):
            return "external"
    for role in NAMES:
        if role not in {"split", "external"} and value == definition(role):
            return role
    return None


def python_adapters() -> Mapping[str, PythonAdapter]:
    module = import_module("zniku.source_admission.adapters")
    return {
        f"zniku.source_admission.adapters:{name}": cast(PythonAdapter, getattr(module, name))
        for role, name in NAMES.items()
        if role not in {"enhancement", "fi", "external"}
    }


def validators() -> Mapping[str, NodeValidator]:
    module = import_module("zniku.source_admission.validators")
    return {
        f"zniku.source_admission.validators:validate_{name}": cast(
            NodeValidator, getattr(module, f"validate_{name}")
        )
        for name in NAMES.values()
    }
