"""实现 Phase 4 首批真实媒体节点的 Python adapters。

除 SourceMedia 返回只读外部引用、OutputFile 按操作者给出的绝对路径显式发布外，
adapter 只操作 Runner 分配的 attempt 输出。FFmpeg 始终通过 argv 与 ``shell=False`` 运行；失败会尝试
确认回收进程并清理本 attempt 已声明输出，无法确认回收时给出稳定失败码。任何失败都不会返回可登记的
部分 Artifact、checkpoint 或 Evidence。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import uuid4

from zniku.media.probe import (
    MediaNodeError,
    exact_video_frame_count,
    probe_media,
    require_media_kind,
    resolve_media_tool,
)
from zniku.runtime import (
    FrameRange,
    ProducedOutput,
    ProgressError,
    ProgressInfrastructureError,
    PythonAdapterContext,
    PythonAdapterResult,
    RunnerInput,
)
from zniku.runtime.runner import OutputTarget


@dataclass(frozen=True, slots=True)
class SegmentRange:
    """SplitVideo 的稳定命名 half-open 帧区间。"""

    port_id: str
    start_frame: int
    end_frame: int

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame

    def to_summary(self) -> dict[str, object]:
        return {
            "port_id": self.port_id,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "frame_count": self.frame_count,
        }


@dataclass(frozen=True, slots=True)
class _FFmpegProgressSpec:
    """把 FFmpeg 机器字段映射到一个已证明可靠的 denominator。

    ``offset`` 与 ``extent`` 只服务于 Split 的多进程累计帧数；它们不能用于猜测未知输出总量。
    没有可信 spec 的 adapter 仍使用同一流式进程与日志路径，但保持 indeterminate。
    """

    field: Literal["frame", "out_time_us"]
    unit: Literal["frames", "microseconds"]
    total: int
    offset: int = 0
    extent: int | None = None

    def __post_init__(self) -> None:
        if self.total <= 0 or self.offset < 0 or self.offset > self.total:
            raise ValueError("FFmpeg progress denominator 无效")
        if self.extent is not None and (self.extent <= 0 or self.offset + self.extent > self.total):
            raise ValueError("FFmpeg progress extent 无效")


def source_media(context: PythonAdapterContext) -> PythonAdapterResult:
    """把显式本地源登记为只读外部引用；不复制、移动、改写或摘要源文件。"""

    target = _single_output(context)
    source_value = _required_string(context.node.parameters, "source_path")
    source = Path(source_value)
    if not source.is_absolute():
        raise MediaNodeError("E_MEDIA_SOURCE_PATH_RELATIVE", "source_path 必须是绝对路径")
    source = _nonempty_file(source)
    _append_log(context.stdout_log_path, f"SourceMedia referenced {source.name}\n")
    return PythonAdapterResult(
        outputs=(ProducedOutput(target.port_id, source, allow_external=True),),
        media_summary={
            "source_name": source.name,
            "source_size": source.stat().st_size,
            "mode": "reference",
        },
        validation_summary={"source_reference_readable": True},
    )


def video_transform(context: PythonAdapterContext) -> PythonAdapterResult:
    """执行 identity、scale 或 frame_rate 三种闭合 automatic VideoTransform。"""

    source = _single_input(context, "video").path
    target = _single_output(context, "video")
    operation = _optional_string(context.node.parameters, "operation", "identity")
    if operation == "identity":
        filter_value = "null"
    elif operation == "scale":
        width = _required_integer(context.node.parameters, "width", minimum=2, maximum=16384)
        height = _required_integer(context.node.parameters, "height", minimum=2, maximum=16384)
        filter_value = f"scale={width}:{height}:flags=lanczos"
    elif operation == "frame_rate":
        frame_rate = _required_fraction(context.node.parameters, "frame_rate")
        filter_value = f"fps={frame_rate.numerator}/{frame_rate.denominator}"
    else:
        raise MediaNodeError("E_MEDIA_TRANSFORM_OPERATION_UNKNOWN", f"未知 operation {operation!r}")
    progress_spec = (
        _FFmpegProgressSpec(
            field="frame",
            unit="frames",
            total=exact_video_frame_count(source),
        )
        if operation in {"identity", "scale"}
        else None
    )

    try:
        _run_ffmpeg(
            context,
            [
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-vf",
                filter_value,
                "-an",
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-fps_mode",
                "passthrough",
                "-map_metadata",
                "-1",
                "-f",
                "matroska",
                str(target.path),
            ],
            progress_spec=progress_spec,
        )
    except Exception:
        _cleanup_outputs(context.outputs)
        raise
    return PythonAdapterResult(media_summary={"operation": operation})


def split_video(context: PythonAdapterContext) -> PythonAdapterResult:
    """按连续 half-open frame range 产生全部命名输出并验证帧数守恒。"""

    source = _single_input(context, "video").path
    input_frames = exact_video_frame_count(source)
    segments = parse_segments(
        context.node.parameters.get("segments"),
        expected_ports=tuple(output.port_id for output in context.outputs),
        input_frames=input_frames,
    )
    outputs = {output.port_id: output for output in context.outputs}
    actual_counts: dict[str, int] = {}
    completed_frames = 0
    try:
        for segment in segments:
            target = outputs[segment.port_id]
            _run_ffmpeg(
                context,
                [
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-vf",
                    (
                        f"trim=start_frame={segment.start_frame}:"
                        f"end_frame={segment.end_frame},setpts=PTS-STARTPTS"
                    ),
                    "-an",
                    "-c:v",
                    "ffv1",
                    "-level",
                    "3",
                    "-fps_mode",
                    "passthrough",
                    "-map_metadata",
                    "-1",
                    "-f",
                    "matroska",
                    str(target.path),
                ],
                progress_spec=_FFmpegProgressSpec(
                    field="frame",
                    unit="frames",
                    total=input_frames,
                    offset=completed_frames,
                    extent=segment.frame_count,
                ),
            )
            actual_counts[segment.port_id] = exact_video_frame_count(target.path)
            completed_frames += segment.frame_count
        for segment in segments:
            if actual_counts[segment.port_id] != segment.frame_count:
                raise MediaNodeError(
                    "E_MEDIA_SPLIT_OUTPUT_COUNT",
                    f"{segment.port_id} 实际帧数与区间长度不一致",
                )
        if sum(actual_counts.values()) != input_frames:
            raise MediaNodeError("E_MEDIA_SPLIT_CONSERVATION", "Split 输出总帧数不守恒")
    except Exception:
        _cleanup_outputs(context.outputs)
        raise
    return PythonAdapterResult(
        outputs=tuple(
            ProducedOutput(
                segment.port_id,
                outputs[segment.port_id].path,
                frame_range=FrameRange(
                    start_frame=segment.start_frame,
                    end_frame=segment.end_frame,
                ),
            )
            for segment in segments
        ),
        media_summary={
            "input_frame_count": input_frames,
            "segments": [segment.to_summary() for segment in segments],
        },
        validation_summary={"frame_count_conserved": True},
    )


def merge_video(context: PythonAdapterContext) -> PythonAdapterResult:
    """按 Runner 已校验的 ordinal 顺序合并视频，并强制输出帧数等于输入总和。"""

    inputs = tuple(item for item in context.inputs if item.port_id == "videos")
    if not inputs:
        raise MediaNodeError("E_MEDIA_MERGE_INPUT_EMPTY", "MergeVideo 输入为空")
    target = _single_output(context, "video")
    input_counts = [exact_video_frame_count(item.path) for item in inputs]
    argv: list[str] = []
    for item in inputs:
        argv.extend(["-i", str(item.path)])
    if len(inputs) == 1:
        argv.extend(["-map", "0:v:0"])
    else:
        pads = "".join(f"[{index}:v:0]" for index in range(len(inputs)))
        argv.extend(
            [
                "-filter_complex",
                f"{pads}concat=n={len(inputs)}:v=1:a=0[outv]",
                "-map",
                "[outv]",
            ]
        )
    argv.extend(
        [
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-fps_mode",
            "passthrough",
            "-map_metadata",
            "-1",
            "-f",
            "matroska",
            str(target.path),
        ]
    )
    try:
        _run_ffmpeg(
            context,
            argv,
            progress_spec=_FFmpegProgressSpec(
                field="frame",
                unit="frames",
                total=sum(input_counts),
            ),
        )
        output_count = exact_video_frame_count(target.path)
        if output_count != sum(input_counts):
            raise MediaNodeError(
                "E_MEDIA_MERGE_CONSERVATION", "Merge 输出帧数不等于有序输入帧数总和"
            )
    except Exception:
        _cleanup_outputs(context.outputs)
        raise
    return PythonAdapterResult(
        media_summary={"input_frame_counts": input_counts, "output_frame_count": output_count},
        validation_summary={"frame_count_conserved": True},
    )


def encode_video(context: PythonAdapterContext) -> PythonAdapterResult:
    """执行一次完整视频编码；不保存 offset、分片或编码器 checkpoint。"""

    source = _single_input(context, "video").path
    target = _single_output(context, "video")
    input_frames = exact_video_frame_count(source)
    codec = _optional_string(context.node.parameters, "codec", "libx264")
    if codec not in {"libx264", "libx265", "ffv1"}:
        raise MediaNodeError("E_MEDIA_ENCODE_CODEC_UNKNOWN", f"未知 codec {codec!r}")
    pixel_format = _optional_string(context.node.parameters, "pixel_format", "yuv420p")
    if pixel_format not in {"yuv420p", "yuv420p10le", "yuv422p10le"}:
        raise MediaNodeError(
            "E_MEDIA_ENCODE_PIXEL_FORMAT_UNKNOWN", f"未知 pixel_format {pixel_format!r}"
        )
    argv = ["-i", str(source), "-map", "0:v:0", "-an", "-c:v", codec]
    if codec == "ffv1":
        argv.extend(["-level", "3"])
    else:
        preset = _optional_string(context.node.parameters, "preset", "medium")
        if preset not in {
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
        }:
            raise MediaNodeError("E_MEDIA_ENCODE_PRESET_UNKNOWN", f"未知 preset {preset!r}")
        crf = _optional_integer(context.node.parameters, "crf", 18, minimum=0, maximum=51)
        argv.extend(["-preset", preset, "-crf", str(crf)])
    argv.extend(
        [
            "-pix_fmt",
            pixel_format,
            "-fps_mode",
            "passthrough",
            "-map_metadata",
            "-1",
            "-f",
            "matroska",
            str(target.path),
        ]
    )
    try:
        _run_ffmpeg(
            context,
            argv,
            progress_spec=_FFmpegProgressSpec(
                field="frame",
                unit="frames",
                total=input_frames,
            ),
        )
    except Exception:
        _cleanup_outputs(context.outputs)
        raise
    return PythonAdapterResult(media_summary={"codec": codec, "pixel_format": pixel_format})


def mux_media(context: PythonAdapterContext) -> PythonAdapterResult:
    """按 ordinal 将可选音轨与视频 stream-copy 到单一 Matroska 媒体。"""

    video = _single_input(context, "video")
    audio = tuple(item for item in context.inputs if item.port_id == "audio")
    target = _single_output(context, "media")
    argv = ["-i", str(video.path)]
    for item in audio:
        argv.extend(["-i", str(item.path)])
    argv.extend(["-map", "0:v:0"])
    for index in range(len(audio)):
        argv.extend(["-map", f"{index + 1}:a:0"])
    argv.extend(
        [
            "-c",
            "copy",
            "-map_metadata",
            "-1",
            "-f",
            "matroska",
            str(target.path),
        ]
    )
    try:
        _run_ffmpeg(context, argv)
    except Exception:
        _cleanup_outputs(context.outputs)
        raise
    return PythonAdapterResult(media_summary={"audio_stream_count": len(audio)})


def output_file(context: PythonAdapterContext) -> PythonAdapterResult:
    """受控发布上游文件；覆盖必须由参数显式授权，且绝不移动上游 Artifact。"""

    if len(context.inputs) != 1:
        raise MediaNodeError("E_MEDIA_OUTPUT_INPUT_COUNT", "OutputFile 必须精确绑定一个输入")
    published_output = _single_output(context, "published")
    source = _nonempty_file(context.inputs[0].path)
    source_size = source.stat().st_size
    source_kind = context.inputs[0].kind
    require_media_kind(probe_media(source), source_kind)
    mode = _required_string(context.node.parameters, "mode")
    overwrite = _required_boolean(context.node.parameters, "overwrite")
    if mode == "reference":
        if overwrite:
            raise MediaNodeError(
                "E_MEDIA_OUTPUT_REFERENCE_OVERWRITE",
                "reference 模式不允许 overwrite=true",
            )
        _append_log(context.stdout_log_path, f"OutputFile referenced {source}\n")
        return PythonAdapterResult(
            outputs=(
                ProducedOutput(
                    published_output.port_id,
                    source,
                    allow_external=True,
                ),
            ),
            media_summary={
                "published_path": str(source),
                "mode": "reference",
                "overwrite": False,
            },
            validation_summary={"published": True, "source_moved": False},
        )
    if mode != "copy":
        raise MediaNodeError("E_MEDIA_OUTPUT_MODE_UNKNOWN", f"未知 mode {mode!r}")
    target_value = _required_string(context.node.parameters, "target_path")
    target = Path(target_value)
    if not target.is_absolute():
        raise MediaNodeError("E_MEDIA_OUTPUT_PATH_RELATIVE", "target_path 必须是绝对路径")
    try:
        parent = target.parent.resolve(strict=True)
    except OSError as error:
        raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", str(error)) from error
    if not parent.is_dir():
        raise MediaNodeError("E_MEDIA_OUTPUT_PARENT_INVALID", "目标父路径不是目录")
    target = parent / target.name
    if target.resolve(strict=False) == source:
        raise MediaNodeError("E_MEDIA_OUTPUT_SAME_PATH", "OutputFile 不得覆盖上游 Artifact")
    if target.exists() and not overwrite:
        raise MediaNodeError("E_MEDIA_OUTPUT_EXISTS", "目标已存在且 overwrite=false")

    if overwrite:
        temporary = parent / f".{target.name}.zniku-{uuid4().hex}.tmp"
        try:
            with source.open("rb") as source_stream, temporary.open("xb") as target_stream:
                _copy_stream_with_progress(
                    context,
                    source_stream,
                    target_stream,
                    total=source_size,
                )
            shutil.copystat(source, temporary)
            require_media_kind(probe_media(temporary), source_kind)
            os.replace(temporary, target)
        except (ProgressError, ProgressInfrastructureError):
            raise
        except Exception as error:
            # 外部路径不属于 attempt；失败候选保留给操作者检查，绝不越权清理。
            raise MediaNodeError(
                "E_MEDIA_OUTPUT_OVERWRITE_FAILED",
                f"覆盖发布失败；若已创建临时候选需由操作者处理：{temporary}",
            ) from error
    else:
        try:
            with source.open("rb") as source_stream, target.open("xb") as target_stream:
                _copy_stream_with_progress(
                    context,
                    source_stream,
                    target_stream,
                    total=source_size,
                )
            shutil.copystat(source, target)
        except FileExistsError as error:
            raise MediaNodeError("E_MEDIA_OUTPUT_EXISTS", "目标在发布期间出现") from error
        except (ProgressError, ProgressInfrastructureError):
            raise
        except Exception as error:
            # 失败的外部 partial 不登记 Artifact，也不得由 Runtime 越权删除。
            raise MediaNodeError(
                "E_MEDIA_OUTPUT_COPY_FAILED",
                f"复制失败；若目标存在 partial 需由操作者处理：{target}",
            ) from error
    require_media_kind(probe_media(target), source_kind)
    stat = target.stat()
    if not target.is_file() or stat.st_size <= 0:
        raise MediaNodeError("E_MEDIA_OUTPUT_INVALID", "发布结果不是非空常规文件")
    _append_log(context.stdout_log_path, f"OutputFile published {target}\n")
    return PythonAdapterResult(
        outputs=(
            ProducedOutput(
                published_output.port_id,
                target,
                allow_external=True,
            ),
        ),
        media_summary={
            "published_path": str(target),
            "mode": "copy",
            "overwrite": overwrite,
            "size": target.stat().st_size,
        },
        validation_summary={"published": True, "source_moved": False},
    )


def parse_segments(
    value: object,
    *,
    expected_ports: tuple[str, ...],
    input_frames: int,
) -> tuple[SegmentRange, ...]:
    """严格解析并校验连续、无重叠、无缺口且覆盖全部输入的 segment。"""

    if not isinstance(value, list | tuple) or len(value) != len(expected_ports):
        raise MediaNodeError("E_MEDIA_SPLIT_SEGMENTS", "segments 数量与输出端口不一致")
    segments: list[SegmentRange] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {
            "port_id",
            "start_frame",
            "end_frame",
        }:
            raise MediaNodeError("E_MEDIA_SPLIT_SEGMENT_FIELDS", "segment 字段集合无效")
        port_id = raw["port_id"]
        start = raw["start_frame"]
        end = raw["end_frame"]
        if not isinstance(port_id, str) or port_id != expected_ports[index]:
            raise MediaNodeError("E_MEDIA_SPLIT_ORDER", "segment 顺序必须匹配命名输出端口")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise MediaNodeError("E_MEDIA_SPLIT_RANGE", "segment 必须是合法 half-open 帧区间")
        expected_start = 0 if index == 0 else segments[-1].end_frame
        if start != expected_start:
            raise MediaNodeError("E_MEDIA_SPLIT_COVERAGE", "segment 区间存在重叠或缺口")
        segments.append(SegmentRange(port_id, start, end))
    if not segments or segments[-1].end_frame != input_frames:
        raise MediaNodeError("E_MEDIA_SPLIT_CONSERVATION", "segments 必须完整覆盖输入帧数")
    return tuple(segments)


def _run_ffmpeg(
    context: PythonAdapterContext,
    argv: Sequence[str],
    *,
    progress_spec: _FFmpegProgressSpec | None = None,
) -> None:
    """流式执行 FFmpeg，并只解释专属 ``-progress`` pipe 的机器字段。

    stderr 始终由子进程直接追加到 attempt 日志；stdout 只承载 FFmpeg 的 progress protocol，
    同时原样写入 stdout 日志以便诊断。任何异常都会先尝试终止并确认回收子进程；若操作系统仍报告
    producer 存活，则以明确 cleanup failure 上抛，不能误报已经回收。
    """

    executable = resolve_media_tool("ffmpeg")
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
        *argv,
    ]
    process: subprocess.Popen[bytes] | None = None
    try:
        with (
            context.stdout_log_path.open("ab") as stdout_stream,
            context.stderr_log_path.open("ab") as stderr_stream,
        ):
            try:
                process = subprocess.Popen(
                    command,
                    cwd=context.work_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=stderr_stream,
                    shell=False,
                )
            except (OSError, ValueError) as error:
                raise MediaNodeError("E_MEDIA_FFMPEG_START_FAILED", str(error)) from error
            progress_fields: dict[bytes, bytes] = {}
            try:
                if process.stdout is None:
                    raise MediaNodeError(
                        "E_MEDIA_FFMPEG_PROGRESS_PIPE",
                        "FFmpeg progress pipe 未建立",
                    )
                with process.stdout:
                    for raw_line in process.stdout:
                        stdout_stream.write(raw_line)
                        stdout_stream.flush()
                        _consume_ffmpeg_progress(
                            context,
                            raw_line,
                            fields=progress_fields,
                            progress_spec=progress_spec,
                        )
                return_code = process.wait()
            except BaseException as error:
                _terminate_process(process, cause=error)
                raise
    except MediaNodeError:
        raise
    except OSError as error:
        if process is not None:
            _terminate_process(process, cause=error)
        raise MediaNodeError("E_MEDIA_FFMPEG_IO_FAILED", str(error)) from error
    if return_code != 0:
        raise MediaNodeError("E_MEDIA_FFMPEG_FAILED", f"FFmpeg 退出码为 {return_code}")


def _consume_ffmpeg_progress(
    context: PythonAdapterContext,
    raw_line: bytes,
    *,
    fields: dict[bytes, bytes],
    progress_spec: _FFmpegProgressSpec | None,
) -> None:
    """消费一个 FFmpeg progress 机器行；其他 stdout 文本只记日志、不推断进度。"""

    line = raw_line.rstrip(b"\r\n")
    key, separator, value = line.partition(b"=")
    if not separator:
        return
    if key == b"progress":
        if value not in {b"continue", b"end"}:
            raise MediaNodeError(
                "E_MEDIA_FFMPEG_PROGRESS_INVALID",
                "FFmpeg progress 终止字段无效",
            )
        _emit_ffmpeg_progress(context, fields, progress_spec)
        fields.clear()
        return
    if key not in {b"frame", b"out_time_us", b"total_size"}:
        return
    if key in fields:
        raise MediaNodeError(
            "E_MEDIA_FFMPEG_PROGRESS_INVALID",
            f"FFmpeg progress 字段 {key.decode('ascii')} 重复",
        )
    fields[key] = value


def _emit_ffmpeg_progress(
    context: PythonAdapterContext,
    fields: Mapping[bytes, bytes],
    progress_spec: _FFmpegProgressSpec | None,
) -> None:
    if progress_spec is None:
        return
    raw_current = fields.get(progress_spec.field.encode("ascii"))
    if raw_current is None:
        return
    normalized = raw_current.strip()
    if not normalized.isdigit():
        raise MediaNodeError(
            "E_MEDIA_FFMPEG_PROGRESS_INVALID",
            f"FFmpeg {progress_spec.field} 不是非负 integer",
        )
    measured = int(normalized)
    if progress_spec.extent is not None and measured > progress_spec.extent:
        raise MediaNodeError(
            "E_MEDIA_FFMPEG_PROGRESS_RANGE",
            "FFmpeg progress 超出当前 Split segment",
        )
    current = progress_spec.offset + measured
    if current > progress_spec.total:
        raise MediaNodeError(
            "E_MEDIA_FFMPEG_PROGRESS_RANGE",
            "FFmpeg progress 超出可信 denominator",
        )
    _report_progress(
        context,
        current=current,
        total=progress_spec.total,
        unit=progress_spec.unit,
    )


def _report_progress(
    context: PythonAdapterContext,
    *,
    current: int,
    total: int,
    unit: Literal["frames", "bytes", "microseconds"],
) -> None:
    reporter = context.progress
    if reporter is None:
        return
    reporter.report(
        fraction=current / total,
        current=current,
        total=total,
        unit=unit,
    )


def _terminate_process(
    process: subprocess.Popen[bytes],
    *,
    cause: BaseException,
) -> None:
    """终止并确认回收 FFmpeg；失败时保留触发清理的原始异常链。

    ``terminate``/``kill`` 返回或抛错都不能证明进程已退出。每个等待阶段后重新读取 ``poll``，
    最终仍存活时以稳定 cleanup failure 失败关闭，避免 Runtime 在 producer 尚存活时收敛 attempt。
    """

    cleanup_errors: list[str] = []

    def poll() -> int | None:
        try:
            return process.poll()
        except OSError as error:
            cleanup_errors.append(f"poll: {error}")
            return None

    def wait(stage: str) -> None:
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired) as error:
            cleanup_errors.append(f"{stage}: {error}")

    if poll() is not None:
        return
    try:
        process.terminate()
    except OSError as error:
        cleanup_errors.append(f"terminate: {error}")
    wait("terminate wait")
    if poll() is not None:
        return

    try:
        process.kill()
    except OSError as error:
        cleanup_errors.append(f"kill: {error}")
    wait("kill wait")
    if poll() is not None:
        return

    detail = "; ".join(cleanup_errors) or "进程在 terminate/kill 后仍报告存活"
    raise MediaNodeError(
        "E_MEDIA_FFMPEG_CLEANUP_FAILED",
        f"FFmpeg producer 无法确认回收：{detail}",
    ) from cause


def _copy_stream_with_progress(
    context: PythonAdapterContext,
    source: BinaryIO,
    target: BinaryIO,
    *,
    total: int,
) -> None:
    """逐块复制并按已读取的源字节数上报；源大小漂移时失败关闭。"""

    current = 0
    while chunk := source.read(1024 * 1024):
        written = target.write(chunk)
        if written != len(chunk):
            raise MediaNodeError("E_MEDIA_OUTPUT_WRITE_INCOMPLETE", "目标文件发生短写")
        current += written
        if current > total:
            raise MediaNodeError("E_MEDIA_OUTPUT_SOURCE_CHANGED", "复制期间源文件大小增加")
        _report_progress(context, current=current, total=total, unit="bytes")
    if current != total:
        raise MediaNodeError("E_MEDIA_OUTPUT_SOURCE_CHANGED", "复制期间源文件大小减少")


def _single_input(context: PythonAdapterContext, port_id: str) -> RunnerInput:
    values = tuple(item for item in context.inputs if item.port_id == port_id)
    if len(values) != 1:
        raise MediaNodeError("E_MEDIA_INPUT_COUNT", f"{port_id} 必须精确绑定一个输入")
    return values[0]


def _single_output(context: PythonAdapterContext, port_id: str | None = None) -> OutputTarget:
    values = (
        context.outputs
        if port_id is None
        else tuple(item for item in context.outputs if item.port_id == port_id)
    )
    if len(values) != 1:
        raise MediaNodeError("E_MEDIA_OUTPUT_COUNT", "节点必须精确声明一个目标输出")
    return values[0]


def _cleanup_outputs(outputs: tuple[OutputTarget, ...]) -> None:
    """只删除 Runner 已验证位于本 attempt 内的声明输出文件。"""

    for output in outputs:
        with suppress(OSError):
            if output.path.is_file() or output.path.is_symlink():
                output.path.unlink()


def _append_log(path: Path, message: str) -> None:
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(message)
    except OSError as error:
        raise MediaNodeError("E_MEDIA_LOG_WRITE_FAILED", str(error)) from error


def _nonempty_file(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
    except OSError as error:
        raise MediaNodeError("E_MEDIA_INPUT_UNREADABLE", str(error)) from error
    if not resolved.is_file() or stat.st_size <= 0:
        raise MediaNodeError("E_MEDIA_INPUT_UNREADABLE", f"不是非空常规文件：{path}")
    return resolved


def _required_string(parameters: Mapping[str, object], name: str) -> str:
    value = parameters.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MediaNodeError("E_MEDIA_PARAMETER_INVALID", f"{name} 必须是非空 string")
    return value


def _optional_string(parameters: Mapping[str, object], name: str, default: str) -> str:
    if name not in parameters:
        return default
    return _required_string(parameters, name)


def _required_boolean(parameters: Mapping[str, object], name: str) -> bool:
    value = parameters.get(name)
    if type(value) is not bool:
        raise MediaNodeError("E_MEDIA_PARAMETER_INVALID", f"{name} 必须是 boolean")
    return value


def _required_integer(
    parameters: Mapping[str, object],
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MediaNodeError("E_MEDIA_PARAMETER_INVALID", f"{name} 必须在 {minimum}..{maximum} 内")
    return value


def _optional_integer(
    parameters: Mapping[str, object],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if name not in parameters:
        return default
    return _required_integer(parameters, name, minimum=minimum, maximum=maximum)


def _required_fraction(parameters: Mapping[str, object], name: str) -> Fraction:
    value = _required_string(parameters, name)
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise MediaNodeError("E_MEDIA_PARAMETER_INVALID", f"{name} 不是合法帧率") from error
    if result <= 0:
        raise MediaNodeError("E_MEDIA_PARAMETER_INVALID", f"{name} 必须为正数")
    return result
