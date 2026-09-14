"""真实短合成媒体运行 prepared 下游整链；外部来件仅为可重复测试替身，不证明 AI。"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from test_prepared_source_node_contracts import pipeline
from zniku.avenhance_v27.probe import Av27MediaError, probe_header
from zniku.graph import ExecutionMode, NodeInstance
from zniku.media.probe import runner_media_probe
from zniku.prepared_source.audio import verify_audio_content
from zniku.prepared_source.definitions import (
    atomic_split_definition,
    built_in_overlap_definitions,
    overlap_python_adapters,
    overlap_validators,
)
from zniku.prepared_source.node_contracts import OVERLAP_NAMESPACE, preflight
from zniku.runtime import NodeExecutionRequest, NodeRunner, RunnerInput
from zniku.runtime.runner import OutputPathSpec
from zniku.source_aligned.timeline import probe_cfr
from zniku.source_preparation import contracts

TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _ffmpeg(*arguments: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", *arguments],
        check=True,
        capture_output=True,
        timeout=120,
    )


def _key(item: RunnerInput) -> tuple[str, str]:
    value = item.media_info.get(OVERLAP_NAMESPACE)
    assert isinstance(value, dict)
    role = str(value["role"])
    leaf, chapter = value.get("leaf"), value.get("chapter")
    identity = (
        str(leaf["leaf_id"])
        if isinstance(leaf, dict)
        else str(chapter["ordinal"])
        if isinstance(chapter, dict)
        else ""
    )
    return role, identity


def _input(artifact: Any, port: str = "video", ordinal: int | None = None) -> RunnerInput:
    return RunnerInput(
        port,
        artifact.artifact_id,
        artifact.kind,
        artifact.path,
        ordinal=ordinal,
        producer_node_run_id=artifact.producer_node_run_id,
        producer_port_id=artifact.producer_port_id,
        media_info=artifact.media_info,
    )


@pytest.mark.skipif(not TOOLS, reason="需要 FFmpeg/FFprobe 的短合成媒体门禁")
@pytest.mark.parametrize(
    "preparation", ["original", "t1-clock-quantization/1", "external-preservation/1"]
)
def test_all_prepared_bindings_execute_split_to_final_with_actual_original_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preparation: str
) -> None:
    """下游使用三种已准入绑定；准备策略自身的真实执行由 source_preparation 测试负责。"""
    with monkeypatch.context() as fixtures:
        fixtures.setattr(contracts, "validate_audio_sources", lambda gate, inputs: None)
        steps = pipeline(tmp_path, n=6, chapters=2, rate="30/1")
    original = tmp_path / "original.mkv"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1920x1080:rate=30:duration=0.2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=0.2",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        "-x264-params",
        "colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-c:a",
        "pcm_s16le",
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
        str(original),
    )
    reference = original
    if preparation != "original":
        reference = tmp_path / "reference.mkv"
        shutil.copyfile(original, reference)
        irregular = tmp_path / "original-irregular.mkv"
        _ffmpeg(
            "-i",
            str(original),
            "-map",
            "0",
            "-c",
            "copy",
            "-bsf:v",
            r"setts=pts=PTS+if(eq(N\,2)\,7\,0):dts=DTS+if(eq(N\,2)\,7\,0)",
            str(irregular),
        )
        original = irregular
        with pytest.raises(Av27MediaError, match="TIMELINE"):
            probe_cfr(original, count=6, rate=Fraction(30))
    source_item, gate_item = steps[0].inputs
    gate_data = json.loads(gate_item.path.read_text(encoding="utf-8"))
    gate_data["preparation_strategy"] = preparation
    if preparation == "original":
        gate_data["reference_media_artifact_id"] = gate_data["original_media_artifact_id"]
    gate_data["audio_policy"] = "original"
    gate_data["audio_bindings"][0]["tracks"] = [
        {
            "stream_index": 1,
            "codec": "pcm_s16le",
            "sample_rate": 48000,
            "channels": 1,
            "channel_layout": "unknown",
            "start_time": "0/1",
            "end_time": "1/5",
            "relative_start": "0/1",
            "relative_end": "1/5",
            "sample_count": 9600,
        }
    ]
    gate_item.path.write_text(json.dumps(gate_data), encoding="utf-8")
    gate_item = replace(
        gate_item, media_info={OVERLAP_NAMESPACE: {"role": "admission", "admission": gate_data}}
    )
    source_item = replace(
        source_item,
        path=reference,
        media_info={OVERLAP_NAMESPACE: {"role": "reference", "admission": gate_data}},
    )
    audio_item = replace(
        steps[-1].inputs[1], path=original, producer_port_id="media", media_info={}
    )
    runner = NodeRunner(
        tmp_path / "attempts",
        python_adapters=overlap_python_adapters(),
        validators=overlap_validators(),
        media_probe=runner_media_probe,
    )
    definitions = {definition.type_id: definition for definition in built_in_overlap_definitions(2)}
    registered: dict[tuple[str, str], RunnerInput] = {}
    final_path: Path | None = None
    for step in steps:
        inputs: tuple[RunnerInput, ...]
        parameters = json.loads(json.dumps(step.parameters))
        parameters["source"]["reference_media_artifact_id"] = gate_data[
            "reference_media_artifact_id"
        ]
        if step.role == "enhancement":
            parameters["expected_output_geometry"] = parameters["expected_input_geometry"]
            parameters["actual_scale_factor"] = 1
        if step.role == "split":
            inputs = (source_item, gate_item)
            definition = atomic_split_definition(2)
        else:
            values: list[RunnerInput] = []
            for item in step.inputs:
                if item.port_id == "gate":
                    values.append(gate_item)
                elif item.port_id == "sources":
                    values.append(audio_item)
                else:
                    values.append(
                        replace(registered[_key(item)], port_id=item.port_id, ordinal=item.ordinal)
                    )
            inputs = tuple(values)
            definition = definitions[step.contract.outputs[0].metadata.producer_type_id]
        node = NodeInstance(
            node_id=f"synthetic-{step.role}-{uuid4()}",
            type_id=definition.type_id,
            definition_version=definition.version,
            parameters=parameters,
        )
        request = NodeExecutionRequest(
            str(uuid4()),
            1,
            definition,
            node,
            inputs,
            output_paths=tuple(
                OutputPathSpec(item.port_id, item.relative_path)
                for item in definition.executor.output_paths
            ),
        )
        if definition.execution_mode is ExecutionMode.AUTOMATIC:
            result = runner.run_automatic(request)
        else:
            handoff = runner.prepare_manual(request)
            expected = preflight(step.role, inputs, parameters).outputs[0].metadata
            filters = (
                "null"
                if step.role == "enhancement"
                else f"fps=60,trim=end_frame={expected.frame_count},setpts=N/(60*TB)"
            )
            _ffmpeg(
                "-i",
                str(inputs[0].path),
                "-an",
                "-vf",
                filters,
                "-frames:v",
                str(expected.frame_count),
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-pix_fmt",
                "yuv422p10le",
                "-threads",
                "1",
                "-video_track_timescale",
                "60" if step.role == "fi" else "30",
                "-color_range",
                "tv",
                "-colorspace",
                "bt709",
                "-color_trc",
                "bt709",
                "-color_primaries",
                "bt709",
                str(handoff.outputs[0].path),
            )
            result = runner.submit_manual(request, handoff)
        for artifact in result.artifacts:
            if step.role == "final":
                final_path = artifact.path
            else:
                value = _input(artifact)
                registered[_key(value)] = value
    assert final_path is not None
    assert len(probe_header(final_path).audios) == 1
    verify_audio_content(original, final_path)
    assert probe_cfr(final_path, count=12, rate=Fraction(60)).frame_count == 12
