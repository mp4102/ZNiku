"""普通工作链复用纯归档命名；受信 exact 检查不接受同名第三方定义。"""

from zniku.graph import NodeDefinition, NodeInstance
from zniku.runtime.runner import OutputPathSpec

from .naming import overlap_output_paths as paths_for_contract
from .work_definitions import definition_role


def overlap_output_paths(
    node: NodeInstance, definition: NodeDefinition, *, media_basename: str
) -> tuple[OutputPathSpec, ...]:
    return paths_for_contract(
        node, definition, media_basename=media_basename, _role_checker=definition_role
    )
