"""覆盖 Phase 2 completed reuse 与 latest-result stale 传播。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from zniku.graph import Edge, Graph, NodeInstance
from zniku.runtime.reuse import (
    InputEdgeSignature,
    ReuseBlockReason,
    ReuseCandidate,
    StaleReason,
    analyze_reuse,
    analyze_stale,
    capture_node_signature,
)


def node(node_id: str, *, version: str = "2.0.0", strength: int = 1) -> NodeInstance:
    return NodeInstance(
        node_id=node_id,
        type_id="example.generic",
        definition_version=version,
        parameters={"strength": strength},
    )


def edge(source: str, target: str, *, ordinal: int | None = None) -> Edge:
    return Edge(
        source_node_id=source,
        source_port_id="output",
        target_node_id=target,
        target_port_id="input" if ordinal is None else "items",
        ordinal=ordinal,
    )


def reuse_graph() -> Graph:
    return Graph(
        nodes=tuple(node(node_id) for node_id in ("source", "a", "b", "merge", "sink", "other")),
        edges=(
            edge("source", "a"),
            edge("source", "b"),
            edge("a", "merge", ordinal=0),
            edge("b", "merge", ordinal=1),
            edge("merge", "sink"),
        ),
    )


def input_ids() -> dict[str, tuple[str, ...]]:
    return {
        "source": (),
        "a": ("source-v1",),
        "b": ("source-v1",),
        "merge": ("a-v1", "b-v1"),
        "sink": ("merge-v1",),
        "other": (),
    }


def latest_candidates(graph: Graph) -> dict[str, ReuseCandidate]:
    values: dict[str, ReuseCandidate] = {}
    for node_id, artifacts in input_ids().items():
        values[node_id] = ReuseCandidate(
            result_id=f"result-{node_id}",
            state="completed",
            signature=capture_node_signature(graph, node_id, input_artifact_ids=artifacts),
            output_artifact_ids=(f"output-{node_id}",),
        )
    return values


def healthy_outputs() -> dict[str, bool]:
    return {f"output-{node_id}": True for node_id in input_ids()}


def replace_graph_node(graph: Graph, changed: NodeInstance) -> Graph:
    return graph.model_copy(
        update={
            "nodes": tuple(
                changed if current.node_id == changed.node_id else current
                for current in graph.nodes
            )
        }
    )


def test_completed_result_is_reused_by_structured_values_without_digest() -> None:
    graph = reuse_graph()
    signature = capture_node_signature(graph, "a", input_artifact_ids=("source-v1",))
    candidate = ReuseCandidate(
        result_id="result-a",
        state="completed",
        signature=signature,
        output_artifact_ids=("output-a",),
    )

    decision = analyze_reuse(signature, candidate, output_quick_probe={"output-a": True})

    assert decision.reusable is True
    assert decision.reused_result_id == "result-a"
    assert decision.reasons == ()
    assert not hasattr(signature, "digest")
    assert not hasattr(signature, "canonical_json")


def test_no_previous_result_has_stable_reason() -> None:
    signature = capture_node_signature(reuse_graph(), "source")

    decision = analyze_reuse(signature, None, output_quick_probe={})

    assert decision.reasons == (ReuseBlockReason.NO_PREVIOUS_RESULT,)


def test_reuse_reports_all_structural_changes_in_stable_order() -> None:
    graph = reuse_graph()
    previous = capture_node_signature(graph, "a", input_artifact_ids=("source-v1",))
    current = replace(
        previous,
        type_id="example.changed",
        definition_version="2.0.1",
        parameter_structure=("object", (("strength", ("integer", "2")),)),
        incoming_edges=(
            InputEdgeSignature(
                target_port_id="input",
                ordinal=None,
                source_node_id="b",
                source_port_id="output",
            ),
        ),
        input_artifact_ids=("source-v2",),
    )
    candidate = ReuseCandidate(
        result_id="result-a",
        state="failed",
        signature=previous,
        output_artifact_ids=("output-a",),
        latest_stale=True,
    )

    decision = analyze_reuse(current, candidate, output_quick_probe={"output-a": False})

    assert decision.reasons == (
        ReuseBlockReason.RESULT_NOT_COMPLETED,
        ReuseBlockReason.LATEST_RESULT_STALE,
        ReuseBlockReason.TYPE_CHANGED,
        ReuseBlockReason.DEFINITION_VERSION_CHANGED,
        ReuseBlockReason.PARAMETERS_CHANGED,
        ReuseBlockReason.INCOMING_EDGES_CHANGED,
        ReuseBlockReason.INPUT_ARTIFACTS_CHANGED,
        ReuseBlockReason.OUTPUT_MISSING_OR_UNREADABLE,
    )
    assert decision.reused_result_id is None


def test_graph_edge_array_order_does_not_create_false_stale() -> None:
    graph = reuse_graph()
    reordered = graph.model_copy(update={"edges": tuple(reversed(graph.edges))})

    assert capture_node_signature(
        graph, "merge", input_artifact_ids=("a-v1", "b-v1")
    ) == capture_node_signature(reordered, "merge", input_artifact_ids=("a-v1", "b-v1"))


def test_ordered_many_ordinal_change_is_structural_change() -> None:
    graph = reuse_graph()
    changed_edges = tuple(
        edge("a", "merge", ordinal=1)
        if current.source_node_id == "a" and current.target_node_id == "merge"
        else edge("b", "merge", ordinal=0)
        if current.source_node_id == "b" and current.target_node_id == "merge"
        else current
        for current in graph.edges
    )
    changed = graph.model_copy(update={"edges": changed_edges})

    assert capture_node_signature(
        graph, "merge", input_artifact_ids=("a-v1", "b-v1")
    ) != capture_node_signature(changed, "merge", input_artifact_ids=("b-v1", "a-v1"))


def test_output_probe_must_explicitly_pass_for_every_registered_output() -> None:
    signature = capture_node_signature(reuse_graph(), "source", input_artifact_ids=())
    candidate = ReuseCandidate(
        result_id="result-source",
        state="completed",
        signature=signature,
        output_artifact_ids=("one", "two"),
    )

    for probes in ({"one": True}, {"one": True, "two": False}):
        decision = analyze_reuse(signature, candidate, output_quick_probe=probes)
        assert decision.reasons == (ReuseBlockReason.OUTPUT_MISSING_OR_UNREADABLE,)


def test_graph_edit_can_skip_new_input_artifact_comparison_until_node_is_ready() -> None:
    graph = reuse_graph()

    analysis = analyze_stale(
        graph,
        latest_candidates(graph),
        current_input_artifact_ids={},
        output_quick_probe=healthy_outputs(),
    )

    assert analysis.stale_node_ids == ()
    deferred = capture_node_signature(graph, "merge", input_artifact_ids=None)
    assert deferred.input_artifact_ids is None


def test_removed_node_head_is_stale_without_deleting_historical_result() -> None:
    old_graph = reuse_graph()
    latest = latest_candidates(old_graph)
    new_graph = old_graph.model_copy(
        update={
            "nodes": tuple(item for item in old_graph.nodes if item.node_id != "other"),
        }
    )

    analysis = analyze_stale(
        new_graph,
        latest,
        current_input_artifact_ids={},
        output_quick_probe=healthy_outputs(),
    )

    assert analysis.stale_node_ids == ("other",)
    assert analysis.nodes[0].reasons == (StaleReason.NODE_REMOVED,)
    assert latest["other"].latest_stale is False


@pytest.mark.parametrize(
    ("change", "expected_reason", "expected_stale"),
    [
        ("parameter", StaleReason.PARAMETERS_CHANGED, ("b", "merge", "sink")),
        (
            "version",
            StaleReason.DEFINITION_VERSION_CHANGED,
            ("a", "merge", "sink"),
        ),
        ("edge", StaleReason.INCOMING_EDGES_CHANGED, ("merge", "sink")),
        (
            "input_artifact",
            StaleReason.UPSTREAM_ARTIFACT_CHANGED,
            ("merge", "sink"),
        ),
        (
            "output_missing",
            StaleReason.OUTPUT_MISSING_OR_UNREADABLE,
            ("a", "merge", "sink"),
        ),
    ],
)
def test_each_latest_result_change_marks_node_and_all_downstream_stale(
    change: str,
    expected_reason: StaleReason,
    expected_stale: tuple[str, ...],
) -> None:
    graph = reuse_graph()
    latest = latest_candidates(graph)
    current_graph = graph
    current_inputs = input_ids()
    probes = healthy_outputs()
    direct_node = {
        "parameter": "b",
        "version": "a",
        "edge": "merge",
        "input_artifact": "merge",
        "output_missing": "a",
    }[change]
    if change == "parameter":
        current_graph = replace_graph_node(graph, node("b", strength=2))
    elif change == "version":
        current_graph = replace_graph_node(graph, node("a", version="2.0.1"))
    elif change == "edge":
        current_graph = graph.model_copy(
            update={
                "edges": tuple(
                    Edge(
                        source_node_id="source",
                        source_port_id="output",
                        target_node_id="merge",
                        target_port_id="items",
                        ordinal=0,
                    )
                    if item.source_node_id == "a" and item.target_node_id == "merge"
                    else item
                    for item in graph.edges
                )
            }
        )
    elif change == "input_artifact":
        current_inputs["merge"] = ("a-v2", "b-v1")
    else:
        probes["output-a"] = False

    analysis = analyze_stale(
        current_graph,
        latest,
        current_input_artifact_ids=current_inputs,
        output_quick_probe=probes,
    )

    assert analysis.stale_node_ids == expected_stale
    reasons = {item.node_id: item.reasons for item in analysis.nodes}
    assert expected_reason in reasons[direct_node]
    for downstream in set(expected_stale) - {direct_node}:
        assert StaleReason.UPSTREAM_STALE in reasons[downstream]


def test_user_rerun_marks_selected_node_and_all_downstream_but_not_other_branch() -> None:
    graph = reuse_graph()
    latest = latest_candidates(graph)

    analysis = analyze_stale(
        graph,
        latest,
        current_input_artifact_ids=input_ids(),
        output_quick_probe=healthy_outputs(),
        rerun_node_ids=("a",),
    )

    assert analysis.stale_node_ids == ("a", "merge", "sink")
    by_id = {item.node_id: item.reasons for item in analysis.nodes}
    assert by_id["a"] == (StaleReason.USER_RERUN,)
    assert by_id["merge"] == (StaleReason.UPSTREAM_STALE,)
    assert "b" not in by_id
    assert "other" not in by_id


def test_stale_analysis_does_not_modify_historical_candidate_values() -> None:
    graph = reuse_graph()
    latest = latest_candidates(graph)
    before = dict(latest)

    analyze_stale(
        graph,
        latest,
        current_input_artifact_ids=input_ids(),
        output_quick_probe={**healthy_outputs(), "output-a": False},
    )

    assert latest == before
    assert all(candidate.latest_stale is False for candidate in latest.values())
