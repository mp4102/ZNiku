"""覆盖 Phase 2 纯 Scheduler 的闭包、稳定拓扑与即时状态。"""

from __future__ import annotations

import pytest

from zniku.graph import Edge, Graph, NodeInstance
from zniku.runtime.scheduler import InstantNodeState, Scheduler, SchedulerError


def node(node_id: str) -> NodeInstance:
    return NodeInstance(
        node_id=node_id,
        type_id="example.generic",
        definition_version="2.0.0",
    )


def edge(source: str, target: str) -> Edge:
    return Edge(
        source_node_id=source,
        source_port_id="output",
        target_node_id=target,
        target_port_id="input",
    )


def branched_graph() -> Graph:
    return Graph(
        nodes=tuple(
            node(node_id) for node_id in ("s2", "out2", "s1", "a", "b", "merge", "out1", "isolated")
        ),
        edges=(
            edge("s1", "a"),
            edge("s2", "b"),
            edge("a", "merge"),
            edge("b", "merge"),
            edge("merge", "out1"),
            edge("merge", "out2"),
        ),
    )


def all_pending(graph: Graph) -> dict[str, str]:
    return {node_.node_id: "pending" for node_ in graph.nodes}


def test_topological_order_is_stable_for_branch_and_merge() -> None:
    scheduler = Scheduler(branched_graph())

    assert scheduler.topological_order == (
        "s2",
        "s1",
        "a",
        "b",
        "merge",
        "out2",
        "out1",
        "isolated",
    )
    assert Scheduler(branched_graph()).topological_order == scheduler.topological_order


def test_selected_target_uses_ancestor_closure_and_excludes_other_outputs() -> None:
    graph = branched_graph()
    scheduler = Scheduler(graph)
    states = all_pending(graph)
    states.update({"s1": "completed", "s2": "completed"})

    analysis = scheduler.analyze(states, selected_targets=("out1",))

    assert analysis.selected_targets == ("out1",)
    assert analysis.selected_node_ids == ("s2", "s1", "a", "b", "merge", "out1")
    assert analysis.ready_node_ids == ("a", "b")
    assert analysis.blocked_node_ids == ("merge", "out1")
    assert "out2" not in analysis.selected_node_ids
    assert "isolated" not in analysis.selected_node_ids


def test_multiple_targets_share_ancestors_without_duplicate_work() -> None:
    scheduler = Scheduler(branched_graph())

    closure = scheduler.ancestor_closure(("out1", "out2"))

    assert closure == ("s2", "s1", "a", "b", "merge", "out2", "out1")
    assert closure.count("merge") == 1


def test_downstream_closure_is_stable_and_can_exclude_source() -> None:
    scheduler = Scheduler(branched_graph())

    assert scheduler.downstream_closure("a") == ("a", "merge", "out2", "out1")
    assert scheduler.downstream_closure("a", include_sources=False) == (
        "merge",
        "out2",
        "out1",
    )


def test_failed_dependency_blocks_downstream_without_becoming_persisted_blocked() -> None:
    graph = branched_graph()
    states = all_pending(graph)
    states.update({"s1": "completed", "s2": "completed", "a": "failed", "b": "completed"})

    analysis = Scheduler(graph).analyze(states, selected_targets="out1")
    by_id = {item.node_id: item for item in analysis.nodes}

    assert by_id["a"].instant_state is None
    assert by_id["a"].persisted_state == "failed"
    assert by_id["merge"].instant_state is InstantNodeState.BLOCKED
    assert by_id["merge"].blocked_by == ("a",)
    assert by_id["out1"].instant_state is InstantNodeState.BLOCKED


def test_waiting_external_is_preserved_and_blocks_only_its_downstream() -> None:
    graph = branched_graph()
    states = all_pending(graph)
    states.update(
        {
            "s1": "completed",
            "s2": "completed",
            "a": "pending",
            "b": "waiting_external",
        }
    )

    analysis = Scheduler(graph).analyze(states, selected_targets="out1")
    by_id = {item.node_id: item for item in analysis.nodes}

    assert analysis.ready_node_ids == ("a",)
    assert by_id["b"].persisted_state == "waiting_external"
    assert by_id["b"].instant_state is None
    assert by_id["merge"].blocked_by == ("a", "b")


def test_running_and_completed_nodes_never_receive_ready_or_blocked_state() -> None:
    graph = Graph(nodes=(node("running"), node("completed")))

    analysis = Scheduler(graph).analyze({"running": "running", "completed": "completed"})

    assert analysis.ready_node_ids == ()
    assert analysis.blocked_node_ids == ()
    assert all(item.instant_state is None for item in analysis.nodes)


def test_multiple_sources_zero_outputs_and_empty_selection_are_allowed() -> None:
    graph = Graph(nodes=(node("s1"), node("s2")))
    scheduler = Scheduler(graph)

    analysis = scheduler.analyze({"s1": "pending", "s2": "pending"})
    empty = scheduler.analyze({}, selected_targets=())

    assert analysis.ready_node_ids == ("s1", "s2")
    assert empty.selected_node_ids == ()
    assert empty.nodes == ()


@pytest.mark.parametrize("invalid_state", ["ready", "blocked", "queued", "validating", "stale"])
def test_only_five_persisted_states_are_accepted(invalid_state: str) -> None:
    with pytest.raises(SchedulerError, match="E_SCHEDULER_STATE_INVALID"):
        Scheduler(Graph(nodes=(node("n"),))).analyze({"n": invalid_state})


def test_state_mapping_fails_closed_for_missing_or_unknown_nodes() -> None:
    scheduler = Scheduler(Graph(nodes=(node("n"),)))
    with pytest.raises(SchedulerError, match="E_SCHEDULER_STATE_MISSING"):
        scheduler.analyze({})
    with pytest.raises(SchedulerError, match="E_SCHEDULER_STATE_NODE_UNKNOWN"):
        scheduler.analyze({"n": "pending", "unknown": "completed"})


def test_target_selection_fails_closed_for_unknown_or_duplicate_nodes() -> None:
    scheduler = Scheduler(Graph(nodes=(node("n"),)))
    with pytest.raises(SchedulerError, match="E_SCHEDULER_TARGET_UNKNOWN"):
        scheduler.analyze({"n": "pending"}, selected_targets=("unknown",))
    with pytest.raises(SchedulerError, match="E_SCHEDULER_TARGET_DUPLICATE"):
        scheduler.analyze({"n": "pending"}, selected_targets=("n", "n"))


def test_scheduler_rejects_cycle_even_if_graph_validator_was_skipped() -> None:
    graph = Graph(
        nodes=(node("a"), node("b")),
        edges=(edge("a", "b"), edge("b", "a")),
    )

    with pytest.raises(SchedulerError, match="E_SCHEDULER_GRAPH_CYCLE"):
        Scheduler(graph)
