"""新增章级批量增强及其真实来源下游 exact 定义；旧定义字节与含义均不变。"""

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
    ValidatorSpec,
)
from zniku.runtime import NodeValidator, PythonAdapter
from zniku.source_admission import definitions as admitted

from .contracts import BATCH_PREFIX, ROLE_TYPES, VERSION, BatchParameters, port_ids


def definition(role: str, count: int = 1) -> NodeDefinition:
    """默认新模板使用本族；Source、Split 和可选 MR 继续已有准入身份。"""
    port_ids(count)
    if role == "split":
        return admitted.definition(role, count)
    if role not in {"enhancement", *ROLE_TYPES}:
        raise ValueError("未知章节批量节点职责")
    return _definition(role, count if role == "enhancement" else 1)


@lru_cache(maxsize=32)
def _definition(role: str, count: int) -> NodeDefinition:
    if role != "enhancement":
        document = admitted.definition(role).model_dump()
        document["type_id"] = ROLE_TYPES[role]
        if document["execution_mode"] == "automatic":
            document["executor"]["adapter"] = f"zniku.chapter_batch.adapters:{role}"
        document["validator"]["adapter"] = f"zniku.chapter_batch.validators:validate_{role}"
        return NodeDefinition.model_validate(document)
    schema = BatchParameters.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["properties"]["leaves"]["minItems"] = count
    schema["properties"]["leaves"]["maxItems"] = count
    ports = port_ids(count)
    return NodeDefinition(
        type_id=BATCH_PREFIX + str(count),
        version=VERSION,
        input_ports=(
            PortSpec(
                port_id="videos",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
        output_ports=tuple(PortSpec(port_id=port, data_type="VideoFile") for port in ports),
        parameter_schema=cast(dict[str, JsonValue], schema),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            output_paths=tuple(
                ExecutorOutputPathSpec(port_id=port, relative_path=f"{port}.enhancement.mov")
                for port in ports
            ),
            instructions="本章所有分叶共用同一增强设置。逐叶保持帧数、帧率与顺序；可分批收件，"
            "齐全并全部检查通过后一次显式提交整章。收到部分文件不是完成或节点内断点。",
        ),
        validator=ValidatorSpec(adapter="zniku.chapter_batch.validators:validate_enhancement"),
    )


def definition_role(value: NodeDefinition) -> str | None:
    """只认可完整精确定义，不从 type 前缀单独宣称能力。"""
    if value.version != VERSION:
        return None
    from .final_publish import is_definition as is_final_publish

    if is_final_publish(value):
        return "final"
    if value.type_id.startswith(BATCH_PREFIX):
        count = len(value.output_ports)
        return (
            "enhancement"
            if 1 <= count <= 10000 and value == definition("enhancement", count)
            else None
        )
    for role, type_id in ROLE_TYPES.items():
        if value.type_id == type_id:
            return role if value == definition(role) else None
    return None


def built_in_definitions(count: int = 1) -> tuple[NodeDefinition, ...]:
    from .final_publish import definition as final_publish_definition

    return (
        definition("enhancement", count),
        *(definition(role) for role in ROLE_TYPES),
        final_publish_definition(),
    )


def python_adapters() -> Mapping[str, PythonAdapter]:
    from .final_publish import ADAPTER, execute

    module = import_module("zniku.chapter_batch.adapters")
    return {
        ADAPTER: execute,
        **{
            f"zniku.chapter_batch.adapters:{role}": cast(PythonAdapter, getattr(module, role))
            for role in ROLE_TYPES
            if role != "fi"
        },
    }


def validators() -> Mapping[str, NodeValidator]:
    from .final_publish import VALIDATOR, validate

    module = import_module("zniku.chapter_batch.validators")
    return {
        VALIDATOR: validate,
        **{
            f"zniku.chapter_batch.validators:validate_{role}": cast(
                NodeValidator, getattr(module, f"validate_{role}")
            )
            for role in ("enhancement", *ROLE_TYPES)
        },
    }
