"""普通工作源的创作者用语；只展示参数和受信角色，不决定媒体是否可处理。"""

from zniku.graph import NodeDefinition
from zniku.prepared_source.work_definitions import definition_role as downstream_role
from zniku.source_preparation.work_definitions import definition_role as source_role

from .chapter_overlap import _ROLE_METADATA
from .models import IconToken, PaletteLevel
from .prepared_color import BINDING_PARAMETERS as BINDING_PARAMETERS
from .prepared_color import PARAMETER_HELP as COLOR_HELP
from .prepared_color import PARAMETER_LABELS as COLOR_LABELS

PARAMETER_LABELS = {
    **COLOR_LABELS,
    "retime_confirmed": "确认保留帧序并按所选帧率重新定时",
    "reference_change_confirmed": "确认采用外部新参考并重新规划",
}
PARAMETER_HELP = {
    **COLOR_HELP,
    "retime_confirmed": "保留解码帧数和顺序，但可能改变播放节奏、时长及音画关系；不会改写原件。",
    "reference_change_confirmed": (
        "外部文件作为新工作参考，不要求像素恒等；重新规划并明确采用其音频。"
    ),
    "strategy_id": "普通工作副本策略，不等于严格保内容修复或已通过真实 AI 验收。",
}
_SOURCE = {
    "source": ("导入原件", "只读登记素材，不要求原片天生符合工作格式。", IconToken.SOURCE),
    "diagnostics": (
        "检查处理兼容性",
        "一次必要扫描明确帧时间轴与处理选项，不做完整内容审计。",
        IconToken.CHECK,
    ),
    "builtin": (
        "制作工作副本",
        "明确确认后保留帧序重新定时，不自动补删帧或覆盖原件。",
        IconToken.TRANSFORM,
    ),
    "external": (
        "导入外部工作源",
        "明确采用外部新参考，重新检查和规划，不宣称与原片像素等价。",
        IconToken.TRANSFORM,
    ),
    "admission": (
        "确认工作参考",
        "复用已完成观察并核对当前输入、工作解释与音频来源。",
        IconToken.CHECK,
    ),
}


def metadata(
    definition: NodeDefinition,
) -> tuple[str, str, str, IconToken, PaletteLevel, tuple[str, ...], tuple[str, ...]]:
    role = source_role(definition)
    if role is not None:
        title, description, icon = _SOURCE[role]
    else:
        role = downstream_role(definition)
        if role is None:
            raise ValueError("普通工作节点定义不匹配")
        if role == "external":
            title, description, icon = (
                "外部马赛克修复",
                "保持当前工作参考的帧数与时间轴；音频仍从明确载体读取。",
                IconToken.TRANSFORM,
            )
        else:
            title, description, icon = _ROLE_METADATA[role]
    return title, description, "prepared", icon, PaletteLevel.ADVANCED, ("工作源", "素材准备"), ()


def port_label(definition: NodeDefinition, direction: str, port_id: str) -> str | None:
    if source_role(definition) is None and downstream_role(definition) is None:
        raise ValueError("普通工作端口没有对应的受信定义")
    return {
        "original_media": "只读原件",
        "reference_media": "当前工作参考",
        "audio_sources": "明确选定的音频载体(有序)",
        "sources": "明确选定的音频载体(有序)",
        "diagnosis": "兼容性检查结果",
        "gate": "工作参考绑定",
    }.get(port_id)
