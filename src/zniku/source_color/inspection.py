"""在旧只读时钟/音频观察之上增加全范围色彩事实；旧报告/1 与拒绝语义不变。"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from time import monotonic

from zniku.runtime.progress import ProgressReporter
from zniku.source_preparation.inspection import file_stat
from zniku.source_preparation.inspection import inspect_source as inspect_previous
from zniku.source_preparation.models import DiagnosticParameters, Finding
from zniku.source_preparation.process import fail

from .bitstream import observe_bitstream_signal
from .container import observe_container_signal
from .frames import observe_frame_signals
from .models import DiagnosticReport
from .policy import combine_observations


def inspect_source(
    path: Path,
    artifact_id: str,
    *,
    target_frame_rate: str | None = None,
    progress: ProgressReporter | None = None,
) -> DiagnosticReport:
    """全部观察完成才产生新报告；保内容可研究与工作解释是否齐备分别表示。"""
    DiagnosticParameters(target_frame_rate=target_frame_rate)
    started = monotonic()
    previous = inspect_previous(
        path, artifact_id, target_frame_rate=target_frame_rate, progress=progress
    )
    if previous.video.codec != "h264":
        raise fail("COLOR_CODEC", "新色彩来源观察当前只支持 H.264 实体视频")
    container = observe_container_signal(path)
    bitstream = observe_bitstream_signal(path, progress=progress)
    frames = observe_frame_signals(path, progress=progress)
    observed = combine_observations(container, bitstream, frames)
    if file_stat(path) != previous.final_stat:
        raise fail("SOURCE_CHANGED", "完整色彩观察期间原件变化")
    issues = [
        finding
        for finding in previous.findings
        if finding.code != "E_SOURCE_PREPARATION_VIDEO_PROFILE_UNSUPPORTED"
    ]

    def add(code: str, message: str) -> None:
        issues.append(Finding(code=f"E_SOURCE_PREPARATION_{code}", message=message))

    video = previous.video
    if (
        video.pixel_format != "yuv420p"
        or video.rotation != "0"
        or video.sample_aspect_ratio not in {"1:1", "1/1"}
        or video.field_order != "progressive"
        or video.hdr_side_data
    ):
        add(
            "VIDEO_PROFILE_UNSUPPORTED",
            "只支持方形像素、零旋转、逐行 H.264/yuv420p；色彩解释不能绕过几何或 HDR 限制",
        )
    if any(
        frame.width != video.width
        or frame.height != video.height
        or frame.pixel_format != video.pixel_format
        or frame.sample_aspect_ratio not in {"1:1", "1/1"}
        or frame.interlaced
        for frame in frames.variants
    ):
        add("VIDEO_PROFILE_UNSUPPORTED", "完整展示帧的几何、像素格式或场序与源合同不一致")
    if observed.conflicts:
        add("COLOR_CONFLICT", "容器、全部 SPS 或帧色彩声明冲突：" + ", ".join(observed.conflicts))
    if observed.changes:
        add("COLOR_CHANGED", "全片声明/帧属性出现变化：" + ", ".join(observed.changes))
    if observed.unsupported:
        add(
            "COLOR_UNSUPPORTED",
            "观察包含未支持或不可解析的色彩语义：" + ", ".join(observed.unsupported),
        )
    if observed.missing_fields:
        add(
            "COLOR_UNSPECIFIED",
            "真实声明未指定以下处理信息："
            + ", ".join(observed.missing_fields)
            + "；不等于文件损坏，也不自动补成 BT.709",
        )
    if previous.target_frame_rate is not None and any(
        Fraction(rate) != Fraction(previous.target_frame_rate) for rate in bitstream.clock_rates
    ):
        add("BITSTREAM_CLOCK_CONFLICT", "全片 SPS 固定时钟与目标率冲突")
    candidates: tuple[str, ...] = ()
    codes = {item.code for item in issues}
    if (
        "E_SOURCE_PREPARATION_CLOCK_NOT_CFR" in codes
        and codes
        <= {"E_SOURCE_PREPARATION_CLOCK_NOT_CFR", "E_SOURCE_PREPARATION_COLOR_UNSPECIFIED"}
        and previous.target_frame_rate is not None
        and video.max_clock_error is not None
        and Fraction(video.max_clock_error) < 1 / Fraction(previous.target_frame_rate) / 4
    ):
        candidates = ("t1-clock-quantization/2",)
    values = previous.model_dump(
        exclude={"schema_version", "findings", "candidate_strategies", "elapsed_seconds"}
    )
    return DiagnosticReport.model_validate(
        {
            **values,
            "observed_signal": observed,
            "findings": tuple(issues),
            "candidate_strategies": candidates,
            "elapsed_seconds": monotonic() - started,
        }
    )
