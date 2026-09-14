"""有界读取 MKV/MP4/MOV 的真实轨级色彩元素，不使用 FFprobe 合并结果推断缺失。

只读取容器结构和有限头数据，跳过媒体载荷；重复、越界、未知尺寸结构或不支持的描述失败关闭。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from zniku.source_preparation.inspection import checked_file
from zniku.source_preparation.process import fail
from zniku.source_preparation.progress import check_cancellation

from .declarations import absent_field, cicp_field, known_field, unspecified_field
from .models import ColorField, SignalLayer

MAX_ELEMENTS = 200000
MAX_HEADER = 4 * 1024 * 1024


class Reader:
    """只在已验证的父区间内 seek/read，长度和结构数量都有限制。"""

    def __init__(self, stream: BinaryIO, size: int) -> None:
        self.stream, self.size, self.elements, self.bytes_read = stream, size, 0, 0

    def read(self, offset: int, size: int) -> bytes:
        check_cancellation()
        if offset < 0 or size < 0 or offset + size > self.size or size > MAX_HEADER:
            raise fail("COLOR_CONTAINER", "容器头字段越界或超预算")
        self.bytes_read += size
        if self.bytes_read > 32 * MAX_HEADER:
            raise fail("COLOR_CONTAINER", "容器结构累计读取超过预算")
        self.stream.seek(offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise fail("COLOR_CONTAINER", "容器结构未完整读取")
        return data

    def tick(self) -> None:
        self.elements += 1
        if self.elements > MAX_ELEMENTS:
            raise fail("COLOR_CONTAINER", "容器结构数量超预算")

    def vint(self, offset: int, *, identifier: bool) -> tuple[int, int, bool]:
        first = self.read(offset, 1)[0]
        mask, count = 128, 1
        while not first & mask:
            mask >>= 1
            count += 1
            if count > (4 if identifier else 8):
                raise fail("COLOR_CONTAINER", "非法 EBML 长度")
        raw = int.from_bytes(self.read(offset, count), "big")
        value = raw if identifier else raw & ((1 << (7 * count)) - 1)
        return value, count, not identifier and value == (1 << (7 * count)) - 1

    def ebml(
        self, start: int, end: int, *, segment: bool = False
    ) -> Iterator[tuple[int, int, int]]:
        position = start
        while position < end:
            self.tick()
            key, a, _ = self.vint(position, identifier=True)
            size, b, unknown = self.vint(position + a, identifier=False)
            content = position + a + b
            if unknown and not (segment and key == 0x18538067):
                raise fail("COLOR_CONTAINER", "未知尺寸 EBML 元素不能证明完整容器头")
            stop = end if unknown else content + size
            if stop > end or stop < content:
                raise fail("COLOR_CONTAINER", "EBML 元素越过父范围")
            yield key, content, stop
            position = stop

    def boxes(self, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
        position = start
        while position < end:
            self.tick()
            if end - position < 8:
                raise fail("COLOR_CONTAINER", "ISO BMFF box 不完整")
            head = self.read(position, 8)
            size, key, header = int.from_bytes(head[:4], "big"), head[4:], 8
            if size == 1:
                size, header = int.from_bytes(self.read(position + 8, 8), "big"), 16
            if size == 0:
                size = end - position
            if size < header or position + size > end:
                raise fail("COLOR_CONTAINER", "ISO BMFF box 越界")
            yield key, position + header, position + size
            position += size


def _unique(entries: Iterator[tuple[int, int, int]], keys: set[int]) -> dict[int, tuple[int, int]]:
    result: dict[int, tuple[int, int]] = {}
    for key, start, end in entries:
        if key in keys:
            if key in result:
                raise fail("COLOR_CONTAINER", "重复轨级色彩或关键容器元素")
            result[key] = (start, end)
    return result


def _uint(reader: Reader, entry: tuple[int, int]) -> int:
    start, end = entry
    if not 1 <= end - start <= 8:
        raise fail("COLOR_CONTAINER", "EBML 整数字段大小不合法")
    return int.from_bytes(reader.read(start, end - start), "big")


def _mkv(reader: Reader) -> SignalLayer:
    top = _unique(reader.ebml(0, reader.size, segment=True), {0x18538067})
    if 0x18538067 not in top:
        raise fail("COLOR_CONTAINER", "缺少 Matroska Segment")
    tracks = _unique(reader.ebml(*top[0x18538067]), {0x1654AE6B})
    if 0x1654AE6B not in tracks:
        raise fail("COLOR_CONTAINER", "缺少 Matroska Tracks")
    video: tuple[int, int] | None = None
    for key, start, end in reader.ebml(*tracks[0x1654AE6B]):
        if key != 0xAE:
            continue
        track = _unique(reader.ebml(start, end), {0x83, 0xE0})
        if 0x83 in track and _uint(reader, track[0x83]) == 1:
            if video is not None or 0xE0 not in track:
                raise fail("COLOR_CONTAINER", "视频轨缺失或不唯一")
            video = track[0xE0]
    if video is None:
        raise fail("COLOR_CONTAINER", "缺少实际视频轨")
    layout = _unique(reader.ebml(*video), {0x55B0, 0x2FB523, 0x2EB524, 0x7670})
    unsupported = [f"matroska:{key:x}" for key in layout if key != 0x55B0]
    raw_entries = [] if 0x55B0 not in layout else list(reader.ebml(*layout[0x55B0]))
    supported_keys = set(range(0x55B1, 0x55BE)) | {0x55D0}
    unsupported.extend(
        f"matroska:unknown-colour:{key:x}" for key, _, _ in raw_entries if key not in supported_keys
    )
    entries = _unique(iter(raw_entries), supported_keys)
    for key in (0x55BC, 0x55BD, 0x55D0):
        if key in entries:
            unsupported.append(f"matroska:hdr:{key:x}")
    for key in (0x55B2, 0x55B3, 0x55B4, 0x55B5, 0x55B6):
        if key in entries and _uint(reader, entries[key]) not in (
            {0, 8} if key == 0x55B2 else {0, 1} if key in {0x55B3, 0x55B4} else {0}
        ):
            unsupported.append(f"matroska:subsampling:{key:x}")

    def value(key: int) -> int | None:
        return _uint(reader, entries[key]) if key in entries else None

    range_value = value(0x55B9)
    range_field = (
        absent_field("color_range")
        if range_value is None
        else unspecified_field("color_range", 0)
        if range_value == 0
        else known_field(
            "color_range",
            range_value,
            {1: "tv", 2: "pc"}.get(range_value, f"matroska:{range_value}"),
        )
    )
    horizontal, vertical = value(0x55B7), value(0x55B8)
    if horizontal not in {None, 0, 1} or vertical not in {None, 0, 2}:
        unsupported.append("matroska:partial-chroma-not-left")
    if horizontal is None and vertical is None:
        chroma = absent_field("chroma_location")
    else:
        raw = f"{horizontal}:{vertical}"
        chroma = (
            unspecified_field("chroma_location", raw)
            if horizontal in {None, 0} or vertical in {None, 0}
            else known_field(
                "chroma_location",
                raw,
                "left" if (horizontal, vertical) == (1, 2) else f"matroska:{raw}",
            )
        )
    return SignalLayer(
        source_layer="matroska",
        scope="container-header",
        fields=(
            cicp_field("color_primaries", value(0x55BB)),
            cicp_field("color_transfer", value(0x55BA)),
            cicp_field("color_space", value(0x55B1)),
            range_field,
            chroma,
        ),
        unsupported=tuple(unsupported),
    )


def _child(reader: Reader, bounds: tuple[int, int], key: bytes) -> tuple[int, int] | None:
    matches = [(a, b) for k, a, b in reader.boxes(*bounds) if k == key]
    if len(matches) > 1:
        raise fail("COLOR_CONTAINER", "重复 ISO BMFF 关键 box")
    return matches[0] if matches else None


def _mp4(reader: Reader, sample_entry_types: frozenset[bytes]) -> SignalLayer:
    moov = _child(reader, (0, reader.size), b"moov")
    if moov is None:
        raise fail("COLOR_CONTAINER", "缺少 ISO BMFF moov")
    samples: tuple[int, int] | None = None
    for key, start, end in reader.boxes(*moov):
        if key != b"trak":
            continue
        mdia = _child(reader, (start, end), b"mdia")
        if mdia is None:
            raise fail("COLOR_CONTAINER", "轨道没有 mdia")
        handler = _child(reader, mdia, b"hdlr")
        if handler is None or handler[1] - handler[0] < 12:
            raise fail("COLOR_CONTAINER", "轨道没有有效 handler")
        if reader.read(handler[0] + 8, 4) != b"vide":
            continue
        if samples is not None:
            raise fail("COLOR_CONTAINER", "视频轨不唯一")
        nested = mdia
        for name in (b"minf", b"stbl", b"stsd"):
            found = _child(reader, nested, name)
            if found is None:
                raise fail("COLOR_CONTAINER", "视频 sample description 不完整")
            nested = found
        samples = nested
    if samples is None or samples[1] - samples[0] < 8:
        raise fail("COLOR_CONTAINER", "缺少视频 sample description")
    count = int.from_bytes(reader.read(samples[0] + 4, 4), "big")
    entries = list(reader.boxes(samples[0] + 8, samples[1]))
    if count != 1 or len(entries) != 1 or entries[0][0] not in sample_entry_types:
        raise fail("COLOR_CONTAINER", "必须是当前节点明确支持的唯一视频 sample entry")
    _, start, end = entries[0]
    if end - start < 78:
        raise fail("COLOR_CONTAINER", "视频 sample entry 不完整")
    fields: tuple[ColorField, ...] = (
        cicp_field("color_primaries", None),
        cicp_field("color_transfer", None),
        cicp_field("color_space", None),
        absent_field("color_range"),
        absent_field("chroma_location"),
    )
    unsupported: list[str] = []
    seen = False
    for key, a, b in reader.boxes(start + 78, end):
        codec_box = b"hvcC" if entries[0][0] in {b"hvc1", b"hev1"} else b"avcC"
        if key not in {b"colr", codec_box, b"btrt", b"pasp", b"free", b"fiel"}:
            unsupported.append(f"iso-bmff:unsupported-entry:{key.hex()}")
        if key == b"fiel" and reader.read(a, b - a) != b"\x01\x00":
            unsupported.append("iso-bmff:non-progressive-fiel")
        if key != b"colr":
            continue
        if seen:
            raise fail("COLOR_CONTAINER", "重复 colr 描述")
        seen = True
        data = reader.read(a, b - a)
        if len(data) < 4:
            raise fail("COLOR_CONTAINER", "colr 不完整")
        if data[:4] not in {b"nclc", b"nclx"}:
            unsupported.append("iso-bmff:icc-or-unknown-colr")
            continue
        expected = 11 if data[:4] == b"nclx" else 10
        if len(data) != expected or (expected == 11 and data[10] & 0x7F):
            raise fail("COLOR_CONTAINER", "colr 字段长度或保留位不合法")
        primary, transfer, matrix = (int.from_bytes(data[i : i + 2], "big") for i in (4, 6, 8))
        range_field = (
            absent_field("color_range")
            if expected == 10
            else known_field("color_range", data[10] >> 7, "pc" if data[10] & 128 else "tv")
        )
        fields = (
            cicp_field("color_primaries", primary),
            cicp_field("color_transfer", transfer),
            cicp_field("color_space", matrix),
            range_field,
            absent_field("chroma_location"),
        )
    return SignalLayer(
        source_layer="iso-bmff",
        scope="container-header",
        fields=fields,
        unsupported=tuple(unsupported),
    )


def observe_container_signal(
    path: Path, *, sample_entry_types: frozenset[bytes] | None = None
) -> SignalLayer:
    """只读实际容器声明；下游可明确列举编码，源入口默认仍只有 AVC。

    允许读取容器不等于支持该编码；调用者仍必须独立完整观察码流和解码帧。
    """
    path = checked_file(path)
    with path.open("rb") as stream:
        reader = Reader(stream, path.stat().st_size)
        return (
            _mkv(reader)
            if path.suffix.lower() == ".mkv"
            else _mp4(reader, sample_entry_types or frozenset({b"avc1", b"avc3"}))
        )
