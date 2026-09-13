"""把精确内建节点的业务信息投影为纯存储名称，不修改媒体合同或图。

一个Split始终只有一个共享执行目录；章节只组织生产者节点，不拆分attempt或复制媒体。
未知定义、缺少可靠章节信息的合法自定义图回退custom，不能从node_id、别名或文件名猜媒体身份。
目录首次分配后由存储映射固化，之后的展示变化不移动既有文件。
"""

from __future__ import annotations

from pydantic import ValidationError

from zniku.avenhance_v27 import definitions as av27
from zniku.avenhance_v27.naming import _enhancement_leaf
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.avenhance_v27.profiles import atomic_split_count_from_type_id, is_av27_definition
from zniku.avenhance_v27.template import excel_chapter_label
from zniku.chapter_overlap.definitions import definition_role
from zniku.chapter_overlap.node_contracts import (
    PARAMETER_MODELS,
    ChapterParameters,
    EnhancementParameters,
)
from zniku.graph import NodeDefinition, NodeInstance
from zniku.project.storage_layout import AttemptNamingHint
from zniku.runtime import Run

_OVERLAP_NAMES = {
    "split": "分章分叶",
    "enhancement": "逐叶增强",
    "merge": "章节增强合并",
    "context": "FI上下文准备",
    "fi": "外部FI补帧",
    "crop": "FI精确裁边",
    "program": "连续视频编码",
    "final": "原音轨封装",
}


def _chapter(ordinal: int, task_name: str) -> AttemptNamingHint:
    return AttemptNamingHint(
        category="chapters",
        chapter_index=ordinal + 1,
        chapter_name=excel_chapter_label(ordinal),
        task_name=task_name,
    )


def resolve_attempt_naming(
    run: Run, node: NodeInstance, definition: NodeDefinition
) -> AttemptNamingHint:
    """只为精确内建合同提取已有参数；失败仅回退命名，不替代执行validator或阻断自由图。"""

    fallback = AttemptNamingHint(task_name=definition.type_id.rsplit(".", 1)[-1][:300] or "任务")
    if (node.type_id, node.definition_version) != (definition.type_id, definition.version):
        return fallback
    role = definition_role(definition)
    if role is not None:
        try:
            params = PARAMETER_MODELS[role].model_validate(
                node.model_dump(mode="json")["parameters"], strict=True
            )
        except (ValidationError, ValueError, Av27MediaError):
            # 业务跨字段校验仍由执行前 validator 报错；命名不能提前成为另一套运行准入。
            return fallback
        name = _OVERLAP_NAMES[role]
        if isinstance(params, ChapterParameters):
            if isinstance(params, EnhancementParameters):
                name += f"-leaf-{params.leaf.ordinal + 1:04d}"
            return _chapter(params.chapter.ordinal, name)
        return AttemptNamingHint(
            category="common" if role == "split" else "program", task_name=name
        )
    if not is_av27_definition(definition):
        return fallback
    common = {
        av27.SOURCE_PROGRAM_TYPE_ID: "源素材",
        av27.SOURCE_ADMISSION_TYPE_ID: "素材分析",
        av27.MOSAIC_RESTORATION_TYPE_ID: "外部修复",
    }
    program = {
        av27.PROGRAM_ENCODE_TYPE_ID: "连续视频编码",
        av27.FINAL_MUX_TYPE_ID: "原音轨封装",
    }
    if node.type_id in common:
        return AttemptNamingHint(category="common", task_name=common[node.type_id])
    if node.type_id in program:
        return AttemptNamingHint(category="program", task_name=program[node.type_id])
    if atomic_split_count_from_type_id(node.type_id) is not None:
        return AttemptNamingHint(category="common", task_name="分章分叶")
    if node.type_id == av27.ENHANCEMENT_TYPE_ID:
        leaf = _enhancement_leaf(node, run.graph_snapshot)
        if leaf is not None:
            segment, ordinal = leaf
            return _chapter(segment.chapter_ordinal, f"逐叶增强-leaf-{ordinal:04d}")
    if node.type_id in {av27.MERGE_VIDEO_TYPE_ID, av27.FRAME_INTERPOLATION_TYPE_ID}:
        chapter_ordinal = node.parameters.get("chapter_ordinal")
        if type(chapter_ordinal) is int and 0 <= chapter_ordinal < 999999:
            name = "章节增强合并" if node.type_id == av27.MERGE_VIDEO_TYPE_ID else "外部FI补帧"
            return _chapter(chapter_ordinal, name)
    return fallback
