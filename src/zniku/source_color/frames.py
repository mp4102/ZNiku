"""完整逐帧观察实际色彩和相关 side data；适用于源、ProRes 与 HEVC，不填工作解释。"""

from __future__ import annotations

from pathlib import Path

from zniku.runtime.progress import ProgressReporter
from zniku.source_preparation.inspection import checked_file, probe_argv
from zniku.source_preparation.process import fail, stream_process
from zniku.source_preparation.progress import sample, stage

from .models import FrameSignal, FrameSignalSummary


def observe_frame_signals(
    path: Path, *, progress: ProgressReporter | None = None
) -> FrameSignalSummary:
    """以常量规模的去重集合覆盖全部展示帧；超过范围停止，不默默截断变化。"""
    path = checked_file(path)
    variants: list[FrameSignal] = []
    count = 0
    unsupported: set[str] = set()

    def consume(raw: bytes) -> None:
        nonlocal count
        pairs = [
            item.split("=", 1)
            for item in raw.decode("utf-8", errors="strict").strip().split("|")
            if "=" in item
        ]
        row = dict(pairs)
        for key, value in pairs:
            if key.endswith("side_data_type") and value not in {
                "H.26[45] User Data Unregistered SEI message",
                "H.264 User Data Unregistered SEI message",
                "H.265 User Data Unregistered SEI message",
            }:
                unsupported.add(f"frame-side-data:{value[:128]}")
        if len(unsupported) > 32:
            raise fail("COLOR_BUDGET", "逐帧 side data 类型超过预算")
        if "width" not in row:
            return
        signal = FrameSignal(
            color_primaries=row.get("color_primaries", "unknown"),
            color_transfer=row.get("color_transfer", "unknown"),
            color_space=row.get("color_space", "unknown"),
            color_range=row.get("color_range", "unknown"),
            chroma_location=row.get("chroma_location", "unknown"),
            width=int(row["width"]),
            height=int(row["height"]),
            pixel_format=row.get("pix_fmt", "unknown"),
            sample_aspect_ratio=row.get("sample_aspect_ratio", "unknown"),
            interlaced=row.get("interlaced_frame", "1") != "0",
        )
        if signal not in variants:
            if len(variants) >= 8:
                raise fail("COLOR_BUDGET", "全片色彩/几何变化超过有界表示范围")
            variants.append(signal)
        count += 1
        sample(count)

    with stage("color_frames", "frames"):
        errors = stream_process(
            [
                *probe_argv(path),
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=width,height,pix_fmt,sample_aspect_ratio,interlaced_frame,color_range,color_space,color_primaries,color_transfer,chroma_location:frame_side_data=side_data_type",
                "-of",
                "compact=p=0:nk=0",
                str(path),
            ],
            consume=consume,
            progress=progress,
            permit_media_errors=True,
        )
    if not variants or count < 1:
        raise fail("COLOR_FRAMES", "没有完整可观察的视频帧")
    if errors:
        unsupported.add("frame-decode-errors")
    changes = tuple(
        key
        for key in FrameSignal.model_fields
        if len({getattr(item, key) for item in variants}) > 1
    )
    return FrameSignalSummary(
        frame_count=count,
        variants=tuple(variants),
        changes=changes,
        unsupported=tuple(sorted(unsupported)),
    )
