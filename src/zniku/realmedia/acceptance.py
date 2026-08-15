"""执行 0.1.0 Real Media Acceptance Candidate 的确定性端到端验收门。

该门显式使用 acceptance fixture 完成人工 Engine 生命周期，不声称验证生产模型画质。它在真实 encode
和 Final 前分别注入失败，证明失败不产生 Evidence、重启后必须显式 retry，随后完成 full verification。
报告只保存媒体身份和验证事实，不包含绝对路径。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Literal

from pydantic import Field

from zniku.contracts import ContractModel, ContractViolation, Sha256Digest
from zniku.workflow.execution import NodeRunState

from .media import hash_file
from .runtime import RealMediaCandidateRuntime


class RealMediaAcceptanceReport(ContractModel):
    """真实媒体候选的可分享最小结果；不包含 source/root 绝对路径。"""

    acceptance_contract_version: Literal["0.1.0"]
    workflow_run_id: str
    reference_filename: str
    reference_digest: Sha256Digest
    execution_plan_digest: Sha256Digest
    chapter_frame_ranges: tuple[tuple[int, int], ...]
    source_frame_rate: str
    expected_final_frame_rate: str
    final_frame_rate: str
    final_frame_count: int = Field(ge=1)
    final_video_codec: Literal["hevc"]
    final_pixel_format: Literal["yuv420p10le"]
    final_audio_stream_count: int = Field(ge=1)
    final_artifact_id: str
    final_digest: Sha256Digest
    evidence_count: int = Field(ge=1)
    original_audio_hashes: tuple[Sha256Digest, ...]
    final_audio_hashes: tuple[Sha256Digest, ...]
    restart_recovery_verified: Literal[True]
    encode_failure_retry_verified: Literal[True]
    final_no_replace_verified: Literal[True]
    manual_outputs_are_acceptance_fixtures: Literal[True]


def run_real_media_acceptance(
    *,
    reference: Path,
    root: Path,
    clip_start_seconds: int = 28,
    clip_duration_seconds: int = 4,
) -> RealMediaAcceptanceReport:
    """创建全新工作根并完成真实媒体纵向候选；已有 root 一律拒绝。"""

    reference_size_before, reference_digest_before = hash_file(reference)
    runtime = RealMediaCandidateRuntime.create(
        root=root,
        reference=reference,
        clip_start_seconds=clip_start_seconds,
        clip_duration_seconds=clip_duration_seconds,
    )
    for node_id in ("plan.node.source", "plan.node.demux", "plan.node.partition"):
        runtime.execute(node_id)
    _complete_ready_manual(runtime)

    runtime = RealMediaCandidateRuntime.open(root=root, reference=reference)
    _complete_ready_manual(runtime)
    runtime.execute("plan.node.reduce")

    evidence_before_encode_failure = len(runtime.snapshot.evidence)
    try:
        runtime.execute("plan.node.video_encode", inject_failure=True)
    except ContractViolation as error:
        if error.code != "E_REAL_INJECTED_FAILURE":
            raise
    else:  # pragma: no cover - gate 防御
        raise AssertionError("编码故障注入未失败")
    if len(runtime.snapshot.evidence) != evidence_before_encode_failure:
        raise ContractViolation("E_REAL_ACCEPTANCE_FALSE_EVIDENCE", "编码失败产生了 Evidence")
    runtime = RealMediaCandidateRuntime.open(root=root, reference=reference)
    runtime.retry("plan.node.video_encode")
    runtime.execute("plan.node.video_encode")
    runtime.execute("plan.node.mux")

    runtime = RealMediaCandidateRuntime.open(root=root, reference=reference)
    target = root / "final" / "ZNIKU-real-media-acceptance-final.mkv"
    target.write_bytes(b"acceptance no-replace sentinel")
    evidence_before_final_failure = len(runtime.snapshot.evidence)
    try:
        runtime.execute("plan.node.final")
    except ContractViolation as error:
        if error.code != "E_PUBLICATION_TARGET_EXISTS":
            raise
    else:  # pragma: no cover - gate 防御
        raise AssertionError("Final no-replace 故障注入未失败")
    if target.read_bytes() != b"acceptance no-replace sentinel":
        raise ContractViolation("E_REAL_ACCEPTANCE_REPLACED", "Final 覆盖了既有 authority")
    if len(runtime.snapshot.evidence) != evidence_before_final_failure:
        raise ContractViolation("E_REAL_ACCEPTANCE_FALSE_EVIDENCE", "Final 失败产生了 Evidence")
    target.unlink()
    runtime.retry("plan.node.final")
    runtime.execute("plan.node.final")

    runtime = RealMediaCandidateRuntime.open(root=root, reference=reference)
    verification = runtime.snapshot.final_verification
    if verification is None or not all(
        item.state is NodeRunState.COMPLETE for item in runtime.snapshot.nodes
    ):
        raise ContractViolation("E_REAL_ACCEPTANCE_INCOMPLETE", "真实媒体候选未完整完成")
    if verification.original_audio_hashes != verification.final_audio_hashes:
        raise ContractViolation("E_REAL_ACCEPTANCE_AUDIO", "原始音频 bitstream 证明不相等")
    source_video = runtime.authority.source_artifact.attributes
    source_rate = str(source_video["frame_rate"])
    expected_rate = Fraction(source_rate) * 2
    if abs(float(Fraction(verification.video_frame_rate) / expected_rate) - 1) > 0.00001:
        raise ContractViolation("E_REAL_ACCEPTANCE_RATE", "Final 帧率不是 source 的精确双倍序列")
    reference_size_after, reference_digest_after = hash_file(reference)
    if (reference_size_after, reference_digest_after) != (
        reference_size_before,
        reference_digest_before,
    ):
        raise ContractViolation("E_REAL_REFERENCE_DRIFT", "验收期间参考源发生变化")
    final_artifact = next(
        item
        for item in runtime.snapshot.artifacts
        if item.artifact_id == verification.final_artifact_id
    )
    final_video = final_artifact.probe.video_streams[0]
    if final_video.codec_name != "hevc" or final_video.pixel_format != "yuv420p10le":
        raise ContractViolation("E_REAL_ACCEPTANCE_VIDEO", "Final 不满足 HEVC Main10 合同")
    return RealMediaAcceptanceReport(
        acceptance_contract_version="0.1.0",
        workflow_run_id=runtime.snapshot.workflow_run_id,
        reference_filename=runtime.authority.reference_probe.filename,
        reference_digest=reference_digest_after,
        execution_plan_digest=runtime.snapshot.execution_plan_digest,
        chapter_frame_ranges=tuple(
            (member.coverage.start, member.coverage.end)
            for member in runtime.authority.chapter_plan.members
        ),
        source_frame_rate=source_rate,
        expected_final_frame_rate=f"{expected_rate.numerator}/{expected_rate.denominator}",
        final_frame_rate=verification.video_frame_rate,
        final_frame_count=verification.video_frame_count,
        final_video_codec="hevc",
        final_pixel_format="yuv420p10le",
        final_audio_stream_count=len(final_artifact.probe.audio_streams),
        final_artifact_id=verification.final_artifact_id,
        final_digest=verification.final_digest,
        evidence_count=len(runtime.snapshot.evidence),
        original_audio_hashes=verification.original_audio_hashes,
        final_audio_hashes=verification.final_audio_hashes,
        restart_recovery_verified=True,
        encode_failure_retry_verified=True,
        final_no_replace_verified=True,
        manual_outputs_are_acceptance_fixtures=True,
    )


def _complete_ready_manual(runtime: RealMediaCandidateRuntime) -> None:
    ready = runtime.ready_nodes()
    if not ready:
        raise ContractViolation("E_REAL_ACCEPTANCE_MANUAL_READY", "缺少预期人工 ready set")
    for node_id in ready:
        manifest = runtime.manifest_for(node_id)
        if manifest is None or manifest.execution_mode.value != "manual_external":
            raise ContractViolation("E_REAL_ACCEPTANCE_MANUAL_SHAPE", "ready set 含非人工节点")
        handoff = runtime.prepare_manual(node_id)
        runtime.create_acceptance_fixture(handoff.handoff_id)
        runtime.submit_manual(handoff.handoff_id)
