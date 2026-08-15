"""执行 Phase 6 短真实媒体、故障注入、目标存储与长片规模门。"""

from __future__ import annotations

from pathlib import Path

import pytest

from zniku.contracts import ContractViolation, Scope
from zniku.validation import (
    ExecutionResourceLimits,
    publish_file_no_replace,
    run_long_film_gate,
    run_short_media_gate,
)
from zniku.workflow import CoreOperatorKind
from zniku.workflow.execution import (
    ExecutionPlan,
    NodeRunState,
    PlannedNode,
    PlannedSubjectKind,
    SyntheticRuntime,
    WorkflowRevision,
)


def test_short_real_media_gate_executes_ffmpeg_and_ffprobe(tmp_path: Path) -> None:
    probe = run_short_media_gate(tmp_path)

    assert probe.filename == "zniku-phase6-short.mkv"
    assert probe.video_codec == "ffv1"
    assert probe.frame_rate == "24000/1001"
    assert probe.width == 320
    assert probe.height == 180
    assert probe.video_stream_count == 1
    assert probe.audio_stream_count == 1
    assert probe.size_bytes == (tmp_path / probe.filename).stat().st_size


def test_target_publication_is_no_replace_and_digest_bound(tmp_path: Path) -> None:
    source_directory = tmp_path / "source"
    target_directory = tmp_path / "target"
    source_directory.mkdir()
    target_directory.mkdir()
    source = source_directory / "candidate.bin"
    source.write_bytes(b"ZNIKU phase 6 target publication\n" * 32)

    receipt = publish_file_no_replace(source, target_directory, "final.bin")
    target = target_directory / "final.bin"
    assert target.read_bytes() == source.read_bytes()
    assert receipt.target_name == "final.bin"
    assert receipt.size_bytes == target.stat().st_size
    assert not tuple(target_directory.glob(".zniku-stage-*"))

    with pytest.raises(ContractViolation, match="E_PUBLICATION_TARGET_EXISTS"):
        publish_file_no_replace(source, target_directory, "final.bin")
    assert target.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("fault_phase", ["after_stage", "before_commit"])
def test_fault_injection_leaves_no_partial_target_or_staging(
    tmp_path: Path, fault_phase: str
) -> None:
    source = tmp_path / "candidate.bin"
    target_directory = tmp_path / "target"
    target_directory.mkdir()
    source.write_bytes(b"fault injection payload")

    def inject(phase: str) -> None:
        if phase == fault_phase:
            raise RuntimeError(f"injected at {phase}")

    with pytest.raises(RuntimeError, match="injected"):
        publish_file_no_replace(
            source,
            target_directory,
            "final.bin",
            fault_hook=inject,
        )
    assert not (target_directory / "final.bin").exists()
    assert not tuple(target_directory.glob(".zniku-stage-*"))


def test_commit_race_preserves_competing_target(tmp_path: Path) -> None:
    source = tmp_path / "candidate.bin"
    target_directory = tmp_path / "target"
    target_directory.mkdir()
    source.write_bytes(b"candidate")
    target = target_directory / "final.bin"

    def race(phase: str) -> None:
        if phase == "before_commit":
            target.write_bytes(b"other publisher")

    with pytest.raises(ContractViolation, match="E_PUBLICATION_TARGET_RACE"):
        publish_file_no_replace(source, target_directory, "final.bin", fault_hook=race)
    assert target.read_bytes() == b"other publisher"
    assert not tuple(target_directory.glob(".zniku-stage-*"))


def test_runtime_fault_retry_reuses_completed_upstream_evidence() -> None:
    digest = "sha256:" + "1" * 64
    plan = ExecutionPlan(
        plan_contract_version="0.1.0",
        execution_plan_id="execution_plan.phase6.fault",
        workflow_id="workflow.phase6.fault",
        workflow_spec_digest=digest,
        binding_digest="sha256:" + "2" * 64,
        core_operator_contract_version="0.1.0",
        nodes=(
            PlannedNode(
                plan_node_id="plan.source",
                stage_spec_id="node.source",
                subject_kind=PlannedSubjectKind.SOURCE,
                scope=Scope.PROGRAM,
                scope_id="program.phase6.fault",
                dependencies=(),
            ),
            PlannedNode(
                plan_node_id="plan.operator",
                stage_spec_id="node.operator",
                subject_kind=PlannedSubjectKind.OPERATOR,
                scope=Scope.PROGRAM,
                scope_id="program.phase6.fault",
                dependencies=("plan.source",),
                operator_kind=CoreOperatorKind.REDUCE,
            ),
            PlannedNode(
                plan_node_id="plan.final",
                stage_spec_id="node.final",
                subject_kind=PlannedSubjectKind.FINAL,
                scope=Scope.PROGRAM,
                scope_id="program.phase6.fault",
                dependencies=("plan.operator",),
            ),
        ),
    )
    revision = WorkflowRevision(
        revision_contract_version="0.1.0",
        revision_id="revision.phase6.fault",
        workflow_id=plan.workflow_id,
        workflow_spec_digest=plan.workflow_spec_digest,
        binding_digest=plan.binding_digest,
        execution_plan_digest=plan.sha256_digest(),
    )
    runtime = SyntheticRuntime.start(plan, revision, "workflow_run.phase6.fault")
    runtime.execute("plan.source")
    source_evidence = runtime.snapshot.evidence[0]
    failed = runtime.execute("plan.operator", fail_attempt=True)

    failed_operator = next(node for node in failed.nodes if node.plan_node_id == "plan.operator")
    assert failed_operator.state is NodeRunState.FAILED
    assert failed.evidence == (source_evidence,)
    runtime.retry("plan.operator")
    runtime.execute("plan.operator")
    runtime.execute("plan.final")
    source_record = next(
        node for node in runtime.snapshot.nodes if node.plan_node_id == "plan.source"
    )
    assert source_record.attempt == 1
    assert runtime.snapshot.evidence[0] == source_evidence
    assert runtime.snapshot.final_output_identity is not None


def test_long_film_gate_expands_120_chapters_deterministically() -> None:
    result = run_long_film_gate()

    assert result.chapter_count == 120
    assert result.plan_node_count == 247
    assert result.duration_frames > 250_000
    assert result.canonical_bytes > 100_000
    assert result.compile_milliseconds < 5000


def test_long_film_resource_limits_fail_closed() -> None:
    with pytest.raises(ContractViolation, match="E_RESOURCE_CHAPTER_LIMIT"):
        run_long_film_gate(
            chapter_count=2,
            limits=ExecutionResourceLimits(
                max_chapters=1,
                max_plan_nodes=100,
                max_canonical_bytes=1_000_000,
            ),
        )

    with pytest.raises(ContractViolation, match="E_RESOURCE_PLAN_NODE_LIMIT"):
        run_long_film_gate(
            chapter_count=2,
            limits=ExecutionResourceLimits(
                max_chapters=2,
                max_plan_nodes=10,
                max_canonical_bytes=1_000_000,
            ),
        )
