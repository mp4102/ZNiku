"""冻结 0.3.4 素材准备的 exact 节点、纯数据参数与真实执行器注册。"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib import import_module
from typing import Literal, cast

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

from .models import (
    SOURCE_PREPARATION_VERSION,
    AdmissionParameters,
    DiagnosticParameters,
    ExternalParameters,
    PreparationModel,
    PrepareParameters,
    SourceParameters,
)

SOURCE_TYPE_ID = "zniku.source_preparation.source"
DIAGNOSTICS_TYPE_ID = "zniku.source_preparation.diagnostics"
BUILTIN_PREPARE_TYPE_ID = "zniku.source_preparation.video_prepare.t1"
EXTERNAL_REPAIR_TYPE_PREFIX = "zniku.source_preparation.video_repair.external."
ADMISSION_TYPE_ID = "zniku.source_preparation.admission"


def _port(name: str, kind: str = "MediaFile", *, many: bool = False) -> PortSpec:
    return PortSpec(
        port_id=name,
        data_type=kind,
        required=True,
        cardinality=Cardinality.ORDERED_MANY if many else Cardinality.ONE,
    )


def source_definition() -> NodeDefinition:
    """返回只读原件入口的精确定义，不要求原件先满足工作参考准入。"""
    return _definition("source")


def diagnostics_definition() -> NodeDefinition:
    """返回完整诊断节点，正常与有问题均可产生报告，检查失败不产生成功报告。"""
    return _definition("diagnostics")


def builtin_prepare_definition() -> NodeDefinition:
    """声明窄 T1 能力；策略能否执行仍受代码中的发布晋级开关限制。"""
    return _definition("video_prepare")


def external_repair_definition(format: Literal["mkv", "mp4", "mov"] = "mkv") -> NodeDefinition:
    """每种外部封装使用独立单输出定义，不靠改后缀改变媒体声明。"""
    if format not in {"mkv", "mp4", "mov"}:
        raise ValueError("外部修复格式只接受 mkv/mp4/mov")
    return _definition("external", format)


def admission_definition() -> NodeDefinition:
    """返回四角色输入型准入，不预分配未来 VideoFile Artifact 身份。"""
    return _definition("admission")


def _definition(role: str, format: str = "mkv") -> NodeDefinition:
    models: dict[str, type[PreparationModel]] = {
        "source": SourceParameters,
        "diagnostics": DiagnosticParameters,
        "video_prepare": PrepareParameters,
        "external": ExternalParameters,
        "admission": AdmissionParameters,
    }
    types = {
        "source": SOURCE_TYPE_ID,
        "diagnostics": DIAGNOSTICS_TYPE_ID,
        "video_prepare": BUILTIN_PREPARE_TYPE_ID,
        "external": EXTERNAL_REPAIR_TYPE_PREFIX + format,
        "admission": ADMISSION_TYPE_ID,
    }
    inputs: tuple[PortSpec, ...] = () if role == "source" else (_port("original_media"),)
    if role in {"video_prepare", "external", "admission"}:
        inputs += (_port("diagnosis", "DataFile"),)
    if role == "admission":
        inputs += (_port("reference_media"), _port("audio_sources", many=True))
    ports = (
        (("diagnosis", "DataFile", "reports/source-check.json"),)
        if role == "diagnostics"
        else (
            (
                ("video", "VideoFile", "reference.mkv"),
                ("gate", "DataFile", "reports/admission.json"),
            )
            if role == "admission"
            else (("media", "MediaFile", f"source-prepared.{format}"),)
        )
    )
    output_paths = tuple(
        ExecutorOutputPathSpec(port_id=p, relative_path=path) for p, _, path in ports
    )
    external = role == "external"
    validator_role = "prepared" if role in {"external", "video_prepare"} else role
    schema = models[role].model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return NodeDefinition(
        type_id=types[role],
        version=SOURCE_PREPARATION_VERSION,
        input_ports=inputs,
        output_ports=tuple(PortSpec(port_id=p, data_type=kind) for p, kind, _ in ports),
        parameter_schema=cast(dict[str, JsonValue], schema),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL if external else ExecutionMode.AUTOMATIC,
        executor=(
            ManualExternalExecutorSpec(
                output_paths=output_paths,
                instructions="保留原件。导入保持完整展示帧、像素、音轨和音画关系的修复副本；文件出现不会自动提交。",
            )
            if external
            else PythonExecutorSpec(
                adapter=f"zniku.source_preparation.adapters:{role}",
                output_paths=output_paths,
            )
        ),
        validator=ValidatorSpec(adapter=f"zniku.source_preparation.validators:{validator_role}"),
    )


@lru_cache(maxsize=1)
def source_preparation_definitions() -> tuple[NodeDefinition, ...]:
    """返回缓存的闭合定义目录，避免展示轮询重复构建 Schema。"""
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
    """闭合匹配完整 definition shape；不得只凭 type/version 接受被改写的执行器。"""
    roles = ("source", "diagnostics", "builtin", "external", "external", "external", "admission")
    for expected, role in zip(source_preparation_definitions(), roles, strict=True):
        if definition == expected:
            return role
    return None


def require_definition_role(definition: NodeDefinition, *allowed: str) -> str:
    """受信执行入口先检查完整定义，防止自定义执行器借用局部准入扩展权限。

    validator 名称不是身份凭证。端口、参数 Schema、执行模式及执行器有任何漂移都必须拒绝，
    不能让手工提交的 JSON 借用诊断 validator 伪装成受信扫描结果。
    """
    from .process import fail

    role = definition_role(definition)
    if role not in allowed:
        raise fail("DEFINITION", "节点定义与此受信入口的完整 exact 定义不一致")
    assert role is not None
    return role


def register_source_preparation_adapters() -> Mapping[str, PythonAdapter]:
    """仅注册本版本静态 Python adapter，不接收操作者可执行代码。"""
    module = import_module("zniku.source_preparation.adapters")
    return {
        f"zniku.source_preparation.adapters:{name}": cast(PythonAdapter, getattr(module, name))
        for name in ("source", "diagnostics", "video_prepare", "admission")
    }


def register_source_preparation_validators() -> Mapping[str, NodeValidator]:
    """注册完整定义身份受限的 validator；名称本身不授予诊断或准入权限。"""
    module = import_module("zniku.source_preparation.validators")
    return {
        f"zniku.source_preparation.validators:{name}": cast(NodeValidator, getattr(module, name))
        for name in ("source", "diagnostics", "prepared", "admission")
    }
