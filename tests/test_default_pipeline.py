"""验证 Phase 3 默认工作流、人工 handoff、full verification 与 no-replace 恢复。"""

from __future__ import annotations

import hashlib

import pytest

from zniku.authoring import ValidationOutcome
from zniku.contracts import (
    Artifact,
    ArtifactType,
    ContractViolation,
    CoverageSpan,
    CoverageUnit,
    MediaKind,
    Scope,
)
from zniku.pipelines import (
    DefaultRunSnapshot,
    DefaultWorkflowBundle,
    DefaultWorkflowRuntime,
    ExternalOutputSubmission,
    build_default_workflow,
)
from zniku.workflow.execution import (
    ChapterMemberBinding,
    ChapterPlan,
    ExecutionPlan,
    NodeRunState,
    SourceBinding,
    WorkflowBindingSet,
    WorkflowRevision,
    compile_execution_plan,
    freeze_revision,
    preflight_workflow,
)


def _source() -> Artifact:
    return Artifact(
        artifact_id="artifact.default.source",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.PROGRAM_MEDIA,
        scope=Scope.PROGRAM,
        scope_id="program.default",
        attributes={
            "duration_frames": 300,
            "audio_stream_ids": ["audio.japanese", "audio.english"],
        },
    )


def _chapter_plan() -> ChapterPlan:
    return ChapterPlan(
        chapter_plan_id="chapter_plan.default",
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


def _compiled() -> tuple[DefaultWorkflowBundle, ExecutionPlan, WorkflowRevision]:
    bundle = build_default_workflow()
    source = _source()
    bindings = WorkflowBindingSet(
        binding_contract_version="0.1.0",
        sources=(
            SourceBinding(
                source_node_id="node.source",
                artifact_id=source.artifact_id,
                artifact_digest=source.sha256_digest(),
            ),
        ),
        chapter_plan=_chapter_plan(),
    )
    preflight = preflight_workflow(
        bundle.spec,
        bindings,
        {source.artifact_id: source},
        bundle.compiler,
    )
    assert preflight.valid
    plan = compile_execution_plan(bundle.spec, bindings, preflight)
    revision = freeze_revision(bundle.spec, bindings, plan)
    return bundle, plan, revision


def _start() -> DefaultWorkflowRuntime:
    bundle, plan, revision = _compiled()
    return DefaultWorkflowRuntime.start(
        plan=plan,
        revision=revision,
        manifests=bundle.manifests,
        source_artifact=_source(),
        chapter_plan=_chapter_plan(),
        workflow_run_id="workflow_run.default",
    )


def _submission(runtime: DefaultWorkflowRuntime, plan_node_id: str) -> ExternalOutputSubmission:
    handoff = runtime.prepare_manual(plan_node_id)
    digest = hashlib.sha256(handoff.handoff_id.encode()).hexdigest()
    return ExternalOutputSubmission(
        handoff_id=handoff.handoff_id,
        candidate_artifact_id=f"artifact.external.{plan_node_id}.{handoff.attempt}",
        candidate_digest=f"sha256:{digest}",
        source_authority_digest=_source().sha256_digest(),
        frame_count=handoff.expected_output_frames,
        completed_leaf_ids=handoff.leaf_ids,
    )


def _finish(runtime: DefaultWorkflowRuntime, *, fail_encode_once: bool = False) -> None:
    encode_failed = False
    while runtime.ready_nodes():
        for node_id in runtime.ready_nodes():
            if ".enhancement." in node_id or ".frame_interpolation." in node_id:
                runtime.submit_external_output(_submission(runtime, node_id))
            elif node_id == "plan.node.video_encode" and fail_encode_once and not encode_failed:
                runtime.execute_automatic(node_id, fail_attempt=True)
                runtime.retry(node_id)
                encode_failed = True
            else:
                runtime.execute_automatic(node_id)


def test_default_workflow_is_formal_and_audio_is_explicit() -> None:
    bundle = build_default_workflow()
    result = bundle.compiler.validate(bundle.spec)

    assert result.outcome is ValidationOutcome.AUTHORING_VALID
    assert {node.node_id for node in bundle.spec.nodes} == {
        "node.source",
        "node.demux",
        "node.partition",
        "node.enhancement",
        "node.frame_interpolation",
        "node.reduce",
        "node.video_encode",
        "node.mux",
        "node.final",
    }
    audio_edge = next(
        edge for edge in bundle.spec.edges if edge.edge_id == "edge.original-audio-mux"
    )
    assert (audio_edge.source.node_id, audio_edge.source.port_id) == (
        "node.demux",
        "audio_out",
    )
    assert (audio_edge.target.node_id, audio_edge.target.port_id) == ("node.mux", "audio_in")


def test_chained_maps_pair_same_chapter_in_execution_plan() -> None:
    _bundle, plan, _revision = _compiled()
    interpolation = tuple(
        item for item in plan.nodes if item.stage_spec_id == "node.frame_interpolation"
    )

    assert len(interpolation) == 3
    for item in interpolation:
        assert item.dependencies == (
            f"plan.node.enhancement.chapter.{item.scope_id.rsplit('.', maxsplit=1)[-1]}",
        )


def test_default_run_requires_handoffs_and_publishes_full_verified_final() -> None:
    runtime = _start()
    with pytest.raises(ContractViolation, match="E_HANDOFF_MESSAGE_NOT_AUTHORITY"):
        runtime.report_manual_completion("已完成")

    _finish(runtime)

    assert all(item.state is NodeRunState.COMPLETE for item in runtime.snapshot.runtime.nodes)
    assert len(runtime.snapshot.handoffs) == 6
    assert runtime.snapshot.full_verification is not None
    verification = runtime.snapshot.full_verification
    assert verification.mode == "full"
    assert verification.frame_count == 600
    assert verification.ordered_audio_stream_ids == ("audio.japanese", "audio.english")
    assert verification.audio_artifact_set_id == runtime.snapshot.audio_proof.artifact_set_id
    assert runtime.snapshot.audio_proof.stream_copy is True
    assert len(verification.evidence_ids) == len(runtime.snapshot.runtime.nodes)


def test_external_output_fails_closed_on_authority_frames_and_leaf_set() -> None:
    runtime = _start()
    runtime.execute_automatic("plan.node.source")
    runtime.execute_automatic("plan.node.demux")
    runtime.execute_automatic("plan.node.partition")
    node_id = runtime.ready_nodes()[0]
    valid = _submission(runtime, node_id)

    with pytest.raises(ContractViolation, match="E_SOURCE_AUTHORITY_DRIFT"):
        runtime.submit_external_output(
            valid.model_copy(update={"source_authority_digest": "sha256:" + "0" * 64})
        )
    with pytest.raises(ContractViolation, match="E_FULL_VERIFY_FRAME_COUNT"):
        runtime.submit_external_output(valid.model_copy(update={"frame_count": 99}))
    with pytest.raises(ContractViolation, match="E_FULL_VERIFY_LEAF_SET"):
        runtime.submit_external_output(valid.model_copy(update={"completed_leaf_ids": ()}))


def test_one_shot_encode_failure_restarts_and_final_stays_unique() -> None:
    runtime = _start()
    _finish(runtime, fail_encode_once=True)

    encode = next(
        item
        for item in runtime.snapshot.runtime.nodes
        if item.plan_node_id == "plan.node.video_encode"
    )
    publications = tuple(
        item
        for item in runtime.snapshot.publications
        if item.plan_node_id == "plan.node.video_encode"
    )
    assert encode.attempt == 2
    assert len(publications) == 1
    assert publications[0].artifact_id.endswith(".2")
    assert runtime.snapshot.full_verification is not None


def test_snapshot_round_trip_restores_without_agent_or_gui_context() -> None:
    bundle, plan, revision = _compiled()
    runtime = DefaultWorkflowRuntime.start(
        plan=plan,
        revision=revision,
        manifests=bundle.manifests,
        source_artifact=_source(),
        chapter_plan=_chapter_plan(),
        workflow_run_id="workflow_run.default",
    )
    runtime.execute_automatic("plan.node.source")
    restored_snapshot = DefaultRunSnapshot.from_json(runtime.snapshot.to_canonical_bytes())
    restored = DefaultWorkflowRuntime(plan, revision, bundle.manifests, restored_snapshot)

    _finish(restored)
    assert restored.snapshot.full_verification is not None
    assert restored.snapshot.source_artifact.sha256_digest() == _source().sha256_digest()


def test_completed_manual_stage_cannot_replace_published_output() -> None:
    runtime = _start()
    runtime.execute_automatic("plan.node.source")
    runtime.execute_automatic("plan.node.demux")
    runtime.execute_automatic("plan.node.partition")
    node_id = runtime.ready_nodes()[0]
    first = _submission(runtime, node_id)
    runtime.submit_external_output(first)

    with pytest.raises(ContractViolation, match="E_PUBLICATION_REPLACE"):
        runtime.submit_external_output(
            first.model_copy(update={"candidate_artifact_id": "artifact.external.replacement"})
        )
