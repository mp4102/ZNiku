"""定义 typed port、基数以及不含隐式 scope 转换的兼容规则。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from .base import ContractModel
from .errors import ContractViolation
from .identifiers import StableId
from .media import ArtifactType, MediaKind, Scope, _validate_classification


class Cardinality(StrEnum):
    """端口绑定单值、可选单值或正式集合。"""

    ONE = "one"
    OPTIONAL = "optional"
    SET = "set"


class PortSpec(ContractModel):
    """Engine 端口的稳定 ID、类型、scope 与基数声明。"""

    port_id: StableId
    artifact_type: ArtifactType
    media_kind: MediaKind | None
    scope: Scope
    cardinality: Cardinality

    @model_validator(mode="after")
    def validate_classification(self) -> PortSpec:
        _validate_classification(self.artifact_type, self.media_kind, entity="PortSpec")
        return self


def assert_ports_compatible(output: PortSpec, input_: PortSpec) -> None:
    """确认一条 output→input 连接满足类型、scope 与基数的精确规则。"""

    if output.artifact_type is not input_.artifact_type:
        raise ContractViolation(
            "E_PORT_ARTIFACT_TYPE_INCOMPATIBLE",
            f"{output.port_id} 的 {output.artifact_type.value} 不能连接到 "
            f"{input_.port_id} 的 {input_.artifact_type.value}",
        )
    if output.media_kind is not input_.media_kind:
        raise ContractViolation(
            "E_PORT_MEDIA_KIND_INCOMPATIBLE",
            f"{output.port_id} 与 {input_.port_id} 的 media_kind 不兼容",
        )
    if output.scope is not input_.scope:
        raise ContractViolation(
            "E_PORT_SCOPE_INCOMPATIBLE",
            f"{output.port_id} 的 {output.scope.value} scope 不能隐式转换为 {input_.scope.value}",
        )

    compatible_cardinality = {
        Cardinality.ONE: {Cardinality.ONE, Cardinality.OPTIONAL},
        Cardinality.OPTIONAL: {Cardinality.OPTIONAL},
        Cardinality.SET: {Cardinality.SET},
    }
    if input_.cardinality not in compatible_cardinality[output.cardinality]:
        raise ContractViolation(
            "E_PORT_CARDINALITY_INCOMPATIBLE",
            f"{output.cardinality.value} output 不能连接到 {input_.cardinality.value} input",
        )


def ports_compatible(output: PortSpec, input_: PortSpec) -> bool:
    """为预览界面提供无副作用兼容性布尔结果，正式失败仍由 assert 函数给出。"""

    try:
        assert_ports_compatible(output, input_)
    except ContractViolation:
        return False
    return True
