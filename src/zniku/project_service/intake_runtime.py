"""把新 MR 的固定视频端口与受控格式选择接到既有 ManualSubmission，不改持久 handoff。

正式输出仍在独立 attempt 的 outputs 内。只允许从直接输入推导的三个有限规范名，
零个保持等待，多个明确拒绝歧义；任何文件存在都不自动登记或推进 Runtime。
"""

from __future__ import annotations

from pathlib import Path

from zniku.runtime import ExternalOutputTarget, NodeRun, RuntimeService
from zniku.runtime.runner import ManualSubmission, NodeExecutionRequest, ProducedOutput
from zniku.source_admission import mosaic_restoration as mr

from .handoff_import import ImportAuthority, _safe_path, _target_path
from .host_bridge import HostBridgeFailure


def intake_context(authority: ImportAuthority) -> NodeExecutionRequest:
    """再次确认当前 waiting exact；选择句柄权限只在 Project Service 内解引用。"""
    handoff = authority.node_run.external_handoff
    if handoff is None:
        raise HostBridgeFailure(
            "E_HANDOFF_INTAKE_BINDING", "任务已不再等待外部文件", http_status=409
        )
    request = authority.runtime.inspect_external_request(
        authority.node_run.run_id, authority.node_run.node_run_id, handoff_id=handoff.handoff_id
    )
    if not mr.is_definition(request.definition) or authority.target.port_id != "video":
        raise HostBridgeFailure(
            "E_HANDOFF_INTAKE_UNSUPPORTED", "此旧任务继续使用原交回方式", http_status=422
        )
    _target_path(authority)
    return request


def published_submission(
    runtime: RuntimeService, node_run: NodeRun
) -> tuple[ManualSubmission | None, tuple[ExternalOutputTarget, ...]]:
    """只解析已发布的唯一实际位置；不按文件时间、体积或任意候选名称猜选。"""
    handoff = node_run.external_handoff
    assert handoff is not None
    run = runtime.repository.get_run(node_run.run_id)
    # 老 exact 不额外解引用/检查输入，保持原 readiness 与提交时机；新身份仍完整比较定义。
    if not any(
        node.node_id == node_run.node_id and node.type_id == mr.TYPE_ID
        for node in run.graph_snapshot.nodes
    ):
        return None, handoff.output_targets
    request = runtime.inspect_external_request(
        node_run.run_id, node_run.node_run_id, handoff_id=handoff.handoff_id
    )
    if not mr.is_definition(request.definition):
        return None, handoff.output_targets
    root = _safe_path(Path(node_run.work_dir) / "outputs")
    paths = [root / mr.archive_basename(request.inputs, container) for container in mr.CONTAINERS]
    present = [_safe_path(path) for path in paths if path.exists() or path.is_symlink()]
    if len(present) > 1:
        raise HostBridgeFailure(
            "E_HANDOFF_INTAKE_AMBIGUOUS",
            "正式输出区存在多份不同封装结果；请保留本次选定的一份，系统不会猜选",
            http_status=409,
        )
    if not present:
        return None, handoff.output_targets
    path = present[0]
    return ManualSubmission(outputs=(ProducedOutput("video", path),)), (
        handoff.output_targets[0].model_copy(update={"path": str(path)}),
    )
