"""声明单一 AV2.7 源准入的 0.3.5 请求；候选导入是显式新参考，不代表内容等价。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from zniku.avenhance_v27.template import PrepareRequest, PublicationRequest
from zniku.chapter_overlap import ChapterLeafPlan
from zniku.chapter_overlap.context import ExperimentalContextPlan
from zniku.project.studio import StorageRevision
from zniku.runtime.models import RandomId
from zniku.source_aligned.template import SourceAlignedProcessing, SourceAlignedPublication

from .models import ProjectServiceModel, ProjectSessionId
from .source_aligned import SourceAlignedFailure

PREFIX = "/api/studio/templates/source-admitted-overlap"
CREATE_ROUTE = PREFIX + "/create"
REPLACE_ROUTE = PREFIX + "/replace-source"
CANCEL_ROUTE = PREFIX + "/cancel-analysis"
PROCESSING_ROUTE = PREFIX + "/processing-preview"
FULL_PREVIEW_ROUTE = PREFIX + "/full-preview"
EXPAND_ROUTE = PREFIX + "/expand"


class SourceAdmittedCreateRequest(ProjectServiceModel):
    """仅创建只读源引用图；返回普通 StatusEnvelope，再通过 run_all 开始分析。"""

    contract_version: Literal["0.3.5"]
    request: PrepareRequest
    data_parent_directory: (
        Annotated[str, StringConstraints(min_length=1, max_length=32767)] | None
    ) = None
    media_basename: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None

    @field_validator("data_parent_directory", "media_basename")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is not None and ("\x00" in value or value.strip() != value or not value.strip()):
            raise ValueError("路径或媒体名称不得包含 NUL、空白值或边界空白")
        return value


class SourceAdmittedReplaceRequest(ProjectServiceModel):
    """仅未展开且空闲的分析图可显式换源；CAS 和 session 拒绝迟到请求。"""

    contract_version: Literal["0.3.5"]
    project_session_id: ProjectSessionId
    expected_storage_revision: StorageRevision
    source_path: Annotated[str, StringConstraints(min_length=1, max_length=32767)]
    reference_change_confirmed: Literal[True]

    @field_validator("source_path")
    @classmethod
    def clean_path(cls, value: str) -> str:
        if "\x00" in value or value.strip() != value or not value.strip():
            raise ValueError("候选路径不得包含 NUL、空白值或边界空白")
        return value

    @field_validator("reference_change_confirmed", mode="before")
    @classmethod
    def strict_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("必须明确确认候选作为新的处理参考，不能假定与原片逐帧相同")
        return value


class SourceAdmittedCancelRequest(ProjectServiceModel):
    """只取消当前新 Source attempt，不放弃其他业务 Run 或删除文件。"""

    contract_version: Literal["0.3.5"]
    project_session_id: ProjectSessionId
    run_id: RandomId


class SourceAdmittedProcessingRequest(ProjectServiceModel):
    contract_version: Literal["0.3.5"]
    processing: SourceAlignedProcessing


class SourceAdmittedProcessingEnvelope(ProjectServiceModel):
    contract_version: Literal["0.3.5"] = "0.3.5"
    status: Literal["pending_real_acceptance"] = "pending_real_acceptance"
    processing: SourceAlignedProcessing


class SourceAdmittedFullRequest(SourceAdmittedProcessingRequest):
    project_session_id: ProjectSessionId
    expected_storage_revision: StorageRevision
    preparation_run_id: RandomId
    publication: PublicationRequest


class SourceAdmittedFullEnvelope(SourceAdmittedProcessingEnvelope):
    profile_id: Literal["zniku.source-admitted-overlap"] = "zniku.source-admitted-overlap"
    profile_version: Literal["0.3.5"] = "0.3.5"
    project_session_id: ProjectSessionId
    storage_revision: StorageRevision
    preparation_run_id: RandomId
    execution_available: Literal[True] = True
    plan: ChapterLeafPlan
    contexts: ExperimentalContextPlan
    publication: SourceAlignedPublication
    node_count: Annotated[int, Field(gt=0)]
    edge_count: Annotated[int, Field(ge=0)]
    warnings: tuple[str, ...] = ()


class SourceAdmittedFailureEnvelope(ProjectServiceModel):
    contract_version: Literal["0.3.5"] = "0.3.5"
    error: SourceAlignedFailure


class SourceAdmittedError(RuntimeError):
    """版本专属 wire 错误；失败不携带候选成功或任何替代规划。"""

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
        self.envelope = SourceAdmittedFailureEnvelope(
            error=SourceAlignedFailure(
                code=code,
                message=message[:4096],
                field_path=field_path,
                related_run_ids=related_run_ids,
            )
        )
        super().__init__(f"{code}: {message}")
