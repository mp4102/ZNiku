"""验证真实媒体验收候选的固定 FFmpeg 原语和 fail-closed 媒体证明。"""

from pathlib import Path

import pytest

from zniku.contracts import ContractViolation
from zniku.realmedia import (
    concat_video,
    decode_verify,
    demux_media,
    derive_acceptance_clip,
    encode_hevc_main10,
    extract_chapter,
    hash_audio_stream,
    make_acceptance_fixture,
    mux_original_audio,
    probe_detailed,
)
from zniku.validation import generate_short_media


def test_trusted_real_media_flow_preserves_original_audio(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mkv"
    generate_short_media(reference)
    clip = tmp_path / "clip.mkv"
    clip_probe = derive_acceptance_clip(reference, clip, start_seconds=0, duration_seconds=1)
    assert len(clip_probe.video_streams) == 1
    assert len(clip_probe.audio_streams) == 1

    video = tmp_path / "demux-video.mkv"
    audio = tmp_path / "demux-audio-001.mka"
    demux_media(clip, video, (audio,))
    original_audio_digest = hash_audio_stream(audio, 0)
    video_probe = probe_detailed(video)
    frames = video_probe.video_streams[0].frame_count
    assert frames is not None and frames >= 2
    split = frames // 2

    chapter_a = tmp_path / "chapter-a.mkv"
    chapter_b = tmp_path / "chapter-b.mkv"
    extract_chapter(video, chapter_a, start_frame=0, end_frame=split)
    extract_chapter(video, chapter_b, start_frame=split, end_frame=frames)
    enhanced_a = tmp_path / "enhanced-a.mkv"
    enhanced_b = tmp_path / "enhanced-b.mkv"
    make_acceptance_fixture(chapter_a, enhanced_a, operation="enhancement")
    make_acceptance_fixture(chapter_b, enhanced_b, operation="enhancement")
    interpolated_a = tmp_path / "interpolated-a.mkv"
    interpolated_b = tmp_path / "interpolated-b.mkv"
    make_acceptance_fixture(enhanced_a, interpolated_a, operation="frame_interpolation")
    make_acceptance_fixture(enhanced_b, interpolated_b, operation="frame_interpolation")

    reduced = tmp_path / "reduced.mkv"
    concat_video((interpolated_a, interpolated_b), reduced)
    encoded = tmp_path / "encoded.mkv"
    encode_hevc_main10(reduced, encoded, crf=35)
    final = tmp_path / "muxed.mkv"
    mux_original_audio(encoded, (audio,), final)
    decode_verify(final)

    final_probe = probe_detailed(final)
    assert final_probe.video_streams[0].codec_name == "hevc"
    assert final_probe.video_streams[0].frame_count == frames * 2
    assert len(final_probe.audio_streams) == 1
    assert hash_audio_stream(final, 0) == original_audio_digest


def test_media_outputs_are_no_replace(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mkv"
    generate_short_media(reference)
    output = tmp_path / "existing.mkv"
    output.write_bytes(b"authority")

    with pytest.raises(ContractViolation, match="E_REAL_MEDIA_OUTPUT_EXISTS"):
        derive_acceptance_clip(reference, output, start_seconds=0, duration_seconds=1)

    assert output.read_bytes() == b"authority"


def test_probe_rejects_non_media(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.bin"
    invalid.write_bytes(b"not media")
    with pytest.raises(ContractViolation, match="E_REAL_MEDIA_TOOL_FAILED"):
        probe_detailed(invalid)
