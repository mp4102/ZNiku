"""色彩政策独立 exact 节点目录；同 type 不同版本不能借用旧验证或覆盖旧合同。"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib import import_module
from typing import Literal, cast

from pydantic import JsonValue

from zniku.graph import NodeDefinition, PythonExecutorSpec, ValidatorSpec
from zniku.runtime import NodeValidator, PythonAdapter
from zniku.source_preparation import definitions as previous
from zniku.source_preparation.models import DiagnosticParameters, PreparationModel, SourceParameters
from zniku.source_preparation.process import fail

from .models import (
    SOURCE_PREPARATION_VERSION,
    AdmissionParameters,
    ExternalParameters,
    PrepareParameters,
)

SOURCE_TYPE_ID = previous.SOURCE_TYPE_ID
DIAGNOSTICS_TYPE_ID = previous.DIAGNOSTICS_TYPE_ID
BUILTIN_PREPARE_TYPE_ID = previous.BUILTIN_PREPARE_TYPE_ID
EXTERNAL_REPAIR_TYPE_PREFIX = previous.EXTERNAL_REPAIR_TYPE_PREFIX
ADMISSION_TYPE_ID = previous.ADMISSION_TYPE_ID


def _new(role: str, base: NodeDefinition, model: type[PreparationModel]) -> NodeDefinition:
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    executor = base.executor
    if isinstance(executor, PythonExecutorSpec):
        executor = executor.model_copy(update={"adapter": f"zniku.source_color.adapters:{role}"})
    validator = "prepared" if role in {"video_prepare", "external"} else role
    return base.model_copy(
        update={
            "version": SOURCE_PREPARATION_VERSION,
            "parameter_schema": cast(dict[str, JsonValue], schema),
            "executor": executor,
            "validator": ValidatorSpec(adapter=f"zniku.source_color.validators:{validator}"),
        }
    )


def source_definition() -> NodeDefinition:
    return _new("source", previous.source_definition(), SourceParameters)


def diagnostics_definition() -> NodeDefinition:
    return _new("diagnostics", previous.diagnostics_definition(), DiagnosticParameters)


def builtin_prepare_definition() -> NodeDefinition:
    return _new("video_prepare", previous.builtin_prepare_definition(), PrepareParameters)


def external_repair_definition(format: Literal["mkv", "mp4", "mov"] = "mkv") -> NodeDefinition:
    return _new("external", previous.external_repair_definition(format), ExternalParameters)


def admission_definition() -> NodeDefinition:
    return _new("admission", previous.admission_definition(), AdmissionParameters)


@lru_cache(maxsize=1)
def source_preparation_definitions() -> tuple[NodeDefinition, ...]:
    """与旧七定义并存的缓存目录。"""
    return (
        source_definition(),
        diagnostics_definition(),
        builtin_prepare_definition(),
        external_repair_definition("mkv"),
        external_repair_definition("mp4"),
        external_repair_definition("mov"),
        admission_definition(),
    )


def definition_role(definition: NodeDefinition) -> str | None:
    for expected, role in zip(
        source_preparation_definitions(),
        ("source", "diagnostics", "builtin", "external", "external", "external", "admission"),
        strict=True,
    ):
        if definition == expected:
            return role
    return None


def require_definition_role(definition: NodeDefinition, *allowed: str) -> str:
    role = definition_role(definition)
    if role not in allowed:
        raise fail("DEFINITION", "完整 exact definition 与新版受信入口不一致")
    assert role is not None
    return role


def register_source_preparation_adapters() -> Mapping[str, PythonAdapter]:
    module = import_module("zniku.source_color.adapters")
    return {
        f"zniku.source_color.adapters:{name}": cast(PythonAdapter, getattr(module, name))
        for name in ("source", "diagnostics", "video_prepare", "admission")
    }


def register_source_preparation_validators() -> Mapping[str, NodeValidator]:
    module = import_module("zniku.source_color.validators")
    return {
        f"zniku.source_color.validators:{name}": cast(NodeValidator, getattr(module, name))
        for name in ("source", "diagnostics", "prepared", "admission")
    }
