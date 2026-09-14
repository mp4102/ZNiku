"""完整但有界地观察素材展示时间、音频和解码结果。

这里不改写媒体，不把容器总时长当作视频终点。异常媒体可以产生完整诊断，
工具失败、取消、输入变化和无完整 EOF 则抛出错误而不产生成功报告。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from time import monotonic
from typing import Any

from zniku.media.probe import resolve_media_tool
from zniku.runtime.progress import ProgressReporter

from .bitstream import initial_h264_clock
from .models import (
    AudioObservation,
    DiagnosticReport,
    FileStat,
    Finding,
    InitialBitstreamClock,
    VideoObservation,
    rational,
)
from .process import capture_process, fail, stream_process
from .progress import sample, stage

_INPUT_FORMATS = {".mkv": "matroska", ".mp4": "mov", ".mov": "mov"}


def checked_file(path: Path) -> Path:
    """在 probe 前拒绝播放列表、链接和非实体文件；不展开外部协议。"""
    if not path.is_absolute() or path.suffix.lower() not in _INPUT_FORMATS:
        raise fail("SOURCE_PATH", "请选择本机实体 MKV、MP4 或 MOV 视频文件")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise fail("SOURCE_LINK", "素材路径不能经过链接或目录联接")
    if not path.is_file() or path.stat().st_size < 1:
        raise fail("SOURCE_PATH", "素材文件不存在、不是普通文件或为空")
    return path.resolve(strict=True)


def file_stat(path: Path) -> FileStat:
    stat = path.stat()
    return FileStat(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def probe_argv(path: Path) -> list[str]:
    return [
        resolve_media_tool("ffprobe"),
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov,matroska",
        "-f",
        _INPUT_FORMATS[path.suffix.lower()],
    ]


def read_header(path: Path, *, progress: ProgressReporter | None = None) -> dict[str, Any]:
    """读取受限实体容器头；容器不匹配不能用改扩展名绕过。"""
    path = checked_file(path)
    raw = capture_process(
        [
            *probe_argv(path),
            "-show_streams",
            "-show_format",
            "-show_chapters",
            "-of",
            "json",
            str(path),
        ],
        progress=progress,
    )
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("streams"), list):
        raise fail("HEADER", "媒体头信息无效")
    formats = str(data.get("format", {}).get("format_name", "")).split(",")
    if _INPUT_FORMATS[path.suffix.lower()] not in formats:
        raise fail("CONTAINER", "文件实际容器与声明不一致")
    return data


def _fraction(value: object) -> Fraction | None:
    if value in {None, "", "N/A", "0/0"}:
        return None
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None


def _row(raw: bytes) -> dict[str, str]:
    return {
        key.rsplit(":", 1)[-1]: value
        for field in raw.decode("utf-8", errors="strict").strip().split("|")
        if "=" in field
        for key, value in (field.split("=", 1),)
    }


def _scan(
    path: Path,
    stream: str,
    entries: str,
    frames: bool,
    callback: Any,
    progress: ProgressReporter | None,
) -> bool:
    return stream_process(
        [
            *probe_argv(path),
            "-select_streams",
            stream,
            "-show_frames" if frames else "-show_packets",
            "-show_entries",
            entries,
            "-of",
            "compact=p=0:nk=0",
            str(path),
        ],
        consume=lambda raw: callback(_row(raw)),
        progress=progress,
        permit_media_errors=True,
    )


def observe_video(
    path: Path,
    header: Mapping[str, Any],
    *,
    rate: Fraction | None,
    progress: ProgressReporter | None = None,
) -> VideoObservation:
    """展示顺序中逐帧比较 rawPTS 与目标时钟；不按 DTS 或时间排序修复观察。"""
    tb = _fraction(header.get("time_base"))
    if tb is None or tb <= 0:
        raise fail("TIME_BASE", "视频 time_base 无法解析")
    count = packets = missing = duplicate = backward = differences = 0
    first: Fraction | None = None
    previous: Fraction | None = None
    maximum = Fraction(0)

    def frame(row: dict[str, str]) -> None:
        nonlocal count, missing, duplicate, backward, differences, first, previous, maximum
        if not row:
            return
        if "media_type" in row and row["media_type"] != "video":
            return
        pts = _fraction(row.get("pts"))
        best = _fraction(row.get("best_effort_timestamp"))
        if pts != best:
            differences += 1
        if pts is None:
            missing += 1
        else:
            actual = pts * tb
            first = actual if first is None else first
            if previous is not None:
                duplicate += actual == previous
                backward += actual < previous
            if rate is not None:
                maximum = max(maximum, abs(actual - first - count / rate))
            previous = actual
        count += 1
        sample(count)

    def packet(row: dict[str, str]) -> None:
        nonlocal packets
        if row:
            packets += 1
            sample(packets)

    with stage("video_scan", "frames"):
        frame_errors = _scan(
            path, "v:0", "frame=media_type,pts,best_effort_timestamp", True, frame, progress
        )
    with stage("packet_scan", "packets"):
        packet_errors = _scan(path, "v:0", "packet=pts", False, packet, progress)
    rotation = "0"
    for side in header.get("side_data_list", []):
        if "rotation" in side:
            rotation = str(side["rotation"])
    clock = InitialBitstreamClock(status="not_applicable")
    if header.get("codec_name") == "h264":
        with stage("bitstream_header"):
            clock_status, fixed_rate = initial_h264_clock(path, progress=progress)
        clock = InitialBitstreamClock(status=clock_status, fixed_frame_rate=fixed_rate)
    return VideoObservation(
        stream_index=int(header["index"]),
        codec=str(header.get("codec_name", "unknown")),
        width=int(header.get("width", 0)),
        height=int(header.get("height", 0)),
        pixel_format=str(header.get("pix_fmt", "unknown")),
        sample_aspect_ratio=str(header.get("sample_aspect_ratio", "unknown")),
        field_order=str(header.get("field_order", "unknown")),
        color_range=str(header.get("color_range", "unknown")),
        color_space=str(header.get("color_space", "unknown")),
        color_transfer=str(header.get("color_transfer", "unknown")),
        color_primaries=str(header.get("color_primaries", "unknown")),
        chroma_location=str(header.get("chroma_location", "unknown")),
        rotation=rotation,
        hdr_side_data=any(
            any(
                token in str(side.get("side_data_type", "")).lower()
                for token in (
                    "mastering",
                    "content light",
                    "dovi",
                    "dolby",
                    "hdr",
                    "smpte2094",
                )
            )
            for side in header.get("side_data_list", [])
        ),
        r_frame_rate=str(header.get("r_frame_rate", "0/0")),
        avg_frame_rate=str(header.get("avg_frame_rate", "0/0")),
        time_base=rational(tb),
        frame_count=count,
        packet_count=packets,
        first_pts=None if first is None else rational(first),
        last_pts=None if previous is None else rational(previous),
        missing_pts=missing,
        duplicate_pts=duplicate,
        backward_pts=backward,
        best_effort_differences=differences,
        max_clock_error=None if rate is None else rational(maximum),
        decode_errors=frame_errors or packet_errors,
        bitstream_clock=clock,
    )


def observe_audio(
    path: Path,
    header: Mapping[str, Any],
    *,
    progress: ProgressReporter | None = None,
) -> AudioObservation:
    """独立读取音频样本与原包语义；不要求该载体的视频先通过 CFR。"""
    index = int(header["index"])
    sample_rate = int(header.get("sample_rate", 0))
    if sample_rate <= 0:
        raise fail("AUDIO_FORMAT", "音轨采样率无法确定")
    tb = _fraction(header.get("time_base"))
    if tb is None or tb <= 0:
        raise fail("AUDIO_FORMAT", "音轨 time_base 无法确定")
    total = missing = jumps = skip = discard = 0
    first: Fraction | None = None
    end: Fraction | None = None
    packet_start: Fraction | None = None

    def frame(row: dict[str, str]) -> None:
        nonlocal total, missing, jumps, first, end
        if "nb_samples" not in row:
            return
        samples = int(row["nb_samples"])
        pts = _fraction(row.get("pts"))
        if samples < 0:
            raise fail("AUDIO_FORMAT", "音频样本数无效")
        if pts is None:
            missing += 1
        else:
            actual = pts * tb
            first = actual if first is None else first
            if end is not None and abs(actual - end) > max(2 * tb, Fraction(1, sample_rate)):
                jumps += 1
            end = actual + Fraction(samples, sample_rate)
        total += samples
        sample(total)

    def packet(row: dict[str, str]) -> None:
        nonlocal packet_start, skip, discard
        pts = _fraction(row.get("pts"))
        if packet_start is None and pts is not None:
            packet_start = pts * tb
        skip = max(skip, int(row.get("skip_samples", "0")))
        discard = max(discard, int(row.get("discard_padding", "0")))

    with stage("audio_scan", "samples"):
        frame_errors = _scan(path, str(index), "frame=pts,nb_samples", True, frame, progress)
    with stage("audio_packet_scan"):
        packet_errors = _scan(
            path,
            str(index),
            "packet=pts:packet_side_data=skip_samples,discard_padding",
            False,
            packet,
            progress,
        )
    return AudioObservation(
        stream_index=index,
        codec=str(header.get("codec_name", "unknown")),
        sample_rate=sample_rate,
        channels=int(header.get("channels", 0)),
        channel_layout=str(header.get("channel_layout", "unknown")),
        language=str(header.get("tags", {}).get("language", "und")),
        profile=str(header.get("profile", "unknown")),
        title=str(header.get("tags", {}).get("title", "")),
        default=bool(header.get("disposition", {}).get("default", 0)),
        forced=bool(header.get("disposition", {}).get("forced", 0)),
        sample_count=total,
        start_time=None if first is None else rational(first),
        end_time=None if end is None else rational(end),
        first_packet_time=None if packet_start is None else rational(packet_start),
        skip_samples=skip,
        discard_padding=discard,
        missing_pts=missing,
        discontinuities=jumps,
        decode_errors=frame_errors or packet_errors,
    )


def _video_profile_message(video: VideoObservation) -> str:
    """区分探测未确定的色彩与已知不支持值；只改善诊断，不推断来源或放宽准入。

    FFprobe 合并视图中的 unknown 不证明容器或 SPS 字段真的缺失，因此这里不将其写成
    absent、标准默认值或 BT.709。原始声明分层及工作色彩解释仍待独立政策冻结。
    """
    fields = (
        ("color_primaries", "色度原色", "bt709"),
        ("color_transfer", "传递特性", "bt709"),
        ("color_space", "色彩矩阵", "bt709"),
        ("color_range", "信号范围", "tv"),
        ("chroma_location", "色度采样位置", "left"),
    )
    missing = [f"{label} ({key})" for key, label, _ in fields if getattr(video, key) == "unknown"]
    unsupported = [
        f"{label} ({key}={getattr(video, key)[:64]})"
        for key, label, expected in fields
        if getattr(video, key) not in {"unknown", expected}
    ]
    details = [
        "首发处理链要求方形像素、零旋转、明确 BT.709 limited/left 声明的 SDR 逐行 H.264/yuv420p。"
    ]
    if missing:
        details.append("当前探测未确定：" + "、".join(missing) + "。这不等于视频损坏。")
        details.append("仅重封装不能代替后续处理的色彩解释；当前不会猜测或自动补标签放行。")
    if unsupported:
        details.append("当前色彩值不在本链支持范围：" + "、".join(unsupported) + "。")
    return "".join(details)


def findings_for(report: DiagnosticReport) -> tuple[Finding, ...]:
    """确定合同不兼容项；诊断事实不由外部修复建议反向修改。"""
    issues: list[Finding] = []

    def add(code: str, message: str) -> None:
        issues.append(Finding(code=f"E_SOURCE_PREPARATION_{code}", message=message))

    v = report.video
    if report.target_frame_rate is None:
        add("RATE_SELECTION_REQUIRED", "帧率声明有歧义，请明确选择精确目标帧率后重新检查")
    if v.codec == "h264":
        if v.bitstream_clock.status in {"unavailable", "ambiguous"}:
            add("BITSTREAM_CLOCK_UNSUPPORTED", "初始 H.264 SPS 时钟无法明确读取或相互矛盾")
        elif (
            report.target_frame_rate is not None
            and v.bitstream_clock.fixed_frame_rate is not None
            and Fraction(v.bitstream_clock.fixed_frame_rate) != Fraction(report.target_frame_rate)
        ):
            add("BITSTREAM_CLOCK_CONFLICT", "初始 H.264 固定 VUI 时钟与目标精确帧率冲突")
    if report.extra_streams or report.chapter_count:
        add("STREAM_LAYOUT", "额外流或内嵌章节尚未获得本版本保内容支持")
    if v.decode_errors or any(a.decode_errors for a in report.audio):
        add("DECODE_ERRORS", "完整检查观察到载荷解码错误，不适用内置时钟修复")
    if v.frame_count < 2 or v.frame_count != v.packet_count:
        add("FRAME_PACKET_RELATION", "包与展示帧关系不符合当前一包一帧范围")
    if v.missing_pts or v.duplicate_pts or v.backward_pts or v.best_effort_differences:
        add("PTS_MAPPING_UNSUPPORTED", "存在缺失、重复、逆序或重建 PTS，需要尚未启用的映射策略")
    if report.target_frame_rate is not None and v.max_clock_error is not None:
        tolerance = max(2 * Fraction(v.time_base), Fraction(1, 1_000_000))
        if tolerance >= 1 / Fraction(report.target_frame_rate) / 4:
            add("TIME_BASE_COARSE", "时间基过粗，不能证明目标 CFR")
        elif Fraction(v.max_clock_error) > tolerance:
            add("CLOCK_NOT_CFR", "展示时间轴不符合目标精确帧率，需要工作副本或外部处理")
    if (
        v.codec != "h264"
        or v.hdr_side_data
        or v.pixel_format != "yuv420p"
        or v.rotation != "0"
        or v.sample_aspect_ratio not in {"1:1", "1/1"}
        or v.field_order != "progressive"
        or any(
            value != "bt709"
            for value in (
                v.color_space,
                v.color_primaries,
                v.color_transfer,
            )
        )
        or v.color_range != "tv"
        or v.chroma_location != "left"
    ):
        add(
            "VIDEO_PROFILE_UNSUPPORTED",
            _video_profile_message(v),
        )
    for audio in report.audio:
        if (
            audio.skip_samples
            or audio.discard_padding
            or (audio.first_packet_time is not None and Fraction(audio.first_packet_time) < 0)
        ):
            add("AUDIO_PRIMING_UNSUPPORTED", "音频包含 priming/edit-list/padding，专项尚未晋级")
            break
    if any(
        a.codec != "aac"
        or a.missing_pts
        or a.discontinuities
        or not a.sample_count
        or a.start_time is None
        or a.end_time is None
        for a in report.audio
    ):
        add("AUDIO_TIMELINE_UNSUPPORTED", "音频编码或有效样本时间轴不符合首发范围")
    if v.first_pts is not None and report.target_frame_rate is not None:
        origin = Fraction(v.first_pts)
        duration = Fraction(v.frame_count) / Fraction(report.target_frame_rate)
        for audio in report.audio:
            if audio.start_time is None or audio.end_time is None:
                continue
            # 起点/末端关系分别比较；AAC 一个解码帧的尾部量化并不表示视频缺帧。
            tolerance = max(
                Fraction(2048, audio.sample_rate), 1 / Fraction(report.target_frame_rate)
            )
            if (
                abs(Fraction(audio.start_time) - origin) > tolerance
                or abs(Fraction(audio.end_time) - origin - duration) > tolerance
            ):
                add("AUDIO_ALIGNMENT", "所选音轨的首尾关系与参考视频不匹配，需要独立音频处理")
                break
    return tuple(issues)


def inspect_source(
    path: Path,
    artifact_id: str,
    *,
    target_frame_rate: str | None = None,
    progress: ProgressReporter | None = None,
) -> DiagnosticReport:
    """完整只读检查；目标率仅来自显式输入或完全一致的双 header 声明。"""
    started = monotonic()
    path = checked_file(path)
    before = file_stat(path)
    header = read_header(path, progress=progress)
    streams = header["streams"]
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) > 16:
        raise fail("STREAM_LAYOUT", "检查入口要求一条视频且最多 16 条音轨")
    if target_frame_rate is None:
        r = _fraction(videos[0].get("r_frame_rate"))
        avg = _fraction(videos[0].get("avg_frame_rate"))
        target_frame_rate = rational(r) if r is not None and r == avg and 1 <= r <= 240 else None
    rate = None if target_frame_rate is None else Fraction(target_frame_rate)
    version = capture_process([resolve_media_tool("ffprobe"), "-version"], progress=progress)
    video = observe_video(path, videos[0], rate=rate, progress=progress)
    audio = tuple(observe_audio(path, item, progress=progress) for item in audios)
    after = file_stat(path)
    if before != after:
        raise fail("SOURCE_CHANGED", "检查期间素材发生变化，所有观察作废")
    duration = _fraction(header.get("format", {}).get("duration"))
    report = DiagnosticReport(
        original_media_artifact_id=artifact_id,
        source_stat=before,
        final_stat=after,
        ffprobe_version=version.decode("utf-8").splitlines()[0][:512],
        container=str(header.get("format", {}).get("format_name", "")),
        container_duration=None if duration is None else rational(duration),
        extra_streams=len(streams) - len(videos) - len(audios),
        chapter_count=len(header.get("chapters", [])),
        target_frame_rate=target_frame_rate,
        video=video,
        audio=audio,
        findings=(),
        candidate_strategies=(),
        elapsed_seconds=monotonic() - started,
    )
    findings = findings_for(report)
    candidates: tuple[Any, ...] = ()
    if (
        {item.code for item in findings} == {"E_SOURCE_PREPARATION_CLOCK_NOT_CFR"}
        and rate is not None
        and video.max_clock_error is not None
        and Fraction(video.max_clock_error) < 1 / rate / 4
    ):
        candidates = ("t1-clock-quantization/1",)
    return DiagnosticReport.model_validate(
        {
            **report.model_dump(),
            "findings": findings,
            "candidate_strategies": candidates,
        }
    )
