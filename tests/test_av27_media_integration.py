"""用极短合成媒体验证 AVEnhanceFlow v2.7 Program 与 Final 的真实 FFmpeg 边界。

这些测试只在临时目录生成媒体，并通过 ``NodeRunner``、正式 adapter 注册表、统一轻量
``runner_media_probe`` 与节点 validator 走完整验收路径。缺少 FFmpeg、FFprobe、ProRes 或
libx265 Main10 时显式 skip；测试不读取真实媒体，也不把临时产物写入仓库。
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import JsonValue

import zniku.avenhance_v27.adapters as av27_adapters
from zniku.avenhance_v27 import (
    av27_python_adapters,
    av27_validators,
    final_mux_definition,
    program_encode_definition,
    source_admission_definition,
    source_program_definition,
)
from zniku.avenhance_v27.probe import AV27_NAMESPACE, probe_header
from zniku.graph import NodeDefinition, NodeInstance, PythonExecutorSpec
from zniku.media import runner_media_probe
from zniku.runtime import (
    NodeExecutionRequest,
    NodeRunner,
    OutputPathSpec,
    RunnerArtifact,
    RunnerInput,
    RunnerResult,
)


def _require_media_tools() -> str:
    """返回 FFmpeg 路径，并在当前节点不具备所需 encoder 时跳过。"""

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("AV27 极短媒体集成测试需要 FFmpeg 与 FFprobe")
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        check=False,
    )
    encoders = completed.stdout.decode("utf-8", errors="replace")
    if completed.returncode != 0 or "prores_ks" not in encoders or "libx265" not in encoders:
        pytest.skip("当前 FFmpeg 缺少 prores_ks 或 libx265 encoder")
    return ffmpeg


def _run_tool(argv: Sequence[str]) -> None:
    """运行一个只写临时目录的合成媒体命令。"""

    completed = subprocess.run(
        list(argv),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def _executor_output_paths(definition: NodeDefinition) -> tuple[OutputPathSpec, ...]:
    """把定义内的受控相对路径映射到直接 ``NodeRunner`` 请求。"""

    executor = definition.executor
    assert isinstance(executor, PythonExecutorSpec)
    return tuple(
        OutputPathSpec(port_id=item.port_id, relative_path=item.relative_path)
        for item in executor.output_paths
    )


def _request(
    definition: NodeDefinition,
    *,
    parameters: dict[str, JsonValue],
    inputs: tuple[RunnerInput, ...] = (),
) -> NodeExecutionRequest:
    """构造绑定精确 ``0.2.1`` 定义与声明输出路径的独立 attempt。"""

    return NodeExecutionRequest(
        node_run_id=str(uuid4()),
        attempt=1,
        definition=definition,
        node=NodeInstance(
            node_id=f"node-{uuid4()}",
            type_id=definition.type_id,
            definition_version=definition.version,
            parameters=parameters,
        ),
        inputs=inputs,
        output_paths=_executor_output_paths(definition),
    )


def _runner(work_root: Path) -> NodeRunner:
    """使用正式 AV27 注册表和 Core 统一媒体 probe 建立 Runner。"""

    return NodeRunner(
        work_root,
        python_adapters=av27_python_adapters(),
        validators=av27_validators(),
        media_probe=runner_media_probe,
    )


def _signal() -> dict[str, JsonValue]:
    """返回冻结的 BT.709 SDR limited signal 参数。"""

    return {
        "color_primaries": "bt709",
        "color_transfer": "bt709",
        "color_space": "bt709",
        "color_range": "tv",
        "chroma_location": "left",
        "field_order": "progressive",
        "rotation": 0,
    }


def _geometry() -> dict[str, JsonValue]:
    return {"width": 64, "height": 36, "sample_aspect_ratio": "1/1"}


def _make_prores_chapter(ffmpeg: str, target: Path, *, hue: int) -> None:
    """生成三帧、2 fps、ProRes 422 HQ 的一个合成 FI chapter。"""

    _run_tool(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=64x36:rate=2:duration=1.5,"
            f"hue=h={hue},format=yuv422p10le,"
            "setparams=range=limited:color_primaries=bt709:"
            "color_trc=bt709:colorspace=bt709",
            "-frames:v",
            "3",
            "-an",
            "-sn",
            "-dn",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-pix_fmt",
            "yuv422p10le",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            "-chroma_sample_location",
            "left",
            "-f",
            "mov",
            str(target),
        ]
    )


def _make_source_with_two_audio_tracks(ffmpeg: str, target: Path) -> None:
    """生成四帧 Source，并以 metadata/disposition 区分两条原始音轨顺序。"""

    _run_tool(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=64x36:rate=1:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=4",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
            "-frames:v",
            "4",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "yuv420p10le",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            "-chroma_sample_location",
            "left",
            "-c:a",
            "pcm_s16le",
            "-metadata:s:a:0",
            "language=jpn",
            "-metadata:s:a:0",
            "title=Primary",
            "-disposition:a:0",
            "default",
            "-metadata:s:a:1",
            "language=eng",
            "-metadata:s:a:1",
            "title=Alternate",
            "-disposition:a:1",
            "0",
            "-map_chapters",
            "-1",
            "-f",
            "matroska",
            str(target),
        ]
    )


def _chapter_input(path: Path, ordinal: int) -> RunnerInput:
    """为真实 ProRes payload 附加已由上游 FI validator 产生的最小 namespace。"""

    media_info: dict[str, object] = runner_media_probe(path, "VideoFile")
    media_info[AV27_NAMESPACE] = {
        "frame_count": 3,
        "frame_rate": "2/1",
        "geometry": {"width": 64, "height": 36},
        "signal": _signal(),
        "duration_seconds": 1.5,
        "stage": {
            "kind": "frame_interpolation",
            "model_name": "synthetic-fi",
            "model_version": "test-1",
            "operator_declared": True,
        },
    }
    return RunnerInput(
        port_id="chapters",
        artifact_id=f"artifact.chapter.{ordinal}",
        kind="VideoFile",
        path=path,
        ordinal=ordinal,
        producer_node_run_id=str(uuid4()),
        producer_port_id="video",
        media_info=media_info,
    )


def _artifact_input(
    artifact: RunnerArtifact,
    port_id: str,
    *,
    ordinal: int | None = None,
) -> RunnerInput:
    """把 Runner 成功产物无损绑定为下一节点的直接 input。"""

    return RunnerInput(
        port_id=port_id,
        artifact_id=artifact.artifact_id,
        kind=artifact.kind,
        path=artifact.path,
        ordinal=ordinal,
        producer_node_run_id=artifact.producer_node_run_id,
        producer_port_id=artifact.producer_port_id,
        artifact_ordinal=artifact.ordinal,
        frame_range=artifact.frame_range,
        media_info=artifact.media_info,
        size=artifact.size,
        mtime_ns=artifact.mtime_ns,
    )


def _artifact(result: RunnerResult, port_id: str) -> RunnerArtifact:
    return next(item for item in result.artifacts if item.producer_port_id == port_id)


def test_program_single_producer_and_final_preserves_ordered_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """闭合 ``2N-1→2N``、单次 Program producer 与 Final 原始双音轨。"""

    ffmpeg = _require_media_tools()
    first = tmp_path / "chapter-0001.mov"
    second = tmp_path / "chapter-0002.mov"
    source = tmp_path / "source-two-audio.mkv"
    _make_prores_chapter(ffmpeg, first, hue=0)
    _make_prores_chapter(ffmpeg, second, hue=90)
    _make_source_with_two_audio_tracks(ffmpeg, source)

    runner = _runner(tmp_path / "attempts")
    source_result = runner.run_automatic(
        _request(
            source_program_definition(),
            parameters={"source_path": str(source.resolve()), "source_ordinal": 0},
        )
    )
    source_artifact = _artifact(source_result, "source_media")
    source_namespace = source_artifact.media_info[AV27_NAMESPACE]
    assert isinstance(source_namespace, Mapping)
    assert source_namespace["frame_count"] == 4
    assert source_namespace["frame_rate"] == "1/1"
    admission_result = runner.run_automatic(
        _request(
            source_admission_definition(),
            parameters={
                "source_mode": "program",
                "sources": [{"source_ordinal": 0, "source_node_id": "source-node-0001"}],
            },
            inputs=(_artifact_input(source_artifact, "sources", ordinal=0),),
        )
    )
    admission_artifact = _artifact(admission_result, "gate")

    real_run_ffmpeg: Any = av27_adapters._run_ffmpeg
    program_calls: list[tuple[str, ...]] = []

    def record_program_call(
        context: object,
        argv: Sequence[str],
        **kwargs: object,
    ) -> int | None:
        program_calls.append(tuple(argv))
        return cast(int | None, real_run_ffmpeg(context, argv, **kwargs))

    monkeypatch.setattr(av27_adapters, "_run_ffmpeg", record_program_call)
    program_result = runner.run_automatic(
        _request(
            program_encode_definition(),
            parameters={
                "encoder": "cpu",
                "source_fps": "1/1",
                "chapters": [
                    {
                        "chapter_id": "chapter-0001",
                        "chapter_ordinal": 0,
                        "source_frames": 2,
                        "expected_fi_frames": 3,
                        "encoded_frames": 4,
                    },
                    {
                        "chapter_id": "chapter-0002",
                        "chapter_ordinal": 1,
                        "source_frames": 2,
                        "expected_fi_frames": 3,
                        "encoded_frames": 4,
                    },
                ],
                "expected_geometry": _geometry(),
                "expected_signal": _signal(),
            },
            inputs=(_chapter_input(first, 0), _chapter_input(second, 1)),
        )
    )
    assert len(program_calls) == 1
    assert program_result.validation_summary["adapter"] == {"single_program_producer": True}
    program_artifact = _artifact(program_result, "video")
    program_namespace = program_artifact.media_info[AV27_NAMESPACE]
    assert isinstance(program_namespace, Mapping)
    assert program_namespace["frame_count"] == 8
    assert probe_header(program_artifact.path, count_frames=True).video.frame_count == 8

    final_calls: list[tuple[str, ...]] = []

    def record_final_call(
        context: object,
        argv: Sequence[str],
        **kwargs: object,
    ) -> int | None:
        final_calls.append(tuple(argv))
        return cast(int | None, real_run_ffmpeg(context, argv, **kwargs))

    monkeypatch.setattr(av27_adapters, "_run_ffmpeg", record_final_call)
    final_result = runner.run_automatic(
        _request(
            final_mux_definition(),
            parameters={
                "source_mode": "program",
                "sources": [{"source_ordinal": 0, "source_frames": 4, "source_fps": "1/1"}],
                "expected_program_frames": 8,
                "expected_geometry": _geometry(),
                "expected_signal": _signal(),
                "mr_mode": "off",
            },
            inputs=(
                _artifact_input(program_artifact, "video"),
                _artifact_input(source_artifact, "sources", ordinal=0),
                _artifact_input(admission_artifact, "gate"),
            ),
        )
    )
    assert len(final_calls) == 1
    final_argv = final_calls[0]
    avoid_negative_ts = final_argv.index("-avoid_negative_ts")
    assert final_argv[avoid_negative_ts + 1] == "disabled"

    final_artifact = _artifact(final_result, "media")
    final_header = probe_header(final_artifact.path)
    assert len(final_header.audios) == 2
    assert [(item.language, item.title) for item in final_header.audios] == [
        ("jpn", "Primary"),
        ("eng", "Alternate"),
    ]
    assert [item.default for item in final_header.audios] == [True, False]
    node_validation = final_result.validation_summary["node"]
    assert isinstance(node_validation, Mapping)
    assert node_validation["audio_stream_count"] == 2
