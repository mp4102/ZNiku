"""把 Application Service 投影为受控、可解释的 Agent 工具面。

Adapter 不缓存完成状态，不接收任意命令字符串，不直接调用 Engine。更换 Agent 会话只需重新绑定同一个
Application Service，即可从 fresh Runtime snapshot 继续。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, cast

from pydantic import JsonValue, field_serializer, field_validator

from zniku.application import ApplicationService
from zniku.contracts import Artifact, ContractModel, ContractViolation, JsonObject, StableId
from zniku.contracts.base import freeze_json_object, thaw_json
from zniku.pipelines import ExternalOutputSubmission
from zniku.workflow.execution import ChapterPlan

AGENT_TOOL_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"


class AgentToolName(StrEnum):
    VALIDATE_WORKFLOW = "validate_workflow"
    COMPILE_WORKFLOW = "compile_workflow"
    START_RUN = "start_run"
    INSPECT_RUN = "inspect_run"
    LIST_READY_NODES = "list_ready_nodes"
    PREPARE_NODE = "prepare_node"
    SUBMIT_EXTERNAL_OUTPUT = "submit_external_output"
    EXECUTE_NODE = "execute_node"
    RETRY_NODE = "retry_node"
    PUBLISH_FINAL = "publish_final"


class AgentToolResponse(ContractModel):
    """Agent 工具的稳定数据响应；authority identity 始终来自后端。"""

    agent_tool_contract_version: Literal["0.1.0"]
    tool: AgentToolName
    authority_id: StableId
    authority_digest: str
    payload: JsonObject

    @field_validator("payload")
    @classmethod
    def freeze_payload(cls, value: JsonObject) -> JsonObject:
        return freeze_json_object(value)

    @field_serializer("payload")
    def serialize_payload(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)


class AgentErrorExplanation(ContractModel):
    """只解释稳定错误与允许动作，不修改状态或降低 gate。"""

    agent_tool_contract_version: Literal["0.1.0"]
    stable_code: StableId
    summary: str
    allowed_action: str


class AgentAdapter:
    """无状态的 Agent-facing facade；所有 authority 查询与写入转交 Application Service。"""

    def __init__(self, application: ApplicationService) -> None:
        self._application = application

    def validate_workflow(self) -> AgentToolResponse:
        result = self._application.validate_workflow(self._application.bundle.spec)
        return self._response(
            AgentToolName.VALIDATE_WORKFLOW,
            self._application.bundle.spec.workflow_id,
            result,
        )

    def compile_workflow(self, source: Artifact, chapter_plan: ChapterPlan) -> AgentToolResponse:
        prepared = self._application.prepare_default(source, chapter_plan)
        return self._response(AgentToolName.COMPILE_WORKFLOW, prepared.preparation_id, prepared)

    def start_run(
        self,
        *,
        command_id: str,
        preparation_id: str,
        workflow_run_id: str,
    ) -> AgentToolResponse:
        snapshot = self._application.start_run(
            command_id=command_id,
            preparation_id=preparation_id,
            workflow_run_id=workflow_run_id,
        )
        return self._response(AgentToolName.START_RUN, workflow_run_id, snapshot)

    def inspect_run(self, workflow_run_id: str) -> AgentToolResponse:
        snapshot = self._application.inspect_run(workflow_run_id)
        return self._response(AgentToolName.INSPECT_RUN, workflow_run_id, snapshot)

    def list_ready_nodes(self, workflow_run_id: str) -> AgentToolResponse:
        summary = self._application.summarize_run(workflow_run_id)
        return self._response(AgentToolName.LIST_READY_NODES, workflow_run_id, summary)

    def execute_node(
        self, *, command_id: str, workflow_run_id: str, plan_node_id: str
    ) -> AgentToolResponse:
        snapshot = self._application.execute_node(
            command_id=command_id,
            workflow_run_id=workflow_run_id,
            plan_node_id=plan_node_id,
        )
        return self._response(AgentToolName.EXECUTE_NODE, workflow_run_id, snapshot)

    def prepare_node(
        self, *, command_id: str, workflow_run_id: str, plan_node_id: str
    ) -> AgentToolResponse:
        handoff = self._application.prepare_node(
            command_id=command_id,
            workflow_run_id=workflow_run_id,
            plan_node_id=plan_node_id,
        )
        return self._response(AgentToolName.PREPARE_NODE, handoff.handoff_id, handoff)

    def submit_external_output(
        self,
        *,
        command_id: str,
        workflow_run_id: str,
        submission: ExternalOutputSubmission,
    ) -> AgentToolResponse:
        snapshot = self._application.submit_external_output(
            command_id=command_id,
            workflow_run_id=workflow_run_id,
            submission=submission,
        )
        return self._response(AgentToolName.SUBMIT_EXTERNAL_OUTPUT, workflow_run_id, snapshot)

    def retry_node(
        self, *, command_id: str, workflow_run_id: str, plan_node_id: str
    ) -> AgentToolResponse:
        snapshot = self._application.retry_node(
            command_id=command_id,
            workflow_run_id=workflow_run_id,
            plan_node_id=plan_node_id,
        )
        return self._response(AgentToolName.RETRY_NODE, workflow_run_id, snapshot)

    def publish_final(self, workflow_run_id: str) -> AgentToolResponse:
        verification = self._application.publish_final(workflow_run_id)
        return self._response(
            AgentToolName.PUBLISH_FINAL, verification.final_artifact_id, verification
        )

    def explain_error(self, error: ContractViolation) -> AgentErrorExplanation:
        actions = {
            "E_MANUAL_HANDOFF_REQUIRED": "调用 prepare_node，并提交完整外部输出供验收。",
            "E_HANDOFF_NODE_NOT_READY": "先调用 list_ready_nodes，等待依赖完成。",
            "E_FULL_VERIFY_FRAME_COUNT": "检查候选输出帧数后重新提交，不得跳过 full verification。",
            "E_SOURCE_AUTHORITY_DRIFT": "停止提交并重新检查 source authority。",
            "E_FINAL_NOT_VERIFIED": "继续执行 Runtime ready set，不能强制发布 Final。",
            "E_COMMAND_IDEMPOTENCY_CONFLICT": "为不同 payload 使用新的 command_id。",
        }
        return AgentErrorExplanation(
            agent_tool_contract_version=AGENT_TOOL_CONTRACT_VERSION,
            stable_code=error.code,
            summary=error.message,
            allowed_action=actions.get(
                error.code, "调用 inspect_run 获取 fresh authority 后再决定。"
            ),
        )

    @staticmethod
    def _response(
        tool: AgentToolName, authority_id: str, value: ContractModel
    ) -> AgentToolResponse:
        return AgentToolResponse(
            agent_tool_contract_version=AGENT_TOOL_CONTRACT_VERSION,
            tool=tool,
            authority_id=authority_id,
            authority_digest=value.sha256_digest(),
            payload=cast(JsonObject, value.to_data()),
        )
