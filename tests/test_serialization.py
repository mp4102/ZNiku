"""验证严格 JSON round-trip、canonical JSON 与 SHA-256 digest 稳定性。"""

from __future__ import annotations

import json

import pytest
from pydantic import JsonValue, ValidationError

from zniku.contracts import ArtifactSet, ContractModel, EngineBinding, EngineManifest, StageRun


@pytest.mark.parametrize(
    "fixture_name", ["valid_manifest", "valid_artifact_set", "valid_stage_run"]
)
def test_json_round_trip_preserves_model(request: pytest.FixtureRequest, fixture_name: str) -> None:
    model = request.getfixturevalue(fixture_name)
    restored = type(model).from_json(model.to_canonical_json())

    assert restored == model
    assert restored.to_canonical_json() == model.to_canonical_json()
    assert restored.sha256_digest() == model.sha256_digest()


def test_json_data_round_trip_preserves_manifest(valid_manifest: EngineManifest) -> None:
    assert EngineManifest.from_data(valid_manifest.to_data()) == valid_manifest


@pytest.mark.parametrize("model_type", [EngineManifest, ArtifactSet, StageRun])
def test_public_models_generate_closed_json_schema(model_type: type[ContractModel]) -> None:
    schema = model_type.model_json_schema()
    assert schema["additionalProperties"] is False
    json.dumps(schema)


def test_digest_is_stable_across_mapping_insertion_order(valid_manifest: EngineManifest) -> None:
    first: dict[str, JsonValue] = {
        "scale": 2,
        "model": "synthetic-v1",
        "tuning": {"detail": 0.5},
    }
    second: dict[str, JsonValue] = {
        "tuning": {"detail": 0.5},
        "model": "synthetic-v1",
        "scale": 2,
    }
    assert json.dumps(first) != json.dumps(second)

    first_run = StageRun(
        contract_version="0.1.0",
        stage_run_id="stage_run.digest",
        workflow_run_id="workflow_run.digest",
        stage_spec_id="stage_spec.digest",
        scope=valid_manifest.supported_scopes[0],
        scope_id="program.digest",
        engine=EngineBinding.from_manifest(valid_manifest),
        parameters=first,
        inputs=(),
    )
    second_run = StageRun.from_json(
        json.dumps({**first_run.to_data(), "parameters": second}, ensure_ascii=False)
    )

    assert first_run.to_canonical_json() == second_run.to_canonical_json()
    assert first_run.sha256_digest() == second_run.sha256_digest()


def test_manifest_digest_has_golden_rfc8785_vector(valid_manifest: EngineManifest) -> None:
    assert (
        valid_manifest.sha256_digest()
        == "sha256:8ca3ef46cbdb07e34a49c3f09d5c5387e2f344ee15e41a57fd3a04e006fb0c66"
    )


def test_business_array_order_changes_digest(valid_stage_run: StageRun) -> None:
    first = valid_stage_run.model_copy(update={"parameters": {"sequence": [1, 2, 3]}})
    second = valid_stage_run.model_copy(update={"parameters": {"sequence": [3, 2, 1]}})

    assert first.sha256_digest() != second.sha256_digest()


def test_artifact_set_member_order_participates_in_digest(
    valid_artifact_set: ArtifactSet,
) -> None:
    payload = valid_artifact_set.to_data()
    members = payload["members"]
    assert isinstance(members, list)
    payload["members"] = list(reversed(members))

    with pytest.raises(ValidationError, match="E_SET_ORDER_MISMATCH"):
        ArtifactSet.from_json(json.dumps(payload))


def test_non_finite_json_value_is_rejected(valid_stage_run: StageRun) -> None:
    with pytest.raises(ValidationError):
        StageRun(
            contract_version="0.1.0",
            stage_run_id="stage_run.nan",
            workflow_run_id=valid_stage_run.workflow_run_id,
            stage_spec_id=valid_stage_run.stage_spec_id,
            scope=valid_stage_run.scope,
            scope_id=valid_stage_run.scope_id,
            engine=valid_stage_run.engine,
            parameters={"scale": float("nan"), "model": "synthetic-v1"},
            inputs=valid_stage_run.inputs,
        )


@pytest.mark.parametrize("value", ["\ud800", 2**60])
def test_values_outside_rfc8785_domain_are_rejected(
    valid_stage_run: StageRun, value: object
) -> None:
    with pytest.raises(ValidationError, match="E_JSON_CANONICALIZATION"):
        valid_stage_run.model_copy(update={"parameters": {"value": value}})


def test_canonical_json_keeps_unicode_unescaped(valid_stage_run: StageRun) -> None:
    changed = valid_stage_run.model_copy(
        update={"parameters": {"scale": 2, "model": "synthetic-v1", "note": "合成"}}
    )
    assert "合成" in changed.to_canonical_json()


def test_rfc8785_normalizes_equivalent_json_numbers(valid_stage_run: StageRun) -> None:
    integer = valid_stage_run.model_copy(update={"parameters": {"value": 1, "zero": 0}})
    floating = valid_stage_run.model_copy(update={"parameters": {"value": 1.0, "zero": -0.0}})

    assert integer.to_canonical_json() == floating.to_canonical_json()
    assert integer.sha256_digest() == floating.sha256_digest()


def test_duplicate_json_object_key_fails_closed() -> None:
    payload = (
        '{"artifact_id":"artifact.first","artifact_id":"artifact.second",'
        '"artifact_type":"metadata","media_kind":null,"scope":"program",'
        '"scope_id":"program.synthetic","producer_stage_run_id":null,"attributes":{}}'
    )

    from zniku.contracts import Artifact

    with pytest.raises(ValueError, match="E_JSON_DUPLICATE_KEY"):
        Artifact.from_json(payload)


def test_validated_model_copy_cannot_bypass_contract(valid_stage_run: StageRun) -> None:
    with pytest.raises(ValidationError, match="E_EXECUTABLE_FIELD_FORBIDDEN"):
        valid_stage_run.model_copy(update={"parameters": {"command": "do-not-run"}})


def test_model_copy_re_freezes_nested_json(valid_stage_run: StageRun) -> None:
    source = {"nested": {"value": 1}}
    changed = valid_stage_run.model_copy(update={"parameters": source})
    source["nested"]["value"] = 2

    assert changed.to_data()["parameters"] == {"nested": {"value": 1}}
    with pytest.raises(TypeError, match="不可修改"):
        changed.parameters["new"] = True  # type: ignore[index]
