"""验证 Compiler front-end 的图、Manifest 与确定性诊断门禁。"""

from __future__ import annotations

from zniku.authoring import (
    CoreNodeContractSet,
    EngineStageNodeSpec,
    FinalNodeSpec,
    InMemoryManifestCatalog,
    PortEndpoint,
    SourceNodeSpec,
    SpecValidationResult,
    ValidationOutcome,
    WorkflowCompiler,
    WorkflowEdgeSpec,
    WorkflowSpec,
)
from zniku.contracts import EngineBinding, EngineManifest


def diagnostic_codes(result: SpecValidationResult) -> set[str]:
    return {item.stable_code for item in result.diagnostics}


def test_valid_program_graph_is_authoring_valid(
    workflow_compiler: WorkflowCompiler,
    valid_workflow_spec: WorkflowSpec,
) -> None:
    result = workflow_compiler.validate(valid_workflow_spec)

    assert result.outcome is ValidationOutcome.AUTHORING_VALID
    assert result.diagnostics == ()
    assert result.spec_digest == valid_workflow_spec.sha256_digest()
    assert result.core_node_contract_digest == CoreNodeContractSet.phase_2a().sha256_digest()


def test_incomplete_draft_returns_blocking_diagnostics(
    workflow_compiler: WorkflowCompiler,
    initial_workflow_spec: WorkflowSpec,
) -> None:
    result = workflow_compiler.validate(initial_workflow_spec)

    assert result.outcome is ValidationOutcome.INVALID
    assert {
        "E_GRAPH_INPUT_CARDINALITY",
        "E_GRAPH_NON_FINAL_TERMINAL",
        "E_GRAPH_NODE_NOT_ON_SOURCE_FINAL_PATH",
    } <= diagnostic_codes(result)


def test_engine_parameter_error_is_manifest_diagnostic(
    workflow_compiler: WorkflowCompiler,
    valid_workflow_spec: WorkflowSpec,
) -> None:
    engine = next(
        node for node in valid_workflow_spec.nodes if isinstance(node, EngineStageNodeSpec)
    )
    invalid = valid_workflow_spec.model_copy(
        update={
            "nodes": tuple(
                node.model_copy(update={"parameters": {"strength": 99}})
                if node.node_id == engine.node_id
                else node
                for node in valid_workflow_spec.nodes
            )
        }
    )

    result = workflow_compiler.validate(invalid)

    assert "E_ENGINE_PARAMETERS_INVALID" in diagnostic_codes(result)
    diagnostic = next(
        item for item in result.diagnostics if item.stable_code == "E_ENGINE_PARAMETERS_INVALID"
    )
    assert diagnostic.phase.value == "manifest"
    assert diagnostic.entity_ref.kind == "parameter"


def test_video_engine_is_incompatible_with_program_media_core_ports(
    valid_manifest: EngineManifest,
) -> None:
    compiler = WorkflowCompiler(
        InMemoryManifestCatalog((valid_manifest,)),
        CoreNodeContractSet.phase_2a(),
    )
    spec = WorkflowSpec(
        workflow_contract_version="0.1.0",
        workflow_id="workflow.incompatible.media",
        nodes=(
            SourceNodeSpec(kind="source", node_id="node.source"),
            EngineStageNodeSpec(
                kind="engine_stage",
                node_id="node.engine",
                engine=EngineBinding.from_manifest(valid_manifest),
                parameters={"scale": 2, "model": "synthetic-v1"},
            ),
            FinalNodeSpec(kind="final", node_id="node.final"),
        ),
        edges=(
            WorkflowEdgeSpec(
                edge_id="edge.source.engine",
                source=PortEndpoint(node_id="node.source", port_id="program"),
                target=PortEndpoint(node_id="node.engine", port_id="video_in"),
            ),
            WorkflowEdgeSpec(
                edge_id="edge.engine.final",
                source=PortEndpoint(node_id="node.engine", port_id="video_out"),
                target=PortEndpoint(node_id="node.final", port_id="program"),
            ),
        ),
    )

    result = compiler.validate(spec)

    assert "E_PORT_MEDIA_KIND_INCOMPATIBLE" in diagnostic_codes(result)


def test_unknown_manifest_does_not_emit_dependent_unknown_port_noise(
    workflow_compiler: WorkflowCompiler,
    valid_workflow_spec: WorkflowSpec,
) -> None:
    engine = next(
        node for node in valid_workflow_spec.nodes if isinstance(node, EngineStageNodeSpec)
    )
    unknown_binding = engine.engine.model_copy(update={"manifest_digest": "sha256:" + "0" * 64})
    unknown = valid_workflow_spec.model_copy(
        update={
            "nodes": tuple(
                node.model_copy(update={"engine": unknown_binding})
                if node.node_id == engine.node_id
                else node
                for node in valid_workflow_spec.nodes
            )
        }
    )

    result = workflow_compiler.validate(unknown)
    codes = diagnostic_codes(result)

    assert "E_ENGINE_BINDING_UNKNOWN" in codes
    assert "E_GRAPH_SOURCE_PORT_UNKNOWN" not in codes
    assert "E_GRAPH_TARGET_PORT_UNKNOWN" not in codes


def test_cycle_diagnostic_is_one_per_strong_component(
    workflow_compiler: WorkflowCompiler,
    valid_workflow_spec: WorkflowSpec,
) -> None:
    cyclic = valid_workflow_spec.model_copy(
        update={
            "edges": (
                *valid_workflow_spec.edges,
                WorkflowEdgeSpec(
                    edge_id="edge.final.engine",
                    source=PortEndpoint(node_id="node.final.program", port_id="program"),
                    target=PortEndpoint(node_id="node.engine.filter", port_id="program_in"),
                ),
            )
        }
    )

    result = workflow_compiler.validate(cyclic)
    cycles = tuple(item for item in result.diagnostics if item.stable_code == "E_GRAPH_CYCLE")

    assert len(cycles) == 1


def test_diagnostics_are_stable_for_graph_input_order(
    workflow_compiler: WorkflowCompiler,
    initial_workflow_spec: WorkflowSpec,
) -> None:
    reordered = initial_workflow_spec.model_copy(
        update={"nodes": tuple(reversed(initial_workflow_spec.nodes))}
    )

    assert (
        workflow_compiler.validate(initial_workflow_spec).to_canonical_json()
        == workflow_compiler.validate(reordered).to_canonical_json()
    )
