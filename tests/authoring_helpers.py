"""为既有合成 Service 测试显式附加当前 authoring wire 绑定。

并发/迟到响应测试直接构造旧 binding，不使用此便捷入口，避免替它们刷新预期计数。
"""

from __future__ import annotations

from typing import Any

from zniku.project import StudioState
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.models import StatusEnvelope


def authoring_command(application: ProjectServiceApplication, payload: Any) -> StatusEnvelope:
    """模拟客户端先观察 status，再提交持有的 session/revision，不重试冲突。"""

    if isinstance(payload, dict) and payload.get("operation") in {
        "save_project",
        "run_all",
        "run_to",
        "expand_av_enhance_v27",
        "rerun_from_here",
    }:
        status = application.inspect()
        payload = {
            "project_session_id": status.project_session_id
            or "00000000-0000-4000-8000-000000000000",
            "expected_storage_revision": status.storage_revision or 0,
            **payload,
        }
        if payload["operation"] == "save_project":
            payload.setdefault("studio_state", status.studio_state or StudioState())
    return application.command(payload)
