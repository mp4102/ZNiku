"""新色彩版本的完整保内容准备：允许保留已证明未指定，不添加工作解释标签。"""

from __future__ import annotations

import json
import shutil
from fractions import Fraction
from pathlib import Path
from typing import Any

from zniku.runtime.progress import ProgressReporter
from zniku.source_preparation.inspection import checked_file, file_stat
from zniku.source_preparation.preservation import _audio_content, _video_content, resolve_mkvmerge
from zniku.source_preparation.process import capture_process, fail, stream_process
from zniku.source_preparation.progress import stage

from .inspection import inspect_source
from .models import DiagnosticReport
from .policy import assert_preserved_signal


def verify_preservation(
    original: Path,
    candidate: Path,
    report: DiagnosticReport,
    *,
    target_frame_rate: str,
    progress: ProgressReporter | None = None,
) -> DiagnosticReport:
    """像素、音频、实际声明和时钟分别通过；工作解释从不参与等价比较。"""
    if report.target_frame_rate != target_frame_rate:
        raise fail("INPUT_BINDING", "修复目标率与完整诊断不同")
    original, candidate = checked_file(original), checked_file(candidate)
    before, candidate_before = file_stat(original), file_stat(candidate)
    if before != report.final_stat or original.samefile(candidate):
        raise fail("SOURCE_CHANGED", "原件改变或候选与原件同一文件")
    fresh = inspect_source(
        candidate,
        report.original_media_artifact_id,
        target_frame_rate=target_frame_rate,
        progress=progress,
    )
    blocking = [
        issue for issue in fresh.findings if issue.code != "E_SOURCE_PREPARATION_COLOR_UNSPECIFIED"
    ]
    if blocking:
        raise fail("CANDIDATE_ADMISSION", blocking[0].message)
    assert_preserved_signal(report.observed_signal, fresh.observed_signal)
    properties = (
        "codec",
        "width",
        "height",
        "pixel_format",
        "sample_aspect_ratio",
        "field_order",
        "rotation",
        "hdr_side_data",
        "frame_count",
    )
    if any(getattr(report.video, key) != getattr(fresh.video, key) for key in properties):
        raise fail("VIDEO_CONTENT_CHANGED", "候选改变视频属性或完整帧数")
    with stage("video_compare", "frames"):
        old_pixels = _video_content(original, progress)
    with stage("video_compare", "frames"):
        new_pixels = _video_content(candidate, progress)
    if old_pixels != new_pixels or old_pixels[0] != report.video.frame_count:
        raise fail("VIDEO_CONTENT_CHANGED", "完整展示序或原始解码像素变化")
    if len(report.audio) != len(fresh.audio):
        raise fail("AUDIO_CONTENT_CHANGED", "候选音轨数变化")
    assert report.video.first_pts is not None and fresh.video.first_pts is not None
    for old, new in zip(report.audio, fresh.audio, strict=True):
        keys = (
            "codec",
            "sample_rate",
            "channels",
            "channel_layout",
            "language",
            "sample_count",
            "profile",
            "title",
            "default",
            "forced",
        )
        with stage("audio_compare"):
            content_equal = _audio_content(original, old.stream_index, progress) == _audio_content(
                candidate, new.stream_index, progress
            )
        if not content_equal or any(getattr(old, key) != getattr(new, key) for key in keys):
            raise fail("AUDIO_CONTENT_CHANGED", "音频样本或明确属性变化")
        for key in ("start_time", "end_time"):
            left, right = getattr(old, key), getattr(new, key)
            if (
                left is None
                or right is None
                or abs(
                    Fraction(left)
                    - Fraction(report.video.first_pts)
                    - Fraction(right)
                    + Fraction(fresh.video.first_pts)
                )
                > Fraction(2, 1000)
            ):
                raise fail("AUDIO_ALIGNMENT", "修复改变了音频相对视频的首尾关系")
    if file_stat(original) != before or file_stat(candidate) != candidate_before:
        raise fail("SOURCE_CHANGED", "比较期间输入变化")
    return fresh


def create_t1_candidate(
    original: Path,
    destination: Path,
    report: DiagnosticReport,
    *,
    target_frame_rate: str,
    progress: ProgressReporter | None = None,
) -> None:
    """仅生成时钟候选，SPS/容器色彩原样交给后续完整比较，不写任何猜测标签。"""
    if (
        report.candidate_strategies != ("t1-clock-quantization/2",)
        or report.target_frame_rate != target_frame_rate
    ):
        raise fail("STRATEGY_NOT_APPLICABLE", "新色彩 T1 前提不满足")
    original = checked_file(original)
    if file_stat(original) != report.final_stat:
        raise fail("SOURCE_CHANGED", "原件改变")
    if (
        destination.exists()
        or destination.suffix.lower() != ".mkv"
        or not destination.parent.is_dir()
        or destination.parent.is_symlink()
        or destination.parent.is_junction()
    ):
        raise fail("OUTPUT_CONFLICT", "候选必须写入新建 attempt 的新 MKV 路径")
    if (
        shutil.disk_usage(destination.parent).free
        < int(report.source_stat.size * 1.1) + 16 * 1024**2
    ):
        raise fail("SPACE_INSUFFICIENT", "候选空间不足")
    tool = resolve_mkvmerge()
    header: Any = json.loads(capture_process([tool, "-J", str(original)], progress=progress))
    tracks = [item for item in header.get("tracks", []) if item.get("type") == "video"]
    if len(tracks) != 1 or type(tracks[0].get("id")) is not int:
        raise fail("TRACK_MAPPING", "无法读取实际 MKVToolNix 视频轨 ID")
    stream_process(
        [
            tool,
            "--output-charset",
            "UTF-8",
            "--abort-on-warnings",
            "--quiet",
            "-o",
            str(destination),
            "--default-duration",
            f"{tracks[0]['id']}:{target_frame_rate}fps",
            str(original),
        ],
        consume=lambda _: None,
        progress=progress,
    )
    if file_stat(original) != report.final_stat:
        raise fail("SOURCE_CHANGED", "候选生成期间原件变化")
