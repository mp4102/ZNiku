"""普通工作路线的薄 exact 定义；共用端口形状但不改变旧策略或执行入口。"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib import import_module
from typing import Literal, cast

from pydantic import JsonValue

from zniku.graph import (
    ManualExternalExecutorSpec,
    NodeDefinition,
    PythonExecutorSpec,
    ValidatorSpec,
)
from zniku.runtime import NodeValidator, PythonAdapter

from . import definitions as old
from .models import PreparationModel
from .process import fail
from .work_models import (
    SOURCE_PREPARATION_VERSION,
    AdmissionParameters,
    DiagnosticParameters,
    ExternalParameters,
    PrepareParameters,
    SourceParameters,
)

SOURCE_TYPE_ID = old.SOURCE_TYPE_ID
DIAGNOSTICS_TYPE_ID = old.DIAGNOSTICS_TYPE_ID
BUILTIN_PREPARE_TYPE_ID = "zniku.source_preparation.video_prepare.frame_retime"
EXTERNAL_REPAIR_TYPE_PREFIX = "zniku.source_preparation.work_reference.external."
ADMISSION_TYPE_ID = old.ADMISSION_TYPE_ID


@lru_cache(maxsize=1)
def source_preparation_definitions() -> tuple[NodeDefinition, ...]:
    """只复用固定端口及输出布局，替换 exact/Schema/受信入口与人工声明。"""
    result: list[NodeDefinition] = []
    models: tuple[type[PreparationModel], ...] = (
        SourceParameters,
        DiagnosticParameters,
        PrepareParameters,
        ExternalParameters,
        ExternalParameters,
        ExternalParameters,
        AdmissionParameters,
    )
    roles = (
        "source",
        "diagnostics",
        "video_prepare",
        "external",
        "external",
        "external",
        "admission",
    )
    for base, model, role in zip(old.source_preparation_definitions(), models, roles, strict=True):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        type_id = base.type_id
        executor: PythonExecutorSpec | ManualExternalExecutorSpec
        if role == "external":
            type_id = EXTERNAL_REPAIR_TYPE_PREFIX + base.type_id.rsplit(".", 1)[-1]
            executor = ManualExternalExecutorSpec(
                output_paths=base.executor.output_paths,
                instructions="导入新的完整工作参考。允许帧数和时长变化，将重新规划并使用新参考自身音频；不声称保内容修复。文件出现不会自动提交。",
            )
        else:
            if role == "video_prepare":
                type_id = BUILTIN_PREPARE_TYPE_ID
            executor = PythonExecutorSpec(
                adapter=f"zniku.source_preparation.work_adapters:{role}",
                output_paths=base.executor.output_paths,
            )
        validator = "prepared" if role in {"external", "video_prepare"} else role
        result.append(
            base.model_copy(
                update={
                    "type_id": type_id,
                    "version": SOURCE_PREPARATION_VERSION,
                    "parameter_schema": cast(dict[str, JsonValue], schema),
                    "executor": executor,
                    "validator": ValidatorSpec(
                        adapter=f"zniku.source_preparation.work_validators:{validator}"
                    ),
                }
            )
        )
    return tuple(result)


def source_definition() -> NodeDefinition:
    return source_preparation_definitions()[0]


def diagnostics_definition() -> NodeDefinition:
    return source_preparation_definitions()[1]


def builtin_prepare_definition() -> NodeDefinition:
    return source_preparation_definitions()[2]


def external_repair_definition(format: Literal["mkv", "mp4", "mov"] = "mkv") -> NodeDefinition:
    if format not in {"mkv", "mp4", "mov"}:
        raise ValueError("外部工作参考仅支持 MKV/MP4/MOV")
    return source_preparation_definitions()[3 + ("mkv", "mp4", "mov").index(format)]


def admission_definition() -> NodeDefinition:
    return source_preparation_definitions()[6]


def definition_role(definition: NodeDefinition) -> str | None:
    roles = ("source", "diagnostics", "builtin", "external", "external", "external", "admission")
    return next(
        (
            role
            for expected, role in zip(source_preparation_definitions(), roles, strict=True)
            if definition == expected
        ),
        None,
    )


def require_definition_role(definition: NodeDefinition, *allowed: str) -> str:
    """完整 exact shape 是受信入口边界；不能只凭自定义节点使用同一 validator。"""
    role = definition_role(definition)
    if role not in allowed:
        raise fail("DEFINITION", "当前定义不是此普通工作源受信入口的完整 exact")
    assert role is not None
    return role


def register_source_preparation_adapters() -> Mapping[str, PythonAdapter]:
    module = import_module("zniku.source_preparation.work_adapters")
    return {
        f"zniku.source_preparation.work_adapters:{name}": cast(PythonAdapter, getattr(module, name))
        for name in ("source", "diagnostics", "video_prepare", "admission")
    }


def register_source_preparation_validators() -> Mapping[str, NodeValidator]:
    module = import_module("zniku.source_preparation.work_validators")
    return {
        f"zniku.source_preparation.work_validators:{name}": cast(
            NodeValidator, getattr(module, name)
        )
        for name in ("source", "diagnostics", "prepared", "admission")
    }
