"""将明确的准备路线与工作解释写入同一普通 Graph，不修改旧 builder。"""

from __future__ import annotations

from typing import Literal

from zniku.graph import Graph, GraphValidator
from zniku.source_preparation.template import build_preparation_graph as previous_graph

from .definitions import ADMISSION_TYPE_ID, BUILTIN_PREPARE_TYPE_ID, source_preparation_definitions
from .models import SOURCE_PREPARATION_VERSION, AdmissionParameters, InterpretationPolicy


def build_preparation_graph(
    source_path: str,
    *,
    route: Literal["diagnose", "direct", "builtin", "external"] = "diagnose",
    target_frame_rate: str | None = None,
    external_format: Literal["mkv", "mp4", "mov"] = "mkv",
    audio_policy: Literal["original", "none"] = "original",
    node_prefix: str = "source-preparation",
    interpretation_policy: InterpretationPolicy = "declared_only",
) -> Graph:
    """仅复用静态拓扑；新的参数和 exact 定义完整重新验证。"""
    AdmissionParameters(audio_policy=audio_policy, interpretation_policy=interpretation_policy)
    old = previous_graph(
        source_path,
        route=route,
        target_frame_rate=target_frame_rate,
        external_format=external_format,
        audio_policy=audio_policy,
        node_prefix=node_prefix,
    )
    nodes = []
    for node in old.nodes:
        parameters = dict(node.parameters)
        if node.type_id == ADMISSION_TYPE_ID:
            parameters["interpretation_policy"] = interpretation_policy
        if node.type_id == BUILTIN_PREPARE_TYPE_ID:
            parameters["strategy_id"] = "t1-clock-quantization/2"
        nodes.append(
            node.model_copy(
                update={"definition_version": SOURCE_PREPARATION_VERSION, "parameters": parameters}
            )
        )
    graph = Graph(nodes=tuple(nodes), edges=old.edges)
    GraphValidator(source_preparation_definitions()).validate(graph)
    return graph
