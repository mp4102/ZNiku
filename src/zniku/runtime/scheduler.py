"""提供与业务节点类型无关的 DAG 调度分析。

Scheduler 只消费已经通过 Graph Core 校验的普通 ``Graph`` 和持久化 NodeRun 状态。``ready`` 与
``blocked`` 仅在本次分析结果中即时计算，绝不成为可写回数据库的状态；模块不理解 Source、Merge、MR
等业务名称，也不生成 ExecutionPlan 或 digest。
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from zniku.graph import Graph

__all__ = [
    "PERSISTED_NODE_STATES",
    "InstantNodeState",
    "ScheduleAnalysis",
    "ScheduledNode",
    "Scheduler",
    "SchedulerError",
]

PERSISTED_NODE_STATES = frozenset({"pending", "running", "waiting_external", "completed", "failed"})


class SchedulerError(ValueError):
    """表示 Graph 或调度输入无法安全分析。"""


class InstantNodeState(StrEnum):
    """只存在于一次调度计算中的 UI/控制状态。"""

    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ScheduledNode:
    """记录选中执行闭包内节点的持久状态与即时调度状态。"""

    node_id: str
    persisted_state: str
    instant_state: InstantNodeState | None
    blocked_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduleAnalysis:
    """按稳定拓扑顺序返回一次无副作用调度分析。"""

    selected_targets: tuple[str, ...]
    selected_node_ids: tuple[str, ...]
    nodes: tuple[ScheduledNode, ...]

    @property
    def ready_node_ids(self) -> tuple[str, ...]:
        """返回当前可以启动的新 ``pending`` attempt。"""

        return tuple(
            node.node_id for node in self.nodes if node.instant_state is InstantNodeState.READY
        )

    @property
    def blocked_node_ids(self) -> tuple[str, ...]:
        """返回因直接依赖尚未 completed 而等待的 ``pending`` 节点。"""

        return tuple(
            node.node_id for node in self.nodes if node.instant_state is InstantNodeState.BLOCKED
        )


class Scheduler:
    """为一张已校验 DAG 提供闭包、稳定拓扑序与 ready 计算。"""

    def __init__(self, graph: Graph) -> None:
        self._graph = graph
        self._node_order = {node.node_id: index for index, node in enumerate(graph.nodes)}
        if len(self._node_order) != len(graph.nodes):
            raise SchedulerError("E_SCHEDULER_NODE_DUPLICATE: Graph 含重复 node_id")

        self._predecessors: dict[str, set[str]] = {node_id: set() for node_id in self._node_order}
        self._successors: dict[str, set[str]] = {node_id: set() for node_id in self._node_order}
        for edge in graph.edges:
            if edge.source_node_id not in self._node_order:
                raise SchedulerError(
                    f"E_SCHEDULER_EDGE_NODE_UNKNOWN: source {edge.source_node_id!r} 不存在"
                )
            if edge.target_node_id not in self._node_order:
                raise SchedulerError(
                    f"E_SCHEDULER_EDGE_NODE_UNKNOWN: target {edge.target_node_id!r} 不存在"
                )
            self._predecessors[edge.target_node_id].add(edge.source_node_id)
            self._successors[edge.source_node_id].add(edge.target_node_id)

        self._topological_order = self._compute_topological_order()
        self._topological_index = {
            node_id: index for index, node_id in enumerate(self._topological_order)
        }

    @property
    def topological_order(self) -> tuple[str, ...]:
        """返回以 Graph.nodes 原始顺序稳定打破并列关系的拓扑序。"""

        return self._topological_order

    def ancestor_closure(self, target_node_ids: Iterable[str] | str) -> tuple[str, ...]:
        """返回目标节点及其全部祖先，结果保持稳定拓扑序。"""

        targets = self._validate_requested_nodes(target_node_ids, purpose="target")
        included = set(targets)
        pending = list(targets)
        while pending:
            node_id = pending.pop()
            for predecessor in self._predecessors[node_id]:
                if predecessor not in included:
                    included.add(predecessor)
                    pending.append(predecessor)
        return tuple(node_id for node_id in self._topological_order if node_id in included)

    def downstream_closure(
        self,
        source_node_ids: Iterable[str] | str,
        *,
        include_sources: bool = True,
    ) -> tuple[str, ...]:
        """返回起点的全部下游；失效传播默认包含起点自身。"""

        sources = self._validate_requested_nodes(source_node_ids, purpose="source")
        included = set(sources) if include_sources else set()
        visited = set(sources)
        pending = list(sources)
        while pending:
            node_id = pending.pop()
            for successor in self._successors[node_id]:
                included.add(successor)
                if successor not in visited:
                    visited.add(successor)
                    pending.append(successor)
        return tuple(node_id for node_id in self._topological_order if node_id in included)

    def analyze(
        self,
        states: Mapping[str, str],
        *,
        selected_targets: Iterable[str] | str | None = None,
    ) -> ScheduleAnalysis:
        """计算选中闭包内 pending 节点的 ready/blocked 即时视图。

        ``running``、``waiting_external``、``completed`` 与 ``failed`` 本身没有即时状态。只有
        ``pending`` 节点会被标成 ready 或 blocked；任一直连前驱未 completed 时即 blocked。
        """

        self._validate_state_values(states)
        unknown_state_nodes = sorted(set(states) - set(self._node_order))
        if unknown_state_nodes:
            raise SchedulerError(
                "E_SCHEDULER_STATE_NODE_UNKNOWN: 状态引用未知节点 " + ", ".join(unknown_state_nodes)
            )

        targets: tuple[str, ...]
        if selected_targets is None:
            targets = ()
            selected = self._topological_order
        else:
            targets = self._validate_requested_nodes(selected_targets, purpose="target")
            selected = self.ancestor_closure(targets)

        missing = tuple(node_id for node_id in selected if node_id not in states)
        if missing:
            raise SchedulerError(
                "E_SCHEDULER_STATE_MISSING: 执行闭包缺少持久状态 " + ", ".join(missing)
            )

        selected_set = set(selected)
        scheduled: list[ScheduledNode] = []
        for node_id in selected:
            state = str(states[node_id])
            if state != "pending":
                scheduled.append(
                    ScheduledNode(
                        node_id=node_id,
                        persisted_state=state,
                        instant_state=None,
                    )
                )
                continue
            blocked_by = tuple(
                predecessor
                for predecessor in self._topological_order
                if predecessor in self._predecessors[node_id]
                and predecessor in selected_set
                and str(states[predecessor]) != "completed"
            )
            instant_state = InstantNodeState.BLOCKED if blocked_by else InstantNodeState.READY
            scheduled.append(
                ScheduledNode(
                    node_id=node_id,
                    persisted_state=state,
                    instant_state=instant_state,
                    blocked_by=blocked_by,
                )
            )
        return ScheduleAnalysis(
            selected_targets=targets,
            selected_node_ids=selected,
            nodes=tuple(scheduled),
        )

    def _compute_topological_order(self) -> tuple[str, ...]:
        indegree = {node_id: len(values) for node_id, values in self._predecessors.items()}
        ready: list[tuple[int, str]] = [
            (self._node_order[node_id], node_id)
            for node_id, degree in indegree.items()
            if degree == 0
        ]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            _, node_id = heapq.heappop(ready)
            ordered.append(node_id)
            for successor in sorted(self._successors[node_id], key=self._node_order.__getitem__):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    heapq.heappush(ready, (self._node_order[successor], successor))
        if len(ordered) != len(self._node_order):
            raise SchedulerError("E_SCHEDULER_GRAPH_CYCLE: Scheduler 只接受已校验 DAG")
        return tuple(ordered)

    def _validate_requested_nodes(
        self,
        node_ids: Iterable[str] | str,
        *,
        purpose: str,
    ) -> tuple[str, ...]:
        values = (node_ids,) if isinstance(node_ids, str) else tuple(node_ids)
        if len(values) != len(set(values)):
            raise SchedulerError(f"E_SCHEDULER_{purpose.upper()}_DUPLICATE: 节点选择不得重复")
        unknown = tuple(node_id for node_id in values if node_id not in self._node_order)
        if unknown:
            raise SchedulerError(
                f"E_SCHEDULER_{purpose.upper()}_UNKNOWN: 未知节点 " + ", ".join(unknown)
            )
        return tuple(sorted(values, key=self._topological_index.__getitem__))

    @staticmethod
    def _validate_state_values(states: Mapping[str, str]) -> None:
        invalid = sorted(
            (node_id, str(state))
            for node_id, state in states.items()
            if str(state) not in PERSISTED_NODE_STATES
        )
        if invalid:
            details = ", ".join(f"{node_id}={state}" for node_id, state in invalid)
            raise SchedulerError(
                "E_SCHEDULER_STATE_INVALID: 只接受 pending/running/waiting_external/"
                f"completed/failed，实际为 {details}"
            )
