"""验证 v0.3.0 Presentation 的严格模型、exact binding 与隔离语义。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError

from zniku.avenhance_v27 import atomic_split_definition, built_in_av27_definitions
from zniku.graph import (
    ExecutionMode,
    Graph,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.media import built_in_media_definitions
from zniku.presentation import (
    CategoryPresentation,
    ControlHint,
    EnumLabel,
    IconToken,
    NodePresentation,
    PaletteLevel,
    ParameterGroupPresentation,
    ParameterPresentation,
    PickerPresentation,
    PortPresentation,
    PresentationCatalog,
    PresentationCatalogError,
    build_builtin_presentation_catalog,
    decode_json_pointer,
    resolve_presentation_catalog,
    validate_node_presentation,
)
from zniku.project import Project, ProjectStore
from zniku.project_service import ProjectServiceApplication, ProjectServiceError
from zniku.runtime import capture_node_signature


def _definition(
    type_id: str,
    *,
    version: str = "1.0.0",
    parameter_schema: dict[str, object] | None = None,
) -> NodeDefinition:
    return NodeDefinition(
        type_id=type_id,
        version=version,
        input_ports=(PortSpec(port_id="video", data_type="VideoFile", required=True),),
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        parameter_schema=cast(
            dict[str, JsonValue],
            parameter_schema
            or {
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": ["a", "b"]}},
                "additionalProperties": False,
            },
        ),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.presentation:adapter"),
    )


def _node_presentation(
    definition: NodeDefinition,
    *,
    category_id: str = "plugin",
    pointer: str = "/mode",
    control_hint: ControlHint = ControlHint.SELECT,
    enum_labels: tuple[EnumLabel, ...] = (
        EnumLabel(value="a", label="模式 A"),
        EnumLabel(value="b", label="模式 B"),
    ),
) -> NodePresentation:
    picker = (
        PickerPresentation(extensions=(".mkv",))
        if control_hint
        in {
            ControlHint.FILE_PATH,
            ControlHint.FILE_PATHS,
            ControlHint.DIRECTORY_PATH,
            ControlHint.SAVE_FILE,
        }
        else None
    )
    return NodePresentation(
        type_id=definition.type_id,
        definition_version=definition.version,
        title="第三方视频处理",
        description="用于严格绑定测试的合成节点。",
        category_id=category_id,
        icon_token=IconToken.TRANSFORM,
        palette_level=PaletteLevel.ADVANCED,
        keywords=("第三方", "测试"),
        parameter_groups=(
            ParameterGroupPresentation(group_id="basic", title="基础设置", order=10),
        ),
        parameters=(
            ParameterPresentation(
                parameter_pointer=pointer,
                label="处理方式",
                group_id="basic",
                order=10,
                control_hint=control_hint,
                enum_labels=enum_labels,
                picker=picker,
            ),
        ),
        ports=(
            PortPresentation(direction="input", port_id="video", label="输入视频"),
            PortPresentation(direction="output", port_id="video", label="输出视频"),
        ),
        card_summary_paths=(pointer,),
    )


def _third_party_payload(
    *,
    categories: list[object],
    nodes: list[object],
) -> dict[str, object]:
    return {
        "contract_version": "0.3.0",
        "locale": "zh-CN",
        "categories": categories,
        "nodes": nodes,
    }


def _all_mapping_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return set(value) | {key for item in value.values() for key in _all_mapping_keys(item)}
    if isinstance(value, list | tuple):
        return {key for item in value for key in _all_mapping_keys(item)}
    return set()


def test_builtin_catalog_covers_all_generic_and_av27_definitions() -> None:
    definitions = (*built_in_media_definitions(), *built_in_av27_definitions())
    catalog = build_builtin_presentation_catalog()

    assert catalog.contract_version == "0.3.0"
    assert catalog.locale == "zh-CN"
    assert len(catalog.nodes) == 23
    assert tuple((node.type_id, node.definition_version) for node in catalog.nodes) == tuple(
        (definition.type_id, definition.version) for definition in definitions
    )

    for definition, presentation in zip(definitions, catalog.nodes, strict=True):
        properties = definition.parameter_schema["properties"]
        assert isinstance(properties, Mapping)
        assert {item.parameter_pointer for item in presentation.parameters} == {
            f"/{name}" for name in properties
        }
        assert {(item.direction, item.port_id) for item in presentation.ports} == {
            *(("input", port.port_id) for port in definition.input_ports),
            *(("output", port.port_id) for port in definition.output_ports),
        }
        validate_node_presentation(presentation, definition, require_complete=True)

    source_program = next(
        node for node in catalog.nodes if node.type_id.endswith(".source_program")
    )
    assert next(port.label for port in source_program.ports if port.port_id == "source_media") == (
        "节目源媒体"
    )


def test_presentation_payload_has_no_execution_or_parameter_constraint_fields() -> None:
    payload = build_builtin_presentation_catalog().model_dump(mode="json")
    keys = _all_mapping_keys(payload)

    assert keys.isdisjoint(
        {
            "required",
            "default",
            "minimum",
            "maximum",
            "pattern",
            "enum",
            "executable",
            "argv",
            "callback",
            "validator",
            "executor",
            "data_type",
            "cardinality",
        }
    )
    assert {"enum_labels", "parameter_pointer", "icon_token"} <= keys


@pytest.mark.parametrize("leaf_count", (1, 2, 3))
def test_dynamic_atomic_split_mirrors_concrete_definition_ports(leaf_count: int) -> None:
    definition = atomic_split_definition(leaf_count)
    presentation = build_builtin_presentation_catalog((definition,)).nodes[0]

    assert tuple(
        item.port_id for item in presentation.ports if item.direction == "output"
    ) == tuple(port.port_id for port in definition.output_ports)
    assert (
        len(tuple(item for item in presentation.ports if item.direction == "output")) == leaf_count
    )


@pytest.mark.parametrize(
    "invalid_type_id",
    (
        "zniku.avenhance.v27.atomic_split.leaves.02",
        "zniku.avenhance.v27.atomic_split.leaves.foo",
    ),
)
def test_dynamic_atomic_split_rejects_noncanonical_identity(invalid_type_id: str) -> None:
    valid = atomic_split_definition(2)
    payload = valid.model_dump(mode="python", round_trip=True)
    payload["type_id"] = invalid_type_id
    invalid = NodeDefinition.model_validate(payload, strict=True)

    with pytest.raises(PresentationCatalogError, match="E_PRESENTATION_BUILTIN_DEFINITION_DRIFT"):
        build_builtin_presentation_catalog((invalid,))


def test_builtin_definition_shape_drift_fails_closed() -> None:
    valid = atomic_split_definition(2)
    payload = valid.model_dump(mode="python", round_trip=True)
    payload["parameter_schema"]["properties"]["drift"] = {"type": "string"}
    drifted = NodeDefinition.model_validate(payload, strict=True)

    with pytest.raises(PresentationCatalogError, match="E_PRESENTATION_BUILTIN_DEFINITION_DRIFT"):
        build_builtin_presentation_catalog((drifted,))


def test_models_reject_unknown_fields_versions_icons_and_forbidden_constraints() -> None:
    with pytest.raises(ValidationError):
        PresentationCatalog.model_validate(
            {
                "contract_version": "0.2.1",
                "locale": "zh-CN",
                "categories": [],
                "nodes": [],
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        NodePresentation.model_validate(
            {
                "type_id": "third.party.node",
                "definition_version": "1.0.0",
                "title": "第三方节点",
                "description": "严格模型测试。",
                "category_id": "plugin",
                "icon_token": "https://example.invalid/icon.svg",
                "palette_level": "advanced",
                "keywords": [],
                "parameter_groups": [],
                "parameters": [],
                "ports": [],
                "card_summary_paths": [],
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        ParameterPresentation.model_validate(
            {
                "parameter_pointer": "/mode",
                "label": "模式",
                "group_id": "basic",
                "order": 1,
                "required": True,
                "default": "a",
                "executable": "cmd.exe",
            },
            strict=True,
        )


@pytest.mark.parametrize("pointer", ("mode", "/bad~2escape", "/bad~", ""))
def test_json_pointer_rejects_non_rfc6901_values(pointer: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        if pointer:
            decode_json_pointer(pointer)
        else:
            ParameterPresentation(
                parameter_pointer=pointer,
                label="模式",
                group_id="basic",
                order=1,
            )


def test_json_pointer_decodes_rfc6901_escapes() -> None:
    assert decode_json_pointer("/a~1b/~0key") == ("a/b", "~key")


def test_json_pointer_accepts_empty_property_token_and_rejects_array_leading_zero() -> None:
    empty_property = _definition(
        "third.party.empty_key",
        parameter_schema={
            "type": "object",
            "properties": {"": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    presentation = _node_presentation(
        empty_property,
        pointer="/",
        control_hint=ControlHint.TEXT,
        enum_labels=(),
    )
    validate_node_presentation(presentation, empty_property)

    array_definition = _definition(
        "third.party.array_index",
        parameter_schema={
            "type": "object",
            "properties": {"mode": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        },
    )
    invalid = _node_presentation(
        array_definition,
        pointer="/mode/00",
        control_hint=ControlHint.TEXT,
        enum_labels=(),
    )
    with pytest.raises(PresentationCatalogError, match="E_PRESENTATION_PARAMETER_POINTER"):
        validate_node_presentation(invalid, array_definition)


def test_parameter_presentation_rejects_duplicate_enum_values_and_picker_mismatch() -> None:
    with pytest.raises(ValidationError, match="E_PRESENTATION_ENUM_LABEL_DUPLICATE"):
        ParameterPresentation(
            parameter_pointer="/mode",
            label="模式",
            group_id="basic",
            order=1,
            enum_labels=(EnumLabel(value="a", label="A"), EnumLabel(value="a", label="另一个 A")),
        )
    with pytest.raises(ValidationError, match="E_PRESENTATION_ENUM_LABEL_DUPLICATE"):
        ParameterPresentation(
            parameter_pointer="/mode",
            label="模式",
            group_id="basic",
            order=1,
            enum_labels=(
                EnumLabel(value={"items": ["a", 1]}, label="复合 A"),
                EnumLabel(value={"items": ["a", 1]}, label="重复复合 A"),
            ),
        )
    with pytest.raises(ValidationError, match="E_PRESENTATION_PICKER_BINDING"):
        ParameterPresentation(
            parameter_pointer="/path",
            label="文件",
            group_id="basic",
            order=1,
            control_hint=ControlHint.FILE_PATH,
        )
    with pytest.raises(ValidationError, match="E_PRESENTATION_PICKER_BINDING"):
        ParameterPresentation(
            parameter_pointer="/mode",
            label="模式",
            group_id="basic",
            order=1,
            picker=PickerPresentation(extensions=(".mkv",)),
        )


def test_exact_definition_parameter_group_port_and_summary_bindings() -> None:
    definition = _definition("third.party.binding")
    valid = _node_presentation(definition)
    validate_node_presentation(valid, definition)

    cases = (
        {"definition_version": "1.0.1"},
        {
            "parameters": [
                {
                    **valid.parameters[0].model_dump(mode="python"),
                    "parameter_pointer": "/missing",
                }
            ],
        },
        {"card_summary_paths": ["/missing"]},
        {"parameters": [{**valid.parameters[0].model_dump(mode="python"), "group_id": "missing"}]},
        {
            "ports": [
                {"direction": "output", "port_id": "missing", "label": "不存在"},
            ]
        },
    )
    for update in cases:
        payload = valid.model_dump(mode="python")
        payload.update(update)
        candidate = NodePresentation.model_validate(payload, strict=True)
        with pytest.raises(PresentationCatalogError):
            validate_node_presentation(candidate, definition)


def test_control_hint_and_enum_labels_cannot_override_schema() -> None:
    definition = _definition("third.party.control")
    mismatch = _node_presentation(
        definition,
        control_hint=ControlHint.INTEGER,
        enum_labels=(),
    )
    with pytest.raises(PresentationCatalogError, match="E_PRESENTATION_CONTROL_SCHEMA"):
        validate_node_presentation(mismatch, definition)

    unknown_enum = _node_presentation(
        definition,
        enum_labels=(EnumLabel(value="new", label="新增值"),),
    )
    with pytest.raises(PresentationCatalogError, match="E_PRESENTATION_ENUM_VALUE"):
        validate_node_presentation(unknown_enum, definition)

    no_enum = _definition(
        "third.party.no_enum",
        parameter_schema={
            "type": "object",
            "properties": {"mode": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    invalid_labels = _node_presentation(
        no_enum,
        control_hint=ControlHint.TEXT,
        enum_labels=(EnumLabel(value="a", label="A"),),
    )
    with pytest.raises(PresentationCatalogError, match="E_PRESENTATION_ENUM_SOURCE"):
        validate_node_presentation(invalid_labels, no_enum)


def test_enum_labels_use_deep_json_identity() -> None:
    definition = _definition(
        "third.party.deep_enum",
        parameter_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "enum": [
                        {"items": ["a", 1]},
                        {"items": ["b", 2]},
                    ]
                }
            },
            "additionalProperties": False,
        },
    )
    valid = _node_presentation(
        definition,
        enum_labels=(EnumLabel(value={"items": ["a", 1]}, label="复合 A"),),
    )
    validate_node_presentation(valid, definition)

    unknown = _node_presentation(
        definition,
        enum_labels=(EnumLabel(value={"items": ["c", 3]}, label="未知"),),
    )
    with pytest.raises(PresentationCatalogError, match="E_PRESENTATION_ENUM_VALUE"):
        validate_node_presentation(unknown, definition)


def test_file_paths_and_slider_require_safely_renderable_schema() -> None:
    files = _definition(
        "third.party.files",
        parameter_schema={
            "type": "object",
            "properties": {"mode": {"type": "array", "items": {"type": "integer"}}},
            "additionalProperties": False,
        },
    )
    file_picker = _node_presentation(
        files,
        control_hint=ControlHint.FILE_PATHS,
        enum_labels=(),
    )
    with pytest.raises(PresentationCatalogError, match="E_PRESENTATION_CONTROL_SCHEMA"):
        validate_node_presentation(file_picker, files)

    slider = _definition(
        "third.party.slider",
        parameter_schema={
            "type": "object",
            "properties": {"mode": {"type": "integer", "minimum": 0}},
            "additionalProperties": False,
        },
    )
    slider_presentation = _node_presentation(
        slider,
        control_hint=ControlHint.SLIDER,
        enum_labels=(),
    )
    with pytest.raises(PresentationCatalogError, match="E_PRESENTATION_CONTROL_SCHEMA"):
        validate_node_presentation(slider_presentation, slider)


def test_invalid_third_party_entries_are_isolated_without_affecting_valid_node() -> None:
    valid_definition = _definition("third.party.valid")
    missing_definition = _definition("third.party.missing")
    valid_node = _node_presentation(valid_definition)
    invalid_node = {
        **_node_presentation(missing_definition).model_dump(mode="json"),
        "callback": "alert(1)",
    }
    payload = _third_party_payload(
        categories=[
            CategoryPresentation(category_id="plugin", title="第三方", order=100).model_dump(
                mode="json"
            )
        ],
        nodes=[valid_node.model_dump(mode="json"), invalid_node],
    )

    resolution = resolve_presentation_catalog(
        (valid_definition, missing_definition),
        third_party_catalogs=(payload,),
    )

    assert tuple(node.type_id for node in resolution.catalog.nodes) == (valid_definition.type_id,)
    assert any(item.code == "W_PRESENTATION_NODE_INVALID" for item in resolution.diagnostics)
    assert any(
        item.code == "W_PRESENTATION_MISSING" and item.type_id == missing_definition.type_id
        for item in resolution.diagnostics
    )


def test_third_party_exact_version_category_pointer_port_and_enum_failures_are_diagnostics() -> (
    None
):
    definition = _definition("third.party.isolated")
    candidates = (
        _node_presentation(definition, category_id="unknown"),
        NodePresentation.model_validate(
            {
                **_node_presentation(definition).model_dump(mode="python"),
                "definition_version": "1.0.1",
            },
            strict=True,
        ),
        _node_presentation(definition, pointer="/unknown"),
        _node_presentation(
            definition,
            enum_labels=(EnumLabel(value="unknown", label="未知"),),
        ),
    )
    for candidate in candidates:
        payload = _third_party_payload(
            categories=[{"category_id": "plugin", "title": "第三方", "order": 100}],
            nodes=[candidate.model_dump(mode="json")],
        )
        resolution = resolve_presentation_catalog(
            (definition,),
            third_party_catalogs=(payload,),
        )
        assert resolution.catalog.nodes == ()
        assert resolution.diagnostics


def test_presentation_change_never_enters_project_or_reuse_signature() -> None:
    definition = _definition("third.party.invariant")
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="transform",
                type_id=definition.type_id,
                definition_version=definition.version,
                parameters={"mode": "a"},
            ),
        )
    )
    project = Project(project_id="ui.invariant", name="展示不污染执行", graph=graph)
    before = capture_node_signature(graph, "transform", input_artifact_ids=())
    presentation = _node_presentation(definition)
    changed = NodePresentation.model_validate(
        {**presentation.model_dump(mode="python"), "title": "只修改展示标题"},
        strict=True,
    )
    after = capture_node_signature(graph, "transform", input_artifact_ids=())

    assert before == after
    assert presentation != changed
    assert "presentation" not in str(project.model_dump(mode="json")).casefold()
    assert "presentation" not in str(before).casefold()


def test_project_service_exposes_presentation_as_independent_read_only_envelope(
    tmp_path: Path,
) -> None:
    definitions = (*built_in_media_definitions(), *built_in_av27_definitions())
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        definition_catalog=definitions,
    )

    before = application.inspect()
    envelope = application.inspect_presentations()
    after = application.inspect()

    assert envelope.contract_version == "0.3.0"
    assert envelope.catalog.contract_version == "0.3.0"
    assert len(envelope.catalog.nodes) == 23
    assert envelope.diagnostics == ()
    assert before == after
    assert "catalog" not in before.model_dump(mode="json")
    assert "presentation" not in before.model_dump(mode="json")


def test_project_service_uses_current_dynamic_split_definition_without_guessing(
    tmp_path: Path,
) -> None:
    definitions = (*built_in_media_definitions(), *built_in_av27_definitions())
    dynamic = atomic_split_definition(3)
    project_path = tmp_path / "dynamic.zniku"
    ProjectStore.create(
        project_path,
        Project(project_id="dynamic.split", name="动态分段", graph=Graph()),
        (dynamic,),
    )
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        definition_catalog=definitions,
    )
    application.command({"operation": "open_project", "path": str(project_path)})

    catalog = application.inspect_presentations().catalog
    presentation = next(
        node
        for node in catalog.nodes
        if node.type_id == dynamic.type_id and node.definition_version == dynamic.version
    )

    assert tuple(
        port.port_id for port in presentation.ports if port.direction == "output"
    ) == tuple(port.port_id for port in dynamic.output_ports)


def test_project_service_fails_closed_when_builtin_definition_drifted(
    tmp_path: Path,
) -> None:
    valid = built_in_media_definitions()[0]
    payload = valid.model_dump(mode="python", round_trip=True)
    payload["parameter_schema"]["properties"]["unexpected"] = {"type": "string"}
    drifted = NodeDefinition.model_validate(payload, strict=True)

    with pytest.raises(
        ProjectServiceError,
        match="E_PROJECT_SERVICE_PRESENTATION_CATALOG",
    ):
        ProjectServiceApplication(
            work_root=tmp_path / "work",
            definition_catalog=(drifted,),
        )
