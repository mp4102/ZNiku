"""验证真实媒体 Runtime 的 Plan 驱动执行、人工 handoff、Evidence 与持久恢复。"""

from pathlib import Path

import pytest

from zniku.contracts import ContractViolation
from zniku.realmedia import RealMediaCandidateRuntime
from zniku.validation import generate_short_media
from zniku.workflow.execution import NodeRunState


def _advance_manual(runtime: RealMediaCandidateRuntime) -> None:
    ready = runtime.ready_nodes()
    assert ready
    for node_id in ready:
        handoff = runtime.prepare_manual(node_id)
        runtime.create_acceptance_fixture(handoff.handoff_id)
        runtime.submit_manual(handoff.handoff_id)


def test_real_runtime_completes_and_recovers_same_run(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mkv"
    generate_short_media(reference)
    root = tmp_path / "run"
    runtime = RealMediaCandidateRuntime.create(
        root=root,
        reference=reference,
        clip_start_seconds=0,
        clip_duration_seconds=1,
    )
    assert runtime.ready_nodes() == ("plan.node.source",)

    runtime.execute("plan.node.source")
    runtime.execute("plan.node.demux")
    runtime.execute("plan.node.partition")
    _advance_manual(runtime)
    _advance_manual(runtime)
    runtime.execute("plan.node.reduce")

    evidence_before_failure = tuple(runtime.snapshot.evidence)
    with pytest.raises(ContractViolation, match="E_REAL_INJECTED_FAILURE"):
        runtime.execute("plan.node.video_encode", inject_failure=True)
    failed = next(
        item for item in runtime.snapshot.nodes if item.plan_node_id == "plan.node.video_encode"
    )
    assert failed.state is NodeRunState.FAILED
    assert runtime.snapshot.evidence == evidence_before_failure

    restored = RealMediaCandidateRuntime.open(root=root, reference=reference)
    assert restored.snapshot.sha256_digest() == runtime.snapshot.sha256_digest()
    restored.retry("plan.node.video_encode")
    restored.execute("plan.node.video_encode")
    restored.execute("plan.node.mux")

    final_target = root / "final" / "ZNIKU-real-media-acceptance-final.mkv"
    final_target.write_bytes(b"existing authority")
    with pytest.raises(ContractViolation, match="E_PUBLICATION_TARGET_EXISTS"):
        restored.execute("plan.node.final")
    assert final_target.read_bytes() == b"existing authority"
    final_target.unlink()
    restored.retry("plan.node.final")
    restored.execute("plan.node.final")

    verification = restored.snapshot.final_verification
    assert verification is not None and verification.verified
    assert verification.original_audio_hashes == verification.final_audio_hashes
    assert all(item.state is NodeRunState.COMPLETE for item in restored.snapshot.nodes)
    final_identity = verification.final_artifact_id

    second_restore = RealMediaCandidateRuntime.open(root=root, reference=reference)
    assert second_restore.snapshot.final_verification is not None
    assert second_restore.snapshot.final_verification.final_artifact_id == final_identity
    assert second_restore.execute("plan.node.final") == second_restore.snapshot


def test_restore_fails_closed_when_published_artifact_drifts(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mkv"
    generate_short_media(reference)
    root = tmp_path / "run"
    runtime = RealMediaCandidateRuntime.create(
        root=root,
        reference=reference,
        clip_start_seconds=0,
        clip_duration_seconds=1,
    )
    runtime.execute("plan.node.source")
    runtime.execute("plan.node.demux")
    artifact = next(
        item for item in runtime.snapshot.artifacts if item.plan_node_id == "plan.node.demux"
    )
    (root / Path(artifact.relative_path)).write_bytes(b"drift")

    with pytest.raises(ContractViolation, match="E_REAL_ARTIFACT_DRIFT"):
        RealMediaCandidateRuntime.open(root=root, reference=reference)
