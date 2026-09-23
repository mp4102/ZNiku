"""为重叠分章节点提供受控媒体 I/O，不拥有 Graph 或 Artifact authority。

FFmpeg 只接受内部构造的 argv，进度以实际输出帧计数为准；无输出期间的心跳仍经过
Runtime reporter，因此取消不会被长时间 packet 扫描吞掉。失败保留本 attempt 的部分
文件但绝不返回成功结果，不删除源、外部 raw 或历史产物。ProRes 范围操作只复制包。
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Sequence
from contextlib import suppress
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from queue import Empty, Full, Queue
from time import monotonic
from typing import BinaryIO

from zniku.avenhance_v27.adapters import _encoder_options, _terminate_process
from zniku.avenhance_v27.probe import probe_header, rates_equivalent
from zniku.media.probe import resolve_media_tool
from zniku.media.scratch import record_internal_scratch
from zniku.runtime import PythonAdapterContext
from zniku.runtime.process_window import background_creation_flags

from .mov_index import indexed_mov_fallback_reason


class OverlapMediaError(RuntimeError):
    """本 attempt 的媒体前检、进程或输出事实不满足合同，禁止登记部分产物。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def output_path(context: PythonAdapterContext, target: Path) -> Path:
    """解析新输出到独立 attempt；已有文件及越界路径均在写入前拒绝。"""

    root = context.work_dir.resolve(strict=True)
    resolved = target.resolve()
    if not resolved.is_relative_to(root) or resolved == root or resolved.exists():
        raise OverlapMediaError("E_OVERLAP_OUTPUT_PATH", "输出必须是 attempt 内的新文件")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def source_path(path: Path) -> Path:
    """只读定位显式输入；不遍历工程或寻找未绑定的邻章文件。"""

    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise OverlapMediaError("E_OVERLAP_INPUT_FILE", "输入必须存在且非空")
    return resolved


def capacity_check(
    context: PythonAdapterContext,
    *,
    output_bytes: int,
    staging_bytes: int = 0,
    input_bytes: int = 0,
    input_frames: int = 0,
    output_frames: int = 0,
) -> dict[str, object]:
    """按实际文件字节与区间比例估算新增副本，预留 25% 和 64 MiB。

    这是磁盘容量前检，不是压缩率保证或预分配；并发占用、ENOSPC 仍使本 attempt
    失败且保留部分输出。原始/raw 文件占用不当作可回收空间。
    """

    values = (output_bytes, staging_bytes, input_bytes, input_frames, output_frames)
    if any(type(value) is not int or value < 0 for value in values):
        raise OverlapMediaError("E_OVERLAP_CAPACITY_RANGE", "容量事实必须是非负整数")
    additional = output_bytes + staging_bytes
    required = (additional * 5 + 3) // 4 + 64 * 1024 * 1024
    available = shutil.disk_usage(context.work_dir).free
    summary: dict[str, object] = {
        "input_bytes": input_bytes,
        "input_frames": input_frames,
        "output_frames": output_frames,
        "estimated_output_bytes": output_bytes,
        "estimated_staging_copy_bytes": staging_bytes,
        "estimated_additional_bytes": additional,
        "required_free_bytes": required,
        "available_bytes": available,
        "original_and_raw_retained": True,
        "estimate_not_allocation": True,
    }
    with context.stdout_log_path.open("ab") as stream:
        stream.write(("overlap capacity " + json.dumps(summary) + "\n").encode())
    if available < required:
        raise OverlapMediaError("E_OVERLAP_CAPACITY", "空闲空间不足以保留原件并创建预估副本")
    return summary


def _pump(stream: BinaryIO, queue: Queue[bytes | Exception | None], stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            value = stream.readline(4097)
            if len(value) > 4096:
                raise OverlapMediaError("E_OVERLAP_PROGRESS", "FFmpeg progress 行超出预算")
            item = value if value else None
            while not stop.is_set():
                try:
                    queue.put(item, timeout=0.2)
                    break
                except Full:
                    continue
            if item is None:
                return
    except Exception as error:
        while not stop.is_set():
            try:
                queue.put(error, timeout=0.2)
                return
            except Full:
                continue


def run_ffmpeg(
    context: PythonAdapterContext,
    argv: Sequence[str],
    *,
    expected_frames: int,
    progress_total: int | None = None,
    progress_offset: int = 0,
    timeout_seconds: float = 24 * 60 * 60,
    stage: str | None = None,
) -> int:
    """受控单进程执行，真实帧计数不得超过合同；取消即终止并回收 producer。

    固定队列/单个读线程避免 pipe 阻塞与无界内存。heartbeat 仅重复已测量进度，不预测
    媒体处理速度。FFmpeg 6.1 的 stream-copy 不输出 progress.frame；复制本模块已知
    一包一帧的视频时在退出后读同进程 mux 实测总包数，不把缺失进度当零或填入预期帧数。
    完成仍须调用方及独立 validator 检查输出媒体，不能据进度登记 Artifact。
    """

    total = expected_frames if progress_total is None else progress_total
    if (
        expected_frames < 1
        or progress_offset < 0
        or total < progress_offset + expected_frames
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or not argv
        or len(argv) > 256
        or (sys.platform == "win32" and len(subprocess.list2cmdline(list(argv))) > 30000)
    ):
        raise OverlapMediaError("E_OVERLAP_PROCESS_BOUND", "进程/进度超出明确预算")
    process: subprocess.Popen[bytes] | None = None
    thread: threading.Thread | None = None
    stop = threading.Event()
    queue: Queue[bytes | Exception | None] = Queue(maxsize=256)
    measured = 0
    started = monotonic()
    copy_packets = any(option == "-c:v" and value == "copy" for option, value in pairwise(argv))

    def report() -> None:
        if context.progress is not None:
            current = progress_offset + measured
            context.progress.report(
                current / total, current=current, total=total, unit="frames", stage=stage
            )

    try:
        report()
        with (
            context.stderr_log_path.open("ab") as stderr,
            context.stdout_log_path.open("ab") as log,
        ):
            stderr_start = stderr.tell()
            process = subprocess.Popen(
                [
                    resolve_media_tool("ffmpeg"),
                    "-hide_banner",
                    "-nostdin",
                    "-n",
                    "-loglevel",
                    "verbose" if copy_packets else "error",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    *argv,
                ],
                cwd=context.work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                shell=False,
                creationflags=background_creation_flags(),
            )
            assert process.stdout is not None
            thread = threading.Thread(target=_pump, args=(process.stdout, queue, stop), daemon=True)
            thread.start()
            last_report = monotonic()
            pending_frame = 0
            while True:
                if monotonic() - started > timeout_seconds:
                    raise OverlapMediaError(
                        "E_OVERLAP_PROCESS_TIMEOUT", "FFmpeg 超过单 attempt 时限"
                    )
                try:
                    line = queue.get(timeout=0.25)
                except Empty:
                    report()
                    last_report = monotonic()
                    continue
                if isinstance(line, Exception):
                    raise line
                if line is None:
                    break
                key, separator, value = (
                    line.decode("utf-8", errors="replace").strip().partition("=")
                )
                log.write(line)
                if key == "frame" and separator:
                    if not value.isdecimal():
                        raise OverlapMediaError("E_OVERLAP_PROGRESS", "FFmpeg frame 必须为整数")
                    pending_frame = int(value)
                if key == "progress":
                    if not measured <= pending_frame <= expected_frames:
                        raise OverlapMediaError("E_OVERLAP_PROGRESS", "帧计数回退或超出输出合同")
                    measured = pending_frame
                    report()
                    last_report = monotonic()
                elif monotonic() - last_report >= 0.25:
                    report()
                    last_report = monotonic()
            return_code = process.wait(timeout=5)
            if return_code:
                raise OverlapMediaError("E_OVERLAP_FFMPEG_FAILED", f"FFmpeg 退出码 {return_code}")
            if copy_packets:
                # 6.1 连 stats_mux_pre 也仅为编码路径创建，不能拿它补 copy 计数。
                # 保留普通实时 progress；只有没有帧计数的旧工具会在复制完成时才更新 N。
                # 最多读本次 stderr 的末尾256KiB，不重扫媒体、不读取上一次命令的统计。
                actual = _muxed_video_packet_count(context.stderr_log_path, stderr_start)
                if actual < measured:
                    raise OverlapMediaError("E_OVERLAP_PROGRESS", "mux 总包数小于已报告进度")
                measured = actual
            if measured != expected_frames:
                raise OverlapMediaError("E_OVERLAP_FRAME_COUNT", "实际输出帧数与合同不同")
            if copy_packets:
                log.write(f"copy_packets={measured}\n".encode())
            report()
    except BaseException:
        if process is not None:
            _terminate_process(process)
        raise
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=2)
        if process is not None and process.stdout is not None:
            # 读端收尾不证明 producer 已退出；不能覆盖上面的停机未确认或原始媒体故障。
            with suppress(OSError):
                process.stdout.close()
    return measured


def _muxed_video_packet_count(path: Path, start: int) -> int:
    """读取受控单输出视频copy的真实mux总结；缺失、重复和未知形状均失败关闭。"""

    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(start, stream.tell() - 256 * 1024))
        tail = stream.read(256 * 1024)
    matches = re.findall(
        rb"(?:^|\n)(?:\[out#[^\]\r\n]+\]\s+)?\s*"
        rb"Output stream #0:0 \(video\): ([0-9]+) packets muxed(?: |\r|$)",
        tail,
    )
    if len(matches) != 1:
        raise OverlapMediaError("E_OVERLAP_FRAME_COUNT", "缺少唯一的输出视频mux实测包数")
    return int(matches[0])


def clock_filter(rate: Fraction) -> str:
    """全帧内一包一帧媒体的 exact 时钟，包 payload 不改变。"""

    return f"setts=time_base=1/{rate.numerator}:ts=N*{rate.denominator}:duration={rate.denominator}"


def verify_mov(path: Path, rate: Fraction, count: int) -> None:
    """MOV header 数量与 exact FPS 独立检查；不触发全片 decode/hash 门禁。"""

    header = probe_header(path)
    video = header.video
    if (
        video.codec != "prores"
        or video.frame_count != count
        or video.avg_frame_rate != rate
        or video.r_frame_rate != rate
        or header.audios
        or header.others
    ):
        raise OverlapMediaError("E_OVERLAP_COPY_OUTPUT", "ProRes copy 输出的帧数、FPS 或流合同不符")


def _timescale_copy(
    context: PythonAdapterContext,
    source: Path,
    target: Path,
    rate: Fraction,
    count: int,
    *,
    total: int,
    offset: int,
    selector: str | None = None,
    stage: str | None = None,
) -> Path:
    """先复制包到目标 timescale，再单独 setts；规避不同输入时基被二次缩放。

    FFmpeg 8.1 单次 setts 和 muxer rescale 叠加可使时钟成倍偏移。两次 packetcopy
    不解码或编码图像；stage 保留且额外成本计入容量与真实帧进度。
    """
    target = output_path(context, target)
    run_ffmpeg(
        context,
        [
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "copy",
            *([] if selector is None else ["-bsf:v", selector]),
            "-video_track_timescale",
            str(rate.numerator),
            "-f",
            "mov",
            str(target),
        ],
        expected_frames=count,
        progress_total=total,
        progress_offset=offset,
        stage=stage,
    )
    video = probe_header(target).video
    if video.frame_count != count or video.time_base != Fraction(1, rate.numerator):
        raise OverlapMediaError("E_OVERLAP_TIMESCALE_COPY", "timescale stage 数量或时基不符")
    record_internal_scratch(context, target, "timescale")
    return target


def copy_prores_range(
    context: PythonAdapterContext,
    source: Path,
    target: Path,
    start: int,
    end: int,
    rate: Fraction,
    *,
    progress_total: int | None = None,
    progress_offset: int = 0,
    source_frame_count: int | None = None,
    allow_equivalent_rate: bool = False,
    allow_indexed_seek: bool = False,
    stage: str | None = None,
) -> int:
    """复制 source-local 半开区间；已证明的常量 MOV 索引才允许有界 seek。

    快路径定位到首帧前半周期，用原始整数 PTS 再筛选，并以实际输出包数明确停止。
    不可证明索引时保留原包序过滤，不用近似 seek 偷换精确帧语义。
    """

    if not 0 <= start < end or rate <= 0:
        raise OverlapMediaError("E_OVERLAP_COPY_RANGE", "copy 必须是非空合法帧区间")
    source, target = source_path(source), output_path(context, target)
    video = probe_header(source).video
    known_count = video.frame_count if source_frame_count is None else source_frame_count
    if (
        video.codec != "prores"
        or known_count is None
        or end > known_count
        or (video.frame_count is not None and video.frame_count != known_count)
    ):
        raise OverlapMediaError("E_OVERLAP_COPY_SOURCE", "copy 仅支持已知帧数的全帧内 ProRes")
    # 外部已验收 fragmented MOV 可没有 nb_frames；仅由直接 Artifact metadata 提供已验收 N。
    # raw 的等价小数 FPS 在相同外部门槛内接纳，copy 后重建 exact CFR，不改变帧或相位。
    rates = (video.avg_frame_rate, video.r_frame_rate)
    rates_match = (
        all(rates_equivalent(value, rate, tolerance=Fraction(1, 500000)) for value in rates)
        if allow_equivalent_rate
        else all(value == rate for value in rates)
    )
    if not rates_match:
        raise OverlapMediaError("E_OVERLAP_COPY_FPS", "源 exact FPS 与 copy 合同不一致")
    selector = f"noise=amount=0:drop='lt(n,{start})+gte(n,{end})'"
    seek_options: list[str] = []
    stop_options: list[str] = []
    if allow_indexed_seek:
        reason = indexed_mov_fallback_reason(source, rate, known_count)
        if reason is None:
            # 相邻帧中点远离微秒舍入边界；copyts 保留源整数 PTS，过滤不靠浮点。
            seek = Fraction(max(0, 2 * start - 1), 2) / rate
            micros = seek.numerator * 1_000_000 // seek.denominator
            duration = Fraction(4 * end - 1, 4) / rate - Fraction(micros, 1_000_000)
            duration_micros = duration.numerator * 1_000_000 // duration.denominator
            seek_options = [
                "-copyts",
                "-seek_timestamp",
                "1",
                "-ss",
                f"{micros // 1_000_000}.{micros % 1_000_000:06d}",
                "-t",
                f"{duration_micros // 1_000_000}.{duration_micros % 1_000_000:06d}",
            ]
            selector = (
                f"noise=amount=0:drop='lt(pts,{start * rate.denominator})"
                f"+gte(pts,{end * rate.denominator})'"
            )
            stop_options = ["-frames:v", str(end - start)]
        with context.stdout_log_path.open("a", encoding="utf-8") as log:
            log.write(
                "overlap range route: "
                + ("indexed-bounded" if reason is None else "sequential-fallback (" + reason + ")")
                + "\n"
            )
    if video.time_base != Fraction(1, rate.numerator):
        total = (
            progress_total if progress_total is not None else 2 * (end - start) + progress_offset
        )
        source = _timescale_copy(
            context,
            source,
            target.with_name(target.stem + ".timescale.mov"),
            rate,
            end - start,
            total=total,
            offset=progress_offset,
            selector=selector,
            stage=stage,
        )
        progress_offset += end - start
        progress_total = total
        selector = ""
    count = run_ffmpeg(
        context,
        [
            *seek_options,
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "copy",
            "-bsf:v",
            (selector + "," if selector else "") + clock_filter(rate),
            *stop_options,
            "-video_track_timescale",
            str(rate.numerator),
            "-f",
            "mov",
            str(target),
        ],
        expected_frames=end - start,
        progress_total=progress_total,
        progress_offset=progress_offset,
        stage=stage,
    )
    verify_mov(target, rate, count)
    # 只为 known-exact 的 context-parts 记录维护提示；正式输出和未知调用自动拒绝。
    record_internal_scratch(context, target, "context_part")
    return count


def write_ffconcat(context: PythonAdapterContext, paths: Sequence[Path], target: Path) -> Path:
    """最多一万条显式输入，单 concat demuxer 顺序读取；argv 不随章数线性膨胀。"""

    if not 1 <= len(paths) <= 10000:
        raise OverlapMediaError("E_OVERLAP_CONCAT_BOUND", "concat 输入数量超出 1..10000")
    sources = tuple(source_path(path) for path in paths)
    lines = ["ffconcat version 1.0\n"]
    for source in sources:
        value = source.as_posix()
        if any(char in value for char in "\r\n\x00"):
            raise OverlapMediaError("E_OVERLAP_CONCAT_PATH", "concat 路径含控制字符")
        lines.append("file '" + value.replace("'", "'\\''") + "'\n")
    target = output_path(context, target)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.writelines(lines)
    return target


def concat_prores(
    context: PythonAdapterContext,
    paths: Sequence[Path],
    target: Path,
    rate: Fraction,
    expected_frames: int,
    *,
    progress_total: int | None = None,
    progress_offset: int = 0,
    source_frame_counts: Sequence[int] | None = None,
    stage: str | None = None,
) -> int:
    """按普通 ordered_many 输入顺序无损合并；不会启动每章一个 decoder。"""

    target = output_path(context, target)
    if not 1 <= len(paths) <= 10000:
        raise OverlapMediaError("E_OVERLAP_CONCAT_BOUND", "输入数量超出预算")
    headers = tuple(probe_header(path).video for path in paths)
    counts = (
        tuple(header.frame_count for header in headers)
        if source_frame_counts is None
        else tuple(source_frame_counts)
    )
    if len(counts) != len(paths):
        raise OverlapMediaError("E_OVERLAP_CONCAT_COUNT", "输入帧数与路径数量不匹配")
    extra = 0
    for header, count in zip(headers, counts, strict=True):
        if (
            count is None
            or type(count) is not int
            or count < 1
            or (header.frame_count is not None and header.frame_count != count)
        ):
            raise OverlapMediaError("E_OVERLAP_CONCAT_COUNT", "必须有已验收输入帧数")
        if header.avg_frame_rate != rate or header.r_frame_rate != rate:
            raise OverlapMediaError("E_OVERLAP_CONCAT_FPS", "Merge/context 输入必须为 exact FPS")
        if header.time_base != Fraction(1, rate.numerator):
            extra += count
    total = (
        progress_total if progress_total is not None else expected_frames + extra + progress_offset
    )
    normalized: list[Path] = []
    for ordinal, (path, header, count) in enumerate(zip(paths, headers, counts, strict=True)):
        assert count is not None
        if header.time_base != Fraction(1, rate.numerator):
            path = _timescale_copy(
                context,
                path,
                target.parent / f"{target.stem}.input-{ordinal:04d}.mov",
                rate,
                count,
                total=total,
                offset=progress_offset,
                stage=stage,
            )
            progress_offset += count
        normalized.append(path)
    manifest = write_ffconcat(context, normalized, target.with_suffix(".ffconcat"))
    count = run_ffmpeg(
        context,
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "copy",
            "-bsf:v",
            clock_filter(rate),
            "-video_track_timescale",
            str(rate.numerator),
            "-f",
            "mov",
            str(target),
        ],
        expected_frames=expected_frames,
        progress_total=total,
        progress_offset=progress_offset,
        stage=stage,
    )
    verify_mov(target, rate, count)
    return count


def encode_program(
    context: PythonAdapterContext,
    paths: Sequence[Path],
    target: Path,
    rate: Fraction,
    expected_frames: int,
    encoder: str,
) -> int:
    """单次连续编码所有 cropped 章，整段只在全局末尾克隆一帧。

    输入只有一个 ffconcat 清单，故一千章不会变成一千路同时打开的 decoder 或超长
    filter/argv。此函数不做拓扑校验，调用 adapter 必须先通过 preflight 证明区间完整。
    """

    if encoder not in ("cpu", "gpu") or not 1 <= len(paths) <= 1000:
        raise OverlapMediaError("E_OVERLAP_PROGRAM_BOUND", "encoder 或章数超出合同")
    target = output_path(context, target)
    manifest = write_ffconcat(context, paths, target.with_suffix(".ffconcat"))
    pixel_format = "yuv420p10le" if encoder == "cpu" else "p010le"
    video_filter = (
        "setsar=1/1,tpad=stop_mode=clone:stop=1,"
        f"settb=expr=1/{rate.numerator},setpts=N*{rate.denominator},"
        f"format={pixel_format},"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
    return run_ffmpeg(
        context,
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-vf",
            video_filter,
            "-c:v",
            "libx265" if encoder == "cpu" else "hevc_nvenc",
            *_encoder_options(encoder, rate),
            "-fps_mode:v",
            "passthrough",
            "-enc_time_base:v",
            f"1/{rate.numerator}",
            "-color_range:v",
            "tv",
            "-colorspace:v",
            "bt709",
            "-color_trc:v",
            "bt709",
            "-color_primaries:v",
            "bt709",
            "-chroma_sample_location:v",
            "left",
            "-tag:v",
            "hvc1",
            "-video_track_timescale",
            str(rate.numerator),
            "-f",
            "mp4",
            str(target),
        ],
        expected_frames=expected_frames,
    )
