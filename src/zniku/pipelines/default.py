"""实现 Phase 3 默认工作流及其证据驱动的合成纵向执行。

默认流程完整表达 Demux、原始音轨集合旁路、章节级人工 Enhancement 与 Frame interpolation、
Reduce、一次 Video encode、Mux 和唯一 Final。Runtime 只处理纯合成媒体 authority；人工完成文字不
是证据，外部输出必须经 full verification 和 no-replace publication 后才能推进正式状态。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import Field, JsonValue, SkipValidation, field_validator, model_validator

from zniku.authoring import (
    CoreNodeContractSet,
    EngineStageNodeSpec,
    FinalNodeSpec,
    InMemoryManifestCatalog,
    PortEndpoint,
    SourceNodeSpec,
    WorkflowCompiler,
    WorkflowEdgeSpec,
    WorkflowSpec,
)
from zniku.contracts import (
    Artifact,
    ContractModel,
    ContractViolation,
    EngineBinding,
    EngineManifest,
    ExecutionMode,
    JsonObject,
    MediaKind,
    Scope,
    Sha256Digest,
    StableId,
)
from zniku.engines import builtin_engine_packages
from zniku.workflow import CoreOperatorKind, CoreOperatorNodeSpec
from zniku.workflow.execution import (
    ChapterPlan,
    ExecutionPlan,
    NodeRunState,
    PlannedNode,
    PlannedSubjectKind,
    RuntimeSnapshot,
    SyntheticRuntime,
    WorkflowRevision,
)

DEFAULT_PIPELINE_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"


def _media_schema() -> JsonObject:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"duration_frames": {"type": "integer", "minimum": 1}},
        "required": ["duration_frames"],
        "additionalProperties": True,
    }


def _video_manifest(
    *,
    engine_id: str,
    display_name: str,
    scope: Scope,
    execution_mode: ExecutionMode,
    parameter_schema: JsonObject,
    supports_recovery: bool,
) -> EngineManifest:
    media_schema = _media_schema()
    return EngineManifest.from_data(
        {
            "contract_version": "0.1.0",
            "engine_id": engine_id,
            "engine_version": "0.1.0",
            "display_name": display_name,
            "execution_mode": execution_mode.value,
            "lifecycle": {
                "supports_acceptance": True,
                "supports_publication": True,
                "supports_recovery": supports_recovery,
            },
            "supported_scopes": [scope.value],
            "inputs": [
                {
                    "port": {
                        "port_id": "video_in",
                        "artifact_type": "media",
                        "media_kind": "video",
                        "scope": scope.value,
                        "cardinality": "one",
                    },
                    "preconditions_schema": dict(media_schema),
                }
            ],
            "outputs": [
                {
                    "port": {
                        "port_id": "video_out",
                        "artifact_type": "media",
                        "media_kind": "video",
                        "scope": scope.value,
                        "cardinality": "one",
                    },
                    "guarantees_schema": dict(media_schema),
                    "attribute_rules": [],
                }
            ],
            "parameter_schema": dict(parameter_schema),
        }
    )


def _closed_parameters(properties: JsonObject, required: tuple[str, ...]) -> JsonObject:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


@dataclass(frozen=True, slots=True)
class DefaultWorkflowBundle:
    """默认 WorkflowSpec、精确 Manifest authority 与 Compiler 的可信组装结果。"""

    spec: WorkflowSpec
    manifests: tuple[EngineManifest, ...]
    compiler: WorkflowCompiler


def build_default_workflow() -> DefaultWorkflowBundle:
    """构造不依赖路径、模型安装位置或可执行入口的默认工作流。"""

    demux_package, mux_package = builtin_engine_packages()
    enhancement = _video_manifest(
        engine_id="zniku.builtin.manual-enhancement",
        display_name="ZNIKU Manual Enhancement",
        scope=Scope.CHAPTER,
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        supports_recovery=True,
        parameter_schema=_closed_parameters(
            {
                "model_name": {"type": "string", "enum": ["Starlight Precise"]},
                "model_version": {"type": "string", "enum": ["2.6"]},
                "scale": {"type": "integer", "enum": [2]},
            },
            ("model_name", "model_version", "scale"),
        ),
    )
    interpolation = _video_manifest(
        engine_id="zniku.builtin.manual-frame-interpolation",
        display_name="ZNIKU Manual Frame Interpolation",
        scope=Scope.CHAPTER,
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        supports_recovery=True,
        parameter_schema=_closed_parameters(
            {
                "model_name": {"type": "string", "enum": ["Chronos Fast"]},
                "model_version": {"type": "string", "enum": ["3"]},
                "fps_multiplier": {"type": "integer", "enum": [2]},
            },
            ("model_name", "model_version", "fps_multiplier"),
        ),
    )
    encode = _video_manifest(
        engine_id="zniku.builtin.synthetic-video-encode",
        display_name="ZNIKU Synthetic One-shot Video Encode",
        scope=Scope.PROGRAM,
        execution_mode=ExecutionMode.AUTOMATIC,
        supports_recovery=False,
        parameter_schema=_closed_parameters(
            {
                "codec": {"type": "string", "enum": ["hevc-main10"]},
                "crf": {"type": "integer", "minimum": 0, "maximum": 51},
                "one_shot": {"type": "boolean", "const": True},
            },
            ("codec", "crf", "one_shot"),
        ),
    )
    nodes = (
        SourceNodeSpec(kind="source", node_id="node.source"),
        EngineStageNodeSpec(
            kind="engine_stage",
            node_id="node.demux",
            engine=demux_package.descriptor.engine,
            parameters={},
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.partition",
            operator_kind=CoreOperatorKind.PARTITION,
            media_kind=MediaKind.VIDEO,
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.enhancement",
            operator_kind=CoreOperatorKind.MAP,
            media_kind=MediaKind.VIDEO,
            engine=EngineBinding.from_manifest(enhancement),
            parameters={
                "model_name": "Starlight Precise",
                "model_version": "2.6",
                "scale": 2,
            },
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.frame_interpolation",
            operator_kind=CoreOperatorKind.MAP,
            media_kind=MediaKind.VIDEO,
            engine=EngineBinding.from_manifest(interpolation),
            parameters={
                "model_name": "Chronos Fast",
                "model_version": "3",
                "fps_multiplier": 2,
            },
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.reduce",
            operator_kind=CoreOperatorKind.REDUCE,
            media_kind=MediaKind.VIDEO,
        ),
        EngineStageNodeSpec(
            kind="engine_stage",
            node_id="node.video_encode",
            engine=EngineBinding.from_manifest(encode),
            parameters={"codec": "hevc-main10", "crf": 18, "one_shot": True},
        ),
        EngineStageNodeSpec(
            kind="engine_stage",
            node_id="node.mux",
            engine=mux_package.descriptor.engine,
            parameters={"container": "matroska"},
        ),
        FinalNodeSpec(kind="final", node_id="node.final"),
    )
    edge_values = (
        ("source-demux", "node.source", "program", "node.demux", "program_in"),
        ("demux-partition", "node.demux", "video_out", "node.partition", "in"),
        ("partition-enhancement", "node.partition", "out", "node.enhancement", "in"),
        (
            "enhancement-interpolation",
            "node.enhancement",
            "out",
            "node.frame_interpolation",
            "in",
        ),
        ("interpolation-reduce", "node.frame_interpolation", "out", "node.reduce", "in"),
        ("reduce-encode", "node.reduce", "out", "node.video_encode", "video_in"),
        ("encode-mux", "node.video_encode", "video_out", "node.mux", "video_in"),
        ("original-audio-mux", "node.demux", "audio_out", "node.mux", "audio_in"),
        ("mux-final", "node.mux", "program_out", "node.final", "program"),
    )
    edges = tuple(
        WorkflowEdgeSpec(
            edge_id=f"edge.{edge_id}",
            source=PortEndpoint(node_id=source_node, port_id=source_port),
            target=PortEndpoint(node_id=target_node, port_id=target_port),
        )
        for edge_id, source_node, source_port, target_node, target_port in edge_values
    )
    spec = WorkflowSpec(
        workflow_contract_version="0.2.0",
        workflow_id="workflow.default.0.1.0",
        nodes=nodes,
        edges=edges,
    )
    manifests = (
        demux_package.manifest,
        enhancement,
        interpolation,
        encode,
        mux_package.manifest,
    )
    compiler = WorkflowCompiler(InMemoryManifestCatalog(manifests), CoreNodeContractSet.phase_2a())
    return DefaultWorkflowBundle(spec=spec, manifests=manifests, compiler=compiler)


class ManualHandoff(ContractModel):
    """Runtime 为一次人工外部 Engine 尝试签发的只读交接合同。"""

    handoff_id: StableId
    plan_node_id: StableId
    attempt: int = Field(ge=1)
    engine: SkipValidation[EngineBinding]
    source_authority_digest: Sha256Digest
    input_artifact_ids: tuple[StableId, ...]
    leaf_ids: tuple[StableId, ...]
    expected_output_frames: int = Field(ge=1)
    model_name: str
    model_version: str

    @field_validator("engine", mode="before")
    @classmethod
    def normalize_engine(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return EngineBinding.from_data(cast(Mapping[str, JsonValue], value))
        if not isinstance(value, EngineBinding):
            raise ValueError("E_HANDOFF_ENGINE_INVALID: engine 类型无效")
        return value


class ExternalOutputSubmission(ContractModel):
    """人工工具产生的惰性候选；提交本身不代表验收、发布或完成。"""

    handoff_id: StableId
    candidate_artifact_id: StableId
    candidate_digest: Sha256Digest
    source_authority_digest: Sha256Digest
    frame_count: int = Field(ge=1)
    completed_leaf_ids: tuple[StableId, ...]


class PublicationRecord(ContractModel):
    """full verification 通过后由 Runtime 生成的 no-replace publication 记录。"""

    publication_id: StableId
    plan_node_id: StableId
    artifact_id: StableId
    artifact_digest: Sha256Digest
    frame_count: int = Field(ge=1)
    evidence_id: StableId
    source_authority_digest: Sha256Digest
    verification_mode: Literal["full"] = "full"
    no_replace: Literal[True] = True


class AudioPassthroughProof(ContractModel):
    """证明 Mux 消费的是 Demux 原始、有序 AudioArtifactSet，而非隐式音频。"""

    demux_plan_node_id: Literal["plan.node.demux"]
    mux_plan_node_id: Literal["plan.node.mux"]
    artifact_set_id: StableId
    ordered_stream_ids: tuple[StableId, ...]
    stream_copy: Literal[True] = True

    @model_validator(mode="after")
    def require_streams(self) -> AudioPassthroughProof:
        if not self.ordered_stream_ids or len(self.ordered_stream_ids) != len(
            set(self.ordered_stream_ids)
        ):
            raise ValueError("E_AUDIO_AUTHORITY_INVALID: 原始音轨必须非空、唯一且保序")
        return self


class FullVerificationRecord(ContractModel):
    """唯一 Final 发布前绑定完整执行证据、媒体计数与原始音轨 authority。"""

    verification_id: StableId
    final_artifact_id: StableId
    final_artifact_digest: Sha256Digest
    source_authority_digest: Sha256Digest
    frame_count: int = Field(ge=1)
    audio_artifact_set_id: StableId
    ordered_audio_stream_ids: tuple[StableId, ...]
    evidence_ids: tuple[StableId, ...]
    mode: Literal["full"] = "full"
    verified: Literal[True] = True


class DefaultRunSnapshot(ContractModel):
    """Phase 3 的可恢复 authority；不保存目录、日志或 GUI/Agent 会话。"""

    pipeline_contract_version: Literal["0.1.0"]
    source_artifact: SkipValidation[Artifact]
    chapter_plan: SkipValidation[ChapterPlan]
    runtime: SkipValidation[RuntimeSnapshot]
    audio_proof: SkipValidation[AudioPassthroughProof]
    handoffs: tuple[SkipValidation[ManualHandoff], ...] = ()
    publications: tuple[SkipValidation[PublicationRecord], ...] = ()
    full_verification: SkipValidation[FullVerificationRecord] | None = None

    @field_validator("source_artifact", mode="before")
    @classmethod
    def normalize_source(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return Artifact.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @field_validator("chapter_plan", mode="before")
    @classmethod
    def normalize_chapter_plan(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return ChapterPlan.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @field_validator("runtime", mode="before")
    @classmethod
    def normalize_runtime(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return RuntimeSnapshot.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @field_validator("audio_proof", mode="before")
    @classmethod
    def normalize_audio_proof(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return AudioPassthroughProof.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @field_validator("handoffs", mode="before")
    @classmethod
    def normalize_handoffs(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return tuple(
                ManualHandoff.from_data(cast(Mapping[str, JsonValue], item))
                if isinstance(item, Mapping)
                else item
                for item in value
            )
        return value

    @field_validator("publications", mode="before")
    @classmethod
    def normalize_publications(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return tuple(
                PublicationRecord.from_data(cast(Mapping[str, JsonValue], item))
                if isinstance(item, Mapping)
                else item
                for item in value
            )
        return value

    @field_validator("full_verification", mode="before")
    @classmethod
    def normalize_verification(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return FullVerificationRecord.from_data(cast(Mapping[str, JsonValue], value))
        return value

    @model_validator(mode="after")
    def validate_authority(self) -> DefaultRunSnapshot:
        if not all(
            isinstance(item, expected)
            for item, expected in (
                (self.source_artifact, Artifact),
                (self.chapter_plan, ChapterPlan),
                (self.runtime, RuntimeSnapshot),
                (self.audio_proof, AudioPassthroughProof),
            )
        ):
            raise ValueError("E_DEFAULT_SNAPSHOT_AUTHORITY_INVALID: authority 类型无效")
        ids = tuple(item.artifact_id for item in self.publications)
        if len(ids) != len(set(ids)):
            raise ValueError("E_PUBLICATION_REPLACE: publication artifact identity 不得重复")
        return self


class DefaultWorkflowRuntime:
    """在冻结 Plan 上执行默认工作流的 application-level Runtime。

    它复用 `SyntheticRuntime` 的 ready/retry authority，并在其外侧增加人工 handoff、full verify、
    no-replace publication、原始音轨证明和 Final gate。该类不解释 shell，也不接触媒体文件。
    """

    def __init__(
        self,
        plan: ExecutionPlan,
        revision: WorkflowRevision,
        manifests: tuple[EngineManifest, ...],
        snapshot: DefaultRunSnapshot,
    ) -> None:
        self._plan = plan
        self._revision = revision
        self._manifests = {
            (item.engine_id, item.engine_version, item.sha256_digest()): item for item in manifests
        }
        self._runtime = SyntheticRuntime(plan, revision, snapshot.runtime)
        self._snapshot = snapshot
        self._validate_restored_snapshot()

    @classmethod
    def start(
        cls,
        *,
        plan: ExecutionPlan,
        revision: WorkflowRevision,
        manifests: tuple[EngineManifest, ...],
        source_artifact: Artifact,
        chapter_plan: ChapterPlan,
        workflow_run_id: str,
    ) -> DefaultWorkflowRuntime:
        if source_artifact.media_kind is not MediaKind.PROGRAM_MEDIA:
            raise ContractViolation(
                "E_DEFAULT_SOURCE_KIND", "默认工作流 source 必须是 ProgramMedia"
            )
        if chapter_plan.coverage.end != source_artifact.attributes["duration_frames"]:
            raise ContractViolation("E_DEFAULT_CHAPTER_COVERAGE", "章节 coverage 必须覆盖 source")
        stream_ids = cast(tuple[str, ...], source_artifact.attributes["audio_stream_ids"])
        audio_proof = AudioPassthroughProof(
            demux_plan_node_id="plan.node.demux",
            mux_plan_node_id="plan.node.mux",
            artifact_set_id=f"artifact_set.{workflow_run_id}.original_audio",
            ordered_stream_ids=stream_ids,
            stream_copy=True,
        )
        runtime = SyntheticRuntime.start(plan, revision, workflow_run_id)
        snapshot = DefaultRunSnapshot(
            pipeline_contract_version=DEFAULT_PIPELINE_CONTRACT_VERSION,
            source_artifact=source_artifact,
            chapter_plan=chapter_plan,
            runtime=runtime.snapshot,
            audio_proof=audio_proof,
        )
        return cls(plan, revision, manifests, snapshot)

    @property
    def snapshot(self) -> DefaultRunSnapshot:
        return self._snapshot

    def ready_nodes(self) -> tuple[str, ...]:
        return self._runtime.ready_nodes()

    def execute_automatic(
        self, plan_node_id: str, *, fail_attempt: bool = False
    ) -> DefaultRunSnapshot:
        """执行非人工节点；一次编码失败只保留失败尝试，重试会从头产生新 attempt。"""

        planned = self._planned(plan_node_id)
        manifest = self._manifest(planned)
        if manifest is not None and manifest.execution_mode is ExecutionMode.MANUAL_EXTERNAL:
            raise ContractViolation("E_MANUAL_HANDOFF_REQUIRED", "人工 Engine 必须先签发 handoff")
        self._assert_source_authority(self._snapshot.source_artifact.sha256_digest())
        before = self._record(plan_node_id)
        self._runtime.execute(plan_node_id, fail_attempt=fail_attempt)
        self._sync_runtime()
        after = self._record(plan_node_id)
        if fail_attempt:
            return self._snapshot
        if before.state is not NodeRunState.COMPLETE and after.state is NodeRunState.COMPLETE:
            self._publish_runtime_output(planned, after)
            if planned.subject_kind is PlannedSubjectKind.FINAL:
                self._verify_final()
        return self._snapshot

    def prepare_manual(self, plan_node_id: str) -> ManualHandoff:
        """为 ready manual Engine 生成稳定 handoff；重放返回同一次 attempt 的同一合同。"""

        planned = self._planned(plan_node_id)
        manifest = self._manifest(planned)
        if manifest is None or manifest.execution_mode is not ExecutionMode.MANUAL_EXTERNAL:
            raise ContractViolation(
                "E_HANDOFF_ENGINE_MODE", "只有 manual_external Engine 可 handoff"
            )
        record = self._record(plan_node_id)
        if record.state is not NodeRunState.READY:
            raise ContractViolation("E_HANDOFF_NODE_NOT_READY", "只有 ready node 可签发 handoff")
        attempt = record.attempt + 1
        existing = next(
            (
                item
                for item in self._snapshot.handoffs
                if item.plan_node_id == plan_node_id and item.attempt == attempt
            ),
            None,
        )
        if existing is not None:
            return existing
        inputs = self._dependency_publications(planned)
        handoff = ManualHandoff(
            handoff_id=f"handoff.{self._snapshot.runtime.workflow_run_id}.{plan_node_id}.{attempt}",
            plan_node_id=plan_node_id,
            attempt=attempt,
            engine=cast(EngineBinding, planned.engine),
            source_authority_digest=self._snapshot.source_artifact.sha256_digest(),
            input_artifact_ids=tuple(item.artifact_id for item in inputs),
            leaf_ids=self._leaf_ids(planned),
            expected_output_frames=self._expected_frames(planned),
            model_name=cast(str, planned.parameters["model_name"]),
            model_version=cast(str, planned.parameters["model_version"]),
        )
        self._snapshot = self._snapshot.model_copy(
            update={"handoffs": (*self._snapshot.handoffs, handoff)}
        )
        return handoff

    def report_manual_completion(self, _message: str) -> None:
        """显式拒绝把聊天或工具完成文字当成正式状态。"""

        raise ContractViolation(
            "E_HANDOFF_MESSAGE_NOT_AUTHORITY", "人工完成消息只能触发验收，不能推进状态"
        )

    def submit_external_output(self, submission: ExternalOutputSubmission) -> DefaultRunSnapshot:
        """稳定读取等价检查、full verify、no-replace publish 后完成人工节点。"""

        handoff = next(
            (item for item in self._snapshot.handoffs if item.handoff_id == submission.handoff_id),
            None,
        )
        if handoff is None:
            raise ContractViolation("E_HANDOFF_UNKNOWN", "submission 引用了未知 handoff")
        record = self._record(handoff.plan_node_id)
        if record.state is NodeRunState.COMPLETE:
            existing = next(
                item
                for item in self._snapshot.publications
                if item.plan_node_id == handoff.plan_node_id
            )
            if existing.artifact_id != submission.candidate_artifact_id:
                raise ContractViolation("E_PUBLICATION_REPLACE", "已完成节点不得替换输出")
            return self._snapshot
        if record.state is not NodeRunState.READY or record.attempt + 1 != handoff.attempt:
            raise ContractViolation("E_HANDOFF_STALE", "handoff attempt 已过期或节点不可验收")
        self._assert_source_authority(submission.source_authority_digest)
        if submission.frame_count != handoff.expected_output_frames:
            raise ContractViolation("E_FULL_VERIFY_FRAME_COUNT", "输出帧数不满足 full verification")
        if submission.completed_leaf_ids != handoff.leaf_ids:
            raise ContractViolation("E_FULL_VERIFY_LEAF_SET", "leaf receipt 不完整或顺序错误")
        if submission.candidate_artifact_id in {
            item.artifact_id for item in self._snapshot.publications
        }:
            raise ContractViolation("E_PUBLICATION_REPLACE", "候选 Artifact identity 已存在")
        self._runtime.execute(handoff.plan_node_id)
        self._sync_runtime()
        after = self._record(handoff.plan_node_id)
        publication = PublicationRecord(
            publication_id=f"publication.{handoff.handoff_id}",
            plan_node_id=handoff.plan_node_id,
            artifact_id=submission.candidate_artifact_id,
            artifact_digest=submission.candidate_digest,
            frame_count=submission.frame_count,
            evidence_id=cast(str, after.evidence_id),
            source_authority_digest=submission.source_authority_digest,
            verification_mode="full",
            no_replace=True,
        )
        self._snapshot = self._snapshot.model_copy(
            update={"publications": (*self._snapshot.publications, publication)}
        )
        return self._snapshot

    def retry(self, plan_node_id: str) -> DefaultRunSnapshot:
        self._runtime.retry(plan_node_id)
        self._sync_runtime()
        return self._snapshot

    def _publish_runtime_output(self, planned: PlannedNode, record: Any) -> None:
        if any(item.plan_node_id == planned.plan_node_id for item in self._snapshot.publications):
            return
        frame_count = self._expected_frames(planned)
        artifact_id = (
            f"artifact.{self._snapshot.runtime.workflow_run_id}."
            f"{planned.plan_node_id}.{record.attempt}"
        )
        seed = f"{artifact_id}\0{frame_count}\0{self._snapshot.source_artifact.sha256_digest()}"
        publication = PublicationRecord(
            publication_id=f"publication.{self._snapshot.runtime.workflow_run_id}.{planned.plan_node_id}.{record.attempt}",
            plan_node_id=planned.plan_node_id,
            artifact_id=artifact_id,
            artifact_digest=f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}",
            frame_count=frame_count,
            evidence_id=cast(str, record.evidence_id),
            source_authority_digest=self._snapshot.source_artifact.sha256_digest(),
            verification_mode="full",
            no_replace=True,
        )
        self._snapshot = self._snapshot.model_copy(
            update={"publications": (*self._snapshot.publications, publication)}
        )

    def _verify_final(self) -> None:
        if not all(item.state is NodeRunState.COMPLETE for item in self._snapshot.runtime.nodes):
            raise ContractViolation("E_FINAL_INCOMPLETE", "Final 前所有 planned node 必须 complete")
        final = next(
            item for item in self._snapshot.publications if item.plan_node_id == "plan.node.final"
        )
        mux = next(
            item for item in self._snapshot.publications if item.plan_node_id == "plan.node.mux"
        )
        encode_records = tuple(
            item
            for item in self._snapshot.runtime.nodes
            if item.plan_node_id == "plan.node.video_encode"
        )
        if len(encode_records) != 1 or encode_records[0].state is not NodeRunState.COMPLETE:
            raise ContractViolation(
                "E_FINAL_ENCODE_CARDINALITY", "Final 必须绑定一次完整 program encode"
            )
        verification = FullVerificationRecord(
            verification_id=f"verification.{self._snapshot.runtime.workflow_run_id}.final",
            final_artifact_id=final.artifact_id,
            final_artifact_digest=final.artifact_digest,
            source_authority_digest=self._snapshot.source_artifact.sha256_digest(),
            frame_count=mux.frame_count,
            audio_artifact_set_id=self._snapshot.audio_proof.artifact_set_id,
            ordered_audio_stream_ids=self._snapshot.audio_proof.ordered_stream_ids,
            evidence_ids=tuple(item.evidence_id for item in self._snapshot.publications),
            mode="full",
            verified=True,
        )
        self._snapshot = self._snapshot.model_copy(update={"full_verification": verification})

    def _expected_frames(self, planned: PlannedNode) -> int:
        dependencies = self._dependency_publications(planned)
        if not dependencies:
            return cast(int, self._snapshot.source_artifact.attributes["duration_frames"])
        if planned.stage_spec_id == "node.enhancement":
            member = next(
                item
                for item in self._snapshot.chapter_plan.members
                if item.scope_id == planned.scope_id
            )
            return member.coverage.end - member.coverage.start
        if planned.stage_spec_id == "node.frame_interpolation":
            return dependencies[0].frame_count * cast(int, planned.parameters["fps_multiplier"])
        if planned.stage_spec_id == "node.reduce":
            return sum(item.frame_count for item in dependencies)
        return max(item.frame_count for item in dependencies)

    def _leaf_ids(self, planned: PlannedNode) -> tuple[str, ...]:
        member = next(
            item
            for item in self._snapshot.chapter_plan.members
            if item.scope_id == planned.scope_id
        )
        length = member.coverage.end - member.coverage.start
        leaf_count = 2 if length > 1 else 1
        return tuple(f"leaf.{planned.scope_id}.{index:03d}" for index in range(1, leaf_count + 1))

    def _dependency_publications(self, planned: PlannedNode) -> tuple[PublicationRecord, ...]:
        by_node = {item.plan_node_id: item for item in self._snapshot.publications}
        return tuple(by_node[item] for item in planned.dependencies if item in by_node)

    def _planned(self, plan_node_id: str) -> PlannedNode:
        try:
            return next(item for item in self._plan.nodes if item.plan_node_id == plan_node_id)
        except StopIteration as error:
            raise ContractViolation("E_RUNTIME_NODE_UNKNOWN", "未知 planned node") from error

    def _record(self, plan_node_id: str) -> Any:
        return next(
            item for item in self._runtime.snapshot.nodes if item.plan_node_id == plan_node_id
        )

    def _manifest(self, planned: PlannedNode) -> EngineManifest | None:
        if planned.engine is None:
            return None
        key = (
            planned.engine.engine_id,
            planned.engine.engine_version,
            planned.engine.manifest_digest,
        )
        manifest = self._manifests.get(key)
        if manifest is None:
            raise ContractViolation(
                "E_RUNTIME_ENGINE_AUTHORITY", "Plan Engine 无法解析精确 Manifest"
            )
        return manifest

    def _assert_source_authority(self, digest: str) -> None:
        if digest != self._snapshot.source_artifact.sha256_digest():
            raise ContractViolation("E_SOURCE_AUTHORITY_DRIFT", "source authority 已漂移")

    def _sync_runtime(self) -> None:
        self._snapshot = self._snapshot.model_copy(update={"runtime": self._runtime.snapshot})

    def _validate_restored_snapshot(self) -> None:
        publication_ids = {item.artifact_id for item in self._snapshot.publications}
        if len(publication_ids) != len(self._snapshot.publications):
            raise ContractViolation("E_PUBLICATION_REPLACE", "恢复 snapshot 含重复 publication")
        if self._snapshot.audio_proof.ordered_stream_ids != cast(
            tuple[str, ...], self._snapshot.source_artifact.attributes["audio_stream_ids"]
        ):
            raise ContractViolation("E_AUDIO_AUTHORITY_DRIFT", "原始音轨顺序与 source 不一致")
        verification = self._snapshot.full_verification
        if verification is None:
            return
        final_publications = tuple(
            item for item in self._snapshot.publications if item.plan_node_id == "plan.node.final"
        )
        if (
            len(final_publications) != 1
            or verification.final_artifact_id != final_publications[0].artifact_id
            or verification.final_artifact_digest != final_publications[0].artifact_digest
            or verification.audio_artifact_set_id != self._snapshot.audio_proof.artifact_set_id
            or verification.ordered_audio_stream_ids
            != self._snapshot.audio_proof.ordered_stream_ids
            or not all(item.state is NodeRunState.COMPLETE for item in self._snapshot.runtime.nodes)
        ):
            raise ContractViolation(
                "E_FINAL_VERIFICATION_DRIFT", "Final verification authority 不闭合"
            )
