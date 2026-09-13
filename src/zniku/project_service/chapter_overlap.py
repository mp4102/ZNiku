"""声明独立的0.3.2规划、处理设置和普通图展开wire；不改旧请求的版本或含义。

请求仅携带已有工程会话、存储计数、preparation Run 和用户设置。N/FPS/Artifact 只能由服务读取；
错误保留结构化字段位置，便于后续表单定位，不把文案解析变成参数 authority。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from zniku.avenhance_v27.template import PublicationRequest
from zniku.chapter_overlap import ChapterLeafPlan, ChapterSettings
from zniku.chapter_overlap.context import ExperimentalContextPlan
from zniku.chapter_overlap.template import OverlapProcessing, OverlapPublication
from zniku.project.studio import StorageRevision
from zniku.runtime.models import RandomId

from .models import ProjectServiceModel, ProjectSessionId

CHAPTER_OVERLAP_PREVIEW_ROUTE = "/api/studio/templates/chapter-overlap-fi/preview"
CHAPTER_OVERLAP_PROCESSING_ROUTE = "/api/studio/templates/chapter-overlap-fi/processing-preview"
CHAPTER_OVERLAP_FULL_PREVIEW_ROUTE = "/api/studio/templates/chapter-overlap-fi/full-preview"
CHAPTER_OVERLAP_EXPAND_ROUTE = "/api/studio/templates/chapter-overlap-fi/expand"


class ChapterOverlapProcessingRequest(ProjectServiceModel):
    """分析前验证设置，不接受浏览器提供的媒体事实。"""

    contract_version: Literal["0.3.2"]
    processing: OverlapProcessing


class ChapterOverlapProcessingEnvelope(ProjectServiceModel):
    """仅表示设置合法，不冒充Aion或源媒体已经验收。"""

    contract_version: Literal["0.3.2"] = "0.3.2"
    status: Literal["pending_real_acceptance"] = "pending_real_acceptance"
    processing: OverlapProcessing


class ChapterOverlapFullRequest(ChapterOverlapProcessingRequest):
    """预览与展开共享请求；每次重新核对会话、CAS、分析Run和原文件stat。"""

    project_session_id: ProjectSessionId
    expected_storage_revision: StorageRevision
    preparation_run_id: RandomId
    publication: PublicationRequest


class ChapterOverlapFullEnvelope(ChapterOverlapProcessingEnvelope):
    """完整新链的只读投影，不传输第二张可运行Graph。"""

    profile_id: Literal["zniku.chapter-overlap-fi"] = "zniku.chapter-overlap-fi"
    profile_version: Literal["0.3.2"] = "0.3.2"
    project_session_id: ProjectSessionId
    storage_revision: StorageRevision
    preparation_run_id: RandomId
    execution_available: Literal[True] = True
    plan: ChapterLeafPlan
    contexts: ExperimentalContextPlan
    publication: OverlapPublication
    node_count: Annotated[int, Field(gt=0)]
    edge_count: Annotated[int, Field(ge=0)]


class ChapterOverlapPreviewRequest(ProjectServiceModel):
    """严格绑定一个已接纳的 preparation；省略设置采用平均1章、最长5分钟。"""

    contract_version: Literal["0.3.2"]
    project_session_id: ProjectSessionId
    expected_storage_revision: StorageRevision
    preparation_run_id: RandomId
    settings: ChapterSettings = Field(default_factory=ChapterSettings)


class ChapterOverlapPreviewEnvelope(ProjectServiceModel):
    """只读规划结果；明确不能据此启动尚未实现的重叠 FI。"""

    contract_version: Literal["0.3.2"] = "0.3.2"
    project_session_id: ProjectSessionId
    storage_revision: StorageRevision
    preparation_run_id: RandomId
    admission_artifact_id: RandomId
    execution_available: Literal[False] = False
    plan: ChapterLeafPlan


class ChapterOverlapFailure(ProjectServiceModel):
    """新预览专属失败，不改变旧 0.3.0 的错误响应形状。"""

    code: Annotated[str, Field(min_length=1, max_length=160)]
    message: Annotated[str, Field(min_length=1, max_length=4096)]
    field_path: tuple[str | int, ...] = ()
    related_run_ids: tuple[RandomId, ...] = ()


class ChapterOverlapFailureEnvelope(ProjectServiceModel):
    """返回可辨认的版本及错误；失败响应永不携带可用规划或成功标志。"""

    contract_version: Literal["0.3.2"] = "0.3.2"
    error: ChapterOverlapFailure


class ChapterOverlapPreviewError(RuntimeError):
    """在 facade 与 HTTP 之间传递专属失败；无状态迁移和补救副作用。"""

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
        self.envelope = ChapterOverlapFailureEnvelope(
            error=ChapterOverlapFailure(
                code=code,
                message=message[:4096],
                field_path=field_path,
                related_run_ids=related_run_ids,
            )
        )
        super().__init__(f"{code}: {message}")
