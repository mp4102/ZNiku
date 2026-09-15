"""以纯合成媒体事实固定 AV2.7 Source 准入，不读取真实素材或改写旧节点合同。"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from time import monotonic
from typing import Any

import pytest

from zniku.avenhance_v27.probe import (
    Av27AudioHeader,
    Av27MediaError,
    Av27MediaHeader,
    Av27VideoHeader,
)
from zniku.source_admission import probe as admission_probe
from zniku.source_admission import timeline as admission_timeline
from zniku.source_admission.probe import admit_source, validate_source_header
from zniku.source_admission.process import probe_lines
from zniku.source_admission.timeline import evaluate_timeline


def _header(path: Path, *, count: int = 1000, rate: Fraction = Fraction(30)) -> Av27MediaHeader:
    video = Av27VideoHeader(
        index=0,
        codec="h264",
        profile="High",
        codec_tag_string="avc1",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        frame_rate=rate,
        avg_frame_rate=rate,
        r_frame_rate=rate,
        time_base=Fraction(1, 90000),
        frame_count=count,
        sample_aspect_ratio="1:1",
        field_order="progressive",
        rotation=0,
        color_range="tv",
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
        chroma_location="left",
        hdr_side_data=(),
        duration_seconds=count / float(rate),
    )
    audio = Av27AudioHeader(
        index=1,
        codec="aac",
        profile="LC",
        extradata_hash=None,
        sample_rate=44100,
        channels=2,
        channel_layout="stereo",
        language=None,
        title=None,
        default=True,
        forced=False,
    )
    return Av27MediaHeader(
        path, "mov,mp4", count / float(rate), ("video", "audio"), (video,), (audio,), (), 0
    )


def _packets(count: int, *, rate: Fraction = Fraction(30)) -> Iterator[Mapping[str, object]]:
    for index in range(count):
        yield {"dts_time": str(index / float(rate)), "pts_time": str((index ^ 1) / float(rate))}


def _decoded(count: int, *, rate: Fraction = Fraction(30)) -> Iterator[Mapping[str, object]]:
    for index in range(count):
        yield {"best_effort_timestamp_time": str(index / float(rate)), "pts_time": "-1000"}


def test_equivalent_realistic_header_rate_and_nonmonotonic_packet_pts_pass(tmp_path: Path) -> None:
    """复现近似 avg/r 表示；不从 PTS 重排推断 DTS 时钟失败。"""
    rate = Fraction(30000, 1001)
    header = _header(tmp_path / "synthetic.mp4", count=1000, rate=rate)
    header = replace(
        header,
        videos=(
            replace(
                header.video,
                avg_frame_rate=Fraction(536592500, 17904321),
                frame_count=1,
            ),
        ),
    )
    canonical, signal, warnings = validate_source_header(header)
    cadence = evaluate_timeline(header, _packets(1000, rate=rate))
    assert canonical == rate
    assert signal["color_space"] == "bt709"
    assert warnings == ()
    assert cadence["confidence"] == "high"
    assert cadence["packet_count"] == 1000  # nb_frames=1 不是另一个 N 权威。


@pytest.mark.parametrize("missing", [None, "unknown", "unspecified", "none", " N/A "])
def test_missing_signal_fields_are_warnings(tmp_path: Path, missing: str | None) -> None:
    header = _header(tmp_path / "synthetic.mp4")
    header = replace(
        header,
        videos=(
            replace(
                header.video,
                color_primaries=missing,
                color_transfer=missing,
                field_order=missing,
                sample_aspect_ratio=missing,
            ),
        ),
    )
    _rate, signal, warnings = validate_source_header(header)
    assert len(warnings) == 4
    assert signal["color_primaries"] == "bt709"
    assert signal["sample_aspect_ratio"] == "1:1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("color_transfer", "smpte2084"),
        ("color_primaries", "bt2020"),
        ("color_range", "pc"),
        ("field_order", "tt"),
        ("sample_aspect_ratio", "4:3"),
        ("rotation", 90),
        ("hdr_side_data", ("mastering display",)),
        ("width", 1440),
    ],
)
def test_explicit_unsupported_signal_fails(tmp_path: Path, field: str, value: object) -> None:
    header = _header(tmp_path / "synthetic.mp4")
    changes: dict[str, Any] = {field: value}
    header = replace(header, videos=(replace(header.video, **changes),))
    with pytest.raises(Av27MediaError):
        validate_source_header(header)


@pytest.mark.parametrize("layout", ["extra_track", "chapters", "two_videos"])
def test_source_layout_fails_before_scan(tmp_path: Path, layout: str) -> None:
    header = _header(tmp_path / "synthetic.mp4")
    if layout == "extra_track":
        header = replace(header, streams=("video", "audio", "data"))
    elif layout == "chapters":
        header = replace(header, chapter_count=1)
    else:
        header = replace(header, videos=(header.video, header.video))
    with pytest.raises(Av27MediaError):
        validate_source_header(header)


def test_header_rate_disagreement_fails(tmp_path: Path) -> None:
    header = _header(tmp_path / "synthetic.mp4")
    header = replace(header, videos=(replace(header.video, r_frame_rate=Fraction(25)),))
    with pytest.raises(Av27MediaError, match="相互矛盾"):
        validate_source_header(header)


def test_dts_99_percent_boundary_does_not_consume_fallback(tmp_path: Path) -> None:
    def packets() -> Iterator[Mapping[str, object]]:
        for index, row in enumerate(_packets(1000)):
            yield dict(row, dts_time="N/A") if index < 10 else row

    def forbidden() -> Iterator[Mapping[str, object]]:
        yield from ()
        raise AssertionError("DTS 足够时不能进行昂贵 decoded fallback")

    cadence = evaluate_timeline(
        _header(tmp_path / "sample.mp4"), packets(), decoded_rows=forbidden()
    )
    assert cadence["analysis_clock"] == "dts"
    assert cadence["dts_ratio"] == 0.99
    assert cadence["confidence"] == "medium"


def test_dts_below_99_percent_falls_back_without_raw_pts_equality(tmp_path: Path) -> None:
    packets = [
        dict(row, dts_time="N/A") if index < 11 else row for index, row in enumerate(_packets(1000))
    ]
    cadence = evaluate_timeline(
        _header(tmp_path / "sample.mp4"), packets, decoded_rows=_decoded(1000)
    )
    assert cadence["analysis_clock"] == "decoded_best_effort_timestamp"
    assert cadence["packet_count"] == 1000
    assert cadence["confidence"] == "high"


def test_missing_decoded_timestamp_does_not_require_decoded_total_equals_packet_n(
    tmp_path: Path,
) -> None:
    packets = [{"pts_time": row["pts_time"]} for row in _packets(1000)]
    # 开头缺少一个展示时间戳，余下999个仍保持30FPS；不构造内部跨度缩短率。
    decoded = [row for index, row in enumerate(_decoded(1000)) if index != 0]
    cadence = evaluate_timeline(_header(tmp_path / "sample.mp4"), packets, decoded_rows=decoded)
    assert cadence["packet_count"] == 1000
    assert cadence["clock_sample_count"] == 999
    assert cadence["confidence"] == "medium"


@pytest.mark.parametrize("failure", ["low_cadence", "large_gap", "nonpositive", "short_duration"])
def test_medium_requirements_are_not_reduced_to_stable_ratio(tmp_path: Path, failure: str) -> None:
    header = _header(tmp_path / "sample.mp4")
    rows = list(_packets(1000))
    if failure == "short_duration":
        header = replace(header, videos=(replace(header.video, duration_seconds=30.0),))
    elif failure == "large_gap":
        rows[500] = dict(rows[500], dts_time=str(510 / 30))
    elif failure == "nonpositive":
        for index in range(100, 900, 10):
            rows[index] = dict(rows[index], dts_time=str((index - 2) / 30))
    else:
        for index in range(1, 999, 2):
            rows[index] = dict(rows[index], dts_time=str(index / 30 + 0.002))
    with pytest.raises(Av27MediaError, match="E_AV27_SOURCE_FPS_AMBIGUOUS"):
        evaluate_timeline(header, rows)


def test_cadence_must_be_equivalent_to_header_not_just_span_within_point_one_percent(
    tmp_path: Path,
) -> None:
    rate = Fraction(27)
    header = _header(tmp_path / "sample.mp4", rate=rate)
    # 非常规 FPS 不会被广播吸附掩盖；0.0003% 在旧0.1%跨度门内但超过AV2.7 2ppm。
    with pytest.raises(Av27MediaError, match="header 与全片 cadence"):
        evaluate_timeline(header, _packets(1000, rate=rate * Fraction(1_000_003, 1_000_000)))


def test_video_duration_takes_precedence_over_longer_container_audio(tmp_path: Path) -> None:
    header = replace(_header(tmp_path / "sample.mp4"), duration_seconds=50.0)
    assert evaluate_timeline(header, _packets(1000))["confidence"] == "high"


def test_admission_builds_bounded_summary_and_reads_packet_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "synthetic.mp4"
    path.write_bytes(b"synthetic marker, not media")
    header = _header(path)
    calls: list[list[str]] = []

    def lines(argv: list[str], **kwargs: object) -> Iterable[str]:
        calls.append(argv)
        assert "-show_packets" in argv
        for row in _packets(1000):
            yield "|".join(f"{key}={value}" for key, value in row.items())

    monkeypatch.setattr(admission_timeline, "probe_lines", lines)
    result = admit_source(path, header=header, ffprobe_executable="synthetic-ffprobe")
    summary = result.namespace_summary()
    assert len(calls) == 1
    assert summary["frame_count"] == 1000
    assert summary["frame_rate"] == "30/1"
    assert len(json.dumps(summary)) < 5000
    assert path.read_bytes() == b"synthetic marker, not media"


def test_unknown_layout_does_not_start_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "synthetic.mp4"
    path.write_bytes(b"synthetic")
    header = replace(_header(path), streams=("video", "data"))

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("header 不通过不能遍历媒体")

    monkeypatch.setattr(admission_probe, "scan_source_timeline", forbidden)
    with pytest.raises(Av27MediaError, match="E_AV27_STREAM_UNSUPPORTED"):
        admit_source(path, header=header)


def test_probe_pipe_cancel_is_prompt_even_when_child_silent() -> None:
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    started = monotonic()
    timer.start()
    try:
        with pytest.raises(Av27MediaError, match="E_SOURCE_ADMISSION_CANCELLED"):
            list(
                probe_lines(
                    [sys.executable, "-c", "import time; time.sleep(10)"], cancel_event=cancel
                )
            )
    finally:
        timer.cancel()
    assert monotonic() - started < 3


def test_probe_pipe_callback_error_propagates_without_media_error_wrapper() -> None:
    def cancelled() -> None:
        raise RuntimeError("runtime target no longer running")

    with pytest.raises(RuntimeError, match="runtime target"):
        list(
            probe_lines([sys.executable, "-c", "import time; time.sleep(10)"], heartbeat=cancelled)
        )


def test_probe_pipe_rejects_unbounded_line_and_nonzero_exit() -> None:
    with pytest.raises(Av27MediaError, match="64 KiB"):
        list(probe_lines([sys.executable, "-c", "print('x' * 70000)"]))
    with pytest.raises(Av27MediaError, match="deliberate failure"):
        list(
            probe_lines(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('deliberate failure', file=sys.stderr); sys.exit(7)",
                ]
            )
        )


def test_probe_pipe_success_stderr_is_not_extra_decode_proof_gate() -> None:
    lines = list(
        probe_lines(
            [
                sys.executable,
                "-c",
                "import sys; print('diagnostic', file=sys.stderr); print('dts_time=0')",
            ]
        )
    )
    assert [line.strip() for line in lines] == ["dts_time=0"]


def test_progress_uses_observed_work_and_remains_monotonic_across_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "synthetic.mp4"
    path.write_bytes(b"synthetic")
    fractions: list[float] = []

    class Reporter:
        def report(self, fraction: float, **kwargs: object) -> None:
            fractions.append(fraction)

    def lines(argv: list[str], **kwargs: Any) -> Iterator[str]:
        decoded = "-show_frames" in argv
        for row in _decoded(1000) if decoded else _packets(1000):
            kwargs["heartbeat"]()
            values = dict(row) if decoded else dict(row, dts_time="N/A")
            yield "|".join(f"{key}={value}" for key, value in values.items())

    monkeypatch.setattr(admission_timeline, "probe_lines", lines)
    result = admit_source(
        path, header=_header(path), ffprobe_executable="synthetic", progress=Reporter()
    )
    assert result.timeline.authority == "decoded_presentation"
    assert fractions[0] == 0.0 and fractions[-1] == 1.0
    assert fractions == sorted(fractions)
    assert any(0.1 < value < 0.5 for value in fractions)
    assert any(0.8 < value < 0.9 for value in fractions)


def test_delta_temporary_streams_are_closed_after_contract_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tempfile.TemporaryFile
    opened: list[Any] = []

    def tracked(*args: Any, **kwargs: Any) -> Any:
        stream = temporary(*args, **kwargs)
        opened.append(stream)
        return stream

    monkeypatch.setattr(tempfile, "TemporaryFile", tracked)
    with pytest.raises(Av27MediaError):
        evaluate_timeline(
            _header(tmp_path / "synthetic.mp4"),
            [{"pts_time": "0"}],
            decoded_rows=[{"best_effort_timestamp_time": "0"}],
        )
    assert len(opened) == 2
    assert all(stream.closed for stream in opened)


def test_header_json_parse_and_invalid_number_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "synthetic.mp4"
    path.write_bytes(b"synthetic")
    payload: dict[str, object] = {
        "format": {"format_name": "mov,mp4", "duration": "10"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
                "avg_frame_rate": "30/1",
                "time_base": "1/90000",
                "duration": "broken-number",
            }
        ],
    }

    def lines(*args: object, **kwargs: object) -> Iterator[str]:
        yield json.dumps(payload)

    monkeypatch.setattr(admission_probe, "probe_lines", lines)
    with pytest.raises(Av27MediaError, match="E_AV27_PROBE_INVALID"):
        admission_probe.probe_header(path, ffprobe_executable="synthetic")


def test_decoded_clock_coverage_is_required_even_with_stable_remaining_frames(
    tmp_path: Path,
) -> None:
    rows = [{"pts_time": row["pts_time"]} for row in _packets(1000)]
    decoded = [row for index, row in enumerate(_decoded(1000)) if index >= 11]
    with pytest.raises(Av27MediaError, match="置信度不足"):
        evaluate_timeline(_header(tmp_path / "synthetic.mp4"), rows, decoded_rows=decoded)


def test_self_contained_parity_scenarios_do_not_require_external_repository() -> None:
    parity = importlib.import_module("tools.check_source_admission_parity")
    assert parity.run_scenarios() == {
        "scenarios": 63,
        "admitted": 39,
        "rejected": 24,
        "external_reference": None,
        "media_io": False,
        "result": "passed",
    }
