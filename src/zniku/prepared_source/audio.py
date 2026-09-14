"""独立核对 Final 的完整有效音频样本，不要求音频载体中的原视频为 CFR。

有损编解码或 priming 转换不属于本版本；逐轨比较固定浮点解码表示，仅是该 Final
节点的局部保音频约束，既不验证视频像素，也不是 Core 全局摘要 authority。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from zniku.avenhance_v27.probe import probe_header
from zniku.media.probe import resolve_media_tool
from zniku.runtime.progress import ProgressReporter
from zniku.source_preparation.inspection import _scan
from zniku.source_preparation.process import stream_process
from zniku.source_preparation.progress import sample, stage

from .node_contracts import fail


def _audio_digest(path: Path, stream_index: int, progress: ProgressReporter | None) -> bytes:
    """固定 f32le 表示，工具完整退出且无错误后才接受一条有界摘要。"""
    hashes: list[bytes] = []

    def consume(line: bytes) -> None:
        value = line.strip()
        if value.startswith(b"SHA256="):
            if hashes:
                fail("FINAL_AUDIO_CONTENT", "单音轨比较不应返回多个摘要")
            hashes.append(value)
        sample()

    stream_process(
        [
            resolve_media_tool("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            f"0:{stream_index}",
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
        consume=consume,
        progress=progress,
        timeout=43200,
    )
    output = hashes[0] if hashes else b""
    if not output.startswith(b"SHA256=") or len(output) != 71:
        fail("FINAL_AUDIO_CONTENT", "音频解码比较未完整返回规范结果")
    return output


def verify_audio_content(
    source: Path, output: Path, *, progress: ProgressReporter | None = None
) -> None:
    """逐轨验证全部实际解码样本；同格式、同首点但内容或尾部变化也拒绝。"""
    originals, results = probe_header(source).audios, probe_header(output).audios
    if len(originals) != len(results):
        fail("FINAL_AUDIO_CONTENT", "Final 音轨数变化")
    for original, result in zip(originals, results, strict=True):
        with stage("final_original_audio_compare"):
            expected = _audio_digest(source, original.index, progress)
        with stage("final_output_audio_compare"):
            actual = _audio_digest(output, result.index, progress)
        if expected != actual:
            fail("FINAL_AUDIO_CONTENT", "Final 解码音频样本内容或总数发生变化")


def verify_video_span(
    path: Path, *, count: int, rate: Fraction, progress: ProgressReporter | None = None
) -> None:
    """可取消地检查 Final 完整视频包数/跨度；不把容器总时长当视频终点。"""
    time_base = probe_header(path).video.time_base
    first: int | None = None
    last: int | None = None
    measured = 0

    def consume(row: dict[str, str]) -> None:
        nonlocal first, last, measured
        try:
            pts = int(row["pts"])
        except (KeyError, ValueError) as exc:
            raise ValueError("Final 视频包缺少 PTS") from exc
        first = pts if first is None else min(first, pts)
        last = pts if last is None else max(last, pts)
        measured += 1
        sample(measured, count)
        if measured > count:
            fail("FINAL_VIDEO_SPAN", "Final 视频包数超过已验证 Program N")

    with stage("final_video_packets", "packets"):
        _scan(path, "v:0", "packet=pts", False, consume, progress)
    if (
        measured != count
        or first is None
        or last is None
        or (abs((last - first) * time_base - (count - 1) / rate) > 2 * time_base)
    ):
        fail("FINAL_VIDEO_SPAN", "Final 视频包数或展示跨度与 Program 不一致")
