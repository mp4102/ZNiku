"""实现同步、原子且幂等的内存 Workflow Authoring Service。

Service 是 Draft revision 与命令接受/拒绝的唯一 authority。候选 WorkflowSpec 只有在 Compiler
同步返回完整结果后才与 validation envelope 一起提交；任何 parse、stale、应用或 Compiler 失败都
保持原 revision。
内存 repository 只验证领域事务，不承诺进程重启后的 durability。
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import rfc8785
from pydantic import ValidationError

from .compiler import WorkflowCompiler
from .models import (
    AUTHORING_CONTRACT_VERSION,
    DIAGNOSTIC_CONTRACT_VERSION,
    WORKFLOW_CONTRACT_VERSION,
    AddEngineStageNodeIntent,
    AddFinalNodeIntent,
    AddSourceNodeIntent,
    AuthoringCommand,
    AuthoringCommandRejected,
    AuthoringResponse,
    CommandEntityRef,
    ConnectPortsIntent,
    DeleteNodeIntent,
    Diagnostic,
    DiagnosticPhase,
    DiagnosticSeverity,
    DisconnectPortsIntent,
    DraftValidationEnvelope,
    EngineStageNodeSpec,
    FinalNodeSpec,
    ReplaceEngineBindingIntent,
    ReplaceParametersIntent,
    SourceNodeSpec,
    SuggestedAction,
    WireParseFailure,
    WorkflowDraftSnapshot,
    WorkflowEdgeSpec,
    WorkflowNodeSpec,
    WorkflowSpec,
)


@dataclass(frozen=True)
class _RecordedCommand:
    payload: bytes
    response: AuthoringResponse


class _ApplyRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _command_occurrence(command_id: str, code: str) -> str:
    digest = hashlib.sha256(f"{command_id}\x1f{code}".encode()).hexdigest()[:24]
    return f"occ.{digest}"


class AuthoringService:
    """用锁保护 Draft、revision 与 command ledger 的最小同步 authority。"""

    def __init__(self, compiler: WorkflowCompiler) -> None:
        self._compiler = compiler
        self._drafts: dict[str, WorkflowDraftSnapshot] = {}
        self._commands: dict[str, _RecordedCommand] = {}
        self._lock = threading.RLock()

    @property
    def compiler(self) -> WorkflowCompiler:
        return self._compiler

    def create_draft(self, draft_id: str, spec: WorkflowSpec) -> WorkflowDraftSnapshot:
        """创建 revision 0，并同步绑定当前 Compiler 结果。"""

        with self._lock:
            if draft_id in self._drafts:
                raise ValueError("E_DRAFT_ALREADY_EXISTS: draft_id 已存在")
            result = self._compiler.validate(spec)
            snapshot = WorkflowDraftSnapshot(
                result_kind="draft_snapshot",
                authoring_contract_version=AUTHORING_CONTRACT_VERSION,
                draft_id=draft_id,
                spec_revision=0,
                spec=spec,
                validation=DraftValidationEnvelope(
                    authoring_contract_version=AUTHORING_CONTRACT_VERSION,
                    draft_id=draft_id,
                    subject_revision=0,
                    result=result,
                ),
            )
            self._drafts[draft_id] = snapshot
            return snapshot

    def get_snapshot(self, draft_id: str) -> WorkflowDraftSnapshot:
        """返回当前不可变 authority snapshot；未知 Draft 明确失败。"""

        with self._lock:
            try:
                return self._drafts[draft_id]
            except KeyError as error:
                raise KeyError(f"E_DRAFT_UNKNOWN: {draft_id}") from error

    def apply(self, command: AuthoringCommand) -> AuthoringResponse:
        """按幂等→stale→候选应用→同步验证顺序原子处理命令。"""

        payload = cast(bytes, rfc8785.dumps(command.idempotency_payload()))
        with self._lock:
            recorded = self._commands.get(command.command_id)
            if recorded is not None:
                if recorded.payload == payload:
                    return recorded.response
                existing_draft = self._drafts.get(command.draft_id)
                return self._reject(
                    command,
                    code="E_COMMAND_ID_PAYLOAD_CONFLICT",
                    message="同一 command_id 已绑定不同 canonical payload。",
                    current_revision=(
                        existing_draft.spec_revision if existing_draft is not None else None
                    ),
                )

            current = self._drafts.get(command.draft_id)
            if current is None:
                rejection = self._reject(
                    command,
                    code="E_DRAFT_UNKNOWN",
                    message="命令引用了未知 Draft。",
                    current_revision=None,
                )
                self._commands[command.command_id] = _RecordedCommand(payload, rejection)
                return rejection
            if command.base_revision != current.spec_revision:
                rejection = self._reject(
                    command,
                    code="E_DRAFT_REVISION_STALE",
                    message="base_revision 不是当前 Draft revision；首版不自动合并。",
                    current_revision=current.spec_revision,
                    refresh=True,
                )
                self._commands[command.command_id] = _RecordedCommand(payload, rejection)
                return rejection

            try:
                candidate = self._apply_intent(current.spec, command)
                result = self._compiler.validate(candidate)
            except (_ApplyRejected, ValidationError, ValueError) as error:
                code = (
                    error.code if isinstance(error, _ApplyRejected) else "E_AUTHORING_APPLY_FAILED"
                )
                rejection = self._reject(
                    command,
                    code=code,
                    message=(
                        error.message
                        if isinstance(error, _ApplyRejected)
                        else "命令无法形成结构合法且可完整验证的 WorkflowSpec。"
                    ),
                    current_revision=current.spec_revision,
                )
                self._commands[command.command_id] = _RecordedCommand(payload, rejection)
                return rejection

            next_revision = current.spec_revision + 1
            snapshot = WorkflowDraftSnapshot(
                result_kind="draft_snapshot",
                authoring_contract_version=AUTHORING_CONTRACT_VERSION,
                draft_id=current.draft_id,
                spec_revision=next_revision,
                spec=candidate,
                validation=DraftValidationEnvelope(
                    authoring_contract_version=AUTHORING_CONTRACT_VERSION,
                    draft_id=current.draft_id,
                    subject_revision=next_revision,
                    result=result,
                ),
            )
            self._drafts[current.draft_id] = snapshot
            self._commands[command.command_id] = _RecordedCommand(payload, snapshot)
            return snapshot

    def _apply_intent(self, spec: WorkflowSpec, command: AuthoringCommand) -> WorkflowSpec:
        nodes: list[WorkflowNodeSpec] = list(spec.nodes)
        edges = list(spec.edges)
        intent = command.intent

        if isinstance(intent, AddSourceNodeIntent):
            node_id = self._allocated_id("node.source", command.command_id)
            self._ensure_node_id_free(nodes, node_id)
            nodes.append(SourceNodeSpec(kind="source", node_id=node_id))
        elif isinstance(intent, AddEngineStageNodeIntent):
            node_id = self._allocated_id("node.engine", command.command_id)
            self._ensure_node_id_free(nodes, node_id)
            nodes.append(
                EngineStageNodeSpec(
                    kind="engine_stage",
                    node_id=node_id,
                    engine=intent.engine,
                    parameters=intent.parameters,
                )
            )
        elif isinstance(intent, AddFinalNodeIntent):
            node_id = self._allocated_id("node.final", command.command_id)
            self._ensure_node_id_free(nodes, node_id)
            nodes.append(FinalNodeSpec(kind="final", node_id=node_id))
        elif isinstance(intent, DeleteNodeIntent):
            if not any(node.node_id == intent.node_id for node in nodes):
                raise _ApplyRejected("E_NODE_UNKNOWN", "无法删除未知 node。")
            nodes = [node for node in nodes if node.node_id != intent.node_id]
            edges = [
                edge
                for edge in edges
                if edge.source.node_id != intent.node_id and edge.target.node_id != intent.node_id
            ]
        elif isinstance(intent, ConnectPortsIntent):
            edge_id = self._allocated_id("edge", command.command_id)
            if any(edge.edge_id == edge_id for edge in edges):
                raise _ApplyRejected("E_EDGE_ID_COLLISION", "Python 分配的 edge ID 已存在。")
            edges.append(
                WorkflowEdgeSpec(
                    edge_id=edge_id,
                    source=intent.source,
                    target=intent.target,
                )
            )
        elif isinstance(intent, DisconnectPortsIntent):
            if not any(edge.edge_id == intent.edge_id for edge in edges):
                raise _ApplyRejected("E_EDGE_UNKNOWN", "无法断开未知 edge。")
            edges = [edge for edge in edges if edge.edge_id != intent.edge_id]
        elif isinstance(intent, ReplaceEngineBindingIntent):
            nodes = self._replace_engine_node(
                nodes,
                intent.node_id,
                lambda node: node.model_copy(update={"engine": intent.engine}),
            )
        elif isinstance(intent, ReplaceParametersIntent):
            nodes = self._replace_engine_node(
                nodes,
                intent.node_id,
                lambda node: node.model_copy(update={"parameters": intent.parameters}),
            )
        else:  # pragma: no cover - 判别联合关闭未知 intent。
            raise _ApplyRejected("E_INTENT_UNSUPPORTED", "未知 authoring intent。")

        return WorkflowSpec(
            workflow_contract_version=WORKFLOW_CONTRACT_VERSION,
            workflow_id=spec.workflow_id,
            nodes=tuple(nodes),
            edges=tuple(edges),
        )

    @staticmethod
    def _allocated_id(prefix: str, command_id: str) -> str:
        digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:20]
        return f"{prefix}.{digest}"

    @staticmethod
    def _ensure_node_id_free(nodes: list[WorkflowNodeSpec], node_id: str) -> None:
        if any(node.node_id == node_id for node in nodes):
            raise _ApplyRejected("E_NODE_ID_COLLISION", "Python 分配的 node ID 已存在。")

    @staticmethod
    def _replace_engine_node(
        nodes: list[WorkflowNodeSpec],
        node_id: str,
        replace: Callable[[EngineStageNodeSpec], EngineStageNodeSpec],
    ) -> list[WorkflowNodeSpec]:
        replaced = False
        result: list[WorkflowNodeSpec] = []
        for node in nodes:
            if node.node_id != node_id:
                result.append(node)
                continue
            if not isinstance(node, EngineStageNodeSpec):
                raise _ApplyRejected("E_NODE_NOT_ENGINE_STAGE", "目标 node 不是 EngineStage。")
            result.append(replace(node))
            replaced = True
        if not replaced:
            raise _ApplyRejected("E_NODE_UNKNOWN", "命令引用了未知 node。")
        return result

    @staticmethod
    def _reject(
        command: AuthoringCommand,
        *,
        code: str,
        message: str,
        current_revision: int | None,
        refresh: bool = False,
    ) -> AuthoringCommandRejected:
        diagnostic = Diagnostic.create(
            stable_code=code,
            occurrence_key=_command_occurrence(command.command_id, code),
            severity=DiagnosticSeverity.ERROR,
            phase=DiagnosticPhase.AUTHORING,
            entity_ref=CommandEntityRef(kind="command", command_id=command.command_id),
            message=message,
            suggested_action=SuggestedAction(action="refresh_authority") if refresh else None,
        )
        return AuthoringCommandRejected(
            result_kind="command_rejected",
            authoring_contract_version=AUTHORING_CONTRACT_VERSION,
            diagnostic_contract_version=DIAGNOSTIC_CONTRACT_VERSION,
            command_id=command.command_id,
            draft_id=command.draft_id,
            base_revision=command.base_revision,
            current_revision=current_revision,
            diagnostics=(diagnostic,),
        )


class AuthoringWireFacade:
    """把 raw command payload 的 parse 失败转换成稳定 wire response。"""

    def __init__(self, service: AuthoringService) -> None:
        self._service = service

    def apply_json(
        self,
        payload: str | bytes | bytearray,
        *,
        correlation_id: str | None = None,
    ) -> AuthoringResponse:
        try:
            command = AuthoringCommand.from_json(payload)
        except (ValidationError, ValueError, TypeError):
            diagnostic = Diagnostic.create(
                stable_code="E_WIRE_COMMAND_INVALID",
                occurrence_key="command.invalid",
                severity=DiagnosticSeverity.ERROR,
                phase=DiagnosticPhase.PARSE,
                entity_ref=CommandEntityRef(kind="command", command_id=None, json_pointer=None),
                message="Payload 无法形成关闭未知字段的 typed AuthoringCommand。",
            )
            return WireParseFailure(
                result_kind="wire_parse_failure",
                authoring_contract_version=AUTHORING_CONTRACT_VERSION,
                diagnostic_contract_version=DIAGNOSTIC_CONTRACT_VERSION,
                correlation_id=correlation_id,
                diagnostics=(diagnostic,),
            )
        return self._service.apply(command)
