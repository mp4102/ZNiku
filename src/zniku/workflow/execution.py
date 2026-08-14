"""定义 Phase 2 的 Binding、Preflight、ExecutionPlan、Freeze 与合成 Runtime。

本模块保持 Compiler 与 Runtime 的 authority 分离：Preflight 只验证绑定，Plan 只冻结确定拓扑，Runtime
只消费冻结 Plan 并从持久 snapshot 派生 ready set。合成 executor 不接触媒体文件。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import (
    Field,
    JsonValue,
    SkipValidation,
    field_serializer,
    field_validator,
    model_validator,
)

from zniku.authoring import (
    EngineStageNodeSpec,
    FinalNodeSpec,
    SourceNodeSpec,
    ValidationOutcome,
    WorkflowCompiler,
    WorkflowSpec,
)
from zniku.contracts import (
    Artifact,
    ContractModel,
    CoverageSpan,
    CoverageUnit,
    EngineBinding,
    JsonObject,
    Scope,
    Sha256Digest,
    StableId,
)
from zniku.contracts.base import freeze_json_object, thaw_json
from zniku.contracts.errors import ContractViolation, fail

from .operators import (
    CORE_OPERATOR_CONTRACT_VERSION,
    CoreOperatorKind,
    CoreOperatorNodeSpec,
)

PREFLIGHT_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"
EXECUTION_PLAN_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"
RUNTIME_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"


class ChapterMemberBinding(ContractModel):
    """把稳定章节成员身份绑定到 scope target 与连续 coverage。"""

    member_id: StableId
    scope_id: StableId
    coverage: CoverageSpan


class ChapterPlan(ContractModel):
    """Preflight 使用的有序、完整章节 authority。"""

    chapter_plan_id: StableId
    members: tuple[ChapterMemberBinding, ...]
    coverage: CoverageSpan

    @field_validator("members", mode="before")
    @classmethod
    def normalize_members(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_complete_coverage(self) -> ChapterPlan:
        if self.coverage.unit is not CoverageUnit.FRAME or not self.members:
            fail("E_CHAPTER_PLAN_EMPTY", "首版 ChapterPlan 必须包含 frame coverage 成员")
        ids = tuple(member.member_id for member in self.members)
        scopes = tuple(member.scope_id for member in self.members)
        if len(ids) != len(set(ids)) or len(scopes) != len(set(scopes)):
            fail("E_CHAPTER_PLAN_ID_DUPLICATE", "chapter member 和 scope ID 必须唯一")
        if self.members[0].coverage.start != self.coverage.start:
            fail("E_CHAPTER_PLAN_COVERAGE", "ChapterPlan 首成员未覆盖总体起点")
        if self.members[-1].coverage.end != self.coverage.end:
            fail("E_CHAPTER_PLAN_COVERAGE", "ChapterPlan 末成员未覆盖总体终点")
        for previous, current in zip(self.members, self.members[1:], strict=False):
            if (
                previous.coverage.unit is not self.coverage.unit
                or current.coverage.unit is not self.coverage.unit
                or previous.coverage.end != current.coverage.start
            ):
                fail("E_CHAPTER_PLAN_COVERAGE", "ChapterPlan coverage 必须连续、保序、无重叠")
        return self


class SourceBinding(ContractModel):
    """将 Source node 绑定到调用方提供的正式 Artifact identity 与 digest。"""

    source_node_id: StableId
    artifact_id: StableId
    artifact_digest: Sha256Digest


class WorkflowBindingSet(ContractModel):
    """Compilation/preflight 的完整 source 与章节 authority。"""

    binding_contract_version: Literal["0.1.0"]
    sources: tuple[SourceBinding, ...]
    chapter_plan: ChapterPlan | None = None

    @field_validator("sources", mode="before")
    @classmethod
    def normalize_sources(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_sources(self) -> WorkflowBindingSet:
        node_ids = tuple(source.source_node_id for source in self.sources)
        artifact_ids = tuple(source.artifact_id for source in self.sources)
        if len(node_ids) != len(set(node_ids)) or len(artifact_ids) != len(set(artifact_ids)):
            fail("E_SOURCE_BINDING_DUPLICATE", "Source 和 Artifact binding 必须唯一")
        return self


class PreflightResult(ContractModel):
    """Binding/preflight 的确定性结论；无副作用且不产生部分 Plan。"""

    preflight_contract_version: Literal["0.1.0"]
    spec_digest: Sha256Digest
    binding_digest: Sha256Digest
    valid: bool
    diagnostic_codes: tuple[StableId, ...]

    @field_validator("diagnostic_codes", mode="before")
    @classmethod
    def normalize_codes(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_outcome(self) -> PreflightResult:
        ordered = tuple(sorted(set(self.diagnostic_codes)))
        object.__setattr__(self, "diagnostic_codes", ordered)
        if self.valid == bool(ordered):
            fail("E_PREFLIGHT_OUTCOME_MISMATCH", "valid 必须由 diagnostics 是否为空决定")
        return self


def preflight_workflow(
    spec: WorkflowSpec,
    bindings: WorkflowBindingSet,
    artifacts: Mapping[str, Artifact],
    compiler: WorkflowCompiler,
) -> PreflightResult:
    """验证 authoring、source authority、章节 selector 和 Artifact digest。"""

    codes: set[str] = set()
    validation = compiler.validate(spec)
    if validation.outcome is not ValidationOutcome.AUTHORING_VALID:
        codes.update(item.stable_code for item in validation.diagnostics)
    sources = {node.node_id for node in spec.nodes if isinstance(node, SourceNodeSpec)}
    bound = {binding.source_node_id: binding for binding in bindings.sources}
    if set(bound) != sources:
        codes.add("E_PREFLIGHT_SOURCE_BINDING_INCOMPLETE")
    for binding in bindings.sources:
        artifact = artifacts.get(binding.artifact_id)
        if artifact is None:
            codes.add("E_PREFLIGHT_SOURCE_ARTIFACT_UNKNOWN")
        elif artifact.sha256_digest() != binding.artifact_digest:
            codes.add("E_PREFLIGHT_SOURCE_DIGEST_MISMATCH")
        elif artifact.scope is not Scope.PROGRAM:
            codes.add("E_PREFLIGHT_SOURCE_SCOPE")

    operators = tuple(node for node in spec.nodes if isinstance(node, CoreOperatorNodeSpec))
    if operators and bindings.chapter_plan is None:
        codes.add("E_PREFLIGHT_CHAPTER_PLAN_REQUIRED")
    if bindings.chapter_plan is not None:
        available = {member.member_id for member in bindings.chapter_plan.members}
        for node in operators:
            if node.operator_kind is not CoreOperatorKind.SELECT:
                continue
            selected = set(node.selected_member_ids)
            if not selected <= available:
                codes.add("E_PREFLIGHT_SELECTOR_UNKNOWN_MEMBER")
            if not available - selected:
                codes.add("E_PREFLIGHT_SELECTOR_REMAINDER_EMPTY")
    return PreflightResult(
        preflight_contract_version=PREFLIGHT_CONTRACT_VERSION,
        spec_digest=spec.sha256_digest(),
        binding_digest=bindings.sha256_digest(),
        valid=not codes,
        diagnostic_codes=tuple(codes),
    )


class PlannedSubjectKind(StrEnum):
    SOURCE = "source"
    ENGINE = "engine"
    OPERATOR = "operator"
    FINAL = "final"


class PlannedNode(ContractModel):
    """ExecutionPlan 中一个确定 scope 的执行主体。"""

    plan_node_id: StableId
    stage_spec_id: StableId
    subject_kind: PlannedSubjectKind
    scope: Scope
    scope_id: StableId
    dependencies: tuple[StableId, ...]
    engine: EngineBinding | None = None
    operator_kind: CoreOperatorKind | None = None
    parameters: JsonObject = Field(default_factory=dict)

    @field_validator("dependencies", mode="before")
    @classmethod
    def normalize_dependencies(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("parameters")
    @classmethod
    def freeze_parameters(cls, value: JsonObject) -> JsonObject:
        return freeze_json_object(value)

    @field_serializer("parameters")
    def serialize_parameters(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)

    @model_validator(mode="after")
    def validate_subject(self) -> PlannedNode:
        if self.subject_kind is PlannedSubjectKind.ENGINE and self.engine is None:
            fail("E_PLAN_ENGINE_REQUIRED", "Engine planned node 必须绑定 Engine")
        if self.subject_kind is PlannedSubjectKind.OPERATOR and self.operator_kind is None:
            fail("E_PLAN_OPERATOR_REQUIRED", "Operator planned node 必须声明 operator_kind")
        if self.subject_kind not in {PlannedSubjectKind.ENGINE} and self.engine is not None:
            fail("E_PLAN_ENGINE_FORBIDDEN", "非 Engine planned node 不得绑定 Engine")
        return self


class ExecutionPlan(ContractModel):
    """Compiler 根据完整 authority 产生的不可变、确定性执行图。"""

    plan_contract_version: Literal["0.1.0"]
    execution_plan_id: StableId
    workflow_id: StableId
    workflow_spec_digest: Sha256Digest
    binding_digest: Sha256Digest
    core_operator_contract_version: Literal["0.1.0"]
    nodes: tuple[SkipValidation[PlannedNode], ...]

    @field_validator("nodes", mode="before")
    @classmethod
    def normalize_nodes(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return tuple(
                PlannedNode.from_data(cast(Mapping[str, JsonValue], item))
                if isinstance(item, Mapping)
                else item
                for item in value
            )
        return value

    @model_validator(mode="after")
    def validate_graph(self) -> ExecutionPlan:
        ids = tuple(node.plan_node_id for node in self.nodes)
        if len(ids) != len(set(ids)):
            fail("E_PLAN_NODE_DUPLICATE", "plan_node_id 必须唯一")
        known = set(ids)
        for node in self.nodes:
            if not isinstance(node, PlannedNode) or not set(node.dependencies) <= known:
                fail("E_PLAN_DEPENDENCY_UNKNOWN", "Plan dependency 必须引用已知 planned node")
            if node.plan_node_id in node.dependencies:
                fail("E_PLAN_SELF_DEPENDENCY", "Plan node 不得依赖自身")
        finals = tuple(node for node in self.nodes if node.subject_kind is PlannedSubjectKind.FINAL)
        if len(finals) != 1:
            fail("E_PLAN_FINAL_CARDINALITY", "ExecutionPlan 必须恰好包含一个 Final")
        object.__setattr__(
            self, "nodes", tuple(sorted(self.nodes, key=lambda node: node.plan_node_id))
        )
        return self


def compile_execution_plan(
    spec: WorkflowSpec,
    bindings: WorkflowBindingSet,
    preflight: PreflightResult,
) -> ExecutionPlan:
    """把 preflight-valid WorkflowSpec 确定展开为 planned nodes。"""

    if not preflight.valid or preflight.spec_digest != spec.sha256_digest():
        raise ContractViolation(
            "E_PLAN_PREFLIGHT_INVALID", "只有匹配的 valid preflight 才能产生 Plan"
        )
    if preflight.binding_digest != bindings.sha256_digest():
        raise ContractViolation("E_PLAN_BINDING_MISMATCH", "Preflight 与 binding authority 不匹配")

    incoming: dict[str, set[str]] = defaultdict(set)
    for edge in spec.edges:
        incoming[edge.target.node_id].add(edge.source.node_id)
    expanded: dict[str, tuple[PlannedNode, ...]] = {}
    chapter_plan = bindings.chapter_plan
    source_bindings = {item.source_node_id: item for item in bindings.sources}
    for node in _topological_nodes(spec):
        planned_nodes: tuple[PlannedNode, ...]
        dependency_ids = tuple(
            sorted(
                planned.plan_node_id
                for source_id in incoming[node.node_id]
                for planned in expanded[source_id]
            )
        )
        if isinstance(node, SourceNodeSpec):
            source = source_bindings[node.node_id]
            planned_nodes = (
                PlannedNode(
                    plan_node_id=f"plan.{node.node_id}",
                    stage_spec_id=node.node_id,
                    subject_kind=PlannedSubjectKind.SOURCE,
                    scope=Scope.PROGRAM,
                    scope_id=source.artifact_id,
                    dependencies=(),
                ),
            )
        elif isinstance(node, EngineStageNodeSpec):
            planned_nodes = (
                PlannedNode(
                    plan_node_id=f"plan.{node.node_id}",
                    stage_spec_id=node.node_id,
                    subject_kind=PlannedSubjectKind.ENGINE,
                    scope=Scope.PROGRAM,
                    scope_id="program.bound",
                    dependencies=dependency_ids,
                    engine=node.engine,
                    parameters=node.parameters,
                ),
            )
        elif isinstance(node, CoreOperatorNodeSpec) and node.operator_kind is CoreOperatorKind.MAP:
            if (
                chapter_plan is None or node.engine is None
            ):  # pragma: no cover - preflight/model gate
                raise ContractViolation("E_PLAN_CHAPTER_AUTHORITY", "Map 缺少 chapter authority")
            selected_ids: set[str] | None = None
            for edge in spec.edges:
                if edge.target.node_id != node.node_id or edge.source.port_id != "selected":
                    continue
                source_node = next(
                    candidate
                    for candidate in spec.nodes
                    if candidate.node_id == edge.source.node_id
                )
                if (
                    isinstance(source_node, CoreOperatorNodeSpec)
                    and source_node.operator_kind is CoreOperatorKind.SELECT
                ):
                    selected_ids = set(source_node.selected_member_ids)
            planned_nodes = tuple(
                PlannedNode(
                    plan_node_id=f"plan.{node.node_id}.{member.member_id}",
                    stage_spec_id=node.node_id,
                    subject_kind=PlannedSubjectKind.ENGINE,
                    scope=Scope.CHAPTER,
                    scope_id=member.scope_id,
                    dependencies=dependency_ids,
                    engine=node.engine,
                    parameters=node.parameters,
                )
                for member in chapter_plan.members
                if selected_ids is None or member.member_id in selected_ids
            )
        elif isinstance(node, CoreOperatorNodeSpec):
            planned_nodes = (
                PlannedNode(
                    plan_node_id=f"plan.{node.node_id}",
                    stage_spec_id=node.node_id,
                    subject_kind=PlannedSubjectKind.OPERATOR,
                    scope=(
                        Scope.PROGRAM
                        if node.operator_kind
                        in {CoreOperatorKind.PARTITION, CoreOperatorKind.REDUCE}
                        else Scope.CHAPTER
                    ),
                    scope_id=(
                        "program.bound"
                        if node.operator_kind
                        in {CoreOperatorKind.PARTITION, CoreOperatorKind.REDUCE}
                        else cast(ChapterPlan, chapter_plan).chapter_plan_id
                    ),
                    dependencies=dependency_ids,
                    operator_kind=node.operator_kind,
                ),
            )
        else:
            assert isinstance(node, FinalNodeSpec)
            planned_nodes = (
                PlannedNode(
                    plan_node_id=f"plan.{node.node_id}",
                    stage_spec_id=node.node_id,
                    subject_kind=PlannedSubjectKind.FINAL,
                    scope=Scope.PROGRAM,
                    scope_id="program.bound",
                    dependencies=dependency_ids,
                ),
            )
        expanded[node.node_id] = planned_nodes

    nodes = tuple(planned for node in spec.nodes for planned in expanded[node.node_id])
    identity_seed = f"{spec.workflow_id}\0{spec.sha256_digest()}\0{bindings.sha256_digest()}"
    plan_id = f"execution_plan.{hashlib.sha256(identity_seed.encode()).hexdigest()[:24]}"
    return ExecutionPlan(
        plan_contract_version=EXECUTION_PLAN_CONTRACT_VERSION,
        execution_plan_id=plan_id,
        workflow_id=spec.workflow_id,
        workflow_spec_digest=spec.sha256_digest(),
        binding_digest=bindings.sha256_digest(),
        core_operator_contract_version=CORE_OPERATOR_CONTRACT_VERSION,
        nodes=nodes,
    )


def _topological_nodes(spec: WorkflowSpec) -> tuple[Any, ...]:
    nodes = {node.node_id: node for node in spec.nodes}
    incoming = dict.fromkeys(nodes, 0)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in spec.edges:
        incoming[edge.target.node_id] += 1
        outgoing[edge.source.node_id].append(edge.target.node_id)
    ready = sorted(node_id for node_id, count in incoming.items() if count == 0)
    ordered: list[Any] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(nodes[node_id])
        for target in sorted(outgoing[node_id]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(nodes):
        raise ContractViolation("E_PLAN_GRAPH_CYCLE", "ExecutionPlan 输入图包含 cycle")
    return tuple(ordered)


class WorkflowRevision(ContractModel):
    """冻结 WorkflowSpec、binding 与 ExecutionPlan digest 的不可变 revision。"""

    revision_contract_version: Literal["0.1.0"]
    revision_id: StableId
    workflow_id: StableId
    workflow_spec_digest: Sha256Digest
    binding_digest: Sha256Digest
    execution_plan_digest: Sha256Digest


def freeze_revision(
    spec: WorkflowSpec, bindings: WorkflowBindingSet, plan: ExecutionPlan
) -> WorkflowRevision:
    """执行无媒体副作用的精确 digest freeze gate。"""

    if (
        plan.workflow_id != spec.workflow_id
        or plan.workflow_spec_digest != spec.sha256_digest()
        or plan.binding_digest != bindings.sha256_digest()
    ):
        raise ContractViolation(
            "E_FREEZE_AUTHORITY_MISMATCH", "Spec、binding 与 Plan authority 不一致"
        )
    digest = plan.sha256_digest()
    return WorkflowRevision(
        revision_contract_version="0.1.0",
        revision_id=f"revision.{digest.removeprefix('sha256:')[:24]}",
        workflow_id=spec.workflow_id,
        workflow_spec_digest=spec.sha256_digest(),
        binding_digest=bindings.sha256_digest(),
        execution_plan_digest=digest,
    )


class NodeRunState(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class NodeRunRecord(ContractModel):
    plan_node_id: StableId
    state: NodeRunState
    attempt: int = Field(ge=0)
    evidence_id: StableId | None = None


class StageEvidence(ContractModel):
    """合成 Runtime 对一次 planned node 验证完成的最小证据。"""

    evidence_id: StableId
    plan_node_id: StableId
    attempt: int = Field(ge=1)
    verified: Literal[True]
    output_identity: StableId


class RuntimeSnapshot(ContractModel):
    runtime_contract_version: Literal["0.1.0"]
    workflow_run_id: StableId
    revision_id: StableId
    execution_plan_digest: Sha256Digest
    nodes: tuple[NodeRunRecord, ...]
    evidence: tuple[StageEvidence, ...]
    final_output_identity: StableId | None = None

    @field_validator("nodes", "evidence", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class SyntheticRuntime:
    """只执行合成 planned node 的确定性 Runtime authority。"""

    def __init__(self, plan: ExecutionPlan, revision: WorkflowRevision, snapshot: RuntimeSnapshot):
        if revision.execution_plan_digest != plan.sha256_digest():
            raise ContractViolation("E_RUNTIME_PLAN_MISMATCH", "Revision 与 Plan digest 不一致")
        if (
            snapshot.execution_plan_digest != plan.sha256_digest()
            or snapshot.revision_id != revision.revision_id
        ):
            raise ContractViolation(
                "E_RUNTIME_SNAPSHOT_MISMATCH", "Snapshot 与冻结 authority 不一致"
            )
        self._plan = plan
        self._revision = revision
        self._snapshot = snapshot
        self._derive_ready()

    @classmethod
    def start(
        cls, plan: ExecutionPlan, revision: WorkflowRevision, workflow_run_id: str
    ) -> SyntheticRuntime:
        snapshot = RuntimeSnapshot(
            runtime_contract_version=RUNTIME_CONTRACT_VERSION,
            workflow_run_id=workflow_run_id,
            revision_id=revision.revision_id,
            execution_plan_digest=plan.sha256_digest(),
            nodes=tuple(
                NodeRunRecord(plan_node_id=node.plan_node_id, state=NodeRunState.BLOCKED, attempt=0)
                for node in plan.nodes
            ),
            evidence=(),
        )
        return cls(plan, revision, snapshot)

    @property
    def snapshot(self) -> RuntimeSnapshot:
        return self._snapshot

    def ready_nodes(self) -> tuple[str, ...]:
        return tuple(
            record.plan_node_id
            for record in self._snapshot.nodes
            if record.state is NodeRunState.READY
        )

    def execute(self, plan_node_id: str, *, fail_attempt: bool = False) -> RuntimeSnapshot:
        """幂等执行一个 ready node；失败不会伪造 Evidence。"""

        records = {record.plan_node_id: record for record in self._snapshot.nodes}
        current = records.get(plan_node_id)
        if current is None:
            raise ContractViolation("E_RUNTIME_NODE_UNKNOWN", "未知 planned node")
        if current.state is NodeRunState.COMPLETE:
            return self._snapshot
        if current.state is not NodeRunState.READY:
            raise ContractViolation("E_RUNTIME_NODE_NOT_READY", "planned node 当前不可执行")
        attempt = current.attempt + 1
        if fail_attempt:
            records[plan_node_id] = current.model_copy(
                update={"state": NodeRunState.FAILED, "attempt": attempt}
            )
            self._replace_snapshot(records)
            return self._snapshot
        evidence_id = f"evidence.{self._snapshot.workflow_run_id}.{plan_node_id}.{attempt}"
        output_id = f"output.{self._snapshot.workflow_run_id}.{plan_node_id}.{attempt}"
        evidence = StageEvidence(
            evidence_id=evidence_id,
            plan_node_id=plan_node_id,
            attempt=attempt,
            verified=True,
            output_identity=output_id,
        )
        records[plan_node_id] = current.model_copy(
            update={
                "state": NodeRunState.COMPLETE,
                "attempt": attempt,
                "evidence_id": evidence_id,
            }
        )
        final_id = self._snapshot.final_output_identity
        planned = next(node for node in self._plan.nodes if node.plan_node_id == plan_node_id)
        if planned.subject_kind is PlannedSubjectKind.FINAL:
            if final_id is not None and final_id != output_id:
                raise ContractViolation("E_RUNTIME_FINAL_REPLACE", "Final identity 不得替换")
            final_id = output_id
        self._snapshot = self._snapshot.model_copy(
            update={
                "nodes": tuple(records.values()),
                "evidence": (*self._snapshot.evidence, evidence),
                "final_output_identity": final_id,
            }
        )
        self._derive_ready()
        return self._snapshot

    def retry(self, plan_node_id: str) -> RuntimeSnapshot:
        records = {record.plan_node_id: record for record in self._snapshot.nodes}
        current = records.get(plan_node_id)
        if current is None or current.state is not NodeRunState.FAILED:
            raise ContractViolation("E_RUNTIME_RETRY_FORBIDDEN", "只有 failed node 可以 retry")
        records[plan_node_id] = current.model_copy(update={"state": NodeRunState.READY})
        self._replace_snapshot(records)
        return self._snapshot

    def _replace_snapshot(self, records: Mapping[str, NodeRunRecord]) -> None:
        self._snapshot = self._snapshot.model_copy(update={"nodes": tuple(records.values())})
        self._derive_ready()

    def _derive_ready(self) -> None:
        records = {record.plan_node_id: record for record in self._snapshot.nodes}
        completed = {
            node_id for node_id, record in records.items() if record.state is NodeRunState.COMPLETE
        }
        for planned in self._plan.nodes:
            record = records[planned.plan_node_id]
            if record.state in {NodeRunState.COMPLETE, NodeRunState.FAILED, NodeRunState.RUNNING}:
                continue
            state = (
                NodeRunState.READY
                if set(planned.dependencies) <= completed
                else NodeRunState.BLOCKED
            )
            records[planned.plan_node_id] = record.model_copy(update={"state": state})
        self._snapshot = self._snapshot.model_copy(update={"nodes": tuple(records.values())})
