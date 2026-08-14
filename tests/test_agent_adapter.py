"""验证 Phase 4 Agent adapter 只经 Application Service 操作 fresh Runtime authority。"""

from __future__ import annotations

import hashlib
from typing import cast

import pytest

from zniku.agent import AgentAdapter, AgentToolName
from zniku.application import ApplicationService
from zniku.contracts import (
    Artifact,
    ArtifactType,
    ContractViolation,
    CoverageSpan,
    CoverageUnit,
    MediaKind,
    Scope,
)
from zniku.contracts.base import JsonObject
from zniku.pipelines import ExternalOutputSubmission, ManualHandoff
from zniku.workflow.execution import ChapterMemberBinding, ChapterPlan


def _source() -> Artifact:
    return Artifact(
        artifact_id="artifact.agent.source",
        artifact_type=ArtifactType.MEDIA,
        media_kind=MediaKind.PROGRAM_MEDIA,
        scope=Scope.PROGRAM,
        scope_id="program.agent",
        attributes={
            "duration_frames": 200,
            "audio_stream_ids": ["audio.main", "audio.commentary"],
        },
    )


def _chapters() -> ChapterPlan:
    return ChapterPlan(
        chapter_plan_id="chapter_plan.agent",
        members=(
            ChapterMemberBinding(
                member_id="chapter.001",
                scope_id="scope.chapter.001",
                coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=100),
            ),
            ChapterMemberBinding(
                member_id="chapter.002",
                scope_id="scope.chapter.002",
                coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=100, end=200),
            ),
        ),
        coverage=CoverageSpan(unit=CoverageUnit.FRAME, start=0, end=200),
    )


def _start(application: ApplicationService, agent: AgentAdapter) -> str:
    compile_response = agent.compile_workflow(_source(), _chapters())
    assert compile_response.tool is AgentToolName.COMPILE_WORKFLOW
    agent.start_run(
        command_id="command.start.agent",
        preparation_id=compile_response.authority_id,
        workflow_run_id="workflow_run.agent",
    )
    return compile_response.authority_id


def _finish(application: ApplicationService, agent: AgentAdapter) -> None:
    command_number = 0
    while application.list_ready_nodes("workflow_run.agent"):
        for node_id in application.list_ready_nodes("workflow_run.agent"):
            command_number += 1
            if ".enhancement." in node_id or ".frame_interpolation." in node_id:
                response = agent.prepare_node(
                    command_id=f"command.handoff.{command_number}",
                    workflow_run_id="workflow_run.agent",
                    plan_node_id=node_id,
                )
                response_data = response.to_data()
                handoff = ManualHandoff.from_data(cast(JsonObject, response_data["payload"]))
                submission = ExternalOutputSubmission(
                    handoff_id=handoff.handoff_id,
                    candidate_artifact_id=f"artifact.agent.external.{node_id}",
                    candidate_digest=("sha256:" + hashlib.sha256(node_id.encode()).hexdigest()),
                    source_authority_digest=_source().sha256_digest(),
                    frame_count=handoff.expected_output_frames,
                    completed_leaf_ids=handoff.leaf_ids,
                )
                agent.submit_external_output(
                    command_id=f"command.submit.{command_number}",
                    workflow_run_id="workflow_run.agent",
                    submission=submission,
                )
            else:
                agent.execute_node(
                    command_id=f"command.execute.{command_number}",
                    workflow_run_id="workflow_run.agent",
                    plan_node_id=node_id,
                )


def test_agent_reuses_unique_compiler_and_cannot_force_final() -> None:
    application = ApplicationService()
    agent = AgentAdapter(application)

    validation = agent.validate_workflow()
    assert validation.tool is AgentToolName.VALIDATE_WORKFLOW
    assert validation.authority_id == "workflow.default.0.1.0"
    _start(application, agent)
    with pytest.raises(ContractViolation, match="E_FINAL_NOT_VERIFIED") as failure:
        agent.publish_final("workflow_run.agent")
    explanation = agent.explain_error(failure.value)
    assert explanation.stable_code == "E_FINAL_NOT_VERIFIED"
    assert "不能强制发布" in explanation.allowed_action


def test_agent_session_restart_continues_same_runtime_authority() -> None:
    application = ApplicationService()
    first_agent = AgentAdapter(application)
    _start(application, first_agent)
    first_agent.execute_node(
        command_id="command.execute.source",
        workflow_run_id="workflow_run.agent",
        plan_node_id="plan.node.source",
    )
    before = application.inspect_run("workflow_run.agent")

    second_agent = AgentAdapter(application)
    inspected = second_agent.inspect_run("workflow_run.agent")
    assert inspected.authority_digest == before.sha256_digest()
    _finish(application, second_agent)
    final = second_agent.publish_final("workflow_run.agent")

    assert final.tool is AgentToolName.PUBLISH_FINAL
    assert application.inspect_run("workflow_run.agent").full_verification is not None


def test_write_commands_are_idempotent_and_conflicts_fail_closed() -> None:
    application = ApplicationService()
    agent = AgentAdapter(application)
    preparation_id = _start(application, agent)
    first = agent.start_run(
        command_id="command.start.agent",
        preparation_id=preparation_id,
        workflow_run_id="workflow_run.agent",
    )
    replay = agent.start_run(
        command_id="command.start.agent",
        preparation_id=preparation_id,
        workflow_run_id="workflow_run.agent",
    )
    assert replay == first

    with pytest.raises(ContractViolation, match="E_COMMAND_IDEMPOTENCY_CONFLICT"):
        application.start_run(
            command_id="command.start.agent",
            preparation_id=preparation_id,
            workflow_run_id="workflow_run.other",
        )


def test_ready_set_and_handoff_are_backend_authority() -> None:
    application = ApplicationService()
    agent = AgentAdapter(application)
    _start(application, agent)
    ready = agent.list_ready_nodes("workflow_run.agent")
    assert ready.tool is AgentToolName.LIST_READY_NODES
    ready_data = cast(JsonObject, ready.to_data()["payload"])
    assert ready_data["ready_node_ids"] == ["plan.node.source"]

    with pytest.raises(ContractViolation, match="E_HANDOFF_ENGINE_MODE") as failure:
        agent.prepare_node(
            command_id="command.invalid.handoff",
            workflow_run_id="workflow_run.agent",
            plan_node_id="plan.node.source",
        )
    assert agent.explain_error(failure.value).allowed_action.startswith("调用 inspect_run")


def test_agent_tool_surface_has_no_generic_shell_or_engine_entrypoint() -> None:
    assert {item.value for item in AgentToolName} == {
        "validate_workflow",
        "compile_workflow",
        "start_run",
        "inspect_run",
        "list_ready_nodes",
        "prepare_node",
        "submit_external_output",
        "execute_node",
        "retry_node",
        "publish_final",
    }
    assert not hasattr(AgentAdapter, "run_command")
    assert not hasattr(AgentAdapter, "invoke_engine")
