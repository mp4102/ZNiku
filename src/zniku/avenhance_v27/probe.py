"""读取 AVEnhanceFlow v2.7 节点所需的严格媒体 header 与 Source 时间线。

本模块只建立节点局部的轻量媒体事实。SourceProgram 会做一次流式 packet timeline
traversal；只有 DTS 覆盖不足时才退回 presentation-frame traversal。这里不读取或写入
AVEnhanceFlow task/state，不计算媒体 payload 摘要，也不产生 Evidence 或 receipt。

所有 FFprobe 调用都使用结构化 argv、``shell=False`` 和有限错误输出；未知 stream、非法
rational、显式颜色冲突或无法证明的帧数默认失败关闭。
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from zniku.media.probe import MediaNodeError, resolve_media_tool
from zniku.runtime.process_window import background_creation_flags

AV27_NAMESPACE: Final = "zniku.avenhance.v27"
_ALLOWED_STREAM_TYPES: Final = frozenset({"video", "audio"})
_MISSING: Final = frozenset({"", "n/a", "unknown", "unspecified"})
_RATE_TOLERANCE: Final = Fraction(1, 500_000)
_KNOWN_FRAME_RATES: Final = (
    Fraction(24000, 1001),
    Fraction(24, 1),
    Fraction(25, 1),
    Fraction(30000, 1001),
    Fraction(30, 1),
    Fraction(50, 1),
    Fraction(60000, 1001),
    Fraction(60, 1),
    Fraction(120000, 1001),
    Fraction(120, 1),
)
_HDR_SIDE_DATA_TOKENS: Final = (
    "mastering display",
    "content light",
    "dovi",
    "dolby vision",
    "hdr10+",
    "smpte2094",
    "dynamic hdr",
)


class Av27MediaError(MediaNodeError):
    """表示 v2.7 节点合同失败；``code`` 是稳定、可测试的 AV27 错误码。"""


@dataclass(frozen=True, slots=True)
class Av27VideoHeader:
    """保存一条视频流的可验证 header 事实。"""

    index: int
    codec: str
    profile: str | None
    codec_tag_string: str | None
    width: int
    height: int
    pixel_format: str | None
    frame_rate: Fraction
    avg_frame_rate: Fraction
    r_frame_rate: Fraction
    time_base: Fraction
    frame_count: int | None
    sample_aspect_ratio: str | None
    field_order: str | None
    rotation: int
    color_range: str | None
    color_space: str | None
    color_transfer: str | None
    color_primaries: str | None
    chroma_location: str | None
    hdr_side_data: tuple[str, ...]
    duration_seconds: float | None

    def to_summary(self) -> dict[str, object]:
        """返回严格 JSON-compatible 的 compact summary。"""

        return {
            "index": self.index,
            "codec": self.codec,
            "profile": self.profile,
            "codec_tag_string": self.codec_tag_string,
            "width": self.width,
            "height": self.height,
            "pixel_format": self.pixel_format,
            "frame_rate": canonical_fraction(self.frame_rate),
            "avg_frame_rate": canonical_fraction(self.avg_frame_rate),
            "r_frame_rate": canonical_fraction(self.r_frame_rate),
            "time_base": canonical_fraction(self.time_base),
            "sample_aspect_ratio": self.sample_aspect_ratio,
            "field_order": self.field_order,
            "rotation": self.rotation,
            "color_range": self.color_range,
            "color_space": self.color_space,
            "color_transfer": self.color_transfer,
            "color_primaries": self.color_primaries,
            "chroma_location": self.chroma_location,
            "hdr_side_data": list(self.hdr_side_data),
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True, slots=True)
class Av27AudioHeader:
    """保存用于 Admission/FinalMux 闭合的一条逻辑音轨 header signature。"""

    index: int
    codec: str
    profile: str | None
    extradata_hash: str | None
    sample_rate: int | None
    channels: int | None
    channel_layout: str | None
    language: str | None
    title: str | None
    default: bool
    forced: bool

    def signature(self) -> dict[str, object]:
        """返回不含 stream index 的 logical ordinal signature。"""

        return {
            "codec": self.codec,
            "profile": self.profile,
            "extradata_hash": self.extradata_hash,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "channel_layout": self.channel_layout,
            "language": self.language,
            "title": self.title,
            "default": self.default,
            "forced": self.forced,
        }

    def to_summary(self) -> dict[str, object]:
        """返回包含原 stream index 的 compact summary。"""

        return {"index": self.index, **self.signature()}


@dataclass(frozen=True, slots=True)
class Av27OtherStreamHeader:
    """保存 subtitle/data/attachment 等非音视频 stream 的识别字段。"""

    index: int
    kind: str
    codec: str
    codec_tag_string: str | None


@dataclass(frozen=True, slots=True)
class Av27MediaHeader:
    """保存一个媒体容器的严格 header 视图。"""

    path: Path
    format_name: str
    duration_seconds: float | None
    streams: tuple[str, ...]
    videos: tuple[Av27VideoHeader, ...]
    audios: tuple[Av27AudioHeader, ...]
    others: tuple[Av27OtherStreamHeader, ...]
    chapter_count: int

    @property
    def video(self) -> Av27VideoHeader:
        """返回唯一视频；多视频或缺视频由调用节点明确拒绝。"""

        if len(self.videos) != 1:
            raise Av27MediaError(
                "E_AV27_VIDEO_STREAM_COUNT",
                f"需要恰好一条视频流，实际为 {len(self.videos)}",
            )
        return self.videos[0]


@dataclass(frozen=True, slots=True)
class SourceTimeline:
    """SourceProgram 一次时间线验收得到的 exact N 与置信度。"""

    frame_count: int
    confidence: str
    dts_coverage: float
    cadence_coverage: float
    authority: str
    warnings: tuple[str, ...] = ()


def canonical_fraction(value: Fraction) -> str:
    """输出已约分的正 rational，避免 float 进入媒体时间权威。"""

    if value <= 0:
        raise Av27MediaError("E_AV27_RATE_INVALID", "rational 必须为正数")
    return f"{value.numerator}/{value.denominator}"


def parse_fraction(value: object, *, code: str = "E_AV27_RATE_INVALID") -> Fraction:
    """严格解析 canonical 正 rational；整数文本也规范为 ``n/1``。"""

    if not isinstance(value, str) or not value or value.strip() != value:
        raise Av27MediaError(code, "rational 必须是无首尾空白的 string")
    try:
        parsed = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise Av27MediaError(code, "rational 无法解析") from error
    if parsed <= 0 or value not in {str(parsed), canonical_fraction(parsed)}:
        raise Av27MediaError(code, "rational 必须为已约分的正数文本")
    return parsed


def rates_equivalent(
    left: Fraction,
    right: Fraction,
    *,
    tolerance: Fraction = _RATE_TOLERANCE,
) -> bool:
    """按冻结的相对容差比较容器 rational 表示。"""

    if left <= 0 or right <= 0:
        return left == right
    return abs(left - right) / max(left, right) <= tolerance


def snap_frame_rate(value: Fraction) -> Fraction:
    """把 0.05% 内的 header 近似值吸附到批准的常见 exact rate。"""

    candidate = min(
        _KNOWN_FRAME_RATES,
        key=lambda item: abs(float(item - value)) / float(item),
    )
    if abs(candidate - value) / candidate <= Fraction(1, 2000):
        return candidate
    return value


def canonical_source_rate(video: Av27VideoHeader) -> Fraction:
    """同时验证 avg/r rate 后返回 Source canonical snapped FPS。"""

    if not rates_equivalent(video.avg_frame_rate, video.r_frame_rate):
        raise Av27MediaError(
            "E_AV27_SOURCE_FPS_AMBIGUOUS",
            "avg_frame_rate/r_frame_rate 相互矛盾",
        )
    return snap_frame_rate(video.avg_frame_rate)


def probe_header(
    path: str | Path,
    *,
    count_frames: bool = False,
    ffprobe_executable: str | None = None,
) -> Av27MediaHeader:
    """读取容器、全部 stream、chapter 与可选 header/count frame 事实。"""

    source = _nonempty_file(path)
    executable = ffprobe_executable or resolve_media_tool("ffprobe")
    entries = (
        "format=format_name,duration:chapter=id:"
        "stream=index,codec_type,codec_name,profile,codec_tag_string,width,height,pix_fmt,"
        "avg_frame_rate,r_frame_rate,time_base,nb_frames,nb_read_frames,duration,"
        "sample_aspect_ratio,"
        "field_order,color_range,color_space,color_transfer,color_primaries,chroma_location,"
        "sample_rate,channels,channel_layout,extradata_hash:"
        "stream_tags=language,title,rotate:stream_disposition=default,forced:"
        "stream_side_data=side_data_type,rotation"
    )
    argv = [executable, "-v", "error"]
    if count_frames:
        argv.append("-count_frames")
    argv.extend(
        [
            "-show_data_hash",
            "sha256",
            "-show_entries",
            entries,
            "-of",
            "json",
            "--",
            str(source),
        ]
    )
    payload = _run_json_probe(argv, timeout=3600 if count_frames else 60)
    return _parse_header(source, payload, count_frames=count_frames)


def probe_source_timeline(
    path: str | Path,
    *,
    header: Av27MediaHeader | None = None,
    ffprobe_executable: str | None = None,
) -> SourceTimeline:
    """以一次 packet traversal 建立 N/cadence，必要时回退 presentation traversal。

    packet 分支只流式保留计数和相邻时间差，不把 packet ledger 放入内存或 Artifact。DTS
    coverage 低于 99% 时只把 cadence clock 切换到 decoded presentation traversal；Source 的 N
    始终来自完整 packet traversal，decoded frame total 不成为第二个帧数 authority。
    """

    source = _nonempty_file(path)
    inspected = header or probe_header(source, ffprobe_executable=ffprobe_executable)
    video = inspected.video
    source_rate = canonical_source_rate(video)
    executable = ffprobe_executable or resolve_media_tool("ffprobe")
    packet = _scan_timeline(
        [
            executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_packets",
            "-show_entries",
            "packet=pts_time,dts_time",
            "-of",
            "compact=p=0:nk=0",
            "--",
            str(source),
        ],
        expected_period=Fraction(1, 1) / source_rate,
        primary_field="dts_time",
        secondary_field="pts_time",
        time_base=video.time_base,
    )
    if packet.total <= 0:
        raise Av27MediaError("E_AV27_SOURCE_FRAME_COUNT_UNKNOWN", "Source 不含可计数视频 packet")
    if video.frame_count is not None and video.frame_count != packet.total:
        raise Av27MediaError(
            "E_AV27_SOURCE_FPS_AMBIGUOUS",
            "Source header frame count 与完整 packet N 矛盾",
        )
    selected = packet
    authority = "packet_dts"
    if packet.primary_coverage < 0.99:
        selected = _scan_timeline(
            [
                executable,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "compact=p=0:nk=0",
                "--",
                str(source),
            ],
            expected_period=Fraction(1, 1) / source_rate,
            primary_field="best_effort_timestamp_time",
            secondary_field=None,
            time_base=video.time_base,
        )
        authority = "decoded_presentation"
    confidence = _timeline_confidence(
        selected,
        packet_count=packet.total,
        rate=source_rate,
        duration=video.duration_seconds or inspected.duration_seconds,
    )
    warnings: list[str] = []
    if confidence != "high":
        warnings.append(
            f"Source cadence 仅达到 {confidence}-confidence ({selected.cadence_coverage:.2%})"
        )
    if authority == "decoded_presentation":
        warnings.append("DTS coverage 低于 99%，已使用 decoded presentation timeline")
    if packet.secondary_coverage < 0.99:
        warnings.append(f"仅 {packet.secondary_coverage:.2%} 视频 packet 含 PTS")
    return SourceTimeline(
        packet.total,
        confidence,
        packet.primary_coverage,
        selected.cadence_coverage,
        authority,
        tuple(warnings),
    )


def resolve_bt709_signal(
    video: Av27VideoHeader,
    *,
    role: str,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """解析 BT.709 SDR limited；缺失标签 warning，显式冲突立即失败。"""

    expected = {
        "color_range": "tv",
        "color_space": "bt709",
        "color_transfer": "bt709",
        "color_primaries": "bt709",
        "chroma_location": "left",
        "field_order": "progressive",
        "sample_aspect_ratio": "1:1",
    }
    aliases = {
        "limited": "tv",
        "mpeg": "tv",
        "1/1": "1:1",
    }
    actual = {
        "color_range": video.color_range,
        "color_space": video.color_space,
        "color_transfer": video.color_transfer,
        "color_primaries": video.color_primaries,
        "chroma_location": video.chroma_location,
        "field_order": video.field_order,
        "sample_aspect_ratio": video.sample_aspect_ratio,
    }
    warnings: list[str] = []
    resolved: dict[str, object] = {}
    for field, wanted in expected.items():
        raw = actual[field]
        normalized = None if raw is None else aliases.get(raw.casefold(), raw.casefold())
        if normalized is None or normalized in _MISSING:
            resolved[field] = wanted
            warnings.append(f"{role} {field} 缺失，按 BT.709 limited 解析")
        elif normalized != wanted:
            raise Av27MediaError(
                "E_AV27_SIGNAL_CONFLICT",
                f"{role} {field}={raw!r} 与 {wanted!r} 冲突",
            )
        else:
            resolved[field] = wanted
    if video.rotation != 0:
        raise Av27MediaError("E_AV27_ROTATION", f"{role} rotation 必须为 0")
    if video.hdr_side_data:
        raise Av27MediaError(
            "E_AV27_SIGNAL_CONFLICT",
            f"{role} 含 HDR side data：{video.hdr_side_data}",
        )
    resolved["rotation"] = 0
    return resolved, tuple(warnings)


def require_square_sar(video: Av27VideoHeader, *, role: str) -> None:
    """只允许缺失或 1:1 SAR。"""

    if video.sample_aspect_ratio not in {None, "1:1", "1/1"}:
        raise Av27MediaError("E_AV27_SAR_INVALID", f"{role} 必须为 square SAR")


def require_progressive_zero_rotation(video: Av27VideoHeader, *, role: str) -> None:
    """拒绝隔行与旋转输出；missing field order 不冒充显式隔行。"""

    if video.field_order is not None and video.field_order.casefold() not in {
        "progressive",
        "unknown",
    }:
        raise Av27MediaError("E_AV27_FIELD_ORDER", f"{role} 必须为 progressive")
    if video.rotation != 0:
        raise Av27MediaError("E_AV27_ROTATION", f"{role} rotation 必须为 0")


def require_only_av_streams(media: Av27MediaHeader, *, role: str) -> None:
    """拒绝 subtitle/attachment/data/unknown 等未声明 stream。"""

    unexpected = tuple(kind for kind in media.streams if kind not in _ALLOWED_STREAM_TYPES)
    unknown_codecs = tuple(
        [item.index for item in media.videos if item.codec.casefold() == "unknown"]
        + [item.index for item in media.audios if item.codec.casefold() == "unknown"]
    )
    if unexpected or unknown_codecs:
        raise Av27MediaError(
            "E_AV27_STREAM_UNSUPPORTED",
            f"{role} 含未允许 stream={unexpected} 或 unknown codec indexes={unknown_codecs}",
        )


def source_namespace_summary(
    media: Av27MediaHeader,
    timeline: SourceTimeline,
    *,
    source_ordinal: int,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """生成 SourceProgram 写入两个 output Artifact 的统一 namespace summary。"""

    video = media.video
    require_only_av_streams(media, role="SourceProgram")
    if media.chapter_count:
        raise Av27MediaError("E_AV27_SOURCE_CHAPTERS", "SourceProgram 不接受内置 chapters")
    if video.width * 9 != video.height * 16:
        raise Av27MediaError("E_AV27_SOURCE_GEOMETRY", "Source coded geometry 必须精确为 16:9")
    require_square_sar(video, role="SourceProgram")
    signal, signal_warnings = resolve_bt709_signal(video, role="SourceProgram")
    source_rate = canonical_source_rate(video)
    summary: dict[str, object] = {
        "frame_count": timeline.frame_count,
        "frame_rate": canonical_fraction(source_rate),
        "geometry": {"width": video.width, "height": video.height},
        "sample_aspect_ratio": video.sample_aspect_ratio,
        "field_order": video.field_order,
        "rotation": video.rotation,
        "signal": signal,
        "chroma_location": video.chroma_location,
        "duration_seconds": media.duration_seconds or video.duration_seconds,
        "container": {"format_name": media.format_name, "chapter_count": media.chapter_count},
        "video": video.to_summary(),
        "audio_tracks": [audio.to_summary() for audio in media.audios],
        "source_ordinal": source_ordinal,
        "timeline": {
            "authority": timeline.authority,
            "confidence": timeline.confidence,
            "dts_coverage": timeline.dts_coverage,
            "cadence_coverage": timeline.cadence_coverage,
        },
    }
    return summary, (*timeline.warnings, *signal_warnings)


def namespace_from_media_info(media_info: Mapping[str, object]) -> Mapping[str, object]:
    """从 RunnerInput 的唯一 authority path 读取 v2.7 namespace。"""

    value = media_info.get(AV27_NAMESPACE)
    if not isinstance(value, Mapping):
        raise Av27MediaError("E_AV27_METADATA_MISSING", f"缺少 {AV27_NAMESPACE} metadata")
    return value


def metadata_frame_count(media_info: Mapping[str, object]) -> int:
    """读取经上游 validator 闭合的严格正 ``frame_count``。"""

    value = namespace_from_media_info(media_info).get("frame_count")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Av27MediaError("E_AV27_FRAME_COUNT_UNKNOWN", "namespaced frame_count 无效")
    return value


def metadata_rate(media_info: Mapping[str, object]) -> Fraction:
    """读取 namespaced canonical source/output FPS。"""

    return parse_fraction(namespace_from_media_info(media_info).get("frame_rate"))


def audio_signatures_from_metadata(
    media_info: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    """按 logical ordinal 读取 SourceProgram 保存的音轨 signatures。"""

    raw = namespace_from_media_info(media_info).get("audio_tracks")
    if not isinstance(raw, list | tuple):
        raise Av27MediaError("E_AV27_AUDIO_METADATA", "audio_tracks 必须为 array")
    result: list[dict[str, object]] = []
    field_names = (
        "codec",
        "profile",
        "extradata_hash",
        "sample_rate",
        "channels",
        "channel_layout",
        "language",
        "title",
        "default",
        "forced",
    )
    fields = set(field_names)
    for item in raw:
        if not isinstance(item, Mapping):
            raise Av27MediaError("E_AV27_AUDIO_METADATA", "audio track 必须为 object")
        keys = set(item)
        if keys != fields and keys != fields | {"index"}:
            raise Av27MediaError("E_AV27_AUDIO_METADATA", "audio track 字段集合无效")
        signature = {name: item[name] for name in field_names}
        result.append(signature)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class _TimelineScan:
    total: int
    primary_count: int
    secondary_count: int
    timestamp_count: int
    delta_count: int
    stable_count: int
    positive_delta_count: int
    max_positive_delta: Fraction | None
    timestamp_span_rate: Fraction | None

    @property
    def primary_coverage(self) -> float:
        return self.primary_count / self.total if self.total else 0.0

    @property
    def secondary_coverage(self) -> float:
        return self.secondary_count / self.total if self.total else 0.0

    @property
    def cadence_coverage(self) -> float:
        return self.stable_count / self.delta_count if self.delta_count else 0.0

    @property
    def positive_delta_coverage(self) -> float:
        return self.positive_delta_count / self.delta_count if self.delta_count else 0.0


def _scan_timeline(
    argv: list[str],
    *,
    expected_period: Fraction,
    primary_field: str,
    secondary_field: str | None,
    time_base: Fraction,
) -> _TimelineScan:
    """流式扫描 FFprobe compact 输出，避免为长节目保留 packet/frame 数组。"""

    process: subprocess.Popen[str] | None = None
    stderr_tail = ""
    try:
        # stderr 落到临时文件，避免长媒体大量 decode error 填满 PIPE 后与 stdout 互锁。
        with tempfile.TemporaryFile() as stderr_stream:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr_stream,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=background_creation_flags(),
            )
            if process.stdout is None:
                raise Av27MediaError("E_AV27_SOURCE_PROBE_FAILED", "FFprobe timeline pipe 未建立")
            total = primary = secondary = delta_count = stable = positive = 0
            previous: Fraction | None = None
            first: Fraction | None = None
            last: Fraction | None = None
            max_positive: Fraction | None = None
            tolerance = max(
                time_base * Fraction(11, 10),
                expected_period * Fraction(3, 100),
                Fraction(1, 500_000),
            )
            with process.stdout:
                for line in process.stdout:
                    if not line.strip():
                        continue
                    fields = _compact_fields(line)
                    total += 1
                    timestamp = _timestamp(fields.get(primary_field))
                    if timestamp is not None:
                        primary += 1
                    if (
                        secondary_field is not None
                        and _timestamp(fields.get(secondary_field)) is not None
                    ):
                        secondary += 1
                    if timestamp is not None and previous is not None:
                        delta = timestamp - previous
                        delta_count += 1
                        if abs(delta - expected_period) <= tolerance:
                            stable += 1
                        if delta > 0:
                            positive += 1
                            if max_positive is None or delta > max_positive:
                                max_positive = delta
                    if timestamp is not None:
                        if first is None:
                            first = timestamp
                        last = timestamp
                        previous = timestamp
            return_code = process.wait(timeout=30)
            stderr_stream.seek(0, os.SEEK_END)
            stderr_size = stderr_stream.tell()
            stderr_stream.seek(max(0, stderr_size - 4096), os.SEEK_SET)
            stderr_tail = stderr_stream.read().decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as error:
        if process is not None:
            process.kill()
            process.wait()
        raise Av27MediaError("E_AV27_SOURCE_PROBE_FAILED", str(error)) from error
    if return_code != 0:
        raise Av27MediaError(
            "E_AV27_SOURCE_PROBE_FAILED",
            stderr_tail.strip().replace("\n", " ")[-1000:] or f"FFprobe 退出码 {return_code}",
        )
    span_rate = None
    if first is not None and last is not None and last > first and primary > 1:
        span_rate = Fraction(primary - 1, 1) / (last - first)
    return _TimelineScan(
        total,
        primary,
        secondary,
        primary,
        delta_count,
        stable,
        positive,
        max_positive,
        span_rate,
    )


def _compact_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in line.strip().split("|"):
        key, separator, value = item.partition("=")
        if separator:
            fields[key] = value
    return fields


def _timestamp(raw_value: str | None) -> Fraction | None:
    if raw_value is None:
        return None
    raw = raw_value.strip()
    if raw.casefold() in _MISSING:
        return None
    try:
        value = Fraction(raw)
    except (ValueError, ZeroDivisionError):
        return None
    return value


def _timeline_confidence(
    scan: _TimelineScan,
    *,
    packet_count: int,
    rate: Fraction,
    duration: float | None,
) -> str:
    """执行冻结的 high/medium cadence、span-rate 与 N/FPS/duration gate。"""

    if packet_count <= 1 or duration is None or duration <= 0:
        raise Av27MediaError("E_AV27_SOURCE_FPS_AMBIGUOUS", "Source duration/cadence 缺失")
    if scan.total != packet_count:
        raise Av27MediaError(
            "E_AV27_SOURCE_FPS_AMBIGUOUS",
            "Source packet N 与 cadence timeline 样本数矛盾",
        )
    expected_period = Fraction(1, 1) / rate
    timestamp_ratio = scan.timestamp_count / packet_count
    span_consistent = (
        scan.timestamp_span_rate is not None
        and abs(float(scan.timestamp_span_rate / rate) - 1.0) <= 0.001
    )
    max_gap_ok = (
        scan.max_positive_delta is not None and scan.max_positive_delta <= expected_period * 5
    )
    medium = (
        scan.timestamp_count >= 2
        and scan.cadence_coverage >= 0.98
        and timestamp_ratio >= 0.99
        and scan.positive_delta_coverage >= 0.99
        and max_gap_ok
        and span_consistent
    )
    if not medium:
        raise Av27MediaError(
            "E_AV27_SOURCE_FPS_AMBIGUOUS",
            "Source 全片 cadence 置信度不足",
        )
    expected_duration = packet_count / float(rate)
    tolerance = max(2 / float(rate), 0.05)
    if abs(duration - expected_duration) > tolerance:
        raise Av27MediaError(
            "E_AV27_SOURCE_FPS_AMBIGUOUS",
            "Source N/FPS/duration 无法闭合",
        )
    high = (
        packet_count >= 1000
        and scan.cadence_coverage >= 0.9999
        and timestamp_ratio >= 0.999999
        and scan.positive_delta_coverage >= 0.9999
        and scan.max_positive_delta is not None
        and scan.max_positive_delta <= expected_period * Fraction(3, 2)
    )
    return "high" if high else "medium"


def _run_json_probe(argv: list[str], *, timeout: int) -> object:
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=background_creation_flags(),
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Av27MediaError("E_AV27_PROBE_FAILED", str(error)) from error
    if completed.returncode != 0:
        raise Av27MediaError(
            "E_AV27_PROBE_FAILED",
            completed.stderr.strip().replace("\n", " ")[-1000:]
            or f"FFprobe 退出码 {completed.returncode}",
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe 没有返回合法 JSON") from error


def _parse_header(path: Path, payload: object, *, count_frames: bool) -> Av27MediaHeader:
    if not isinstance(payload, Mapping):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe 根值必须是 object")
    streams_raw = payload.get("streams")
    format_raw = payload.get("format")
    chapters_raw = payload.get("chapters", [])
    if not isinstance(streams_raw, list) or not isinstance(format_raw, Mapping):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe 缺少 streams/format")
    if not isinstance(chapters_raw, list):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe chapters 必须是 array")
    format_name = _text(format_raw.get("format_name"), required=True)
    assert format_name is not None
    duration = _positive_float(format_raw.get("duration"))
    kinds: list[str] = []
    videos: list[Av27VideoHeader] = []
    audios: list[Av27AudioHeader] = []
    others: list[Av27OtherStreamHeader] = []
    for item in streams_raw:
        if not isinstance(item, Mapping):
            raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe stream 必须是 object")
        kind = _text(item.get("codec_type"), required=True)
        assert kind is not None
        kinds.append(kind)
        if kind == "video":
            videos.append(_parse_video(item, count_frames=count_frames))
        elif kind == "audio":
            audios.append(_parse_audio(item))
        else:
            others.append(
                Av27OtherStreamHeader(
                    index=_integer(item.get("index"), allow_zero=True),
                    kind=kind,
                    codec=_required_lower_text(item.get("codec_name")),
                    codec_tag_string=_text(item.get("codec_tag_string")),
                )
            )
    if not kinds:
        raise Av27MediaError("E_AV27_STREAM_MISSING", "媒体不含 stream")
    return Av27MediaHeader(
        path=path,
        format_name=format_name,
        duration_seconds=duration,
        streams=tuple(kinds),
        videos=tuple(videos),
        audios=tuple(audios),
        others=tuple(others),
        chapter_count=len(chapters_raw),
    )


def _parse_video(raw: Mapping[str, Any], *, count_frames: bool) -> Av27VideoHeader:
    side_data = raw.get("side_data_list", [])
    tags = raw.get("tags", {})
    if not isinstance(side_data, list):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "side_data_list 必须是 array")
    if not isinstance(tags, Mapping):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "video tags 必须是 object")
    rotations: list[int] = []
    tag_rotation = tags.get("rotate")
    if tag_rotation is not None:
        rotations.append(_rotation(tag_rotation))
    hdr_side_data: list[str] = []
    for item in side_data:
        if not isinstance(item, Mapping):
            continue
        side_type = _lower_text(item.get("side_data_type")) or ""
        if any(token in side_type for token in _HDR_SIDE_DATA_TOKENS):
            hdr_side_data.append(side_type)
        if item.get("rotation") is not None:
            rotations.append(_rotation(item["rotation"]))
    if len(set(rotations)) > 1:
        raise Av27MediaError("E_AV27_ROTATION", "视频 rotation authorities 冲突")
    rotation = rotations[0] if rotations else 0
    average = _required_rate(raw.get("avg_frame_rate"), "avg_frame_rate")
    advertised = _required_rate(raw.get("r_frame_rate"), "r_frame_rate")
    if not rates_equivalent(average, advertised):
        raise Av27MediaError("E_AV27_RATE_INVALID", "avg_frame_rate/r_frame_rate 相互矛盾")
    time_base = _required_rate(raw.get("time_base"), "time_base")
    frame_key = "nb_read_frames" if count_frames else "nb_frames"
    return Av27VideoHeader(
        index=_integer(raw.get("index"), allow_zero=True),
        codec=_required_lower_text(raw.get("codec_name")),
        profile=_text(raw.get("profile")),
        codec_tag_string=_text(raw.get("codec_tag_string")),
        width=_integer(raw.get("width")),
        height=_integer(raw.get("height")),
        pixel_format=_lower_text(raw.get("pix_fmt")),
        frame_rate=average,
        avg_frame_rate=average,
        r_frame_rate=advertised,
        time_base=time_base,
        frame_count=_optional_positive_integer(raw.get(frame_key)),
        sample_aspect_ratio=_text(raw.get("sample_aspect_ratio")),
        field_order=_lower_text(raw.get("field_order")),
        rotation=rotation,
        color_range=_lower_text(raw.get("color_range")),
        color_space=_lower_text(raw.get("color_space")),
        color_transfer=_lower_text(raw.get("color_transfer")),
        color_primaries=_lower_text(raw.get("color_primaries")),
        chroma_location=_lower_text(raw.get("chroma_location")),
        hdr_side_data=tuple(hdr_side_data),
        duration_seconds=_positive_float(raw.get("duration")),
    )


def _parse_audio(raw: Mapping[str, Any]) -> Av27AudioHeader:
    tags = raw.get("tags", {})
    dispositions = raw.get("disposition", {})
    if not isinstance(tags, Mapping) or not isinstance(dispositions, Mapping):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "audio tags/disposition 必须是 object")
    return Av27AudioHeader(
        index=_integer(raw.get("index"), allow_zero=True),
        codec=_required_lower_text(raw.get("codec_name")),
        profile=_text(raw.get("profile")),
        extradata_hash=_text(raw.get("extradata_hash")),
        sample_rate=_optional_positive_integer(raw.get("sample_rate")),
        channels=_optional_positive_integer(raw.get("channels")),
        channel_layout=_text(raw.get("channel_layout")),
        language=_text(tags.get("language")),
        title=_text(tags.get("title")),
        default=_flag(dispositions.get("default", 0)),
        forced=_flag(dispositions.get("forced", 0)),
    )


def _required_rate(value: object, field: str) -> Fraction:
    if not isinstance(value, str):
        raise Av27MediaError("E_AV27_RATE_INVALID", f"{field} 必须是 rational string")
    try:
        parsed = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise Av27MediaError("E_AV27_RATE_INVALID", f"{field} 无效") from error
    if parsed <= 0:
        raise Av27MediaError("E_AV27_RATE_INVALID", f"{field} 必须为正")
    return parsed


def _rotation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise Av27MediaError("E_AV27_ROTATION", "rotation 必须是有限整数角度")
    try:
        numeric = float(value)
    except ValueError as error:
        raise Av27MediaError("E_AV27_ROTATION", "rotation 无法解析") from error
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise Av27MediaError("E_AV27_ROTATION", "rotation 必须是有限整数角度")
    return int(numeric) % 360


def _text(value: object, *, required: bool = False) -> str | None:
    if value in (None, "", "N/A"):
        if required:
            raise Av27MediaError("E_AV27_PROBE_INVALID", "必需文本字段缺失")
        return None
    if not isinstance(value, str):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe 文本字段类型无效")
    return value


def _lower_text(value: object) -> str | None:
    text = _text(value)
    return None if text is None else text.casefold()


def _required_lower_text(value: object) -> str:
    text = _lower_text(value)
    if text is None:
        raise Av27MediaError("E_AV27_PROBE_INVALID", "codec_name 缺失")
    return text


def _integer(value: object, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "integer 不得为 bool")
    try:
        parsed = int(value) if isinstance(value, str | int) else -1
    except ValueError as error:
        raise Av27MediaError("E_AV27_PROBE_INVALID", "integer 无法解析") from error
    minimum = 0 if allow_zero else 1
    if parsed < minimum:
        raise Av27MediaError("E_AV27_PROBE_INVALID", f"integer 必须 >= {minimum}")
    return parsed


def _optional_positive_integer(value: object) -> int | None:
    if value in (None, "", "N/A", "0", 0):
        return None
    return _integer(value)


def _positive_float(value: object) -> float | None:
    if value in (None, "", "N/A"):
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "duration 类型无效")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise Av27MediaError("E_AV27_PROBE_INVALID", "duration 必须有限")
    return parsed if parsed > 0 else None


def _flag(value: object) -> bool:
    if value in (0, "0", False):
        return False
    if value in (1, "1", True):
        return True
    raise Av27MediaError("E_AV27_PROBE_INVALID", "disposition flag 必须为 0/1")


def _nonempty_file(path: str | Path) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
        stat = resolved.stat()
    except OSError as error:
        raise Av27MediaError("E_AV27_INPUT_UNREADABLE", str(error)) from error
    if not resolved.is_file() or stat.st_size <= 0:
        raise Av27MediaError("E_AV27_INPUT_UNREADABLE", f"不是非空常规文件：{path}")
    return resolved
