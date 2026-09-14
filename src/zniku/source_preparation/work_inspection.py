"""普通单次解码检查：同时聚合视频、音频及包事实，不调用旧 SPS/像素审计。

可读但不支持的头信息产生有限 unsupported 报告；执行失败、取消或输入变化不产生成功报告。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from time import monotonic
from typing import Any

from zniku.media.probe import resolve_media_tool
from zniku.runtime.progress import ProgressReporter

from .inspection import _fraction, _row, checked_file, file_stat, probe_argv, read_header
from .models import AudioObservation, Finding, rational
from .process import capture_process, fail, stream_process
from .progress import sample, stage
from .work_models import (
    SIGNAL_EXPECTED,
    Decision,
    DiagnosticParameters,
    FrameVariant,
    ObservedSignal,
    ProbeSignal,
    WorkDiagnosticReport,
    WorkVideoObservation,
    clock_tolerance,
)

_CODECS = {"h264", "hevc", "ffv1", "prores", "vp9", "av1", "mpeg4", "mpeg2video"}
_PIXELS = {"yuv420p", "yuv420p10le", "yuv422p", "yuv422p10le", "yuv444p", "yuv444p10le"}


def _signal(data: Mapping[str, Any]) -> ProbeSignal:
    values = {}
    for name in SIGNAL_EXPECTED:
        value = data.get(name)
        values[name] = (
            None if value in {None, "", "unknown", "unspecified", "reserved", "N/A"} else str(value)
        )
    return ProbeSignal.model_validate(values)


def _hdr(data: Mapping[str, Any]) -> bool:
    return any(
        token in str(data.get("side_data_list", data.get("side_data_type", ""))).lower()
        for token in ("mastering", "content light", "dovi", "dolby", "hdr", "smpte2094", "icc")
    )


def signal_observation(
    header: Mapping[str, Any], variants: tuple[FrameVariant, ...], *, hdr: bool = False
) -> ObservedSignal:
    """汇总工具层差异；不把未知解读为原始 absence 或未审计声明的一致性证明。"""
    declared = _signal(header)
    missing: list[str] = []
    conflicts: list[str] = []
    changes: list[str] = []
    unsupported: list[str] = []
    for name, expected in SIGNAL_EXPECTED.items():
        frame_values = {getattr(v.signal, name) for v in variants}
        explicit = {value for value in frame_values if value is not None}
        head = getattr(declared, name)
        if not explicit and head is None:
            missing.append(name)
        if len(frame_values) > 1:
            changes.append(name)
        if head is not None and explicit and explicit != {head}:
            conflicts.append(name)
        if any(value != expected for value in explicit | ({head} if head else set())):
            unsupported.append(name)
    if hdr or _hdr(header):
        unsupported.append("hdr_or_icc")
    shape = {
        (v.width, v.height, v.pixel_format, v.sample_aspect_ratio, v.interlaced) for v in variants
    }
    if len(shape) > 1:
        changes.append("frame_geometry")
    for v in variants:
        if (
            v.width != header.get("width")
            or v.height != header.get("height")
            or v.pixel_format != header.get("pix_fmt")
            or v.sample_aspect_ratio not in {"1:1", "1/1"}
            or v.interlaced
        ):
            unsupported.append("frame_geometry")
            break
    return ObservedSignal(
        header=declared,
        variants=variants,
        missing_fields=tuple(missing),
        conflicts=tuple(conflicts),
        changes=tuple(changes),
        unsupported=tuple(unsupported),
    )


@dataclass
class _AudioScan:
    header: Mapping[str, Any]
    tb: Fraction
    sample_rate: int
    count: int = 0
    first: Fraction | None = None
    end: Fraction | None = None
    packet_first: Fraction | None = None
    missing: int = 0
    jumps: int = 0
    skip: int = 0
    discard: int = 0

    def frame(self, row: Mapping[str, str]) -> None:
        samples = int(row.get("nb_samples", "0"))
        pts = _fraction(row.get("pts"))
        if samples <= 0:
            raise fail("AUDIO_FORMAT", "解码音频样本数无效")
        if pts is None:
            self.missing += 1
        else:
            current = pts * self.tb
            if self.first is None:
                self.first = current
            if self.end is not None and abs(current - self.end) > max(
                2 * self.tb, Fraction(1, self.sample_rate)
            ):
                self.jumps += 1
            self.end = current + Fraction(samples, self.sample_rate)
        self.count += samples

    def packet(self, row: Mapping[str, str]) -> None:
        pts = _fraction(row.get("pts"))
        if pts is not None and self.packet_first is None:
            self.packet_first = pts * self.tb
        self.skip = max(self.skip, int(row.get("skip_samples", "0")))
        self.discard = max(self.discard, int(row.get("discard_padding", "0")))

    def result(self, errors: bool) -> AudioObservation:
        h = self.header
        return AudioObservation(
            stream_index=int(h["index"]),
            codec=str(h.get("codec_name", "unknown")),
            sample_rate=self.sample_rate,
            channels=int(h.get("channels", 0)),
            channel_layout=str(h.get("channel_layout", "unknown")),
            language=str(h.get("tags", {}).get("language", "und")),
            profile=str(h.get("profile", "unknown")),
            title=str(h.get("tags", {}).get("title", "")),
            default=bool(h.get("disposition", {}).get("default", 0)),
            forced=bool(h.get("disposition", {}).get("forced", 0)),
            sample_count=self.count,
            start_time=None if self.first is None else rational(self.first),
            end_time=None if self.end is None else rational(self.end),
            first_packet_time=None if self.packet_first is None else rational(self.packet_first),
            skip_samples=self.skip,
            discard_padding=self.discard,
            missing_pts=self.missing,
            discontinuities=self.jumps,
            decode_errors=errors,
        )


def _observe(
    path: Path,
    header: Mapping[str, Any],
    target: str | None,
    progress: ProgressReporter | None,
) -> tuple[WorkVideoObservation, ObservedSignal, tuple[AudioObservation, ...]]:
    streams = header["streams"]
    video = next(h for h in streams if h["codec_type"] == "video")
    tb = Fraction(video["time_base"])
    rate = None if target is None else Fraction(target)
    audios = {
        int(h["index"]): _AudioScan(h, Fraction(h["time_base"]), int(h["sample_rate"]))
        for h in streams
        if h["codec_type"] == "audio"
    }
    variants: dict[tuple[object, ...], FrameVariant] = {}
    count = packet_count = missing = duplicate = backward = differences = 0
    first: Fraction | None = None
    previous: Fraction | None = None
    maximum = Fraction(0)
    hdr = False

    def consume(raw: bytes) -> None:
        nonlocal \
            count, \
            packet_count, \
            missing, \
            duplicate, \
            backward, \
            differences, \
            first, \
            previous, \
            maximum, \
            hdr
        kind = raw.split(b"|", 1)[0].strip()
        row = _row(raw)
        if not row or "stream_index" not in row:
            return
        index = int(row["stream_index"])
        if kind == b"packet":
            if index == video["index"]:
                packet_count += 1
            elif index in audios:
                audios[index].packet(row)
            return
        if kind != b"frame":
            return
        if index in audios:
            audios[index].frame(row)
            return
        if index != video["index"]:
            return
        pts, best = _fraction(row.get("pts")), _fraction(row.get("best_effort_timestamp"))
        differences += pts != best
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
        color = _signal(row)
        shape = (
            int(row["width"]),
            int(row["height"]),
            row["pix_fmt"],
            row.get("sample_aspect_ratio", "unknown"),
            row.get("interlaced_frame") != "0",
        )
        key = (*shape, *color.model_dump().values())
        if key in variants:
            v = variants[key]
            variants[key] = v.model_copy(update={"count": v.count + 1})
        else:
            if len(variants) >= 16:
                raise fail(
                    "OBSERVATION_BUDGET", "全片属性变化超过有限报告预算，不能形成可靠工作参考"
                )
            variants[key] = FrameVariant(
                width=int(row["width"]),
                height=int(row["height"]),
                pixel_format=row["pix_fmt"],
                sample_aspect_ratio=str(shape[3]),
                interlaced=bool(shape[4]),
                signal=color,
                count=1,
            )
        hdr |= _hdr(row)
        count += 1
        sample(count)

    entries = (
        "frame=media_type,stream_index,pts,best_effort_timestamp,width,height,pix_fmt,"
        "sample_aspect_ratio,interlaced_frame,color_range,color_space,color_transfer,color_primaries,"
        "chroma_location,nb_samples:frame_side_data=side_data_type:"
        "packet=codec_type,stream_index,pts:packet_side_data=side_data_type,skip_samples,discard_padding"
    )
    with stage("video_scan", "frames"):
        errors = stream_process(
            [
                *probe_argv(path),
                "-show_frames",
                "-show_packets",
                "-show_entries",
                entries,
                "-of",
                "compact=p=1:nk=0",
                str(path),
            ],
            consume=consume,
            progress=progress,
            permit_media_errors=True,
        )
    rotation = next(
        (str(v["rotation"]) for v in video.get("side_data_list", []) if "rotation" in v), "0"
    )
    observed = WorkVideoObservation(
        stream_index=int(video["index"]),
        codec=video["codec_name"],
        width=video["width"],
        height=video["height"],
        pixel_format=video["pix_fmt"],
        sample_aspect_ratio=video.get("sample_aspect_ratio", "unknown"),
        field_order=video.get("field_order", "unknown"),
        rotation=rotation,
        r_frame_rate=video.get("r_frame_rate", "0/0"),
        avg_frame_rate=video.get("avg_frame_rate", "0/0"),
        time_base=rational(tb),
        frame_count=count,
        packet_count=packet_count,
        first_pts=None if first is None else rational(first),
        last_pts=None if previous is None else rational(previous),
        missing_pts=missing,
        duplicate_pts=duplicate,
        backward_pts=backward,
        best_effort_differences=differences,
        max_clock_error=None if rate is None else rational(maximum),
        decode_errors=errors,
    )
    return (
        observed,
        signal_observation(video, tuple(variants.values()), hdr=hdr),
        tuple(a.result(errors) for a in audios.values()),
    )


def findings_for(
    video: WorkVideoObservation,
    signal: ObservedSignal,
    audio: tuple[AudioObservation, ...],
    target: str | None,
) -> tuple[Decision, tuple[Finding, ...], tuple[Finding, ...]]:
    """时钟不兼容与处理能力限制分开；音频原有尾差只是观察，不伪称损坏。"""
    findings: list[Finding] = []
    warnings: list[Finding] = []
    unsupported = False

    def add(code: str, message: str, *, blocking: bool = True) -> None:
        nonlocal unsupported
        findings.append(Finding(code=f"E_SOURCE_PREPARATION_{code}", message=message))
        unsupported |= blocking

    if target is None:
        add(
            "RATE_SELECTION_REQUIRED",
            "请明确选择精确帧率解释；不会从近似小数猜常见帧率",
            blocking=False,
        )
    if video.decode_errors or any(a.decode_errors for a in audio):
        add("DECODE_ERRORS", "完整扫描出现解码错误；当前内置工作准备不自动修复载荷")
    if video.frame_count < 2:
        add("FRAME_COUNT", "工作参考至少需要两个有效展示帧")
    if (
        video.missing_pts
        or video.duplicate_pts
        or video.backward_pts
        or video.best_effort_differences
    ):
        add("PTS_MAPPING_UNSUPPORTED", "原时间包含缺失、重复、逆序或重建，当前不自动决定帧映射")
    if signal.conflicts or signal.changes or signal.unsupported:
        add("COLOR_UNSUPPORTED", "全帧工具观察存在色彩/几何变化、冲突、HDR 或当前不支持值")
    if signal.missing_fields:
        add(
            "COLOR_UNSPECIFIED",
            "工具未声明：" + "、".join(signal.missing_fields) + "；需要明确工作解释",
            blocking=False,
        )
    if video.rotation != "0" or video.sample_aspect_ratio not in {"1:1", "1/1"}:
        add("VIDEO_PROFILE_UNSUPPORTED", "当前处理器仅支持方形像素、零旋转；原件不因此视为损坏")
    for item in audio:
        if (
            item.codec != "aac"
            or item.missing_pts
            or item.discontinuities
            or not item.sample_count
            or item.start_time is None
            or item.end_time is None
        ):
            add(
                "AUDIO_CAPABILITY_UNSUPPORTED",
                "当前普通封装仅支持可连续解释的 AAC 音轨；可导入新的外部工作参考",
            )
            break
        if (
            item.skip_samples
            or item.discard_padding
            or (
                item.first_packet_time is not None
                and Fraction(item.first_packet_time)
                < Fraction(item.start_time) - Fraction(1, item.sample_rate)
            )
        ):
            add(
                "AUDIO_PRIMING_UNSUPPORTED",
                "AAC 编码延迟/填充组合尚未实现有效样本封装支持；不表示原件损坏",
            )
            break
        if target and video.first_pts is not None:
            end = Fraction(video.first_pts) + video.frame_count / Fraction(target)
            delta = Fraction(item.end_time) - end
            if abs(delta) > Fraction(2, 1000):
                warnings.append(
                    Finding(
                        code="E_SOURCE_PREPARATION_AUDIO_TAIL_OBSERVED",
                        message=(
                            f"所选音轨相对工作视频尾差为 {rational(delta)} 秒；"
                            "保留原样，不自动裁剪或补静音"
                        ),
                    )
                )
    retime = False
    if target and video.max_clock_error is not None:
        tolerance = clock_tolerance(Fraction(target), Fraction(video.time_base))
        # 量化误差只能解释细时间基；不借用 T1 四分之一帧门槛限制普通显式重定时。
        retime = Fraction(video.max_clock_error) > tolerance
        if retime:
            add(
                "CLOCK_NOT_CFR",
                "需要明确保留解码帧数/顺序并重新定时；这可能改变原播放节奏",
                blocking=False,
            )
    status: Decision = (
        "unsupported" if unsupported else "preparation_required" if retime else "direct"
    )
    return status, tuple(findings), tuple(warnings)


def inspect_source(
    path: Path,
    artifact_id: str,
    *,
    target_frame_rate: str | None = None,
    progress: ProgressReporter | None = None,
) -> WorkDiagnosticReport:
    """一次全流解码聚合所有必需观察；头检/版本读取不是额外全片扫描。"""
    DiagnosticParameters(target_frame_rate=target_frame_rate)
    started = monotonic()
    path = checked_file(path)
    before = file_stat(path)
    header = read_header(path, progress=progress)
    streams = header["streams"]
    videos = [v for v in streams if v.get("codec_type") == "video"]
    audios = [v for v in streams if v.get("codec_type") == "audio"]
    choices = tuple(
        sorted(
            {
                rational(r)
                for v in videos
                for name in ("r_frame_rate", "avg_frame_rate")
                if (r := _fraction(v.get(name))) is not None and 1 <= r <= 240
            }
        )
    )
    if target_frame_rate is None and len(choices) == 1:
        target_frame_rate = choices[0]
    version = (
        capture_process([resolve_media_tool("ffprobe"), "-version"], progress=progress)
        .decode()
        .splitlines()[0][:512]
    )
    duration = _fraction(header.get("format", {}).get("duration"))
    header_issue = None
    if len(videos) != 1 or len(audios) > 16:
        header_issue = "当前工作处理器需要单一视频和最多 16 个音轨；可选择新的外部工作参考"
    elif (
        videos[0].get("codec_name") not in _CODECS
        or videos[0].get("pix_fmt") not in _PIXELS
        or not all(
            isinstance(videos[0].get(k), int)
            and 2 <= videos[0][k] <= 8192
            and videos[0][k] % 2 == 0
            for k in ("width", "height")
        )
    ):
        header_issue = "当前解码/处理格式或几何能力不支持；不表示文件损坏"
    elif any(
        _fraction(v.get("time_base")) is None or Fraction(v["time_base"]) <= 0
        for v in videos + audios
    ):
        header_issue = "无法可靠解释流时间基，需要外部工作参考"
    elif any(int(a.get("sample_rate", 0)) <= 0 or int(a.get("channels", 0)) <= 0 for a in audios):
        header_issue = "无法解释音轨采样率或布局，当前音频处理能力不支持"
    video = None
    signal = None
    audio: tuple[AudioObservation, ...] = ()
    warnings: tuple[Finding, ...] = ()
    status: Decision = "unsupported"
    findings: tuple[Finding, ...]
    if header_issue:
        findings = (Finding(code="E_SOURCE_PREPARATION_PROFILE_UNSUPPORTED", message=header_issue),)
    else:
        video, signal, audio = _observe(path, header, target_frame_rate, progress)
        status, findings, warnings = findings_for(video, signal, audio, target_frame_rate)
    after = file_stat(path)
    if before != after:
        raise fail("SOURCE_CHANGED", "完整检查期间素材发生变化，当前观察作废")
    return WorkDiagnosticReport(
        original_media_artifact_id=artifact_id,
        source_stat=before,
        final_stat=after,
        inspection_scope="header_only" if header_issue else "frames_eof",
        status=status,
        ffprobe_version=version,
        container=str(header.get("format", {}).get("format_name", "")),
        container_duration=None if duration is None else rational(duration),
        extra_streams=len(streams) - len(videos) - len(audios),
        chapter_count=len(header.get("chapters", [])),
        target_frame_rate=target_frame_rate,
        target_frame_rate_choices=choices,
        video=video,
        observed_signal=signal,
        audio=audio,
        findings=findings,
        warnings=warnings,
        candidate_strategies=("frame-retime-ffv1/1",) if status == "preparation_required" else (),
        elapsed_seconds=monotonic() - started,
    )
