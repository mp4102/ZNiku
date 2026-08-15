"""把真实媒体 Runtime authority 投影为 Studio 可验证的窄化只读 DTO。

投影不携带绝对路径、FFmpeg argv 或可写状态；Studio 只能把 stable command intent 交给本地 host，随后
重新读取本投影。JSON Schema 由本模块的 Pydantic authority 生成并纳入 drift gate。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from zniku.contracts import ContractModel, Sha256Digest, StableId

from .runtime import RealMediaCandidateRuntime


class RealMonitorChapter(ContractModel):
    member_id: StableId
    scope_id: StableId
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)


class RealMonitorNode(ContractModel):
    plan_node_id: StableId
    stage_spec_id: StableId
    subject_kind: str
    scope: str
    execution_mode: Literal["automatic", "manual_external", "runtime"]
    state: str
    attempt: int = Field(ge=0)
    evidence_id: StableId | None


class RealMonitorHandoff(ContractModel):
    handoff_id: StableId
    plan_node_id: StableId
    attempt: int = Field(ge=1)
    input_artifact_id: StableId
    input_relative_path: str
    candidate_relative_path: str
    expected_frame_count: int = Field(ge=1)
    expected_frame_rate: str
    fixture_operation: str
    candidate_exists: bool


class RealMonitorProjection(ContractModel):
    """Studio Run Monitor 的 fresh read model。"""

    monitor_contract_version: Literal["0.1.0"]
    workflow_run_id: StableId
    revision_id: StableId
    execution_plan_digest: Sha256Digest
    reference_filename: str
    reference_digest: Sha256Digest
    chapter_split_source_second: Literal[30]
    chapters: tuple[RealMonitorChapter, ...]
    nodes: tuple[RealMonitorNode, ...]
    handoffs: tuple[RealMonitorHandoff, ...]
    ready_node_ids: tuple[StableId, ...]
    artifact_count: int = Field(ge=1)
    evidence_count: int = Field(ge=0)
    final_verified: bool
    final_artifact_id: StableId | None
    final_digest: Sha256Digest | None


class RealHostEnvelope(ContractModel):
    """host 未启动和已启动状态的闭合 union 外壳。"""

    host_contract_version: Literal["0.1.0"]
    candidate_exists: bool
    projection: RealMonitorProjection | None


def build_monitor_projection(runtime: RealMediaCandidateRuntime) -> RealMonitorProjection:
    """从 fresh Runtime snapshot 构造确定性 Studio projection。"""

    snapshot = runtime.snapshot
    authority = runtime.authority
    records = {item.plan_node_id: item for item in snapshot.nodes}
    nodes = []
    for planned in authority.plan.nodes:
        record = records[planned.plan_node_id]
        if planned.engine is None:
            execution_mode: Literal["automatic", "manual_external", "runtime"] = "runtime"
        else:
            manifest = runtime.manifest_for(planned.plan_node_id)
            if manifest is None:  # pragma: no cover - planned engine 已由 Runtime 校验
                raise AssertionError("Engine planned node 缺少 Manifest")
            execution_mode = manifest.execution_mode.value
        nodes.append(
            RealMonitorNode(
                plan_node_id=planned.plan_node_id,
                stage_spec_id=planned.stage_spec_id,
                subject_kind=planned.subject_kind.value,
                scope=planned.scope.value,
                execution_mode=execution_mode,
                state=record.state.value,
                attempt=record.attempt,
                evidence_id=record.evidence_id,
            )
        )
    handoffs = tuple(
        RealMonitorHandoff(
            handoff_id=item.handoff_id,
            plan_node_id=item.plan_node_id,
            attempt=item.attempt,
            input_artifact_id=item.input_artifact_id,
            input_relative_path=item.input_relative_path,
            candidate_relative_path=item.candidate_relative_path,
            expected_frame_count=item.expected_frame_count,
            expected_frame_rate=item.expected_frame_rate,
            fixture_operation=item.fixture_operation,
            candidate_exists=(runtime.root / item.candidate_relative_path).is_file(),
        )
        for item in snapshot.handoffs
    )
    verification = snapshot.final_verification
    return RealMonitorProjection(
        monitor_contract_version="0.1.0",
        workflow_run_id=snapshot.workflow_run_id,
        revision_id=snapshot.revision_id,
        execution_plan_digest=snapshot.execution_plan_digest,
        reference_filename=authority.reference_probe.filename,
        reference_digest=snapshot.source_reference_digest,
        chapter_split_source_second=30,
        chapters=tuple(
            RealMonitorChapter(
                member_id=member.member_id,
                scope_id=member.scope_id,
                start_frame=member.coverage.start,
                end_frame=member.coverage.end,
            )
            for member in authority.chapter_plan.members
        ),
        nodes=tuple(nodes),
        handoffs=handoffs,
        ready_node_ids=runtime.ready_nodes(),
        artifact_count=len(snapshot.artifacts),
        evidence_count=len(snapshot.evidence),
        final_verified=verification is not None,
        final_artifact_id=verification.final_artifact_id if verification else None,
        final_digest=verification.final_digest if verification else None,
    )


def build_host_envelope(runtime: RealMediaCandidateRuntime | None) -> RealHostEnvelope:
    return RealHostEnvelope(
        host_contract_version="0.1.0",
        candidate_exists=runtime is not None,
        projection=build_monitor_projection(runtime) if runtime is not None else None,
    )
