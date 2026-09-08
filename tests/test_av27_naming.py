"""以合成图验证友好名称只投影 formal port binding，不改变图或制造额外媒体。"""

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import JsonValue

from zniku.avenhance_v27.definitions import (
    AV27_NODE_VERSION,
    atomic_split_definition,
    enhancement_definition,
    frame_interpolation_definition,
    merge_video_definition,
)
from zniku.avenhance_v27.naming import descriptive_output_paths, validate_media_basename
from zniku.graph import (
    Edge,
    ExecutionMode,
    Graph,
    GraphValidator,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.runtime import NodeExecutionRequest, NodeRunner, RunnerInput, capture_node_signature

_BASE = "Synthetic Movie (2026)"


def _enhancement_parameters(chapter: int, leaf: int) -> dict[str, JsonValue]:
    geometry: JsonValue = {"width": 1920, "height": 1080, "sample_aspect_ratio": "1/1"}
    return {
        "model_name": "Synthetic Enhancement",
        "expected_input_geometry": geometry,
        "expected_output_geometry": geometry,
        "expected_frames": 10,
        "expected_fps": "30/1",
        "chapter_id": f"chapter-{chapter + 1:04d}",
        "chapter_ordinal": chapter,
        "leaf_id": f"leaf-{leaf + 1:04d}",
        "leaf_ordinal": leaf,
    }


def _graph() -> tuple[Graph, NodeDefinition, NodeDefinition]:
    split_definition = atomic_split_definition(3)
    enhancement = enhancement_definition()
    chapters = (0, 1, 1)
    segments: list[JsonValue] = [
        {
            "port_id": f"leaf-{index + 1:04d}",
            "source_ordinal": 0,
            "planned_effective_video_artifact_id": "synthetic-effective",
            "chapter_id": f"chapter-{chapter + 1:04d}",
            "chapter_ordinal": chapter,
            "leaf_id": f"leaf-{index + 1:04d}",
            "leaf_ordinal": index,
            "start_frame": index * 10,
            "end_frame": (index + 1) * 10,
        }
        for index, chapter in enumerate(chapters)
    ]
    split = NodeInstance(
        node_id="arbitrary-split-identity",
        type_id=split_definition.type_id,
        definition_version=AV27_NODE_VERSION,
        parameters={"segments": segments},
    )
    nodes = tuple(
        NodeInstance(
            node_id=f"arbitrary-enhancement-{index}",
            type_id=enhancement.type_id,
            definition_version=AV27_NODE_VERSION,
            parameters=_enhancement_parameters(chapter, index),
        )
        for index, chapter in enumerate(chapters)
    )
    return (
        Graph(
            nodes=(split, *nodes),
            edges=tuple(
                Edge(
                    source_node_id=split.node_id,
                    source_port_id=f"leaf-{index + 1:04d}",
                    target_node_id=node.node_id,
                    target_port_id="video",
                )
                for index, node in enumerate(nodes)
            ),
        ),
        split_definition,
        enhancement,
    )


def test_split_and_enhancement_keep_global_ports_but_restart_local_leaf_number() -> None:
    graph, split, enhancement = _graph()
    before = graph.model_dump_json()
    signatures = tuple(capture_node_signature(graph, node.node_id) for node in graph.nodes)
    outputs = descriptive_output_paths(graph.nodes[0], split, media_basename=_BASE, graph=graph)
    assert [(item.port_id, item.relative_path) for item in outputs] == [
        ("leaf-0001", f"A/{_BASE}.A.leaf-0001.mkv"),
        ("leaf-0002", f"B/{_BASE}.B.leaf-0001.mkv"),
        ("leaf-0003", f"B/{_BASE}.B.leaf-0002.mkv"),
    ]
    for node, expected in zip(graph.nodes[1:], outputs, strict=True):
        paths = descriptive_output_paths(node, enhancement, media_basename=_BASE, graph=graph)
        assert len(paths) == 1
        assert paths[0].port_id == "video"
        assert (
            paths[0].relative_path
            == expected.relative_path.removesuffix(".mkv") + ".enhancement.mov"
        )
        # 更换显示基名只改变路径返回值，不写回参数，不能使已完成节点 stale。
        assert descriptive_output_paths(node, enhancement, media_basename="Other", graph=graph)
    assert graph.model_dump_json() == before
    assert tuple(capture_node_signature(graph, node.node_id) for node in graph.nodes) == signatures


@pytest.mark.parametrize(
    ("definition", "suffix"),
    [
        (merge_video_definition(), "enhancement.mov"),
        (frame_interpolation_definition(), "enhancement.fi.mov"),
    ],
)
@pytest.mark.parametrize(("ordinal", "label"), [(0, "A"), (1, "B"), (26, "AA")])
def test_chapter_names_use_formal_ordinal(
    definition: NodeDefinition, suffix: str, ordinal: int, label: str
) -> None:
    node = NodeInstance(
        node_id="not-a-chapter-label",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters={"chapter_id": "arbitrary-chapter", "chapter_ordinal": ordinal},
    )
    paths = descriptive_output_paths(
        node, definition, media_basename=_BASE, graph=Graph(nodes=(node,))
    )
    assert paths[0].relative_path == f"{label}/{_BASE}.{label}.{suffix}"


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        " title",
        "title ",
        "title.",
        "../title",
        "A/title",
        "A\\title",
        "C:relative",
        "title?",
        "title*",
        'title"',
        "title<",
        "title>",
        "title|",
        "a\x00b",
        "a\x1fb",
        "CON",
        "nul.ext",
        "COM1.part",
        "LPT9",
        "COM¹",
        "conout$",
        "x" * 181,
        "😀" * 91,
        "\ud800",
    ],
)
def test_unsafe_media_basename_fails_without_normalization(value: str) -> None:
    with pytest.raises(ValueError, match="E_AV27_MEDIA_BASENAME"):
        validate_media_basename(value)


@pytest.mark.parametrize("value", [_BASE, "合成.测试 (2026)", "COM10", "x" * 180, "😀" * 90])
def test_safe_media_basename_is_not_rewritten(value: str) -> None:
    assert validate_media_basename(value) == value


def test_custom_valid_graph_without_atomic_split_keeps_definition_output_name() -> None:
    enhancement = enhancement_definition()
    source_definition = NodeDefinition(
        type_id="test.synthetic.source",
        version="1.0.0",
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="synthetic:source"),
    )
    source = NodeInstance(
        node_id="source", type_id=source_definition.type_id, definition_version="1.0.0"
    )
    node = NodeInstance(
        node_id="custom-enhancement",
        type_id=enhancement.type_id,
        definition_version=AV27_NODE_VERSION,
        parameters=_enhancement_parameters(0, 0),
    )
    graph = Graph(
        nodes=(source, node),
        edges=(
            Edge(
                source_node_id="source",
                source_port_id="video",
                target_node_id=node.node_id,
                target_port_id="video",
            ),
        ),
    )
    GraphValidator((source_definition, enhancement)).validate(graph)
    assert descriptive_output_paths(node, enhancement, media_basename=_BASE, graph=graph) == ()
    assert (
        descriptive_output_paths(source, source_definition, media_basename=_BASE, graph=graph) == ()
    )


def test_inconsistent_segment_identity_does_not_guess_leaf_from_name() -> None:
    graph, _split, definition = _graph()
    node = graph.nodes[2].model_copy(
        update={"parameters": {**graph.nodes[2].parameters, "leaf_ordinal": 0}}
    )
    assert descriptive_output_paths(node, definition, media_basename=_BASE, graph=graph) == ()


def test_identical_filenames_stay_in_independent_attempt_directories(tmp_path: Path) -> None:
    graph, _split, definition = _graph()
    node = graph.nodes[1]
    source = tmp_path / "synthetic-source.bin"
    source.write_bytes(b"synthetic-input")
    runner = NodeRunner(tmp_path / "attempts")
    paths = descriptive_output_paths(node, definition, media_basename=_BASE, graph=graph)
    handoffs = tuple(
        runner.prepare_manual(
            NodeExecutionRequest(
                node_run_id=str(uuid4()),
                attempt=attempt,
                definition=definition,
                node=node.model_copy(update={"node_id": f"same-name-node-{attempt}"}),
                inputs=(
                    RunnerInput("video", f"synthetic-artifact-{attempt}", "VideoFile", source),
                ),
                output_paths=paths,
            )
        )
        for attempt in (1, 2)
    )
    targets = [Path(handoff.outputs[0].path) for handoff in handoffs]
    assert targets[0].name == targets[1].name == f"{_BASE}.A.leaf-0001.enhancement.mov"
    assert targets[0] != targets[1]
    for target, handoff in zip(targets, handoffs, strict=True):
        assert target.is_relative_to(Path(handoff.work_dir))
        assert target.parent.is_dir()
        assert not target.exists()
