"""合成音频尾差、编码延迟、HDR/逐帧变化及模型自洽的普通路线专项。"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import pytest

from test_source_preparation_kernel import _tool
from test_source_preparation_kernel import media as media
from test_work_source_kernel import _admit, _diagnose, _runner
from zniku.media.probe import MediaNodeError
from zniku.source_preparation.work_inspection import (
    findings_for,
    inspect_source,
    signal_observation,
)
from zniku.source_preparation.work_models import (
    FrameVariant,
    ProbeSignal,
    WorkSourceGate,
    clock_tolerance,
    resolve_interpretation,
    summary_for_report,
    work_copy_estimate_bytes,
)


def _audio_source(media: Path, tmp_path: Path) -> Path:
    audio = tmp_path / "source.aac"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=1.1",
            "-c:a",
            "aac",
            "-f",
            "adts",
            str(audio),
        ]
    )
    source = tmp_path / "audio.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(source),
        ]
    )
    return source


def test_aac_long_tail_is_supported_original_relationship(media: Path, tmp_path: Path) -> None:
    source = _audio_source(media, tmp_path)
    runner = _runner(tmp_path / "attempts")
    original, diagnosis = _diagnose(runner, source)
    gate = _admit(runner, original, diagnosis)
    track = gate.audio_bindings[0].tracks[0]
    assert track.codec == "aac" and Fraction(track.relative_start) == 0
    assert Fraction(track.relative_end) > Fraction(gate.source_frame_count) / Fraction(
        gate.frame_rate
    )
    assert gate.audio_video_start == "0/1"
    assert WorkSourceGate.model_validate_json(gate.model_dump_json()) == gate


def test_aac_priming_is_capability_limit_not_bad_source(media: Path, tmp_path: Path) -> None:
    candidate = tmp_path / "priming.mp4"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(candidate),
        ]
    )
    report = inspect_source(candidate, str(uuid4()), target_frame_rate="30/1")
    assert report.status == "unsupported"
    assert any(f.code.endswith("AUDIO_PRIMING_UNSUPPORTED") for f in report.findings)
    assert report.audio[0].skip_samples > 0


def test_hdr_probe_observation_cannot_be_overridden(media: Path, tmp_path: Path) -> None:
    candidate = tmp_path / "hdr.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-c",
            "copy",
            "-bsf:v",
            "h264_metadata=colour_primaries=9:transfer_characteristics=16:matrix_coefficients=9",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "smpte2084",
            "-colorspace",
            "bt2020nc",
            str(candidate),
        ]
    )
    report = inspect_source(candidate, str(uuid4()), target_frame_rate="30/1")
    assert report.status == "unsupported" and report.observed_signal is not None
    with pytest.raises(MediaNodeError, match="COLOR_UNSUPPORTED"):
        resolve_interpretation(report.observed_signal, "operator_confirmed_bt709_limited_left")


def test_midstream_change_then_restore_is_not_hidden(media: Path, tmp_path: Path) -> None:
    first = tmp_path / "first.h264"
    changed = tmp_path / "changed.h264"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-frames:v",
            "6",
            "-c:v",
            "libx264",
            "-bf",
            "0",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            str(first),
        ]
    )
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(first),
            "-c",
            "copy",
            "-bsf:v",
            "h264_metadata=matrix_coefficients=6",
            str(changed),
        ]
    )
    concatenated = tmp_path / "joined.h264"
    concatenated.write_bytes(first.read_bytes() + changed.read_bytes() + first.read_bytes())
    candidate = tmp_path / "changed.mkv"
    _tool(
        ["ffmpeg", "-v", "error", "-r", "30", "-i", str(concatenated), "-c", "copy", str(candidate)]
    )
    report = inspect_source(candidate, str(uuid4()), target_frame_rate="30/1")
    assert report.status == "unsupported" and report.observed_signal is not None
    assert "color_space" in report.observed_signal.changes
    assert report.video is not None and report.video.frame_count == 18


def test_closed_observation_summary_cannot_clear_known_conflict() -> None:
    variant = FrameVariant(
        width=64,
        height=48,
        pixel_format="yuv420p",
        sample_aspect_ratio="1:1",
        interlaced=False,
        signal=ProbeSignal(color_space="smpte170m"),
        count=30,
    )
    observed = signal_observation(
        {"width": 64, "height": 48, "pix_fmt": "yuv420p", "color_space": "bt709"}, (variant,)
    )
    forged = observed.model_copy(update={"conflicts": (), "unsupported": ()})
    with pytest.raises(MediaNodeError, match="COLOR_REPORT"):
        resolve_interpretation(forged, "operator_confirmed_bt709_limited_left")


def test_unsupported_header_is_report_not_fake_eof(media: Path, tmp_path: Path) -> None:
    candidate = tmp_path / "two-video.mkv"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-map",
            "0:v:0",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            str(candidate),
        ]
    )
    report = inspect_source(candidate, str(uuid4()), target_frame_rate="30/1")
    assert report.status == "unsupported" and report.inspection_scope == "header_only"
    assert report.video is None and report.observed_signal is None


def test_coarse_exact_mov_clock_is_supported_but_wrong_step_requires_preparation(
    media: Path,
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "coarse.mov"
    _tool(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(media),
            "-c",
            "copy",
            "-video_track_timescale",
            "30",
            str(candidate),
        ]
    )
    report = inspect_source(candidate, str(uuid4()), target_frame_rate="30/1")
    assert report.status == "direct", report.model_dump_json(indent=2)
    assert report.video is not None and report.observed_signal is not None
    assert report.video.time_base == "1/30" and report.video.max_clock_error == "0/1"
    assert clock_tolerance(Fraction(30), Fraction(1, 30)) == 0
    wrong = report.video.model_copy(update={"max_clock_error": "1/30"})
    status, _, _ = findings_for(wrong, report.observed_signal, report.audio, "30/1")
    assert status == "preparation_required"


def test_work_capacity_uses_actual_storage_and_span_is_not_duration(media: Path) -> None:
    report = inspect_source(media, str(uuid4()), target_frame_rate="30/1")
    assert report.video is not None
    small = work_copy_estimate_bytes(report.video)
    large = work_copy_estimate_bytes(
        report.video.model_copy(update={"pixel_format": "yuv444p10le"})
    )
    reserve = 16 * 1024**2
    assert large - reserve == 4 * (small - reserve)
    summary = summary_for_report(report)
    impacts = summary["preparation_impacts"]
    assert isinstance(impacts, dict) and "source_duration" not in impacts
    assert impacts["source_frame_span"] == "967/1000"
    assert impacts["working_duration"] == "1/1"
