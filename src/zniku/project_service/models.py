"""定义 ZNIKU Studio 与本地 Project Service 之间的 0.3.0 wire 合同。

Project authoring 保存使用 schema 3 和 CAS；本模块定义浏览器与 Project Service 的成对 wire。
Run summary、
定向日志和 handoff readiness 都是已有 SQLite authority 的只读投影，不引入第二套运行状态。所有
请求和响应拒绝未知字段、隐式类型转换及非有限数值，未知客户端默认失败关闭。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from zniku.avenhance_v27.preflight import Av27ProfilePreflightResult
from zniku.avenhance_v27.template import (
    AV27_PROFILE_VERSION,
    ExpandRequest,
    PrepareRequest,
    TemplatePhase,
    TemplatePlanSummary,
    TemplatePreviewRequest,
)
from zniku.graph import NodeDefinition
from zniku.graph.models import Identifier
from zniku.presentation import PresentationCatalog, PresentationDiagnostic
from zniku.project import AuthoringDiagnostic, Project, ProjectSnapshot, StudioState, StudioWarning
from zniku.project.studio import StorageRevision
from zniku.runtime import Artifact, LatestNodeResult, Run, RuntimeFailure
from zniku.runtime.models import RandomId, UtcTimestamp

PROJECT_SERVICE_CONTRACT_VERSION: Literal["0.3.0"] = "0.3.0"
type ActiveProjectOperation = Literal[
    "run_all",
    "run_to",
    "rerun_from_here",
    "submit_external",
    "abandon_run",
]
type RunTargetMode = Literal["all", "selected"]
type RunSummaryState = Literal["pending", "running", "completed", "failed"]
type ReadinessState = Literal[
    "missing",
    "empty",
    "present",
    "probe_passed",
    "probe_failed",
]
type ProgressUnit = Literal["frames", "bytes", "microseconds", "items"]

LocalPath = Annotated[str, StringConstraints(min_length=1, max_length=32767)]
OpaqueCursor = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
ProjectSessionId = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
]


class ProjectServiceModel(BaseModel):
    """Project Service wire 值对象的严格基类。"""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


class ProjectServiceFailure(ProjectServiceModel):
    """向 Studio 暴露稳定错误码、受限说明及相关 Run identity。"""

    code: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    message: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    related_run_ids: tuple[str, ...] = ()


class RunNodeStateCounts(ProjectServiceModel):
    """统计一个 Run 执行闭包中每个节点最高 attempt 的持久状态。"""

    pending: Annotated[int, Field(ge=0)] = 0
    running: Annotated[int, Field(ge=0)] = 0
    waiting_external: Annotated[int, Field(ge=0)] = 0
    completed: Annotated[int, Field(ge=0)] = 0
    failed: Annotated[int, Field(ge=0)] = 0

    @property
    def total(self) -> int:
        """返回五种持久状态的节点总数；该辅助值不进入 wire。"""

        return self.pending + self.running + self.waiting_external + self.completed + self.failed


class RunSummary(ProjectServiceModel):
    """提供有界 Run selector 和全局 Next action 所需的轻量投影。"""

    run_id: str
    project_id: str
    target_mode: RunTargetMode
    selected_targets: tuple[str, ...] = ()
    state: RunSummaryState
    node_count: Annotated[int, Field(ge=0)]
    state_counts: RunNodeStateCounts
    actionable: bool
    requires_operator_action: bool
    created_at: UtcTimestamp
    started_at: UtcTimestamp | None = None
    ended_at: UtcTimestamp | None = None
    latest_activity_at: UtcTimestamp
    error: RuntimeFailure | None = None

    @model_validator(mode="after")
    def validate_derived_fields(self) -> RunSummary:
        """拒绝计数、目标模式或派生布尔值互相矛盾的 summary。"""

        if self.node_count != self.state_counts.total:
            raise ValueError("E_RUN_SUMMARY_COUNT: node_count 必须等于状态计数总和")
        if (self.target_mode == "all") != (not self.selected_targets):
            raise ValueError("E_RUN_SUMMARY_TARGET: target_mode 与 selected_targets 不一致")
        if self.actionable != (self.state in {"pending", "running"}):
            raise ValueError("E_RUN_SUMMARY_ACTIONABLE: actionable 与 Run state 不一致")
        operator_expected = (
            self.state == "failed"
            or self.state_counts.waiting_external > 0
            or self.state_counts.failed > 0
        )
        if self.requires_operator_action != operator_expected:
            raise ValueError("E_RUN_SUMMARY_OPERATOR_ACTION: requires_operator_action 与状态不一致")
        if self.latest_activity_at < self.created_at:
            raise ValueError("E_RUN_SUMMARY_ACTIVITY: latest_activity_at 不得早于 created_at")
        return self


class NodeProgressProjection(ProjectServiceModel):
    """描述当前进程仍持有的细粒度 automatic attempt 进度。"""

    node_run_id: str
    fraction: Annotated[float, Field(ge=0.0, le=1.0)]
    current: Annotated[int, Field(ge=0)] | None = None
    total: Annotated[int, Field(gt=0)] | None = None
    unit: ProgressUnit | None = None
    observed_at: UtcTimestamp

    @model_validator(mode="after")
    def validate_measurement(self) -> NodeProgressProjection:
        """current/total/unit 必须完整出现，并与 fraction 精确到冻结容差。"""

        values = (self.current, self.total, self.unit)
        if any(value is None for value in values):
            if any(value is not None for value in values):
                raise ValueError("E_PROGRESS_MEASUREMENT_PARTIAL: current/total/unit 必须同时出现")
            return self
        assert self.current is not None and self.total is not None
        if self.current > self.total:
            raise ValueError("E_PROGRESS_CURRENT_RANGE: current 不得大于 total")
        if abs(self.fraction - self.current / self.total) > 1e-9:
            raise ValueError("E_PROGRESS_FRACTION_MISMATCH: fraction 与 current/total 不一致")
        return self


class NodeLogProjection(ProjectServiceModel):
    """携带一个 attempt 的有界 stdout/stderr 尾部，而不是任意路径读取能力。"""

    node_run_id: str
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_available: bool = False
    stderr_available: bool = False


class ExternalOutputReadiness(ProjectServiceModel):
    """描述 handoff 中一个 server-declared target 的本次只读观测。"""

    port_id: str
    ordinal: Annotated[int, Field(ge=0)] | None = None
    path: LocalPath
    state: ReadinessState
    size: Annotated[int, Field(ge=0)] | None = None
    mtime_ns: Annotated[int, Field(ge=0)] | None = None
    message: Annotated[str, StringConstraints(min_length=1, max_length=4096)] | None = None


class ExternalHandoffReadiness(ProjectServiceModel):
    """绑定一个最新 waiting attempt 的无副作用人工输出预检结果。"""

    contract_version: Literal["0.3.0"] = PROJECT_SERVICE_CONTRACT_VERSION
    run_id: str
    node_run_id: str
    handoff_id: str
    checked_at: UtcTimestamp
    probe_requested: bool
    ready_for_submit: bool
    targets: tuple[ExternalOutputReadiness, ...] = ()

    @model_validator(mode="after")
    def validate_ready_state(self) -> ExternalHandoffReadiness:
        """只有显式 probe 且全部目标通过时才允许声明可提交。"""

        expected = self.probe_requested and all(
            target.state == "probe_passed" for target in self.targets
        )
        if self.ready_for_submit != expected:
            raise ValueError("E_HANDOFF_READINESS_READY: ready_for_submit 与 targets 不一致")
        return self


class StatusEnvelope(ProjectServiceModel):
    """Studio 高频刷新得到的有界 Project/Run summary 读模型。"""

    contract_version: Literal["0.3.0"] = PROJECT_SERVICE_CONTRACT_VERSION
    project_path: LocalPath | None = None
    snapshot: ProjectSnapshot | None = None
    project_session_id: ProjectSessionId | None
    storage_revision: StorageRevision | None
    studio_state: StudioState | None
    authoring_diagnostics: tuple[AuthoringDiagnostic, ...]
    studio_warnings: tuple[StudioWarning, ...]
    run_summaries: tuple[RunSummary, ...] = ()
    next_run_cursor: OpaqueCursor | None = None
    active_run_id: str | None = None
    active_operation: ActiveProjectOperation | None = None
    latest_results: tuple[LatestNodeResult, ...] = ()
    error: ProjectServiceFailure | None = None

    @model_validator(mode="after")
    def validate_active_binding(self) -> StatusEnvelope:
        """后台 operation 存在时必须同时给出其 Run binding。"""

        if self.active_operation is not None and self.active_run_id is None:
            raise ValueError("E_STATUS_ACTIVE_RUN_MISSING: active operation 必须绑定 Run")
        fields = (
            self.project_path,
            self.project_session_id,
            self.storage_revision,
            self.studio_state,
        )
        if any((field is None) != (self.snapshot is None) for field in fields):
            raise ValueError("E_STATUS_PROJECT_BINDING: Project 与 authoring binding 必须完整")
        if self.snapshot is None and (self.authoring_diagnostics or self.studio_warnings):
            raise ValueError("E_STATUS_PROJECT_BINDING: 无工程时不得携带 authoring 诊断")
        return self


# 只兼容 Python import 名称；序列化 root 和字段仍只有 0.3.0 StatusEnvelope。
ProjectServiceEnvelope = StatusEnvelope


class PresentationCatalogEnvelope(ProjectServiceModel):
    """独立返回纯展示 catalog 与被隔离的非阻塞第三方 diagnostic。"""

    contract_version: Literal["0.3.0"] = PROJECT_SERVICE_CONTRACT_VERSION
    catalog: PresentationCatalog
    diagnostics: tuple[PresentationDiagnostic, ...] = ()

    @field_validator("diagnostics", mode="before")
    @classmethod
    def normalize_diagnostics(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class RunSummaryPageEnvelope(ProjectServiceModel):
    """按 opaque cursor 返回 terminal Run 历史的一页 summary。"""

    contract_version: Literal["0.3.0"] = PROJECT_SERVICE_CONTRACT_VERSION
    run_summaries: tuple[RunSummary, ...] = ()
    next_run_cursor: OpaqueCursor | None = None


class HandoffContractField(ProjectServiceModel):
    """保存服务端生成的有限纯文本展示行，不是可执行参数或运行权威。"""

    label: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    value: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class ExternalHandoffContractProjection(ProjectServiceModel):
    """把原 Run snapshot 与已登记 input metadata 投影为只读人工交付说明。"""

    node_run_id: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    handoff_id: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    input_artifact_id: Annotated[str, StringConstraints(min_length=1, max_length=160)] | None
    title: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    fields: Annotated[tuple[HandoffContractField, ...], Field(min_length=1, max_length=32)]


class RunDetailEnvelope(ProjectServiceModel):
    """返回一个明确 Run 的完整历史及其引用 Artifact 闭包。"""

    contract_version: Literal["0.3.0"] = PROJECT_SERVICE_CONTRACT_VERSION
    run: Run
    artifacts: tuple[Artifact, ...] = ()
    progress_samples: tuple[NodeProgressProjection, ...] = ()
    handoff_contracts: tuple[ExternalHandoffContractProjection, ...] = ()

    @model_validator(mode="after")
    def validate_handoff_contract_bindings(self) -> RunDetailEnvelope:
        """展示合同只能绑定本 Run 的唯一最新 waiting attempt 与其原始输入。"""

        node_runs = {item.node_run_id: item for item in self.run.node_runs}
        latest_attempts: dict[str, int] = {}
        for item in self.run.node_runs:
            latest_attempts[item.node_id] = max(latest_attempts.get(item.node_id, 0), item.attempt)
        seen: set[str] = set()
        artifact_ids = {item.artifact_id for item in self.artifacts}
        for contract in self.handoff_contracts:
            node_run = node_runs.get(contract.node_run_id)
            if contract.node_run_id in seen or node_run is None:
                raise ValueError("E_HANDOFF_CONTRACT_BINDING: NodeRun 缺失或重复")
            seen.add(contract.node_run_id)
            if (
                node_run.state.value != "waiting_external"
                or node_run.external_handoff is None
                or node_run.external_handoff.handoff_id != contract.handoff_id
                or latest_attempts[node_run.node_id] != node_run.attempt
            ):
                raise ValueError("E_HANDOFF_CONTRACT_BINDING: 不是唯一最新 waiting handoff")
            if contract.input_artifact_id is not None and (
                contract.input_artifact_id not in artifact_ids
                or contract.input_artifact_id not in node_run.input_artifact_ids
                or contract.input_artifact_id not in node_run.external_handoff.input_artifact_ids
            ):
                raise ValueError("E_HANDOFF_CONTRACT_BINDING: input Artifact 不属于 handoff")
        return self


class NodeLogEnvelope(ProjectServiceModel):
    """将日志 tail 精确绑定到请求的 Run 与 NodeRun。"""

    contract_version: Literal["0.3.0"] = PROJECT_SERVICE_CONTRACT_VERSION
    run_id: str
    log: NodeLogProjection


class RerunPreviewEnvelope(ProjectServiceModel):
    """对精确工程会话、存储计数和所选 Run/节点的只读重跑影响；不是执行计划。"""

    contract_version: Literal["0.3.0"] = PROJECT_SERVICE_CONTRACT_VERSION
    project_session_id: ProjectSessionId
    storage_revision: StorageRevision
    run_id: RandomId
    node_id: Identifier
    mode: Literal["same_run", "new_run"]
    rerun_node_ids: tuple[Identifier, ...]
    reusable_node_ids: tuple[Identifier, ...]
    projected_at: UtcTimestamp

    @model_validator(mode="after")
    def validate_node_sets(self) -> RerunPreviewEnvelope:
        """所选节点必须从头运行；两组节点唯一且互斥，拒绝误导性的双重身份。"""

        rerun, reusable = set(self.rerun_node_ids), set(self.reusable_node_ids)
        if (
            self.node_id not in rerun
            or len(rerun) != len(self.rerun_node_ids)
            or len(reusable) != len(self.reusable_node_ids)
            or rerun & reusable
            or any(not identity for identity in rerun | reusable)
        ):
            raise ValueError("E_RERUN_PREVIEW_NODE_SETS: 节点集合必须唯一、互斥且包含所选重跑节点")
        return self


class CreatorAudioTrackSummary(ProjectServiceModel):
    """提供不含 extradata hash 或 Artifact identity 的人类音轨说明。"""

    ordinal: Annotated[int, Field(ge=0)]
    codec: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    channels: Annotated[int, Field(gt=0)] | None = None
    sample_rate: Annotated[int, Field(gt=0)] | None = None
    language: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    label: Annotated[str, StringConstraints(min_length=1, max_length=320)]


class CreatorSourceMediaSummary(ProjectServiceModel):
    """提供分析 Run 已验收媒体事实的创作者安全展示文本。"""

    source_ordinal: Annotated[int, Field(ge=0)]
    chapter_label: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    display_name: Annotated[
        str,
        StringConstraints(min_length=1, max_length=255, pattern=r"^[^/\\:]+$"),
    ]
    size_bytes: Annotated[int, Field(gt=0)]
    size_label: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    container: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    video_codec: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    pixel_format: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    resolution: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    frame_rate: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    duration: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    frame_count: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    audio_tracks: tuple[CreatorAudioTrackSummary, ...] = ()

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        """只允许 basename，避免普通创作者投影泄露目录、drive 或 ADS。"""

        if value.strip() != value or value in {".", ".."} or "\x00" in value:
            raise ValueError("E_CREATOR_MEDIA_NAME: display_name 必须是安全 basename")
        return value

    @field_validator("audio_tracks", mode="before")
    @classmethod
    def normalize_audio_tracks(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class CreatorTemplateSummary(ProjectServiceModel):
    """汇总模板 preview 中普通用户需要的媒体与预计步骤信息。"""

    analyzed: bool
    sources: tuple[CreatorSourceMediaSummary, ...] = ()
    estimated_step_count: Annotated[int, Field(ge=0)]
    estimated_steps: Annotated[str, StringConstraints(min_length=1, max_length=80)]

    @field_validator("sources", mode="before")
    @classmethod
    def normalize_sources(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_analyzed_sources(self) -> CreatorTemplateSummary:
        if self.analyzed != bool(self.sources):
            raise ValueError("E_CREATOR_MEDIA_ANALYZED: analyzed 与 sources 不一致")
        if self.estimated_steps != f"预计 {self.estimated_step_count} 个处理步骤":
            raise ValueError("E_CREATOR_STEP_LABEL: estimated_steps 与数量不一致")
        return self


class TemplatePreviewEnvelope(ProjectServiceModel):
    """返回 Python builder 的完整普通 Graph、定义、计划投影与 profile 诊断。"""

    contract_version: Literal["0.3.0"] = PROJECT_SERVICE_CONTRACT_VERSION
    profile_version: Literal["2.7.0"] = AV27_PROFILE_VERSION
    phase: TemplatePhase
    project: Project
    definitions: tuple[NodeDefinition, ...]
    profile: Av27ProfilePreflightResult
    plan: TemplatePlanSummary
    creator: CreatorTemplateSummary

    @model_validator(mode="after")
    def validate_phase(self) -> TemplatePreviewEnvelope:
        """拒绝 builder phase、preflight phase 与 profile version 互相漂移。"""

        if self.profile.profile_version != self.profile_version:
            raise ValueError("E_AV27_TEMPLATE_PROFILE_VERSION: profile version 不一致")
        if self.profile.phase != self.phase:
            raise ValueError("E_AV27_TEMPLATE_PROFILE_PHASE: profile phase 不一致")
        if self.creator.estimated_step_count != len(self.project.graph.nodes):
            raise ValueError("E_CREATOR_STEP_COUNT: 预计步骤必须等于 preview Graph 节点数")
        if self.phase == "preparation":
            if self.creator.analyzed or self.creator.sources:
                raise ValueError("E_CREATOR_MEDIA_PHASE: preparation preview 尚无分析事实")
        elif (
            not self.creator.analyzed
            or len(self.creator.sources) != self.plan.source_count
            or tuple(item.source_ordinal for item in self.creator.sources)
            != tuple(range(self.plan.source_count))
        ):
            raise ValueError("E_CREATOR_MEDIA_PHASE: expanded preview 缺少完整有序媒体事实")
        return self


class OpenProjectCommand(ProjectServiceModel):
    """打开一个既有且结构合法的 ``.zniku``。"""

    operation: Literal["open_project"]
    path: LocalPath


class CreateProjectCommand(ProjectServiceModel):
    """创建不覆盖既有文件的空 Project；身份由服务端生成，名称可由路径派生。"""

    operation: Literal["create_project"]
    path: LocalPath
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None

    @model_validator(mode="after")
    def reject_explicit_null_name(self) -> CreateProjectCommand:
        """省略 name 表示使用文件名；显式 null 不作为另一种宽松输入。"""

        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("E_PROJECT_SERVICE_PROJECT_NAME: name 不接受 null")
        return self


class CreateAvEnhanceV27Command(ProjectServiceModel):
    """以严格请求原子创建 AVEnhanceFlow v2.7 preparation Project。"""

    operation: Literal["create_av_enhance_v27"]
    request: PrepareRequest


class AuthoringBoundCommand(ProjectServiceModel):
    """把 mutation 绑定到当前打开会话和客户端已观察的存储计数。"""

    project_session_id: ProjectSessionId
    expected_storage_revision: StorageRevision


class ExpandAvEnhanceV27Command(AuthoringBoundCommand):
    """基于明确 preparation Run 原子展开 AVEnhanceFlow v2.7 Graph。"""

    operation: Literal["expand_av_enhance_v27"]
    request: ExpandRequest


class SaveProjectCommand(AuthoringBoundCommand):
    """保存当前 Project 的名称、Graph、参数与 Studio 布局。"""

    operation: Literal["save_project"]
    project: Project
    studio_state: StudioState


class RunAllCommand(AuthoringBoundCommand):
    """从当前 Project 建立普通全图 Run snapshot。"""

    operation: Literal["run_all"]


class RunToCommand(AuthoringBoundCommand):
    """只运行目标节点及其祖先闭包。"""

    operation: Literal["run_to"]
    node_id: str


class RerunFromHereCommand(AuthoringBoundCommand):
    """从指定节点创建全新 attempt；终态 Run 会建立新的普通 Run。"""

    operation: Literal["rerun_from_here"]
    run_id: str
    node_id: str


class SubmitExternalCommand(ProjectServiceModel):
    """提交精确绑定到最新 waiting attempt 的 manual_external 目标文件。"""

    operation: Literal["submit_external"]
    run_id: str
    node_run_id: str
    handoff_id: str


class AbandonRunCommand(ProjectServiceModel):
    """把没有 automatic running attempt 的非终态 Run 原子收敛为 cancelled。"""

    operation: Literal["abandon_run"]
    run_id: str


type ProjectServiceCommand = Annotated[
    OpenProjectCommand
    | CreateProjectCommand
    | CreateAvEnhanceV27Command
    | ExpandAvEnhanceV27Command
    | SaveProjectCommand
    | RunAllCommand
    | RunToCommand
    | RerunFromHereCommand
    | SubmitExternalCommand
    | AbandonRunCommand,
    Field(discriminator="operation"),
]

_COMMAND_ADAPTER: TypeAdapter[ProjectServiceCommand] = TypeAdapter(ProjectServiceCommand)
_TEMPLATE_PREVIEW_ADAPTER: TypeAdapter[TemplatePreviewRequest] = TypeAdapter(TemplatePreviewRequest)


def parse_project_service_command(payload: Any) -> ProjectServiceCommand:
    """严格解析 Project Service command；未知 operation 或字段直接失败。"""

    return _COMMAND_ADAPTER.validate_python(payload, strict=True)


def parse_template_preview_request(payload: Any) -> TemplatePreviewRequest:
    """严格解析无副作用 template preview；不接受客户端 Graph、Artifact 或定义。"""

    return _TEMPLATE_PREVIEW_ADAPTER.validate_python(payload, strict=True)


__all__ = [
    "PROJECT_SERVICE_CONTRACT_VERSION",
    "AbandonRunCommand",
    "ActiveProjectOperation",
    "CreateAvEnhanceV27Command",
    "CreateProjectCommand",
    "CreatorAudioTrackSummary",
    "CreatorSourceMediaSummary",
    "CreatorTemplateSummary",
    "ExpandAvEnhanceV27Command",
    "ExternalHandoffContractProjection",
    "ExternalHandoffReadiness",
    "ExternalOutputReadiness",
    "HandoffContractField",
    "NodeLogEnvelope",
    "NodeLogProjection",
    "NodeProgressProjection",
    "OpenProjectCommand",
    "PresentationCatalogEnvelope",
    "ProjectServiceCommand",
    "ProjectServiceEnvelope",
    "ProjectServiceFailure",
    "RerunFromHereCommand",
    "RunAllCommand",
    "RunDetailEnvelope",
    "RunNodeStateCounts",
    "RunSummary",
    "RunSummaryPageEnvelope",
    "RunToCommand",
    "SaveProjectCommand",
    "StatusEnvelope",
    "SubmitExternalCommand",
    "TemplatePreviewEnvelope",
    "parse_project_service_command",
    "parse_template_preview_request",
]
