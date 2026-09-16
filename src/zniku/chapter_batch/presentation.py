"""首次展开章级模板时建立可编辑实例别名；纯 StudioState 不参与执行和结果失效。"""

from zniku.project.studio import NodeViewState, StudioState
from zniku.source_aligned.node_contracts import ChapterParameters
from zniku.source_aligned.template import SourceAlignedBuild

from .contracts import BatchParameters
from .definitions import definition_role


def chapter_views(build: SourceAlignedBuild, current: StudioState) -> StudioState:
    """按本次正式规划标章，保留已有别名、折叠、分组和视口，不修改 Graph 或历史 Run。"""
    node_ids = {node.node_id for node in build.project.graph.nodes}
    views = {view.node_id: view for view in current.node_views if view.node_id in node_ids}
    definitions = {(item.type_id, item.version): item for item in build.definitions}
    chapters = {chapter.ordinal: chapter for chapter in build.plan.chapters}
    titles = {
        "enhancement": "批量增强",
        "merge": "增强合并",
        "context": "FI 上下文准备",
        "fi": "外部章节补帧",
        "crop": "FI 精确裁边",
    }
    for node in build.project.graph.nodes:
        definition = definitions[(node.type_id, node.definition_version)]
        role = definition_role(definition)
        if role not in titles:
            continue
        params = (
            BatchParameters.model_validate(node.model_dump(mode="json")["parameters"])
            if role == "enhancement"
            else ChapterParameters.model_validate(
                {
                    key: node.model_dump(mode="json")["parameters"][key]
                    for key in ("source", "chapter")
                }
            )
        )
        chapter = chapters[params.chapter.ordinal]
        name = f"{chapter.label} 章 · {titles[role]}"
        if isinstance(params, BatchParameters):
            name += f" ({len(params.leaves)} 份)"
        existing = views.get(node.node_id, NodeViewState(node_id=node.node_id))
        if existing.display_name is None:
            views[node.node_id] = existing.model_copy(update={"display_name": name})
    result = current.model_copy(update={"node_views": tuple(views.values())})
    result.validate_graph(build.project.graph)
    return result
