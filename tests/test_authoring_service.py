"""验证 Authoring Service 的同步 revision、幂等、stale 与原子失败语义。"""

from __future__ import annotations

import json

from zniku.authoring import (
    AuthoringCommand,
    AuthoringCommandRejected,
    AuthoringService,
    AuthoringWireFacade,
    ConnectPortsIntent,
    DisconnectPortsIntent,
    PortEndpoint,
    ReplaceParametersIntent,
    ValidationOutcome,
    WireParseFailure,
    WorkflowCompiler,
    WorkflowDraftSnapshot,
    WorkflowSpec,
)


def make_service(
    compiler: WorkflowCompiler,
    initial_spec: WorkflowSpec,
) -> tuple[AuthoringService, WorkflowDraftSnapshot]:
    service = AuthoringService(compiler)
    snapshot = service.create_draft("draft.synthetic.program", initial_spec)
    return service, snapshot


def connect_command(*, command_id: str, revision: int = 0) -> AuthoringCommand:
    return AuthoringCommand(
        authoring_contract_version="0.1.0",
        command_id=command_id,
        draft_id="draft.synthetic.program",
        base_revision=revision,
        intent=ConnectPortsIntent(
            intent_kind="connect_ports",
            source=PortEndpoint(node_id="node.engine.filter", port_id="program_out"),
            target=PortEndpoint(node_id="node.final.program", port_id="program"),
        ),
    )


def test_connect_commits_new_revision_with_matching_validation(
    workflow_compiler: WorkflowCompiler,
    initial_workflow_spec: WorkflowSpec,
) -> None:
    service, initial = make_service(workflow_compiler, initial_workflow_spec)

    response = service.apply(connect_command(command_id="command.connect.final"))

    assert isinstance(response, WorkflowDraftSnapshot)
    assert response.spec_revision == initial.spec_revision + 1
    assert response.validation.result.outcome is ValidationOutcome.AUTHORING_VALID
    assert response.validation.result.spec_digest == response.spec.sha256_digest()
    assert response.spec.edges[-1].edge_id.startswith("edge.")


def test_same_command_and_payload_is_idempotent_before_stale_check(
    workflow_compiler: WorkflowCompiler,
    initial_workflow_spec: WorkflowSpec,
) -> None:
    service, _initial = make_service(workflow_compiler, initial_workflow_spec)
    command = connect_command(command_id="command.idempotent")

    first = service.apply(command)
    second = service.apply(command)

    assert isinstance(first, WorkflowDraftSnapshot)
    assert second is first
    assert service.get_snapshot(command.draft_id).spec_revision == 1


def test_same_command_id_with_different_payload_fails_closed(
    workflow_compiler: WorkflowCompiler,
    initial_workflow_spec: WorkflowSpec,
) -> None:
    service, _initial = make_service(workflow_compiler, initial_workflow_spec)
    first = connect_command(command_id="command.payload.conflict")
    assert isinstance(service.apply(first), WorkflowDraftSnapshot)
    conflict = first.model_copy(
        update={
            "base_revision": 1,
            "intent": DisconnectPortsIntent(
                intent_kind="disconnect_ports",
                edge_id="edge.source.filter",
            ),
        }
    )

    response = service.apply(conflict)

    assert isinstance(response, AuthoringCommandRejected)
    assert response.diagnostics[0].stable_code == "E_COMMAND_ID_PAYLOAD_CONFLICT"
    assert service.get_snapshot(first.draft_id).spec_revision == 1


def test_new_stale_command_is_rejected_without_mutation(
    workflow_compiler: WorkflowCompiler,
    initial_workflow_spec: WorkflowSpec,
) -> None:
    service, _initial = make_service(workflow_compiler, initial_workflow_spec)
    assert isinstance(
        service.apply(connect_command(command_id="command.first")), WorkflowDraftSnapshot
    )

    response = service.apply(connect_command(command_id="command.stale", revision=0))

    assert isinstance(response, AuthoringCommandRejected)
    assert response.current_revision == 1
    assert response.diagnostics[0].stable_code == "E_DRAFT_REVISION_STALE"
    assert response.diagnostics[0].suggested_action is not None
    assert service.get_snapshot("draft.synthetic.program").spec_revision == 1


def test_schema_invalid_parameters_form_a_new_invalid_draft_revision(
    workflow_compiler: WorkflowCompiler,
    valid_workflow_spec: WorkflowSpec,
) -> None:
    service, _initial = make_service(workflow_compiler, valid_workflow_spec)
    command = AuthoringCommand(
        authoring_contract_version="0.1.0",
        command_id="command.parameters.invalid",
        draft_id="draft.synthetic.program",
        base_revision=0,
        intent=ReplaceParametersIntent(
            intent_kind="replace_parameters",
            node_id="node.engine.filter",
            parameters={"strength": 99},
        ),
    )

    response = service.apply(command)

    assert isinstance(response, WorkflowDraftSnapshot)
    assert response.spec_revision == 1
    assert response.validation.result.outcome is ValidationOutcome.INVALID
    assert any(
        item.stable_code == "E_ENGINE_PARAMETERS_INVALID"
        for item in response.validation.result.diagnostics
    )


def test_disconnect_is_a_typed_atomic_edit(
    workflow_compiler: WorkflowCompiler,
    valid_workflow_spec: WorkflowSpec,
) -> None:
    service, _initial = make_service(workflow_compiler, valid_workflow_spec)
    command = AuthoringCommand(
        authoring_contract_version="0.1.0",
        command_id="command.disconnect.final",
        draft_id="draft.synthetic.program",
        base_revision=0,
        intent=DisconnectPortsIntent(
            intent_kind="disconnect_ports",
            edge_id="edge.filter.final",
        ),
    )

    response = service.apply(command)

    assert isinstance(response, WorkflowDraftSnapshot)
    assert response.spec_revision == 1
    assert response.validation.result.outcome is ValidationOutcome.INVALID


def test_unknown_edge_rejection_keeps_revision(
    workflow_compiler: WorkflowCompiler,
    valid_workflow_spec: WorkflowSpec,
) -> None:
    service, _initial = make_service(workflow_compiler, valid_workflow_spec)
    command = AuthoringCommand(
        authoring_contract_version="0.1.0",
        command_id="command.disconnect.unknown",
        draft_id="draft.synthetic.program",
        base_revision=0,
        intent=DisconnectPortsIntent(
            intent_kind="disconnect_ports",
            edge_id="edge.unknown",
        ),
    )

    response = service.apply(command)

    assert isinstance(response, AuthoringCommandRejected)
    assert response.diagnostics[0].stable_code == "E_EDGE_UNKNOWN"
    assert service.get_snapshot(command.draft_id).spec_revision == 0


def test_wire_facade_rejects_duplicate_keys_without_fabricating_snapshot(
    workflow_compiler: WorkflowCompiler,
    initial_workflow_spec: WorkflowSpec,
) -> None:
    service, _initial = make_service(workflow_compiler, initial_workflow_spec)
    facade = AuthoringWireFacade(service)
    payload = json.dumps(connect_command(command_id="command.wire").to_data())
    duplicate = payload.replace(
        '"command_id": "command.wire"',
        '"command_id": "command.wire", "command_id": "command.other"',
    )

    response = facade.apply_json(duplicate, correlation_id="request.synthetic")

    assert isinstance(response, WireParseFailure)
    assert response.diagnostics[0].phase.value == "parse"
    assert service.get_snapshot("draft.synthetic.program").spec_revision == 0
