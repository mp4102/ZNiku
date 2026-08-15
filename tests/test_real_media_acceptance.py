"""验证完整真实媒体候选门的报告、故障恢复和 no-replace 结论。"""

from pathlib import Path

from zniku.realmedia import RealMediaCandidateRuntime, run_real_media_acceptance
from zniku.validation import generate_short_media


def test_real_media_acceptance_report_is_closed_and_verified(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mkv"
    generate_short_media(reference)
    report = run_real_media_acceptance(
        reference=reference,
        root=tmp_path / "run",
        clip_start_seconds=0,
        clip_duration_seconds=1,
    )
    assert report.original_audio_hashes == report.final_audio_hashes
    assert report.restart_recovery_verified
    assert report.encode_failure_retry_verified
    assert report.final_no_replace_verified
    assert report.manual_outputs_are_acceptance_fixtures
    assert report.evidence_count == 11
    assert report.expected_final_frame_rate == "48000/1001"
    assert report.final_video_codec == "hevc"
    assert report.final_pixel_format == "yuv420p10le"
    restored = RealMediaCandidateRuntime.open(root=tmp_path / "run", reference=reference)
    mux_evidence = next(
        item for item in restored.snapshot.evidence if item.plan_node_id == "plan.node.mux"
    )
    assert len(mux_evidence.input_artifact_ids) == 2
    assert all("demux.1.001" not in item for item in mux_evidence.input_artifact_ids)
