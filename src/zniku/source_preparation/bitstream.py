"""读取初始 H.264 SPS 的明确固定 VUI 时钟，不从容器派生帧率猜测码流声明。

借助 FFmpeg 自身 trace_headers 解析首个包及容器 extradata；只记录初始 SPS 观察，
不宣称覆盖后续 SPS 切换，也不把 fixed_frame_rate_flag=0 当成固定时钟冲突。
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import Literal

from zniku.media.probe import resolve_media_tool
from zniku.runtime.progress import ProgressReporter

from .models import rational
from .process import MAX_CAPTURE, fail, stream_process

type ClockStatus = Literal["not_applicable", "not_fixed", "fixed", "unavailable", "ambiguous"]
_START = re.compile(r"^\[trace_headers @ [0-9a-fA-Fx]+\] Sequence Parameter Set$")
_FIELD = re.compile(
    r"^\[trace_headers @ [0-9a-fA-Fx]+\]\s+\d+\s+"
    r"(timing_info_present_flag|num_units_in_tick|time_scale|fixed_frame_rate_flag|"
    r"frame_mbs_only_flag)\s+[01]+\s+=\s+([0-9]+)$"
)


def initial_h264_clock(
    path: Path, *, progress: ProgressReporter | None = None
) -> tuple[ClockStatus, str | None]:
    """以首包、60 秒和有限日志预算读取初始 SPS；执行失败不冒充无固定时钟。"""
    observations: list[dict[str, int]] = []
    current: dict[str, int] | None = None
    received = 0

    def consume(raw: bytes) -> None:
        nonlocal current, received
        received += len(raw)
        if received > MAX_CAPTURE:
            raise fail("OUTPUT_BUDGET", "初始码流头日志超过检查预算")
        line = raw.decode("utf-8", errors="replace").strip()
        if _START.fullmatch(line):
            if current is not None:
                observations.append(current)
            if len(observations) >= 32:
                raise fail("OUTPUT_BUDGET", "初始 SPS 数量超过检查预算")
            current = {}
        elif current is not None and (match := _FIELD.fullmatch(line)):
            name, value = match.groups()
            current[name] = int(value)

    stream_process(
        [
            resolve_media_tool("ffmpeg"),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "info",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "mov,matroska",
            "-f",
            "matroska" if path.suffix.lower() == ".mkv" else "mov",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-bsf:v",
            "trace_headers",
            "-frames:v",
            "1",
            "-an",
            "-f",
            "null",
            "-",
        ],
        consume=consume,
        progress=progress,
        timeout=60,
        merge_stderr=True,
    )
    if current is not None:
        observations.append(current)
    if not observations:
        return "unavailable", None
    rates: set[Fraction] = set()
    not_fixed = False
    for item in observations:
        timing = item.get("timing_info_present_flag")
        if timing == 0 or (timing == 1 and item.get("fixed_frame_rate_flag") == 0):
            not_fixed = True
            continue
        ticks, scale = item.get("num_units_in_tick", 0), item.get("time_scale", 0)
        if (
            timing != 1
            or item.get("fixed_frame_rate_flag") != 1
            or item.get("frame_mbs_only_flag") != 1
            or ticks <= 0
            or scale <= 0
        ):
            return "unavailable", None
        rate = Fraction(scale, 2 * ticks)
        if not 1 <= rate <= 240:
            return "unavailable", None
        rates.add(rate)
    if len(rates) > 1 or (rates and not_fixed):
        return "ambiguous", None
    return ("fixed", rational(next(iter(rates)))) if rates else ("not_fixed", None)
