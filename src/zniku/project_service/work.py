"""普通工作源的版本化服务值；共享展示数据，不把浅观察填进旧严格合同。"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from zniku.avenhance_v27.template import PublicationRequest
from zniku.chapter_overlap import ChapterLeafPlan
from zniku.chapter_overlap.context import ExperimentalContextPlan
from zniku.graph.models import Identifier
from zniku.prepared_source.template import PreparedSourceProcessing, PreparedSourcePublication
from zniku.project.studio import StorageRevision
from zniku.runtime.models import RandomId

from .models import LocalPath, NodeProgressProjection, ProjectServiceModel, ProjectSessionId
from .prepared_color import (
    ColorPreparedSourceAction,
    ColorPreparedSourceFailure,
    ColorPreparedSourceFinding,
    ColorPreparedSourceHandoff,
    ColorPreparedSourceStageProgress,
    RateText,
)

type WorkRoute = Literal["direct", "builtin", "external"]
type WorkConfirmationId = Literal[
    "color_interpretation", "target_frame_rate", "retime", "external_reference"
]
type WorkInterpretationPolicy = Literal["declared_only", "operator_confirmed_bt709_limited_left"]


class WorkCreateRequest(ProjectServiceModel):
    contract_version: Literal["0.3.4-work.1"]
    project_path: LocalPath
    project_id: Identifier
    project_name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    source_path: LocalPath
    data_parent: LocalPath | None = None


class WorkViewRequest(ProjectServiceModel):
    contract_version: Literal["0.3.4-work.1"]
    project_session_id: ProjectSessionId
    run_id: RandomId


class WorkChooseRequest(WorkViewRequest):
    """确认仅对当前报告/路线有效；服务必须重新核对，不能由勾选覆盖不支持项。"""

    expected_storage_revision: StorageRevision
    route: WorkRoute
    target_frame_rate: RateText | None = None
    external_format: Literal["mp4", "mov", "mkv"] = "mkv"
    interpretation_policy: WorkInterpretationPolicy = "declared_only"
    confirmations: Annotated[list[WorkConfirmationId], Field(max_length=4)] = Field(
        default_factory=list
    )


class WorkConfirmation(ProjectServiceModel):
    id: WorkConfirmationId
    routes: tuple[WorkRoute, ...]
    label: str
    description: str


class WorkImpact(ProjectServiceModel):
    id: Literal["frames", "timing", "color", "audio", "storage", "external_reference"]
    routes: tuple[WorkRoute, ...]
    title: str
    description: str


class WorkSettings(ProjectServiceModel):
    route: Literal["diagnose", "direct", "builtin", "external"]
    target_frame_rate: RateText | None = None
    external_format: Literal["mp4", "mov", "mkv"] = "mkv"
    interpretation_policy: WorkInterpretationPolicy = "declared_only"
    confirmations: tuple[WorkConfirmationId, ...] = ()
    audio_source: Literal["original", "reference", "none"] = "original"


class WorkRetryTarget(ProjectServiceModel):
    """实际失败 attempt 的快照绑定；重试仍调用普通 rerun_from_here，绝不续传。"""

    run_id: RandomId
    node_run_id: RandomId
    node_id: Identifier


class WorkViewEnvelope(ProjectServiceModel):
    contract_version: Literal["0.3.4-work.1"] = "0.3.4-work.1"
    profile_id: Literal["zniku.prepared-work-overlap"] = "zniku.prepared-work-overlap"
    profile_version: Literal["0.3.4-work.1"] = "0.3.4-work.1"
    project_session_id: ProjectSessionId
    storage_revision: StorageRevision
    run_id: RandomId
    route: Literal["diagnose", "direct", "builtin", "external"]
    state: Literal["checking", "needs_choice", "preparing", "waiting_external", "ready", "failed"]
    stage: str
    decision: Literal["direct", "preparation_required", "unsupported"] | None = None
    inspection_scope: Literal["header_only", "frames_eof"] | None = None
    impacts: tuple[WorkImpact, ...] = ()
    required_confirmations: tuple[WorkConfirmation, ...] = ()
    current_settings: WorkSettings
    retry_target: WorkRetryTarget | None = None
    current_node_run_id: RandomId | None = None
    progress: NodeProgressProjection | None = None
    stage_progress: ColorPreparedSourceStageProgress | None = None
    source_name: str
    original_path: LocalPath
    reference_path: LocalPath | None = None
    source_frame_count: Annotated[int, Field(gt=0)] | None = None
    frame_rate: RateText | None = None
    frame_rate_choices: tuple[RateText, ...] = ()
    audio_track_count: Annotated[int, Field(ge=0)] | None = None
    findings: tuple[ColorPreparedSourceFinding, ...] = ()
    available_actions: tuple[ColorPreparedSourceAction, ...] = ()
    diagnosis_status: Literal["pending", "completed", "failed"]
    admission_status: Literal["not_started", "pending", "completed", "failed"]
    interpretation_policy: WorkInterpretationPolicy = "declared_only"
    color_interpretation_available: bool = False
    color_interpretation_required: bool = False
    color_interpretation_reason: str = "等待工作源观察。"
    working_signal_basis: str | None = None
    handoff: ColorPreparedSourceHandoff | None = None
    error: ColorPreparedSourceFailure | None = None


class WorkOperationRequest(WorkViewRequest):
    node_run_id: RandomId


class WorkOperationEnvelope(WorkOperationRequest):
    active: bool
    operation: str | None
    stage_progress: ColorPreparedSourceStageProgress | None
    cancel_requested: bool


class WorkProcessingRequest(ProjectServiceModel):
    contract_version: Literal["0.3.4-work.1"]
    processing: PreparedSourceProcessing


class WorkProcessingEnvelope(ProjectServiceModel):
    contract_version: Literal["0.3.4-work.1"] = "0.3.4-work.1"
    status: Literal["pending_real_acceptance"] = "pending_real_acceptance"
    processing: PreparedSourceProcessing


class WorkFullRequest(WorkProcessingRequest):
    project_session_id: ProjectSessionId
    expected_storage_revision: StorageRevision
    preparation_run_id: RandomId
    publication: PublicationRequest


class WorkFullEnvelope(WorkProcessingEnvelope):
    profile_id: Literal["zniku.prepared-work-overlap"] = "zniku.prepared-work-overlap"
    profile_version: Literal["0.3.4-work.1"] = "0.3.4-work.1"
    project_session_id: ProjectSessionId
    storage_revision: StorageRevision
    preparation_run_id: RandomId
    execution_available: Literal[True] = True
    plan: ChapterLeafPlan
    contexts: ExperimentalContextPlan
    publication: PreparedSourcePublication
    node_count: Annotated[int, Field(gt=0)]
    edge_count: Annotated[int, Field(ge=0)]


class WorkFailureEnvelope(ProjectServiceModel):
    contract_version: Literal["0.3.4-work.1"] = "0.3.4-work.1"
    error: ColorPreparedSourceFailure


class WorkError(RuntimeError):
    """精确版本错误不夹带成功状态，既有旧错误保持原 wire。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        field_path: tuple[str | int, ...] = (),
        related_run_ids: tuple[str, ...] = (),
    ) -> None:
        self.http_status = http_status
        self.envelope = WorkFailureEnvelope(
            error=ColorPreparedSourceFailure(
                code=code,
                message=message[:4096] or "工作源准备失败",
                field_path=field_path,
                related_run_ids=related_run_ids,
            )
        )
        super().__init__(f"{code}: {message}")
