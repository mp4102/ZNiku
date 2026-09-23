"""在单一顺序解码流中按帧选择责任区间并连续编码，不物化裁后 ProRes。

选择依据是已验证 raw 的解码帧序号，不依赖外部文件的近似 PTS。ffconcat 和 filter 文件
仅为当前 attempt 的受控执行输入，不是 Graph authority；失败保留部分文件且不登记成果。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from zniku.avenhance_v27.adapters import _encoder_options
from zniku.chapter_overlap.media_io import (
    OverlapMediaError,
    output_path,
    run_ffmpeg,
    write_ffconcat,
)
from zniku.runtime import PythonAdapterContext


@dataclass(frozen=True, slots=True)
class RawRange:
    """一个直接 raw 输入的实际帧数与本地零基半开保留区间。"""

    path: Path
    frame_count: int
    start: int
    end: int

    def __post_init__(self) -> None:
        values = (self.frame_count, self.start, self.end)
        if any(type(value) is not int for value in values) or not (
            0 <= self.start < self.end <= self.frame_count
        ):
            raise OverlapMediaError("E_FUSED_RANGE", "raw 区间必须为严格整数且非空、不越界")


def selection_expression(parts: Sequence[RawRange]) -> str:
    """构造深度至多十层的区间选择，避免每帧线性判断一千章与超长 argv。"""
    if not 1 <= len(parts) <= 1000:
        raise OverlapMediaError("E_FUSED_RANGE", "章数必须为 1..1000")
    ranges: list[tuple[int, int, int]] = []
    cursor = 0
    for part in parts:
        ranges.append((cursor + part.start, cursor + part.end, cursor + part.frame_count))
        cursor += part.frame_count

    def tree(first: int, end: int) -> str:
        if end - first == 1:
            start, stop, _ = ranges[first]
            return f"between(n,{start},{stop - 1})"
        mid = (first + end) // 2
        return f"if(lt(n,{ranges[mid - 1][2]}),{tree(first, mid)},{tree(mid, end)})"

    return tree(0, len(ranges))


def selected_filter(parts: Sequence[RawRange], rate: Fraction, *, tail: bool = True) -> str:
    """先选真实帧再重建连续 exact 时钟；只有最终全片允许一次尾帧复制。"""
    if rate <= 0:
        raise OverlapMediaError("E_FUSED_RATE", "输出帧率必须为正")
    clone = "tpad=stop_mode=clone:stop=1," if tail else ""
    return (
        f"select='{selection_expression(parts)}',setsar=1/1,{clone}"
        f"settb=expr=1/{rate.numerator},setpts=N*{rate.denominator}"
    )


def encode_fused(
    context: PythonAdapterContext,
    parts: Sequence[RawRange],
    target: Path,
    rate: Fraction,
    expected_frames: int,
    encoder: str,
) -> int:
    """保持旧成片配方，一次顺序解码/编码，不输出 crop 或 timescale 媒体。"""
    if encoder not in {"cpu", "gpu"} or (
        sum(part.end - part.start for part in parts) + 1 != expected_frames
    ):
        raise OverlapMediaError("E_FUSED_PROGRAM", "编码器或全片帧数不符合责任区间")
    target = output_path(context, target)
    manifest = write_ffconcat(
        context, tuple(part.path for part in parts), target.with_suffix(".ffconcat")
    )
    script = output_path(context, target.with_suffix(".filter"))
    pixel_format = "yuv420p10le" if encoder == "cpu" else "p010le"
    script.write_text(
        selected_filter(parts, rate)
        + f",format={pixel_format},"
        + "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        encoding="utf-8",
    )
    return run_ffmpeg(
        context,
        [
            "-copyts",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-filter_script:v",
            str(script),
            "-c:v",
            "libx265" if encoder == "cpu" else "hevc_nvenc",
            *_encoder_options(encoder, rate),
            "-fps_mode:v",
            "passthrough",
            "-enc_time_base:v",
            f"1/{rate.numerator}",
            "-color_range:v",
            "tv",
            "-colorspace:v",
            "bt709",
            "-color_trc:v",
            "bt709",
            "-color_primaries:v",
            "bt709",
            "-chroma_sample_location:v",
            "left",
            "-tag:v",
            "hvc1",
            "-video_track_timescale",
            str(rate.numerator),
            "-f",
            "mp4",
            str(target),
        ],
        expected_frames=expected_frames,
        stage="精确选帧并连续编码",
    )
