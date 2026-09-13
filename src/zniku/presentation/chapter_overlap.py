"""声明重叠补帧候选的中文展示，不增加参数约束或改变执行语义。

仅接受与正式 Python NodeDefinition 完整相同的内建节点；未知或漂移的 namespace 不回退为可信
标题。所有计划、来源和上下文字段仍由原 Schema 表单读写，不嵌入可执行 UI 或媒体计算。
"""

from __future__ import annotations

import re
from functools import lru_cache

from zniku.chapter_overlap.definitions import (
    atomic_split_definition,
    built_in_overlap_definitions,
)
from zniku.chapter_overlap.node_contracts import ATOMIC_SPLIT_TYPE_PREFIX, ROLE_TYPES
from zniku.graph import NodeDefinition

from .models import IconToken, PaletteLevel

PARAMETER_LABELS = {
    "plan": "精确章节与分叶计划",
    "source": "直接来源绑定",
    "chapter": "正式章节范围",
    "leaf": "章内处理段范围",
    "source_media_artifact_id": "原始音轨来源绑定",
    "admission_artifact_id": "素材准入绑定",
    "fi_profile": "Aion 候选与上下文设置",
    "chapter_count": "章节总数",
}
BINDING_PARAMETERS = frozenset(
    {"plan", "source", "chapter", "leaf", "source_media_artifact_id", "admission_artifact_id"}
)
PARAMETER_HELP = {
    "plan": "Python 计算的章叶范围；修改必须满足原 Schema 和节点局部验证。不会自动重建其他节点。",
    "source": "直接输入的 Artifact 身份与精确时间轴；不得通过文件名猜测或改写为其他来源。",
    "chapter": "零基半开源帧区间；不包含 FI 重叠上下文。",
    "leaf": "章内增强片段；与正式章节绑定，不跨章。",
    "fi_profile": (
        "软件 v1.0 / Aion 待真实验收。默认前后 32 帧、最短输入 2 帧是工程默认，"
        "不是已验证的模型要求。"
    ),
}
_ROLE_METADATA: dict[str, tuple[str, str, IconToken]] = {
    "split": (
        "分章与章内均衡分叶",
        "按平均章数或精确切点分章，再按最大时长在章内均衡分叶。",
        IconToken.SPLIT,
    ),
    "enhancement": (
        "外部逐叶增强",
        "把当前处理段交给外部工具增强；提交结果不得改变精确帧数或来源。",
        IconToken.TRANSFORM,
    ),
    "merge": (
        "合并章节增强段",
        "按章内顺序合并已完成增强片段，为补帧准备完整章节。",
        IconToken.MERGE,
    ),
    "context": (
        "准备补帧上下文",
        "等待直接连入的章节增强结果，收集本章及所需相邻画面；没有预计等待时间。",
        IconToken.MERGE,
    ),
    "fi": (
        "外部上下文补帧",
        "使用包含上下文的输入完成 Aion 补帧；保留外部原始结果，后续节点另行裁边。候选待真实验收。",
        IconToken.TRANSFORM,
    ),
    "crop": (
        "精确裁边补帧章节",
        "根据已绑定上下文裁出正式章节，不覆盖外部原始补帧结果。",
        IconToken.SPLIT,
    ),
    "program": (
        "连续编码重叠补帧成片",
        "按顺序读取裁边章节并连续编码，只在全片最后补一帧，不生成强制整片 ProRes。",
        IconToken.ENCODE,
    ),
    "final": (
        "封装重叠补帧成片",
        "把连续视频与原音轨封装；仍执行新处理链的最终媒体合同。",
        IconToken.MUX,
    ),
}
_STATIC_DEFINITIONS = {
    definition.type_id: definition for definition in built_in_overlap_definitions()
}


@lru_cache(maxsize=32)
def _split_definition(count: int) -> NodeDefinition:
    """按有限叶数缓存不可变 definition，避免每次刷新重复生成大型 Schema。"""
    return atomic_split_definition(count)


def port_label(definition: NodeDefinition, direction: str, port_id: str) -> str | None:
    """按正式端口身份补充上下文、raw 与 cropped 角色，不从路径猜测。"""
    role = next(
        (name for name, type_id in ROLE_TYPES.items() if definition.type_id == type_id), None
    )
    return {
        ("context", "input", "chapters"): "所需章节增强视频 (有序)",
        ("context", "output", "video"): "包含邻章上下文的补帧输入",
        ("fi", "input", "video"): "包含邻章上下文的补帧输入",
        ("fi", "output", "video"): "外部原始补帧结果 (保留)",
        ("crop", "input", "video"): "外部原始补帧结果 (保留)",
        ("crop", "output", "video"): "精确裁边后章节视频",
        ("program", "input", "chapters"): "精确裁边后章节视频 (有序)",
    }.get((role or "", direction, port_id))


def metadata(
    definition: NodeDefinition,
) -> tuple[str, str, str, IconToken, PaletteLevel, tuple[str, ...], tuple[str, ...]]:
    """严格绑定动态 Split 或七个固定 definition；漂移时由 catalog 转为阻断错误。"""

    role = next(
        (name for name, type_id in ROLE_TYPES.items() if definition.type_id == type_id), None
    )
    expected = _STATIC_DEFINITIONS.get(definition.type_id)
    if definition.type_id.startswith(ATOMIC_SPLIT_TYPE_PREFIX):
        suffix = definition.type_id.removeprefix(ATOMIC_SPLIT_TYPE_PREFIX)
        if re.fullmatch(r"[1-9][0-9]{0,4}", suffix) is None or int(suffix) > 10_000:
            raise ValueError("重叠分叶 definition 数量无效")
        expected = _split_definition(int(suffix))
        role = "split"
    if expected != definition or role is None:
        raise ValueError("重叠补帧 definition 与正式 exact identity / Schema / executor 不一致")
    title, description, icon = _ROLE_METADATA[role]
    return (
        title,
        description,
        "overlap",
        icon,
        PaletteLevel.ADVANCED,
        ("ZNIKU", "重叠", "FI", "候选"),
        (),
    )
