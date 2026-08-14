"""验证 WorkflowSpec、core node contracts 与 Diagnostic 的结构不变量。"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

from zniku.authoring import (
    CoreNodeContractSet,
    Diagnostic,
    DiagnosticPhase,
    DiagnosticSeverity,
    EdgeEntityRef,
    WorkflowSpec,
)


def mutable_payload(spec: WorkflowSpec) -> dict[str, Any]:
    return cast(dict[str, Any], spec.to_data())


def test_workflow_spec_round_trip_and_graph_order_are_stable(
    valid_workflow_spec: WorkflowSpec,
) -> None:
    reversed_spec = valid_workflow_spec.model_copy(
        update={
            "nodes": tuple(reversed(valid_workflow_spec.nodes)),
            "edges": tuple(reversed(valid_workflow_spec.edges)),
        }
    )

    assert reversed_spec.nodes == valid_workflow_spec.nodes
    assert reversed_spec.edges == valid_workflow_spec.edges
    assert reversed_spec.sha256_digest() == valid_workflow_spec.sha256_digest()
    assert WorkflowSpec.from_json(valid_workflow_spec.to_canonical_json()) == valid_workflow_spec


def test_duplicate_node_id_is_a_structural_failure(valid_workflow_spec: WorkflowSpec) -> None:
    payload = mutable_payload(valid_workflow_spec)
    payload["nodes"].append(payload["nodes"][0])
    with pytest.raises(ValidationError, match="E_WORKFLOW_NODE_ID_DUPLICATE"):
        WorkflowSpec.from_json(json.dumps(payload))


def test_duplicate_edge_id_is_a_structural_failure(valid_workflow_spec: WorkflowSpec) -> None:
    payload = mutable_payload(valid_workflow_spec)
    payload["edges"].append(payload["edges"][0])
    with pytest.raises(ValidationError, match="E_WORKFLOW_EDGE_ID_DUPLICATE"):
        WorkflowSpec.from_json(json.dumps(payload))


@pytest.mark.parametrize("field", ["workflow_contract_version", "workflow_id", "nodes", "edges"])
def test_required_workflow_fields_fail_closed(
    valid_workflow_spec: WorkflowSpec, field: str
) -> None:
    payload = mutable_payload(valid_workflow_spec)
    del payload[field]
    with pytest.raises(ValidationError):
        WorkflowSpec.from_json(json.dumps(payload))


def test_unknown_workflow_field_and_editor_state_fail_closed(
    valid_workflow_spec: WorkflowSpec,
) -> None:
    payload = mutable_payload(valid_workflow_spec)
    payload["editor_state"] = {"zoom": 0.8}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkflowSpec.from_json(json.dumps(payload))


def test_missing_node_discriminator_fails_closed(valid_workflow_spec: WorkflowSpec) -> None:
    payload = mutable_payload(valid_workflow_spec)
    del payload["nodes"][0]["kind"]
    with pytest.raises(ValidationError):
        WorkflowSpec.from_json(json.dumps(payload))


def test_engine_parameters_cannot_carry_executable_authority(
    valid_workflow_spec: WorkflowSpec,
) -> None:
    payload = mutable_payload(valid_workflow_spec)
    engine = next(node for node in payload["nodes"] if node["kind"] == "engine_stage")
    engine["parameters"] = {"entrypoint": "unsafe"}
    with pytest.raises(ValidationError, match="E_EXECUTABLE_FIELD_FORBIDDEN"):
        WorkflowSpec.from_json(json.dumps(payload))


def test_core_node_contract_is_exact_and_digest_stable() -> None:
    contracts = CoreNodeContractSet.phase_2a()

    assert contracts.source_outputs == contracts.final_inputs
    assert contracts.source_outputs[0].port_id == "program"
    assert (
        contracts.sha256_digest()
        == CoreNodeContractSet.from_json(contracts.to_canonical_json()).sha256_digest()
    )


def test_diagnostic_identity_ignores_related_ref_input_order() -> None:
    first = EdgeEntityRef(kind="edge", edge_id="edge.a")
    second = EdgeEntityRef(kind="edge", edge_id="edge.b")
    diagnostic_a = Diagnostic.create(
        stable_code="E_GRAPH_TEST",
        occurrence_key="test.occurrence",
        severity=DiagnosticSeverity.ERROR,
        phase=DiagnosticPhase.GRAPH,
        entity_ref=first,
        related_refs=(second, first),
        message="测试诊断 A",
    )
    diagnostic_b = Diagnostic.create(
        stable_code="E_GRAPH_TEST",
        occurrence_key="test.occurrence",
        severity=DiagnosticSeverity.ERROR,
        phase=DiagnosticPhase.GRAPH,
        entity_ref=first,
        related_refs=(first, second),
        message="本地化文案可以变化",
    )

    assert diagnostic_a.related_refs == diagnostic_b.related_refs
    assert diagnostic_a.diagnostic_id == diagnostic_b.diagnostic_id


def test_diagnostic_id_mismatch_fails_closed() -> None:
    with pytest.raises(ValidationError, match="E_DIAGNOSTIC_ID_MISMATCH"):
        Diagnostic(
            diagnostic_id="sha256:" + "0" * 64,
            stable_code="E_GRAPH_TEST",
            occurrence_key="test.occurrence",
            severity=DiagnosticSeverity.ERROR,
            phase=DiagnosticPhase.GRAPH,
            entity_ref=EdgeEntityRef(kind="edge", edge_id="edge.a"),
            message="测试诊断",
        )
