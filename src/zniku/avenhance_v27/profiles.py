"""提供 AVEnhanceFlow v2.7 definition profile 的稳定只读 helper。

本模块只索引专用定义身份并辨认动态 AtomicSplit family；它不检查图拓扑、不生成 NodeInstance，
也不承担 Phase 4 的 preview/create/expand 或 profile preflight。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from zniku.graph import NodeDefinition

from .definitions import (
    ATOMIC_SPLIT_TYPE_PREFIX,
    AV27_NODE_VERSION,
    built_in_av27_definitions,
)

_ATOMIC_SPLIT_TYPE_PATTERN = re.compile(
    rf"^{re.escape(ATOMIC_SPLIT_TYPE_PREFIX)}(?P<count>[1-9][0-9]*)$"
)


def atomic_split_count_from_type_id(type_id: str) -> int | None:
    """严格解析动态 Split identity；前导零、零或其他 namespace 返回 ``None``。"""

    matched = _ATOMIC_SPLIT_TYPE_PATTERN.fullmatch(type_id)
    return int(matched.group("count")) if matched is not None else None


def av27_definition_catalog(leaf_count: int = 1) -> Mapping[tuple[str, str], NodeDefinition]:
    """按 exact ``type_id/version`` 返回不可变目录，重复身份立即失败。"""

    catalog: dict[tuple[str, str], NodeDefinition] = {}
    for definition in built_in_av27_definitions(leaf_count):
        identity = (definition.type_id, definition.version)
        if identity in catalog:
            raise ValueError(f"E_AV27_DEFINITION_DUPLICATE: {identity[0]}@{identity[1]} 重复")
        catalog[identity] = definition
    return MappingProxyType(catalog)


def is_av27_definition(definition: NodeDefinition) -> bool:
    """判断 definition 是否属于已冻结的 v27 identity family，不声称其 Graph profile 合法。"""

    if definition.version != AV27_NODE_VERSION:
        return False
    split_count = atomic_split_count_from_type_id(definition.type_id)
    if split_count is not None and len(definition.output_ports) != split_count:
        # 先用已经物化的 shape 拒绝夸大 count，避免仅凭不可信 type_id 分配巨大端口表。
        return False
    expected = av27_definition_catalog(split_count or 1).get(
        (definition.type_id, definition.version)
    )
    return expected == definition
