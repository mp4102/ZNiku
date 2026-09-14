"""复用普通准备图的连接结构，显式选择 work exact 与确认参数，不增加条件运行层。"""

from __future__ import annotations

from typing import Literal

from zniku.graph import Graph, GraphValidator

from . import definitions as old_definitions
from .template import build_preparation_graph as old_graph
from .work_definitions import source_preparation_definitions
from .work_models import (
    SOURCE_PREPARATION_VERSION,
    AdmissionParameters,
    AudioPolicy,
    InterpretationPolicy,
)


def build_preparation_graph(
    source_path: str,
    *,
    route: Literal["diagnose", "direct", "builtin", "external"] = "diagnose",
    target_frame_rate: str | None = None,
    external_format: Literal["mkv", "mp4", "mov"] = "mkv",
    audio_policy: AudioPolicy = "original",
    node_prefix: str = "source-preparation",
    interpretation_policy: InterpretationPolicy = "declared_only",
    retime_confirmed: bool = False,
    reference_change_confirmed: bool = False,
    diagnosis_target_frame_rate: str | None = "inherit",
) -> Graph:
    """只在参数明确确认后建准备路线；外部音频边指向新参考实际 carrier。"""
    AdmissionParameters(
        target_frame_rate=target_frame_rate,
        audio_policy=audio_policy,
        interpretation_policy=interpretation_policy,
    )
    if route == "builtin" and not retime_confirmed:
        raise ValueError("内置工作准备必须明确确认保留帧数/顺序并改变时间")
    if route == "external" and not reference_change_confirmed:
        raise ValueError("外部新参考必须明确确认变化并重新规划")
    base = old_graph(
        source_path,
        route=route,
        target_frame_rate=target_frame_rate,
        external_format=external_format,
        audio_policy="none" if audio_policy == "none" else "original",
        node_prefix=node_prefix,
    )
    mapping = dict(
        zip(
            (d.type_id for d in old_definitions.source_preparation_definitions()),
            source_preparation_definitions(),
            strict=True,
        )
    )
    nodes = []
    for node in base.nodes:
        definition = mapping[node.type_id]
        params = dict(node.parameters)
        role = node.node_id.removeprefix(f"{node_prefix}-")
        if role == "diagnostics" and diagnosis_target_frame_rate != "inherit":
            params["target_frame_rate"] = diagnosis_target_frame_rate
        if role == "prepare":
            params = {"target_frame_rate": target_frame_rate}
            if route == "builtin":
                params.update(strategy_id="frame-retime-ffv1/1", retime_confirmed=True)
            else:
                params["reference_change_confirmed"] = True
        if role == "admission":
            params.update(
                audio_policy="reference" if route == "external" else audio_policy,
                interpretation_policy=interpretation_policy,
            )
        nodes.append(
            node.model_copy(
                update={
                    "type_id": definition.type_id,
                    "definition_version": SOURCE_PREPARATION_VERSION,
                    "parameters": params,
                }
            )
        )
    edges = tuple(
        edge.model_copy(update={"source_node_id": f"{node_prefix}-prepare"})
        if route == "external" and edge.target_port_id == "audio_sources"
        else edge
        for edge in base.edges
    )
    graph = Graph(nodes=tuple(nodes), edges=edges)
    GraphValidator(source_preparation_definitions()).validate(graph)
    return graph
