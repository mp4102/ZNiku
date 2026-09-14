"""新 exact 色彩链复用归档名称纯计算，不转换旧定义或改变产物身份。"""

from zniku.graph import NodeDefinition, NodeInstance
from zniku.prepared_source.naming import overlap_output_paths as paths_for_contract
from zniku.runtime.runner import OutputPathSpec

from .definitions import definition_role


def overlap_output_paths(
    node: NodeInstance, definition: NodeDefinition, *, media_basename: str
) -> tuple[OutputPathSpec, ...]:
    return paths_for_contract(
        node, definition, media_basename=media_basename, _role_checker=definition_role
    )
