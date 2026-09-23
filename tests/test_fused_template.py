"""融合候选只生成显式普通 Graph；纯合成规模与旧合同隔离，不运行外部模型。"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_av27_template import _binding
from test_source_admitted_service import create
from zniku.avenhance_v27.template import PublicationRequest
from zniku.chapter_batch import definitions, final_publish, fused
from zniku.chapter_batch.fused_template import build_fused
from zniku.graph import GraphValidator
from zniku.project import ProjectStore
from zniku.source_admission.definitions import external_definition
from zniku.source_admission.naming import publication_target
from zniku.source_aligned.template import (
    SourceAlignedBuild,
    SourceAlignedProcessing,
    build_source_aligned,
)


def prepared_build(tmp_path: Path, chapters: int) -> tuple[Path, SourceAlignedBuild]:
    """只持久化小分析图并构造虚拟帧绑定；绝不把占位媒体称为通过真实分析。"""
    app, source, path = create(tmp_path)
    snapshot = app.inspect().snapshot
    assert snapshot is not None
    binding = _binding(snapshot, source_mode="program", mr_mode="off", frames=(chapters * 12,))
    build = build_source_aligned(
        snapshot,
        binding,
        SourceAlignedProcessing.model_validate(
            {
                "settings": {"chapter_selector": {"mode": "average", "count": chapters}},
                "enhancement": {"model_name": "Synthetic"},
                "fi_profile": {"left_context_frames": 1, "right_context_frames": 1},
                "program_encode": {"encoder": "cpu"},
            }
        ),
        PublicationRequest(
            output_root=str(tmp_path), title="Synthetic", year="2026", overwrite=False
        ),
        definition_factory=definitions.definition,
        external_factory=external_definition,
        publication_target=publication_target,
        batch_enhancement=True,
        final_publication_factory=final_publish.definition,
    )
    assert source.read_bytes() == b"synthetic-source"
    return path, build


@pytest.mark.parametrize("chapters", (1, 3, 26, 1000))
@pytest.mark.parametrize("export", (False, True))
def test_fused_template_has_explicit_raw_edges_and_optional_real_crop_branch(
    tmp_path: Path, chapters: int, export: bool
) -> None:
    path, original = prepared_build(tmp_path, chapters)
    historical = original.model_dump_json()
    build = build_fused(original, export_cropped_chapters=export)
    assert original.model_dump_json() == historical
    graph = build.project.graph
    by_id = {node.node_id: node for node in graph.nodes}
    assert by_id["overlap.program"].type_id == fused.PROGRAM_TYPE
    assert by_id["overlap.program"].definition_version == "0.3.6"
    assert by_id["overlap.final"].type_id == fused.FINAL_TYPE
    assert by_id["output"].parameters["mode"] == "reference"
    raw_edges = [edge for edge in graph.edges if edge.target_node_id == "overlap.program"]
    assert len(raw_edges) == chapters
    assert [edge.ordinal for edge in raw_edges] == list(range(chapters))
    assert all(edge.target_port_id == "videos" for edge in raw_edges)
    assert all(
        by_id[edge.source_node_id].type_id == definitions.definition("fi").type_id
        for edge in raw_edges
    )
    crops = [node for node in graph.nodes if node.type_id == definitions.definition("crop").type_id]
    assert len(crops) == (chapters if export else 0)
    assert all(
        not any(edge.source_node_id == node.node_id for edge in graph.edges) for node in crops
    )
    assert len(graph.nodes) == len(original.project.graph.nodes) - (0 if export else chapters)
    GraphValidator(build.definitions).validate(graph)
    store = ProjectStore.open(path)
    store.save(build.project, build.definitions)
    reopened = ProjectStore.open(path).load()
    assert reopened.project.graph == graph
    assert len(reopened.definitions) == len(build.definitions)
    assert not any(node.definition_version == "0.3.6" for node in original.project.graph.nodes)
    assert original.project.graph.nodes[-3].type_id != fused.PROGRAM_TYPE


@pytest.mark.parametrize("value", (None, 0, 1, "true", "false"))
def test_export_flag_is_strict_boolean(tmp_path: Path, value: object) -> None:
    _, original = prepared_build(tmp_path, 3)
    with pytest.raises(ValueError, match="E_FUSED_TEMPLATE"):
        build_fused(original, export_cropped_chapters=value)  # type: ignore[arg-type]
