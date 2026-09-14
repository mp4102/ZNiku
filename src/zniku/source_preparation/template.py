"""把明确的检查/准备选择展开为普通 Graph，不在 Runtime 中实现条件路由。"""

from __future__ import annotations

from typing import Literal, cast

from pydantic import JsonValue

from zniku.graph import Edge, Graph, GraphValidator, NodeInstance, UiPosition

from .definitions import (
    ADMISSION_TYPE_ID,
    BUILTIN_PREPARE_TYPE_ID,
    DIAGNOSTICS_TYPE_ID,
    EXTERNAL_REPAIR_TYPE_PREFIX,
    SOURCE_TYPE_ID,
    source_preparation_definitions,
)
from .models import SOURCE_PREPARATION_VERSION


def build_preparation_graph(
    source_path: str,
    *,
    route: Literal["diagnose", "direct", "builtin", "external"] = "diagnose",
    target_frame_rate: str | None = None,
    external_format: Literal["mkv", "mp4", "mov"] = "mkv",
    audio_policy: Literal["original", "none"] = "original",
    node_prefix: str = "source-preparation",
) -> Graph:
    """生成稳定节点 ID；切换选择只是新 Graph 配置，不改变任何正在运行的 snapshot。"""
    if route not in {"diagnose", "direct", "builtin", "external"}:
        raise ValueError("未知素材准备路线")
    nodes: list[NodeInstance] = []
    edges: list[Edge] = []

    def add(role: str, type_id: str, parameters: dict[str, object]) -> str:
        node_id = f"{node_prefix}-{role}"
        nodes.append(
            NodeInstance(
                node_id=node_id,
                type_id=type_id,
                definition_version=SOURCE_PREPARATION_VERSION,
                parameters=cast(dict[str, JsonValue], parameters),
                ui_position=UiPosition(x=float(len(nodes) * 300), y=160.0),
            )
        )
        return node_id

    def edge(
        source: str, source_port: str, target: str, target_port: str, ordinal: int | None = None
    ) -> None:
        edges.append(
            Edge(
                source_node_id=source,
                source_port_id=source_port,
                target_node_id=target,
                target_port_id=target_port,
                ordinal=ordinal,
            )
        )

    source = add("source", SOURCE_TYPE_ID, {"source_path": source_path})
    diagnosis = add("diagnostics", DIAGNOSTICS_TYPE_ID, {"target_frame_rate": target_frame_rate})
    edge(source, "media", diagnosis, "original_media")
    reference = source
    if route in {"builtin", "external"}:
        if target_frame_rate is None:
            raise ValueError("修复路线必须明确目标精确 FPS")
        parameters: dict[str, object] = {"target_frame_rate": target_frame_rate}
        if route == "builtin":
            parameters["strategy_id"] = "t1-clock-quantization/1"
        reference = add(
            "prepare",
            BUILTIN_PREPARE_TYPE_ID
            if route == "builtin"
            else (EXTERNAL_REPAIR_TYPE_PREFIX + external_format),
            parameters,
        )
        edge(source, "media", reference, "original_media")
        edge(diagnosis, "diagnosis", reference, "diagnosis")
    if route != "diagnose":
        admission = add(
            "admission",
            ADMISSION_TYPE_ID,
            {
                "target_frame_rate": target_frame_rate,
                "audio_policy": audio_policy,
            },
        )
        edge(source, "media", admission, "original_media")
        edge(reference, "media", admission, "reference_media")
        edge(source, "media", admission, "audio_sources", 0)
        edge(diagnosis, "diagnosis", admission, "diagnosis")
    graph = Graph(nodes=tuple(nodes), edges=tuple(edges))
    GraphValidator(source_preparation_definitions()).validate(graph)
    return graph
