"""生成 Studio 所需的 Registry、Core Operator、Plan 与 Runtime 只读 authority。

前端只消费本模块生成的 JSON/Schema，不复写 operator ports、EngineManifest、ExecutionPlan 或 Runtime
状态语义。投影使用纯合成 source，绝不读取真实媒体或向 Runtime 写回状态。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import JsonValue, SkipValidation, field_validator, model_validator

from zniku.application import ApplicationService
from zniku.authoring import WorkflowSpec
from zniku.contracts import (
    Artifact,
    ArtifactType,
    ContractModel,
    CoverageSpan,
    CoverageUnit,
    EngineManifest,
    MediaKind,
    PortSpec,
    Scope,
)
from zniku.pipelines import DefaultRunSnapshot
from zniku.workflow import (
    CORE_OPERATOR_CONTRACT_VERSION,
    CoreOperatorKind,
    CoreOperatorNodeSpec,
    operator_input_ports,
    operator_output_ports,
)
from zniku.workflow.execution import (
    ChapterMemberBinding,
    ChapterPlan,
    ExecutionPlan,
    WorkflowRevision,
)

STUDIO_PROJECTION_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"


class CoreOperatorProjection(ContractModel):
    """Studio Palette/Canvas 使用的 exact operator typed-port 投影。"""

    operator_contract_version: Literal["0.1.0"]
    operator_kind: CoreOperatorKind
    media_kind: MediaKind
    inputs: tuple[PortSpec, ...]
    outputs: tuple[PortSpec, ...]

    @field_validator("inputs", "outputs", mode="before")
    @classmethod
    def normalize_ports(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class StudioAuthorityProjection(ContractModel):
    """Phase 5 默认流程的完整只读 Studio authority bundle。"""

    studio_projection_contract_version: Literal["0.1.0"]
    manifests: tuple[SkipValidation[EngineManifest], ...]
    operator_contracts: tuple[SkipValidation[CoreOperatorProjection], ...]
    workflow_spec: SkipValidation[WorkflowSpec]
    chapter_plan: SkipValidation[ChapterPlan]
    execution_plan: SkipValidation[ExecutionPlan]
    workflow_revision: SkipValidation[WorkflowRevision]
    runtime_snapshot: SkipValidation[DefaultRunSnapshot]

    @field_validator("manifests", "operator_contracts", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any, info: Any) -> Any:
        if not isinstance(value, list | tuple):
            return value
        if info.field_name == "manifests":
            return tuple(
                EngineManifest.from_data(cast(Mapping[str, JsonValue], item))
                if isinstance(item, Mapping)
                else item
                for item in value
            )
        return tuple(
            CoreOperatorProjection.from_data(cast(Mapping[str, JsonValue], item))
            if isinstance(item, Mapping)
            else item
            for item in value
        )

    @field_validator("workflow_spec", mode="before")
    @classmethod
    def normalize_workflow(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return WorkflowSpec.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @field_validator("chapter_plan", mode="before")
    @classmethod
    def normalize_chapters(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return ChapterPlan.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @field_validator("execution_plan", mode="before")
    @classmethod
    def normalize_plan(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return ExecutionPlan.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @field_validator("workflow_revision", mode="before")
    @classmethod
    def normalize_revision(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return WorkflowRevision.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @field_validator("runtime_snapshot", mode="before")
    @classmethod
    def normalize_runtime(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return DefaultRunSnapshot.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @model_validator(mode="after")
    def validate_authorities(self) -> StudioAuthorityProjection:
        if self.execution_plan.workflow_spec_digest != self.workflow_spec.sha256_digest():
            raise ValueError("E_STUDIO_SPEC_PLAN_DRIFT: WorkflowSpec 与 Plan digest 不一致")
        if self.workflow_revision.execution_plan_digest != self.execution_plan.sha256_digest():
            raise ValueError("E_STUDIO_PLAN_REVISION_DRIFT: Plan 与 Revision digest 不一致")
        if (
            self.runtime_snapshot.runtime.execution_plan_digest
            != self.execution_plan.sha256_digest()
        ):
            raise ValueError("E_STUDIO_RUNTIME_PLAN_DRIFT: Runtime 与 Plan digest 不一致")
        return self


def _operator_projection(
    operator_kind: CoreOperatorKind, media_kind: MediaKind
) -> CoreOperatorProjection:
    values: dict[str, object] = {
        "kind": "core_operator",
        "node_id": f"projection.{operator_kind.value}.{media_kind.value}",
        "operator_kind": operator_kind,
        "media_kind": media_kind,
    }
    if operator_kind is CoreOperatorKind.MAP:
        # Map 端口形状与 Engine identity 无关；默认 manual Engine 只用于通过 node gate。
        default = ApplicationService().bundle.spec
        map_node = next(
            node
            for node in default.nodes
            if isinstance(node, CoreOperatorNodeSpec) and node.operator_kind is CoreOperatorKind.MAP
        )
        values["engine"] = map_node.engine
        values["parameters"] = map_node.parameters
    if operator_kind is CoreOperatorKind.SELECT:
        values["selected_member_ids"] = ("chapter.projection",)
    node = CoreOperatorNodeSpec.model_validate(values)
    return CoreOperatorProjection(
        operator_contract_version=CORE_OPERATOR_CONTRACT_VERSION,
        operator_kind=operator_kind,
        media_kind=media_kind,
        inputs=operator_input_ports(node),
        outputs=operator_output_ports(node),
    )


def build_studio_authority_projection() -> StudioAuthorityProjection:
    """确定构造默认流程、三章节 Plan 与一个已开始的只读 Runtime snapshot。"""

    application = ApplicationService()
    source = Artifact(
        artifact_id="artifact.studio.synthetic.source",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.PROGRAM_MEDIA,
        scope=Scope.PROGRAM,
        scope_id="program.studio.synthetic",
        attributes={
            "duration_frames": 300,
            "audio_stream_ids": ["audio.japanese", "audio.english"],
        },
    )
    chapters = ChapterPlan(
        chapter_plan_id="chapter_plan.studio.synthetic",
        members=tuple(
            ChapterMemberBinding(
                member_id=f"chapter.{index:03d}",
                scope_id=f"scope.chapter.{index:03d}",
                coverage=CoverageSpan(
                    unit=CoverageUnit.FRAME,
                    start=(index - 1) * 100,
                    end=index * 100,
                ),
            )
            for index in range(1, 4)
        ),
        coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=300),
    )
    prepared = application.prepare_default(source, chapters)
    snapshot = application.start_run(
        command_id="command.studio.projection.start",
        preparation_id=prepared.preparation_id,
        workflow_run_id="workflow_run.studio.synthetic",
    )
    snapshot = application.execute_node(
        command_id="command.studio.projection.source",
        workflow_run_id="workflow_run.studio.synthetic",
        plan_node_id="plan.node.source",
    )
    operator_contracts = tuple(
        _operator_projection(operator_kind, media_kind)
        for operator_kind in CoreOperatorKind
        for media_kind in (MediaKind.VIDEO, MediaKind.AUDIO)
    )
    return StudioAuthorityProjection(
        studio_projection_contract_version=STUDIO_PROJECTION_CONTRACT_VERSION,
        manifests=application.bundle.manifests,
        operator_contracts=operator_contracts,
        workflow_spec=application.bundle.spec,
        chapter_plan=chapters,
        execution_plan=prepared.plan,
        workflow_revision=prepared.revision,
        runtime_snapshot=snapshot,
    )
