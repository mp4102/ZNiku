"""流式验证本扩展要求的源对齐 CFR 时间轴与音轨相对起点。

只读已绑定的直接文件，不生成媒体、不修改时间戳、不比较像素内容。逐帧扫描是本节点
明确的媒体合同，不提升为 Core 全局准入；有限行缓冲与超时避免长片积累帧账本。
"""

from __future__ import annotations

import subprocess
import threading
from collections import deque
from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from queue import Empty, Queue
from time import monotonic
from typing import BinaryIO

from zniku.avenhance_v27.probe import Av27MediaError, probe_header
from zniku.chapter_overlap.media_io import _pump
from zniku.media.probe import resolve_media_tool
from zniku.runtime.process_window import background_creation_flags
from zniku.runtime.progress import ProgressReporter


def _error(message: str) -> Av27MediaError:
    return Av27MediaError("E_SOURCE_ALIGNED_TIMELINE", message)


def _rows(
    path: Path,
    *,
    stream: str,
    entries: str,
    frames: bool,
    first: bool = False,
    progress: ProgressReporter | None = None,
) -> Generator[dict[str, str], None, None]:
    """调用受控 FFprobe；不把全片 JSON 或无限 stderr 留在内存。"""
    argv = [resolve_media_tool("ffprobe"), "-v", "error", "-select_streams", stream]
    if first:
        argv.extend(["-read_intervals", "%+#64" if frames else "%+#1"])
    argv.extend(
        [
            "-show_frames" if frames else "-show_packets",
            "-show_entries",
            entries,
            "-of",
            "compact=p=0:nk=0",
            "--",
            str(path),
        ]
    )
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=background_creation_flags(),
    )
    assert process.stdout is not None and process.stderr is not None
    stop = threading.Event()
    queue: Queue[bytes | Exception | None] = Queue(maxsize=64)
    errors: deque[bytes] = deque(maxlen=2)

    def drain(stream: BinaryIO) -> None:
        with stream:
            while chunk := stream.read(4096):
                errors.append(chunk)

    stdout_reader = threading.Thread(target=_pump, args=(process.stdout, queue, stop), daemon=True)
    stderr_reader = threading.Thread(target=drain, args=(process.stderr,), daemon=True)
    stdout_reader.start()
    stderr_reader.start()
    deadline, heartbeat = monotonic() + 3600, 0.0
    try:
        while True:
            now = monotonic()
            if now >= deadline:
                raise _error("FFprobe 时间轴读取超过一小时预算")
            if progress is not None and now >= heartbeat:
                # 验证期间只有可取消心跳，不把读入帧数伪装成媒体处理进度。
                progress.report(0.0)
                heartbeat = now + 0.25
            try:
                raw = queue.get(timeout=0.2)
            except Empty:
                continue
            if raw is None:
                break
            if isinstance(raw, Exception):
                raise _error("FFprobe 时间轴输出超出预算或读取失败") from raw
            line = raw.decode("utf-8", errors="strict").strip()
            if line:
                yield dict(field.split("=", 1) for field in line.split("|") if "=" in field)
        if process.wait(timeout=30) != 0:
            raise _error("FFprobe 时间轴读取失败")
        stderr_reader.join(timeout=2)
        if errors:
            raise _error("FFprobe 报告媒体解码错误")
    finally:
        stop.set()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=30)
        stdout_reader.join(timeout=2)
        stderr_reader.join(timeout=2)
        process.stdout.close()


@dataclass(frozen=True, slots=True)
class CfrTimeline:
    """仅保存已验证帧数、首展示时刻与量化精度，不是内容逐帧证明。"""

    frame_count: int
    first_pts: Fraction
    time_base: Fraction


def check_frame_timestamps(
    timestamps: Iterable[int], *, count: int, rate: Fraction, time_base: Fraction
) -> CfrTimeline:
    """逐帧核对相对 i/FPS，允许容器量化但不允许累积漂移、重复或逆序。"""
    if type(count) is not int or count < 1 or rate <= 0 or time_base <= 0:
        raise _error("N/FPS/time_base 必须为正")
    period = 1 / rate
    tolerance = max(2 * time_base, Fraction(1, 1_000_000))
    if tolerance >= period / 4:
        raise _error("容器时间基太粗，无法验证精确 CFR")
    first: Fraction | None = None
    previous: Fraction | None = None
    measured = 0
    for index, timestamp in enumerate(timestamps):
        if type(timestamp) is not int:
            raise _error("每个解码帧必须有整数展示时间戳")
        actual = timestamp * time_base
        first = actual if first is None else first
        if index >= count:
            raise _error("实际解码帧数多于原片 N")
        if previous is not None and actual <= previous:
            raise _error("展示时间轴重复或逆序")
        if abs(actual - first - index / rate) > tolerance:
            raise _error("展示时间轴不满足原片精确 CFR，禁止自动改速或补丢帧")
        previous = actual
        measured += 1
    if measured != count or first is None:
        raise _error("实际解码帧数少于原片 N")
    return CfrTimeline(measured, first, time_base)


def probe_cfr(
    path: Path, *, count: int, rate: Fraction, progress: ProgressReporter | None = None
) -> CfrTimeline:
    """不采用 nb_frames 快路径；从完整解码展示序列验证 N 和相对时刻。"""
    header = probe_header(path)
    if header.video.frame_count is not None and header.video.frame_count != count:
        raise _error("header 帧数与原片 N 不符")

    def timestamps() -> Iterator[int]:
        for row in _rows(
            path,
            stream="v:0",
            entries="frame=best_effort_timestamp",
            frames=True,
            progress=progress,
        ):
            value = row.get("best_effort_timestamp")
            if value is None or value == "N/A":
                raise _error("解码帧缺少展示时间戳")
            try:
                yield int(value)
            except ValueError as exc:
                raise _error("非法展示时间戳") from exc

    return check_frame_timestamps(
        timestamps(), count=count, rate=rate, time_base=header.video.time_base
    )


def _first_time(path: Path, *, stream: str, video: bool) -> Fraction:
    key = "best_effort_timestamp_time" if video else "pts_time"
    rows = _rows(
        path,
        stream=stream,
        entries=f"{'frame' if video else 'packet'}={key}",
        frames=video,
        first=True,
    )
    try:
        for row in rows:
            value = row.get(key)
            if value is not None and value != "N/A":
                try:
                    return Fraction(value)
                except ValueError as exc:
                    raise _error("首时间戳无效") from exc
        raise _error("无法取得首视频帧或音频 packet 时间戳")
    finally:
        rows.close()


@dataclass(frozen=True, slots=True)
class AudioOrigins:
    """音轨相对原片首视频帧的 packet 起点，包含容器表达的编码预滚。"""

    video_start: Fraction
    offsets: tuple[Fraction, ...]
    decoded_offsets: tuple[Fraction, ...]


def probe_audio_origins(path: Path) -> AudioOrigins:
    header = probe_header(path)
    video_start = _first_time(path, stream="v:0", video=True)
    return AudioOrigins(
        video_start,
        tuple(
            _first_time(path, stream=f"a:{ordinal}", video=False) - video_start
            for ordinal in range(len(header.audios))
        ),
        tuple(
            _first_time(path, stream=f"a:{ordinal}", video=True) - video_start
            for ordinal in range(len(header.audios))
        ),
    )


def verify_audio_origins(source: Path, output: Path) -> None:
    """检查 stream-copy 后各音轨相对首视频帧的起点，拒绝静默改变同步偏移。"""
    expected, actual = probe_audio_origins(source), probe_audio_origins(output)
    if len(expected.offsets) != len(actual.offsets) or any(
        abs(left - right) > Fraction(2, 1000)
        for expected_values, actual_values in (
            (expected.offsets, actual.offsets),
            (expected.decoded_offsets, actual.decoded_offsets),
        )
        for left, right in zip(expected_values, actual_values, strict=True)
    ):
        raise _error("Final 原音轨相对视频起点变化，禁止以相同音轨格式冒充同步")


def require_supported_audio_origins(source: Path) -> AudioOrigins:
    """首版不静默丢弃 MP4 edit-list/skip-samples；不支持的预滚在写成片前拒绝。"""
    origins = probe_audio_origins(source)
    if any(
        abs(packet - decoded) > Fraction(2, 1000)
        for packet, decoded in zip(origins.offsets, origins.decoded_offsets, strict=True)
    ):
        raise Av27MediaError(
            "E_SOURCE_ALIGNED_AUDIO_PRIMING_UNSUPPORTED",
            "原音轨含编码预滚或 edit-list 样本裁剪，当前 MKV 包复制不能保证保持；"
            "原片和处理产物保留，不会自动裁音频、移位或重新编码",
        )
    return origins


def offset_decimal(value: Fraction) -> str:
    """FFmpeg itsoffset 的微秒精度文本；容器时间基量化在 Final 独立校验。"""
    sign = "-" if value < 0 else ""
    value = abs(value)
    micros = (value.numerator * 1_000_000 + value.denominator // 2) // value.denominator
    return f"{sign}{micros // 1_000_000}.{micros % 1_000_000:06d}"


def same_audio_signatures(
    left: Sequence[Mapping[str, object]], right: Sequence[Mapping[str, object]]
) -> bool:
    """ISO BMFF 的 und 与 Matroska 省略语言都表示未定义；其他音轨属性仍精确比较。"""

    def normalized(signature: Mapping[str, object]) -> dict[str, object]:
        result = dict(signature)
        if result.get("language") == "und":
            result["language"] = None
        return result

    return [normalized(item) for item in left] == [normalized(item) for item in right]


def verify_video_span(path: Path, *, count: int, rate: Fraction) -> None:
    """Final 已知为单次编码视频的包复制，独立视频 span 不受长音轨容器时长干扰。"""
    time_base = probe_header(path).video.time_base
    first: int | None = None
    last: int | None = None
    measured = 0
    for row in _rows(path, stream="v:0", entries="packet=pts", frames=False):
        try:
            pts = int(row["pts"])
        except (KeyError, ValueError) as exc:
            raise _error("Final 视频 packet 缺失 PTS") from exc
        first = pts if first is None else min(first, pts)
        last = pts if last is None else max(last, pts)
        measured += 1
        if measured > count:
            raise _error("Final 视频实际包数超过已验证 Program N")
    if (
        measured != count
        or first is None
        or last is None
        or (abs((last - first) * time_base - (count - 1) / rate) > 2 * time_base)
    ):
        raise _error("Final 视频包数/展示跨度与 Program 不一致")
