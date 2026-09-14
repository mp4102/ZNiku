"""定义素材检查/准备及工作参考流程的独立 0.3.4-color.1 wire。

这些值只投影普通 Graph/Run 和直接输入结果，不持久化第二份运行状态。检查报告不是准入 authority；
用户选择、CAS 与具体 Run 始终显式绑定。旧 0.3.0/0.3.3 wire 不变。
"""

from __future__ import annotations

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

PREPARED_COLOR_PREFIX = "/api/studio/templates/prepared-color"
PREPARED_COLOR_ACTIONS = (
    "create",
    "view",
    "choose",
    "cancel",
    "operation-view",
    "operation-cancel",
    "processing-preview",
    "full-preview",
    "expand",
)
PREPARED_COLOR_ROUTES = frozenset(
    f"{PREPARED_COLOR_PREFIX}/{action}" for action in PREPARED_COLOR_ACTIONS
)
RateText = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*/[1-9][0-9]*$", max_length=64)]


class ColorPreparedSourceCreateRequest(ProjectServiceModel):
    """显式创建只含原件入口和诊断的普通工程；不启动处理、不复制原件。"""

    contract_version: Literal["0.3.4-color.1"]
    project_path: LocalPath
    project_id: Identifier
    project_name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    source_path: LocalPath
    data_parent: LocalPath | None = None


class ColorPreparedSourceViewRequest(ProjectServiceModel):
    """只读视图绑定明确工程会话与 Run，轮询不要求固定 storage revision。"""

    contract_version: Literal["0.3.4-color.1"]
    project_session_id: ProjectSessionId
    run_id: RandomId


class ColorPreparedSourceChooseRequest(ColorPreparedSourceViewRequest):
    """显式改变准备前半图；不隐式提交外部文件，不修改活动 snapshot。"""

    expected_storage_revision: StorageRevision
    route: Literal["direct", "builtin", "external"]
    target_frame_rate: RateText | None = None
    external_format: Literal["mp4", "mov", "mkv"] = "mkv"
    interpretation_policy: Literal["declared_only", "operator_confirmed_bt709_limited_left"] = (
        "declared_only"
    )


class ColorPreparedSourceOperationRequest(ColorPreparedSourceViewRequest):
    """绑定实际 attempt 的操作视图/停止请求；不要求图等于向导模板。"""

    node_run_id: RandomId


class ColorPreparedSourceFinding(ProjectServiceModel):
    """向创作者解释一个实际发现，不用本地化字符串决定执行资格。"""

    code: str
    reason: str
    impact: str
    recommendation: str


class ColorPreparedSourceAction(ProjectServiceModel):
    """当前策略能力及空间估计，未晋级策略明确 disabled。"""

    route: Literal["direct", "builtin", "external"]
    label: str
    enabled: bool
    reason: str
    strategy_id: str | None = None
    estimated_additional_bytes: Annotated[int, Field(ge=0)] | None = None


class ColorPreparedSourceHandoff(ProjectServiceModel):
    """只引用当前 waiting attempt，复用统一外部助手。"""

    run_id: RandomId
    node_run_id: RandomId
    node_id: Identifier


class ColorPreparedSourceStageProgress(ProjectServiceModel):
    """当前 attempt 的业务日志实测投影，未知总量不伪造百分比。"""

    stage: str
    current: Annotated[int, Field(ge=0)] | None = None
    total: Annotated[int, Field(gt=0)] | None = None
    unit: Literal["frames", "packets", "bytes", "tracks", "samples"] | None = None
    elapsed_seconds: Annotated[float, Field(ge=0)]
    rate_per_second: Annotated[float, Field(ge=0)] | None = None


class ColorPreparedSourceOperationEnvelope(ColorPreparedSourceOperationRequest):
    """临时 worker 的只读事实，不包含可被误用为工作源准入的路线或媒体字段。"""

    active: bool
    operation: str | None
    stage_progress: ColorPreparedSourceStageProgress | None
    cancel_requested: bool


class ColorPreparedSourceFailure(ProjectServiceModel):
    """保留原始稳定错误与字段位置，不回显整份客户端输入。"""

    code: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    message: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    field_path: tuple[str | int, ...] = ()
    related_run_ids: tuple[RandomId, ...] = ()


class ColorPreparedSourceViewEnvelope(ProjectServiceModel):
    """从当前图、最新结果及指定 Run 派生的有界准备步骤展示。"""

    contract_version: Literal["0.3.4-color.1"] = "0.3.4-color.1"
    profile_id: Literal["zniku.prepared-color-overlap"] = "zniku.prepared-color-overlap"
    profile_version: Literal["0.3.4-color.1"] = "0.3.4-color.1"
    project_session_id: ProjectSessionId
    storage_revision: StorageRevision
    run_id: RandomId
    route: Literal["diagnose", "direct", "builtin", "external"]
    state: Literal["checking", "needs_choice", "preparing", "waiting_external", "ready", "failed"]
    stage: str
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
    interpretation_policy: Literal["declared_only", "operator_confirmed_bt709_limited_left"] = (
        "declared_only"
    )
    color_interpretation_available: bool = False
    color_interpretation_required: bool = False
    color_interpretation_reason: str = "等待完整色彩声明检查。"
    working_signal_basis: str | None = None
    handoff: ColorPreparedSourceHandoff | None = None
    error: ColorPreparedSourceFailure | None = None


class ColorPreparedSourceProcessingRequest(ProjectServiceModel):
    """只检查用户处理参数，不接受浏览器声明的 N/FPS。"""

    contract_version: Literal["0.3.4-color.1"]
    processing: PreparedSourceProcessing


class ColorPreparedSourceProcessingEnvelope(ProjectServiceModel):
    """处理配置合法不等于真实模型验收完成。"""

    contract_version: Literal["0.3.4-color.1"] = "0.3.4-color.1"
    status: Literal["pending_real_acceptance"] = "pending_real_acceptance"
    processing: PreparedSourceProcessing


class ColorPreparedSourceFullRequest(ColorPreparedSourceProcessingRequest):
    """预览/展开以完成准入的 preparation Run 为实际输入，仍受 CAS 约束。"""

    project_session_id: ProjectSessionId
    expected_storage_revision: StorageRevision
    preparation_run_id: RandomId
    publication: PublicationRequest


class ColorPreparedSourceFullEnvelope(ColorPreparedSourceProcessingEnvelope):
    """完整处理图的只读摘要；不传输第二张 Graph。"""

    profile_id: Literal["zniku.prepared-color-overlap"] = "zniku.prepared-color-overlap"
    profile_version: Literal["0.3.4-color.1"] = "0.3.4-color.1"
    project_session_id: ProjectSessionId
    storage_revision: StorageRevision
    preparation_run_id: RandomId
    execution_available: Literal[True] = True
    plan: ChapterLeafPlan
    contexts: ExperimentalContextPlan
    publication: PreparedSourcePublication
    node_count: Annotated[int, Field(gt=0)]
    edge_count: Annotated[int, Field(ge=0)]


class ColorPreparedSourceFailureEnvelope(ProjectServiceModel):
    """失败不携带成功摘要或可用规划。"""

    contract_version: Literal["0.3.4-color.1"] = "0.3.4-color.1"
    error: ColorPreparedSourceFailure


class ColorPreparedSourceError(RuntimeError):
    """业务路由返回版本化错误；没有补救副作用。"""

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
        self.envelope = ColorPreparedSourceFailureEnvelope(
            error=ColorPreparedSourceFailure(
                code=code,
                message=message[:4096] or "素材检查与准备失败",
                field_path=field_path,
                related_run_ids=related_run_ids,
            )
        )
        super().__init__(f"{code}: {message}")
