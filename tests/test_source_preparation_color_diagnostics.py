"""验证色彩诊断更清晰但拒绝边界不变；纯合成合同数据，不读真实媒体。

此回归不证明 unknown 可保留或可以进入处理链，也不把 FFprobe 合并视图当作原始声明。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from zniku.source_preparation.inspection import findings_for
from zniku.source_preparation.models import (
    T1_PROMOTED,
    DiagnosticReport,
    FileStat,
    InitialBitstreamClock,
    SourceSignal,
    VideoObservation,
    summary_for_report,
)


def _report(**changes: str) -> DiagnosticReport:
    video = VideoObservation(
        stream_index=0,
        codec="h264",
        width=320,
        height=180,
        pixel_format="yuv420p",
        sample_aspect_ratio="1:1",
        field_order="progressive",
        color_range="tv",
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
        chroma_location="left",
        rotation="0",
        r_frame_rate="30/1",
        avg_frame_rate="30/1",
        time_base="1/1000",
        frame_count=30,
        packet_count=30,
        first_pts="0/1",
        last_pts="29/30",
        missing_pts=0,
        duplicate_pts=0,
        backward_pts=0,
        best_effort_differences=0,
        max_clock_error="0/1",
        decode_errors=False,
        bitstream_clock=InitialBitstreamClock(status="not_fixed"),
    )
    return DiagnosticReport(
        original_media_artifact_id=str(uuid4()),
        source_stat=FileStat(size=1024, mtime_ns=1),
        final_stat=FileStat(size=1024, mtime_ns=1),
        ffprobe_version="synthetic-no-tool-execution",
        container="matroska",
        container_duration="1/1",
        extra_streams=0,
        chapter_count=0,
        target_frame_rate="30/1",
        video=VideoObservation.model_validate({**video.model_dump(), **changes}),
        audio=(),
        findings=(),
        candidate_strategies=(),
        elapsed_seconds=0.0,
    )


@pytest.mark.parametrize(
    "field,label",
    [
        ("color_primaries", "色度原色"),
        ("color_transfer", "传递特性"),
        ("color_space", "色彩矩阵"),
        ("color_range", "信号范围"),
        ("chroma_location", "色度采样位置"),
    ],
)
def test_undetermined_color_names_exact_field_without_claiming_absence(
    field: str, label: str
) -> None:
    report = _report(**{field: "unknown"})
    before = report.model_dump()
    findings = findings_for(report)
    assert len(findings) == 1
    assert findings[0].code == "E_SOURCE_PREPARATION_VIDEO_PROFILE_UNSUPPORTED"
    assert f"{label} ({field})" in findings[0].message
    assert "探测未确定" in findings[0].message
    assert "不等于视频损坏" in findings[0].message
    assert "不会猜测或自动补标签放行" in findings[0].message
    assert report.model_dump() == before
    summary = summary_for_report(report.model_copy(update={"findings": findings}))
    assert summary["status"] == "needs_preparation"
    assert summary["available_strategies"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("color_primaries", "bt2020"),
        ("color_transfer", "arib-std-b67"),
        ("color_space", "smpte170m"),
        ("color_range", "pc"),
        ("chroma_location", "center"),
        ("color_range", "unknown "),
    ],
)
def test_known_or_unrecognized_values_are_not_misreported_as_missing(
    field: str, value: str
) -> None:
    findings = findings_for(_report(**{field: value}))
    assert len(findings) == 1
    assert findings[0].code == "E_SOURCE_PREPARATION_VIDEO_PROFILE_UNSUPPORTED"
    assert f"{field}={value}" in findings[0].message
    assert "不在本链支持范围" in findings[0].message
    assert "探测未确定" not in findings[0].message


def test_partial_declaration_lists_only_unknown_fields_and_keeps_conflicts() -> None:
    findings = findings_for(
        _report(color_primaries="unknown", color_transfer="unknown", color_range="pc")
    )
    assert len(findings) == 1
    assert "色度原色 (color_primaries)" in findings[0].message
    assert "传递特性 (color_transfer)" in findings[0].message
    assert "color_range=pc" in findings[0].message
    assert "色彩矩阵 (color_space)" not in findings[0].message


def test_diagnostic_change_does_not_widen_signal_or_promote_strategy() -> None:
    assert not findings_for(_report())
    with pytest.raises(ValidationError):
        SourceSignal(color_primaries="unknown")  # type: ignore[arg-type]
    assert T1_PROMOTED is False
