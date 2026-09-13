"""从新节点的严格参数生成attempt相对路径，不修改旧目标或依文件名认定来源。

各attempt目录隔离，章内友好名称只用于交付和归档。安全路径检查复用既有命名约束；
缺少完整合同的自定义草稿保留definition默认路径，不以显示逻辑改写Graph。
"""

from __future__ import annotations

from pydantic import ValidationError

from zniku.avenhance_v27.naming import _path
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.graph import NodeDefinition, NodeInstance
from zniku.runtime.runner import OutputPathSpec

from .definitions import definition_role
from .node_contracts import (
    PARAMETER_MODELS,
    ChapterParameters,
    EnhancementParameters,
    SplitParameters,
)


def overlap_output_paths(
    node: NodeInstance, definition: NodeDefinition, *, media_basename: str
) -> tuple[OutputPathSpec, ...]:
    """仅 exact 新定义和严格参数产生名称；不完整草稿回退，危险基名不自动修正。"""

    if (node.type_id, node.definition_version) != (
        definition.type_id,
        definition.version,
    ):
        return ()
    role = definition_role(definition)
    if role is None or role in {"program", "final"}:
        return ()
    try:
        params = PARAMETER_MODELS[role].model_validate(
            node.model_dump(mode="json")["parameters"], strict=True
        )
    except (ValidationError, Av27MediaError, ValueError):
        return ()
    if isinstance(params, SplitParameters):
        if params.plan.leaf_count != len(definition.output_ports):
            return ()
        return tuple(
            OutputPathSpec(
                leaf.leaf_id,
                _path(media_basename, chapter.ordinal, f"leaf-{leaf.ordinal + 1:04d}.mkv"),
            )
            for chapter in params.plan.chapters
            for leaf in chapter.leaves
        )
    assert isinstance(params, ChapterParameters)
    suffix = {
        "merge": "enhancement.mov",
        "context": "enhancement.fi-input.mov",
        "fi": "enhancement.fi-raw.mov",
        "crop": "enhancement.fi.mov",
    }.get(role)
    if isinstance(params, EnhancementParameters):
        leaf, chapter = params.leaf, params.chapter
        if not chapter.start_frame <= leaf.start_frame < leaf.end_frame <= chapter.end_frame or (
            (leaf.ordinal == 0) != (leaf.start_frame == chapter.start_frame)
            or (leaf.ordinal == leaf.count - 1) != (leaf.end_frame == chapter.end_frame)
        ):
            return ()
        suffix = f"leaf-{leaf.ordinal + 1:04d}.enhancement.mov"
    return (
        ()
        if suffix is None
        else (OutputPathSpec("video", _path(media_basename, params.chapter.ordinal, suffix)),)
    )
