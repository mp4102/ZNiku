"""声明采用显式工作色彩解释的新 exact 下游节点，不借用旧 validator 身份。"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib import import_module
from typing import cast

from zniku.graph import NodeDefinition, PythonExecutorSpec, ValidatorSpec
from zniku.prepared_source import definitions as previous
from zniku.prepared_source.node_contracts import (
    ATOMIC_SPLIT_TYPE_PREFIX as ATOMIC_SPLIT_TYPE_PREFIX,
)
from zniku.prepared_source.node_contracts import DeclaredContainer
from zniku.runtime import NodeValidator, PythonAdapter

OVERLAP_NODE_VERSION = "0.3.4-color.1"
ENHANCEMENT_TYPE_ID = previous.ENHANCEMENT_TYPE_ID
MERGE_VIDEO_TYPE_ID = previous.MERGE_VIDEO_TYPE_ID
FI_CONTEXT_TYPE_ID = previous.FI_CONTEXT_TYPE_ID
FRAME_INTERPOLATION_TYPE_ID = previous.FRAME_INTERPOLATION_TYPE_ID
FI_CROP_TYPE_ID = previous.FI_CROP_TYPE_ID
PROGRAM_ENCODE_TYPE_ID = previous.PROGRAM_ENCODE_TYPE_ID
FINAL_MUX_TYPE_ID = previous.FINAL_MUX_TYPE_ID


@lru_cache(maxsize=32)
def _definition(role: str, count: int = 1) -> NodeDefinition:
    """参数数学未改变；新版本始终绑定自身执行器及完成 validator。"""
    old = previous.atomic_split_definition(count) if role == "split" else previous._definition(role)
    executor = old.executor
    if isinstance(executor, PythonExecutorSpec):
        executor = executor.model_copy(
            update={"adapter": executor.adapter.replace("prepared_source", "prepared_color")}
        )
    assert old.validator is not None
    return old.model_copy(
        update={
            "version": OVERLAP_NODE_VERSION,
            "executor": executor,
            "validator": ValidatorSpec(
                adapter=old.validator.adapter.replace("prepared_source", "prepared_color")
            ),
        }
    )


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
    old = previous.external_definition(container)
    return old.model_copy(
        update={
            "version": OVERLAP_NODE_VERSION,
            "validator": ValidatorSpec(adapter="zniku.prepared_color.validators:validate_external"),
        }
    )


def built_in_overlap_definitions(leaf_count: int = 1) -> tuple[NodeDefinition, ...]:
    previous.atomic_split_port_ids(leaf_count)
    return tuple(
        _definition(role, leaf_count if role == "split" else 1) for role in previous._NAMES
    )


def definition_role(definition: NodeDefinition) -> str | None:
    """完整 shape 相等才识别；同 typeID 旧版本或自定义 executor 不获得新语义。"""
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
    module = import_module("zniku.prepared_color.adapters")
    return {
        f"zniku.prepared_color.adapters:{name}": cast(PythonAdapter, getattr(module, name))
        for role, name in previous._NAMES.items()
        if role not in {"enhancement", "fi"}
    }


def overlap_validators() -> Mapping[str, NodeValidator]:
    module = import_module("zniku.prepared_color.validators")
    return {
        f"zniku.prepared_color.validators:validate_{name}": cast(
            NodeValidator, getattr(module, f"validate_{name}")
        )
        for name in (*previous._NAMES.values(), "external")
    }
