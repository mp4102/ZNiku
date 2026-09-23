"""只在显式新模板展开时生成融合普通 Graph，绝不转换用户旧工程或运行快照。"""

from zniku.graph import Graph, GraphValidator
from zniku.source_aligned.template import SourceAlignedBuild

from . import definitions, final_publish, fused


def build_fused(
    build: SourceAlignedBuild, *, export_cropped_chapters: bool = False
) -> SourceAlignedBuild:
    """消费本次纯 builder 结果；可选 Crop 是普通旁路真实输出，不是必经依赖。"""
    if type(export_cropped_chapters) is not bool:
        raise ValueError("E_FUSED_TEMPLATE: 导出意图必须为布尔值")
    catalog = {(value.type_id, value.version): value for value in build.definitions}
    roles = {
        node.node_id: definitions.definition_role(catalog[(node.type_id, node.definition_version)])
        for node in build.project.graph.nodes
    }
    program_ids = {node for node, role in roles.items() if role == "program"}
    crop_ids = {node for node, role in roles.items() if role == "crop"}
    final_ids = {
        node.node_id
        for node in build.project.graph.nodes
        if final_publish.is_definition(catalog[(node.type_id, node.definition_version)])
    }
    if len(program_ids) != 1 or len(final_ids) != 1 or len(crop_ids) != build.plan.chapter_count:
        raise ValueError("E_FUSED_TEMPLATE: 仅接受当前构造的完整批量模板")
    program_id = next(iter(program_ids))
    crop_sources = {
        edge.target_node_id: edge.source_node_id
        for edge in build.project.graph.edges
        if edge.target_node_id in crop_ids and roles.get(edge.source_node_id) == "fi"
    }
    if set(crop_sources) != crop_ids:
        raise ValueError("E_FUSED_TEMPLATE: 裁边未连接各自显式 FI")
    nodes = []
    for node in build.project.graph.nodes:
        if node.node_id in crop_ids and not export_cropped_chapters:
            continue
        if node.node_id in program_ids | final_ids:
            definition = fused.definition("program" if node.node_id == program_id else "final")
            node = node.model_copy(
                update={"type_id": definition.type_id, "definition_version": definition.version}
            )
        nodes.append(node)
    edges = []
    for edge in build.project.graph.edges:
        if edge.target_node_id == program_id:
            if edge.source_node_id not in crop_sources:
                raise ValueError("E_FUSED_TEMPLATE: Program 原始边界不符")
            edge = edge.model_copy(
                update={
                    "source_node_id": crop_sources[edge.source_node_id],
                    "target_port_id": "videos",
                }
            )
        elif not export_cropped_chapters and (
            edge.target_node_id in crop_ids or edge.source_node_id in crop_ids
        ):
            continue
        edges.append(edge)
    graph = Graph(nodes=tuple(nodes), edges=tuple(edges))
    used = {(node.type_id, node.definition_version) for node in nodes}
    current = tuple(value for value in build.definitions if (value.type_id, value.version) in used)
    current += (fused.definition("program"), fused.definition("final"))
    GraphValidator(current).validate(graph)
    return build.model_copy(
        update={
            "project": build.project.model_copy(update={"graph": graph}),
            "definitions": current,
        }
    )
