"""验证 Phase 6 Engine 扩展、Registry 投影与 Runtime 无私有分支。"""

from __future__ import annotations

import inspect

from zniku.contracts import (
    Artifact,
    ArtifactRef,
    ArtifactType,
    CoverageSpan,
    CoverageUnit,
    JsonObject,
    MediaKind,
    PortBinding,
    Scope,
    StageRun,
)
from zniku.engines import (
    ArtifactValue,
    EngineInvocationRequest,
    InstalledEngineCatalog,
    phase6_extension_packages,
)
from zniku.pipelines import build_phase6_extension_workflow
from zniku.studio import build_studio_authority_projection
from zniku.workflow.execution import (
    ChapterMemberBinding,
    ChapterPlan,
    PlannedSubjectKind,
    SourceBinding,
    SyntheticRuntime,
    WorkflowBindingSet,
    compile_execution_plan,
    freeze_revision,
    preflight_workflow,
)


def _video(scope: Scope, scope_id: str) -> Artifact:
    return Artifact(
        artifact_id=f"artifact.input.{scope.value}",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.VIDEO,
        scope=scope,
        scope_id=scope_id,
        producer_stage_run_id="stage_run.upstream",
        attributes={"duration_frames": 240},
    )


def _invoke_extension(index: int, parameters: JsonObject) -> Artifact:
    packages = phase6_extension_packages()
    package = packages[index]
    scope = package.manifest.supported_scopes[0]
    scope_id = "scope.chapter.a" if scope is Scope.CHAPTER else "program.phase6"
    source = _video(scope, scope_id)
    stage = StageRun(
        contract_version="0.1.0",
        stage_run_id=f"stage_run.phase6.{index}",
        workflow_run_id="workflow_run.phase6",
        stage_spec_id=f"stage_spec.phase6.{index}",
        scope=scope,
        scope_id=scope_id,
        engine=package.descriptor.engine,
        parameters=parameters,
        inputs=(
            PortBinding(
                port_id="video_in",
                target=ArtifactRef(artifact_id=source.artifact_id),
            ),
        ),
    )
    result = InstalledEngineCatalog(packages).invoke(
        EngineInvocationRequest(
            sdk_contract_version="0.1.0",
            invocation_id=f"invocation.phase6.{index}",
            stage_run=stage,
            inputs=(ArtifactValue(port_id="video_in", artifact=source),),
        )
    )
    value = result.outputs[0]
    assert isinstance(value, ArtifactValue)
    return value.artifact


def _source() -> Artifact:
    return Artifact(
        artifact_id="artifact.phase6.source",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.PROGRAM_MEDIA,
        scope=Scope.PROGRAM,
        scope_id="program.phase6",
        attributes={"duration_frames": 360, "audio_stream_ids": ["audio.main"]},
    )


def _bindings(source: Artifact) -> WorkflowBindingSet:
    return WorkflowBindingSet(
        binding_contract_version="0.1.0",
        sources=(
            SourceBinding(
                source_node_id="node.source",
                artifact_id=source.artifact_id,
                artifact_digest=source.sha256_digest(),
            ),
        ),
        chapter_plan=ChapterPlan(
            chapter_plan_id="chapter_plan.phase6",
            members=tuple(
                ChapterMemberBinding(
                    member_id=f"chapter.{member}",
                    scope_id=f"scope.chapter.{member}",
                    coverage=CoverageSpan(
                        unit=CoverageUnit.FRAME,
                        start=index * 120,
                        end=(index + 1) * 120,
                    ),
                )
                for index, member in enumerate(("a", "b", "c"))
            ),
            coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=360),
        ),
    )


def test_extension_packages_pass_existing_catalog_conformance() -> None:
    decensored = _invoke_extension(0, {"model_name": "Jasna Synthetic", "model_version": "0.1.0"})
    new01 = _invoke_extension(1, {"profile": "phase6-demo"})

    assert decensored.attributes["phase6_effect"] == "mosaic-removal"
    assert new01.attributes["phase6_effect"] == "zniku:new01"
    assert decensored.artifact_id != "artifact.input.chapter"
    assert new01.artifact_id != "artifact.input.program"
    assert decensored.producer_stage_run_id == "stage_run.phase6.0"


def test_decensoring_branch_and_new01_compile_and_run_without_runtime_branch() -> None:
    bundle = build_phase6_extension_workflow()
    source = _source()
    bindings = _bindings(source)
    preflight = preflight_workflow(
        bundle.spec, bindings, {source.artifact_id: source}, bundle.compiler
    )
    assert preflight.valid
    plan = compile_execution_plan(bundle.spec, bindings, preflight)
    decensoring = tuple(node for node in plan.nodes if node.stage_spec_id == "node.decensoring")
    assert len(decensoring) == 1
    assert decensoring[0].scope_id == "scope.chapter.a"
    new01 = next(node for node in plan.nodes if node.stage_spec_id == "node.new01")
    assert new01.subject_kind is PlannedSubjectKind.ENGINE
    assert new01.scope is Scope.PROGRAM

    revision = freeze_revision(bundle.spec, bindings, plan)
    runtime = SyntheticRuntime.start(plan, revision, "workflow_run.phase6.extension")
    while runtime.ready_nodes():
        for node_id in runtime.ready_nodes():
            runtime.execute(node_id)
    assert runtime.snapshot.final_output_identity is not None

    runtime_source = inspect.getsource(SyntheticRuntime)
    assert "synthetic-decensoring" not in runtime_source
    assert "synthetic-new01" not in runtime_source


def test_installed_extensions_automatically_enter_studio_registry_projection() -> None:
    projection = build_studio_authority_projection()
    identities = {manifest.engine_id for manifest in projection.registry_manifests}

    assert "zniku.extension.synthetic-decensoring" in identities
    assert "zniku.extension.synthetic-new01" in identities
    assert tuple(manifest.engine_id for manifest in projection.registry_manifests) == tuple(
        sorted(identities)
    )
