"""覆盖 0.2.0 Graph Core 严格模型、精确版本与 JSON round-trip。"""

from __future__ import annotations

import pytest
from pydantic import JsonValue, ValidationError

from zniku.graph import (
    JSON_SCHEMA_DIALECT,
    Cardinality,
    CommandExecutorSpec,
    CorePortType,
    Edge,
    ExecutionMode,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
    UiPosition,
    ValidatorSpec,
)


def parameter_schema() -> dict[str, JsonValue]:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": {
            "strength": {"type": "integer", "minimum": 0, "maximum": 10},
            "model": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "required": ["strength"],
        "additionalProperties": False,
    }


def test_graph_models_round_trip_without_legacy_authority_fields() -> None:
    definition = NodeDefinition(
        type_id="builtin.video_transform",
        version="2.0.0-rc.1+local",
        input_ports=(
            PortSpec(
                port_id="input",
                data_type="VideoFile",
                cardinality=Cardinality.ONE,
                required=True,
            ),
        ),
        output_ports=(PortSpec(port_id="output", data_type="VideoFile"),),
        parameter_schema=parameter_schema(),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="zniku_nodes.video:transform"),
        validator=ValidatorSpec(adapter="zniku_nodes.video:validate_transform"),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="transform-1",
                type_id=definition.type_id,
                definition_version=definition.version,
                parameters={"strength": 4, "model": "synthetic"},
                ui_position=UiPosition(x=120.5, y=-8.0),
            ),
        ),
        edges=(),
    )

    assert NodeDefinition.model_validate_json(definition.model_dump_json()) == definition
    assert Graph.model_validate_json(graph.model_dump_json()) == graph
    data = graph.model_dump(mode="json")
    assert set(data) == {"nodes", "edges"}
    assert set(data["nodes"][0]) == {
        "node_id",
        "type_id",
        "definition_version",
        "parameters",
        "ui_position",
    }
    assert "scope" not in graph.model_dump_json()
    assert "digest" not in graph.model_dump_json()


def test_core_port_types_are_discoverable_without_closing_plugin_extension() -> None:
    assert {item.value for item in CorePortType} == {
        "MediaFile",
        "VideoFile",
        "AudioFile",
        "DataFile",
    }
    core_port = PortSpec(port_id="video", data_type=CorePortType.VIDEO_FILE)
    plugin_port = PortSpec(port_id="subtitle", data_type="example.vendor.SubtitleFile")

    assert core_port.data_type == "VideoFile"
    assert plugin_port.data_type == "example.vendor.SubtitleFile"


@pytest.mark.parametrize("version", ["2", "2.0", "v2.0.0", "2.0.x", ">=2.0.0", "02.0.0"])
def test_definition_and_instance_reject_non_exact_versions(version: str) -> None:
    with pytest.raises(ValidationError):
        NodeInstance(
            node_id="node-1",
            type_id="builtin.source",
            definition_version=version,
        )


def test_all_models_fail_closed_on_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Graph.model_validate({"nodes": (), "edges": (), "final_node_id": "output-1"})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PortSpec.model_validate(
            {
                "port_id": "input",
                "data_type": "VideoFile",
                "scope": "program",
            }
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CommandExecutorSpec.model_validate(
            {
                "kind": "command",
                "executable": "ffmpeg",
                "argv": (),
                "shell": True,
            }
        )


def test_node_definition_rejects_invalid_parameter_schema() -> None:
    with pytest.raises(ValidationError, match="E_PARAMETER_SCHEMA_INVALID"):
        NodeDefinition(
            type_id="builtin.invalid",
            version="2.0.0",
            parameter_schema={
                "$schema": JSON_SCHEMA_DIALECT,
                "type": "object",
                "properties": {"strength": {"type": "not-a-json-type"}},
                "additionalProperties": False,
            },
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=PythonExecutorSpec(adapter="plugins.invalid:run"),
        )

    with pytest.raises(ValidationError, match="E_PARAMETER_SCHEMA_ROOT"):
        NodeDefinition(
            type_id="builtin.invalid",
            version="2.0.0",
            parameter_schema={"type": "array", "items": {"type": "string"}},
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=PythonExecutorSpec(adapter="plugins.invalid:run"),
        )


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    (
        ("executable", "python\x00.exe", "E_COMMAND_EXECUTABLE_NUL"),
        ("argv", ("-c", "value\x00suffix"), "E_COMMAND_ARGV_NUL"),
    ),
)
def test_command_executor_rejects_nul(
    field: str,
    value: str | tuple[str, ...],
    error_code: str,
) -> None:
    values: dict[str, object] = {"executable": "python", "argv": ("-V",)}
    values[field] = value

    with pytest.raises(ValidationError, match=error_code):
        CommandExecutorSpec.model_validate(values)


def test_node_definition_allows_same_port_id_in_opposite_directions() -> None:
    definition = NodeDefinition(
        type_id="builtin.transform",
        version="2.0.0",
        input_ports=(PortSpec(port_id="media", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="media", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="plugins.transform:run"),
    )

    assert definition.input_ports[0].port_id == definition.output_ports[0].port_id


def test_node_definition_rejects_duplicate_ports_within_each_direction() -> None:
    with pytest.raises(ValidationError, match="E_INPUT_PORT_DUPLICATE"):
        NodeDefinition(
            type_id="builtin.invalid",
            version="2.0.0",
            input_ports=(
                PortSpec(port_id="media", data_type="VideoFile"),
                PortSpec(port_id="media", data_type="VideoFile"),
            ),
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=PythonExecutorSpec(adapter="plugins.invalid:run"),
        )
    with pytest.raises(ValidationError, match="E_OUTPUT_PORT_DUPLICATE"):
        NodeDefinition(
            type_id="builtin.invalid",
            version="2.0.0",
            output_ports=(
                PortSpec(port_id="media", data_type="VideoFile"),
                PortSpec(port_id="media", data_type="VideoFile"),
            ),
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=PythonExecutorSpec(adapter="plugins.invalid:run"),
        )


def test_node_definition_rejects_output_input_only_rules() -> None:
    with pytest.raises(ValidationError, match="E_OUTPUT_ORDERED_MANY"):
        NodeDefinition(
            type_id="builtin.invalid",
            version="2.0.0",
            output_ports=(
                PortSpec(
                    port_id="media",
                    data_type="VideoFile",
                    cardinality=Cardinality.ORDERED_MANY,
                ),
            ),
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=PythonExecutorSpec(adapter="plugins.invalid:run"),
        )
    with pytest.raises(ValidationError, match="E_OUTPUT_REQUIRED"):
        NodeDefinition(
            type_id="builtin.invalid",
            version="2.0.0",
            output_ports=(PortSpec(port_id="media", data_type="VideoFile", required=True),),
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=PythonExecutorSpec(adapter="plugins.invalid:run"),
        )


def test_json_values_are_defensively_copied_and_deeply_frozen() -> None:
    schema: dict[str, JsonValue] = {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": {
            "filters": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
        "additionalProperties": False,
    }
    parameters: dict[str, JsonValue] = {"filters": ["denoise", "scale"]}
    definition = NodeDefinition(
        type_id="builtin.transform",
        version="2.0.0",
        parameter_schema=schema,
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="plugins.transform:run"),
    )
    instance = NodeInstance(
        node_id="transform",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters=parameters,
    )

    schema["type"] = "array"
    source_filters = parameters["filters"]
    assert isinstance(source_filters, list)
    source_filters.append("encode")
    assert definition.parameter_schema["type"] == "object"
    assert instance.parameters["filters"] == ["denoise", "scale"]

    with pytest.raises(TypeError, match="不可原地修改"):
        definition.parameter_schema["type"] = "array"
    frozen_filters = instance.parameters["filters"]
    assert isinstance(frozen_filters, list)
    with pytest.raises(TypeError, match="不可原地修改"):
        frozen_filters.append("encode")

    assert NodeDefinition.model_validate_json(definition.model_dump_json()) == definition
    assert NodeInstance.model_validate_json(instance.model_dump_json()) == instance
    assert instance.model_dump(mode="json")["parameters"] == {"filters": ["denoise", "scale"]}


def test_execution_mode_and_executor_must_agree() -> None:
    with pytest.raises(ValidationError, match="E_EXECUTOR_MODE_MISMATCH"):
        NodeDefinition(
            type_id="builtin.external",
            version="2.0.0",
            execution_mode=ExecutionMode.MANUAL_EXTERNAL,
            executor=CommandExecutorSpec(executable="ffmpeg", argv=("-version",)),
        )
    with pytest.raises(ValidationError, match="E_EXECUTOR_MODE_MISMATCH"):
        NodeDefinition(
            type_id="builtin.automatic",
            version="2.0.0",
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=ManualExternalExecutorSpec(instructions="导出后提交文件"),
        )


def test_command_executor_preserves_argv_and_has_no_shell_switch() -> None:
    executor = CommandExecutorSpec(
        executable="C:/Program Files/ffmpeg/bin/ffmpeg.exe",
        argv=("-i", "{input}", "-vf", "scale=1920:1080", "{output}"),
    )

    assert executor.argv[1] == "{input}"
    assert set(executor.model_dump()) == {"kind", "executable", "argv"}


def test_edge_ordinal_is_non_negative_when_present() -> None:
    with pytest.raises(ValidationError):
        Edge(
            source_node_id="source",
            source_port_id="media",
            target_node_id="merge",
            target_port_id="items",
            ordinal=-1,
        )
