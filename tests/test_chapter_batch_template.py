"""纯合成 Graph 回归：十五叶只产生三个真实增强节点，旧逐叶模板仍保留原义。"""

from pathlib import Path

from test_av27_template import _binding
from test_source_admitted_service import create
from zniku.avenhance_v27.template import PublicationRequest
from zniku.chapter_batch import definitions
from zniku.chapter_batch.contracts import BATCH_PREFIX
from zniku.chapter_batch.final_publish import TYPE_ID as FINAL_PUBLISH_TYPE_ID
from zniku.chapter_batch.final_publish import definition as final_publication_definition
from zniku.graph import GraphValidator
from zniku.project import ProjectStore
from zniku.source_admission.definitions import external_definition
from zniku.source_admission.naming import publication_target
from zniku.source_aligned.template import SourceAlignedProcessing, build_source_aligned


def test_fifteen_leaves_three_real_batch_nodes_and_legacy_template_unchanged(
    tmp_path: Path,
) -> None:
    """坐标/Graph 测试不运行虚构时长媒体，真实短链由独立媒体测试承担。"""
    app, source, path = create(tmp_path)
    snapshot = app.inspect().snapshot
    assert snapshot is not None
    binding = _binding(snapshot, source_mode="program", mr_mode="off", frames=(26001,))
    processing = SourceAlignedProcessing.model_validate(
        {
            "settings": {
                "chapter_selector": {"mode": "average", "count": 3},
                "leaf_max_minutes": 1,
            },
            "enhancement": {"model_name": "Synthetic"},
            "program_encode": {"encoder": "cpu"},
        }
    )
    publication = PublicationRequest(
        output_root=str(tmp_path), title="Synthetic", year="2026", overwrite=False
    )
    original = snapshot.project.graph
    build = build_source_aligned(
        snapshot,
        binding,
        processing,
        publication,
        definition_factory=definitions.definition,
        external_factory=external_definition,
        publication_target=publication_target,
        batch_enhancement=True,
    )
    assert snapshot.project.graph == original
    assert (build.plan.chapter_count, build.plan.leaf_count) == (3, 15)
    batch_nodes = [
        node for node in build.project.graph.nodes if node.type_id.startswith(BATCH_PREFIX)
    ]
    assert len(batch_nodes) == 3
    assert {node.type_id for node in batch_nodes} == {BATCH_PREFIX + "5"}
    graph = build.project.graph
    for node in batch_nodes:
        incoming = [edge for edge in graph.edges if edge.target_node_id == node.node_id]
        outgoing = [edge for edge in graph.edges if edge.source_node_id == node.node_id]
        assert len(incoming) == len(outgoing) == 5
        assert {edge.ordinal for edge in incoming} == {0, 1, 2, 3, 4}
        assert {edge.ordinal for edge in outgoing} == {0, 1, 2, 3, 4}
        assert len({edge.target_node_id for edge in outgoing}) == 1
        assert len(node.parameters["leaves"]) == 5  # type: ignore[arg-type]
    GraphValidator(build.definitions).validate(graph)
    # 新默认入口显式选择新 exact；旧 builder 调用仍保留 final + copy 的原形状。
    direct = build_source_aligned(
        snapshot,
        binding,
        processing,
        publication,
        definition_factory=definitions.definition,
        external_factory=external_definition,
        publication_target=publication_target,
        batch_enhancement=True,
        final_publication_factory=final_publication_definition,
    )
    direct_nodes = {node.node_id: node for node in direct.project.graph.nodes}
    old_nodes = {node.node_id: node for node in graph.nodes}
    assert direct_nodes["overlap.final"].type_id == FINAL_PUBLISH_TYPE_ID
    assert (
        direct_nodes["overlap.final"].parameters["target_path"]
        == direct.publication.output_target_path
    )
    assert direct_nodes["output"].parameters == {"mode": "reference", "overwrite": False}
    assert old_nodes["overlap.final"].type_id == definitions.definition("final").type_id
    assert old_nodes["output"].parameters["mode"] == "copy"
    assert not Path(direct.publication.output_target_path).exists()
    assert not list(tmp_path.glob(".zniku-publish-*.pending"))
    GraphValidator(direct.definitions).validate(direct.project.graph)
    ProjectStore.open(path).save(build.project, build.definitions)
    assert ProjectStore.open(path).load().project.graph == graph
    assert source.read_bytes() == b"synthetic-source"

    legacy = build_source_aligned(snapshot, binding, processing, publication)
    assert not any(node.type_id.startswith(BATCH_PREFIX) for node in legacy.project.graph.nodes)
    assert (
        sum(node.node_id.startswith("overlap.enhance.") for node in legacy.project.graph.nodes)
        == 15
    )
