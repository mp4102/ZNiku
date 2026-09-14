"""有界逐包读取 ProRes 帧头中的实际 CICP 字节，不以 MOV colr 或解码器合并值代替。

字段位置依据 FFmpeg libavcodec/proresdec.c 的 frame header 语法：帧标识后偏移
14/15/16 是三项色彩描述。本模块只读取每帧前 28 字节，不把大 ProRes 包读进内存。
不支持的版本、字段、结构、时间内变化均不被降为缺失。
"""

from __future__ import annotations

from pathlib import Path

from zniku.runtime.progress import ProgressReporter
from zniku.source_color.declarations import absent_field, cicp_field
from zniku.source_color.models import SIGNAL_NAMES, ColorField
from zniku.source_preparation.inspection import checked_file, probe_argv
from zniku.source_preparation.process import stream_process
from zniku.source_preparation.progress import sample, stage

from .node_contracts import CodecSignalObservation, fail


def observe_prores(
    path: Path, *, progress: ProgressReporter | None = None
) -> CodecSignalObservation:
    """只支持单一逐行 422 10-bit ProRes 帧头；全部包与完整解码帧数仍由调用方核对。"""
    path = checked_file(path)
    size = path.stat().st_size
    variants: list[tuple[ColorField, ...]] = []
    count = 0
    previous_end = 0
    with path.open("rb") as stream:

        def consume(raw: bytes) -> None:
            nonlocal count, previous_end
            row = dict(
                part.split("=", 1) for part in raw.decode("ascii").strip().split("|") if "=" in part
            )
            if not row:
                return
            if set(row) != {"size", "pos"}:
                fail("COLOR_PRORES", "包位置不可观察，不能验证实际帧头")
            start, length = int(row["pos"]), int(row["size"])
            if start < previous_end or length < 28 or start + length > size:
                fail("COLOR_PRORES", "ProRes 包边界越界、重复或不完整")
            stream.seek(start)
            header = stream.read(28)
            if (
                len(header) != 28
                or header[4:8] != b"icpf"
                or int.from_bytes(header[:4], "big") != length
            ):
                fail("COLOR_PRORES", "包并非完整单帧 ProRes")
            header_size = int.from_bytes(header[8:10], "big")
            if (
                header_size < 20
                or header_size + 8 > length
                or int.from_bytes(header[10:12], "big") > 1
            ):
                fail("COLOR_PRORES", "不支持或截断的 ProRes 帧头版本")
            if header[20] & 0xCC != 0x80 or header[25] & 0xF:
                fail("COLOR_PRORES", "只接受逐行 422 无 alpha 帧头")
            fields = (
                *tuple(
                    cicp_field(name, value)
                    for name, value in zip(
                        SIGNAL_NAMES[:3],
                        header[22:25],
                        strict=True,
                    )
                ),
                absent_field("color_range"),
                absent_field("chroma_location"),
            )
            if fields not in variants:
                if len(variants) >= 8:
                    fail("COLOR_PRORES", "ProRes 色彩变化超过预算")
                variants.append(fields)
            count += 1
            previous_end = start + length
            sample(count)

        with stage("color_prores_headers", "frames"):
            stream_process(
                [
                    *probe_argv(path),
                    "-select_streams",
                    "v:0",
                    "-show_packets",
                    "-show_entries",
                    "packet=pos,size",
                    "-of",
                    "compact=p=0:nk=0",
                    str(path),
                ],
                consume=consume,
                progress=progress,
            )
    if not variants:
        fail("COLOR_PRORES", "没有完整帧头记录")
    changes = tuple(
        field.field
        for index, field in enumerate(variants[0])
        if len({variant[index].model_dump_json() for variant in variants}) > 1
    )
    return CodecSignalObservation(
        codec="prores",
        scope="all-prores-frame-headers-eof",
        fields=variants[0],
        records=count,
        changes=changes,
    )
