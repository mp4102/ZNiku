"""定义 ZNIKU Engine SDK 的安装描述与单次候选输出调用合同。

本模块只描述 Runtime 与受信 Engine adapter 之间的窄化边界。调用结果仍是候选 Artifact，不能据此
派生 StageRun complete、Evidence、publication 或 Final；这些职责只属于后续 Runtime。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, cast

from pydantic import Field, JsonValue, SkipValidation, field_validator, model_validator

from zniku.contracts import (
    Artifact,
    ArtifactRef,
    ArtifactSet,
    ArtifactSetRef,
    ContractModel,
    EngineBinding,
    PortBinding,
    Sha256Digest,
    StableId,
    StageRun,
)
from zniku.contracts.errors import fail
from zniku.contracts.identifiers import ExactVersion

ENGINE_SDK_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"


class InstalledEngineState(StrEnum):
    """Installed Engine 是否可以被精确解析。"""

    ENABLED = "enabled"
    DISABLED = "disabled"


class EnginePackageDescriptor(ContractModel):
    """将可信 package 版本和代码摘要绑定到唯一 EngineManifest。"""

    sdk_contract_version: Literal["0.1.0"]
    package_id: StableId
    package_version: ExactVersion
    implementation_digest: Sha256Digest
    engine: SkipValidation[EngineBinding]

    @field_validator("engine", mode="before")
    @classmethod
    def normalize_engine(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return EngineBinding.from_data(cast(Mapping[str, JsonValue], value))
        if not isinstance(value, EngineBinding):
            raise ValueError("E_ENGINE_PACKAGE_BINDING_INVALID: engine 必须是 EngineBinding")
        return value


class ArtifactValue(ContractModel):
    """向 Engine 提供一个已绑定的单一 Artifact。"""

    kind: Literal["artifact"] = "artifact"
    port_id: StableId
    artifact: SkipValidation[Artifact]

    @field_validator("artifact", mode="before")
    @classmethod
    def normalize_artifact(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return Artifact.from_data(cast(Mapping[str, JsonValue], value))
        if not isinstance(value, Artifact):
            raise ValueError("E_ENGINE_ARTIFACT_VALUE_INVALID: artifact 必须是 Artifact")
        return value

    def binding(self) -> PortBinding:
        return PortBinding(
            port_id=self.port_id,
            target=ArtifactRef(artifact_id=self.artifact.artifact_id),
        )


class ArtifactSetValue(ContractModel):
    """向 Engine 提供一个已完整形成的 ArtifactSet。"""

    kind: Literal["artifact_set"] = "artifact_set"
    port_id: StableId
    artifact_set: SkipValidation[ArtifactSet]

    @field_validator("artifact_set", mode="before")
    @classmethod
    def normalize_artifact_set(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return ArtifactSet.from_data(cast(Mapping[str, JsonValue], value))
        if not isinstance(value, ArtifactSet):
            raise ValueError("E_ENGINE_ARTIFACT_SET_VALUE_INVALID: artifact_set 必须是 ArtifactSet")
        return value

    def binding(self) -> PortBinding:
        return PortBinding(
            port_id=self.port_id,
            target=ArtifactSetRef(artifact_set_id=self.artifact_set.artifact_set_id),
        )


EngineArtifactValue = Annotated[ArtifactValue | ArtifactSetValue, Field(discriminator="kind")]


def artifact_value_id(value: EngineArtifactValue) -> str:
    if isinstance(value, ArtifactValue):
        return value.artifact.artifact_id
    return value.artifact_set.artifact_set_id


class EngineInvocationRequest(ContractModel):
    """Runtime 提供给 automatic Engine 的不可变调用输入。

    `StageRun.outputs` 必须为空；Engine 只能返回候选输出，不能修改 StageRun 或正式 authority。
    """

    sdk_contract_version: Literal["0.1.0"]
    invocation_id: StableId
    stage_run: SkipValidation[StageRun]
    inputs: tuple[EngineArtifactValue, ...]

    @field_validator("inputs", mode="before")
    @classmethod
    def normalize_inputs(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return tuple(value)
        return value

    @field_validator("stage_run", mode="before")
    @classmethod
    def normalize_stage_run(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return StageRun.from_data(cast(Mapping[str, JsonValue], value))
        if not isinstance(value, StageRun):
            raise ValueError("E_ENGINE_STAGE_RUN_INVALID: stage_run 必须是 StageRun")
        return value

    @model_validator(mode="after")
    def validate_stage_bindings(self) -> EngineInvocationRequest:
        if self.stage_run.outputs:
            fail("E_ENGINE_REQUEST_OUTPUT_PREBOUND", "Engine 调用前不得预绑定候选输出")
        port_ids = tuple(value.port_id for value in self.inputs)
        if len(port_ids) != len(set(port_ids)):
            fail("E_ENGINE_REQUEST_INPUT_DUPLICATE", "Engine request input port 不得重复")
        expected = {binding.port_id: binding for binding in self.stage_run.inputs}
        actual = {value.port_id: value.binding() for value in self.inputs}
        if actual != expected:
            fail(
                "E_ENGINE_REQUEST_INPUT_BINDING_MISMATCH",
                "Engine request 的完整输入值必须与 StageRun input bindings 精确一致",
            )
        return self

    def input_value(self, port_id: str) -> EngineArtifactValue:
        """按稳定 port ID 取得输入；未知 port 失败关闭。"""

        for value in self.inputs:
            if value.port_id == port_id:
                return value
        raise KeyError(port_id)


class EngineInvocationResult(ContractModel):
    """Engine 返回的候选输出；不代表验收、发布或完成。"""

    sdk_contract_version: Literal["0.1.0"]
    invocation_id: StableId
    engine: SkipValidation[EngineBinding]
    outputs: tuple[EngineArtifactValue, ...]

    @field_validator("outputs", mode="before")
    @classmethod
    def normalize_outputs(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return tuple(value)
        return value

    @field_validator("engine", mode="before")
    @classmethod
    def normalize_engine(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return EngineBinding.from_data(cast(Mapping[str, JsonValue], value))
        if not isinstance(value, EngineBinding):
            raise ValueError("E_ENGINE_RESULT_BINDING_INVALID: engine 必须是 EngineBinding")
        return value

    @model_validator(mode="after")
    def validate_output_ids(self) -> EngineInvocationResult:
        port_ids = tuple(value.port_id for value in self.outputs)
        if len(port_ids) != len(set(port_ids)):
            fail("E_ENGINE_RESULT_OUTPUT_DUPLICATE", "Engine result output port 不得重复")
        authority_ids = tuple(artifact_value_id(value) for value in self.outputs)
        if len(authority_ids) != len(set(authority_ids)):
            fail("E_ENGINE_RESULT_AUTHORITY_DUPLICATE", "候选输出 authority ID 不得重复")
        return self


class InstalledEngineRecord(ContractModel):
    """Registry 对一个已安装 Engine package 的只读投影。"""

    descriptor: SkipValidation[EnginePackageDescriptor]
    state: InstalledEngineState

    @field_validator("descriptor", mode="before")
    @classmethod
    def normalize_descriptor(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return EnginePackageDescriptor.from_data(cast(Mapping[str, JsonValue], value))
        if not isinstance(value, EnginePackageDescriptor):
            raise ValueError("E_ENGINE_RECORD_DESCRIPTOR_INVALID: descriptor 类型无效")
        return value
