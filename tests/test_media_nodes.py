"""验证 Phase 4 媒体节点目录、帧守恒、轻量 probe 与发布边界。

测试只生成一秒合成媒体；真实长片不进入测试目录或 Git。所有 FFmpeg/FFprobe 调用均使用 argv 和
``shell=False``，失败 attempt 不产生可登记的部分 Artifact。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import JsonValue

import zniku.media.adapters as media_adapters_module
from zniku.graph import Edge, ExecutionMode, Graph, GraphValidator, NodeInstance
from zniku.media import (
    MediaNodeError,
    automatic_video_transform_definition,
    built_in_media_definitions,
    encode_video_definition,
    exact_video_frame_count,
    external_video_transform_definition,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    merge_video_definition,
    mux_media_definition,
    output_file_definition,
    parse_segments,
    probe_media,
    runner_media_probe,
    source_media_definition,
    split_video_definition,
)
from zniku.project import Project, ProjectStore
from zniku.runtime import (
    Artifact,
    FrameRange,
    ManualSubmission,
    NodeExecutionRequest,
    NodeRunner,
    ProducedOutput,
    PythonAdapterResult,
    RunnerError,
    RunnerFailureReason,
    RunnerInput,
    RuntimeService,
)
from zniku.runtime.runner import OutputTarget

ROOT = Path(__file__).parents[1]


class RecordingProgress:
    """记录 adapter 上报的原始测量值，不替代 Runtime Reporter 合同测试。"""

    def __init__(self) -> None:
        self.samples: list[tuple[float, int | float | None, int | float | None, str | None]] = []

    def report(
        self,
        *,
        fraction: float,
        current: int | float | None = None,
        total: int | float | None = None,
        unit: str | None = None,
    ) -> None:
        self.samples.append((fraction, current, total, unit))


def _progress_context(tmp_path: Path, reporter: RecordingProgress) -> Any:
    work_dir = tmp_path / "adapter-progress"
    work_dir.mkdir(parents=True)
    stdout_log_path = work_dir / "stdout.log"
    stderr_log_path = work_dir / "stderr.log"
    stdout_log_path.touch()
    stderr_log_path.touch()
    return SimpleNamespace(
        work_dir=work_dir,
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
        progress=reporter,
    )


def _request(
    definition: Any,
    *,
    parameters: dict[str, JsonValue] | None = None,
    inputs: tuple[RunnerInput, ...] = (),
) -> NodeExecutionRequest:
    node = NodeInstance(
        node_id=f"node-{uuid4()}",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=parameters or {},
    )
    return NodeExecutionRequest(
        node_run_id=str(uuid4()),
        attempt=1,
        definition=definition,
        node=node,
        inputs=inputs,
    )


def _runner(work_root: Path) -> NodeRunner:
    return NodeRunner(
        work_root,
        python_adapters=media_python_adapters(),
        validators=media_validators(),
        media_probe=runner_media_probe,
    )


def _input_from_artifact(
    port_id: str,
    artifact: Any,
    *,
    ordinal: int | None = None,
) -> RunnerInput:
    return RunnerInput(
        port_id=port_id,
        artifact_id=artifact.artifact_id,
        kind=artifact.kind,
        path=artifact.path,
        ordinal=ordinal,
    )


def _require_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("Phase 4 短媒体测试需要 FFmpeg/FFprobe")
    return ffmpeg, ffprobe


def _run_tool(argv: list[str]) -> None:
    completed = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


@pytest.fixture
def synthetic_media(tmp_path: Path) -> tuple[Path, Path]:
    ffmpeg, _ffprobe = _require_tools()
    video = tmp_path / "synthetic-video.mkv"
    audio = tmp_path / "synthetic-audio.mka"
    _run_tool(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=12",
            "-frames:v",
            "12",
            "-an",
            "-c:v",
            "ffv1",
            "-f",
            "matroska",
            str(video),
        ]
    )
    _run_tool(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=1",
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-f",
            "matroska",
            str(audio),
        ]
    )
    return video, audio


def test_builtin_catalog_is_typed_versioned_and_has_distinct_external_presets() -> None:
    definitions = built_in_media_definitions()
    identities = {(item.type_id, item.version) for item in definitions}

    assert len(identities) == len(definitions)
    assert {item.version for item in definitions} == {"0.2.0"}
    assert {
        "zniku.media.video_transform.mr.external",
        "zniku.media.video_transform.enhancement.external",
        "zniku.media.video_transform.fi.external",
    }.issubset({item.type_id for item in definitions})
    assert all(
        item.execution_mode is ExecutionMode.MANUAL_EXTERNAL
        for item in definitions
        if item.type_id.endswith(".external")
    )
    merge = merge_video_definition()
    assert merge.input_ports[0].cardinality.value == "ordered_many"
    assert merge.input_ports[0].data_type == "VideoFile"


def test_parameter_schemas_reject_missing_source_and_copy_target() -> None:
    source = source_media_definition()
    output = output_file_definition()
    split = split_video_definition()
    source_graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id=source.type_id,
                definition_version=source.version,
                parameters={},
            ),
        )
    )
    output_graph = Graph(
        nodes=(
            NodeInstance(
                node_id="output",
                type_id=output.type_id,
                definition_version=output.version,
                parameters={"mode": "copy", "overwrite": False},
            ),
        )
    )
    split_graph = Graph(
        nodes=(
            NodeInstance(
                node_id="split",
                type_id=split.type_id,
                definition_version=split.version,
                parameters={},
            ),
        )
    )

    source_codes = {item.code for item in GraphValidator((source,)).inspect(source_graph)}
    output_codes = {item.code for item in GraphValidator((output,)).inspect(output_graph)}
    split_codes = {item.code for item in GraphValidator((split,)).inspect(split_graph)}
    assert "E_PARAMETERS_INVALID" in source_codes
    assert "E_PARAMETERS_INVALID" in output_codes
    assert "E_PARAMETERS_INVALID" in split_codes

    transform = automatic_video_transform_definition()
    transform_graph = Graph(
        nodes=(
            NodeInstance(
                node_id="transform",
                type_id=transform.type_id,
                definition_version=transform.version,
                parameters={"operation": "scale", "width": 1920},
            ),
        )
    )
    transform_codes = {item.code for item in GraphValidator((transform,)).inspect(transform_graph)}
    assert "E_PARAMETERS_INVALID" in transform_codes


def test_probe_falls_back_from_zero_avg_rate_and_uses_shell_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "probe.mkv"
    source.write_bytes(b"synthetic")
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "0/0",
                "r_frame_rate": "30000/1001",
                "nb_frames": "12",
            }
        ],
        "format": {"format_name": "matroska", "duration": "1.0"},
    }
    calls: list[dict[str, object]] = []

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(dict(kwargs))
        return subprocess.CompletedProcess([], 0, json.dumps(payload), "")

    monkeypatch.setattr("zniku.media.probe.subprocess.run", fake_run)
    info = probe_media(source, ffprobe_executable="ffprobe")

    assert str(info.video_streams[0].frame_rate) == "30000/1001"
    assert calls == [
        {
            "stdin": subprocess.DEVNULL,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": False,
            "check": False,
            "timeout": 60,
        }
    ]


def test_ffmpeg_progress_parser_only_accepts_machine_blocks_and_split_offset(
    tmp_path: Path,
) -> None:
    reporter = RecordingProgress()
    context = _progress_context(tmp_path, reporter)
    fields: dict[bytes, bytes] = {}
    spec = media_adapters_module._FFmpegProgressSpec(
        field="frame",
        unit="frames",
        total=10,
        offset=4,
        extent=6,
    )

    media_adapters_module._consume_ffmpeg_progress(
        context,
        b"frame: 999 fps=99 human text\n",
        fields=fields,
        progress_spec=spec,
    )
    media_adapters_module._consume_ffmpeg_progress(
        context,
        b"progress=continue\n",
        fields=fields,
        progress_spec=spec,
    )
    assert reporter.samples == []

    media_adapters_module._consume_ffmpeg_progress(
        context,
        b"frame=2\n",
        fields=fields,
        progress_spec=spec,
    )
    media_adapters_module._consume_ffmpeg_progress(
        context,
        b"progress=continue\n",
        fields=fields,
        progress_spec=spec,
    )
    assert reporter.samples == [(0.6, 6, 10, "frames")]

    media_adapters_module._consume_ffmpeg_progress(
        context,
        b"frame=10\n",
        fields=fields,
        progress_spec=None,
    )
    media_adapters_module._consume_ffmpeg_progress(
        context,
        b"progress=end\n",
        fields=fields,
        progress_spec=None,
    )
    assert reporter.samples == [(0.6, 6, 10, "frames")]


def test_frame_rate_transform_and_mux_remain_indeterminate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = RecordingProgress()
    observed_specs: list[object] = []

    def fake_run_ffmpeg(
        _context: Any,
        _argv: Any,
        *,
        progress_spec: object = None,
    ) -> None:
        observed_specs.append(progress_spec)

    monkeypatch.setattr(media_adapters_module, "_run_ffmpeg", fake_run_ffmpeg)
    transform_context = _progress_context(tmp_path / "transform", reporter)
    transform_context.node = SimpleNamespace(
        parameters={"operation": "frame_rate", "frame_rate": "24/1"}
    )
    transform_context.inputs = (
        RunnerInput("video", "artifact.video", "VideoFile", tmp_path / "input.mkv"),
    )
    transform_context.outputs = (
        OutputTarget("video", "VideoFile", transform_context.work_dir / "output.mkv"),
    )
    media_adapters_module.video_transform(transform_context)

    mux_context = _progress_context(tmp_path / "mux", reporter)
    mux_context.node = SimpleNamespace(parameters={})
    mux_context.inputs = (
        RunnerInput("video", "artifact.video", "VideoFile", tmp_path / "input.mkv"),
    )
    mux_context.outputs = (OutputTarget("media", "MediaFile", mux_context.work_dir / "output.mkv"),)
    media_adapters_module.mux_media(mux_context)

    assert observed_specs == [None, None]
    assert reporter.samples == []


def test_output_copy_reports_exact_chunk_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """快速复制只合并展示采样，仍报告首个字节样本与精确 EOF。"""

    monkeypatch.setattr(media_adapters_module, "monotonic", lambda: 1.0)
    reporter = RecordingProgress()
    context = _progress_context(tmp_path, reporter)
    payload = b"x" * (2 * 1024 * 1024 + 17)
    target = BytesIO()

    media_adapters_module._copy_stream_with_progress(
        context,
        BytesIO(payload),
        target,
        total=len(payload),
    )

    assert target.getvalue() == payload
    assert [sample[1] for sample in reporter.samples] == [
        1024 * 1024,
        len(payload),
    ]
    assert all(sample[2:] == (len(payload), "bytes") for sample in reporter.samples)
    assert [sample[0] for sample in reporter.samples] == sorted(
        sample[0] for sample in reporter.samples
    )


def test_output_copy_timed_samples_and_source_drift_remain_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter = RecordingProgress()
    context = _progress_context(tmp_path, reporter)
    times = iter((0.0, 0.1, 0.3, 0.4, 0.7))
    monkeypatch.setattr(media_adapters_module, "monotonic", lambda: next(times))
    payload = b"x" * (4 * 1024 * 1024 + 1)
    media_adapters_module._copy_stream_with_progress(
        context, BytesIO(payload), BytesIO(), total=len(payload)
    )
    assert [sample[1] for sample in reporter.samples] == [
        1024 * 1024,
        3 * 1024 * 1024,
        len(payload),
    ]
    monkeypatch.setattr(media_adapters_module, "monotonic", lambda: 0.0)
    for expected_size in (len(payload) - 1, len(payload) + 1):
        with pytest.raises(MediaNodeError, match="E_MEDIA_OUTPUT_SOURCE_CHANGED"):
            media_adapters_module._copy_stream_with_progress(
                context, BytesIO(payload), BytesIO(), total=expected_size
            )


def test_output_copy_short_write_never_reports_success(tmp_path: Path) -> None:
    class ShortWriter(BytesIO):
        def write(self, value: Any) -> int:
            return super().write(value[:-1])

    reporter = RecordingProgress()
    context = _progress_context(tmp_path, reporter)
    with pytest.raises(MediaNodeError, match="E_MEDIA_OUTPUT_WRITE_INCOMPLETE"):
        media_adapters_module._copy_stream_with_progress(
            context, BytesIO(b"synthetic"), ShortWriter(), total=9
        )
    assert reporter.samples == []


def test_ffmpeg_reporter_failure_terminates_then_kills_stubborn_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingProgress(RecordingProgress):
        def report(
            self,
            *,
            fraction: float,
            current: int | float | None = None,
            total: int | float | None = None,
            unit: str | None = None,
        ) -> None:
            raise RuntimeError("synthetic reporter failure")

    class StubbornProcess:
        def __init__(self) -> None:
            self.stdout = BytesIO(b"frame=1\nprogress=continue\n")
            self.terminate_called = False
            self.kill_called = False
            self.wait_timeouts: list[float | None] = []
            self.return_code: int | None = None

        def poll(self) -> int | None:
            return self.return_code

        def terminate(self) -> None:
            self.terminate_called = True

        def kill(self) -> None:
            self.kill_called = True

        def wait(self, timeout: float | None = None) -> int:
            self.wait_timeouts.append(timeout)
            if len(self.wait_timeouts) == 1:
                assert timeout is not None
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            self.return_code = -9
            return -9

    reporter = RejectingProgress()
    context = _progress_context(tmp_path, reporter)
    process = StubbornProcess()
    monkeypatch.setattr(media_adapters_module, "resolve_media_tool", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        "zniku.media.adapters.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )

    with pytest.raises(RuntimeError, match="synthetic reporter failure"):
        media_adapters_module._run_ffmpeg(
            context,
            ["-i", "synthetic", "output.mkv"],
            progress_spec=media_adapters_module._FFmpegProgressSpec(
                field="frame",
                unit="frames",
                total=10,
            ),
        )

    assert process.terminate_called is True
    assert process.kill_called is True
    assert process.wait_timeouts == [2, 2]


@pytest.mark.parametrize("kill_raises", [False, True])
def test_ffmpeg_cleanup_failure_preserves_original_cause_when_process_stays_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    kill_raises: bool,
) -> None:
    original = RuntimeError("synthetic reporter failure")

    class RejectingProgress(RecordingProgress):
        def report(
            self,
            *,
            fraction: float,
            current: int | float | None = None,
            total: int | float | None = None,
            unit: str | None = None,
        ) -> None:
            raise original

    class UnreapableProcess:
        def __init__(self) -> None:
            self.stdout = BytesIO(b"frame=1\nprogress=continue\n")
            self.terminate_called = False
            self.kill_called = False
            self.poll_calls = 0
            self.wait_timeouts: list[float | None] = []

        def poll(self) -> None:
            self.poll_calls += 1
            return None

        def terminate(self) -> None:
            self.terminate_called = True

        def kill(self) -> None:
            self.kill_called = True
            if kill_raises:
                raise OSError("synthetic kill failure")

        def wait(self, timeout: float | None = None) -> int:
            self.wait_timeouts.append(timeout)
            assert timeout is not None
            raise subprocess.TimeoutExpired("ffmpeg", timeout)

    reporter = RejectingProgress()
    context = _progress_context(tmp_path, reporter)
    process = UnreapableProcess()
    monkeypatch.setattr(media_adapters_module, "resolve_media_tool", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        "zniku.media.adapters.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )

    with pytest.raises(MediaNodeError) as captured:
        media_adapters_module._run_ffmpeg(
            context,
            ["-i", "synthetic", "output.mkv"],
            progress_spec=media_adapters_module._FFmpegProgressSpec(
                field="frame",
                unit="frames",
                total=10,
            ),
        )

    assert captured.value.code == "E_MEDIA_FFMPEG_CLEANUP_FAILED"
    assert captured.value.__cause__ is original
    assert "kill" in str(captured.value)
    assert process.terminate_called is True
    assert process.kill_called is True
    assert process.poll_calls == 3
    assert process.wait_timeouts == [2, 2]


def test_real_slow_ffmpeg_emits_multiple_machine_progress_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg, _ffprobe = _require_tools()
    reporter = RecordingProgress()
    context = _progress_context(tmp_path, reporter)
    target = context.work_dir / "slow-progress.mkv"
    popen_calls: list[tuple[list[str], dict[str, object]]] = []
    real_popen = subprocess.Popen

    def spy_popen(command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        popen_calls.append((command, dict(kwargs)))
        return real_popen(command, **kwargs)

    monkeypatch.setattr(media_adapters_module, "resolve_media_tool", lambda _name: ffmpeg)
    monkeypatch.setattr("zniku.media.adapters.subprocess.Popen", spy_popen)
    media_adapters_module._run_ffmpeg(
        context,
        [
            "-stats_period",
            "0.1",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=10:duration=2.4",
            "-an",
            "-c:v",
            "ffv1",
            "-f",
            "matroska",
            str(target),
        ],
        progress_spec=media_adapters_module._FFmpegProgressSpec(
            field="frame",
            unit="frames",
            total=24,
        ),
    )

    assert target.is_file()
    intermediate = [sample for sample in reporter.samples if 0.0 < sample[0] < 1.0]
    assert len(intermediate) >= 2
    assert [sample[0] for sample in reporter.samples] == sorted(
        sample[0] for sample in reporter.samples
    )
    assert all(sample[2:] == (24, "frames") for sample in reporter.samples)
    assert len(popen_calls) == 1
    command, options = popen_calls[0]
    assert command[command.index("-progress") : command.index("-progress") + 2] == [
        "-progress",
        "pipe:1",
    ]
    assert "-nostats" in command
    assert options["shell"] is False
    assert options["stdout"] is subprocess.PIPE
    assert options["stderr"] is not subprocess.PIPE
    assert "progress=end" in context.stdout_log_path.read_text(encoding="utf-8")


def test_short_media_chain_preserves_split_and_merge_counts_and_publishes(
    tmp_path: Path,
    synthetic_media: tuple[Path, Path],
) -> None:
    source_video, source_audio = synthetic_media
    runner = _runner(tmp_path / "work")

    source_result = runner.run_automatic(
        _request(
            source_media_definition("VideoFile"),
            parameters={"source_path": str(source_video.resolve())},
        )
    )
    source_artifact = source_result.artifacts[0]
    assert source_artifact.path == source_video.resolve()

    transformed = runner.run_automatic(
        _request(
            automatic_video_transform_definition(),
            parameters={"operation": "identity"},
            inputs=(_input_from_artifact("video", source_artifact),),
        )
    )
    assert exact_video_frame_count(transformed.artifacts[0].path) == 12

    split_definition = split_video_definition()
    split = runner.run_automatic(
        _request(
            split_definition,
            parameters={
                "segments": [
                    {"port_id": "A", "start_frame": 0, "end_frame": 5},
                    {"port_id": "B", "start_frame": 5, "end_frame": 12},
                ]
            },
            inputs=(_input_from_artifact("video", transformed.artifacts[0]),),
        )
    )
    assert [artifact.frame_range for artifact in split.artifacts] == [
        FrameRange(start_frame=0, end_frame=5),
        FrameRange(start_frame=5, end_frame=12),
    ]
    assert [exact_video_frame_count(item.path) for item in split.artifacts] == [5, 7]

    merged = runner.run_automatic(
        _request(
            merge_video_definition(),
            inputs=tuple(
                _input_from_artifact("videos", artifact, ordinal=index)
                for index, artifact in enumerate(split.artifacts)
            ),
        )
    )
    assert exact_video_frame_count(merged.artifacts[0].path) == 12

    encoded = runner.run_automatic(
        _request(
            encode_video_definition(),
            parameters={
                "codec": "ffv1",
                "pixel_format": "yuv420p",
                "require_frame_count_equal": True,
            },
            inputs=(_input_from_artifact("video", merged.artifacts[0]),),
        )
    )
    audio_result = runner.run_automatic(
        _request(
            source_media_definition("AudioFile"),
            parameters={"source_path": str(source_audio.resolve())},
        )
    )
    muxed = runner.run_automatic(
        _request(
            mux_media_definition(),
            inputs=(
                _input_from_artifact("video", encoded.artifacts[0]),
                _input_from_artifact("audio", audio_result.artifacts[0], ordinal=0),
            ),
        )
    )
    mux_info = probe_media(muxed.artifacts[0].path)
    assert len(mux_info.video_streams) == 1
    assert len(mux_info.audio_streams) == 1

    published = tmp_path / "published.mkv"
    output_result = runner.run_automatic(
        _request(
            output_file_definition("MediaFile"),
            parameters={
                "mode": "copy",
                "target_path": str(published.resolve()),
                "overwrite": False,
            },
            inputs=(_input_from_artifact("in", muxed.artifacts[0]),),
        )
    )
    assert len(output_result.artifacts) == 1
    assert output_result.artifacts[0].path == published.resolve()
    assert published.is_file()
    assert output_result.media_summary["adapter"] == {
        "published_path": str(published.resolve()),
        "mode": "copy",
        "overwrite": False,
        "size": published.stat().st_size,
    }
    assert muxed.artifacts[0].path.is_file(), "OutputFile 不得移动上游 Artifact"


def test_output_reference_and_explicit_overwrite_contract(
    tmp_path: Path,
    synthetic_media: tuple[Path, Path],
) -> None:
    source_video, _source_audio = synthetic_media
    runner = _runner(tmp_path / "work")
    source = runner.run_automatic(
        _request(
            source_media_definition("VideoFile"),
            parameters={"source_path": str(source_video.resolve())},
        )
    ).artifacts[0]

    referenced = runner.run_automatic(
        _request(
            output_file_definition("VideoFile"),
            parameters={"mode": "reference", "overwrite": False},
            inputs=(_input_from_artifact("in", source),),
        )
    )
    assert referenced.media_summary["adapter"] == {
        "published_path": str(source_video.resolve()),
        "mode": "reference",
        "overwrite": False,
    }
    assert len(referenced.artifacts) == 1
    assert referenced.artifacts[0].path == source_video.resolve()

    target = tmp_path / "existing.mkv"
    target.write_bytes(b"existing")
    with pytest.raises(RunnerError) as captured:
        runner.run_automatic(
            _request(
                output_file_definition("VideoFile"),
                parameters={
                    "mode": "copy",
                    "target_path": str(target.resolve()),
                    "overwrite": False,
                },
                inputs=(_input_from_artifact("in", source),),
            )
        )
    assert captured.value.reason is RunnerFailureReason.ADAPTER_FAILED
    assert target.read_bytes() == b"existing"

    overwritten = runner.run_automatic(
        _request(
            output_file_definition("VideoFile"),
            parameters={
                "mode": "copy",
                "target_path": str(target.resolve()),
                "overwrite": True,
            },
            inputs=(_input_from_artifact("in", source),),
        )
    )
    adapter_summary = overwritten.media_summary["adapter"]
    assert isinstance(adapter_summary, Mapping)
    assert adapter_summary["overwrite"] is True
    assert probe_media(target).video_streams


def test_deleted_published_artifact_invalidates_output_reuse(
    tmp_path: Path,
    synthetic_media: tuple[Path, Path],
) -> None:
    """发布结果是普通 Artifact；文件缺失时不得真空复用旧 OutputFile。"""

    source_video, _source_audio = synthetic_media
    target = tmp_path / "published-for-reuse.mkv"
    source_definition = source_media_definition("VideoFile")
    output_definition = output_file_definition("VideoFile")
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id=source_definition.type_id,
                definition_version=source_definition.version,
                parameters={"source_path": str(source_video.resolve())},
            ),
            NodeInstance(
                node_id="output",
                type_id=output_definition.type_id,
                definition_version=output_definition.version,
                parameters={
                    "mode": "copy",
                    "target_path": str(target.resolve()),
                    "overwrite": False,
                },
            ),
        ),
        edges=(
            Edge(
                source_node_id="source",
                source_port_id="out",
                target_node_id="output",
                target_port_id="in",
            ),
        ),
    )
    store = ProjectStore.create(
        tmp_path / "reuse.zniku",
        Project(project_id="test.media-reuse", name="媒体发布复用", graph=graph),
        (source_definition, output_definition),
    )
    service = RuntimeService(
        store,
        tmp_path / "reuse-work",
        python_adapters=media_python_adapters(),
        validators=media_validators(),
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )
    first = service.run_until_blocked(service.create_run().run_id)
    first_output = next(item for item in first.node_runs if item.node_id == "output")
    assert target.is_file()
    target.unlink()

    second = service.run_until_blocked(service.create_run().run_id)
    second_source = next(item for item in second.node_runs if item.node_id == "source")
    second_output = next(item for item in second.node_runs if item.node_id == "output")

    assert second_source.reused_from_result_id is not None
    assert second_output.reused_from_result_id is None
    assert second_output.output_artifact_ids != first_output.output_artifact_ids
    assert target.is_file()


def test_smoke_project_tool_creates_valid_free_dag(
    tmp_path: Path,
    synthetic_media: tuple[Path, Path],
) -> None:
    """README 公开的 positional 工具接口必须真正创建可编辑的普通 DAG。"""

    source_video, _source_audio = synthetic_media
    project_path = tmp_path / "smoke" / "phase4.zniku"
    output_directory = tmp_path / "smoke-output"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "create_media_smoke_project.py"),
            str(project_path),
            str(source_video),
            str(output_directory),
            "--split-frame",
            "5",
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    snapshot = ProjectStore.open(project_path).load()
    assert GraphValidator(snapshot.definitions).inspect(snapshot.project.graph) == ()
    assert len(snapshot.project.graph.nodes) == 8
    assert (
        sum(
            1
            for node in snapshot.project.graph.nodes
            if node.type_id.startswith("zniku.media.output_file.")
        )
        == 2
    )


def test_split_rejects_gaps_and_cleans_only_attempt_outputs_on_failure(
    tmp_path: Path,
    synthetic_media: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_video, _source_audio = synthetic_media
    definition = split_video_definition()
    source_input = RunnerInput("video", "artifact-source", "VideoFile", source_video)

    with pytest.raises(RunnerError) as gap_error:
        _runner(tmp_path / "gap").run_automatic(
            _request(
                definition,
                parameters={
                    "segments": [
                        {"port_id": "A", "start_frame": 0, "end_frame": 5},
                        {"port_id": "B", "start_frame": 6, "end_frame": 12},
                    ]
                },
                inputs=(source_input,),
            )
        )
    assert gap_error.value.reason is RunnerFailureReason.ADAPTER_FAILED

    real_run = media_adapters_module._run_ffmpeg
    calls = 0

    def fail_second(
        context: Any,
        argv: Any,
        *,
        progress_spec: Any = None,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise MediaNodeError("E_TEST_SECOND_SEGMENT", "synthetic second segment failure")
        real_run(context, argv, progress_spec=progress_spec)

    monkeypatch.setattr(media_adapters_module, "_run_ffmpeg", fail_second)
    work_root = tmp_path / "partial"
    with pytest.raises(RunnerError):
        _runner(work_root).run_automatic(
            _request(
                definition,
                parameters={
                    "segments": [
                        {"port_id": "A", "start_frame": 0, "end_frame": 5},
                        {"port_id": "B", "start_frame": 5, "end_frame": 12},
                    ]
                },
                inputs=(source_input,),
            )
        )
    attempt = next(work_root.iterdir())
    assert not tuple((attempt / "outputs").glob("*"))
    assert source_video.is_file()


def test_split_validator_rejects_drifted_artifact_frame_range(
    tmp_path: Path,
    synthetic_media: tuple[Path, Path],
) -> None:
    source_video, _source_audio = synthetic_media
    definition = split_video_definition()

    def wrong_frame_range(context: Any) -> PythonAdapterResult:
        result = media_adapters_module.split_video(context)
        outputs = tuple(
            ProducedOutput(
                item.port_id,
                item.path,
                frame_range=(
                    FrameRange(start_frame=0, end_frame=4)
                    if item.port_id == "A"
                    else item.frame_range
                ),
            )
            for item in result.outputs
        )
        return PythonAdapterResult(
            outputs=outputs,
            media_summary=result.media_summary,
            validation_summary=result.validation_summary,
        )

    adapters = dict(media_python_adapters())
    adapters["zniku.media.adapters:split_video"] = wrong_frame_range
    runner = NodeRunner(
        tmp_path / "work",
        python_adapters=adapters,
        validators=media_validators(),
        media_probe=runner_media_probe,
    )
    with pytest.raises(RunnerError) as captured:
        runner.run_automatic(
            _request(
                definition,
                parameters={
                    "segments": [
                        {"port_id": "A", "start_frame": 0, "end_frame": 5},
                        {"port_id": "B", "start_frame": 5, "end_frame": 12},
                    ]
                },
                inputs=(RunnerInput("video", "artifact-source", "VideoFile", source_video),),
            )
        )
    assert captured.value.code == "E_RUNNER_VALIDATION_REJECTED"
    assert "E_MEDIA_SPLIT_FRAME_RANGE" in str(captured.value)


def test_manual_fi_constraint_rejects_same_frame_count_without_artifact(
    tmp_path: Path,
    synthetic_media: tuple[Path, Path],
) -> None:
    source_video, _source_audio = synthetic_media
    definition = external_video_transform_definition("fi")
    request = _request(
        definition,
        parameters={"tool": "synthetic", "model": "none", "tool_version": "1.0"},
        inputs=(RunnerInput("video", "artifact-source", "VideoFile", source_video),),
    )
    runner = _runner(tmp_path / "manual")
    handoff = runner.prepare_manual(request)
    shutil.copy2(source_video, handoff.outputs[0].path)

    with pytest.raises(RunnerError) as captured:
        runner.submit_manual(request, handoff, ManualSubmission())
    assert captured.value.code == "E_RUNNER_VALIDATION_REJECTED"
    assert captured.value.reason is RunnerFailureReason.VALIDATOR_FAILED
    assert not hasattr(captured.value, "artifacts")

    enhancement = external_video_transform_definition("enhancement")
    enhancement_request = _request(
        enhancement,
        parameters={
            "tool": "synthetic",
            "model": "identity-fixture",
            "tool_version": "1.0",
        },
        inputs=(RunnerInput("video", "artifact-source", "VideoFile", source_video),),
    )
    enhancement_runner = _runner(tmp_path / "enhancement")
    enhancement_handoff = enhancement_runner.prepare_manual(enhancement_request)
    shutil.copy2(source_video, enhancement_handoff.outputs[0].path)
    completed = enhancement_runner.submit_manual(
        enhancement_request,
        enhancement_handoff,
        ManualSubmission(),
    )
    assert len(completed.artifacts) == 1
    assert completed.artifacts[0].kind == "VideoFile"


def test_runner_probe_and_artifact_quick_probe_fail_closed(
    tmp_path: Path,
    synthetic_media: tuple[Path, Path],
) -> None:
    source_video, _source_audio = synthetic_media
    summary = runner_media_probe(source_video, "VideoFile")
    assert summary["video_streams"]

    data = tmp_path / "data.bin"
    data.write_bytes(b"x")
    artifact = Artifact(
        artifact_id=str(uuid4()),
        kind="DataFile",
        path=str(data.resolve()),
        producer_node_run_id=str(uuid4()),
        producer_port_id="out",
        size=1,
        mtime_ns=data.stat().st_mtime_ns,
    )
    assert media_artifact_quick_probe(artifact)
    data.write_bytes(b"changed")
    assert not media_artifact_quick_probe(artifact)
    data.write_bytes(b"")
    assert not media_artifact_quick_probe(artifact)

    with pytest.raises(MediaNodeError) as wrong_kind:
        runner_media_probe(source_video, "AudioFile")
    assert wrong_kind.value.code == "E_MEDIA_AUDIO_STREAM_MISSING"


def test_segment_parser_rejects_unknown_fields_and_requires_custom_type_id() -> None:
    with pytest.raises(MediaNodeError) as captured:
        parse_segments(
            [
                {"port_id": "A", "start_frame": 0, "end_frame": 1, "unknown": True},
                {"port_id": "B", "start_frame": 1, "end_frame": 2},
            ],
            expected_ports=("A", "B"),
            input_frames=2,
        )
    assert captured.value.code == "E_MEDIA_SPLIT_SEGMENT_FIELDS"

    with pytest.raises(ValueError, match="E_MEDIA_SPLIT_TYPE_ID_REQUIRED"):
        split_video_definition(("first", "second"))


def test_adapter_mapping_contains_only_callables() -> None:
    adapters = media_python_adapters()
    validators = media_validators()
    assert adapters
    assert validators
    assert all(callable(item) for item in (*adapters.values(), *validators.values()))
    assert all(not isinstance(item, str) for item in adapters.values())
    assert ProducedOutput("out", Path("relative.out")).allow_external is False
