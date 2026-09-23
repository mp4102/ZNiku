"""视频自身展示跨度验收；合成 MKV 的长音轨不得替代视频时长，不触碰真实素材。"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from test_source_admission_probe import _header
from test_source_aligned_media import media
from test_source_aligned_node_contracts import pipeline
from zniku.avenhance_v27.probe import AV27_NAMESPACE, Av27MediaError, Av27MediaHeader
from zniku.source_admission import timeline as admission_timeline
from zniku.source_admission.contracts import NAMESPACE, SOURCE_NAMESPACE
from zniku.source_admission.mosaic_restoration import inspect_candidate
from zniku.source_admission.probe import admit_source, probe_header
from zniku.source_admission.timeline import evaluate_timeline

_TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _mkv_header(path: Path, *, count: int = 1000) -> Av27MediaHeader:
    header = _header(path, count=count)
    return replace(
        header,
        format_name="matroska,webm",
        duration_seconds=50.0,
        videos=(replace(header.video, duration_seconds=None),),
    )


def _packets(*, count: int = 1000, origin: float = 0.0) -> list[dict[str, object]]:
    # 最后读取的 packet 并非最后展示的画面；端点必须根据 PTS 而不是读取顺序确定。
    return [
        {
            "dts_time": str(origin + index / 30),
            "pts_time": str(origin + (index ^ 1) / 30),
            "duration_time": str(1 / 30),
        }
        for index in range(count)
    ]


def _decoded(*, count: int = 1000, origin: float = 0.0) -> list[dict[str, object]]:
    return [
        {
            "best_effort_timestamp_time": str(origin + index / 30),
            "pts_time": "-1000",  # 不以 raw PTS 替代已经选中的展示时钟。
            "duration_time": str(1 / 30),
        }
        for index in range(count)
    ]


@pytest.mark.parametrize("origin", [0.0, 7.0, -7.0])
def test_missing_video_duration_uses_presentation_span_not_container(
    tmp_path: Path, origin: float
) -> None:
    header = _mkv_header(tmp_path / "synthetic.mkv")
    cadence = evaluate_timeline(header, _packets(origin=origin))
    assert cadence["duration_seconds"] == pytest.approx(1000 / 30)
    assert cadence["duration_source"] == "packet_presentation"
    assert cadence["analysis_clock"] == "dts"
    assert cadence["confidence"] == "high"


def test_container_duration_may_be_absent_when_video_span_is_complete(tmp_path: Path) -> None:
    header = replace(_mkv_header(tmp_path / "synthetic.mkv"), duration_seconds=None)
    assert evaluate_timeline(header, _packets())["duration_seconds"] == pytest.approx(1000 / 30)


def test_short_container_duration_does_not_truncate_complete_video_span(tmp_path: Path) -> None:
    header = replace(_mkv_header(tmp_path / "synthetic.mkv"), duration_seconds=1.0)
    assert evaluate_timeline(header, _packets())["duration_seconds"] == pytest.approx(1000 / 30)


@pytest.mark.parametrize("invalid", [0.0, -1.0, float("nan"), float("inf")])
def test_explicit_invalid_video_duration_is_not_replaced_by_other_facts(
    tmp_path: Path, invalid: float
) -> None:
    header = _header(tmp_path / "synthetic.mp4")
    header = replace(header, videos=(replace(header.video, duration_seconds=invalid),))
    with pytest.raises(Av27MediaError, match="E_AV27_SOURCE_FPS_AMBIGUOUS"):
        evaluate_timeline(header, _packets())


def test_video_stream_duration_remains_authoritative_when_present(tmp_path: Path) -> None:
    header = replace(_header(tmp_path / "synthetic.mp4"), duration_seconds=50.0)
    rows = _packets()
    for row in rows:
        row.pop("pts_time")
        row.pop("duration_time")
    cadence = evaluate_timeline(header, rows)
    assert cadence["duration_source"] == "video_stream"
    assert cadence["duration_seconds"] == 1000 / 30
    invalid = replace(header, videos=(replace(header.video, duration_seconds=30.0),))
    with pytest.raises(Av27MediaError, match="N/FPS/duration"):
        evaluate_timeline(invalid, _packets())


@pytest.mark.parametrize("index", [0, 501, 998, 999])
@pytest.mark.parametrize("invalid", [None, "N/A", "nan", "inf"])
def test_missing_or_nonfinite_presentation_timestamp_cannot_use_container_fallback(
    tmp_path: Path, index: int, invalid: object
) -> None:
    header = replace(_mkv_header(tmp_path / "synthetic.mkv"), duration_seconds=1000 / 30)
    rows = _packets()
    rows[index]["pts_time"] = invalid
    with pytest.raises(Av27MediaError, match="E_AV27_SOURCE_FPS_AMBIGUOUS"):
        evaluate_timeline(header, rows)


@pytest.mark.parametrize("invalid", [None, "N/A", "nan", "inf", "0", "-0.033333333"])
def test_terminal_packet_duration_must_be_positive_and_finite(
    tmp_path: Path, invalid: object
) -> None:
    rows = _packets()
    rows[998]["duration_time"] = invalid  # PTS 最大值不是最后读取的 packet。
    header = replace(_mkv_header(tmp_path / "synthetic.mkv"), duration_seconds=1000 / 30)
    with pytest.raises(Av27MediaError, match="E_AV27_SOURCE_FPS_AMBIGUOUS"):
        evaluate_timeline(header, rows)


def test_nonterminal_packet_duration_is_not_required_for_endpoints(tmp_path: Path) -> None:
    rows = _packets()
    for index, row in enumerate(rows):
        if index != 998:
            row.pop("duration_time")
    cadence = evaluate_timeline(_mkv_header(tmp_path / "synthetic.mkv"), rows)
    assert cadence["duration_seconds"] == pytest.approx(1000 / 30)


@pytest.mark.parametrize("failure", ["long_last_frame", "late_pts", "missing_packet"])
def test_complete_video_endpoints_cannot_hide_nonclosing_duration(
    tmp_path: Path, failure: str
) -> None:
    rows = _packets()
    if failure == "long_last_frame":
        rows[998]["duration_time"] = "0.2"
    elif failure == "late_pts":
        rows[998]["pts_time"] = str(999 / 30 + 0.2)
    else:
        rows.pop(500)
    header = replace(_mkv_header(tmp_path / "synthetic.mkv"), duration_seconds=len(rows) / 30)
    with pytest.raises(Av27MediaError, match="E_AV27_SOURCE_FPS_AMBIGUOUS"):
        evaluate_timeline(header, rows)


@pytest.mark.parametrize("duration_field", ["duration_time", "pkt_duration_time"])
def test_decoded_fallback_uses_best_effort_relative_span(
    tmp_path: Path, duration_field: str
) -> None:
    packets = _packets(origin=7.0)
    for row in packets:
        row.pop("dts_time")
    decoded = _decoded(origin=7.0)
    if duration_field == "pkt_duration_time":
        for row in decoded:
            row[duration_field] = row.pop("duration_time")
    cadence = evaluate_timeline(
        _mkv_header(tmp_path / "synthetic.mkv"), packets, decoded_rows=decoded
    )
    assert cadence["duration_source"] == "decoded_presentation"
    assert cadence["duration_seconds"] == pytest.approx(1000 / 30)
    assert cadence["analysis_clock"] == "decoded_best_effort_timestamp"


@pytest.mark.parametrize(
    "failure",
    ["one_less", "one_more", "extra_missing_timestamp", "missing_timestamp", "no_duration"],
)
def test_decoded_fallback_needs_complete_endpoints_for_missing_stream_duration(
    tmp_path: Path, failure: str
) -> None:
    packets = _packets()
    for row in packets:
        row.pop("dts_time")
    decoded = _decoded(count=1001 if failure in {"one_more", "extra_missing_timestamp"} else 1000)
    if failure == "one_less":
        decoded.pop(0)
    elif failure == "missing_timestamp":
        decoded[500]["best_effort_timestamp_time"] = "N/A"
    elif failure == "extra_missing_timestamp":
        # 可用的 1000 个时间戳不能掩盖实际读取了 1001 帧，必须同时检查 decoded 总数。
        decoded[-1]["best_effort_timestamp_time"] = "N/A"
    elif failure == "no_duration":
        decoded[-1].pop("duration_time")
    with pytest.raises(Av27MediaError, match="E_AV27_SOURCE_FPS_AMBIGUOUS"):
        evaluate_timeline(_mkv_header(tmp_path / "synthetic.mkv"), packets, decoded_rows=decoded)


@pytest.mark.parametrize("decoded_fallback", [False, True])
def test_one_packet_scan_requests_duration_and_summary_uses_validated_video_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decoded_fallback: bool
) -> None:
    path = tmp_path / "synthetic.mkv"
    path.write_bytes(b"synthetic marker, not media")
    before = path.stat()
    calls: list[list[str]] = []

    def lines(argv: list[str], **kwargs: object) -> Iterator[str]:
        calls.append(argv)
        decoded = "-show_frames" in argv
        entries = argv[argv.index("-show_entries") + 1]
        requested = set(entries.split("=", 1)[1].split(","))
        expected = (
            {"best_effort_timestamp_time", "duration_time", "pkt_duration_time"}
            if decoded
            else {"pts_time", "dts_time", "duration_time"}
        )
        assert expected <= requested
        for row in _decoded() if decoded else _packets():
            values: Mapping[str, object] = row
            if decoded_fallback and not decoded:
                values = {key: value for key, value in row.items() if key != "dts_time"}
            yield "|".join(f"{key}={value}" for key, value in values.items())

    monkeypatch.setattr(admission_timeline, "probe_lines", lines)
    result = admit_source(path, header=_mkv_header(path), ffprobe_executable="synthetic-ffprobe")
    summary = result.namespace_summary()
    assert len(calls) == (2 if decoded_fallback else 1)
    assert sum("-show_packets" in call for call in calls) == 1
    assert summary["duration_seconds"] == pytest.approx(1000 / 30)
    assert summary["duration_seconds"] == result.cadence["duration_seconds"]
    video_summary = summary["video"]
    assert isinstance(video_summary, dict)
    assert video_summary["duration_seconds"] is None  # 保留 header 缺字段事实，不伪造 header。
    assert result.cadence["duration_source"] == (
        "decoded_presentation" if decoded_fallback else "packet_presentation"
    )
    after = path.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)


def _long_audio_mkv(tmp_path: Path) -> Path:
    """仅生成 4 秒合成画面和 5 秒静音，作为音轨较长的 MKV 反例。"""
    path = tmp_path / "synthetic-long-audio.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x36:rate=25:duration=4",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo:d=5",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            "-chroma_sample_location",
            "left",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.skipif(not _TOOLS, reason="requires ffmpeg/ffprobe")
def test_synthetic_mkv_long_audio_does_not_reject_shorter_video(tmp_path: Path) -> None:
    """真实 FFprobe 缺失 stream.duration 的 MKV，只按自身 4 秒视频验收。"""
    path = _long_audio_mkv(tmp_path)
    before = path.stat()
    header = probe_header(path)
    assert header.video.duration_seconds is None
    assert header.duration_seconds == pytest.approx(5.0)
    admitted = admit_source(path, header=header)
    assert admitted.timeline.frame_count == 100
    assert admitted.frame_rate == Fraction(25)
    assert admitted.cadence["duration_source"] == "packet_presentation"
    assert admitted.namespace_summary()["duration_seconds"] == pytest.approx(4.0)
    after = path.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)


@pytest.mark.skipif(not _TOOLS, reason="requires ffmpeg/ffprobe")
def test_mosaic_restoration_candidate_keeps_source_binding_with_long_audio_mkv(
    tmp_path: Path,
) -> None:
    """候选只读验收仍要求参考源的 N/FPS/几何/信号，音轨尾部不构成视频变化。"""
    step = pipeline(tmp_path, n=100, chapters=1, rate="25/1")[0]
    original, gate = step.inputs
    source_path = tmp_path / "synthetic-source.mp4"
    media(source_path, rate="25/1", frames=100)
    source_admission = admit_source(source_path)
    original = replace(
        original,
        path=source_path,
        port_id="video",
        ordinal=None,
        media_info={
            AV27_NAMESPACE: source_admission.namespace_summary(),
            SOURCE_NAMESPACE: {"contract_version": "0.3.5"},
        },
    )
    candidate = _long_audio_mkv(tmp_path)
    before = {path: path.stat() for path in (source_path, candidate)}
    inspected = inspect_candidate(
        (original, gate),
        {
            "source": step.parameters["source"],
            "declared_container": "mp4",
            "model_name": "synthetic",
            "operator_frame_order_confirmed": True,
        },
        candidate,
    )
    assert inspected.container == "mkv"
    assert inspected.archive_name == "synthetic-source.RM.mkv"
    assert inspected.summary["frame_count"] == source_admission.timeline.frame_count == 100
    metadata = inspected.media_info[NAMESPACE]
    assert isinstance(metadata, dict)
    source = metadata["source"]
    assert isinstance(source, dict)
    assert source["frame_rate"] == "25/1"
    assert source["original_video_artifact_id"] == original.artifact_id
    assert not candidate.with_name(inspected.archive_name).exists()
    for path, previous in before.items():
        current = path.stat()
        assert (previous.st_size, previous.st_mtime_ns) == (current.st_size, current.st_mtime_ns)
