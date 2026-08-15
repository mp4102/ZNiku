"""提供 Phase 6 长片规模与显式资源预算开发门。

墙钟结果只用于发现数量级回退，不是生产 SLA。输入完全由合成 Artifact 与 ChapterPlan 构成，不读取
长片媒体或模型文件。
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import Field

from zniku.contracts import (
    Artifact,
    ArtifactType,
    ContractModel,
    ContractViolation,
    CoverageSpan,
    CoverageUnit,
    MediaKind,
    Scope,
    Sha256Digest,
)
from zniku.pipelines import build_default_workflow
from zniku.workflow.execution import (
    ChapterMemberBinding,
    ChapterPlan,
    ExecutionPlan,
    SourceBinding,
    WorkflowBindingSet,
    compile_execution_plan,
    preflight_workflow,
)

PERFORMANCE_GATE_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"


class ExecutionResourceLimits(ContractModel):
    """冻结前可机器执行的 Plan 数量与 canonical payload 上限。"""

    max_chapters: int = Field(ge=1)
    max_plan_nodes: int = Field(ge=1)
    max_canonical_bytes: int = Field(ge=1024)


class LongFilmGateResult(ContractModel):
    """一次合成长片 gate 的可读结果。"""

    performance_contract_version: Literal["0.1.0"]
    duration_frames: int = Field(ge=1)
    chapter_count: int = Field(ge=1)
    plan_node_count: int = Field(ge=1)
    canonical_bytes: int = Field(ge=1)
    compile_milliseconds: int = Field(ge=0)
    plan_digest: Sha256Digest


DEFAULT_EXECUTION_LIMITS = ExecutionResourceLimits(
    max_chapters=256,
    max_plan_nodes=1024,
    max_canonical_bytes=4 * 1024 * 1024,
)


def validate_execution_resources(
    chapter_plan: ChapterPlan,
    plan: ExecutionPlan,
    limits: ExecutionResourceLimits = DEFAULT_EXECUTION_LIMITS,
) -> None:
    """在启动 Runtime 前拒绝超过明确预算的冻结 authority。"""

    if len(chapter_plan.members) > limits.max_chapters:
        raise ContractViolation("E_RESOURCE_CHAPTER_LIMIT", "chapter 数超过执行预算")
    if len(plan.nodes) > limits.max_plan_nodes:
        raise ContractViolation("E_RESOURCE_PLAN_NODE_LIMIT", "planned node 数超过执行预算")
    if len(plan.to_canonical_bytes()) > limits.max_canonical_bytes:
        raise ContractViolation("E_RESOURCE_PLAN_BYTES_LIMIT", "ExecutionPlan canonical bytes 超限")


def _chapter_plan(chapter_count: int, duration_frames: int) -> ChapterPlan:
    boundaries = tuple(
        round(index * duration_frames / chapter_count) for index in range(chapter_count + 1)
    )
    return ChapterPlan(
        chapter_plan_id="chapter_plan.phase6.long_film",
        members=tuple(
            ChapterMemberBinding(
                member_id=f"chapter.{index + 1:03d}",
                scope_id=f"scope.chapter.{index + 1:03d}",
                coverage=CoverageSpan(
                    unit=CoverageUnit.FRAME,
                    start=boundaries[index],
                    end=boundaries[index + 1],
                ),
            )
            for index in range(chapter_count)
        ),
        coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=duration_frames),
    )


def run_long_film_gate(
    *,
    chapter_count: int = 120,
    maximum_milliseconds: int = 5000,
    limits: ExecutionResourceLimits = DEFAULT_EXECUTION_LIMITS,
) -> LongFilmGateResult:
    """编译三小时、双 Map 默认流程并验证确定性与资源预算。"""

    duration_frames = round(3 * 60 * 60 * 24000 / 1001)
    source = Artifact(
        artifact_id="artifact.phase6.long_film.source",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.PROGRAM_MEDIA,
        scope=Scope.PROGRAM,
        scope_id="program.phase6.long_film",
        attributes={"duration_frames": duration_frames, "audio_stream_ids": ["audio.main"]},
    )
    chapters = _chapter_plan(chapter_count, duration_frames)
    bindings = WorkflowBindingSet(
        binding_contract_version="0.1.0",
        sources=(
            SourceBinding(
                source_node_id="node.source",
                artifact_id=source.artifact_id,
                artifact_digest=source.sha256_digest(),
            ),
        ),
        chapter_plan=chapters,
    )
    bundle = build_default_workflow()
    started = time.perf_counter_ns()
    preflight = preflight_workflow(
        bundle.spec, bindings, {source.artifact_id: source}, bundle.compiler
    )
    if not preflight.valid:
        raise ContractViolation("E_PERFORMANCE_PREFLIGHT", "长片合成 authority preflight 失败")
    plan = compile_execution_plan(bundle.spec, bindings, preflight)
    elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000)
    if elapsed_ms > maximum_milliseconds:
        raise ContractViolation("E_PERFORMANCE_BUDGET", "长片 Plan 编译超过开发门预算")
    validate_execution_resources(chapters, plan, limits)
    rebuilt = compile_execution_plan(bundle.spec, bindings, preflight)
    if rebuilt.sha256_digest() != plan.sha256_digest():
        raise ContractViolation("E_PERFORMANCE_PLAN_DRIFT", "相同 authority 重建 Plan digest 漂移")
    expected_nodes = chapter_count * 2 + 7
    if len(plan.nodes) != expected_nodes:
        raise ContractViolation("E_PERFORMANCE_EXPANSION_SHAPE", "双 Map 展开节点数不符合预期")
    return LongFilmGateResult(
        performance_contract_version=PERFORMANCE_GATE_CONTRACT_VERSION,
        duration_frames=duration_frames,
        chapter_count=chapter_count,
        plan_node_count=len(plan.nodes),
        canonical_bytes=len(plan.to_canonical_bytes()),
        compile_milliseconds=elapsed_ms,
        plan_digest=plan.sha256_digest(),
    )
