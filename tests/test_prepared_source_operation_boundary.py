"""以纯内存受信运行快照验证自由图的准备监控与停止边界。

不启动媒体、不访问工程文件；只替换 Service 的存储/worker 外壳，Graph、Run、NodeRun 和
exact definitions 仍使用正式严格模型。停止能力不得依赖模板节点名，也不能误停另一交接。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Condition, Event
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from zniku.graph import Graph, ValidatorSpec
from zniku.project import Project, ProjectSnapshot
from zniku.project_service import ProjectServiceError
from zniku.project_service.prepared_source import PreparedSourceError
from zniku.project_service.prepared_source_application import dispatch
from zniku.runtime import (
    ExternalHandoff,
    NodeRun,
    NodeRunState,
    Run,
    RunState,
    RuntimeRepositoryError,
)
from zniku.source_preparation import build_preparation_graph, source_preparation_definitions


@dataclass
class _Operation:
    """保存测试 worker 身份；日志读取和最终状态返回均不访问磁盘。"""

    app: Any
    request: dict[str, str]
    run: Run
    attempt: NodeRun
    signal: Event
    store: Any


def _operation(
    *,
    prefix: str = "free",
    automatic: bool = False,
    extra_node: bool = False,
    second_waiting: bool = False,
    operation: str = "submit_external",
    forged_definition: bool = False,
) -> _Operation:
    """构造真实可验证 DAG；实体路径只是参数，测试从不读取它。"""
    definitions = source_preparation_definitions()
    graph = build_preparation_graph(
        "D:/synthetic/not-read.mkv",
        route="diagnose" if automatic else "external",
        target_frame_rate="30/1",
        node_prefix=prefix,
    )
    selected = next(
        node
        for node in graph.nodes
        if node.node_id.endswith("-diagnostics" if automatic else "-prepare")
    )
    if forged_definition:
        definitions = tuple(
            item.model_copy(update={"validator": ValidatorSpec(adapter="tests:untrusted")})
            if item.type_id == selected.type_id
            else item
            for item in definitions
        )
    if extra_node:
        graph = Graph(
            nodes=(*graph.nodes, graph.nodes[0].model_copy(update={"node_id": "unrelated-source"})),
            edges=graph.edges,
        )
    if second_waiting:
        duplicate = selected.model_copy(update={"node_id": "another-repair"})
        graph = Graph(
            nodes=(*graph.nodes, duplicate),
            edges=(
                *graph.edges,
                *(
                    edge.model_copy(update={"target_node_id": duplicate.node_id})
                    for edge in graph.edges
                    if edge.target_node_id == selected.node_id
                ),
            ),
        )
    run_id, node_run_id, session_id = str(uuid4()), str(uuid4()), str(uuid4())
    now = datetime.now(UTC)

    def make_attempt(node_id: str, identity: str) -> NodeRun:
        return NodeRun(
            node_run_id=identity,
            run_id=run_id,
            node_id=node_id,
            definition_version="0.3.4",
            attempt=1,
            state=NodeRunState.RUNNING if automatic else NodeRunState.WAITING_EXTERNAL,
            created_at=now,
            started_at=now,
            work_dir="D:/synthetic/not-created",
            external_handoff=None
            if automatic
            else ExternalHandoff(handoff_id=str(uuid4()), node_run_id=identity, created_at=now),
        )

    attempt = make_attempt(selected.node_id, node_run_id)
    attempts: tuple[NodeRun, ...] = (attempt,)
    if second_waiting:
        attempts += (make_attempt("another-repair", str(uuid4())),)
    run = Run(
        run_id=run_id,
        project_id="synthetic",
        graph_snapshot=graph,
        definitions_snapshot=definitions,
        state=RunState.RUNNING,
        node_runs=attempts,
        created_at=now,
        started_at=now,
    )
    snapshot = ProjectSnapshot(
        project=Project(project_id=run.project_id, name="纯合成停止边界", graph=graph),
        definitions=definitions,
    )
    store = SimpleNamespace(snapshot=snapshot)
    store.load = lambda: store.snapshot

    def get_run(identity: str) -> Run:
        result: Run | None = repository.runs.get(identity)
        if result is None:
            raise RuntimeRepositoryError("E_RUNTIME_NOT_FOUND", "合成 Run 不存在")
        return result

    repository = SimpleNamespace(
        runs={run_id: run},
        get_run=get_run,
        get_node_run=lambda identity: next(
            item for item in run.node_runs if item.node_run_id == identity
        ),
    )
    runtime = SimpleNamespace(repository=repository)
    signal = Event()

    def assert_session(value: str) -> None:
        if value != session_id:
            raise ProjectServiceError("E_PROJECT_SESSION_CONFLICT", "会话已切换", http_status=409)

    app = SimpleNamespace(
        _state=Condition(),
        _active_run_id=run_id,
        _active_operation="run_all" if automatic else operation,
        _active_preparation_node_run_id=None if automatic else node_run_id,
        _preparation_cancel=signal,
        _project_session_id=session_id,
        assert_preview_session=assert_session,
        _require_session=lambda: (store, runtime),
        _read_log=lambda *_: ("", False, False),
        inspect=lambda: SimpleNamespace(),
    )
    request = {
        "contract_version": "0.3.4",
        "project_session_id": session_id,
        "run_id": run_id,
        "node_run_id": node_run_id,
    }
    return _Operation(app, request, run, attempt, signal, store)


@pytest.mark.parametrize("operation", ["import_external", "submit_external"])
@pytest.mark.parametrize(
    "prefix,extra_node", [("source-preparation", False), ("free", False), ("free", True)]
)
def test_operation_cancel_accepts_exact_node_in_any_valid_graph(
    operation: str, prefix: str, extra_node: bool
) -> None:
    case = _operation(operation=operation, prefix=prefix, extra_node=extra_node)
    before = case.run.model_dump()
    dispatch(case.app, "operation-cancel", case.request)
    assert case.signal.is_set()
    assert case.app._active_operation == operation
    assert case.run.model_dump() == before


def test_operation_view_does_not_become_wizard_admission_authority() -> None:
    case = _operation(extra_node=True)
    result = dispatch(case.app, "operation-view", case.request).model_dump()
    assert result["active"] is True
    assert result["node_run_id"] == case.attempt.node_run_id
    assert result["operation"] == "submit_external"
    assert result["cancel_requested"] is False
    assert result["stage_progress"] is None
    assert (
        not {"available_actions", "admission_status", "source_frame_count", "route"} & result.keys()
    )
    assert not case.signal.is_set()
    with pytest.raises(PreparedSourceError, match="GRAPH_EDITED"):
        dispatch(
            case.app,
            "view",
            {key: value for key, value in case.request.items() if key != "node_run_id"},
        )


def test_automatic_stop_resolves_running_exact_node_not_template_name() -> None:
    case = _operation(automatic=True, extra_node=True)
    dispatch(case.app, "operation-cancel", case.request)
    assert case.signal.is_set()


def test_old_wizard_stop_still_targets_current_worker_after_graph_edit() -> None:
    case = _operation(automatic=True)
    snapshot = case.store.snapshot
    case.store.snapshot = snapshot.model_copy(
        update={"project": snapshot.project.model_copy(update={"graph": Graph()})}
    )
    dispatch(
        case.app,
        "cancel",
        {key: value for key, value in case.request.items() if key != "node_run_id"},
    )
    assert case.signal.is_set()


@pytest.mark.parametrize("field", ["run_id", "project_session_id", "node_run_id"])
def test_operation_stop_rejects_other_identity(field: str) -> None:
    case = _operation()
    request = {**case.request, field: str(uuid4())}
    with pytest.raises(PreparedSourceError):
        dispatch(case.app, "operation-cancel", request)
    assert not case.signal.is_set()


def test_multiple_waiting_repairs_stop_only_explicit_active_attempt() -> None:
    case = _operation(second_waiting=True)
    other = case.run.node_runs[1]
    other_request = {**case.request, "node_run_id": other.node_run_id}
    with pytest.raises(PreparedSourceError):
        dispatch(case.app, "operation-cancel", other_request)
    assert not case.signal.is_set()
    dispatch(case.app, "operation-cancel", case.request)
    assert case.signal.is_set()


def test_existing_run_is_not_cancellable_when_another_run_owns_worker() -> None:
    case = _operation()
    case.app._active_run_id = str(uuid4())
    with pytest.raises(PreparedSourceError):
        dispatch(case.app, "operation-cancel", case.request)
    assert not case.signal.is_set()


def test_old_attempt_cannot_receive_signal_after_retry() -> None:
    case = _operation()
    later_id = str(uuid4())
    handoff = case.attempt.external_handoff
    assert handoff is not None
    # 更换 handoff 与 attempt 身份必须是同一受控模型更新，不能构造交叉绑定。
    later = case.attempt.model_copy(
        update={
            "node_run_id": later_id,
            "attempt": 2,
            "external_handoff": handoff.model_copy(
                update={"handoff_id": str(uuid4()), "node_run_id": later_id}
            ),
        }
    )
    retried = case.run.model_copy(update={"node_runs": (*case.run.node_runs, later)})
    case.app._require_session()[1].repository.runs[case.run.run_id] = retried
    case.app._active_preparation_node_run_id = later.node_run_id
    with pytest.raises(PreparedSourceError):
        dispatch(case.app, "operation-cancel", case.request)
    assert not case.signal.is_set()
    dispatch(case.app, "operation-cancel", {**case.request, "node_run_id": later.node_run_id})
    assert case.signal.is_set()


def test_waiting_repair_without_active_worker_is_not_cancellable() -> None:
    case = _operation()
    case.app._active_operation = None
    case.app._active_preparation_node_run_id = None
    case.app._preparation_cancel = None
    result = dispatch(case.app, "operation-view", case.request).model_dump()
    assert result["active"] is False
    with pytest.raises(PreparedSourceError):
        dispatch(case.app, "operation-cancel", case.request)
    assert not case.signal.is_set()


def test_matching_type_id_with_changed_exact_definition_cannot_stop_worker() -> None:
    case = _operation(forged_definition=True)
    with pytest.raises(PreparedSourceError):
        dispatch(case.app, "operation-cancel", case.request)
    assert not case.signal.is_set()


def test_operation_wire_rejects_unknown_fields_without_signalling() -> None:
    case = _operation()
    with pytest.raises(PreparedSourceError, match="REQUEST_INVALID"):
        dispatch(case.app, "operation-cancel", {**case.request, "force": True})
    assert not case.signal.is_set()
