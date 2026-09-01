"""提供 completed 结果复用与 Project latest-result stale 的纯分析。

本模块只比较结构化值：节点 type/version、参数、入边 (含 ordinal)、直接输入 artifact ID 与调用方提供
的输出 quick-probe 结果。它不计算 canonical JSON 或 digest，也不修改历史 NodeRun；stale 结果仅供
Project Service 更新对应 NodeInstance 的 latest-result 投影。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from zniku.graph import Graph

from .scheduler import PERSISTED_NODE_STATES, Scheduler

__all__ = [
    "InputEdgeSignature",
    "NodeReuseSignature",
    "ReuseBlockReason",
    "ReuseCandidate",
    "ReuseDecision",
    "StaleAnalysis",
    "StaleNode",
    "StaleReason",
    "analyze_reuse",
    "analyze_stale",
    "capture_node_signature",
]


def _structured_json(value: Any) -> object:
    """生成保留 JSON 类型且与 object 键顺序无关的结构化比较值。"""

    if isinstance(value, Mapping):
        return (
            "object",
            tuple(
                (str(key), _structured_json(item))
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            ),
        )
    if isinstance(value, list | tuple):
        return ("array", tuple(_structured_json(item) for item in value))
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int):
        return ("integer", str(value))
    if isinstance(value, float):
        return ("number", value.hex())
    if isinstance(value, str):
        return ("string", value)
    raise TypeError(f"E_REUSE_PARAMETERS_NON_JSON: 不支持参数类型 {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class InputEdgeSignature:
    """描述当前节点的一条直接入边，不依赖 Graph 中的数组位置。"""

    target_port_id: str
    ordinal: int | None
    source_node_id: str
    source_port_id: str


@dataclass(frozen=True, slots=True)
class NodeReuseSignature:
    """保存判断结果是否仍适用于当前 NodeInstance 的结构快照。"""

    type_id: str
    definition_version: str
    parameter_structure: object
    incoming_edges: tuple[InputEdgeSignature, ...]
    input_artifact_ids: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class ReuseCandidate:
    """表示 Project 中一个 NodeInstance 的 latest NodeResult 候选。"""

    result_id: str
    state: str
    signature: NodeReuseSignature
    output_artifact_ids: tuple[str, ...] = ()
    latest_stale: bool = False

    def __post_init__(self) -> None:
        if not self.result_id:
            raise ValueError("E_REUSE_RESULT_ID_EMPTY: result_id 不得为空")
        if self.state not in PERSISTED_NODE_STATES:
            raise ValueError(f"E_REUSE_STATE_INVALID: 未知持久状态 {self.state!r}")
        if self.signature.input_artifact_ids is None:
            raise ValueError(
                "E_REUSE_CANDIDATE_INPUTS_UNKNOWN: 历史结果必须记录直接输入 artifact ID"
            )
        if any(not artifact_id for artifact_id in self.output_artifact_ids):
            raise ValueError("E_REUSE_OUTPUT_ARTIFACT_ID_EMPTY: artifact_id 不得为空")


class ReuseBlockReason(StrEnum):
    """按固定优先级描述 completed 结果不可复用的原因。"""

    NO_PREVIOUS_RESULT = "no_previous_result"
    RESULT_NOT_COMPLETED = "result_not_completed"
    LATEST_RESULT_STALE = "latest_result_stale"
    TYPE_CHANGED = "type_changed"
    DEFINITION_VERSION_CHANGED = "definition_version_changed"
    PARAMETERS_CHANGED = "parameters_changed"
    INCOMING_EDGES_CHANGED = "incoming_edges_changed"
    INPUT_ARTIFACTS_CHANGED = "input_artifacts_changed"
    OUTPUT_MISSING_OR_UNREADABLE = "output_missing_or_unreadable"


@dataclass(frozen=True, slots=True)
class ReuseDecision:
    """返回是否复用以及稳定、可呈现的阻断原因。"""

    reusable: bool
    reused_result_id: str | None
    reasons: tuple[ReuseBlockReason, ...]


def capture_node_signature(
    graph: Graph,
    node_id: str,
    *,
    input_artifact_ids: tuple[str, ...] | None = None,
) -> NodeReuseSignature:
    """从 Graph 捕获不含 UI 位置和 digest 的节点复用结构。

    ``input_artifact_ids`` 若提供，必须按本函数生成的 ``incoming_edges`` 顺序排列：先 target port，
    再 ordinal，最后 source node/port。传 ``None`` 表示图编辑阶段尚无新输入绑定，复用检查会跳过
    artifact ID 比较；节点 ready 后正式复用判断必须提供实际 ID。
    """

    matches = tuple(node for node in graph.nodes if node.node_id == node_id)
    if len(matches) != 1:
        raise ValueError(f"E_REUSE_NODE_UNKNOWN_OR_DUPLICATE: node {node_id!r} 不存在或不唯一")
    node = matches[0]
    incoming = tuple(
        sorted(
            (
                InputEdgeSignature(
                    target_port_id=edge.target_port_id,
                    ordinal=edge.ordinal,
                    source_node_id=edge.source_node_id,
                    source_port_id=edge.source_port_id,
                )
                for edge in graph.edges
                if edge.target_node_id == node_id
            ),
            key=lambda item: (
                item.target_port_id,
                -1 if item.ordinal is None else item.ordinal,
                item.source_node_id,
                item.source_port_id,
            ),
        )
    )
    if input_artifact_ids is not None and len(input_artifact_ids) != len(incoming):
        raise ValueError("E_REUSE_INPUT_ARTIFACT_COUNT: 每条直接入边必须对应一个 input artifact ID")
    if input_artifact_ids is not None and any(
        not artifact_id for artifact_id in input_artifact_ids
    ):
        raise ValueError("E_REUSE_INPUT_ARTIFACT_ID_EMPTY: artifact_id 不得为空")
    return NodeReuseSignature(
        type_id=node.type_id,
        definition_version=node.definition_version,
        parameter_structure=_structured_json(node.parameters),
        incoming_edges=incoming,
        input_artifact_ids=input_artifact_ids,
    )


def analyze_reuse(
    current: NodeReuseSignature,
    candidate: ReuseCandidate | None,
    *,
    output_quick_probe: Mapping[str, bool],
) -> ReuseDecision:
    """比较当前结构与 latest result，且要求其每个输出 quick probe 明确为 True。"""

    if candidate is None:
        return ReuseDecision(
            reusable=False,
            reused_result_id=None,
            reasons=(ReuseBlockReason.NO_PREVIOUS_RESULT,),
        )

    reasons: list[ReuseBlockReason] = []
    if candidate.state != "completed":
        reasons.append(ReuseBlockReason.RESULT_NOT_COMPLETED)
    if candidate.latest_stale:
        reasons.append(ReuseBlockReason.LATEST_RESULT_STALE)
    previous = candidate.signature
    if current.type_id != previous.type_id:
        reasons.append(ReuseBlockReason.TYPE_CHANGED)
    if current.definition_version != previous.definition_version:
        reasons.append(ReuseBlockReason.DEFINITION_VERSION_CHANGED)
    if current.parameter_structure != previous.parameter_structure:
        reasons.append(ReuseBlockReason.PARAMETERS_CHANGED)
    if current.incoming_edges != previous.incoming_edges:
        reasons.append(ReuseBlockReason.INCOMING_EDGES_CHANGED)
    if (
        current.input_artifact_ids is not None
        and current.input_artifact_ids != previous.input_artifact_ids
    ):
        reasons.append(ReuseBlockReason.INPUT_ARTIFACTS_CHANGED)
    if any(
        output_quick_probe.get(artifact_id) is not True
        for artifact_id in candidate.output_artifact_ids
    ):
        reasons.append(ReuseBlockReason.OUTPUT_MISSING_OR_UNREADABLE)

    reusable = not reasons
    return ReuseDecision(
        reusable=reusable,
        reused_result_id=candidate.result_id if reusable else None,
        reasons=tuple(reasons),
    )


class StaleReason(StrEnum):
    """描述 latest result 被标为不可复用的直接或传播原因。"""

    RESULT_NOT_COMPLETED = "result_not_completed"
    ALREADY_STALE = "already_stale"
    NODE_REMOVED = "node_removed"
    TYPE_CHANGED = "type_changed"
    DEFINITION_VERSION_CHANGED = "definition_version_changed"
    PARAMETERS_CHANGED = "parameters_changed"
    INCOMING_EDGES_CHANGED = "incoming_edges_changed"
    UPSTREAM_ARTIFACT_CHANGED = "upstream_artifact_changed"
    OUTPUT_MISSING_OR_UNREADABLE = "output_missing_or_unreadable"
    USER_RERUN = "user_rerun"
    UPSTREAM_STALE = "upstream_stale"


_REUSE_TO_STALE = {
    ReuseBlockReason.RESULT_NOT_COMPLETED: StaleReason.RESULT_NOT_COMPLETED,
    ReuseBlockReason.LATEST_RESULT_STALE: StaleReason.ALREADY_STALE,
    ReuseBlockReason.TYPE_CHANGED: StaleReason.TYPE_CHANGED,
    ReuseBlockReason.DEFINITION_VERSION_CHANGED: StaleReason.DEFINITION_VERSION_CHANGED,
    ReuseBlockReason.PARAMETERS_CHANGED: StaleReason.PARAMETERS_CHANGED,
    ReuseBlockReason.INCOMING_EDGES_CHANGED: StaleReason.INCOMING_EDGES_CHANGED,
    ReuseBlockReason.INPUT_ARTIFACTS_CHANGED: StaleReason.UPSTREAM_ARTIFACT_CHANGED,
    ReuseBlockReason.OUTPUT_MISSING_OR_UNREADABLE: StaleReason.OUTPUT_MISSING_OR_UNREADABLE,
}


@dataclass(frozen=True, slots=True)
class StaleNode:
    """记录一个 NodeInstance latest result 的全部稳定失效原因。"""

    node_id: str
    reasons: tuple[StaleReason, ...]


@dataclass(frozen=True, slots=True)
class StaleAnalysis:
    """按稳定拓扑序返回应更新 Project latest-result 投影的节点。"""

    nodes: tuple[StaleNode, ...]

    @property
    def stale_node_ids(self) -> tuple[str, ...]:
        """返回需要更新 latest-result stale 投影的节点 ID。"""

        return tuple(node.node_id for node in self.nodes)


def analyze_stale(
    graph: Graph,
    latest_results: Mapping[str, ReuseCandidate],
    *,
    current_input_artifact_ids: Mapping[str, tuple[str, ...] | None],
    output_quick_probe: Mapping[str, bool],
    rerun_node_ids: tuple[str, ...] = (),
) -> StaleAnalysis:
    """分析当前 Project latest results 并向现行 Graph 的全部下游传播 stale。

    返回值不包含任何 NodeRun，也不会改变 ``latest_results``。Project Service 只需把列出的 node_id
    标记到独立 latest-result 投影，历史 Run/NodeRun 始终保持原值。若节点已从当前 Graph 删除，本函数
    会标记该节点 head 为 stale，但无法从新 Graph 推断旧下游；保存图时应先用旧 Graph 分析传播，再保存
    新 Graph。
    """

    scheduler = Scheduler(graph)
    graph_node_ids = set(scheduler.topological_order)
    removed_node_ids = tuple(sorted(set(latest_results) - graph_node_ids))
    known_input_nodes = graph_node_ids | set(latest_results)
    unknown_inputs = sorted(set(current_input_artifact_ids) - known_input_nodes)
    if unknown_inputs:
        raise ValueError(
            "E_STALE_INPUT_NODE_UNKNOWN: input artifact 引用未知节点 " + ", ".join(unknown_inputs)
        )

    reasons_by_node: dict[str, list[StaleReason]] = {}
    for node_id in removed_node_ids:
        _append_reason(reasons_by_node, node_id, StaleReason.NODE_REMOVED)
    for node_id in scheduler.topological_order:
        candidate = latest_results.get(node_id)
        if candidate is None:
            continue
        current = capture_node_signature(
            graph,
            node_id,
            input_artifact_ids=current_input_artifact_ids.get(node_id),
        )
        decision = analyze_reuse(
            current,
            candidate,
            output_quick_probe=output_quick_probe,
        )
        for reuse_reason in decision.reasons:
            stale_reason = _REUSE_TO_STALE.get(reuse_reason)
            if stale_reason is not None:
                _append_reason(reasons_by_node, node_id, stale_reason)

    rerun_ids = scheduler.downstream_closure(rerun_node_ids) if rerun_node_ids else ()
    rerun_sources = set(rerun_node_ids)
    for node_id in rerun_ids:
        if node_id not in latest_results:
            continue
        rerun_reason = (
            StaleReason.USER_RERUN if node_id in rerun_sources else StaleReason.UPSTREAM_STALE
        )
        _append_reason(reasons_by_node, node_id, rerun_reason)

    direct_stale = tuple(node_id for node_id in reasons_by_node if node_id in graph_node_ids)
    for source_node_id in direct_stale:
        for node_id in scheduler.downstream_closure(source_node_id, include_sources=False):
            if node_id in latest_results:
                _append_reason(reasons_by_node, node_id, StaleReason.UPSTREAM_STALE)

    return StaleAnalysis(
        nodes=tuple(
            StaleNode(node_id=node_id, reasons=tuple(reasons_by_node[node_id]))
            for node_id in (*scheduler.topological_order, *removed_node_ids)
            if node_id in reasons_by_node
        )
    )


def _append_reason(
    reasons_by_node: dict[str, list[StaleReason]],
    node_id: str,
    reason: StaleReason,
) -> None:
    values = reasons_by_node.setdefault(node_id, [])
    if reason not in values:
        values.append(reason)
