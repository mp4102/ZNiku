"""验证 Phase 2 Core Operator、Preflight、ExecutionPlan、Freeze 与合成 Runtime。"""

from __future__ import annotations

import pytest

from zniku.authoring import (
    CoreNodeContractSet,
    EngineStageNodeSpec,
    FinalNodeSpec,
    InMemoryManifestCatalog,
    PortEndpoint,
    SourceNodeSpec,
    WorkflowCompiler,
    WorkflowEdgeSpec,
    WorkflowSpec,
)
from zniku.contracts import (
    Artifact,
    ArtifactType,
    ContractViolation,
    CoverageSpan,
    CoverageUnit,
    EngineBinding,
    EngineManifest,
    JsonObject,
    MediaKind,
    Scope,
)
from zniku.engines import builtin_engine_packages
from zniku.workflow import (
    CoreOperatorKind,
    CoreOperatorNodeSpec,
    operator_input_ports,
    operator_output_ports,
)
from zniku.workflow.execution import (
    ChapterMemberBinding,
    ChapterPlan,
    NodeRunState,
    RuntimeSnapshot,
    SourceBinding,
    SyntheticRuntime,
    WorkflowBindingSet,
    compile_execution_plan,
    freeze_revision,
    preflight_workflow,
)


def _chapter_engine() -> EngineManifest:
    schema: JsonObject = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    return EngineManifest.from_data(
        {
            "contract_version": "0.1.0",
            "engine_id": "example.synthetic.chapter-filter",
            "engine_version": "1.0.0",
            "display_name": "Synthetic Chapter Filter",
            "execution_mode": "automatic",
            "lifecycle": {
                "supports_acceptance": True,
                "supports_publication": False,
                "supports_recovery": False,
            },
            "supported_scopes": ["chapter"],
            "inputs": [
                {
                    "port": {
                        "port_id": "video_in",
                        "artifact_type": "media",
                        "media_kind": "video",
                        "scope": "chapter",
                        "cardinality": "one",
                    },
                    "preconditions_schema": dict(schema),
                }
            ],
            "outputs": [
                {
                    "port": {
                        "port_id": "video_out",
                        "artifact_type": "media",
                        "media_kind": "video",
                        "scope": "chapter",
                        "cardinality": "one",
                    },
                    "guarantees_schema": dict(schema),
                    "attribute_rules": [],
                }
            ],
            "parameter_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"strength": {"type": "integer", "minimum": 1, "maximum": 3}},
                "required": ["strength"],
                "additionalProperties": False,
            },
        }
    )


def _workflow() -> tuple[WorkflowSpec, WorkflowCompiler]:
    demux_package, mux_package = builtin_engine_packages()
    chapter = _chapter_engine()
    nodes = (
        SourceNodeSpec(kind="source", node_id="node.source"),
        EngineStageNodeSpec(
            kind="engine_stage",
            node_id="node.demux",
            engine=demux_package.descriptor.engine,
            parameters={},
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.partition",
            operator_kind=CoreOperatorKind.PARTITION,
            media_kind=MediaKind.VIDEO,
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.select",
            operator_kind=CoreOperatorKind.SELECT,
            media_kind=MediaKind.VIDEO,
            selected_member_ids=("chapter.001", "chapter.003"),
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.map",
            operator_kind=CoreOperatorKind.MAP,
            media_kind=MediaKind.VIDEO,
            engine=EngineBinding.from_manifest(chapter),
            parameters={"strength": 2},
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.collect",
            operator_kind=CoreOperatorKind.COLLECT,
            media_kind=MediaKind.VIDEO,
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.reduce",
            operator_kind=CoreOperatorKind.REDUCE,
            media_kind=MediaKind.VIDEO,
        ),
        EngineStageNodeSpec(
            kind="engine_stage",
            node_id="node.mux",
            engine=mux_package.descriptor.engine,
            parameters={"container": "matroska"},
        ),
        FinalNodeSpec(kind="final", node_id="node.final"),
    )
    edge_data = (
        ("source-demux", "node.source", "program", "node.demux", "program_in"),
        ("demux-partition", "node.demux", "video_out", "node.partition", "in"),
        ("partition-select", "node.partition", "out", "node.select", "in"),
        ("select-map", "node.select", "selected", "node.map", "in"),
        ("map-collect", "node.map", "out", "node.collect", "processed"),
        ("remainder-collect", "node.select", "remainder", "node.collect", "remainder"),
        ("collect-reduce", "node.collect", "out", "node.reduce", "in"),
        ("reduce-mux", "node.reduce", "out", "node.mux", "video_in"),
        ("audio-mux", "node.demux", "audio_out", "node.mux", "audio_in"),
        ("mux-final", "node.mux", "program_out", "node.final", "program"),
    )
    edges = tuple(
        WorkflowEdgeSpec(
            edge_id=f"edge.{edge_id}",
            source=PortEndpoint(node_id=source_node, port_id=source_port),
            target=PortEndpoint(node_id=target_node, port_id=target_port),
        )
        for edge_id, source_node, source_port, target_node, target_port in edge_data
    )
    spec = WorkflowSpec(
        workflow_contract_version="0.2.0",
        workflow_id="workflow.default.synthetic",
        nodes=nodes,
        edges=edges,
    )
    compiler = WorkflowCompiler(
        InMemoryManifestCatalog((demux_package.manifest, mux_package.manifest, chapter)),
        CoreNodeContractSet.phase_2a(),
    )
    return spec, compiler


def _source() -> Artifact:
    return Artifact(
        artifact_id="artifact.program.source",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.PROGRAM_MEDIA,
        scope=Scope.PROGRAM,
        scope_id="program.synthetic",
        attributes={"duration_frames": 300, "audio_stream_ids": ["audio.001"]},
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
            chapter_plan_id="chapter_plan.synthetic",
            members=tuple(
                ChapterMemberBinding(
                    member_id=f"chapter.{index:03d}",
                    scope_id=f"scope.chapter.{index:03d}",
                    coverage=CoverageSpan(
                        unit=CoverageUnit.FRAME, start=(index - 1) * 100, end=index * 100
                    ),
                )
                for index in range(1, 4)
            ),
            coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=300),
        ),
    )


def test_operator_workflow_requires_new_contract_version() -> None:
    spec, _ = _workflow()
    with pytest.raises(ValueError, match="E_WORKFLOW_VERSION_OPERATOR_UNSUPPORTED"):
        spec.model_copy(update={"workflow_contract_version": "0.1.0"})


@pytest.mark.parametrize("operator_kind", tuple(CoreOperatorKind))
def test_all_core_operators_have_closed_typed_ports(operator_kind: CoreOperatorKind) -> None:
    values: dict[str, object] = {
        "kind": "core_operator",
        "node_id": f"node.operator.{operator_kind.value}",
        "operator_kind": operator_kind,
        "media_kind": MediaKind.VIDEO,
    }
    if operator_kind is CoreOperatorKind.MAP:
        values.update(
            engine=EngineBinding.from_manifest(_chapter_engine()), parameters={"strength": 2}
        )
    if operator_kind is CoreOperatorKind.SELECT:
        values["selected_member_ids"] = ("chapter.001",)
    node = CoreOperatorNodeSpec.model_validate(values)

    assert operator_input_ports(node)
    assert operator_output_ports(node)
    assert all(
        port.media_kind is MediaKind.VIDEO
        for port in (*operator_input_ports(node), *operator_output_ports(node))
    )


def test_preflight_plan_freeze_and_runtime_recovery() -> None:
    spec, compiler = _workflow()
    source = _source()
    bindings = _bindings(source)
    preflight = preflight_workflow(spec, bindings, {source.artifact_id: source}, compiler)
    assert preflight.valid
    plan = compile_execution_plan(spec, bindings, preflight)
    map_nodes = tuple(node for node in plan.nodes if node.stage_spec_id == "node.map")
    assert tuple(node.scope_id for node in map_nodes) == ("scope.chapter.001", "scope.chapter.003")
    revision = freeze_revision(spec, bindings, plan)
    runtime = SyntheticRuntime.start(plan, revision, "workflow_run.synthetic")
    while runtime.ready_nodes():
        for node_id in runtime.ready_nodes():
            runtime.execute(node_id)
    assert all(record.state is NodeRunState.COMPLETE for record in runtime.snapshot.nodes)
    assert runtime.snapshot.final_output_identity is not None
    assert len(runtime.snapshot.evidence) == len(plan.nodes)
    restored = SyntheticRuntime(
        plan, revision, RuntimeSnapshot.from_json(runtime.snapshot.to_canonical_bytes())
    )
    assert restored.snapshot == runtime.snapshot
    final_node = next(
        record for record in restored.snapshot.nodes if record.plan_node_id == "plan.node.final"
    )
    assert restored.execute(final_node.plan_node_id) == restored.snapshot


def test_preflight_rejects_unknown_selector_and_digest_drift() -> None:
    spec, compiler = _workflow()
    source = _source()
    bindings = _bindings(source)
    select = next(
        node
        for node in spec.nodes
        if isinstance(node, CoreOperatorNodeSpec) and node.operator_kind is CoreOperatorKind.SELECT
    )
    bad_select = select.model_copy(update={"selected_member_ids": ("chapter.missing",)})
    bad_spec = spec.model_copy(
        update={
            "nodes": tuple(
                bad_select if node.node_id == select.node_id else node for node in spec.nodes
            )
        }
    )
    preflight = preflight_workflow(bad_spec, bindings, {source.artifact_id: source}, compiler)
    assert not preflight.valid
    assert "E_PREFLIGHT_SELECTOR_UNKNOWN_MEMBER" in preflight.diagnostic_codes
    drifted = bindings.model_copy(
        update={
            "sources": (
                bindings.sources[0].model_copy(update={"artifact_digest": "sha256:" + "0" * 64}),
            )
        }
    )
    result = preflight_workflow(spec, drifted, {source.artifact_id: source}, compiler)
    assert not result.valid
    assert "E_PREFLIGHT_SOURCE_DIGEST_MISMATCH" in result.diagnostic_codes


def test_runtime_failure_requires_explicit_retry_and_no_evidence() -> None:
    spec, compiler = _workflow()
    source = _source()
    bindings = _bindings(source)
    preflight = preflight_workflow(spec, bindings, {source.artifact_id: source}, compiler)
    plan = compile_execution_plan(spec, bindings, preflight)
    revision = freeze_revision(spec, bindings, plan)
    runtime = SyntheticRuntime.start(plan, revision, "workflow_run.failure")
    node_id = runtime.ready_nodes()[0]
    failed = runtime.execute(node_id, fail_attempt=True)
    record = next(record for record in failed.nodes if record.plan_node_id == node_id)
    assert record.state is NodeRunState.FAILED
    assert failed.evidence == ()
    with pytest.raises(ContractViolation, match="E_RUNTIME_NODE_NOT_READY"):
        runtime.execute(node_id)
    runtime.retry(node_id)
    runtime.execute(node_id)
    assert len(runtime.snapshot.evidence) == 1
