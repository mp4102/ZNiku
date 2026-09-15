"""按 AVEnhanceFlow 2.7 的单一时钟、置信度与闭合条件验收 Source。

每次只完整读取一次 packet，DTS 覆盖不足才读取 decoded best-effort 时间线。N 始终来自
packet，header nb_frames 不再成为第二权威。只将浮点相邻差值写入自动关闭的 TemporaryFile，
结束后回读差值以复现先确定 cadence 再统计比例的规则；RAM 恒定，不保留逐包 PTS 账本。
"""

from __future__ import annotations

import math
import struct
import tempfile
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from zniku.avenhance_v27.probe import (
    Av27MediaError,
    Av27MediaHeader,
    SourceTimeline,
    canonical_fraction,
    canonical_source_rate,
    rates_equivalent,
    snap_frame_rate,
)
from zniku.media.probe import resolve_media_tool
from zniku.runtime.progress import ProgressReporter

from .process import check_cancel, probe_lines


class _DeltaStream(Protocol):
    """只依赖匿名临时二进制流的最小接口，兼容 Windows TemporaryFile 包装。"""

    def write(self, data: bytes, /) -> int: ...

    def read(self, size: int, /) -> bytes: ...

    def seek(self, offset: int, /) -> int: ...


@dataclass(slots=True)
class _Clock:
    """只保留计数、端点和相邻差值，不保存可恢复的逐帧位置记录。"""

    deltas: _DeltaStream
    total: int = 0
    pts_count: int = 0
    sample_count: int = 0
    first: float | None = None
    last: float | None = None
    delta_count: int = 0
    positive_count: int = 0
    max_delta: float | None = None

    def add(self, timestamp: float | None) -> None:
        if timestamp is None:
            return
        if self.first is None:
            self.first = timestamp
        if self.last is not None and math.isfinite(delta := timestamp - self.last):
            self.delta_count += 1
            self.deltas.write(struct.pack("<d", delta))
            if delta > 0:
                self.positive_count += 1
                self.max_delta = delta if self.max_delta is None else max(self.max_delta, delta)
        self.sample_count += 1
        self.last = timestamp


def _timestamp(value: object) -> float | None:
    try:
        number = float(str(value))
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def _fields(line: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in line.strip().split("|") if "=" in item)


def _consume(clock: _Clock, rows: Iterable[Mapping[str, object]], *, decoded: bool) -> None:
    for row in rows:
        clock.total += 1
        if _timestamp(row.get("pts_time")) is not None:
            clock.pts_count += 1
        clock.add(_timestamp(row.get("best_effort_timestamp_time" if decoded else "dts_time")))


def _cadence(
    clock: _Clock,
    *,
    packet_count: int,
    pts_count: int,
    dts_count: int,
    header: Av27MediaHeader,
    analysis_clock: str,
    cancel_event: threading.Event | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, object]:
    rate = canonical_source_rate(header.video)
    duration = header.video.duration_seconds or header.duration_seconds
    if duration is None or not math.isfinite(duration) or duration <= 0 or packet_count <= 1:
        raise Av27MediaError("E_AV27_SOURCE_FPS_AMBIGUOUS", "Source 无法形成有效 N/FPS/时长")
    span_fps = (
        (clock.sample_count - 1) / (clock.last - clock.first)
        if clock.first is not None and clock.last is not None and clock.last > clock.first
        else None
    )
    cadence_rate = snap_frame_rate(Fraction(str(span_fps or packet_count / duration)))
    expected_delta = 1 / float(cadence_rate)
    tolerance = max(float(header.video.time_base) * 1.1, expected_delta * 0.03, 0.000002)
    stable_count = 0
    clock.deltas.seek(0)
    while block := clock.deltas.read(64 * 1024):
        check_cancel(cancel_event)
        if heartbeat is not None:
            heartbeat()
        stable_count += sum(
            abs(value - expected_delta) <= tolerance for (value,) in struct.iter_unpack("<d", block)
        )
    stable_ratio = stable_count / clock.delta_count if clock.delta_count else 0.0
    timestamp_ratio = clock.sample_count / packet_count
    positive_ratio = clock.positive_count / clock.delta_count if clock.delta_count else 0.0
    span_consistent = (
        span_fps is not None and abs(span_fps - float(cadence_rate)) / float(cadence_rate) <= 0.001
    )
    high = (
        packet_count >= 1000
        and stable_ratio >= 0.9999
        and timestamp_ratio >= 0.999999
        and positive_ratio >= 0.9999
        and clock.max_delta is not None
        and clock.max_delta <= expected_delta * 1.5
        and span_consistent
    )
    medium = (
        clock.sample_count >= 2
        and stable_ratio >= 0.98
        and timestamp_ratio >= 0.99
        and positive_ratio >= 0.99
        and clock.max_delta is not None
        and clock.max_delta <= expected_delta * 5
        and span_consistent
    )
    confidence = "high" if high else "medium" if medium else "low"
    if any(
        not rates_equivalent(candidate, rate)
        for candidate in (header.video.r_frame_rate, header.video.avg_frame_rate, cadence_rate)
    ):
        raise Av27MediaError(
            "E_AV27_SOURCE_FPS_AMBIGUOUS",
            "Source header 与全片 cadence 帧率不等价："
            f"header={canonical_fraction(rate)}，cadence={canonical_fraction(cadence_rate)}，"
            f"时钟={analysis_clock}；允许相对误差 2e-6",
        )
    if confidence == "low":
        raise Av27MediaError(
            "E_AV27_SOURCE_FPS_AMBIGUOUS",
            f"Source cadence 置信度不足：稳定间隔={stable_ratio:.4%} (要求>=98%)，"
            f"时间戳覆盖={timestamp_ratio:.4%} (要求>=99%)，"
            f"正向间隔={positive_ratio:.4%} (要求>=99%)，"
            f"最大间隔={clock.max_delta!r} 秒 (要求<=5帧周期)，"
            f"全片跨度一致={span_consistent}",
        )
    duration_tolerance = max(2 / float(rate), 0.05)
    if abs(duration - packet_count / float(rate)) > duration_tolerance:
        raise Av27MediaError(
            "E_AV27_SOURCE_FPS_AMBIGUOUS",
            "Source N/FPS/duration 无法闭合："
            f"N={packet_count}，FPS={canonical_fraction(rate)}，duration={duration}，"
            f"允许时长误差={duration_tolerance:.6f} 秒",
        )
    warnings: list[str] = []
    if confidence != "high":
        warnings.append(f"全片 cadence 置信度为 {confidence} (稳定比例 {stable_ratio:.2%})")
    if pts_count / packet_count < 0.99:
        warnings.append(f"仅 {pts_count / packet_count:.2%} 视频 packet 含 PTS")
    if timestamp_ratio < 1:
        warnings.append(f"所选 {analysis_clock} 时钟仅覆盖 {timestamp_ratio:.2%} 视频 packet")
    if positive_ratio < 1:
        warnings.append(f"仅 {positive_ratio:.2%} 相邻视频时间戳严格递增")
    if analysis_clock != "dts":
        warnings.append("DTS 覆盖不足，已使用 decoded best-effort presentation 时间线")
    return {
        "mode": "full-timeline",
        "analysis_clock": analysis_clock,
        "confidence": confidence,
        "effective_frame_rate": canonical_fraction(cadence_rate),
        "packet_count": packet_count,
        "clock_sample_count": clock.sample_count,
        "pts_ratio": pts_count / packet_count,
        "dts_ratio": dts_count / packet_count,
        "timestamp_ratio": timestamp_ratio,
        "cadence_ratio": stable_ratio,
        "positive_delta_ratio": positive_ratio,
        "max_delta": clock.max_delta,
        "timestamp_span_fps": span_fps,
        "warnings": warnings,
    }


def evaluate_timeline(
    header: Av27MediaHeader,
    packet_rows: Iterable[Mapping[str, object]],
    *,
    decoded_rows: Iterable[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """对合成行或已提供的行流应用同一算法；不会调用工具或读取媒体。

    decoded_rows 为惰性输入；DTS 已满足条件时完全不消费它。不要求原始 PTS 等于
    best-effort PTS，也不以 header nb_frames 代替完整 packet N。
    """
    with tempfile.TemporaryFile() as packet_deltas:
        packet = _Clock(packet_deltas)
        _consume(packet, packet_rows, decoded=False)
        if packet.total <= 0:
            raise Av27MediaError("E_AV27_SOURCE_FRAME_COUNT_UNKNOWN", "Source 不含视频 packet")
        if packet.sample_count / packet.total >= 0.99 and packet.sample_count >= 2:
            return _cadence(
                packet,
                packet_count=packet.total,
                pts_count=packet.pts_count,
                dts_count=packet.sample_count,
                header=header,
                analysis_clock="dts",
            )
        if decoded_rows is None:
            raise Av27MediaError("E_AV27_SOURCE_FPS_AMBIGUOUS", "DTS 覆盖不足且没有展示时间线")
        with tempfile.TemporaryFile() as decoded_deltas:
            decoded = _Clock(decoded_deltas)
            _consume(decoded, decoded_rows, decoded=True)
            return _cadence(
                decoded,
                packet_count=packet.total,
                pts_count=packet.pts_count,
                dts_count=packet.sample_count,
                header=header,
                analysis_clock="decoded_best_effort_timestamp",
            )


def scan_source_timeline(
    path: Path,
    *,
    header: Av27MediaHeader,
    ffprobe_executable: str | None = None,
    cancel_event: threading.Event | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[SourceTimeline, dict[str, object]]:
    """执行一次 packet 检查和必要 fallback，返回有界事实与真实读取进度。

    进度按已读取媒体时间映射到固定阶段区间，非耗时预测；预留 fallback 和统计区间。
    心跳保持已测量值以响应 Runtime 取消，不随等待时长虚构增长。
    """
    executable = ffprobe_executable or resolve_media_tool("ffprobe")
    duration = header.video.duration_seconds or header.duration_seconds or 0
    fraction = 0.0

    def report() -> None:
        check_cancel(cancel_event)
        if progress is not None:
            progress.report(fraction)

    def rows(*, decoded: bool) -> Iterable[Mapping[str, object]]:
        nonlocal fraction
        entries = "frame=best_effort_timestamp_time" if decoded else "packet=pts_time,dts_time"
        argv = [
            executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames" if decoded else "-show_packets",
            "-show_entries",
            entries,
            "-of",
            "compact=p=0:nk=0",
            "--",
            str(path),
        ]
        offset, extent = (0.75, 0.20) if decoded else (0.0, 0.75)
        with closing(probe_lines(argv, cancel_event=cancel_event, heartbeat=report)) as lines:
            for line in lines:
                if not line.strip():
                    continue
                row = _fields(line)
                timestamp = _timestamp(
                    row.get("best_effort_timestamp_time" if decoded else "dts_time")
                )
                if timestamp is None and not decoded:
                    timestamp = _timestamp(row.get("pts_time"))
                if timestamp is not None and duration > 0:
                    fraction = max(
                        fraction, offset + extent * min(1.0, max(0.0, timestamp / duration))
                    )
                yield row
        fraction = offset + extent
        report()

    with tempfile.TemporaryFile() as packet_deltas:
        packet = _Clock(packet_deltas)
        _consume(packet, rows(decoded=False), decoded=False)
        if packet.total <= 0:
            raise Av27MediaError("E_AV27_SOURCE_FRAME_COUNT_UNKNOWN", "Source 不含视频 packet")
        if packet.sample_count / packet.total >= 0.99 and packet.sample_count >= 2:
            cadence = _cadence(
                packet,
                packet_count=packet.total,
                pts_count=packet.pts_count,
                dts_count=packet.sample_count,
                header=header,
                analysis_clock="dts",
                cancel_event=cancel_event,
                heartbeat=report,
            )
        else:
            with tempfile.TemporaryFile() as decoded_deltas:
                decoded = _Clock(decoded_deltas)
                _consume(decoded, rows(decoded=True), decoded=True)
                cadence = _cadence(
                    decoded,
                    packet_count=packet.total,
                    pts_count=packet.pts_count,
                    dts_count=packet.sample_count,
                    header=header,
                    analysis_clock="decoded_best_effort_timestamp",
                    cancel_event=cancel_event,
                    heartbeat=report,
                )
    fraction = 0.99
    report()
    warnings = cadence["warnings"]
    assert isinstance(warnings, list)
    result = SourceTimeline(
        frame_count=packet.total,
        confidence=str(cadence["confidence"]),
        dts_coverage=packet.sample_count / packet.total,
        cadence_coverage=float(str(cadence["cadence_ratio"])),
        authority="packet_dts" if cadence["analysis_clock"] == "dts" else "decoded_presentation",
        warnings=tuple(str(item) for item in warnings),
    )
    return result, cadence
