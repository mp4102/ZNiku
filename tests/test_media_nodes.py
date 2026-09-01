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
from pathlib import Path
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

ROOT = Path(__file__).parents[1]


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

    def fail_second(context: Any, argv: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise MediaNodeError("E_TEST_SECOND_SEGMENT", "synthetic second segment failure")
        real_run(context, argv)

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
