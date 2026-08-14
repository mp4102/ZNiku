"""验证 Phase 1 Engine package、Installed Catalog 与纯合成 Demux/Mux conformance。"""

from __future__ import annotations

from typing import cast

import pytest

from zniku.contracts import (
    Artifact,
    ArtifactRef,
    ArtifactSetRef,
    ArtifactType,
    ContractViolation,
    EngineBinding,
    MediaKind,
    PortBinding,
    Scope,
    StageRun,
)
from zniku.engines import (
    ArtifactSetValue,
    ArtifactValue,
    EngineInvocationRequest,
    EngineInvocationResult,
    EnginePackage,
    InstalledEngineCatalog,
    InstalledEngineState,
    builtin_engine_packages,
)


def _source_artifact() -> Artifact:
    return Artifact(
        artifact_id="artifact.source.program",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.PROGRAM_MEDIA,
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        attributes={
            "duration_frames": 240,
            "audio_stream_ids": ["audio.english", "audio.japanese"],
        },
    )


def _demux_request(package: EnginePackage) -> EngineInvocationRequest:
    source = _source_artifact()
    stage_run = StageRun(
        contract_version="0.1.0",
        stage_run_id="stage_run.synthetic.demux",
        workflow_run_id="workflow_run.synthetic",
        stage_spec_id="stage_spec.demux",
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        engine=package.descriptor.engine,
        parameters={},
        inputs=(
            PortBinding(
                port_id="program_in",
                target=ArtifactRef(artifact_id=source.artifact_id),
            ),
        ),
    )
    return EngineInvocationRequest(
        sdk_contract_version="0.1.0",
        invocation_id="invocation.synthetic.demux",
        stage_run=stage_run,
        inputs=(ArtifactValue(port_id="program_in", artifact=source),),
    )


def _mux_request(package: EnginePackage, demux: EngineInvocationResult) -> EngineInvocationRequest:
    video = demux.outputs[0]
    audio = demux.outputs[1]
    assert isinstance(video, ArtifactValue)
    assert isinstance(audio, ArtifactSetValue)
    stage_run = StageRun(
        contract_version="0.1.0",
        stage_run_id="stage_run.synthetic.mux",
        workflow_run_id="workflow_run.synthetic",
        stage_spec_id="stage_spec.mux",
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        engine=package.descriptor.engine,
        parameters={"container": "matroska"},
        inputs=(
            PortBinding(
                port_id="video_in",
                target=ArtifactRef(artifact_id=video.artifact.artifact_id),
            ),
            PortBinding(
                port_id="audio_in",
                target=ArtifactSetRef(artifact_set_id=audio.artifact_set.artifact_set_id),
            ),
        ),
    )
    return EngineInvocationRequest(
        sdk_contract_version="0.1.0",
        invocation_id="invocation.synthetic.mux",
        stage_run=stage_run,
        inputs=(
            ArtifactValue(port_id="video_in", artifact=video.artifact),
            ArtifactSetValue(port_id="audio_in", artifact_set=audio.artifact_set),
        ),
    )


def test_builtin_packages_are_exact_and_catalog_is_deterministic() -> None:
    packages = builtin_engine_packages()
    catalog = InstalledEngineCatalog(reversed(packages))

    assert tuple(record.descriptor.package_id for record in catalog.records()) == (
        "zniku.builtin.synthetic-demux",
        "zniku.builtin.synthetic-mux",
    )
    assert all(record.state is InstalledEngineState.ENABLED for record in catalog.records())
    for package in packages:
        assert catalog.resolve(package.descriptor.engine) == package.manifest
        assert package.descriptor.engine == EngineBinding.from_manifest(package.manifest)


def test_disabled_package_fails_closed() -> None:
    demux, mux = builtin_engine_packages()
    catalog = InstalledEngineCatalog(
        (demux, mux),
        disabled_package_ids=(demux.descriptor.package_id,),
    )

    assert catalog.resolve(demux.descriptor.engine) is None
    with pytest.raises(ContractViolation, match="E_ENGINE_DISABLED"):
        catalog.invoke(_demux_request(demux))


def test_unknown_disabled_package_and_duplicate_package_fail_closed() -> None:
    demux, mux = builtin_engine_packages()
    with pytest.raises(ContractViolation, match="E_ENGINE_DISABLED_PACKAGE_UNKNOWN"):
        InstalledEngineCatalog((demux,), disabled_package_ids=("missing.package",))
    with pytest.raises(ContractViolation, match="E_ENGINE_PACKAGE_ID_DUPLICATE"):
        InstalledEngineCatalog((demux, demux))

    conflicting_descriptor = demux.descriptor.model_copy(
        update={"package_id": "another.synthetic-demux"}
    )
    conflicting_package = EnginePackage(
        descriptor=conflicting_descriptor,
        manifest=demux.manifest,
        adapter=demux.adapter,
    )
    with pytest.raises(ContractViolation, match="E_ENGINE_BINDING_DUPLICATE"):
        InstalledEngineCatalog((demux, conflicting_package))

    with pytest.raises(ContractViolation, match="E_ENGINE_PACKAGE_MANIFEST_MISMATCH"):
        EnginePackage(
            descriptor=demux.descriptor,
            manifest=mux.manifest,
            adapter=demux.adapter,
        )


def test_synthetic_demux_and_mux_form_a_typed_no_replace_chain() -> None:
    demux_package, mux_package = builtin_engine_packages()
    catalog = InstalledEngineCatalog((demux_package, mux_package))

    demux_request = _demux_request(demux_package)
    demux_first = catalog.invoke(demux_request)
    demux_replay = catalog.invoke(demux_request)
    assert demux_first == demux_replay
    assert tuple(value.port_id for value in demux_first.outputs) == ("video_out", "audio_out")
    video = demux_first.outputs[0]
    audio = demux_first.outputs[1]
    assert isinstance(video, ArtifactValue)
    assert isinstance(audio, ArtifactSetValue)
    assert audio.artifact_set.expected_member_ids == (
        "member.audio.001",
        "member.audio.002",
    )
    assert [member.artifact.attributes["stream_id"] for member in audio.artifact_set.members] == [
        "audio.english",
        "audio.japanese",
    ]

    mux_result = catalog.invoke(_mux_request(mux_package, demux_first))
    assert len(mux_result.outputs) == 1
    program = mux_result.outputs[0]
    assert isinstance(program, ArtifactValue)
    assert program.artifact.media_kind is MediaKind.PROGRAM_MEDIA
    assert cast(tuple[str, ...], program.artifact.attributes["audio_stream_ids"]) == (
        "audio.english",
        "audio.japanese",
    )
    assert program.artifact.artifact_id not in {
        video.artifact.artifact_id,
        audio.artifact_set.artifact_set_id,
    }


def test_invocation_round_trip_and_digest_are_stable() -> None:
    demux, _ = builtin_engine_packages()
    request = _demux_request(demux)

    restored = EngineInvocationRequest.from_json(request.to_canonical_bytes())
    assert restored == request
    assert restored.sha256_digest() == request.sha256_digest()

    payload = request.to_data()
    payload["unknown"] = True
    with pytest.raises(ValueError, match="extra_forbidden"):
        EngineInvocationRequest.from_data(payload)


def test_request_values_must_match_stage_run_bindings() -> None:
    demux, _ = builtin_engine_packages()
    request = _demux_request(demux)

    with pytest.raises(ValueError, match="E_ENGINE_REQUEST_INPUT_BINDING_MISMATCH"):
        request.model_copy(update={"inputs": ()})


class _MissingOutputAdapter:
    def invoke(self, request: EngineInvocationRequest) -> EngineInvocationResult:
        return EngineInvocationResult(
            sdk_contract_version="0.1.0",
            invocation_id=request.invocation_id,
            engine=request.stage_run.engine,
            outputs=(),
        )


def test_adapter_candidate_must_pass_output_contract() -> None:
    demux, _ = builtin_engine_packages()
    broken = EnginePackage(
        descriptor=demux.descriptor,
        manifest=demux.manifest,
        adapter=_MissingOutputAdapter(),
    )

    with pytest.raises(ContractViolation, match="E_ENGINE_RESULT_OUTPUT_MISSING"):
        InstalledEngineCatalog((broken,)).invoke(_demux_request(broken))
