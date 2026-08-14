"""定义 EngineManifest 及其输入、输出媒体合同。

Manifest 只声明稳定身份、精确版本、typed ports、参数和媒体语义，不包含 entrypoint、命令行、环境
变量或可执行位置。真正的安装发现、allowlist 与调用方式属于未来 Registry 和 Runtime policy。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    Field,
    JsonValue,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from .base import (
    ContractModel,
    JsonObject,
    ensure_no_executable_keys,
    freeze_json_object,
    thaw_json,
)
from .errors import ContractViolation, fail
from .identifiers import AttributePath, EngineId, ExactVersion, StableId
from .media import Artifact, ArtifactSet, ArtifactType, Scope
from .ports import Cardinality, PortSpec
from .schema import JSON_SCHEMA_DIALECT, validate_json_instance, validate_schema_document

DisplayName = Annotated[str, StringConstraints(min_length=1, max_length=120)]


def _open_media_schema() -> dict[str, JsonValue]:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }


class ExecutionMode(StrEnum):
    """首批 Engine 执行方式；远程执行尚未进入合同。"""

    AUTOMATIC = "automatic"
    MANUAL_EXTERNAL = "manual_external"


class EngineLifecycleCapabilities(ContractModel):
    """显式声明 Engine 是否提供验收、发布与恢复集成能力。"""

    supports_acceptance: bool
    supports_publication: bool
    supports_recovery: bool


class MediaAttributeDisposition(StrEnum):
    """Engine 对某个输出媒体属性的声明。"""

    PRESERVED = "preserved"
    CHANGED = "changed"
    NOT_GUARANTEED = "not_guaranteed"


class MediaAttributeSource(ContractModel):
    """指出输出属性声明所引用的直接输入端口与属性路径。"""

    port_id: StableId
    path: AttributePath


class MediaAttributeRule(ContractModel):
    """说明一个输出属性保持、改变或无法保证。"""

    path: AttributePath
    disposition: MediaAttributeDisposition
    source: MediaAttributeSource | None = None

    @model_validator(mode="after")
    def validate_source(self) -> MediaAttributeRule:
        if self.disposition is MediaAttributeDisposition.PRESERVED and self.source is None:
            fail("E_MEDIA_RULE_SOURCE_REQUIRED", "preserved 属性必须引用一个直接输入属性")
        return self


class EngineInputContract(ContractModel):
    """将输入 PortSpec 与 Artifact 属性前置条件绑定。"""

    port: PortSpec
    preconditions_schema: JsonObject = Field(default_factory=_open_media_schema)

    @field_validator("preconditions_schema")
    @classmethod
    def validate_and_freeze_schema(cls, value: JsonObject) -> JsonObject:
        validate_schema_document(value, purpose="media")
        return freeze_json_object(value)

    @field_serializer("preconditions_schema")
    def serialize_schema(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)

    def validate_artifact_attributes(self, artifact: Artifact) -> None:
        """确认已完成粗粒度端口匹配的 Artifact 满足输入属性条件。"""

        validate_json_instance(
            artifact.attributes,
            self.preconditions_schema,
            code="E_INPUT_MEDIA_CONTRACT_VIOLATION",
        )


class EngineOutputContract(ContractModel):
    """将输出 PortSpec、Artifact 属性保证和媒体属性变化声明绑定。"""

    port: PortSpec
    guarantees_schema: JsonObject = Field(default_factory=_open_media_schema)
    attribute_rules: tuple[MediaAttributeRule, ...] = ()

    @field_validator("attribute_rules", mode="before")
    @classmethod
    def normalize_rules(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("guarantees_schema")
    @classmethod
    def validate_and_freeze_schema(cls, value: JsonObject) -> JsonObject:
        validate_schema_document(value, purpose="media")
        return freeze_json_object(value)

    @field_serializer("guarantees_schema")
    def serialize_schema(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)

    @model_validator(mode="after")
    def validate_rules(self) -> EngineOutputContract:
        paths = tuple(rule.path for rule in self.attribute_rules)
        if len(paths) != len(set(paths)):
            fail("E_MEDIA_RULE_DUPLICATE", "同一输出端口不得重复声明属性路径")
        for index, path in enumerate(paths):
            for other in paths[index + 1 :]:
                if path.startswith(f"{other}.") or other.startswith(f"{path}."):
                    fail("E_MEDIA_RULE_PATH_OVERLAP", "属性规则不得同时声明祖先和子孙路径")
        if self.port.artifact_type is not ArtifactType.MEDIA and self.attribute_rules:
            fail("E_MEDIA_RULE_NON_MEDIA", "非 media 输出不得声明媒体属性变化")
        return self

    def validate_artifact_attributes(self, artifact: Artifact) -> None:
        """确认一个已产生 Artifact 满足 Engine 对输出属性的保证。"""

        validate_json_instance(
            artifact.attributes,
            self.guarantees_schema,
            code="E_OUTPUT_MEDIA_CONTRACT_VIOLATION",
        )


class EngineManifest(ContractModel):
    """Engine 身份、版本、端口、scope、参数与声明式媒体能力的唯一合同。"""

    contract_version: Literal["0.1.0"]
    engine_id: EngineId
    engine_version: ExactVersion
    display_name: DisplayName
    execution_mode: ExecutionMode
    lifecycle: EngineLifecycleCapabilities
    supported_scopes: tuple[Scope, ...]
    inputs: tuple[EngineInputContract, ...]
    outputs: tuple[EngineOutputContract, ...]
    parameter_schema: JsonObject

    @field_validator("supported_scopes", "inputs", "outputs", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("display_name")
    @classmethod
    def reject_blank_display_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("E_DISPLAY_NAME_BLANK: display_name 不得只包含空白")
        return value

    @field_validator("parameter_schema")
    @classmethod
    def validate_and_freeze_parameter_schema(cls, value: JsonObject) -> JsonObject:
        validate_schema_document(value, purpose="parameters")
        return freeze_json_object(value)

    @field_serializer("parameter_schema")
    def serialize_parameter_schema(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)

    @model_validator(mode="after")
    def validate_manifest_relations(self) -> EngineManifest:
        if not self.supported_scopes:
            fail("E_ENGINE_SCOPE_EMPTY", "supported_scopes 至少包含一个 scope")
        if len(self.supported_scopes) != len(set(self.supported_scopes)):
            fail("E_ENGINE_SCOPE_DUPLICATE", "supported_scopes 不得重复")
        if len(self.supported_scopes) != 1:
            fail("E_ENGINE_SCOPE_AMBIGUOUS", "0.1.0 manifest 必须固定到一个精确 scope")
        if not self.inputs:
            fail("E_ENGINE_INPUT_EMPTY", "媒体 Engine 至少声明一个输入端口")
        if not self.outputs:
            fail("E_ENGINE_OUTPUT_EMPTY", "媒体 Engine 至少声明一个输出端口")

        input_ports = tuple(contract.port for contract in self.inputs)
        output_ports = tuple(contract.port for contract in self.outputs)
        ports = (*input_ports, *output_ports)
        port_ids = tuple(port.port_id for port in ports)
        if len(port_ids) != len(set(port_ids)):
            fail("E_ENGINE_PORT_DUPLICATE", "input/output port_id 必须在 manifest 内全局唯一")

        supported = set(self.supported_scopes)
        for port in ports:
            if port.scope not in supported:
                fail(
                    "E_ENGINE_PORT_SCOPE_UNSUPPORTED",
                    f"端口 {port.port_id} 使用了未声明的 {port.scope.value} scope",
                )

        input_contracts = {contract.port.port_id: contract for contract in self.inputs}
        for output in self.outputs:
            for rule in output.attribute_rules:
                if rule.source is None:
                    continue
                source = input_contracts.get(rule.source.port_id)
                if source is None:
                    fail(
                        "E_MEDIA_RULE_SOURCE_UNKNOWN",
                        f"属性规则引用了未知 input port：{rule.source.port_id}",
                    )
                if source.port.artifact_type is not ArtifactType.MEDIA:
                    fail("E_MEDIA_RULE_SOURCE_NON_MEDIA", "媒体属性来源必须是 media input port")
                if rule.disposition is MediaAttributeDisposition.PRESERVED:
                    source_shape = (
                        source.port.artifact_type,
                        source.port.media_kind,
                        source.port.scope,
                        source.port.cardinality,
                    )
                    output_shape = (
                        output.port.artifact_type,
                        output.port.media_kind,
                        output.port.scope,
                        output.port.cardinality,
                    )
                    if source_shape != output_shape:
                        fail(
                            "E_MEDIA_RULE_PRESERVED_SHAPE",
                            "0.1.0 preserved 规则要求输入与输出端口形状完全一致",
                        )
        return self

    def validate_parameters(self, parameters: JsonObject) -> None:
        """按 manifest 的闭合 Schema 校验参数，不静默注入 default。"""

        ensure_no_executable_keys(parameters)
        validate_json_instance(parameters, self.parameter_schema, code="E_PARAMETER_INVALID")

    def input_contract(self, port_id: str) -> EngineInputContract:
        """按稳定 ID 取得输入合同；未知 ID 失败关闭。"""

        for contract in self.inputs:
            if contract.port.port_id == port_id:
                return contract
        raise ContractViolation("E_ENGINE_INPUT_PORT_UNKNOWN", f"未知 input port：{port_id}")

    def output_contract(self, port_id: str) -> EngineOutputContract:
        """按稳定 ID 取得输出合同；未知 ID 失败关闭。"""

        for contract in self.outputs:
            if contract.port.port_id == port_id:
                return contract
        raise ContractViolation("E_ENGINE_OUTPUT_PORT_UNKNOWN", f"未知 output port：{port_id}")


def validate_contract_artifact(
    contract: EngineInputContract | EngineOutputContract,
    artifact: Artifact | ArtifactSet,
) -> None:
    """验证一个 Artifact/ArtifactSet 的粗粒度端口类型和成员属性合同。"""

    port = contract.port
    if port.cardinality is Cardinality.SET and not isinstance(artifact, ArtifactSet):
        raise ContractViolation(
            "E_BINDING_CARDINALITY_MISMATCH", f"set port {port.port_id} 必须绑定 ArtifactSet"
        )
    if port.cardinality is not Cardinality.SET and not isinstance(artifact, Artifact):
        raise ContractViolation(
            "E_BINDING_CARDINALITY_MISMATCH",
            f"{port.cardinality.value} port {port.port_id} 必须绑定单一 Artifact",
        )
    if artifact.artifact_type is not port.artifact_type:
        raise ContractViolation(
            "E_BINDING_ARTIFACT_TYPE_MISMATCH", f"{port.port_id} 的 artifact_type 不匹配"
        )
    if artifact.media_kind is not port.media_kind:
        raise ContractViolation(
            "E_BINDING_MEDIA_KIND_MISMATCH", f"{port.port_id} 的 media_kind 不匹配"
        )
    if artifact.scope is not port.scope:
        raise ContractViolation("E_BINDING_SCOPE_MISMATCH", f"{port.port_id} 的 scope 不匹配")

    values = (
        (artifact,)
        if isinstance(artifact, Artifact)
        else tuple(member.artifact for member in artifact.members)
    )
    for value in values:
        contract.validate_artifact_attributes(value)
