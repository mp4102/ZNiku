"""普通包复制的音轨完整度检查，不要求原件音视频等长，不宣称逐样本恒等。

只遍历压缩包的数量、大小和时间覆盖；不解码原音轨或输出 PCM，不创建摘要权威。
起点另由现有轻量同步检查核对，任何尾部截断或额外包都会拒绝发布。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from zniku.avenhance_v27.probe import probe_header
from zniku.runtime.progress import ProgressReporter
from zniku.source_preparation.inspection import _scan
from zniku.source_preparation.progress import sample, stage

from .node_contracts import fail


@dataclass(frozen=True)
class AudioPacketSpan:
    count: int
    size: int
    start: Fraction
    end: Fraction


def packet_span(
    path: Path, index: int, progress: ProgressReporter | None = None
) -> AudioPacketSpan:
    count = size = 0
    first: Fraction | None = None
    last: Fraction | None = None
    last_duration: Fraction | None = None
    end: Fraction | None = None

    def consume(row: dict[str, str]) -> None:
        nonlocal count, size, first, last, last_duration, end
        if not row:
            return
        try:
            pts = Fraction(row["pts_time"])
            packet_size = int(row["size"])
        except (KeyError, ValueError, ZeroDivisionError) as error:
            raise ValueError("音频包缺少可核对的时间/长度") from error
        raw_duration = row.get("duration_time")
        duration = None if raw_duration is None or raw_duration == "N/A" else Fraction(raw_duration)
        if (duration is not None and duration <= 0) or packet_size <= 0:
            fail("WORK_AUDIO_PACKET", "音频包时间或大小无效")
        first = pts if first is None else min(first, pts)
        # Matroska 首包可以不报告 duration；数量/载荷及末包覆盖仍可独立核对。
        # 不把未声明 duration 写成实测，也不因不影响末端的可选缺失拒绝整条音轨。
        if last is None or pts >= last:
            last, last_duration = pts, duration
        if duration is not None:
            end = pts + duration if end is None else max(end, pts + duration)
        count += 1
        size += packet_size
        sample(count)

    with stage("work_audio_packets", "packets"):
        errors = _scan(
            path, str(index), "packet=pts_time,duration_time,size", False, consume, progress
        )
    if errors or first is None or end is None or last_duration is None:
        fail("WORK_AUDIO_PACKET", "音轨包检查未完整成功")
    return AudioPacketSpan(count, size, first, end)


def verify_audio_copy(
    source: Path, output: Path, *, progress: ProgressReporter | None = None
) -> None:
    """核对所选包复制轨道未截断；不把相同数量/大小包装成字节或样本相同。"""
    originals, results = probe_header(source).audios, probe_header(output).audios
    if len(originals) != len(results):
        fail("WORK_AUDIO_COUNT", "输出音轨数量变化")
    for original, result in zip(originals, results, strict=True):
        before = packet_span(source, original.index, progress)
        after = packet_span(output, result.index, progress)
        if (
            before.count != after.count
            or before.size != after.size
            or abs((before.end - before.start) - (after.end - after.start)) > Fraction(2, 1000)
        ):
            fail("WORK_AUDIO_COPY", "输出音轨包数、载荷总量或时间覆盖变化；不自动裁尾")
