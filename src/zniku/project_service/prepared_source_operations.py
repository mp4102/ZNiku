"""按真实活动 attempt 投影准备进度与发送停止，不限制合法自由图的拓扑。

这里只管理本服务的临时协作信号，不编辑 Graph，不生成准入结论，不增加 Runtime 状态。
向导路线选择/展开仍单独保持小图形状门禁；手工操作必须明确绑定其正在验证的 attempt。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from zniku.runtime import NodeRun, NodeRunState, Run
from zniku.source_preparation.definitions import definition_role

from .models import StatusEnvelope
from .prepared_source import (
    PreparedSourceOperationEnvelope,
    PreparedSourceOperationRequest,
    PreparedSourceViewRequest,
)

if TYPE_CHECKING:
    from .service import ProjectServiceApplication


def _attempt(run: Run, node_run_id: str) -> tuple[NodeRun, str]:
    """只接受 snapshot 中完整 exact 准备定义的最新 attempt，不从 UI 别名推断。"""
    from .prepared_source_application import _error

    attempt = next((item for item in run.node_runs if item.node_run_id == node_run_id), None)
    if attempt is None or any(
        item.node_id == attempt.node_id and item.attempt > attempt.attempt for item in run.node_runs
    ):
        raise _error("OPERATION_ATTEMPT", "检查任务不存在或已产生新的 attempt，请刷新。")
    node = next(
        (item for item in run.graph_snapshot.nodes if item.node_id == attempt.node_id), None
    )
    definition = next(
        (
            item
            for item in run.definitions_snapshot
            if node is not None
            and (item.type_id, item.version) == (node.type_id, node.definition_version)
        ),
        None,
    )
    role = None if definition is None else definition_role(definition)
    if role is None:
        raise _error("OPERATION_DEFINITION", "当前节点不是正式素材准备定义，不能使用此停止入口。")
    return attempt, role


def _run(app: ProjectServiceApplication, request: PreparedSourceViewRequest) -> Run:
    """锁内只核对会话、工程和普通 snapshot 身份，不要求当前编辑图与运行图相同。"""
    from .prepared_source_application import _error

    app.assert_preview_session(request.project_session_id)
    store, runtime = app._require_session()
    run = runtime.repository.get_run(request.run_id)
    if store.load().project.project_id != run.project_id:
        raise _error("PROJECT", "当前检查不属于已打开工程。")
    return run


def _active_attempt(app: ProjectServiceApplication, run: Run) -> NodeRun | None:
    """manual 用显式临时绑定，automatic 用当前唯一 RUNNING 受信节点；不猜 waiting 次序。"""
    from .prepared_source import PreparedSourceError

    if (
        app._active_run_id != run.run_id
        or app._active_operation is None
        or app._preparation_cancel is None
    ):
        return None
    manual_target = app._active_preparation_node_run_id
    if manual_target is not None:
        try:
            attempt, role = _attempt(run, manual_target)
        except PreparedSourceError:
            return None
        if role == "external" and attempt.state in {
            NodeRunState.WAITING_EXTERNAL,
            NodeRunState.RUNNING,
        }:
            return attempt
        # 显式 Submit 可以在同一个 worker 内继续运行下游 Admission；已完成旧交接不冒充它。
    running = [item for item in run.node_runs if item.state is NodeRunState.RUNNING]
    if len(running) != 1:
        return None
    try:
        attempt, role = _attempt(run, running[0].node_run_id)
    except PreparedSourceError:
        return None
    return attempt if role in {"source", "diagnostics", "builtin", "admission"} else None


def operation_view(
    app: ProjectServiceApplication, payload: object
) -> PreparedSourceOperationEnvelope:
    """任意合法图均可读取明确 attempt 的有界阶段；没有活动操作时不展示过期进度。"""
    from .prepared_source_application import _parse, _stage_progress

    request = _parse(PreparedSourceOperationRequest, payload)
    with app._state:
        run = _run(app, request)
        attempt, _ = _attempt(run, request.node_run_id)
        current = _active_attempt(app, run)
        active = current is not None and current.node_run_id == attempt.node_run_id
        return PreparedSourceOperationEnvelope(
            **request.model_dump(),
            active=active,
            operation=app._active_operation if active else None,
            stage_progress=_stage_progress(app, attempt) if active else None,
            cancel_requested=bool(
                active and app._preparation_cancel and app._preparation_cancel.is_set()
            ),
        )


def operation_cancel(
    app: ProjectServiceApplication, payload: object, *, run_only: bool = False
) -> StatusEnvelope:
    """只通知当前受信操作；错 Run/attempt 或已结束操作不改变任何信号或运行状态。"""
    from .prepared_source_application import _error, _parse

    request = (
        _parse(PreparedSourceViewRequest, payload)
        if run_only
        else _parse(PreparedSourceOperationRequest, payload)
    )
    with app._state:
        run = _run(app, request)
        active = _active_attempt(app, run)
        if isinstance(request, PreparedSourceOperationRequest):
            _attempt(run, request.node_run_id)
        if active is None or (
            isinstance(request, PreparedSourceOperationRequest)
            and active.node_run_id != request.node_run_id
        ):
            raise _error("NO_ACTIVE_CHECK", "此任务当前没有正在执行的素材检查或验证。")
        assert app._preparation_cancel is not None
        app._preparation_cancel.set()
        return app.inspect()
