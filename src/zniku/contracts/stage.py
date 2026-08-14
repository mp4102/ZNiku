"""定义 StageRun 的最小身份、Engine 绑定和直接端口引用。

本模块不定义 pending/ready/running/complete 等状态，也不生成 receipt、Evidence 或副作用。输出绑定
可以为空；空值只表示当前记录没有绑定输出，不得被解释为任何 Runtime 状态。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Literal, cast

from pydantic import Field, JsonValue, field_serializer, field_validator, model_validator

from .base import (
    ContractModel,
    JsonObject,
    ensure_no_executable_keys,
    freeze_json_object,
    json_values_equal,
    thaw_json,
)
from .engine import (
    EngineInputContract,
    EngineManifest,
    EngineOutputContract,
    MediaAttributeDisposition,
    validate_contract_artifact,
)
from .errors import ContractViolation, fail
from .identifiers import EngineId, ExactVersion, Sha256Digest, StableId
from .media import Artifact, ArtifactSet, Scope
from .ports import Cardinality


class EngineBinding(ContractModel):
    """将 StageRun 固定到精确 Engine 版本与 manifest digest。"""

    engine_id: EngineId
    engine_version: ExactVersion
    manifest_digest: Sha256Digest

    @classmethod
    def from_manifest(cls, manifest: EngineManifest) -> EngineBinding:
        """从已校验 manifest 生成无歧义绑定。"""

        return cls(
            engine_id=manifest.engine_id,
            engine_version=manifest.engine_version,
            manifest_digest=manifest.sha256_digest(),
        )


class ArtifactRef(ContractModel):
    """单一 Artifact 的稳定引用。"""

    kind: Literal["artifact"] = "artifact"
    artifact_id: StableId


class ArtifactSetRef(ContractModel):
    """完整 ArtifactSet 的稳定引用。"""

    kind: Literal["artifact_set"] = "artifact_set"
    artifact_set_id: StableId


BindingTarget = Annotated[ArtifactRef | ArtifactSetRef, Field(discriminator="kind")]


class PortBinding(ContractModel):
    """将一个 manifest port 绑定到单值或集合身份。"""

    port_id: StableId
    target: BindingTarget


class StageRun(ContractModel):
    """某个冻结 Stage 在特定 scope target 上的一次最小执行身份记录。"""

    contract_version: Literal["0.1.0"]
    stage_run_id: StableId
    workflow_run_id: StableId
    stage_spec_id: StableId
    scope: Scope
    scope_id: StableId
    engine: EngineBinding
    parameters: JsonObject
    inputs: tuple[PortBinding, ...]
    outputs: tuple[PortBinding, ...] = ()

    @field_validator("inputs", "outputs", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
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
    def validate_binding_ids(self) -> StageRun:
        input_ids = tuple(binding.port_id for binding in self.inputs)
        output_ids = tuple(binding.port_id for binding in self.outputs)
        if len(input_ids) != len(set(input_ids)):
            fail("E_STAGE_INPUT_BINDING_DUPLICATE", "input port 只能绑定一次")
        if len(output_ids) != len(set(output_ids)):
            fail("E_STAGE_OUTPUT_BINDING_DUPLICATE", "output port 只能绑定一次")
        if set(input_ids) & set(output_ids):
            fail("E_STAGE_BINDING_DIRECTION_AMBIGUOUS", "同一 port_id 不得同时作为输入与输出")
        return self


ArtifactLookup = Mapping[str, Artifact | ArtifactSet]


def validate_stage_run_bindings(
    stage_run: StageRun,
    manifest: EngineManifest,
    artifacts: ArtifactLookup,
) -> None:
    """校验 StageRun 与精确 manifest、参数和实际 Artifact authority 的全部绑定关系。"""

    expected_engine = EngineBinding.from_manifest(manifest)
    if stage_run.engine != expected_engine:
        raise ContractViolation(
            "E_STAGE_ENGINE_BINDING_MISMATCH",
            "StageRun 的 engine_id、version 或 manifest digest 与给定 manifest 不一致",
        )
    if stage_run.scope not in manifest.supported_scopes:
        raise ContractViolation(
            "E_STAGE_SCOPE_UNSUPPORTED", f"Engine 不支持 {stage_run.scope.value} StageRun"
        )

    manifest.validate_parameters(stage_run.parameters)
    input_artifacts = _validate_direction(
        stage_run.inputs,
        contracts={contract.port.port_id: contract for contract in manifest.inputs},
        artifacts=artifacts,
        stage_run=stage_run,
        direction="input",
        require_bindings=True,
    )
    output_artifacts = _validate_direction(
        stage_run.outputs,
        contracts={contract.port.port_id: contract for contract in manifest.outputs},
        artifacts=artifacts,
        stage_run=stage_run,
        direction="output",
        require_bindings=False,
    )

    if set(_bound_identity_ids(input_artifacts.values())) & set(
        _bound_identity_ids(output_artifacts.values())
    ):
        raise ContractViolation(
            "E_STAGE_INPUT_OUTPUT_IDENTITY_CONFLICT",
            "同一 Artifact、ArtifactSet 或集合成员身份不得同时作为输入和输出",
        )
    _validate_preserved_rules(manifest, input_artifacts, output_artifacts)


def _validate_direction(
    bindings: tuple[PortBinding, ...],
    *,
    contracts: Mapping[str, EngineInputContract | EngineOutputContract],
    artifacts: ArtifactLookup,
    stage_run: StageRun,
    direction: Literal["input", "output"],
    require_bindings: bool,
) -> dict[str, Artifact | ArtifactSet]:
    resolved: dict[str, Artifact | ArtifactSet] = {}
    bound_ids = {binding.port_id for binding in bindings}
    if require_bindings:
        missing = tuple(
            port_id
            for port_id, contract in contracts.items()
            if contract.port.cardinality is not Cardinality.OPTIONAL and port_id not in bound_ids
        )
        if missing:
            raise ContractViolation(
                "E_STAGE_REQUIRED_INPUT_MISSING", f"缺少必需 input binding：{', '.join(missing)}"
            )

    for binding in bindings:
        contract = contracts.get(binding.port_id)
        if contract is None:
            raise ContractViolation(
                f"E_STAGE_{direction.upper()}_PORT_UNKNOWN",
                f"StageRun 绑定了 manifest 未声明的 {direction} port：{binding.port_id}",
            )
        if contract.port.scope is not stage_run.scope:
            raise ContractViolation(
                "E_STAGE_PORT_SCOPE_MISMATCH",
                f"{binding.port_id} 的 scope 与 StageRun scope 不一致",
            )

        target_id = (
            binding.target.artifact_id
            if isinstance(binding.target, ArtifactRef)
            else binding.target.artifact_set_id
        )
        if contract.port.cardinality is Cardinality.SET:
            if not isinstance(binding.target, ArtifactSetRef):
                raise ContractViolation(
                    "E_STAGE_BINDING_CARDINALITY_MISMATCH",
                    f"set port {binding.port_id} 必须绑定 ArtifactSetRef",
                )
        elif not isinstance(binding.target, ArtifactRef):
            raise ContractViolation(
                "E_STAGE_BINDING_CARDINALITY_MISMATCH",
                f"{contract.port.cardinality.value} port {binding.port_id} 必须绑定 ArtifactRef",
            )
        artifact = artifacts.get(target_id)
        if artifact is None:
            raise ContractViolation("E_STAGE_ARTIFACT_UNKNOWN", f"找不到绑定对象：{target_id}")
        authority_id = (
            artifact.artifact_id if isinstance(artifact, Artifact) else artifact.artifact_set_id
        )
        if authority_id != target_id:
            raise ContractViolation(
                "E_STAGE_ARTIFACT_ID_MISMATCH",
                f"binding target {target_id} 与对象 authority ID {authority_id} 不一致",
            )
        if artifact.scope_id != stage_run.scope_id:
            raise ContractViolation(
                "E_STAGE_SCOPE_ID_MISMATCH",
                f"绑定对象 {target_id} 不属于 StageRun 的 scope_id",
            )

        if contract.port.cardinality is Cardinality.SET:
            if not isinstance(artifact, ArtifactSet):
                raise ContractViolation(
                    "E_STAGE_BINDING_CARDINALITY_MISMATCH",
                    f"set port {binding.port_id} 必须绑定 ArtifactSet",
                )
        elif not isinstance(artifact, Artifact):
            raise ContractViolation(
                "E_STAGE_BINDING_CARDINALITY_MISMATCH",
                f"{contract.port.cardinality.value} port {binding.port_id} 必须绑定单一 Artifact",
            )

        validate_contract_artifact(contract, artifact)
        producer_ids = _producer_stage_run_ids(artifact)
        if direction == "input":
            if stage_run.stage_run_id in producer_ids:
                raise ContractViolation(
                    "E_STAGE_INPUT_SELF_PRODUCED",
                    "StageRun 不得消费由自身产生的 Artifact 或集合成员",
                )
        elif any(producer_id != stage_run.stage_run_id for producer_id in producer_ids):
            raise ContractViolation(
                "E_STAGE_OUTPUT_PRODUCER_MISMATCH",
                f"输出 {target_id} 及其全部集合成员必须绑定到当前 StageRun",
            )
        resolved[binding.port_id] = artifact
    return resolved


def _producer_stage_run_ids(value: Artifact | ArtifactSet) -> tuple[str | None, ...]:
    if isinstance(value, Artifact):
        return (value.producer_stage_run_id,)
    return (
        value.producer_stage_run_id,
        *(member.artifact.producer_stage_run_id for member in value.members),
    )


def _bound_identity_ids(values: Iterable[Artifact | ArtifactSet]) -> tuple[str, ...]:
    identities: list[str] = []
    for value in values:
        if isinstance(value, Artifact):
            identities.append(value.artifact_id)
            continue
        identities.append(value.artifact_set_id)
        identities.extend(member.artifact.artifact_id for member in value.members)
    return tuple(identities)


def _validate_preserved_rules(
    manifest: EngineManifest,
    inputs: Mapping[str, Artifact | ArtifactSet],
    outputs: Mapping[str, Artifact | ArtifactSet],
) -> None:
    for output_contract in manifest.outputs:
        output = outputs.get(output_contract.port.port_id)
        if output is None:
            continue
        for rule in output_contract.attribute_rules:
            if rule.disposition is not MediaAttributeDisposition.PRESERVED or rule.source is None:
                continue
            source = inputs.get(rule.source.port_id)
            if source is None:
                raise ContractViolation(
                    "E_MEDIA_RULE_SOURCE_UNBOUND",
                    f"preserved 规则来源端口未绑定：{rule.source.port_id}",
                )
            _validate_preserved_shape(source, output)
            source_values = _artifact_values(source)
            output_values = _artifact_values(output)
            for source_value, output_value in zip(source_values, output_values, strict=True):
                before = _attribute_at_path(source_value.attributes, rule.source.path)
                after = _attribute_at_path(output_value.attributes, rule.path)
                if not json_values_equal(before, after):
                    raise ContractViolation(
                        "E_MEDIA_RULE_PRESERVED_VIOLATION",
                        f"输出属性 {rule.path} 未保持输入 {rule.source.path} 的值",
                    )


def _validate_preserved_shape(
    source: Artifact | ArtifactSet,
    output: Artifact | ArtifactSet,
) -> None:
    if isinstance(source, Artifact) and isinstance(output, Artifact):
        return
    if not isinstance(source, ArtifactSet) or not isinstance(output, ArtifactSet):
        raise ContractViolation(
            "E_MEDIA_RULE_PRESERVED_SHAPE",
            "preserved 规则两端必须同为单一 Artifact 或同为 ArtifactSet",
        )

    source_members = tuple((member.member_id, member.coverage) for member in source.members)
    output_members = tuple((member.member_id, member.coverage) for member in output.members)
    if (
        source.expected_member_ids != output.expected_member_ids
        or source.coverage != output.coverage
        or source.coverage_mode is not output.coverage_mode
        or source_members != output_members
    ):
        raise ContractViolation(
            "E_MEDIA_RULE_PRESERVED_SHAPE",
            "ArtifactSet preserved 规则要求成员逻辑身份、顺序和 coverage 完全对齐",
        )


def _artifact_values(value: Artifact | ArtifactSet) -> tuple[Artifact, ...]:
    if isinstance(value, Artifact):
        return (value,)
    return tuple(member.artifact for member in value.members)


def _attribute_at_path(attributes: JsonObject, path: str) -> JsonValue:
    current: Any = attributes
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ContractViolation("E_MEDIA_RULE_ATTRIBUTE_MISSING", f"媒体属性路径不存在：{path}")
        current = current[part]
    return cast(JsonValue, current)
