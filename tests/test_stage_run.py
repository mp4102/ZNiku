"""验证 StageRun 的精确 Engine、参数及直接 Artifact 绑定。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from zniku.contracts import (
    Artifact,
    ArtifactRef,
    ArtifactSet,
    ArtifactSetMember,
    ArtifactSetRef,
    ArtifactType,
    ContractViolation,
    EngineBinding,
    EngineManifest,
    MediaKind,
    PortBinding,
    Scope,
    StageRun,
    validate_stage_run_bindings,
)


def artifact_authority(
    video_artifact: Artifact, output_artifact: Artifact
) -> Mapping[str, Artifact]:
    return {
        video_artifact.artifact_id: video_artifact,
        output_artifact.artifact_id: output_artifact,
    }


def rebuild_stage_run(base: StageRun, **updates: object) -> StageRun:
    return base.model_copy(update=updates)


def set_port_manifest(manifest_payload: dict[str, Any]) -> EngineManifest:
    manifest_payload["supported_scopes"] = ["chapter"]
    for direction in ("inputs", "outputs"):
        manifest_payload[direction][0]["port"]["scope"] = "chapter"
        manifest_payload[direction][0]["port"]["cardinality"] = "set"
    manifest_payload["inputs"][0]["preconditions_schema"]["properties"] = {
        "frame_rate": {"type": "string", "maxLength": 32}
    }
    manifest_payload["inputs"][0]["preconditions_schema"]["required"] = ["frame_rate"]
    manifest_payload["outputs"][0]["guarantees_schema"]["properties"] = {
        "frame_rate": {"type": "string", "maxLength": 32}
    }
    manifest_payload["outputs"][0]["guarantees_schema"]["required"] = ["frame_rate"]
    manifest_payload["outputs"][0]["attribute_rules"] = [
        manifest_payload["outputs"][0]["attribute_rules"][0]
    ]
    return EngineManifest.from_json(json.dumps(manifest_payload))


def set_stage_run(
    manifest: EngineManifest,
    input_set: ArtifactSet,
    output_set: ArtifactSet,
) -> StageRun:
    return StageRun(
        contract_version="0.1.0",
        stage_run_id="stage_run.set.001",
        workflow_run_id="workflow_run.synthetic.001",
        stage_spec_id="stage_spec.set",
        scope=Scope.CHAPTER,
        scope_id=input_set.scope_id,
        engine=EngineBinding.from_manifest(manifest),
        parameters={"scale": 2, "model": "synthetic-v1"},
        inputs=(
            PortBinding(
                port_id="video_in",
                target=ArtifactSetRef(artifact_set_id=input_set.artifact_set_id),
            ),
        ),
        outputs=(
            PortBinding(
                port_id="video_out",
                target=ArtifactSetRef(artifact_set_id=output_set.artifact_set_id),
            ),
        ),
    )


def test_valid_stage_run_binding(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    validate_stage_run_bindings(
        valid_stage_run,
        valid_manifest,
        artifact_authority(video_artifact, output_artifact),
    )


def test_engine_digest_mismatch_fails_closed(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    wrong_binding = EngineBinding(
        engine_id=valid_manifest.engine_id,
        engine_version=valid_manifest.engine_version,
        manifest_digest="sha256:" + "0" * 64,
    )
    changed = rebuild_stage_run(valid_stage_run, engine=wrong_binding)

    with pytest.raises(ContractViolation, match="E_STAGE_ENGINE_BINDING_MISMATCH"):
        validate_stage_run_bindings(
            changed,
            valid_manifest,
            artifact_authority(video_artifact, output_artifact),
        )


def test_required_input_missing_fails_closed(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    changed = rebuild_stage_run(valid_stage_run, inputs=())

    with pytest.raises(ContractViolation, match="E_STAGE_REQUIRED_INPUT_MISSING"):
        validate_stage_run_bindings(
            changed,
            valid_manifest,
            artifact_authority(video_artifact, output_artifact),
        )


def test_unknown_input_port_fails_closed(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    changed = rebuild_stage_run(
        valid_stage_run,
        inputs=(
            PortBinding(
                port_id="unknown_input",
                target=ArtifactRef(artifact_id=video_artifact.artifact_id),
            ),
        ),
    )

    with pytest.raises(
        ContractViolation,
        match=r"E_STAGE_REQUIRED_INPUT_MISSING|E_STAGE_INPUT_PORT_UNKNOWN",
    ):
        validate_stage_run_bindings(
            changed,
            valid_manifest,
            artifact_authority(video_artifact, output_artifact),
        )


def test_set_reference_cannot_bind_single_port(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    changed = rebuild_stage_run(
        valid_stage_run,
        inputs=(
            PortBinding(
                port_id="video_in",
                target=ArtifactSetRef(artifact_set_id="artifact_set.chapters"),
            ),
        ),
    )
    authority = {
        **artifact_authority(video_artifact, output_artifact),
        "artifact_set.chapters": video_artifact,
    }

    with pytest.raises(ContractViolation, match="E_STAGE_BINDING_CARDINALITY_MISMATCH"):
        validate_stage_run_bindings(changed, valid_manifest, authority)


def test_wrong_artifact_scope_fails_closed(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    chapter_artifact = video_artifact.model_copy(
        update={"artifact_id": "artifact.chapter.input", "scope": Scope.CHAPTER}
    )
    changed = rebuild_stage_run(
        valid_stage_run,
        inputs=(
            PortBinding(
                port_id="video_in",
                target=ArtifactRef(artifact_id=chapter_artifact.artifact_id),
            ),
        ),
    )
    authority = {
        chapter_artifact.artifact_id: chapter_artifact,
        output_artifact.artifact_id: output_artifact,
    }

    with pytest.raises(ContractViolation, match="E_BINDING_SCOPE_MISMATCH"):
        validate_stage_run_bindings(changed, valid_manifest, authority)


def test_stage_parameters_must_satisfy_manifest(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    changed = rebuild_stage_run(valid_stage_run, parameters={"scale": 9, "model": "synthetic-v1"})
    with pytest.raises(ContractViolation, match="E_PARAMETER_INVALID"):
        validate_stage_run_bindings(
            changed,
            valid_manifest,
            artifact_authority(video_artifact, output_artifact),
        )


def test_stage_run_has_no_runtime_status_field(valid_stage_run: StageRun) -> None:
    payload = valid_stage_run.to_data()
    payload["status"] = "complete"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        StageRun.model_validate(payload)


def test_stage_run_rejects_unknown_contract_version(valid_stage_run: StageRun) -> None:
    with pytest.raises(ValidationError):
        valid_stage_run.model_copy(update={"contract_version": "0.2.0"})


def test_duplicate_binding_fails_closed(valid_stage_run: StageRun) -> None:
    binding = valid_stage_run.inputs[0]
    with pytest.raises(ValidationError, match="E_STAGE_INPUT_BINDING_DUPLICATE"):
        StageRun(
            contract_version="0.1.0",
            stage_run_id=valid_stage_run.stage_run_id,
            workflow_run_id=valid_stage_run.workflow_run_id,
            stage_spec_id=valid_stage_run.stage_spec_id,
            scope=valid_stage_run.scope,
            scope_id=valid_stage_run.scope_id,
            engine=valid_stage_run.engine,
            parameters=valid_stage_run.parameters,
            inputs=(binding, binding),
        )


def test_stage_run_rejects_executable_parameter_key(valid_stage_run: StageRun) -> None:
    with pytest.raises(ValidationError, match="E_EXECUTABLE_FIELD_FORBIDDEN"):
        StageRun(
            contract_version="0.1.0",
            stage_run_id=valid_stage_run.stage_run_id,
            workflow_run_id=valid_stage_run.workflow_run_id,
            stage_spec_id=valid_stage_run.stage_spec_id,
            scope=valid_stage_run.scope,
            scope_id=valid_stage_run.scope_id,
            engine=valid_stage_run.engine,
            parameters={"command": "do-not-run"},
            inputs=valid_stage_run.inputs,
        )


def test_authority_lookup_key_must_match_object_id(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    authority = {
        video_artifact.artifact_id: video_artifact.model_copy(
            update={"artifact_id": "artifact.substituted"}
        ),
        output_artifact.artifact_id: output_artifact,
    }

    with pytest.raises(ContractViolation, match="E_STAGE_ARTIFACT_ID_MISMATCH"):
        validate_stage_run_bindings(valid_stage_run, valid_manifest, authority)


def test_binding_scope_id_must_match_stage_target(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    wrong_scope = video_artifact.model_copy(update={"scope_id": "program.other"})
    authority = {
        wrong_scope.artifact_id: wrong_scope,
        output_artifact.artifact_id: output_artifact,
    }

    with pytest.raises(ContractViolation, match="E_STAGE_SCOPE_ID_MISMATCH"):
        validate_stage_run_bindings(valid_stage_run, valid_manifest, authority)


def test_output_must_be_produced_by_current_stage_run(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    wrong_producer = output_artifact.model_copy(update={"producer_stage_run_id": "stage_run.other"})
    authority = {
        video_artifact.artifact_id: video_artifact,
        wrong_producer.artifact_id: wrong_producer,
    }

    with pytest.raises(ContractViolation, match="E_STAGE_OUTPUT_PRODUCER_MISMATCH"):
        validate_stage_run_bindings(valid_stage_run, valid_manifest, authority)


def test_input_and_output_cannot_share_identity(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
) -> None:
    self_output = video_artifact.model_copy(
        update={"producer_stage_run_id": valid_stage_run.stage_run_id}
    )
    changed = valid_stage_run.model_copy(
        update={
            "outputs": (
                PortBinding(
                    port_id="video_out",
                    target=ArtifactRef(artifact_id=video_artifact.artifact_id),
                ),
            )
        }
    )

    with pytest.raises(ContractViolation, match="E_STAGE_INPUT_SELF_PRODUCED"):
        validate_stage_run_bindings(
            changed,
            valid_manifest,
            {video_artifact.artifact_id: self_output},
        )


def test_preserved_media_attribute_is_verified(
    valid_stage_run: StageRun,
    valid_manifest: EngineManifest,
    video_artifact: Artifact,
    output_artifact: Artifact,
) -> None:
    changed_output = output_artifact.model_copy(
        update={"attributes": {"width": 3840, "height": 2160, "frame_rate": "60000/1001"}}
    )
    authority = {
        video_artifact.artifact_id: video_artifact,
        changed_output.artifact_id: changed_output,
    }

    with pytest.raises(ContractViolation, match="E_MEDIA_RULE_PRESERVED_VIOLATION"):
        validate_stage_run_bindings(valid_stage_run, valid_manifest, authority)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (True, 1),
        ({"flag": False}, {"flag": 0}),
    ],
)
def test_preserved_media_attribute_uses_json_type_semantics(
    manifest_payload: dict[str, Any],
    valid_stage_run: StageRun,
    video_artifact: Artifact,
    output_artifact: Artifact,
    before: Any,
    after: Any,
) -> None:
    marker_schema = {
        "type": ["boolean", "integer", "object"],
        "additionalProperties": False,
        "properties": {"flag": {"type": ["boolean", "integer"]}},
        "required": ["flag"],
    }
    for direction, schema_field in (
        ("inputs", "preconditions_schema"),
        ("outputs", "guarantees_schema"),
    ):
        schema = manifest_payload[direction][0][schema_field]
        schema["properties"]["marker"] = marker_schema
        schema["required"].append("marker")
    manifest_payload["outputs"][0]["attribute_rules"] = [
        {
            "path": "marker",
            "disposition": "preserved",
            "source": {"port_id": "video_in", "path": "marker"},
        }
    ]
    manifest = EngineManifest.from_json(json.dumps(manifest_payload))
    input_attributes = dict(video_artifact.attributes)
    input_attributes["marker"] = before
    output_attributes = dict(output_artifact.attributes)
    output_attributes["marker"] = after
    input_with_marker = video_artifact.model_copy(update={"attributes": input_attributes})
    output_with_marker = output_artifact.model_copy(update={"attributes": output_attributes})
    stage_run = valid_stage_run.model_copy(update={"engine": EngineBinding.from_manifest(manifest)})

    with pytest.raises(ContractViolation, match="E_MEDIA_RULE_PRESERVED_VIOLATION"):
        validate_stage_run_bindings(
            stage_run,
            manifest,
            {
                input_with_marker.artifact_id: input_with_marker,
                output_with_marker.artifact_id: output_with_marker,
            },
        )


def test_valid_artifact_set_stage_binding(
    manifest_payload: dict[str, Any],
    valid_artifact_set: ArtifactSet,
) -> None:
    manifest = set_port_manifest(manifest_payload)

    output_members = tuple(
        ArtifactSetMember(
            member_id=member.member_id,
            artifact=Artifact(
                artifact_id=f"artifact.output.chapter.{index}",
                artifact_type=ArtifactType.MEDIA,
                media_kind=MediaKind.VIDEO,
                scope=Scope.CHAPTER,
                scope_id=member.artifact.scope_id,
                producer_stage_run_id="stage_run.set.001",
                attributes=member.artifact.attributes,
            ),
            coverage=member.coverage,
        )
        for index, member in enumerate(valid_artifact_set.members, start=1)
    )
    output_set = ArtifactSet(
        artifact_set_id="artifact_set.output.chapters",
        artifact_type=valid_artifact_set.artifact_type,
        media_kind=valid_artifact_set.media_kind,
        scope=valid_artifact_set.scope,
        scope_id=valid_artifact_set.scope_id,
        expected_member_ids=valid_artifact_set.expected_member_ids,
        members=output_members,
        coverage=valid_artifact_set.coverage,
        coverage_mode=valid_artifact_set.coverage_mode,
        producer_stage_run_id="stage_run.set.001",
    )
    stage_run = set_stage_run(manifest, valid_artifact_set, output_set)

    validate_stage_run_bindings(
        stage_run,
        manifest,
        {
            valid_artifact_set.artifact_set_id: valid_artifact_set,
            output_set.artifact_set_id: output_set,
        },
    )


def test_output_set_members_must_be_produced_by_current_stage_run(
    manifest_payload: dict[str, Any],
    valid_artifact_set: ArtifactSet,
) -> None:
    manifest = set_port_manifest(manifest_payload)
    repacked_output = valid_artifact_set.model_copy(
        update={
            "artifact_set_id": "artifact_set.repacked-output",
            "producer_stage_run_id": "stage_run.set.001",
        }
    )
    stage_run = set_stage_run(manifest, valid_artifact_set, repacked_output)

    with pytest.raises(ContractViolation, match="E_STAGE_OUTPUT_PRODUCER_MISMATCH"):
        validate_stage_run_bindings(
            stage_run,
            manifest,
            {
                valid_artifact_set.artifact_set_id: valid_artifact_set,
                repacked_output.artifact_set_id: repacked_output,
            },
        )


def test_set_member_artifact_identity_cannot_cross_input_and_output(
    manifest_payload: dict[str, Any],
    valid_artifact_set: ArtifactSet,
) -> None:
    manifest = set_port_manifest(manifest_payload)
    output_members = tuple(
        member.model_copy(
            update={
                "artifact": member.artifact.model_copy(
                    update={"producer_stage_run_id": "stage_run.set.001"}
                )
            }
        )
        for member in valid_artifact_set.members
    )
    output_set = valid_artifact_set.model_copy(
        update={
            "artifact_set_id": "artifact_set.same-member-artifacts",
            "members": output_members,
            "producer_stage_run_id": "stage_run.set.001",
        }
    )
    stage_run = set_stage_run(manifest, valid_artifact_set, output_set)

    with pytest.raises(ContractViolation, match="E_STAGE_INPUT_OUTPUT_IDENTITY_CONFLICT"):
        validate_stage_run_bindings(
            stage_run,
            manifest,
            {
                valid_artifact_set.artifact_set_id: valid_artifact_set,
                output_set.artifact_set_id: output_set,
            },
        )


def test_preserved_set_requires_logical_member_and_coverage_alignment(
    manifest_payload: dict[str, Any],
    valid_artifact_set: ArtifactSet,
) -> None:
    manifest = set_port_manifest(manifest_payload)
    output_members = tuple(
        ArtifactSetMember(
            member_id=f"member.output.{index}",
            artifact=member.artifact.model_copy(
                update={
                    "artifact_id": f"artifact.output.member.{index}",
                    "producer_stage_run_id": "stage_run.set.001",
                }
            ),
            coverage=member.coverage,
        )
        for index, member in enumerate(valid_artifact_set.members, start=1)
    )
    output_set = valid_artifact_set.model_copy(
        update={
            "artifact_set_id": "artifact_set.changed-logical-shape",
            "expected_member_ids": tuple(member.member_id for member in output_members),
            "members": output_members,
            "producer_stage_run_id": "stage_run.set.001",
        }
    )
    stage_run = set_stage_run(manifest, valid_artifact_set, output_set)

    with pytest.raises(ContractViolation, match="E_MEDIA_RULE_PRESERVED_SHAPE"):
        validate_stage_run_bindings(
            stage_run,
            manifest,
            {
                valid_artifact_set.artifact_set_id: valid_artifact_set,
                output_set.artifact_set_id: output_set,
            },
        )
