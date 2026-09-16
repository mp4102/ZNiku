"""把精确内建节点的业务信息投影为纯存储名称，不修改媒体合同或图。

一个Split始终只有一个共享执行目录；章节只组织生产者节点，不拆分attempt或复制媒体。
未知定义、缺少可靠章节信息的合法自定义图回退通用任务，不能从node_id、别名或文件名猜媒体身份。
旧可读布局继续使用原命名提示；英文布局由独立入口显式选择，不借打开旧工程静默改名。
目录首次分配后由存储映射固化，之后的展示变化不移动既有文件。
"""

from __future__ import annotations

from pydantic import ValidationError

from zniku.avenhance_v27 import definitions as av27
from zniku.avenhance_v27.naming import _enhancement_leaf
from zniku.avenhance_v27.probe import Av27MediaError
from zniku.avenhance_v27.profiles import atomic_split_count_from_type_id, is_av27_definition
from zniku.avenhance_v27.template import excel_chapter_label
from zniku.chapter_batch.contracts import BATCH_PREFIX, BatchParameters
from zniku.chapter_batch.definitions import definition_role as batch_role
from zniku.chapter_overlap.definitions import definition_role
from zniku.chapter_overlap.node_contracts import (
    PARAMETER_MODELS,
    ChapterParameters,
    EnhancementParameters,
)
from zniku.graph import NodeDefinition, NodeInstance
from zniku.project.storage_layout import AttemptNamingHint
from zniku.runtime import Run
from zniku.source_admission.definitions import definition_role as admitted_role
from zniku.source_aligned.definitions import definition_role as aligned_role
from zniku.source_aligned.node_contracts import (
    PARAMETER_MODELS as ALIGNED_MODELS,
)
from zniku.source_aligned.node_contracts import (
    ChapterParameters as AlignedChapterParameters,
)
from zniku.source_aligned.node_contracts import (
    EnhancementParameters as AlignedEnhancementParameters,
)
from zniku.source_aligned.node_contracts import (
    ExternalParameters,
)

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

_ENGLISH_NAMES = {
    "split": "chapter-split",
    "enhancement": "enhancement",
    "merge": "chapter-merge",
    "context": "fi-context",
    "fi": "frame-interpolation",
    "crop": "fi-crop",
    "program": "video-encode",
    "final": "audio-mux",
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
    """保持旧可读布局的命名合同；已存在及未来分配的旧工程路径不静默切换风格。"""

    return _resolve_attempt_naming(run, node, definition, english=False)


def resolve_english_attempt_naming(
    run: Run, node: NodeInstance, definition: NodeDefinition
) -> AttemptNamingHint:
    """投影英文工序名与可选章节；不改变参数、媒体文件名或已持久化的目录。

    只识别精确内建合同。未知或不可靠定义统一回退 task，避免把插件 type_id、别名或
    用户文件名当成安全目录或媒体身份。重名隔离由存储分配器负责，不在此处扫描磁盘。
    """

    return _resolve_attempt_naming(run, node, definition, english=True)


def _resolve_attempt_naming(
    run: Run, node: NodeInstance, definition: NodeDefinition, *, english: bool
) -> AttemptNamingHint:
    """只提取已有参数；失败仅回退命名，不替代执行 validator 或阻断自由图。"""

    fallback = AttemptNamingHint(
        task_name="task" if english else definition.type_id.rsplit(".", 1)[-1][:300] or "任务"
    )
    names = _ENGLISH_NAMES if english else _OVERLAP_NAMES
    if (node.type_id, node.definition_version) != (definition.type_id, definition.version):
        return fallback
    new_role = aligned_role(definition) or admitted_role(definition) or batch_role(definition)
    if new_role in {"source", "admission"}:
        return AttemptNamingHint(
            category="common",
            task_name=("source-analysis" if new_role == "source" else "source-admission")
            if english
            else ("素材检查" if new_role == "source" else "素材准入"),
        )
    if new_role is not None:
        try:
            model = (
                BatchParameters
                if node.type_id.startswith(BATCH_PREFIX)
                else ExternalParameters
                if new_role == "external"
                else ALIGNED_MODELS[new_role]
            )
            aligned = model.model_validate(node.model_dump(mode="json")["parameters"], strict=True)
        except (ValidationError, ValueError, Av27MediaError):
            return fallback
        if new_role == "external":
            return AttemptNamingHint(
                category="common", task_name="source-repair" if english else "外部修复"
            )
        name = names[new_role]
        if isinstance(aligned, BatchParameters) and not english:
            name = "章节批量增强"
        if isinstance(aligned, AlignedChapterParameters):
            if isinstance(aligned, AlignedEnhancementParameters):
                name += f"-leaf-{aligned.leaf.ordinal + 1:04d}"
            return _chapter(aligned.chapter.ordinal, name)
        return AttemptNamingHint(
            category="common" if new_role == "split" else "program", task_name=name
        )
    role = definition_role(definition)
    if role is not None:
        try:
            params = PARAMETER_MODELS[role].model_validate(
                node.model_dump(mode="json")["parameters"], strict=True
            )
        except (ValidationError, ValueError, Av27MediaError):
            # 业务跨字段校验仍由执行前 validator 报错；命名不能提前成为另一套运行准入。
            return fallback
        name = names[role]
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
        av27.SOURCE_PROGRAM_TYPE_ID: "source" if english else "源素材",
        av27.SOURCE_ADMISSION_TYPE_ID: "source-analysis" if english else "素材分析",
        av27.MOSAIC_RESTORATION_TYPE_ID: "source-repair" if english else "外部修复",
    }
    program = {
        av27.PROGRAM_ENCODE_TYPE_ID: names["program"],
        av27.FINAL_MUX_TYPE_ID: names["final"],
    }
    if node.type_id in common:
        return AttemptNamingHint(category="common", task_name=common[node.type_id])
    if node.type_id in program:
        return AttemptNamingHint(category="program", task_name=program[node.type_id])
    if atomic_split_count_from_type_id(node.type_id) is not None:
        return AttemptNamingHint(category="common", task_name=names["split"])
    if node.type_id == av27.ENHANCEMENT_TYPE_ID:
        leaf = _enhancement_leaf(node, run.graph_snapshot)
        if leaf is not None:
            segment, ordinal = leaf
            return _chapter(segment.chapter_ordinal, f"{names['enhancement']}-leaf-{ordinal:04d}")
    if node.type_id in {av27.MERGE_VIDEO_TYPE_ID, av27.FRAME_INTERPOLATION_TYPE_ID}:
        chapter_ordinal = node.parameters.get("chapter_ordinal")
        if type(chapter_ordinal) is int and 0 <= chapter_ordinal < 999999:
            name = names["merge"] if node.type_id == av27.MERGE_VIDEO_TYPE_ID else names["fi"]
            return _chapter(chapter_ordinal, name)
    return fallback
