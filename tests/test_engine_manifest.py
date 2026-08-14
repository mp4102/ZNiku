"""验证 EngineManifest、精确版本、受限 Schema 和媒体合同。"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from zniku.contracts import (
    Artifact,
    ArtifactSet,
    ArtifactType,
    ContractViolation,
    EngineManifest,
    MediaKind,
    Scope,
    validate_contract_artifact,
)


def parse_manifest(payload: dict[str, Any]) -> EngineManifest:
    return EngineManifest.from_json(json.dumps(payload))


def test_valid_engine_manifest(valid_manifest: EngineManifest) -> None:
    assert valid_manifest.engine_id == "example.synthetic.enhancement"
    assert valid_manifest.engine_version == "1.2.3"
    assert valid_manifest.inputs[0].port.port_id == "video_in"
    assert valid_manifest.outputs[0].port.port_id == "video_out"
    assert valid_manifest.lifecycle.supports_acceptance
    assert valid_manifest.lifecycle.supports_publication
    assert valid_manifest.lifecycle.supports_recovery
    assert valid_manifest.sha256_digest().startswith("sha256:")


@pytest.mark.parametrize(
    "version",
    ["latest", "^1.2.3", ">=1.2.3", "1.*", "v1.2.3", "1.2", "01.2.3", "1.02.3"],
)
def test_non_exact_or_illegal_versions_fail_closed(
    manifest_payload: dict[str, Any], version: str
) -> None:
    manifest_payload["engine_version"] = version
    with pytest.raises(ValidationError):
        parse_manifest(manifest_payload)


@pytest.mark.parametrize("version", ["0.1.0-alpha.1", "2.0.0-rc.1+build.7"])
def test_exact_semver_prerelease_and_build_are_allowed(
    manifest_payload: dict[str, Any], version: str
) -> None:
    manifest_payload["engine_version"] = version
    assert parse_manifest(manifest_payload).engine_version == version


def test_unknown_top_level_field_fails_closed(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["entrypoint"] = "unsafe"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        parse_manifest(manifest_payload)


def test_unknown_contract_version_fails_closed(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["contract_version"] = "0.2.0"
    with pytest.raises(ValidationError):
        parse_manifest(manifest_payload)


def test_unknown_nested_field_fails_closed(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["inputs"][0]["port"]["direction"] = "input"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        parse_manifest(manifest_payload)


def test_path_like_engine_id_fails_closed(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["engine_id"] = "C:/Windows/System32/cmd.exe"
    with pytest.raises(ValidationError):
        parse_manifest(manifest_payload)


def test_duplicate_port_id_fails_closed(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["outputs"][0]["port"]["port_id"] = "video_in"
    with pytest.raises(ValidationError, match="E_ENGINE_PORT_DUPLICATE"):
        parse_manifest(manifest_payload)


def test_port_scope_must_be_declared(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["outputs"][0]["port"]["scope"] = "chapter"
    with pytest.raises(ValidationError, match="E_ENGINE_PORT_SCOPE_UNSUPPORTED"):
        parse_manifest(manifest_payload)


def test_manifest_scope_is_exact_in_0_1_0(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["supported_scopes"] = ["program", "chapter"]
    with pytest.raises(ValidationError, match="E_ENGINE_SCOPE_AMBIGUOUS"):
        parse_manifest(manifest_payload)


def test_parameter_schema_must_reject_unknown_parameters(
    valid_manifest: EngineManifest,
) -> None:
    with pytest.raises(ContractViolation, match="E_PARAMETER_INVALID"):
        valid_manifest.validate_parameters({"scale": 2, "model": "synthetic-v1", "unknown": True})


@pytest.mark.parametrize(
    "parameters",
    [
        {"model": "synthetic-v1"},
        {"scale": 0, "model": "synthetic-v1"},
        {"scale": 2, "model": "unknown-model"},
        {"scale": "2", "model": "synthetic-v1"},
        {"scale": 2, "model": "synthetic-v1", "tuning": {"extra": 1}},
    ],
)
def test_invalid_parameter_values_fail_closed(
    valid_manifest: EngineManifest, parameters: dict[str, Any]
) -> None:
    with pytest.raises(ContractViolation, match="E_PARAMETER_INVALID"):
        valid_manifest.validate_parameters(parameters)


def test_schema_default_is_not_silently_materialized(valid_manifest: EngineManifest) -> None:
    parameters = {"model": "synthetic-v1"}
    before = copy.deepcopy(parameters)

    with pytest.raises(ContractViolation):
        valid_manifest.validate_parameters(parameters)
    assert parameters == before


def test_parameter_schema_requires_closed_nested_objects(
    manifest_payload: dict[str, Any],
) -> None:
    del manifest_payload["parameter_schema"]["properties"]["tuning"]["additionalProperties"]
    with pytest.raises(ValidationError, match="E_SCHEMA_ADDITIONAL_PROPERTIES_REQUIRED"):
        parse_manifest(manifest_payload)


def test_unknown_schema_keyword_fails_closed(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["properties"]["scale"]["minimun"] = 1
    with pytest.raises(ValidationError, match="E_SCHEMA_KEYWORD_UNKNOWN"):
        parse_manifest(manifest_payload)


def test_regex_schema_keyword_is_not_in_safe_subset(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["properties"]["model"] = {
        "type": "string",
        "maxLength": 64,
        "pattern": "^(a+)+$",
    }
    with pytest.raises(ValidationError, match="E_SCHEMA_KEYWORD_UNKNOWN"):
        parse_manifest(manifest_payload)


def test_required_must_reference_declared_property(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["required"].append("ghost")
    with pytest.raises(ValidationError, match="E_SCHEMA_REQUIRED_UNKNOWN"):
        parse_manifest(manifest_payload)


def test_schema_keyword_must_apply_to_declared_type(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["properties"]["scale"]["minLength"] = 1
    with pytest.raises(ValidationError, match="E_SCHEMA_KEYWORD_TYPE_MISMATCH"):
        parse_manifest(manifest_payload)


def test_schema_bounds_must_be_satisfiable(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["properties"]["scale"]["minimum"] = 5
    manifest_payload["parameter_schema"]["properties"]["scale"]["maximum"] = 4
    with pytest.raises(ValidationError, match="E_SCHEMA_BOUNDS_INVALID"):
        parse_manifest(manifest_payload)


def test_exclusive_schema_bounds_must_leave_a_value(manifest_payload: dict[str, Any]) -> None:
    scale_schema = manifest_payload["parameter_schema"]["properties"]["scale"]
    scale_schema["exclusiveMinimum"] = 5
    scale_schema["maximum"] = 5
    with pytest.raises(ValidationError, match="E_SCHEMA_BOUNDS_INVALID"):
        parse_manifest(manifest_payload)


def test_required_properties_must_fit_max_properties(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["maxProperties"] = 0
    with pytest.raises(ValidationError, match="E_SCHEMA_BOUNDS_INVALID"):
        parse_manifest(manifest_payload)


def test_enum_values_must_match_declared_type(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["properties"]["model"]["enum"].append(42)
    with pytest.raises(ValidationError, match="E_SCHEMA_ENUM_VALUE_INVALID"):
        parse_manifest(manifest_payload)


def test_unbounded_array_schema_fails_closed(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["properties"]["frames"] = {
        "type": "array",
        "items": {"type": "integer"},
    }
    with pytest.raises(ValidationError, match="E_SCHEMA_ARRAY_UNBOUNDED"):
        parse_manifest(manifest_payload)


def test_array_schema_has_a_hard_item_limit(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["properties"]["frames"] = {
        "type": "array",
        "items": {"type": "integer"},
        "maxItems": 257,
        "uniqueItems": True,
    }
    with pytest.raises(ValidationError, match="E_SCHEMA_ARRAY_TOO_LARGE"):
        parse_manifest(manifest_payload)


def test_wrong_schema_dialect_fails_closed(manifest_payload: dict[str, Any]) -> None:
    manifest_payload["parameter_schema"]["$schema"] = "http://json-schema.org/draft-07/schema#"
    with pytest.raises(ValidationError, match="E_SCHEMA_DIALECT_UNSUPPORTED"):
        parse_manifest(manifest_payload)


@pytest.mark.parametrize("property_name", ["command", "script", "entrypoint", "python-code"])
def test_executable_parameter_names_are_forbidden(
    manifest_payload: dict[str, Any], property_name: str
) -> None:
    manifest_payload["parameter_schema"]["properties"][property_name] = {"type": "string"}
    with pytest.raises(ValidationError, match="E_EXECUTABLE_FIELD_FORBIDDEN"):
        parse_manifest(manifest_payload)


def test_media_input_contract_rejects_missing_required_attribute(
    valid_manifest: EngineManifest,
) -> None:
    artifact = Artifact(
        artifact_id="artifact.no-height",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.VIDEO,
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        attributes={"width": 1920, "frame_rate": "30000/1001"},
    )

    with pytest.raises(ContractViolation, match="E_INPUT_MEDIA_CONTRACT_VIOLATION"):
        validate_contract_artifact(valid_manifest.inputs[0], artifact)


def test_public_artifact_validator_checks_cardinality(
    valid_manifest: EngineManifest,
    valid_artifact_set: ArtifactSet,
) -> None:
    with pytest.raises(ContractViolation, match="E_BINDING_CARDINALITY_MISMATCH"):
        validate_contract_artifact(valid_manifest.inputs[0], valid_artifact_set)


def test_media_output_contract_rejects_broken_guarantee(
    valid_manifest: EngineManifest,
) -> None:
    artifact = Artifact(
        artifact_id="artifact.bad-output",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.VIDEO,
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        producer_stage_run_id="stage_run.enhance.001",
        attributes={"width": 1, "height": 1, "frame_rate": "30000/1001"},
    )

    with pytest.raises(ContractViolation, match="E_OUTPUT_MEDIA_CONTRACT_VIOLATION"):
        validate_contract_artifact(valid_manifest.outputs[0], artifact)


def test_media_attribute_rule_paths_cannot_overlap(
    manifest_payload: dict[str, Any],
) -> None:
    manifest_payload["outputs"][0]["attribute_rules"].append(
        {
            "path": "width.value",
            "disposition": "changed",
            "source": {"port_id": "video_in", "path": "width"},
        }
    )
    with pytest.raises(ValidationError, match="E_MEDIA_RULE_PATH_OVERLAP"):
        parse_manifest(manifest_payload)
