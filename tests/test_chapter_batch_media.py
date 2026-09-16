"""短真实合成媒体验证多输出批量验收；这是节点局部测试，不冒充完整 Source/Planner 流程。

两份四帧 FFV1 及 ProRes 使用明确合成坐标绑定；不读取用户素材或运行外部 AI。
真实三章端到端流程另测，此处专门覆盖一次 validator 检查两个实际来件。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from test_source_aligned_node_contracts import pipeline
from zniku.chapter_batch import definitions
from zniku.chapter_batch.contracts import NAMESPACE, BatchMetadata
from zniku.graph import NodeInstance
from zniku.runtime import NodeExecutionRequest, NodeRunner, RunnerError, RunnerInput
from zniku.runtime.runner import OutputPathSpec
from zniku.source_admission import contracts as admitted
from zniku.source_aligned.node_contracts import LeafBinding

TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _media(path: Path, *, frames: int, prores: bool) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30",
            "-frames:v",
            str(frames),
            "-an",
            "-c:v",
            "prores_ks" if prores else "ffv1",
            *(["-profile:v", "3"] if prores else []),
            "-pix_fmt",
            "yuv422p10le" if prores else "yuv420p10le",
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
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
@pytest.mark.parametrize("invalid_second", [False, True])
def test_two_real_prores_outputs_checked_together_and_bad_leaf_identified(
    tmp_path: Path, invalid_second: bool
) -> None:
    step = pipeline(tmp_path, n=8, chapters=1, rate="30/1")[0]
    source, gate = step.inputs
    split = admitted.preflight(
        "split",
        (
            replace(
                source,
                media_info={
                    **source.media_info,
                    admitted.SOURCE_NAMESPACE: {"contract_version": "0.3.5"},
                },
            ),
            gate,
        ),
        step.parameters,
    )
    original = split.outputs[0].metadata
    leaves = tuple(
        LeafBinding(
            leaf_id=f"leaf-{index + 1:04d}",
            global_ordinal=index,
            ordinal=index,
            count=2,
            start_frame=index * 4,
            end_frame=(index + 1) * 4,
        )
        for index in range(2)
    )
    inputs = []
    for index, leaf in enumerate(leaves):
        path = tmp_path / f"input-{index}.mkv"
        _media(path, frames=4, prores=False)
        metadata = original.model_copy(
            update={
                "producer_type_id": "zniku.overlap.split.leaves.2",
                "leaf": leaf,
                "frame_count": 4,
            }
        )
        inputs.append(
            RunnerInput(
                "videos",
                str(uuid4()),
                "VideoFile",
                path,
                ordinal=index,
                producer_node_run_id=str(uuid4()),
                producer_port_id=leaf.leaf_id,
                media_info={admitted.NAMESPACE: metadata.model_dump(mode="json")},
            )
        )
    assert original.chapter is not None
    definition = definitions.definition("enhancement", 2)
    node = NodeInstance(
        node_id="batch",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters={
            "source": original.source.expectation().model_dump(),
            "chapter": original.chapter.model_dump(),
            "leaves": [leaf.model_dump() for leaf in leaves],
            "expected_input_geometry": original.geometry.model_dump(),
            "expected_output_geometry": original.geometry.model_dump(),
            "model_name": "Synthetic ProRes fixture",
            "actual_scale_factor": 1,
        },
    )
    request = NodeExecutionRequest(
        str(uuid4()),
        1,
        definition,
        node,
        tuple(inputs),
        output_paths=tuple(
            OutputPathSpec(item.port_id, item.relative_path)
            for item in definition.executor.output_paths
        ),
    )
    runner = NodeRunner(tmp_path / "attempts", validators=definitions.validators())
    handoff = runner.prepare_manual(request)
    for index, output in enumerate(handoff.outputs):
        _media(Path(output.path), frames=5 if invalid_second and index == 1 else 4, prores=True)
    if invalid_second:
        # Runner 不返回可登记的部分结果；两份来件仍保留，Runtime 的整节点失败另有集成测试。
        with pytest.raises(RunnerError, match="leaf-0002"):
            runner.submit_manual(request, handoff)
        assert all(Path(output.path).is_file() for output in handoff.outputs)
        return
    result = runner.submit_manual(request, handoff)
    assert len(result.artifacts) == 2
    for index, artifact in enumerate(result.artifacts):
        metadata = BatchMetadata.model_validate(artifact.media_info[NAMESPACE])
        assert metadata.producer_type_id == definition.type_id
        assert metadata.leaf == leaves[index]
        assert metadata.frame_count == 4
