"""实现窄 T1 候选和完整保内容比较；未验证候选始终停留在当前 attempt。

逐帧摘要仅用于本节点的明确保内容合同，不是 Core checksum authority。
默认未晋级策略在公开 adapter 关闭；这里保留可独立合成测试的真实实现。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from fractions import Fraction
from pathlib import Path
from typing import Any

from zniku.media.probe import resolve_media_tool
from zniku.runtime.progress import ProgressReporter

from .inspection import checked_file, file_stat, inspect_source
from .models import DiagnosticReport
from .process import capture_process, fail, stream_process
from .progress import sample, stage


def resolve_mkvmerge() -> str:
    """复用 PATH 或 Windows 正式安装目录；不接受 Graph 指定可执行路径。"""
    found = shutil.which("mkvmerge")
    if found is not None:
        return found
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / "MKVToolNix" / "mkvmerge.exe"
            if candidate.is_file():
                return str(candidate)
    raise fail("TOOL_UNAVAILABLE", "缺少 MKVToolNix；安装后重试，原件与输出均未改动")


def _ffmpeg_input(path: Path) -> list[str]:
    return [
        resolve_media_tool("ffmpeg"),
        "-v",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov,matroska",
        "-f",
        "matroska" if path.suffix.lower() == ".mkv" else "mov",
        "-i",
        str(path),
    ]


def _video_content(path: Path, progress: ProgressReporter | None) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0

    def consume(line: bytes) -> None:
        nonlocal count
        if not line.strip() or line.startswith(b"#"):
            return
        fields = line.strip().split(b",")
        if len(fields) != 6:
            raise fail("VERIFY_OUTPUT", "逐帧比较输出格式无效")
        # 丢弃源时钟字段，仅比较展示序、尺寸和原始像素；时间保持另外验证。
        digest.update(fields[4].strip() + b":" + fields[5].strip() + b"\n")
        count += 1
        sample(count)

    stream_process(
        [
            *_ffmpeg_input(path),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            "settb=AVTB,setpts=N",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "rawvideo",
            "-f",
            "framehash",
            "-hash",
            "sha256",
            "-",
        ],
        consume=consume,
        progress=progress,
    )
    return count, digest.hexdigest()


def _audio_content(path: Path, index: int, progress: ProgressReporter | None) -> str:
    result = capture_process(
        [
            *_ffmpeg_input(path),
            "-map",
            f"0:{index}",
            "-vn",
            "-sn",
            "-dn",
            "-c:a",
            "pcm_f32le",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ],
        progress=progress,
        timeout=43200,
    )
    text = result.decode("ascii").strip()
    if not text.startswith("SHA256=") or len(text) != 71:
        raise fail("VERIFY_OUTPUT", "音频内容比较输出无效")
    return text


def verify_preservation(
    original: Path,
    candidate: Path,
    report: DiagnosticReport,
    *,
    target_frame_rate: str,
    progress: ProgressReporter | None = None,
) -> DiagnosticReport:
    """分别证明展示内容、音频内容、属性及参考时钟；任何一项失败均不放行。"""
    if report.target_frame_rate != target_frame_rate:
        raise fail("INPUT_BINDING", "修复目标帧率必须与当前完整诊断一致，改变后需要重新诊断")
    original, candidate = checked_file(original), checked_file(candidate)
    original_stat, candidate_stat = file_stat(original), file_stat(candidate)
    if original_stat != report.final_stat:
        raise fail("SOURCE_CHANGED", "原件已改变，不能使用旧诊断")
    if original == candidate or original.samefile(candidate):
        raise fail("CANDIDATE_ALIASED", "修复副本不能是原件或原件硬链接")
    fresh = inspect_source(
        candidate,
        report.original_media_artifact_id,
        target_frame_rate=target_frame_rate,
        progress=progress,
    )
    if fresh.findings:
        raise fail("CANDIDATE_ADMISSION", fresh.findings[0].message)
    properties = (
        "codec",
        "width",
        "height",
        "pixel_format",
        "sample_aspect_ratio",
        "field_order",
        "color_range",
        "color_space",
        "color_transfer",
        "color_primaries",
        "chroma_location",
        "rotation",
        "hdr_side_data",
        "frame_count",
    )
    if any(getattr(report.video, key) != getattr(fresh.video, key) for key in properties):
        raise fail("VIDEO_CONTENT_CHANGED", "视频属性或帧数发生变化，不符合保内容修复")
    with stage("video_compare", "frames"):
        source_content = _video_content(original, progress)
    with stage("video_compare", "frames"):
        repaired_content = _video_content(candidate, progress)
    if source_content != repaired_content or source_content[0] != report.video.frame_count:
        raise fail("VIDEO_CONTENT_CHANGED", "展示顺序或完整解码像素不同，不能称为等价修复")
    if len(report.audio) != len(fresh.audio):
        raise fail("AUDIO_CONTENT_CHANGED", "音轨数量变化")
    assert report.video.first_pts is not None and fresh.video.first_pts is not None
    for source, target in zip(report.audio, fresh.audio, strict=True):
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
            equal_content = _audio_content(original, source.stream_index, progress) == (
                _audio_content(candidate, target.stream_index, progress)
            )
        if any(getattr(source, key) != getattr(target, key) for key in keys) or not equal_content:
            raise fail("AUDIO_CONTENT_CHANGED", "音频有效解码样本或属性发生变化")
        for key in ("start_time", "end_time"):
            old, new = getattr(source, key), getattr(target, key)
            if (
                old is None
                or new is None
                or abs(
                    Fraction(old)
                    - Fraction(report.video.first_pts)
                    - Fraction(new)
                    + Fraction(fresh.video.first_pts)
                )
                > Fraction(2, 1000)
            ):
                raise fail("AUDIO_ALIGNMENT", "修复改变了音轨相对视频的首尾关系")
    if file_stat(original) != original_stat or file_stat(candidate) != candidate_stat:
        raise fail("SOURCE_CHANGED", "内容验证期间文件变化，结果作废")
    return fresh


def create_t1_candidate(
    original: Path,
    destination: Path,
    report: DiagnosticReport,
    *,
    target_frame_rate: str,
    progress: ProgressReporter | None = None,
) -> None:
    """生成窄时钟候选；函数本身不宣称通过、也不登记 Artifact。"""
    if report.candidate_strategies != ("t1-clock-quantization/1",) or (
        report.target_frame_rate != target_frame_rate
    ):
        raise fail("STRATEGY_NOT_APPLICABLE", "完整诊断不满足 T1 逐帧时钟前提")
    original = checked_file(original)
    if file_stat(original) != report.final_stat:
        raise fail("SOURCE_CHANGED", "原件已改变")
    if destination.exists() or destination.suffix.lower() != ".mkv":
        raise fail("OUTPUT_CONFLICT", "候选输出必须是新的 MKV 路径")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise fail("OUTPUT_PATH", "候选目录必须是当前 attempt 内已创建目录")
    if (
        shutil.disk_usage(destination.parent).free
        < int(report.source_stat.size * 1.1) + 16 * 1024**2
    ):
        raise fail("SPACE_INSUFFICIENT", "工作盘不足以保存候选及验证报告，原件不改")
    tool = resolve_mkvmerge()
    data: Any = json.loads(capture_process([tool, "-J", str(original)], progress=progress))
    tracks = [item for item in data.get("tracks", []) if item.get("type") == "video"]
    if len(tracks) != 1 or type(tracks[0].get("id")) is not int:
        raise fail("TRACK_MAPPING", "无法确定实际视频轨 ID")
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
        raise fail("SOURCE_CHANGED", "候选生成期间原件变化，不能验证发布")
