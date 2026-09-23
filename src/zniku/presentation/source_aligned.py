"""为原片规划与外部前处理提供纯中文展示；精确字段约束仍只来自节点 Schema。"""

from zniku.chapter_batch.contracts import BATCH_PREFIX
from zniku.chapter_batch.definitions import definition_role as batch_role
from zniku.chapter_batch.final_publish import is_definition as is_final_publish
from zniku.chapter_batch.fused import definition_role as fused_role
from zniku.graph import NodeDefinition
from zniku.source_admission.definitions import definition_role as admitted_role
from zniku.source_aligned.definitions import definition_role

from .chapter_overlap import _ROLE_METADATA
from .models import IconToken, PaletteLevel

PARAMETER_LABELS = {
    "leaves": "本章分叶清单",
    "source": "原片与准入绑定",
    "declared_container": "外部输出封装",
    "operator_frame_order_confirmed": "确认外部处理未剪辑、变速或改变帧序",
}
PARAMETER_HELP = {
    "leaves": "章内有序完整分叶；共享增强设置，全部产物通过后一次提交整章。",
    "source": "规划以原片为准；实际处理文件由直接输入绑定，不填写未来产物身份。",
    "operator_frame_order_confirmed": "这是操作者的处理约定，不是软件对逐帧画面或所用模型的证明。",
    "declared_container": "应与外部软件实际输出一致。导入只复制，不转码，也不靠改后缀转换封装。",
}


def metadata(
    definition: NodeDefinition,
) -> tuple[str, str, str, IconToken, PaletteLevel, tuple[str, ...], tuple[str, ...]]:
    """纯展示复用旧角色名称，不把相同 type_id 的旧合同当作新定义。"""
    role = (
        admitted_role(definition) if definition.version == "0.3.5" else definition_role(definition)
    )
    role = role or batch_role(definition) or fused_role(definition)
    if role is None:
        raise ValueError("原片规划 definition 与正式 exact identity/Schema/executor 不一致")
    if fused_role(definition) == "program":
        return (
            "精确选帧并连续编码",
            "融合候选：直接读取各章 FI raw，按责任区间选帧；不生成整套裁后 ProRes。",
            "overlap",
            IconToken.ENCODE,
            PaletteLevel.ADVANCED,
            ("融合", "裁边", "连续编码", "候选"),
            (),
        )
    if is_final_publish(definition) or fused_role(definition) == "final":
        return (
            "成片封装并发布",
            "在成片目录的独占候选区封装原音轨，检查通过后发布；不再复制完整成片，也不移动上游。",
            "overlap",
            IconToken.MUX,
            PaletteLevel.ADVANCED,
            ("成片", "封装", "发布"),
            ("target_path", "overwrite"),
        )
    if role in {"source", "admission"}:
        return (
            "读取参考视频" if role == "source" else "参考视频准入",
            "按 AVEnhanceFlow v2.7.0 的媒体规则分析当前参考；失败可显式选用外部修复候选。",
            "av27",
            IconToken.SOURCE,
            PaletteLevel.ADVANCED,
            ("参考视频", "AV2.7 准入"),
            (),
        )
    title, description, icon = (
        (
            "章节批量增强",
            "本章所有分叶共用增强设置；可分批收件，齐全后一次检查和提交。",
            IconToken.TRANSFORM,
        )
        if definition.type_id.startswith(BATCH_PREFIX)
        else (
            "外部马赛克修复",
            "可选第0步：交付完整原片的外部处理结果，验证同帧数、时间轴与几何后进入分章分叶。",
            IconToken.TRANSFORM,
        )
        if role == "external"
        else _ROLE_METADATA[role]
    )
    return title, description, "overlap", icon, PaletteLevel.ADVANCED, ("原片规划", "外部处理"), ()
