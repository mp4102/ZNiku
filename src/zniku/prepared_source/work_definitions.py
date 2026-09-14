"""普通工作参考的薄版本目录；共享章节 Schema，不改变旧节点或 Runtime。

这里只替换 exact 与受信执行入口。帧区间、FI 相位、显式外部提交继续复用同一数学合同。
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib import import_module
from typing import cast

from zniku.graph import NodeDefinition, PythonExecutorSpec, ValidatorSpec
from zniku.runtime import NodeValidator, PythonAdapter

from . import definitions as previous
from .node_contracts import ATOMIC_SPLIT_TYPE_PREFIX, DeclaredContainer

OVERLAP_NODE_VERSION = "0.3.4-work.1"
ENHANCEMENT_TYPE_ID = previous.ENHANCEMENT_TYPE_ID
MERGE_VIDEO_TYPE_ID = previous.MERGE_VIDEO_TYPE_ID
FI_CONTEXT_TYPE_ID = previous.FI_CONTEXT_TYPE_ID
FRAME_INTERPOLATION_TYPE_ID = previous.FRAME_INTERPOLATION_TYPE_ID
FI_CROP_TYPE_ID = previous.FI_CROP_TYPE_ID
PROGRAM_ENCODE_TYPE_ID = previous.PROGRAM_ENCODE_TYPE_ID
FINAL_MUX_TYPE_ID = previous.FINAL_MUX_TYPE_ID
_MODULE = "zniku.prepared_source.work_execution"


def _versioned(base: NodeDefinition) -> NodeDefinition:
    executor = base.executor
    if isinstance(executor, PythonExecutorSpec):
        executor = executor.model_copy(
            update={"adapter": f"{_MODULE}:{executor.adapter.rsplit(':', 1)[-1]}"}
        )
    assert base.validator is not None
    return base.model_copy(
        update={
            "version": OVERLAP_NODE_VERSION,
            "executor": executor,
            "validator": ValidatorSpec(
                adapter=f"{_MODULE}:{base.validator.adapter.rsplit(':', 1)[-1]}"
            ),
        }
    )


@lru_cache(maxsize=32)
def _definition(role: str, count: int = 1) -> NodeDefinition:
    return _versioned(previous._definition(role, count))


def atomic_split_definition(count: int) -> NodeDefinition:
    previous.atomic_split_port_ids(count)
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


def external_definition(container: DeclaredContainer = "mp4") -> NodeDefinition:
    return _versioned(previous.external_definition(container))


def built_in_overlap_definitions(leaf_count: int = 1) -> tuple[NodeDefinition, ...]:
    previous.atomic_split_port_ids(leaf_count)
    return tuple(_definition(role, leaf_count) for role in previous._NAMES)


def definition_role(definition: NodeDefinition) -> str | None:
    """完整 definition 匹配才使用普通合同，不接受同名替换或跨版本冒认。"""
    if definition.version != OVERLAP_NODE_VERSION:
        return None
    if definition.type_id.startswith(ATOMIC_SPLIT_TYPE_PREFIX):
        count = len(definition.output_ports)
        if 1 <= count <= 10000 and definition == atomic_split_definition(count):
            return "split"
        return None
    for container in ("mp4", "mov", "mkv"):
        if definition == external_definition(container):
            return "external"
    for role in previous._NAMES:
        if role != "split" and definition == _definition(role):
            return role
    return None


def overlap_python_adapters() -> Mapping[str, PythonAdapter]:
    module = import_module(_MODULE)
    return {
        f"{_MODULE}:{name}": cast(PythonAdapter, getattr(module, name))
        for role, name in previous._NAMES.items()
        if role not in {"enhancement", "fi"}
    }


def overlap_validators() -> Mapping[str, NodeValidator]:
    module = import_module(_MODULE)
    return {
        f"{_MODULE}:validate_{name}": cast(NodeValidator, getattr(module, f"validate_{name}"))
        for name in (*previous._NAMES.values(), "external")
    }
