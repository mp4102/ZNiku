"""只为 ProRes 边界读取确认窄范围 MOV 索引，不实现通用容器解析器。

只接受单视频轨、常量 stts、零偏移和已知样本数的普通 MOV。未知结构、fragmented
或预算不足返回回退原因；不扫描媒体 payload，不以此替代既有媒体业务 validator。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO

_FAST_RATES = frozenset(
    Fraction(value)
    for value in ("24", "30", "24000/1001", "30000/1001", "48", "60", "48000/1001", "60000/1001")
)


@dataclass(frozen=True, slots=True)
class _Box:
    kind: bytes
    payload: int
    end: int


class _Unsupported(ValueError):
    pass


class _IndexReader:
    """最多读取 256 KiB 索引头和 4096 个 atom；大表及 mdat 只定位跳过。"""

    def __init__(self, stream: BinaryIO, size: int) -> None:
        self.stream = stream
        self.size = size
        self.read_bytes = 0
        self.boxes = 0

    def read(self, offset: int, count: int) -> bytes:
        if offset < 0 or count < 0 or offset + count > self.size:
            raise _Unsupported("invalid-atom-range")
        self.read_bytes += count
        if self.read_bytes > 256 * 1024:
            raise _Unsupported("index-budget")
        self.stream.seek(offset)
        value = self.stream.read(count)
        if len(value) != count:
            raise _Unsupported("incomplete-index")
        return value

    def children(self, start: int, end: int) -> tuple[_Box, ...]:
        result: list[_Box] = []
        cursor = start
        while cursor < end:
            self.boxes += 1
            if self.boxes > 4096 or end - cursor < 8:
                raise _Unsupported("atom-budget-or-truncated")
            header = self.read(cursor, 8)
            size, kind = int.from_bytes(header[:4], "big"), header[4:]
            header_size = 8
            if size == 1:
                size = int.from_bytes(self.read(cursor + 8, 8), "big")
                header_size = 16
            elif size == 0:
                size = end - cursor
            if size < header_size or cursor + size > end:
                raise _Unsupported("invalid-atom-size")
            result.append(_Box(kind, cursor + header_size, cursor + size))
            cursor += size
        return tuple(result)

    def inside(self, box: _Box) -> tuple[_Box, ...]:
        return self.children(box.payload, box.end)

    @staticmethod
    def one(boxes: tuple[_Box, ...], kind: bytes) -> _Box:
        matches = [box for box in boxes if box.kind == kind]
        if len(matches) != 1:
            raise _Unsupported("missing-or-ambiguous-" + kind.decode("ascii"))
        return matches[0]

    def prefix(self, box: _Box, count: int) -> bytes:
        if box.end - box.payload < count:
            raise _Unsupported("truncated-table")
        return self.read(box.payload, count)

    def inspect(self, rate: Fraction, count: int) -> None:
        top = self.children(0, self.size)
        if any(box.kind == b"moof" for box in top):
            raise _Unsupported("fragmented-mov")
        moov = self.inside(self.one(top, b"moov"))
        self.one(top, b"mdat")
        if any(box.kind == b"mvex" for box in moov):
            raise _Unsupported("fragmented-mov")
        trak = self.inside(self.one(moov, b"trak"))
        edits = [box for box in trak if box.kind == b"edts"]
        if len(edits) > 1:
            raise _Unsupported("ambiguous-edits")
        if edits:
            elst = self.one(self.inside(edits[0]), b"elst")
            prefix = self.prefix(elst, 8)
            if prefix[:4] != b"\0\0\0\0" or int.from_bytes(prefix[4:], "big") != 1:
                raise _Unsupported("unsupported-edit-list")
            edit = self.prefix(elst, 20)
            if (
                elst.end - elst.payload != 20
                or edit[12:16] != b"\0\0\0\0"
                or edit[16:20] != b"\0\1\0\0"
            ):
                raise _Unsupported("nonzero-or-rate-edit")
        mdia = self.inside(self.one(trak, b"mdia"))
        if self.prefix(self.one(mdia, b"hdlr"), 12)[8:12] != b"vide":
            raise _Unsupported("not-single-video-track")
        mdhd = self.prefix(self.one(mdia, b"mdhd"), 20)
        if mdhd[:4] != b"\0\0\0\0":
            raise _Unsupported("unsupported-media-header")
        if (
            int.from_bytes(mdhd[12:16], "big") != rate.numerator
            or int.from_bytes(mdhd[16:20], "big") != count * rate.denominator
        ):
            raise _Unsupported("noncanonical-clock")
        minf = self.inside(self.one(mdia, b"minf"))
        stbl = self.inside(self.one(minf, b"stbl"))
        if any(box.kind in (b"ctts", b"stss") for box in stbl):
            raise _Unsupported("composition-or-keyframe-table")
        stts_box = self.one(stbl, b"stts")
        stts = self.prefix(stts_box, 16)
        if (
            stts_box.end - stts_box.payload != 16
            or stts[:8] != b"\0\0\0\0\0\0\0\1"
            or int.from_bytes(stts[8:12], "big") != count
            or int.from_bytes(stts[12:16], "big") != rate.denominator
        ):
            raise _Unsupported("nonconstant-sample-clock")
        stsz_box = self.one(stbl, b"stsz")
        stsz = self.prefix(stsz_box, 12)
        expected_size = 12 + (4 * count if int.from_bytes(stsz[4:8], "big") == 0 else 0)
        if (
            stsz[:4] != b"\0\0\0\0"
            or int.from_bytes(stsz[8:12], "big") != count
            or stsz_box.end - stsz_box.payload != expected_size
        ):
            raise _Unsupported("sample-count-mismatch")
        self.one(stbl, b"stsc")
        if sum(box.kind in (b"stco", b"co64") for box in stbl) != 1:
            raise _Unsupported("missing-or-ambiguous-chunk-index")


def indexed_mov_fallback_reason(path: Path, rate: Fraction, count: int) -> str | None:
    """返回窄快路径不适用原因；None 仅表示可按已证明的 CFR 索引 seek。

    解析失败不使原本允许的媒体失败，也不能悄悄放宽帧率/包数合同。真正读写错误仍由
    后续原安全媒体路径处理；这里不把不确定索引当作可用索引。
    """

    if rate <= 0 or type(count) is not int or count < 1:
        return "invalid-rate-or-count"
    if rate not in _FAST_RATES:
        return "unverified-rate"
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            reader = _IndexReader(stream, stream.tell())
            reader.inspect(rate, count)
    except (OSError, _Unsupported) as error:
        return str(error) if isinstance(error, _Unsupported) else "index-unavailable"
    return None
