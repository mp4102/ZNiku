"""实现真实媒体验收候选使用的受信 FFmpeg/FFprobe 原语。

所有 argv 都在本模块内部按闭合操作构造并以 ``shell=False`` 执行。调用方只能提供已由 Runtime policy
解析的 Path 和有界数值，不能提供命令、filter、codec 或可执行入口。输出一律 no-replace；工具失败时
清理本次未发布候选，且进程退出成功仍需经过 probe、digest 或完整 decode 才能形成上层 Evidence。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from zniku.contracts import ContractModel, ContractViolation, Sha256Digest

REAL_MEDIA_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"
_TOOL_TIMEOUT_SECONDS = 900


class MediaStreamProbe(ContractModel):
    """单一媒体 stream 的闭合技术摘要。"""

    index: int = Field(ge=0)
    codec_type: Literal["video", "audio"]
    codec_name: str = Field(min_length=1, max_length=64)
    frame_rate: str | None = None
    frame_count: int | None = Field(default=None, ge=1)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    pixel_format: str | None = Field(default=None, max_length=64)
    sample_rate: int | None = Field(default=None, ge=1)
    channels: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> MediaStreamProbe:
        if self.codec_type == "video":
            if None in (self.frame_rate, self.frame_count, self.width, self.height):
                raise ValueError("E_REAL_PROBE_VIDEO_FIELDS: video probe 字段不完整")
        elif None in (self.sample_rate, self.channels):
            raise ValueError("E_REAL_PROBE_AUDIO_FIELDS: audio probe 字段不完整")
        return self


class DetailedMediaProbe(ContractModel):
    """绑定文件内容身份和全部音视频 stream 顺序的 FFprobe 结果。"""

    real_media_contract_version: Literal["0.1.0"]
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    content_digest: Sha256Digest
    container: str = Field(min_length=1, max_length=128)
    duration_microseconds: int = Field(ge=1)
    streams: tuple[MediaStreamProbe, ...]

    @field_validator("filename")
    @classmethod
    def reject_path(cls, value: str) -> str:
        if Path(value).name != value:
            raise ValueError("E_REAL_PROBE_FILENAME_PATH: filename 不得含路径")
        return value

    @model_validator(mode="after")
    def require_unique_streams(self) -> DetailedMediaProbe:
        indices = tuple(stream.index for stream in self.streams)
        if not self.streams or len(indices) != len(set(indices)):
            raise ValueError("E_REAL_PROBE_STREAMS: stream 必须非空且 index 唯一")
        return self

    @property
    def video_streams(self) -> tuple[MediaStreamProbe, ...]:
        return tuple(stream for stream in self.streams if stream.codec_type == "video")

    @property
    def audio_streams(self) -> tuple[MediaStreamProbe, ...]:
        return tuple(stream for stream in self.streams if stream.codec_type == "audio")


def _tool(name: Literal["ffmpeg", "ffprobe"]) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise ContractViolation("E_REAL_MEDIA_TOOL_UNAVAILABLE", f"缺少受信媒体工具 {name}")
    return executable


def _run(argv: list[str], *, timeout: int = _TOOL_TIMEOUT_SECONDS) -> str:
    """执行内部固定 argv；异常只暴露截断后的 stderr，不把命令变成合同字段。"""

    try:
        completed = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip().replace("\n", " ")[-500:]
        raise ContractViolation("E_REAL_MEDIA_TOOL_FAILED", f"媒体工具失败：{detail}") from error
    except subprocess.TimeoutExpired as error:
        raise ContractViolation("E_REAL_MEDIA_TOOL_TIMEOUT", "媒体工具执行超时") from error
    return completed.stdout


def _require_input(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ContractViolation("E_REAL_MEDIA_INPUT", "媒体输入不是常规文件")
    return resolved


def _require_new_output(path: Path) -> Path:
    resolved_parent = path.parent.resolve(strict=True)
    if not resolved_parent.is_dir() or path.exists():
        raise ContractViolation("E_REAL_MEDIA_OUTPUT_EXISTS", "媒体输出目录无效或目标已存在")
    return resolved_parent / path.name


def hash_file(path: Path) -> tuple[int, Sha256Digest]:
    """流式计算文件大小和 SHA-256；常规文件缺失或为空时失败关闭。"""

    source = _require_input(path)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    if size == 0:
        raise ContractViolation("E_REAL_MEDIA_EMPTY", "媒体文件为空")
    return size, Sha256Digest(f"sha256:{digest.hexdigest()}")


def _fraction(value: str) -> Fraction:
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ContractViolation("E_REAL_PROBE_RATE", "FFprobe 返回非法帧率") from error
    if result <= 0:
        raise ContractViolation("E_REAL_PROBE_RATE", "FFprobe 返回非正帧率")
    return result


def probe_detailed(path: Path) -> DetailedMediaProbe:
    """完整读取 stream 顺序、视频帧数与文件 digest；不信任文件名或容器元数据。"""

    source = _require_input(path)
    output = _run(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            (
                "format=format_name,duration:"
                "stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,"
                "nb_read_frames,sample_rate,channels"
            ),
            "-of",
            "json",
            str(source),
        ]
    )
    try:
        payload: dict[str, Any] = json.loads(output)
        stream_values: list[dict[str, Any]] = payload["streams"]
        format_value: dict[str, Any] = payload["format"]
        duration_us = round(float(format_value["duration"]) * 1_000_000)
        streams = tuple(_parse_stream(item) for item in stream_values)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ContractViolation("E_REAL_PROBE_INVALID", "FFprobe JSON 缺少必需字段") from error
    size, digest = hash_file(source)
    return DetailedMediaProbe(
        real_media_contract_version=REAL_MEDIA_CONTRACT_VERSION,
        filename=source.name,
        size_bytes=size,
        content_digest=digest,
        container=str(format_value["format_name"]),
        duration_microseconds=duration_us,
        streams=streams,
    )


def _parse_stream(item: dict[str, Any]) -> MediaStreamProbe:
    kind = item.get("codec_type")
    if kind not in {"video", "audio"}:
        raise ContractViolation("E_REAL_PROBE_STREAM_KIND", "候选只接受音视频 stream")
    common: dict[str, Any] = {
        "index": int(item["index"]),
        "codec_type": kind,
        "codec_name": str(item["codec_name"]),
    }
    if kind == "video":
        rate = str(item["avg_frame_rate"])
        _fraction(rate)
        common.update(
            frame_rate=rate,
            frame_count=int(item["nb_read_frames"]),
            width=int(item["width"]),
            height=int(item["height"]),
            pixel_format=str(item.get("pix_fmt") or "unknown"),
        )
    else:
        common.update(sample_rate=int(item["sample_rate"]), channels=int(item["channels"]))
    return MediaStreamProbe(**common)


def _run_output(argv: list[str], output: Path) -> None:
    target = _require_new_output(output)
    try:
        _run([*argv, str(target)])
        if not target.is_file() or target.stat().st_size <= 0:
            raise ContractViolation("E_REAL_MEDIA_OUTPUT_MISSING", "媒体工具未产生非空输出")
    except Exception:
        if target.exists():
            target.unlink()
        raise


def derive_acceptance_clip(
    source: Path, output: Path, *, start_seconds: int = 28, duration_seconds: int = 4
) -> DetailedMediaProbe:
    """从只读参考源派生精确可解码的 FFV1+原始音频隔离片段。"""

    if not 0 <= start_seconds <= 86_400 or not 1 <= duration_seconds <= 30:
        raise ContractViolation("E_REAL_CLIP_RANGE", "验收片段时间范围越界")
    _run_output(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            str(start_seconds),
            "-t",
            str(duration_seconds),
            "-i",
            str(_require_input(source)),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-map_metadata",
            "-1",
        ],
        output,
    )
    probe = probe_detailed(output)
    if len(probe.video_streams) != 1 or not probe.audio_streams:
        raise ContractViolation("E_REAL_CLIP_STREAMS", "隔离片段必须恰有一路视频且至少一路音频")
    return probe


def demux_media(source: Path, video_output: Path, audio_outputs: tuple[Path, ...]) -> None:
    """将 ProgramMedia 显式拆成无音频视频和按原顺序排列的独立音轨。"""

    probe = probe_detailed(source)
    if len(probe.video_streams) != 1 or len(audio_outputs) != len(probe.audio_streams):
        raise ContractViolation(
            "E_REAL_DEMUX_SHAPE", "Demux 输出数量与输入 stream authority 不匹配"
        )
    _run_output(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(_require_input(source)),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            "-map_metadata",
            "-1",
        ],
        video_output,
    )
    for ordinal, output in enumerate(audio_outputs):
        _run_output(
            [
                _tool("ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-i",
                str(_require_input(source)),
                "-map",
                f"0:a:{ordinal}",
                "-vn",
                "-c:a",
                "copy",
                "-map_metadata",
                "-1",
            ],
            output,
        )


def extract_chapter(source: Path, output: Path, *, start_frame: int, end_frame: int) -> None:
    """按冻结的 half-open frame coverage 生成一段无音频 FFV1 chapter。"""

    if start_frame < 0 or end_frame <= start_frame:
        raise ContractViolation("E_REAL_CHAPTER_RANGE", "chapter frame coverage 非法")
    expression = f"trim=start_frame={start_frame}:end_frame={end_frame},setpts=PTS-STARTPTS"
    _run_output(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(_require_input(source)),
            "-map",
            "0:v:0",
            "-vf",
            expression,
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "passthrough",
            "-map_metadata",
            "-1",
        ],
        output,
    )


def make_acceptance_fixture(
    source: Path, output: Path, *, operation: Literal["enhancement", "frame_interpolation"]
) -> None:
    """生成明确标注的人工 Engine 验收 fixture；不冒充生产模型处理结果。"""

    input_probe = probe_detailed(source)
    if len(input_probe.video_streams) != 1 or input_probe.audio_streams:
        raise ContractViolation("E_REAL_FIXTURE_INPUT", "人工 fixture 输入必须是单路无音频视频")
    if operation == "enhancement":
        filters = "null"
    else:
        rate = _fraction(input_probe.video_streams[0].frame_rate or "0/0") * 2
        filters = f"fps={rate.numerator}/{rate.denominator}"
    _run_output(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(_require_input(source)),
            "-map",
            "0:v:0",
            "-vf",
            filters,
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "yuv420p",
            "-map_metadata",
            "-1",
        ],
        output,
    )


def concat_video(inputs: tuple[Path, ...], output: Path) -> None:
    """按调用方冻结顺序拼接完整 chapter set，不使用 shell 或 concat 清单脚本。"""

    if not inputs:
        raise ContractViolation("E_REAL_REDUCE_EMPTY", "Reduce 输入集合为空")
    argv = [_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin"]
    for source in inputs:
        argv.extend(["-i", str(_require_input(source))])
    filter_value = f"concat=n={len(inputs)}:v=1:a=0[outv]"
    argv.extend(
        [
            "-filter_complex",
            filter_value,
            "-map",
            "[outv]",
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "passthrough",
            "-map_metadata",
            "-1",
        ]
    )
    _run_output(argv, output)


def encode_hevc_main10(source: Path, output: Path, *, crf: int = 28) -> None:
    """执行一次性 CPU HEVC Main10 编码；失败候选整体删除，只能由 Runtime 显式 retry。"""

    if not 0 <= crf <= 51:
        raise ContractViolation("E_REAL_ENCODE_CRF", "CRF 越界")
    _run_output(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(_require_input(source)),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx265",
            "-preset",
            "ultrafast",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p10le",
            "-fps_mode",
            "passthrough",
            "-map_metadata",
            "-1",
        ],
        output,
    )


def mux_original_audio(video: Path, audio: tuple[Path, ...], output: Path) -> None:
    """把编码视频和 Demux 的全部有序原始音轨 stream-copy 到 Matroska。"""

    if not audio:
        raise ContractViolation("E_REAL_MUX_AUDIO_EMPTY", "Mux 必须显式消费原始音轨集合")
    argv = [
        _tool("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(_require_input(video)),
    ]
    for source in audio:
        argv.extend(["-i", str(_require_input(source))])
    argv.extend(["-map", "0:v:0"])
    for index in range(len(audio)):
        argv.extend(["-map", f"{index + 1}:a:0"])
    argv.extend(["-c", "copy", "-fps_mode", "passthrough", "-map_metadata", "-1"])
    _run_output(argv, output)


def decode_verify(path: Path) -> None:
    """完整解码全部音视频 stream；无输出且任一解码错误即失败。"""

    _run(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-xerror",
            "-i",
            str(_require_input(path)),
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-f",
            "null",
            "-",
        ]
    )


def hash_audio_stream(path: Path, ordinal: int) -> Sha256Digest:
    """对指定 audio ordinal 的压缩 bitstream 计算 FFmpeg SHA-256。"""

    if not 0 <= ordinal <= 31:
        raise ContractViolation("E_REAL_AUDIO_ORDINAL", "audio ordinal 越界")
    output = _run(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(_require_input(path)),
            "-map",
            f"0:a:{ordinal}",
            "-c",
            "copy",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ]
    ).strip()
    prefix = "SHA256="
    if not output.startswith(prefix) or len(output) != len(prefix) + 64:
        raise ContractViolation("E_REAL_AUDIO_HASH", "音频 bitstream hash 输出非法")
    return Sha256Digest(f"sha256:{output.removeprefix(prefix).lower()}")
