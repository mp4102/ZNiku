"""定义产品 Phase 2 的 Runtime 内建 Core Operator 静态合同。

Operator 只表达图结构、scope 变换和集合形状，不携带 Runtime 状态、路径、执行入口或媒体副作用。
Map 可以精确引用一个成员级 EngineBinding，但 operator 本身不是 Engine。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, JsonValue, field_serializer, field_validator, model_validator

from zniku.contracts import (
    ArtifactType,
    Cardinality,
    ContractModel,
    EngineBinding,
    JsonObject,
    MediaKind,
    PortSpec,
    Scope,
    StableId,
)
from zniku.contracts.base import ensure_no_executable_keys, freeze_json_object, thaw_json
from zniku.contracts.errors import fail

CORE_OPERATOR_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"
EXECUTABLE_WORKFLOW_CONTRACT_VERSION: Literal["0.2.0"] = "0.2.0"


class CoreOperatorKind(StrEnum):
    """首版闭合的 Runtime 图操作集合。"""

    PARTITION = "partition"
    MAP = "map"
    SELECT = "select"
    PASSTHROUGH = "passthrough"
    COLLECT = "collect"
    REDUCE = "reduce"


class CoreOperatorNodeSpec(ContractModel):
    """WorkflowSpec 中一个不执行媒体算法的 Core Operator node。"""

    kind: Literal["core_operator"]
    node_id: StableId
    operator_kind: CoreOperatorKind
    media_kind: MediaKind
    engine: EngineBinding | None = None
    parameters: JsonObject = Field(default_factory=dict)
    selected_member_ids: tuple[StableId, ...] = ()

    @field_validator("selected_member_ids", mode="before")
    @classmethod
    def normalize_members(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("parameters")
    @classmethod
    def freeze_parameters(cls, value: JsonObject) -> JsonObject:
        ensure_no_executable_keys(value)
        return freeze_json_object(value)

    @field_serializer("parameters")
    def serialize_parameters(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)

    @model_validator(mode="after")
    def validate_operator_fields(self) -> CoreOperatorNodeSpec:
        if self.media_kind is MediaKind.PROGRAM_MEDIA:
            fail("E_OPERATOR_MEDIA_KIND_UNSUPPORTED", "Core Operator 处理拆分后的媒体流")
        if self.operator_kind is CoreOperatorKind.MAP:
            if self.engine is None:
                fail("E_OPERATOR_MAP_ENGINE_REQUIRED", "Map 必须精确绑定成员级 Engine")
        elif self.engine is not None or self.parameters:
            fail("E_OPERATOR_ENGINE_FORBIDDEN", "只有 Map 可以携带 EngineBinding 和参数")
        if self.operator_kind is CoreOperatorKind.SELECT:
            if not self.selected_member_ids:
                fail("E_OPERATOR_SELECTOR_EMPTY", "Select 必须声明至少一个稳定 member ID")
            if len(self.selected_member_ids) != len(set(self.selected_member_ids)):
                fail("E_OPERATOR_SELECTOR_DUPLICATE", "Select member ID 不得重复")
        elif self.selected_member_ids:
            fail("E_OPERATOR_SELECTOR_FORBIDDEN", "只有 Select 可以声明 selected_member_ids")
        return self


def operator_input_ports(node: CoreOperatorNodeSpec) -> tuple[PortSpec, ...]:
    """返回 Core Operator 的 exact input ports。"""

    if node.operator_kind is CoreOperatorKind.PARTITION:
        return (_port("in", node.media_kind, Scope.PROGRAM, Cardinality.ONE),)
    if node.operator_kind is CoreOperatorKind.COLLECT:
        return (
            _port("processed", node.media_kind, Scope.CHAPTER, Cardinality.SET),
            _port("remainder", node.media_kind, Scope.CHAPTER, Cardinality.SET),
        )
    return (_port("in", node.media_kind, Scope.CHAPTER, Cardinality.SET),)


def operator_output_ports(node: CoreOperatorNodeSpec) -> tuple[PortSpec, ...]:
    """返回 Core Operator 的 exact output ports。"""

    if node.operator_kind is CoreOperatorKind.PARTITION:
        return (_port("out", node.media_kind, Scope.CHAPTER, Cardinality.SET),)
    if node.operator_kind is CoreOperatorKind.SELECT:
        return (
            _port("selected", node.media_kind, Scope.CHAPTER, Cardinality.SET),
            _port("remainder", node.media_kind, Scope.CHAPTER, Cardinality.SET),
        )
    if node.operator_kind is CoreOperatorKind.REDUCE:
        return (_port("out", node.media_kind, Scope.PROGRAM, Cardinality.ONE),)
    return (_port("out", node.media_kind, Scope.CHAPTER, Cardinality.SET),)


def _port(port_id: str, media_kind: MediaKind, scope: Scope, cardinality: Cardinality) -> PortSpec:
    return PortSpec(
        port_id=port_id,
        artifact_type=ArtifactType.MEDIA,
        media_kind=media_kind,
        scope=scope,
        cardinality=cardinality,
    )
