"""执行 Phase 6 短真实媒体与目标存储开发门。

本模块只以固定 argv、``shell=False`` 调用受信 FFmpeg/FFprobe，并在调用方提供的临时目录工作。结果
模型不保存绝对路径；成功消息不等于 Runtime Evidence，也不注册为生产 Engine。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from zniku.contracts import ContractModel, ContractViolation, Sha256Digest

MEDIA_VALIDATION_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"


class MediaProbe(ContractModel):
    """短真实媒体的只读 FFprobe 摘要，不泄露本机路径。"""

    validation_contract_version: Literal["0.1.0"]
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    content_digest: Sha256Digest
    container: str = Field(min_length=1, max_length=128)
    duration_microseconds: int = Field(ge=1)
    video_codec: str = Field(min_length=1, max_length=64)
    frame_rate: str = Field(pattern=r"^[1-9][0-9]*/[1-9][0-9]*$")
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    video_stream_count: int = Field(ge=1)
    audio_stream_count: int = Field(ge=1)

    @field_validator("filename")
    @classmethod
    def reject_path(cls, value: str) -> str:
        if Path(value).name != value:
            raise ValueError("E_MEDIA_PROBE_FILENAME_PATH: probe 只保存文件名")
        return value


class PublicationReceipt(ContractModel):
    """no-replace publication 的最小结果，不把目录当作完成证明。"""

    validation_contract_version: Literal["0.1.0"]
    target_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    content_digest: Sha256Digest

    @field_validator("target_name")
    @classmethod
    def reject_path(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("E_PUBLICATION_TARGET_NAME: target_name 只允许文件名")
        return value


def _require_tool(name: Literal["ffmpeg", "ffprobe"]) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise ContractViolation("E_MEDIA_TOOL_UNAVAILABLE", f"Phase 6 gate 缺少 {name}")
    return executable


def _validate_target_name(value: str) -> None:
    if Path(value).name != value or value in {".", ".."}:
        raise ContractViolation("E_PUBLICATION_TARGET_NAME", "target_name 只允许文件名")


def _run_checked(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """只执行内部固定构造的 argv；失败时不把媒体 stdout 当作 authority。"""

    try:
        return subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ContractViolation("E_MEDIA_TOOL_FAILED", f"媒体开发门执行失败：{argv[0]}") from error


def generate_short_media(destination: Path) -> None:
    """在不存在的目标生成一秒 FFV1+PCM 合成媒体；不得覆盖调用方文件。"""

    if destination.exists():
        raise ContractViolation("E_MEDIA_TARGET_EXISTS", "短媒体目标已存在，禁止覆盖")
    if not destination.parent.is_dir():
        raise ContractViolation("E_MEDIA_TARGET_DIRECTORY", "短媒体目标目录不存在")
    _run_checked(
        [
            _require_tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24000/1001:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "pcm_s16le",
            "-shortest",
            str(destination),
        ]
    )


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return size, f"sha256:{digest.hexdigest()}"


def probe_media(path: Path) -> MediaProbe:
    """用 FFprobe JSON 读取短媒体技术摘要，并独立计算文件 digest。"""

    if not path.is_file():
        raise ContractViolation("E_MEDIA_SOURCE_MISSING", "probe source 不是常规文件")
    result = _run_checked(
        [
            _require_tool("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        format_data = payload["format"]
        video_streams = [item for item in streams if item["codec_type"] == "video"]
        audio_streams = [item for item in streams if item["codec_type"] == "audio"]
        video = video_streams[0]
        duration_us = round(float(format_data["duration"]) * 1_000_000)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ContractViolation(
            "E_MEDIA_PROBE_INVALID", "FFprobe 输出缺少正式门所需字段"
        ) from error
    if not video_streams or not audio_streams:
        raise ContractViolation("E_MEDIA_STREAM_REQUIRED", "短真实媒体必须同时包含视频与音频")
    size, digest = _sha256_file(path)
    return MediaProbe(
        validation_contract_version=MEDIA_VALIDATION_CONTRACT_VERSION,
        filename=path.name,
        size_bytes=size,
        content_digest=digest,
        container=str(format_data["format_name"]),
        duration_microseconds=duration_us,
        video_codec=str(video["codec_name"]),
        frame_rate=str(video["avg_frame_rate"]),
        width=int(video["width"]),
        height=int(video["height"]),
        video_stream_count=len(video_streams),
        audio_stream_count=len(audio_streams),
    )


def run_short_media_gate(directory: Path) -> MediaProbe:
    """生成并验证一个不会进入 Git 的短真实媒体 fixture。"""

    destination = directory / "zniku-phase6-short.mkv"
    generate_short_media(destination)
    probe = probe_media(destination)
    if probe.frame_rate != "24000/1001" or probe.width != 320 or probe.height != 180:
        raise ContractViolation("E_MEDIA_GATE_TECHNICAL_MISMATCH", "短媒体技术属性与 gate 不匹配")
    return probe


def publish_file_no_replace(
    source: Path,
    target_directory: Path,
    target_name: str,
    *,
    fault_hook: Callable[[str], None] | None = None,
) -> PublicationReceipt:
    """经 same-directory staging 与原子 hard-link no-replace 发布一个文件。

    `fault_hook` 仅用于开发期故障注入；它不能改变目标路径或 publication 数据。任一失败都会清理本次
    staging，且绝不删除调用前已存在的目标。
    """

    _validate_target_name(target_name)
    if not source.is_file():
        raise ContractViolation("E_PUBLICATION_SOURCE_MISSING", "publication source 不存在")
    if not target_directory.is_dir():
        raise ContractViolation("E_PUBLICATION_DIRECTORY_MISSING", "目标目录不存在")
    target = target_directory / target_name
    if target.exists():
        raise ContractViolation("E_PUBLICATION_TARGET_EXISTS", "目标已存在，禁止覆盖")

    descriptor, stage_name = tempfile.mkstemp(prefix=".zniku-stage-", dir=target_directory)
    os.close(descriptor)
    stage = Path(stage_name)
    committed = False
    try:
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as input_stream, stage.open("wb") as output_stream:
            for block in iter(lambda: input_stream.read(1024 * 1024), b""):
                output_stream.write(block)
                digest.update(block)
                size += len(block)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if size <= 0:
            raise ContractViolation("E_PUBLICATION_SOURCE_EMPTY", "禁止发布空文件")
        expected_digest = f"sha256:{digest.hexdigest()}"
        staged_size, staged_digest = _sha256_file(stage)
        if (staged_size, staged_digest) != (size, expected_digest):
            raise ContractViolation("E_PUBLICATION_STAGE_MISMATCH", "staging size/digest 复核失败")
        if fault_hook is not None:
            fault_hook("after_stage")
        if target.exists():
            raise ContractViolation("E_PUBLICATION_TARGET_RACE", "目标在 commit 前出现")
        if fault_hook is not None:
            fault_hook("before_commit")
        try:
            os.link(stage, target)
        except FileExistsError as error:
            raise ContractViolation(
                "E_PUBLICATION_TARGET_RACE", "no-replace commit 发生竞争"
            ) from error
        committed = True
        target_size, target_digest = _sha256_file(target)
        if (target_size, target_digest) != (size, expected_digest):
            raise ContractViolation("E_PUBLICATION_TARGET_MISMATCH", "发布后 size/digest 复核失败")
        return PublicationReceipt(
            validation_contract_version=MEDIA_VALIDATION_CONTRACT_VERSION,
            target_name=target_name,
            size_bytes=size,
            content_digest=expected_digest,
        )
    except Exception:
        if committed and target.exists():
            target.unlink()
        raise
    finally:
        if stage.exists():
            stage.unlink()
