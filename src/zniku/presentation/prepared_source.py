"""声明工作源准备与新处理链的中文展示，不承担准入或策略晋级。

每个条目先核对完整 exact definition；参数分组与端口文字只说明直接输入角色。是否可用、
是否保内容及是否能继续由业务节点与服务决定，不能由展示标题、文件名或颜色推导。
"""

from __future__ import annotations

from zniku.graph import NodeDefinition
from zniku.prepared_source.definitions import definition_role as downstream_role
from zniku.source_preparation.definitions import definition_role as preparation_definition_role

from .chapter_overlap import _ROLE_METADATA
from .chapter_overlap import BINDING_PARAMETERS as OVERLAP_BINDINGS
from .chapter_overlap import PARAMETER_HELP as OVERLAP_HELP
from .chapter_overlap import PARAMETER_LABELS as OVERLAP_LABELS
from .models import IconToken, PaletteLevel

PARAMETER_LABELS = {
    **OVERLAP_LABELS,
    "source": "原件、工作参考与音频绑定",
    "target_frame_rate": "有依据的目标精确帧率",
    "strategy_id": "工作副本策略版本",
    "audio_policy": "工作源音频约定",
    "declared_container": "外部结果实际封装",
    "operator_frame_order_confirmed": "确认马赛克修复保留工作参考帧序",
}
PARAMETER_HELP = {
    **OVERLAP_HELP,
    "source": "原件、已准入工作参考、有效处理视频和音频是独立直接输入；不同路径不代表开启 MR。",
    "target_frame_rate": "只接受有依据的精确有理数；不从近似小数、文件名或画面内容猜测。",
    "strategy_id": "策略由产品闭合目录提供；填写参数不能启用尚未完成验证的策略。",
    "audio_policy": "音频与视频分别核对。使用原件音频不意味着其原视频必须先满足工作时钟。",
    "declared_container": "填写外部软件实际输出封装；选择文件只复制，不转换封装或自动提交。",
    "operator_frame_order_confirmed": "这是外部马赛克修复约定，不是保内容兼容修复或逐帧画质证明。",
}
BINDING_PARAMETERS = OVERLAP_BINDINGS

_PREPARATION_METADATA: dict[str, tuple[str, str, IconToken]] = {
    "source": (
        "保留原件导入",
        "只读登记本机实体媒体，原件不复制、不改名、不覆盖。",
        IconToken.SOURCE,
    ),
    "diagnostics": (
        "素材检查",
        "完整检查视频时钟与音频关系；发现不兼容不等于文件损坏。",
        IconToken.CHECK,
    ),
    "builtin": (
        "内置素材准备",
        "按已启用策略生成独立工作副本；内容验证通过前不是可用产物。",
        IconToken.TRANSFORM,
    ),
    "external": (
        "外部保内容素材修复",
        "提交与原件内容保持一致的兼容修复结果；不符合时可另建新工作源工程。",
        IconToken.TRANSFORM,
    ),
    "admission": (
        "工作源准入",
        "独立核对原件、工作参考与音频关系，通过后才允许精确规划。",
        IconToken.CHECK,
    ),
}


def _role(definition: NodeDefinition) -> tuple[str | None, bool]:
    """使用业务 definition 的完整匹配；namespace 只选择目录，不证明媒体合法。"""
    role = downstream_role(definition)
    if role is not None:
        return role, True
    preparation_role = preparation_definition_role(definition)
    if preparation_role == "video_prepare":
        preparation_role = "builtin"
    return preparation_role if isinstance(preparation_role, str) else None, False


def metadata(
    definition: NodeDefinition,
) -> tuple[str, str, str, IconToken, PaletteLevel, tuple[str, ...], tuple[str, ...]]:
    """将可信 exact definition 投影为标题；未知或漂移定义交给 catalog 失败关闭。"""
    role, downstream = _role(definition)
    if role is None:
        raise ValueError("工作源 definition 与正式 exact identity/Schema/executor 不一致")
    if downstream and role == "external":
        title, description, icon = (
            "外部工作参考马赛克修复",
            "以准入后的完整工作参考执行可选 MR；不替代素材兼容修复，音频仍来自显式音轨载体。",
            IconToken.TRANSFORM,
        )
    elif downstream:
        title, description, icon = _ROLE_METADATA[role]
        if role == "final":
            description = "将连续视频与显式绑定音频封装；不从 MR 或增强结果偷换音轨。"
    else:
        title, description, icon = _PREPARATION_METADATA[role]
    return (
        title,
        description,
        "prepared",
        icon,
        PaletteLevel.ADVANCED,
        ("工作源", "素材准备", "0.3.4"),
        (),
    )


def port_label(definition: NodeDefinition, direction: str, port_id: str) -> str | None:
    """依照实际端口与 exact 角色显示四类来源，绝不从文件路径推导角色。"""
    role, downstream = _role(definition)
    if role is None:
        raise ValueError("工作源端口展示没有匹配的 exact definition")
    common = {
        "original_media": "只读原件",
        "reference_media": "待准入工作参考",
        "audio_sources": "显式音频载体 (有序)",
        "diagnosis": "已完成的素材检查报告",
        "diagnostics": "已完成的素材检查报告",
        "gate": "工作参考与音频准入绑定",
    }
    if downstream:
        return {
            ("context", "input", "chapters"): "所需章节增强视频 (有序)",
            ("context", "output", "video"): "包含邻章上下文的补帧输入",
            ("fi", "input", "video"): "包含邻章上下文的补帧输入",
            ("fi", "output", "video"): "外部原始补帧结果 (保留)",
            ("crop", "input", "video"): "外部原始补帧结果 (保留)",
            ("crop", "output", "video"): "精确裁边后章节视频",
            ("program", "input", "chapters"): "精确裁边后章节视频 (有序)",
            ("final", "input", "sources"): "显式音频载体 (有序)",
            ("external", "input", "video"): "已准入工作参考视频",
            ("external", "output", "video"): "外部 MR 有效处理视频",
        }.get((role, direction, port_id), common.get(port_id))
    return {
        ("source", "output", "media"): "只读原件",
        ("diagnostics", "input", "original_media"): "待检查原件",
        ("diagnostics", "output", "diagnosis"): "完整素材检查报告",
        ("builtin", "output", "media"): "已验证工作副本",
        ("external", "output", "media"): "已验证外部保内容修复结果",
        ("admission", "output", "video"): "已准入工作参考视频",
    }.get((role, direction, port_id), common.get(port_id))
