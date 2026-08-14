"""定义 Workflow authoring、Compiler 诊断与 wire envelope 的正式值对象。

本模块只表达结构合法的 WorkflowSpec、Draft revision、typed command 和静态诊断。它不创建
WorkflowRevision、ExecutionPlan、Runtime 状态或媒体副作用。所有模型关闭未知字段、不可变，并复用
Contract Kernel 的 RFC 8785/JCS、稳定 ID 与执行语义键防御。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, cast

import rfc8785
from pydantic import (
    Field,
    JsonValue,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from zniku.contracts import (
    ArtifactType,
    Cardinality,
    ContractModel,
    EngineBinding,
    JsonObject,
    MediaKind,
    PortSpec,
    Scope,
    Sha256Digest,
    StableId,
)
from zniku.contracts.base import ensure_no_executable_keys, freeze_json_object, thaw_json
from zniku.contracts.errors import fail

WORKFLOW_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"
AUTHORING_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"
COMPILER_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"
DIAGNOSTIC_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"
PROJECTION_CONTRACT_VERSION: Literal["0.1.0"] = "0.1.0"

DiagnosticCode = Annotated[
    str,
    StringConstraints(min_length=3, max_length=96, pattern=r"^[A-Z][A-Z0-9_]*$"),
]
JsonPointer = Annotated[
    str,
    StringConstraints(min_length=0, max_length=512, pattern=r"^(?:/[^\r\n]*)?$"),
]


def _normalize_tuple(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(value)
    return value


def _canonical_bytes(value: JsonValue) -> bytes:
    try:
        return cast(bytes, rfc8785.dumps(value))
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError) as error:
        raise ValueError(f"E_JSON_CANONICALIZATION: {error}") from error


def _digest_value(value: JsonValue) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


class SourceNodeSpec(ContractModel):
    """表示一个尚未绑定真实媒体的 program source slot。"""

    kind: Literal["source"]
    node_id: StableId


class EngineStageNodeSpec(ContractModel):
    """保存 Engine-backed Stage 的精确 manifest 绑定和惰性参数。"""

    kind: Literal["engine_stage"]
    node_id: StableId
    engine: EngineBinding
    parameters: JsonObject

    @field_validator("parameters")
    @classmethod
    def freeze_parameters(cls, value: JsonObject) -> JsonObject:
        ensure_no_executable_keys(value)
        return freeze_json_object(value)

    @field_serializer("parameters")
    def serialize_parameters(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)


class FinalNodeSpec(ContractModel):
    """表示唯一终端类别；本阶段只校验其静态 program input。"""

    kind: Literal["final"]
    node_id: StableId


type WorkflowNodeSpec = Annotated[
    SourceNodeSpec | EngineStageNodeSpec | FinalNodeSpec,
    Field(discriminator="kind"),
]


class PortEndpoint(ContractModel):
    """用稳定 node ID 与 port ID 引用一侧端口。"""

    node_id: StableId
    port_id: StableId


class WorkflowEdgeSpec(ContractModel):
    """表示一条 output→input Artifact 数据边。"""

    edge_id: StableId
    source: PortEndpoint
    target: PortEndpoint


class WorkflowSpec(ContractModel):
    """保存与 Studio 布局无关、按稳定 ID 归一化的语义编排图。"""

    workflow_contract_version: Literal["0.1.0"]
    workflow_id: StableId
    nodes: tuple[WorkflowNodeSpec, ...]
    edges: tuple[WorkflowEdgeSpec, ...]

    @field_validator("nodes", "edges", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @model_validator(mode="after")
    def validate_and_sort_graph(self) -> WorkflowSpec:
        node_ids = tuple(node.node_id for node in self.nodes)
        edge_ids = tuple(edge.edge_id for edge in self.edges)
        if len(node_ids) != len(set(node_ids)):
            fail("E_WORKFLOW_NODE_ID_DUPLICATE", "node_id 必须在 WorkflowSpec 内唯一")
        if len(edge_ids) != len(set(edge_ids)):
            fail("E_WORKFLOW_EDGE_ID_DUPLICATE", "edge_id 必须在 WorkflowSpec 内唯一")
        object.__setattr__(self, "nodes", tuple(sorted(self.nodes, key=lambda node: node.node_id)))
        object.__setattr__(self, "edges", tuple(sorted(self.edges, key=lambda edge: edge.edge_id)))
        return self


class CoreNodeContractSet(ContractModel):
    """提供 Source/Final exact PortSpec，并作为 Python 与 Studio 的共同投影 authority。"""

    workflow_contract_version: Literal["0.1.0"]
    source_outputs: tuple[PortSpec, ...]
    final_inputs: tuple[PortSpec, ...]

    @field_validator("source_outputs", "final_inputs", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @model_validator(mode="after")
    def validate_phase_2a_contract(self) -> CoreNodeContractSet:
        expected = PortSpec(
            port_id="program",
            artifact_type=ArtifactType.MEDIA,
            media_kind=MediaKind.PROGRAM_MEDIA,
            scope=Scope.PROGRAM,
            cardinality=Cardinality.ONE,
        )
        if self.source_outputs != (expected,) or self.final_inputs != (expected,):
            fail(
                "E_CORE_NODE_CONTRACT_UNSUPPORTED",
                "0.1.0 Source output 与 Final input 必须精确使用 program PortSpec",
            )
        return self

    @classmethod
    def phase_2a(cls) -> CoreNodeContractSet:
        """构造当前正式 Source/Final program contract。"""

        program = PortSpec(
            port_id="program",
            artifact_type=ArtifactType.MEDIA,
            media_kind=MediaKind.PROGRAM_MEDIA,
            scope=Scope.PROGRAM,
            cardinality=Cardinality.ONE,
        )
        return cls(
            workflow_contract_version=WORKFLOW_CONTRACT_VERSION,
            source_outputs=(program,),
            final_inputs=(program,),
        )


class DiagnosticSeverity(StrEnum):
    """诊断严重级别；只有 error 阻断 authoring-valid。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticPhase(StrEnum):
    """诊断产生的权威边界。"""

    PARSE = "parse"
    AUTHORING = "authoring"
    GRAPH = "graph"
    MANIFEST = "manifest"


class PortDirection(StrEnum):
    """端口引用方向；PortSpec 本身不重复保存 direction。"""

    INPUT = "input"
    OUTPUT = "output"


class DocumentEntityRef(ContractModel):
    """定位尚未形成正式领域对象的 wire document。"""

    kind: Literal["document"]
    json_pointer: JsonPointer | None = None


class CommandEntityRef(ContractModel):
    """定位 authoring command 或其字段。"""

    kind: Literal["command"]
    command_id: StableId | None = None
    json_pointer: JsonPointer | None = None


class WorkflowEntityRef(ContractModel):
    """定位整个 WorkflowSpec。"""

    kind: Literal["workflow"]
    workflow_id: StableId


class NodeEntityRef(ContractModel):
    """定位一个稳定 node。"""

    kind: Literal["node"]
    node_id: StableId


class PortEntityRef(ContractModel):
    """定位 node 的 input 或 output port。"""

    kind: Literal["port"]
    node_id: StableId
    port_id: StableId
    direction: PortDirection


class EdgeEntityRef(ContractModel):
    """定位一条稳定 edge。"""

    kind: Literal["edge"]
    edge_id: StableId


class ParameterEntityRef(ContractModel):
    """定位 EngineStage 参数 JSON Pointer。"""

    kind: Literal["parameter"]
    node_id: StableId
    json_pointer: JsonPointer = ""


type EntityRef = Annotated[
    DocumentEntityRef
    | CommandEntityRef
    | WorkflowEntityRef
    | NodeEntityRef
    | PortEntityRef
    | EdgeEntityRef
    | ParameterEntityRef,
    Field(discriminator="kind"),
]


class SuggestedAction(ContractModel):
    """首版只允许刷新 authority；不得承载任意命令。"""

    action: Literal["refresh_authority"]


class Diagnostic(ContractModel):
    """用稳定 identity、code 和 typed entity reference 表达一个 Python 结论。"""

    diagnostic_id: Sha256Digest
    stable_code: DiagnosticCode
    occurrence_key: StableId
    severity: DiagnosticSeverity
    phase: DiagnosticPhase
    entity_ref: EntityRef
    related_refs: tuple[EntityRef, ...] = ()
    message: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    details: JsonObject = Field(default_factory=dict)
    suggested_action: SuggestedAction | None = None

    @field_validator("related_refs", mode="before")
    @classmethod
    def normalize_related_refs(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @field_validator("details")
    @classmethod
    def freeze_details(cls, value: JsonObject) -> JsonObject:
        ensure_no_executable_keys(value)
        return freeze_json_object(value)

    @field_serializer("details")
    def serialize_details(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)

    @model_validator(mode="after")
    def validate_identity(self) -> Diagnostic:
        sorted_refs = tuple(
            sorted(self.related_refs, key=lambda ref: _canonical_bytes(ref.to_data()))
        )
        object.__setattr__(self, "related_refs", sorted_refs)
        expected = self.derive_id(
            phase=self.phase,
            stable_code=self.stable_code,
            entity_ref=self.entity_ref,
            related_refs=sorted_refs,
            occurrence_key=self.occurrence_key,
        )
        if self.diagnostic_id != expected:
            fail("E_DIAGNOSTIC_ID_MISMATCH", "diagnostic_id 与稳定 identity 字段不一致")
        return self

    @staticmethod
    def derive_id(
        *,
        phase: DiagnosticPhase,
        stable_code: str,
        entity_ref: EntityRef,
        related_refs: tuple[EntityRef, ...],
        occurrence_key: str,
    ) -> str:
        """从不含本地化文案的稳定字段派生 diagnostic identity。"""

        refs = sorted((ref.to_data() for ref in related_refs), key=_canonical_bytes)
        identity = cast(
            JsonValue,
            {
                "diagnostic_contract_version": DIAGNOSTIC_CONTRACT_VERSION,
                "phase": phase.value,
                "stable_code": stable_code,
                "entity_ref": entity_ref.to_data(),
                "related_refs": refs,
                "occurrence_key": occurrence_key,
            },
        )
        return _digest_value(identity)

    @classmethod
    def create(
        cls,
        *,
        stable_code: DiagnosticCode,
        occurrence_key: StableId,
        severity: DiagnosticSeverity,
        phase: DiagnosticPhase,
        entity_ref: EntityRef,
        message: str,
        related_refs: tuple[EntityRef, ...] = (),
        details: Mapping[str, JsonValue] | None = None,
        suggested_action: SuggestedAction | None = None,
    ) -> Diagnostic:
        """构造已经过 canonical related-ref 排序的正式诊断。"""

        sorted_refs = tuple(sorted(related_refs, key=lambda ref: _canonical_bytes(ref.to_data())))
        diagnostic_id = cls.derive_id(
            phase=phase,
            stable_code=stable_code,
            entity_ref=entity_ref,
            related_refs=sorted_refs,
            occurrence_key=occurrence_key,
        )
        return cls(
            diagnostic_id=diagnostic_id,
            stable_code=stable_code,
            occurrence_key=occurrence_key,
            severity=severity,
            phase=phase,
            entity_ref=entity_ref,
            related_refs=sorted_refs,
            message=message,
            details=dict(details or {}),
            suggested_action=suggested_action,
        )


class ValidationOutcome(StrEnum):
    """Compiler 静态 authoring gate 的权威结论。"""

    INVALID = "invalid"
    AUTHORING_VALID = "authoring_valid"


_PHASE_ORDER = {
    DiagnosticPhase.PARSE: 0,
    DiagnosticPhase.AUTHORING: 1,
    DiagnosticPhase.GRAPH: 2,
    DiagnosticPhase.MANIFEST: 3,
}
_SEVERITY_ORDER = {
    DiagnosticSeverity.ERROR: 0,
    DiagnosticSeverity.WARNING: 1,
    DiagnosticSeverity.INFO: 2,
}


class SpecValidationResult(ContractModel):
    """纯 Compiler 结果；不感知 Draft，也不复制 WorkflowSpec。"""

    workflow_contract_version: Literal["0.1.0"]
    compiler_contract_version: Literal["0.1.0"]
    diagnostic_contract_version: Literal["0.1.0"]
    spec_digest: Sha256Digest
    core_node_contract_digest: Sha256Digest
    outcome: ValidationOutcome
    diagnostics: tuple[Diagnostic, ...]

    @field_validator("diagnostics", mode="before")
    @classmethod
    def normalize_diagnostics(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @model_validator(mode="after")
    def validate_outcome_and_sort(self) -> SpecValidationResult:
        ordered = tuple(
            sorted(
                self.diagnostics,
                key=lambda item: (
                    _PHASE_ORDER[item.phase],
                    _SEVERITY_ORDER[item.severity],
                    item.stable_code,
                    item.diagnostic_id,
                ),
            )
        )
        object.__setattr__(self, "diagnostics", ordered)
        has_error = any(item.severity is DiagnosticSeverity.ERROR for item in ordered)
        expected = ValidationOutcome.INVALID if has_error else ValidationOutcome.AUTHORING_VALID
        if self.outcome is not expected:
            fail("E_VALIDATION_OUTCOME_MISMATCH", "outcome 必须由 diagnostics 的 error 集合决定")
        return self


class AddSourceNodeIntent(ContractModel):
    """请求 Python 分配一个 Source node ID。"""

    intent_kind: Literal["add_source_node"]


class AddEngineStageNodeIntent(ContractModel):
    """请求 Python 分配一个 EngineStage node ID。"""

    intent_kind: Literal["add_engine_stage_node"]
    engine: EngineBinding
    parameters: JsonObject

    @field_validator("parameters")
    @classmethod
    def freeze_parameters(cls, value: JsonObject) -> JsonObject:
        ensure_no_executable_keys(value)
        return freeze_json_object(value)

    @field_serializer("parameters")
    def serialize_parameters(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)


class AddFinalNodeIntent(ContractModel):
    """请求 Python 分配一个 Final node ID。"""

    intent_kind: Literal["add_final_node"]


class DeleteNodeIntent(ContractModel):
    """删除 node 及与之相连的 edge。"""

    intent_kind: Literal["delete_node"]
    node_id: StableId


class ConnectPortsIntent(ContractModel):
    """请求 Python 分配 edge ID 并连接两个稳定 endpoint。"""

    intent_kind: Literal["connect_ports"]
    source: PortEndpoint
    target: PortEndpoint


class DisconnectPortsIntent(ContractModel):
    """按稳定 edge ID 断开一条数据边。"""

    intent_kind: Literal["disconnect_ports"]
    edge_id: StableId


class ReplaceEngineBindingIntent(ContractModel):
    """替换 EngineStage 的精确 manifest binding。"""

    intent_kind: Literal["replace_engine_binding"]
    node_id: StableId
    engine: EngineBinding


class ReplaceParametersIntent(ContractModel):
    """原子替换 EngineStage 的完整惰性参数对象。"""

    intent_kind: Literal["replace_parameters"]
    node_id: StableId
    parameters: JsonObject

    @field_validator("parameters")
    @classmethod
    def freeze_parameters(cls, value: JsonObject) -> JsonObject:
        ensure_no_executable_keys(value)
        return freeze_json_object(value)

    @field_serializer("parameters")
    def serialize_parameters(self, value: JsonObject) -> JsonValue:
        return thaw_json(value)


type AuthoringIntent = Annotated[
    AddSourceNodeIntent
    | AddEngineStageNodeIntent
    | AddFinalNodeIntent
    | DeleteNodeIntent
    | ConnectPortsIntent
    | DisconnectPortsIntent
    | ReplaceEngineBindingIntent
    | ReplaceParametersIntent,
    Field(discriminator="intent_kind"),
]


class AuthoringCommand(ContractModel):
    """针对一个精确 Draft revision 的原子 typed edit intent。"""

    authoring_contract_version: Literal["0.1.0"]
    command_id: StableId
    draft_id: StableId
    base_revision: Annotated[int, Field(ge=0)]
    intent: AuthoringIntent

    def idempotency_payload(self) -> dict[str, JsonValue]:
        """返回不含 command_id、但包含并发语义的 canonical payload。"""

        data = self.to_data()
        del data["command_id"]
        return data


class DraftValidationEnvelope(ContractModel):
    """由 Authoring service 将纯 Compiler 结果绑定到 Draft revision。"""

    authoring_contract_version: Literal["0.1.0"]
    draft_id: StableId
    subject_revision: Annotated[int, Field(ge=0)]
    result: SpecValidationResult


class WorkflowDraftSnapshot(ContractModel):
    """Authoring service 返回的唯一 Draft authority snapshot。"""

    result_kind: Literal["draft_snapshot"]
    authoring_contract_version: Literal["0.1.0"]
    draft_id: StableId
    spec_revision: Annotated[int, Field(ge=0)]
    spec: WorkflowSpec
    validation: DraftValidationEnvelope

    @model_validator(mode="after")
    def validate_authority_bindings(self) -> WorkflowDraftSnapshot:
        if self.validation.authoring_contract_version != self.authoring_contract_version:
            fail("E_SNAPSHOT_AUTHORING_VERSION_MISMATCH", "snapshot 与 validation version 不一致")
        if self.validation.draft_id != self.draft_id:
            fail("E_SNAPSHOT_DRAFT_ID_MISMATCH", "snapshot 与 validation draft_id 不一致")
        if self.validation.subject_revision != self.spec_revision:
            fail("E_SNAPSHOT_REVISION_MISMATCH", "snapshot 与 validation revision 不一致")
        if self.validation.result.spec_digest != self.spec.sha256_digest():
            fail("E_SNAPSHOT_SPEC_DIGEST_MISMATCH", "validation 没有绑定当前 WorkflowSpec")
        return self


class WireParseFailure(ContractModel):
    """raw payload 无法形成 typed command 或 WorkflowSpec 时的稳定 wire 结果。"""

    result_kind: Literal["wire_parse_failure"]
    authoring_contract_version: Literal["0.1.0"]
    diagnostic_contract_version: Literal["0.1.0"]
    correlation_id: StableId | None = None
    diagnostics: tuple[Diagnostic, ...]

    @field_validator("diagnostics", mode="before")
    @classmethod
    def normalize_diagnostics(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @model_validator(mode="after")
    def validate_parse_diagnostics(self) -> WireParseFailure:
        if not self.diagnostics or any(
            item.phase is not DiagnosticPhase.PARSE for item in self.diagnostics
        ):
            fail("E_PARSE_FAILURE_DIAGNOSTICS_INVALID", "parse failure 必须只含 parse diagnostics")
        return self


class AuthoringCommandRejected(ContractModel):
    """已解析命令因幂等、revision 或原子应用规则被拒绝。"""

    result_kind: Literal["command_rejected"]
    authoring_contract_version: Literal["0.1.0"]
    diagnostic_contract_version: Literal["0.1.0"]
    command_id: StableId
    draft_id: StableId
    base_revision: Annotated[int, Field(ge=0)]
    current_revision: Annotated[int, Field(ge=0)] | None = None
    diagnostics: tuple[Diagnostic, ...]

    @field_validator("diagnostics", mode="before")
    @classmethod
    def normalize_diagnostics(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @model_validator(mode="after")
    def validate_authoring_diagnostics(self) -> AuthoringCommandRejected:
        if not self.diagnostics or any(
            item.phase is not DiagnosticPhase.AUTHORING for item in self.diagnostics
        ):
            fail(
                "E_COMMAND_REJECTION_DIAGNOSTICS_INVALID",
                "command rejection 必须只含 authoring diagnostics",
            )
        return self


type AuthoringResponse = Annotated[
    WorkflowDraftSnapshot | WireParseFailure | AuthoringCommandRejected,
    Field(discriminator="result_kind"),
]


class ProjectionSourceSchema(ContractModel):
    """绑定一个 Python source Schema 的逻辑名称与 JCS digest。"""

    name: StableId
    schema_digest: Sha256Digest


class ProjectionFile(ContractModel):
    """绑定一个生成文件的仓库相对路径与原始字节 digest。"""

    path: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    file_digest: Sha256Digest


class ProjectionManifest(ContractModel):
    """记录 Python→Studio 投影的版本、source digest 与确定文件清单。"""

    projection_contract_version: Literal["0.1.0"]
    workflow_contract_version: Literal["0.1.0"]
    authoring_contract_version: Literal["0.1.0"]
    compiler_contract_version: Literal["0.1.0"]
    diagnostic_contract_version: Literal["0.1.0"]
    generator_version: Literal["zniku-projection/0.1.0"]
    core_node_contract_digest: Sha256Digest
    source_schemas: tuple[ProjectionSourceSchema, ...]
    files: tuple[ProjectionFile, ...]

    @field_validator("source_schemas", "files", mode="before")
    @classmethod
    def normalize_sequences(cls, value: Any) -> Any:
        return _normalize_tuple(value)

    @model_validator(mode="after")
    def validate_projection_inventory(self) -> ProjectionManifest:
        schema_names = tuple(item.name for item in self.source_schemas)
        file_paths = tuple(item.path for item in self.files)
        if len(schema_names) != len(set(schema_names)):
            fail("E_PROJECTION_SCHEMA_DUPLICATE", "source Schema name 不得重复")
        if len(file_paths) != len(set(file_paths)):
            fail("E_PROJECTION_FILE_DUPLICATE", "projection file path 不得重复")
        object.__setattr__(
            self, "source_schemas", tuple(sorted(self.source_schemas, key=lambda item: item.name))
        )
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda item: item.path)))
        return self
