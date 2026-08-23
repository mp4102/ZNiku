"""覆盖 0.2.0 GraphValidator 的端口、输入顺序、参数与 DAG 不变量。"""

from __future__ import annotations

import pytest
from pydantic import JsonValue

from zniku.graph import (
    JSON_SCHEMA_DIALECT,
    Cardinality,
    Edge,
    ExecutionMode,
    Graph,
    GraphValidationError,
    GraphValidator,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)


def definition(
    type_id: str,
    *,
    inputs: tuple[PortSpec, ...] = (),
    outputs: tuple[PortSpec, ...] = (),
    schema: dict[str, JsonValue] | None = None,
    version: str = "2.0.0",
) -> NodeDefinition:
    return NodeDefinition(
        type_id=type_id,
        version=version,
        input_ports=inputs,
        output_ports=outputs,
        parameter_schema=schema
        or {
            "$schema": JSON_SCHEMA_DIALECT,
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=f"plugins.{type_id}:run"),
    )


def node(node_id: str, definition_: NodeDefinition, **parameters: JsonValue) -> NodeInstance:
    return NodeInstance(
        node_id=node_id,
        type_id=definition_.type_id,
        definition_version=definition_.version,
        parameters=parameters,
    )


def edge(
    source: str,
    source_port: str,
    target: str,
    target_port: str,
    *,
    ordinal: int | None = None,
) -> Edge:
    return Edge(
        source_node_id=source,
        source_port_id=source_port,
        target_node_id=target,
        target_port_id=target_port,
        ordinal=ordinal,
    )


def codes(error: GraphValidationError) -> set[str]:
    return {item.code for item in error.violations}


def test_valid_graph_supports_multiple_sources_outputs_and_arbitrary_branch_merge() -> None:
    video_source = definition(
        "builtin.video_source",
        outputs=(PortSpec(port_id="video", data_type="VideoFile"),),
    )
    second_source = definition(
        "builtin.second_source",
        outputs=(PortSpec(port_id="video", data_type="VideoFile"),),
    )
    transform = definition(
        "builtin.transform",
        inputs=(PortSpec(port_id="input", data_type="VideoFile", required=True),),
        outputs=(PortSpec(port_id="output", data_type="VideoFile"),),
    )
    merge = definition(
        "builtin.merge",
        inputs=(
            PortSpec(
                port_id="items",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
        outputs=(PortSpec(port_id="output", data_type="VideoFile"),),
    )
    local_probe = definition(
        "builtin.local_probe",
        inputs=(PortSpec(port_id="input", data_type="VideoFile", required=True),),
    )
    graph = Graph(
        nodes=(
            node("source-a", video_source),
            node("source-b", second_source),
            node("transform-a", transform),
            node("merge", merge),
            node("probe-a", local_probe),
            node("probe-b", local_probe),
        ),
        edges=(
            edge("source-a", "video", "transform-a", "input"),
            edge("transform-a", "output", "merge", "items", ordinal=0),
            edge("source-b", "video", "merge", "items", ordinal=1),
            edge("merge", "output", "probe-a", "input"),
            edge("merge", "output", "probe-b", "input"),
        ),
    )

    GraphValidator((video_source, second_source, transform, merge, local_probe)).validate(graph)


def test_graph_with_no_output_or_edge_is_a_valid_local_experiment() -> None:
    source = definition(
        "builtin.source",
        outputs=(PortSpec(port_id="media", data_type="MediaFile"),),
    )

    GraphValidator((source,)).validate(Graph(nodes=(node("source", source),)))


def test_definition_binding_is_exact_and_unknown_versions_fail_closed() -> None:
    source = definition("builtin.source", version="2.0.0")
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id="builtin.source",
                definition_version="2.0.1",
            ),
        )
    )

    with pytest.raises(GraphValidationError) as captured:
        GraphValidator((source,)).validate(graph)
    assert codes(captured.value) == {"E_DEFINITION_UNKNOWN"}


def test_duplicate_definition_identity_is_rejected() -> None:
    source = definition("builtin.source")
    with pytest.raises(ValueError, match="E_DEFINITION_DUPLICATE"):
        GraphValidator((source, source.model_copy()))


def test_duplicate_node_and_unknown_edge_endpoints_fail() -> None:
    source = definition(
        "builtin.source", outputs=(PortSpec(port_id="media", data_type="MediaFile"),)
    )
    graph = Graph(
        nodes=(node("same", source), node("same", source)),
        edges=(edge("missing", "media", "also-missing", "input"),),
    )

    violations = GraphValidator((source,)).inspect(graph)
    assert {item.code for item in violations} == {
        "E_NODE_DUPLICATE",
        "E_EDGE_SOURCE_NODE_UNKNOWN",
        "E_EDGE_TARGET_NODE_UNKNOWN",
    }


def test_ports_must_exist_in_the_correct_direction() -> None:
    source = definition(
        "builtin.source", outputs=(PortSpec(port_id="media", data_type="MediaFile"),)
    )
    sink = definition(
        "builtin.sink",
        inputs=(PortSpec(port_id="input", data_type="MediaFile", required=True),),
    )
    graph = Graph(
        nodes=(node("source", source), node("sink", sink)),
        edges=(edge("source", "unknown", "sink", "also-unknown"),),
    )

    with pytest.raises(GraphValidationError) as captured:
        GraphValidator((source, sink)).validate(graph)
    assert codes(captured.value) == {
        "E_EDGE_SOURCE_PORT_UNKNOWN",
        "E_EDGE_TARGET_PORT_UNKNOWN",
        "E_REQUIRED_INPUT_MISSING",
    }


def test_port_types_require_exact_equality_without_implicit_media_conversion() -> None:
    source = definition(
        "builtin.source", outputs=(PortSpec(port_id="media", data_type="MediaFile"),)
    )
    sink = definition(
        "builtin.sink",
        inputs=(PortSpec(port_id="video", data_type="VideoFile", required=True),),
    )
    graph = Graph(
        nodes=(node("source", source), node("sink", sink)),
        edges=(edge("source", "media", "sink", "video"),),
    )

    with pytest.raises(GraphValidationError) as captured:
        GraphValidator((source, sink)).validate(graph)
    assert codes(captured.value) == {"E_PORT_TYPE_INCOMPATIBLE"}


def test_required_input_and_one_input_single_edge_are_enforced() -> None:
    source = definition(
        "builtin.source", outputs=(PortSpec(port_id="video", data_type="VideoFile"),)
    )
    sink = definition(
        "builtin.sink",
        inputs=(PortSpec(port_id="input", data_type="VideoFile", required=True),),
    )
    missing = Graph(nodes=(node("sink", sink),))
    with pytest.raises(GraphValidationError) as captured:
        GraphValidator((sink,)).validate(missing)
    assert codes(captured.value) == {"E_REQUIRED_INPUT_MISSING"}

    multiple = Graph(
        nodes=(node("a", source), node("b", source), node("sink", sink)),
        edges=(
            edge("a", "video", "sink", "input"),
            edge("b", "video", "sink", "input"),
        ),
    )
    with pytest.raises(GraphValidationError) as captured:
        GraphValidator((source, sink)).validate(multiple)
    assert codes(captured.value) == {"E_INPUT_MULTIPLE_EDGES"}


@pytest.mark.parametrize(
    ("ordinals", "expected_code"),
    [
        ((None, 1), "E_ORDINAL_REQUIRED"),
        ((0, 0), "E_ORDINAL_DUPLICATE"),
        ((0, 2), "E_ORDINAL_NON_CONTIGUOUS"),
    ],
)
def test_ordered_many_requires_unique_contiguous_ordinals(
    ordinals: tuple[int | None, int | None], expected_code: str
) -> None:
    source = definition(
        "builtin.source", outputs=(PortSpec(port_id="video", data_type="VideoFile"),)
    )
    merge = definition(
        "builtin.merge",
        inputs=(
            PortSpec(
                port_id="items",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY,
                required=True,
            ),
        ),
    )
    graph = Graph(
        nodes=(node("a", source), node("b", source), node("merge", merge)),
        edges=(
            edge("a", "video", "merge", "items", ordinal=ordinals[0]),
            edge("b", "video", "merge", "items", ordinal=ordinals[1]),
        ),
    )

    with pytest.raises(GraphValidationError) as captured:
        GraphValidator((source, merge)).validate(graph)
    assert expected_code in codes(captured.value)


def test_one_input_rejects_ordinal() -> None:
    source = definition(
        "builtin.source", outputs=(PortSpec(port_id="video", data_type="VideoFile"),)
    )
    sink = definition(
        "builtin.sink",
        inputs=(PortSpec(port_id="input", data_type="VideoFile", required=True),),
    )
    graph = Graph(
        nodes=(node("source", source), node("sink", sink)),
        edges=(edge("source", "video", "sink", "input", ordinal=0),),
    )

    with pytest.raises(GraphValidationError) as captured:
        GraphValidator((source, sink)).validate(graph)
    assert codes(captured.value) == {"E_ORDINAL_NOT_ALLOWED"}


def test_node_parameters_are_validated_by_bound_definition_schema() -> None:
    transform = definition(
        "builtin.transform",
        schema={
            "$schema": JSON_SCHEMA_DIALECT,
            "type": "object",
            "properties": {"strength": {"type": "integer", "minimum": 0, "maximum": 10}},
            "required": ["strength"],
            "additionalProperties": False,
        },
    )

    parameter_cases: tuple[dict[str, JsonValue], ...] = (
        {"strength": 11},
        {"strength": 4, "unknown": True},
        {},
    )
    for parameters in parameter_cases:
        graph = Graph(
            nodes=(
                NodeInstance(
                    node_id="transform",
                    type_id=transform.type_id,
                    definition_version=transform.version,
                    parameters=parameters,
                ),
            )
        )
        with pytest.raises(GraphValidationError) as captured:
            GraphValidator((transform,)).validate(graph)
        assert codes(captured.value) == {"E_PARAMETERS_INVALID"}


def test_cycle_is_rejected_but_arbitrary_acyclic_branch_is_allowed() -> None:
    transform = definition(
        "builtin.transform",
        inputs=(PortSpec(port_id="input", data_type="VideoFile"),),
        outputs=(PortSpec(port_id="output", data_type="VideoFile"),),
    )
    graph = Graph(
        nodes=(node("a", transform), node("b", transform), node("c", transform)),
        edges=(
            edge("a", "output", "b", "input"),
            edge("b", "output", "c", "input"),
            edge("c", "output", "a", "input"),
        ),
    )

    with pytest.raises(GraphValidationError) as captured:
        GraphValidator((transform,)).validate(graph)
    assert "E_GRAPH_CYCLE" in codes(captured.value)
