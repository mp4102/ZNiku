"""声明单视频交回的只读选择、检查后收纳和显式发布 wire；不增加 Runtime 状态。"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from .handoff_import import HandoffImportBinding
from .host_bridge import HostBridgeModel, HostLocalPath, HostOpaqueId

Name = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class HandoffIntakeCandidate(HostBridgeModel):
    candidate_handle: HostOpaqueId
    name: Name
    size: Annotated[int, Field(gt=0)]
    container: Literal["mp4", "mov", "mkv"]


class HandoffIntakeObserveEnvelope(HandoffImportBinding):
    inbox_path: HostLocalPath
    candidates: tuple[HandoffIntakeCandidate, ...]
    rejected_count: Annotated[int, Field(ge=0)]
    message: str | None = None


class HandoffIntakeSelectRequest(HandoffImportBinding):
    selection_handle: HostOpaqueId | None = None
    candidate_handle: HostOpaqueId | None = None

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if (self.selection_handle is None) == (self.candidate_handle is None):
            raise ValueError("必须指定唯一的选择或目录候选句柄")
        return self


class HandoffIntakeSelectEnvelope(HandoffImportBinding):
    ticket_id: HostOpaqueId
    source_name: Name
    source_path: HostLocalPath
    source_size: Annotated[int, Field(gt=0)]
    container: Literal["mp4", "mov", "mkv"]
    archive_name: Name
    incoming_path: HostLocalPath
    output_path: HostLocalPath
    replace_existing: bool
    action: Literal["copy", "rename", "none"]
    expires_in_seconds: Literal[300] = 300


class HandoffIntakeCheckRequest(HostBridgeModel):
    contract_version: Literal["0.3.0"]
    ticket_id: HostOpaqueId
    overwrite: bool


class HandoffIntakeJobRequest(HostBridgeModel):
    contract_version: Literal["0.3.0"]
    job_id: HostOpaqueId


class HandoffIntakeJobEnvelope(HandoffIntakeJobRequest):
    phase: Literal["checking", "copying", "ready", "failed"]
    bytes_done: Annotated[int, Field(ge=0)]
    total_bytes: Annotated[int, Field(gt=0)]
    ready_id: HostOpaqueId | None = None
    message: str | None = None


class HandoffIntakePublishRequest(HostBridgeModel):
    contract_version: Literal["0.3.0"]
    ready_id: HostOpaqueId


class HandoffIntakePublishEnvelope(HostBridgeModel):
    contract_version: Literal["0.3.0"] = "0.3.0"
    output_path: HostLocalPath
