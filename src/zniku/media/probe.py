"""提供 Phase 4 媒体节点使用的轻量 FFprobe 读取。

本模块只读取容器、音视频流和可选精确视频帧数，不计算摘要、不执行 full decode，也不建立
Evidence。所有外部进程均以 argv 数组和 ``shell=False`` 启动；未知、缺失或不可解析的 FFprobe
字段默认失败关闭。
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from zniku.runtime.models import Artifact


class MediaNodeError(RuntimeError):
    """表示媒体节点 adapter/validator 的稳定失败，不携带部分 Artifact。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class VideoStreamInfo:
    """FFprobe 返回的一路视频流轻量信息。"""

    index: int
    codec: str
    width: int
    height: int
    frame_rate: Fraction
    pixel_format: str | None
    frame_count: int | None

    def to_summary(self) -> dict[str, object]:
        """返回可写入普通 Runtime 摘要的 JSON object。"""

        return {
            "index": self.index,
            "codec": self.codec,
            "width": self.width,
            "height": self.height,
            "frame_rate": f"{self.frame_rate.numerator}/{self.frame_rate.denominator}",
            "pixel_format": self.pixel_format,
            "frame_count": self.frame_count,
        }


@dataclass(frozen=True, slots=True)
class AudioStreamInfo:
    """FFprobe 返回的一路音频流轻量信息。"""

    index: int
    codec: str
    sample_rate: int | None
    channels: int | None

    def to_summary(self) -> dict[str, object]:
        """返回可写入普通 Runtime 摘要的 JSON object。"""

        return {
            "index": self.index,
            "codec": self.codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }


@dataclass(frozen=True, slots=True)
class MediaProbeInfo:
    """一个媒体文件的轻量容器与流摘要。"""

    format_name: str
    duration_seconds: float | None
    video_streams: tuple[VideoStreamInfo, ...]
    audio_streams: tuple[AudioStreamInfo, ...]

    def to_summary(self) -> dict[str, object]:
        """返回不包含 digest、命令或文件内容的普通 JSON object。"""

        return {
            "format_name": self.format_name,
            "duration_seconds": self.duration_seconds,
            "video_streams": [stream.to_summary() for stream in self.video_streams],
            "audio_streams": [stream.to_summary() for stream in self.audio_streams],
        }


def resolve_media_tool(name: str) -> str:
    """解析本机受信工具；工具不可用时立即失败关闭。"""

    executable = shutil.which(name)
    if executable is None:
        raise MediaNodeError("E_MEDIA_TOOL_UNAVAILABLE", f"找不到本机工具 {name!r}")
    return executable


def probe_media(
    path: str | Path,
    *,
    count_frames: bool = False,
    ffprobe_executable: str | None = None,
) -> MediaProbeInfo:
    """读取媒体流；``count_frames`` 仅由 Split/Merge 等节点按业务需要启用。"""

    source = _regular_nonempty_file(path)
    executable = ffprobe_executable or resolve_media_tool("ffprobe")
    entries = (
        "format=format_name,duration:"
        "stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,"
        "r_frame_rate,nb_frames,nb_read_frames,sample_rate,channels"
    )
    argv = [executable, "-v", "error"]
    if count_frames:
        argv.append("-count_frames")
    argv.extend(["-show_entries", entries, "-of", "json", str(source)])
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
            timeout=3600 if count_frames else 60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MediaNodeError("E_MEDIA_PROBE_FAILED", str(error)) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip().replace("\n", " ")[-1000:]
        raise MediaNodeError(
            "E_MEDIA_PROBE_FAILED",
            detail or f"FFprobe 退出码为 {completed.returncode}",
        )
    try:
        payload = json.loads(completed.stdout)
        return _parse_probe_payload(payload, count_frames=count_frames)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise MediaNodeError("E_MEDIA_PROBE_INVALID", str(error)) from error


def require_media_kind(info: MediaProbeInfo, kind: str) -> None:
    """按 Core typed port 要求检查声明媒体流。"""

    if kind == "VideoFile" and not info.video_streams:
        raise MediaNodeError("E_MEDIA_VIDEO_STREAM_MISSING", "文件不含视频流")
    if kind == "AudioFile" and not info.audio_streams:
        raise MediaNodeError("E_MEDIA_AUDIO_STREAM_MISSING", "文件不含音频流")
    if kind == "MediaFile" and not (info.video_streams or info.audio_streams):
        raise MediaNodeError("E_MEDIA_STREAM_MISSING", "文件不含可识别的音视频流")
    if kind not in {"MediaFile", "VideoFile", "AudioFile"}:
        raise MediaNodeError("E_MEDIA_KIND_UNKNOWN", f"未知媒体类型 {kind!r}")


def exact_video_frame_count(path: str | Path) -> int:
    """为 Split/Merge 返回第一路视频的精确帧数。"""

    info = probe_media(path, count_frames=True)
    if not info.video_streams:
        raise MediaNodeError("E_MEDIA_VIDEO_STREAM_MISSING", "文件不含视频流")
    count = info.video_streams[0].frame_count
    if count is None:
        raise MediaNodeError("E_MEDIA_FRAME_COUNT_UNKNOWN", "FFprobe 未返回可靠视频帧数")
    return count


def runner_media_probe(path: Path, kind: str) -> dict[str, object]:
    """适配 NodeRunner 的统一媒体 probe callable。"""

    info = probe_media(path)
    require_media_kind(info, kind)
    return info.to_summary()


def media_artifact_quick_probe(artifact: Artifact) -> bool:
    """为 Runtime reuse 提供低成本 fail-closed 可读性检查。

    媒体 Artifact 只做轻量 FFprobe 与 typed stream 检查；DataFile 只读取一个字节。该检查不计算
    hash、不尝试证明内容身份。
    """

    try:
        path = _regular_nonempty_file(artifact.path)
        stat = path.stat()
        if artifact.size is not None and stat.st_size != artifact.size:
            return False
        if artifact.mtime_ns is not None and stat.st_mtime_ns != artifact.mtime_ns:
            return False
        if artifact.kind == "DataFile":
            with path.open("rb") as stream:
                return bool(stream.read(1))
        if artifact.kind in {"MediaFile", "VideoFile", "AudioFile"}:
            runner_media_probe(path, artifact.kind)
            return True
        return False
    except (MediaNodeError, OSError):
        return False


def _regular_nonempty_file(path: str | Path) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
        stat = resolved.stat()
    except OSError as error:
        raise MediaNodeError("E_MEDIA_INPUT_UNREADABLE", str(error)) from error
    if not resolved.is_file() or stat.st_size <= 0:
        raise MediaNodeError("E_MEDIA_INPUT_UNREADABLE", f"不是非空常规文件：{path}")
    return resolved


def _parse_probe_payload(payload: object, *, count_frames: bool) -> MediaProbeInfo:
    if not isinstance(payload, dict):
        raise TypeError("FFprobe 根值必须是 object")
    streams = payload.get("streams")
    format_value = payload.get("format")
    if not isinstance(streams, list) or not isinstance(format_value, dict):
        raise TypeError("FFprobe 缺少 streams 或 format")
    format_name = format_value.get("format_name")
    if not isinstance(format_name, str) or not format_name:
        raise TypeError("FFprobe format_name 无效")
    duration = _optional_positive_float(format_value.get("duration"))

    video: list[VideoStreamInfo] = []
    audio: list[AudioStreamInfo] = []
    for raw in streams:
        if not isinstance(raw, dict):
            raise TypeError("FFprobe stream 必须是 object")
        kind = raw.get("codec_type")
        if kind == "video":
            video.append(_parse_video_stream(raw, count_frames=count_frames))
        elif kind == "audio":
            audio.append(_parse_audio_stream(raw))
    if not video and not audio:
        raise ValueError("FFprobe 未返回音视频流")
    return MediaProbeInfo(format_name, duration, tuple(video), tuple(audio))


def _parse_video_stream(raw: dict[str, Any], *, count_frames: bool) -> VideoStreamInfo:
    frame_rate = _first_positive_fraction(
        raw.get("avg_frame_rate"),
        raw.get("r_frame_rate"),
    )
    frame_count = _optional_positive_int(
        raw.get("nb_read_frames") if count_frames else raw.get("nb_frames")
    )
    if count_frames and frame_count is None:
        raise ValueError("FFprobe 未返回 nb_read_frames")
    return VideoStreamInfo(
        index=_nonnegative_int(raw.get("index"), "stream.index"),
        codec=_nonempty_string(raw.get("codec_name"), "stream.codec_name"),
        width=_positive_int(raw.get("width"), "stream.width"),
        height=_positive_int(raw.get("height"), "stream.height"),
        frame_rate=frame_rate,
        pixel_format=(str(raw["pix_fmt"]) if raw.get("pix_fmt") not in (None, "", "N/A") else None),
        frame_count=frame_count,
    )


def _parse_audio_stream(raw: dict[str, Any]) -> AudioStreamInfo:
    return AudioStreamInfo(
        index=_nonnegative_int(raw.get("index"), "stream.index"),
        codec=_nonempty_string(raw.get("codec_name"), "stream.codec_name"),
        sample_rate=_optional_positive_int(raw.get("sample_rate")),
        channels=_optional_positive_int(raw.get("channels")),
    )


def _positive_fraction(value: object) -> Fraction:
    if not isinstance(value, str):
        raise TypeError("视频帧率必须是 string")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("视频帧率无效") from error
    if result <= 0:
        raise ValueError("视频帧率必须为正数")
    return result


def _first_positive_fraction(*values: object) -> Fraction:
    """优先使用 avg_frame_rate，但把 ``0/0`` 等无效值视为可回退而非终局。"""

    for value in values:
        try:
            return _positive_fraction(value)
        except (TypeError, ValueError):
            continue
    raise ValueError("FFprobe 未返回有效视频帧率")


def _nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field_name} 必须为非空 string")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} 必须为非负 integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value)  # FFprobe JSON 的数值字段通常是十进制字符串。
        except ValueError as error:
            raise TypeError(f"{field_name} 必须为非负 integer") from error
    else:
        raise TypeError(f"{field_name} 必须为非负 integer")
    if result < 0:
        raise ValueError(f"{field_name} 必须为非负 integer")
    return result


def _positive_int(value: object, field_name: str) -> int:
    result = _nonnegative_int(value, field_name)
    if result <= 0:
        raise ValueError(f"{field_name} 必须为正 integer")
    return result


def _optional_positive_int(value: object) -> int | None:
    if value in (None, "", "N/A", "0"):
        return None
    return _positive_int(value, "FFprobe integer")


def _optional_positive_float(value: object) -> float | None:
    if value in (None, "", "N/A"):
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise TypeError("FFprobe duration 必须是 number")
    result = float(value)  # FFprobe duration 通常是十进制字符串。
    if not math.isfinite(result):
        raise ValueError("FFprobe duration 必须是有限 number")
    if result <= 0:
        return None
    return result
