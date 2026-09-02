"""覆盖 AVEnhanceFlow v2.7 专用定义目录、Schema 与稳定 shape identity。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from zniku.avenhance_v27 import (
    AV27_NODE_VERSION,
    AV27_PROFILE_VERSION,
    atomic_split_count_from_type_id,
    atomic_split_definition,
    atomic_split_port_ids,
    atomic_split_type_id,
    av27_definition_catalog,
    built_in_av27_definitions,
    is_av27_definition,
)
from zniku.graph import Cardinality, ExecutionMode, NodeDefinition

_GEOMETRY = {"width": 1920, "height": 1080, "sample_aspect_ratio": "1/1"}
_SIGNAL = {
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
    "chroma_location": "left",
    "field_order": "progressive",
    "rotation": 0,
}


def _valid_parameters(type_id: str) -> dict[str, Any]:
    if type_id == "zniku.avenhance.v27.source_program":
        return {"source_path": "D:/media/source.mkv", "source_ordinal": 0}
    if type_id == "zniku.avenhance.v27.source_admission":
        return {
            "source_mode": "program",
            "sources": [{"source_ordinal": 0, "source_node_id": "source-0001"}],
        }
    if type_id == "zniku.avenhance.v27.mosaic_restoration.external":
        return {"model_name": "Mosaic Model", "model_version": "1.0"}
    if type_id.startswith("zniku.avenhance.v27.atomic_split.leaves."):
        count = atomic_split_count_from_type_id(type_id)
        assert count is not None
        return {
            "planned_admission_artifact_id": "artifact-admission",
            "segments": [
                {
                    "port_id": port_id,
                    "source_ordinal": 0,
                    "planned_effective_video_artifact_id": "artifact-video",
                    "chapter_id": "chapter-0001",
                    "chapter_ordinal": 0,
                    "leaf_id": f"leaf-id-{index:04d}",
                    "leaf_ordinal": index - 1,
                    "start_frame": (index - 1) * 100,
                    "end_frame": index * 100,
                }
                for index, port_id in enumerate(atomic_split_port_ids(count), start=1)
            ],
        }
    if type_id == "zniku.avenhance.v27.enhancement.external":
        return {
            "model_name": "Enhancement Model",
            "actual_scale_factor": 2,
            "expected_input_geometry": _GEOMETRY,
            "expected_output_geometry": {
                "width": 3840,
                "height": 2160,
                "sample_aspect_ratio": "1/1",
            },
            "expected_frames": 100,
            "expected_fps": "30000/1001",
            "chapter_id": "chapter-0001",
            "chapter_ordinal": 0,
            "leaf_id": "leaf-id-0001",
            "leaf_ordinal": 0,
        }
    if type_id == "zniku.avenhance.v27.merge_video":
        return {
            "model_name": "Enhancement Model",
            "chapter_id": "chapter-0001",
            "chapter_ordinal": 0,
            "expected_frames": 200,
            "expected_fps": "30000/1001",
            "expected_geometry": _GEOMETRY,
            "actual_scale_factor": 1,
        }
    if type_id == "zniku.avenhance.v27.frame_interpolation.external":
        return {
            "model_name": "FI Model",
            "chapter_id": "chapter-0001",
            "chapter_ordinal": 0,
            "expected_input_frames": 200,
            "expected_output_frames": 399,
            "source_fps": "30000/1001",
            "expected_geometry": _GEOMETRY,
            "expected_signal": _SIGNAL,
        }
    if type_id == "zniku.avenhance.v27.program_encode":
        return {
            "encoder": "gpu",
            "source_fps": "30000/1001",
            "chapters": [
                {
                    "chapter_id": "chapter-0001",
                    "chapter_ordinal": 0,
                    "source_frames": 200,
                    "expected_fi_frames": 399,
                    "encoded_frames": 400,
                }
            ],
            "expected_geometry": _GEOMETRY,
            "expected_signal": _SIGNAL,
        }
    if type_id == "zniku.avenhance.v27.final_mux":
        return {
            "source_mode": "program",
            "sources": [
                {
                    "source_ordinal": 0,
                    "source_frames": 200,
                    "source_fps": "30000/1001",
                }
            ],
            "expected_program_frames": 400,
            "expected_geometry": _GEOMETRY,
            "expected_signal": _SIGNAL,
            "mr_mode": "off",
        }
    raise AssertionError(f"没有 {type_id} 的测试参数")


def _ports(definition: NodeDefinition, direction: str) -> tuple[tuple[str, str, str, bool], ...]:
    ports = definition.input_ports if direction == "input" else definition.output_ports
    return tuple(
        (port.port_id, port.data_type, port.cardinality.value, port.required) for port in ports
    )


def _output_paths(definition: NodeDefinition) -> tuple[tuple[str, str], ...]:
    return tuple((item.port_id, item.relative_path) for item in definition.executor.output_paths)


def test_frozen_catalog_has_exact_nine_identities_ports_modes_and_references() -> None:
    definitions = built_in_av27_definitions(2)

    assert AV27_NODE_VERSION == "0.2.1"
    assert AV27_PROFILE_VERSION == "2.7.0"
    assert [(item.type_id, item.version) for item in definitions] == [
        ("zniku.avenhance.v27.source_program", "0.2.1"),
        ("zniku.avenhance.v27.source_admission", "0.2.1"),
        ("zniku.avenhance.v27.mosaic_restoration.external", "0.2.1"),
        ("zniku.avenhance.v27.atomic_split.leaves.2", "0.2.1"),
        ("zniku.avenhance.v27.enhancement.external", "0.2.1"),
        ("zniku.avenhance.v27.merge_video", "0.2.1"),
        ("zniku.avenhance.v27.frame_interpolation.external", "0.2.1"),
        ("zniku.avenhance.v27.program_encode", "0.2.1"),
        ("zniku.avenhance.v27.final_mux", "0.2.1"),
    ]
    assert [item.execution_mode for item in definitions] == [
        ExecutionMode.AUTOMATIC,
        ExecutionMode.AUTOMATIC,
        ExecutionMode.MANUAL_EXTERNAL,
        ExecutionMode.AUTOMATIC,
        ExecutionMode.MANUAL_EXTERNAL,
        ExecutionMode.AUTOMATIC,
        ExecutionMode.MANUAL_EXTERNAL,
        ExecutionMode.AUTOMATIC,
        ExecutionMode.AUTOMATIC,
    ]
    assert [item.executor.kind for item in definitions] == [
        "python",
        "python",
        "manual_external",
        "python",
        "manual_external",
        "python",
        "manual_external",
        "python",
        "python",
    ]
    assert [item.validator.adapter if item.validator else None for item in definitions] == [
        "zniku.avenhance_v27.validators:validate_source_program",
        "zniku.avenhance_v27.validators:validate_source_admission",
        "zniku.avenhance_v27.validators:validate_mosaic_restoration",
        "zniku.avenhance_v27.validators:validate_atomic_split",
        "zniku.avenhance_v27.validators:validate_enhancement",
        "zniku.avenhance_v27.validators:validate_merge_video",
        "zniku.avenhance_v27.validators:validate_frame_interpolation",
        "zniku.avenhance_v27.validators:validate_program_encode",
        "zniku.avenhance_v27.validators:validate_final_mux",
    ]

    assert _ports(definitions[0], "input") == ()
    assert _ports(definitions[0], "output") == (
        ("video", "VideoFile", "one", False),
        ("source_media", "MediaFile", "one", False),
    )
    assert _ports(definitions[1], "input") == (("sources", "MediaFile", "ordered_many", True),)
    assert _ports(definitions[2], "input") == (
        ("video", "VideoFile", "one", True),
        ("gate", "DataFile", "one", True),
    )
    assert _ports(definitions[3], "input") == (
        ("videos", "VideoFile", "ordered_many", True),
        ("gate", "DataFile", "one", True),
    )
    assert _ports(definitions[3], "output") == (
        ("leaf-0001", "VideoFile", "one", False),
        ("leaf-0002", "VideoFile", "one", False),
    )
    assert _ports(definitions[4], "input") == (("video", "VideoFile", "one", True),)
    assert _ports(definitions[5], "input") == (("videos", "VideoFile", "ordered_many", True),)
    assert _ports(definitions[6], "input") == (("video", "VideoFile", "one", True),)
    assert _ports(definitions[7], "input") == (("chapters", "VideoFile", "ordered_many", True),)
    assert _ports(definitions[8], "input") == (
        ("video", "VideoFile", "one", True),
        ("sources", "MediaFile", "ordered_many", True),
        ("gate", "DataFile", "one", True),
    )
    assert all(
        port.cardinality is Cardinality.ONE
        for definition in definitions
        for port in definition.output_ports
    )


def test_managed_paths_exist_only_in_executor_and_cover_declared_outputs() -> None:
    definitions = built_in_av27_definitions(3)

    assert [_output_paths(item) for item in definitions] == [
        (),
        (("gate", "admission.json"),),
        (("video", "mr.mkv"),),
        (
            ("leaf-0001", "leaves/leaf-0001.mkv"),
            ("leaf-0002", "leaves/leaf-0002.mkv"),
            ("leaf-0003", "leaves/leaf-0003.mkv"),
        ),
        (("video", "enhancement.mov"),),
        (("video", "merge.mov"),),
        (("video", "fi.mov"),),
        (("video", "program.mp4"),),
        (("media", "final.mkv"),),
    ]
    for definition in definitions:
        dumped = definition.model_dump(mode="json")
        assert "output_paths" not in dumped
        assert "output_paths" in dumped["executor"]
        assert {item.port_id for item in definition.executor.output_paths}.issubset(
            {port.port_id for port in definition.output_ports}
        )


def test_all_root_schemas_accept_minimal_contract_and_fail_closed() -> None:
    forbidden = {"executable", "argv", "shell", "code", "tool", "tool_version"}

    for definition in built_in_av27_definitions(2):
        schema = definition.parameter_schema
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        properties = schema["properties"]
        assert isinstance(properties, Mapping)
        assert forbidden.isdisjoint(properties)

        parameters = _valid_parameters(definition.type_id)
        validator = Draft202012Validator(schema)
        assert list(validator.iter_errors(parameters)) == []

        for field in forbidden:
            invalid = dict(parameters)
            invalid[field] = "malicious"
            assert list(validator.iter_errors(invalid)), f"{definition.type_id} 不应接受 {field}"

        required = schema["required"]
        assert isinstance(required, list) and required and isinstance(required[0], str)
        missing = dict(parameters)
        missing.pop(required[0])
        assert list(validator.iter_errors(missing))


def test_enhancement_allows_implicit_identity_scale_only_at_validator_boundary() -> None:
    """Schema 允许省略 k=1 声明；实测 k>1 的确认由媒体 validator 失败关闭。"""

    definition = next(
        item
        for item in built_in_av27_definitions(1)
        if item.type_id == "zniku.avenhance.v27.enhancement.external"
    )
    parameters = _valid_parameters(definition.type_id)
    parameters.pop("actual_scale_factor")

    assert list(Draft202012Validator(definition.parameter_schema).iter_errors(parameters)) == []


@pytest.mark.parametrize(
    "invalid_fps",
    [0.5, "0/1", "1/0", "01/1", "1/01", "30000.0/1001"],
)
def test_rational_schema_rejects_float_zero_and_noncanonical_text(invalid_fps: object) -> None:
    definition = built_in_av27_definitions(1)[6]
    parameters = _valid_parameters(definition.type_id)
    parameters["source_fps"] = invalid_fps

    assert list(Draft202012Validator(definition.parameter_schema).iter_errors(parameters))


def test_source_schema_rejects_relative_path_and_unknown_precomputed_facts() -> None:
    definition = built_in_av27_definitions()[0]
    validator = Draft202012Validator(definition.parameter_schema)

    relative = _valid_parameters(definition.type_id)
    relative["source_path"] = "relative/source.mkv"
    assert list(validator.iter_errors(relative))

    for forbidden in ("expected_frames", "expected_fps", "geometry", "signal"):
        parameters = _valid_parameters(definition.type_id)
        parameters[forbidden] = "client-value"
        assert list(validator.iter_errors(parameters))


def test_atomic_split_shape_is_stable_and_count_is_exact_identity() -> None:
    first = atomic_split_definition(3)
    again = atomic_split_definition(3)
    other = atomic_split_definition(4)

    assert first == again
    assert first.type_id == atomic_split_type_id(3)
    assert first.type_id != other.type_id
    assert atomic_split_port_ids(3) == ("leaf-0001", "leaf-0002", "leaf-0003")
    assert atomic_split_count_from_type_id(first.type_id) == 3
    assert atomic_split_count_from_type_id("zniku.avenhance.v27.atomic_split.leaves.03") is None
    assert atomic_split_count_from_type_id("zniku.avenhance.v27.atomic_split.leaves.0") is None

    for invalid in (True, False, 0, -1, 1.5, "2"):
        with pytest.raises(ValueError, match="E_AV27_SPLIT_COUNT"):
            atomic_split_definition(invalid)  # type: ignore[arg-type]


def test_atomic_split_schema_binds_each_segment_to_its_port_and_exact_count() -> None:
    definition = atomic_split_definition(2)
    validator = Draft202012Validator(definition.parameter_schema)
    valid = _valid_parameters(definition.type_id)

    swapped = dict(valid)
    swapped["segments"] = list(reversed(valid["segments"]))
    assert list(validator.iter_errors(swapped))

    missing = dict(valid)
    missing["segments"] = valid["segments"][:-1]
    assert list(validator.iter_errors(missing))

    extra_field = dict(valid)
    first_segment = dict(valid["segments"][0])
    first_segment["output_path"] = "client-controlled.mkv"
    extra_field["segments"] = [first_segment, valid["segments"][1]]
    assert list(validator.iter_errors(extra_field))


def test_profile_catalog_is_read_only_and_identity_check_is_structural() -> None:
    catalog = av27_definition_catalog(2)
    split = catalog[("zniku.avenhance.v27.atomic_split.leaves.2", "0.2.1")]

    assert len(catalog) == 9
    assert all(is_av27_definition(item) for item in catalog.values())
    assert not is_av27_definition(split.model_copy(update={"version": "0.2.2"}))
    assert not is_av27_definition(
        split.model_copy(update={"parameter_schema": {**split.parameter_schema, "title": "drift"}})
    )
    assert not is_av27_definition(
        split.model_copy(update={"type_id": "zniku.avenhance.v27.atomic_split.leaves.999999999"})
    )
    with pytest.raises(TypeError):
        catalog[("forbidden", "0.2.1")] = split  # type: ignore[index]


def test_definition_json_round_trip_is_deterministic() -> None:
    for definition in built_in_av27_definitions(2):
        encoded = definition.model_dump_json()
        assert NodeDefinition.model_validate_json(encoded) == definition
        assert NodeDefinition.model_validate_json(encoded).model_dump_json() == encoded
