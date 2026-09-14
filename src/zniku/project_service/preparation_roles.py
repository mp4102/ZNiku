"""合并完整 exact 身份供进度、取消和存储展示使用，不合并其媒体准入语义。

仅这类通用能力可以识别多版；版本化报告、路线选择和下游处理仍分别进入对应合同。
"""

from zniku.graph import NodeDefinition
from zniku.source_color.definitions import definition_role as color_role
from zniku.source_preparation.definitions import definition_role as original_role
from zniku.source_preparation.work_definitions import definition_role as work_role


def definition_role(definition: NodeDefinition) -> str | None:
    """完整匹配任一受信定义；不能凭同名 validator、type_id 或版本字符串授权。"""
    return original_role(definition) or color_role(definition) or work_role(definition)
