"""提供 v0.3.5 单一 Source 准入，不改变旧 exact 节点的媒体规则。

复用 AV27 的 header 值对象与解析器，仅共享信息结构；新的准入由本模块独立检查。
允许 AVEnhanceFlow 2.7 的有理帧率等价、缺失信号解释及 DTS 优先时间线，不加入逐帧
raw PTS 等于 best-effort PTS、音频 priming 清零或完整内容证明等额外前置条件。
"""

from __future__ import annotations

import json
import threading
from contextlib import closing
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from zniku.avenhance_v27.probe import (
    Av27MediaError,
    Av27MediaHeader,
    SourceTimeline,
    _parse_header,
    canonical_fraction,
    canonical_source_rate,
    require_only_av_streams,
)
from zniku.media.probe import resolve_media_tool
from zniku.runtime.progress import ProgressReporter

from .process import check_cancel, probe_lines
from .timeline import scan_source_timeline

_MISSING = frozenset({"", "n/a", "none", "unknown", "unspecified"})
_EXPECTED_SIGNAL: dict[str, object] = {
    "color_range": "tv",
    "color_space": "bt709",
    "color_transfer": "bt709",
    "color_primaries": "bt709",
    "chroma_location": "left",
    "field_order": "progressive",
    "sample_aspect_ratio": "1:1",
    "rotation": 0,
}


@dataclass(frozen=True, slots=True)
class SourceAdmission:
    """本次准入的有限媒体事实；不包含 repair receipt 或可执行内容。"""

    header: Av27MediaHeader
    timeline: SourceTimeline
    frame_rate: Fraction
    signal: dict[str, object]
    warnings: tuple[str, ...]
    cadence: dict[str, object]

    def namespace_summary(self, *, source_ordinal: int = 0) -> dict[str, object]:
        """映射旧下游可消费的信息形状，不声称已经执行旧 Source exact 合同。"""
        if type(source_ordinal) is not int or source_ordinal < 0:
            raise Av27MediaError("E_AV27_SOURCE_ORDER", "source_ordinal 必须是非负整数")
        video = self.header.video
        return {
            "frame_count": self.timeline.frame_count,
            "frame_rate": canonical_fraction(self.frame_rate),
            "geometry": {"width": video.width, "height": video.height},
            "sample_aspect_ratio": self.signal["sample_aspect_ratio"],
            "field_order": self.signal["field_order"],
            "rotation": self.signal["rotation"],
            "signal": dict(self.signal),
            "chroma_location": self.signal["chroma_location"],
            "duration_seconds": video.duration_seconds or self.header.duration_seconds,
            "container": {
                "format_name": self.header.format_name,
                "chapter_count": self.header.chapter_count,
            },
            "video": video.to_summary(),
            "audio_tracks": [audio.to_summary() for audio in self.header.audios],
            "source_ordinal": source_ordinal,
            "timeline": {
                "authority": self.timeline.authority,
                "confidence": self.timeline.confidence,
                "dts_coverage": self.timeline.dts_coverage,
                "cadence_coverage": self.timeline.cadence_coverage,
            },
            "source_admission": {
                "profile": "avenhanceflow-2.7-source-v0.3.5",
                "cadence": dict(self.cadence),
            },
        }


def validate_source_header(
    header: Av27MediaHeader,
) -> tuple[Fraction, dict[str, object], tuple[str, ...]]:
    """先验证便宜的结构、信号与 rate，避免已知不支持的输入触发全片读取。"""
    video = header.video
    require_only_av_streams(header, role="Source")
    if header.chapter_count:
        raise Av27MediaError("E_AV27_SOURCE_CHAPTERS", "Source 不接受容器内置 chapters")
    if (
        type(video.width) is not int
        or type(video.height) is not int
        or video.width <= 0
        or video.height <= 0
        or video.width * 9 != video.height * 16
    ):
        raise Av27MediaError("E_AV27_SOURCE_GEOMETRY", "Source coded geometry 必须精确为 16:9")
    warnings: list[str] = []
    for field, expected in _EXPECTED_SIGNAL.items():
        if field == "rotation":
            continue
        raw = getattr(video, field)
        actual = None if raw is None else str(raw).strip().casefold()
        if actual is None or actual in _MISSING:
            warnings.append(f"Source {field} 缺失，按 BT.709 SDR limited 工作域解释")
            continue
        aliases = {"mpeg": "tv", "limited": "tv"} if field == "color_range" else {}
        if field == "sample_aspect_ratio":
            aliases = {"1/1": "1:1"}
        if aliases.get(actual, actual) != expected:
            raise Av27MediaError(
                "E_AV27_SIGNAL_CONFLICT", f"Source {field}={raw!r} 与 {expected!r} 冲突"
            )
    if video.rotation:
        raise Av27MediaError("E_AV27_ROTATION", "Source rotation 必须为 0")
    if video.hdr_side_data:
        raise Av27MediaError("E_AV27_SIGNAL_CONFLICT", "Source 含 HDR side data")
    return canonical_source_rate(video), dict(_EXPECTED_SIGNAL), tuple(warnings)


def probe_header(
    path: str | Path,
    *,
    ffprobe_executable: str | None = None,
    cancel_event: threading.Event | None = None,
    progress: ProgressReporter | None = None,
) -> Av27MediaHeader:
    """可取消地读 header；复用旧解析器，不调用旧 Source 时间线或逐帧 CFR 门。"""
    source = _source_file(path)
    entries = (
        "format=format_name,duration:chapter=id:"
        "stream=index,codec_type,codec_name,profile,codec_tag_string,width,height,pix_fmt,"
        "avg_frame_rate,r_frame_rate,time_base,nb_frames,duration,sample_aspect_ratio,"
        "field_order,color_range,color_space,color_transfer,color_primaries,chroma_location,"
        "sample_rate,channels,channel_layout,extradata_hash:"
        "stream_tags=language,title,rotate:stream_disposition=default,forced:"
        "stream_side_data=side_data_type,rotation"
    )
    argv = [
        ffprobe_executable or resolve_media_tool("ffprobe"),
        "-v",
        "error",
        "-show_data_hash",
        "sha256",
        "-show_entries",
        entries,
        "-of",
        "json",
        "--",
        str(source),
    ]
    # show_data_hash 只取得音轨 codec extradata signature，不扫描或摘要整个媒体 payload。
    chunks: list[str] = []
    size = 0
    with closing(
        probe_lines(
            argv,
            cancel_event=cancel_event,
            heartbeat=(lambda: progress.report(0.0)) if progress is not None else None,
            timeout=60,
        )
    ) as lines:
        for line in lines:
            size += len(line.encode("utf-8"))
            if size > 4 * 1024 * 1024:
                raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe header 超过 4 MiB")
            chunks.append(line)
    try:
        payload = json.loads("".join(chunks))
    except json.JSONDecodeError as error:
        raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe header 不是合法 JSON") from error
    try:
        return _parse_header(source, payload, count_frames=False)
    except (ValueError, TypeError, OverflowError) as error:
        raise Av27MediaError("E_AV27_PROBE_INVALID", "FFprobe header 数值字段无效") from error


def admit_source(
    path: str | Path,
    *,
    header: Av27MediaHeader | None = None,
    ffprobe_executable: str | None = None,
    cancel_event: threading.Event | None = None,
    progress: ProgressReporter | None = None,
) -> SourceAdmission:
    """执行新准入并返回一次性结果；失败不创建媒体、结果 Artifact 或修复候选。"""
    check_cancel(cancel_event)
    source = _source_file(path)
    inspected = header or probe_header(
        source,
        ffprobe_executable=ffprobe_executable,
        cancel_event=cancel_event,
        progress=progress,
    )
    if inspected.path.resolve() != source:
        raise Av27MediaError("E_AV27_SOURCE_PATH", "header 与本次 Source 路径不匹配")
    rate, signal, signal_warnings = validate_source_header(inspected)
    timeline, cadence = scan_source_timeline(
        source,
        header=inspected,
        ffprobe_executable=ffprobe_executable,
        cancel_event=cancel_event,
        progress=progress,
    )
    check_cancel(cancel_event)
    if progress is not None:
        progress.report(1.0)
    return SourceAdmission(
        inspected,
        timeline,
        rate,
        signal,
        (*signal_warnings, *timeline.warnings),
        cadence,
    )


def probe_source_timeline(
    path: str | Path,
    *,
    header: Av27MediaHeader | None = None,
    ffprobe_executable: str | None = None,
    cancel_event: threading.Event | None = None,
    progress: ProgressReporter | None = None,
) -> SourceTimeline:
    """为现有 probe 注入接口提供最小适配；只有新 exact 节点应选择本实现。"""
    return admit_source(
        path,
        header=header,
        ffprobe_executable=ffprobe_executable,
        cancel_event=cancel_event,
        progress=progress,
    ).timeline


def _source_file(path: str | Path) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_file() or resolved.stat().st_size <= 0:
            raise OSError("不是非空常规文件")
    except OSError as error:
        raise Av27MediaError("E_AV27_INPUT_UNREADABLE", str(error)) from error
    return resolved
