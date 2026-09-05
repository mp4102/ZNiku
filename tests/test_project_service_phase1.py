"""验证 Project Service 的 Run 可见性与人工交接合同。

本模块只创建临时 ``.zniku``、纯合成 DataFile 和受控 attempt 目录。测试把 status、Run detail、
历史分页、定向日志和 readiness 当作既有 Project/Runtime authority 的只读投影；任何读取都不得推进
状态、登记 Artifact 或改写文件。测试不导入其他测试模块的私有 helper，避免测试顺序成为隐含合同。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from pydantic import BaseModel, ValidationError

import zniku.project_service as project_service
from authoring_helpers import authoring_command
from zniku.graph import (
    Edge,
    ExecutionMode,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
    UiPosition,
    ValidatorSpec,
)
from zniku.project import PROJECT_SCHEMA_VERSION, Project, ProjectStore
from zniku.project_service import (
    ProjectServiceApplication,
    ProjectServiceError,
    parse_project_service_command,
)
from zniku.project_service.host import make_project_service_handler
from zniku.runtime import (
    NodeRun,
    NodeRunState,
    NodeValidatorContext,
    NodeValidatorResult,
    PythonAdapterContext,
    PythonAdapterResult,
    Run,
    RunState,
    RuntimeRepository,
)

_SOURCE_ADAPTER = "tests.phase1:source"
_SINK_ADAPTER = "tests.phase1:sink"
_MANUAL_VALIDATOR = "tests.phase1:manual-validator"


def _data_input() -> tuple[PortSpec, ...]:
    return (PortSpec(port_id="in", data_type="DataFile", required=True),)


def _data_output() -> tuple[PortSpec, ...]:
    return (PortSpec(port_id="out", data_type="DataFile"),)


def _source_definition() -> NodeDefinition:
    return NodeDefinition(
        type_id="test.phase1.Source",
        version="1.0.0",
        output_ports=_data_output(),
        parameter_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=_SOURCE_ADAPTER),
    )


def _manual_definition() -> NodeDefinition:
    return NodeDefinition(
        type_id="test.phase1.Manual",
        version="1.0.0",
        input_ports=_data_input(),
        output_ports=_data_output(),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(instructions="生成声明目标并提交"),
        validator=ValidatorSpec(adapter=_MANUAL_VALIDATOR),
    )


def _sink_definition() -> NodeDefinition:
    return NodeDefinition(
        type_id="test.phase1.Sink",
        version="1.0.0",
        input_ports=_data_input(),
        output_ports=_data_output(),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=_SINK_ADAPTER),
    )


def _project(
    *, include_manual: bool, include_sink: bool = False
) -> tuple[Project, tuple[NodeDefinition, ...]]:
    source = _source_definition()
    nodes = [
        NodeInstance(
            node_id="source",
            type_id=source.type_id,
            definition_version=source.version,
            parameters={"text": "synthetic"},
            ui_position=UiPosition(x=20.0, y=30.0),
        )
    ]
    edges: list[Edge] = []
    definitions: list[NodeDefinition] = [source]
    if include_manual:
        manual = _manual_definition()
        nodes.append(
            NodeInstance(
                node_id="manual",
                type_id=manual.type_id,
                definition_version=manual.version,
                ui_position=UiPosition(x=260.0, y=30.0),
            )
        )
        edges.append(
            Edge(
                source_node_id="source",
                source_port_id="out",
                target_node_id="manual",
                target_port_id="in",
            )
        )
        definitions.append(manual)
    if include_sink:
        if not include_manual:
            raise AssertionError("sink fixture 必须包含 manual 节点")
        sink = _sink_definition()
        nodes.append(
            NodeInstance(
                node_id="sink",
                type_id=sink.type_id,
                definition_version=sink.version,
                ui_position=UiPosition(x=500.0, y=30.0),
            )
        )
        edges.append(
            Edge(
                source_node_id="manual",
                source_port_id="out",
                target_node_id="sink",
                target_port_id="in",
            )
        )
        definitions.append(sink)
    return (
        Project(
            project_id="project.phase1",
            name="Phase 1 合成工程",
            graph=Graph(nodes=tuple(nodes), edges=tuple(edges)),
        ),
        tuple(definitions),
    )


def _store(
    tmp_path: Path,
    *,
    include_manual: bool,
    include_sink: bool = False,
    filename: str = "phase1.zniku",
) -> ProjectStore:
    project, definitions = _project(
        include_manual=include_manual,
        include_sink=include_sink,
    )
    return ProjectStore.create(tmp_path / filename, project, definitions)


def _adapters(
    *,
    stdout_payload: bytes = b"source stdout\n",
) -> dict[str, Callable[[PythonAdapterContext], PythonAdapterResult]]:
    def source(context: PythonAdapterContext) -> PythonAdapterResult:
        context.stdout_log_path.write_bytes(stdout_payload)
        context.stderr_log_path.write_text("source stderr\n", encoding="utf-8")
        context.outputs[0].path.write_text(
            str(context.node.parameters["text"]),
            encoding="utf-8",
        )
        return PythonAdapterResult()

    def sink(context: PythonAdapterContext) -> PythonAdapterResult:
        content = context.inputs[0].path.read_text(encoding="utf-8")
        context.outputs[0].path.write_text(f"{content}-sink", encoding="utf-8")
        return PythonAdapterResult()

    return {_SOURCE_ADAPTER: source, _SINK_ADAPTER: sink}


def _application(
    tmp_path: Path,
    store: ProjectStore,
    *,
    stdout_payload: bytes = b"source stdout\n",
    validator_calls: list[str] | None = None,
) -> ProjectServiceApplication:
    calls = validator_calls if validator_calls is not None else []

    def validate_manual(context: NodeValidatorContext) -> NodeValidatorResult:
        content = context.outputs[0].path.read_text(encoding="utf-8")
        calls.append(content)
        if content == "reject":
            return NodeValidatorResult(passed=False, message="合成 validator 拒绝输出")
        return NodeValidatorResult(passed=True, summary={"synthetic": True})

    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters=_adapters(stdout_payload=stdout_payload),
        validators={_MANUAL_VALIDATOR: validate_manual},
    )
    authoring_command(application, {"operation": "open_project", "path": str(store.path)})
    return application


def _state(value: object) -> str:
    return str(getattr(value, "value", value))


def _latest(run: Run, node_id: str) -> NodeRun:
    return max(
        (item for item in run.node_runs if item.node_id == node_id),
        key=lambda item: item.attempt,
    )


def _run_and_wait(application: ProjectServiceApplication, command: object) -> str:
    started = authoring_command(application, command)
    assert started.active_run_id is not None
    run_id = started.active_run_id
    assert application.wait_until_idle(timeout=5)
    return run_id


def _start_waiting_run(application: ProjectServiceApplication) -> tuple[str, Run, NodeRun]:
    run_id = _run_and_wait(application, {"operation": "run_all"})
    detail = application.inspect_run_detail(run_id)
    waiting = _latest(detail.run, "manual")
    assert waiting.state is NodeRunState.WAITING_EXTERNAL
    assert waiting.external_handoff is not None
    return run_id, detail.run, waiting


def _tree(root: Path) -> tuple[tuple[str, bool, int, int], ...]:
    if not root.exists():
        return ()
    return tuple(
        (
            str(path.relative_to(root)),
            path.is_file(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
    )


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _sqlite_dump(path: Path) -> tuple[str, ...]:
    """读取 SQLite 逻辑内容，用于证明失败命令没有留下部分写入。"""

    with sqlite3.connect(path) as connection:
        return tuple(connection.iterdump())


def test_phase1_wire_version_is_exact() -> None:
    """浏览器 wire 升为 exact 0.3.0，不能与 Project schema version 捆绑。"""

    assert PROJECT_SCHEMA_VERSION == 3
    assert project_service.PROJECT_SERVICE_CONTRACT_VERSION == "0.3.0"


def test_phase1_commands_are_strict_and_identity_bound() -> None:
    run_id = "00000000-0000-4000-8000-000000000001"
    node_run_id = "00000000-0000-4000-8000-000000000002"
    handoff_id = "00000000-0000-4000-8000-000000000003"

    submit = parse_project_service_command(
        {
            "operation": "submit_external",
            "run_id": run_id,
            "node_run_id": node_run_id,
            "handoff_id": handoff_id,
        }
    )
    assert submit.run_id == run_id  # type: ignore[union-attr]
    assert submit.node_run_id == node_run_id  # type: ignore[union-attr]
    assert submit.handoff_id == handoff_id  # type: ignore[union-attr]

    abandon = parse_project_service_command({"operation": "abandon_run", "run_id": run_id})
    assert abandon.run_id == run_id  # type: ignore[union-attr]

    invalid_payloads: tuple[object, ...] = (
        {"operation": "submit_external", "node_run_id": node_run_id},
        {
            "operation": "submit_external",
            "run_id": run_id,
            "node_run_id": node_run_id,
            "handoff_id": handoff_id,
            "path": "C:\\synthetic\\forbidden.mkv",
        },
        {"operation": "abandon_run", "run_id": run_id, "force": True},
        {"operation": "abandon_run", "run_id": 1},
        {"operation": "start_another_run"},
    )
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            parse_project_service_command(payload)


def test_status_summary_and_detail_are_separate_bounded_read_models(tmp_path: Path) -> None:
    store = _store(tmp_path, include_manual=True)
    application = _application(tmp_path, store)

    terminal_id = _run_and_wait(
        application,
        {"operation": "run_to", "node_id": "source"},
    )
    waiting_id, waiting_run, _ = _start_waiting_run(application)

    status = application.inspect(view_run_id=terminal_id)
    assert status.contract_version == "0.3.0"
    assert set(_dump(status)) == {
        "project_session_id",
        "storage_revision",
        "studio_state",
        "authoring_diagnostics",
        "studio_warnings",
        "contract_version",
        "project_path",
        "snapshot",
        "run_summaries",
        "next_run_cursor",
        "active_run_id",
        "active_operation",
        "latest_results",
        "error",
    }
    assert not hasattr(status, "runs")
    assert not hasattr(status, "artifacts")
    assert not hasattr(status, "logs")

    summaries = {item.run_id: item for item in status.run_summaries}
    assert tuple(item.run_id for item in status.run_summaries) == (waiting_id, terminal_id)
    waiting = summaries[waiting_id]
    assert waiting.target_mode == "all"
    assert waiting.selected_targets == ()
    assert _state(waiting.state) == "running"
    assert waiting.node_count == 2
    assert waiting.state_counts.pending == 0
    assert waiting.state_counts.running == 0
    assert waiting.state_counts.waiting_external == 1
    assert waiting.state_counts.completed == 1
    assert waiting.state_counts.failed == 0
    assert sum(_dump(waiting.state_counts).values()) == waiting.node_count
    assert waiting.actionable is True
    assert waiting.requires_operator_action is True
    assert waiting.latest_activity_at >= waiting.created_at

    terminal = summaries[terminal_id]
    assert terminal.target_mode == "selected"
    assert terminal.selected_targets == ("source",)
    assert _state(terminal.state) == "completed"
    assert terminal.actionable is False
    assert terminal.requires_operator_action is False

    detail = application.inspect_run_detail(waiting_id)
    assert detail.contract_version == "0.3.0"
    assert set(_dump(detail)) == {
        "contract_version",
        "run",
        "artifacts",
        "progress_samples",
        "handoff_contracts",
    }
    assert detail.run == waiting_run
    referenced_ids = {
        artifact_id
        for node_run in detail.run.node_runs
        for artifact_id in (*node_run.input_artifact_ids, *node_run.output_artifact_ids)
    }
    assert {item.artifact_id for item in detail.artifacts} == referenced_ids
    assert detail.progress_samples == ()
    assert detail.handoff_contracts == ()

    with pytest.raises(ProjectServiceError) as missing:
        application.inspect(view_run_id="00000000-0000-4000-8000-ffffffffffff")
    assert missing.value.code == "E_PROJECT_SERVICE_RUN_NOT_FOUND"
    assert missing.value.http_status == 404


def test_terminal_history_uses_bounded_opaque_cursor_without_gaps(tmp_path: Path) -> None:
    store = _store(tmp_path, include_manual=False)
    application = _application(tmp_path, store)
    created_ids = [_run_and_wait(application, {"operation": "run_all"}) for _ in range(23)]

    status = application.inspect()
    assert len(status.run_summaries) == 20
    assert status.next_run_cursor is not None
    assert tuple(item.run_id for item in status.run_summaries) == tuple(reversed(created_ids[-20:]))

    oldest_extra = application.inspect(view_run_id=created_ids[0])
    assert len(oldest_extra.run_summaries) == 21
    assert oldest_extra.next_run_cursor == status.next_run_cursor
    assert oldest_extra.run_summaries[-1].run_id == created_ids[0]

    collected: list[str] = []
    cursor: str | None = None
    while True:
        page = application.list_run_summaries(
            cursor=cursor,
            limit=7,
        )
        assert page.contract_version == "0.3.0"
        assert 1 <= len(page.run_summaries) <= 7
        collected.extend(item.run_id for item in page.run_summaries)
        cursor = page.next_run_cursor
        if cursor is None:
            break

    assert collected == list(reversed(created_ids))
    assert len(collected) == len(set(collected)) == 23

    for cursor, limit in (("not-an-opaque-cursor", 7), (None, 0), (None, 101)):
        with pytest.raises(ProjectServiceError) as invalid:
            application.list_run_summaries(cursor=cursor, limit=limit)
        assert invalid.value.http_status in {400, 422}


def test_logs_are_bounded_and_bound_to_exact_run_and_attempt(tmp_path: Path) -> None:
    stdout = b"discarded-prefix\n" + b"x" * (128 * 1024) + b"\xfftail"
    store = _store(tmp_path, include_manual=False)
    application = _application(tmp_path, store, stdout_payload=stdout)
    first_id = _run_and_wait(application, {"operation": "run_all"})
    second_id = _run_and_wait(application, {"operation": "run_all"})
    first = application.inspect_run_detail(first_id).run
    second = application.inspect_run_detail(second_id).run
    first_node_run = _latest(first, "source")
    second_node_run = _latest(second, "source")

    envelope = application.inspect_node_logs(
        first_id,
        first_node_run.node_run_id,
    )
    assert envelope.contract_version == "0.3.0"
    assert envelope.run_id == first_id
    assert envelope.log.node_run_id == first_node_run.node_run_id
    assert envelope.log.stdout_available is True
    assert envelope.log.stdout_truncated is True
    assert envelope.log.stdout.endswith("�tail")
    assert "discarded-prefix" not in envelope.log.stdout
    assert len(envelope.log.stdout.encode("utf-8")) <= 128 * 1024 + 2
    assert envelope.log.stderr.splitlines() == ["source stderr"]
    assert envelope.log.stderr_available is True
    assert envelope.log.stderr_truncated is False

    with pytest.raises(ProjectServiceError) as outside:
        application.inspect_node_logs(first_id, second_node_run.node_run_id)
    assert outside.value.code == "E_PROJECT_SERVICE_NODE_RUN_OUTSIDE_RUN"
    assert outside.value.http_status == 409


def test_readiness_five_states_are_read_only(tmp_path: Path) -> None:
    validator_calls: list[str] = []
    store = _store(tmp_path, include_manual=True)
    application = _application(tmp_path, store, validator_calls=validator_calls)
    run_id, _, waiting = _start_waiting_run(application)
    assert waiting.external_handoff is not None
    handoff = waiting.external_handoff
    target = handoff.output_targets[0]
    target_path = Path(target.path)
    attempt_root = Path(waiting.work_dir)

    def observe(*, probe: bool) -> Any:
        before_status = application.inspect(view_run_id=run_id)
        before_detail = application.inspect_run_detail(run_id)
        before_db = _sqlite_dump(store.path)
        before_tree = _tree(attempt_root)
        readiness = application.inspect_external_readiness(
            run_id=run_id,
            node_run_id=waiting.node_run_id,
            probe=probe,
        )
        assert application.inspect(view_run_id=run_id) == before_status
        assert application.inspect_run_detail(run_id) == before_detail
        assert _sqlite_dump(store.path) == before_db
        assert _tree(attempt_root) == before_tree
        assert readiness.contract_version == "0.3.0"
        assert readiness.run_id == run_id
        assert readiness.node_run_id == waiting.node_run_id
        assert readiness.handoff_id == handoff.handoff_id
        assert readiness.probe_requested is probe
        assert len(readiness.targets) == 1
        assert readiness.targets[0].port_id == target.port_id
        assert readiness.targets[0].ordinal == target.ordinal
        assert readiness.targets[0].path == target.path
        return readiness

    missing = observe(probe=False)
    assert missing.targets[0].state == "missing"
    assert missing.targets[0].size is None
    assert missing.targets[0].mtime_ns is None
    assert missing.ready_for_submit is False
    assert validator_calls == []

    missing_probe = observe(probe=True)
    assert missing_probe.targets[0].state == "missing"
    assert missing_probe.ready_for_submit is False
    assert validator_calls == []

    target_path.write_bytes(b"")
    empty = observe(probe=False)
    assert empty.targets[0].state == "empty"
    assert empty.targets[0].size == 0
    assert empty.ready_for_submit is False
    assert validator_calls == []

    target_path.write_text("valid", encoding="utf-8")
    present = observe(probe=False)
    assert present.targets[0].state == "present"
    assert present.targets[0].size == len(b"valid")
    assert present.targets[0].mtime_ns is not None
    assert present.ready_for_submit is False
    assert validator_calls == []

    passed = observe(probe=True)
    assert passed.targets[0].state == "probe_passed"
    assert passed.ready_for_submit is True
    assert validator_calls == ["valid"]

    target_path.write_text("reject", encoding="utf-8")
    failed = observe(probe=True)
    assert failed.targets[0].state == "probe_failed"
    assert failed.targets[0].message is not None
    assert failed.targets[0].message.startswith("E_RUNNER_VALIDATION_REJECTED:")
    assert failed.targets[0].message.endswith("合成 validator 拒绝输出")
    assert failed.ready_for_submit is False
    assert validator_calls == ["valid", "reject"]


@pytest.mark.parametrize("transition", ("supersede", "abandon"))
def test_readiness_fails_closed_when_handoff_changes_during_validator(
    tmp_path: Path,
    transition: str,
) -> None:
    """耗时 probe 不能把已过期或已终结的 handoff 误报为 ready/probe_failed。"""

    store = _store(
        tmp_path,
        include_manual=True,
        filename=f"readiness-{transition}.zniku",
    )
    validator_entered = threading.Event()
    release_validator = threading.Event()

    def blocking_validator(context: NodeValidatorContext) -> NodeValidatorResult:
        assert context.outputs[0].path.read_text(encoding="utf-8") == "valid"
        validator_entered.set()
        if not release_validator.wait(timeout=10):
            raise RuntimeError("合成 validator 等待释放超时")
        return NodeValidatorResult(passed=True, summary={"synthetic": True})

    application = ProjectServiceApplication(
        work_root=tmp_path / f"readiness-{transition}-work",
        python_adapters=_adapters(),
        validators={_MANUAL_VALIDATOR: blocking_validator},
    )
    authoring_command(application, {"operation": "open_project", "path": str(store.path)})
    run_id, _, waiting = _start_waiting_run(application)
    assert waiting.external_handoff is not None
    Path(waiting.external_handoff.output_targets[0].path).write_text(
        "valid",
        encoding="utf-8",
    )

    outcome: list[object] = []

    def inspect() -> None:
        try:
            outcome.append(
                application.inspect_external_readiness(
                    run_id=run_id,
                    node_run_id=waiting.node_run_id,
                    probe=True,
                )
            )
        except BaseException as error:  # 测试线程必须把失败显式交回主线程断言。
            outcome.append(error)

    worker = threading.Thread(target=inspect, daemon=True)
    worker.start()
    assert validator_entered.wait(timeout=5)

    try:
        if transition == "supersede":
            rerun = authoring_command(
                application,
                {
                    "operation": "rerun_from_here",
                    "run_id": run_id,
                    "node_id": "source",
                },
            )
            assert rerun.active_run_id == run_id
            assert application.wait_until_idle(timeout=5)
            latest_manual = _latest(application.inspect_run_detail(run_id).run, "manual")
            assert latest_manual.node_run_id != waiting.node_run_id
            assert latest_manual.state is NodeRunState.WAITING_EXTERNAL
        else:
            authoring_command(application, {"operation": "abandon_run", "run_id": run_id})
            latest_manual = _latest(application.inspect_run_detail(run_id).run, "manual")
            assert latest_manual.node_run_id == waiting.node_run_id
            assert latest_manual.state is NodeRunState.FAILED

        after_transition = application.inspect_run_detail(run_id)
        after_transition_db = _sqlite_dump(store.path)
        after_transition_tree = _tree(application.work_root)
    finally:
        release_validator.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(outcome) == 1
    error = outcome[0]
    assert isinstance(error, ProjectServiceError)
    assert error.code == "E_PROJECT_SERVICE_HANDOFF_NOT_ACTIONABLE"
    assert error.http_status == 409
    assert application.inspect_run_detail(run_id) == after_transition
    assert _sqlite_dump(store.path) == after_transition_db
    assert _tree(application.work_root) == after_transition_tree


@pytest.mark.parametrize(
    "command",
    (
        {"operation": "run_all"},
        {"operation": "run_to", "node_id": "source"},
    ),
)
def test_ordinary_run_conflict_does_not_create_second_run(
    tmp_path: Path,
    command: object,
) -> None:
    store = _store(tmp_path, include_manual=True)
    application = _application(tmp_path, store)
    run_id, _, _ = _start_waiting_run(application)
    before = application.inspect(view_run_id=run_id)
    before_ids = tuple(item.run_id for item in before.run_summaries)
    before_tree = _tree(application.work_root)

    with pytest.raises(ProjectServiceError) as conflict:
        authoring_command(application, command)

    assert conflict.value.code == "E_PROJECT_SERVICE_RUN_CONFLICT"
    assert conflict.value.http_status == 409
    assert conflict.value.related_run_ids == (run_id,)
    after = application.inspect(view_run_id=run_id)
    assert tuple(item.run_id for item in after.run_summaries) == before_ids
    assert _tree(application.work_root) == before_tree


def test_abandon_rejects_running_automatic_attempt_without_db_or_fs_change(
    tmp_path: Path,
) -> None:
    """automatic attempt 真正执行时只能返回稳定冲突，不能发送取消或部分落库。"""

    store = _store(tmp_path, include_manual=False, filename="running-abandon.zniku")
    entered = threading.Event()
    release = threading.Event()

    def blocking_source(context: PythonAdapterContext) -> PythonAdapterResult:
        context.stdout_log_path.write_text("automatic running", encoding="utf-8")
        entered.set()
        if not release.wait(timeout=10):
            raise RuntimeError("合成 automatic adapter 等待释放超时")
        context.outputs[0].path.write_text("completed after release", encoding="utf-8")
        return PythonAdapterResult()

    application = ProjectServiceApplication(
        work_root=tmp_path / "running-work",
        python_adapters={_SOURCE_ADAPTER: blocking_source},
    )
    authoring_command(application, {"operation": "open_project", "path": str(store.path)})
    started = authoring_command(application, {"operation": "run_all"})
    assert started.active_run_id is not None
    run_id = started.active_run_id
    assert entered.wait(timeout=5)

    try:
        before_detail = application.inspect_run_detail(run_id)
        running = _latest(before_detail.run, "source")
        assert before_detail.run.state is RunState.RUNNING
        assert running.state is NodeRunState.RUNNING
        before_db = _sqlite_dump(store.path)
        before_tree = _tree(application.work_root)

        with pytest.raises(ProjectServiceError) as captured:
            authoring_command(application, {"operation": "abandon_run", "run_id": run_id})

        assert captured.value.code == "E_PROJECT_SERVICE_RUN_ACTIVE"
        assert captured.value.http_status == 409
        assert application.inspect_run_detail(run_id) == before_detail
        assert _sqlite_dump(store.path) == before_db
        assert _tree(application.work_root) == before_tree
    finally:
        release.set()
        assert application.wait_until_idle(timeout=5)


def test_abandon_run_preserves_completed_history_and_cancels_blocked_closure(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, include_manual=True, include_sink=True)
    application = _application(tmp_path, store)
    run_id, before_run, waiting = _start_waiting_run(application)
    source_before = _latest(before_run, "source")
    sink_before = _latest(before_run, "sink")
    assert source_before.state is NodeRunState.COMPLETED
    assert sink_before.state is NodeRunState.PENDING
    assert waiting.external_handoff is not None
    target_path = Path(waiting.external_handoff.output_targets[0].path)
    target_path.write_text("unfinished", encoding="utf-8")
    artifact_ids_before = tuple(
        item.artifact_id for item in application.inspect_run_detail(run_id).artifacts
    )
    assert not Path(sink_before.work_dir).exists()

    returned = authoring_command(application, {"operation": "abandon_run", "run_id": run_id})
    assert returned.active_run_id == run_id
    after = application.inspect_run_detail(run_id)
    source_after = _latest(after.run, "source")
    manual_after = _latest(after.run, "manual")
    sink_after = _latest(after.run, "sink")

    assert after.run.state is RunState.FAILED
    assert after.run.error is not None
    assert after.run.error.reason == "cancelled"
    assert source_after == source_before
    assert manual_after.state is NodeRunState.FAILED
    assert manual_after.error is not None
    assert manual_after.error.reason == "cancelled"
    assert manual_after.external_handoff == waiting.external_handoff
    assert sink_after.state is NodeRunState.FAILED
    assert sink_after.error is not None
    assert sink_after.error.reason == "cancelled"
    assert sink_after.started_at == sink_after.ended_at
    assert not Path(sink_after.work_dir).exists()
    assert target_path.read_text(encoding="utf-8") == "unfinished"
    assert tuple(item.artifact_id for item in after.artifacts) == artifact_ids_before


def test_abandon_after_rerun_only_cancels_latest_attempts_and_preserves_history(
    tmp_path: Path,
) -> None:
    """同一 Run 的 superseded attempts 是不可改历史，abandon 只收敛 latest attempts。"""

    store = _store(
        tmp_path,
        include_manual=True,
        include_sink=True,
        filename="rerun-abandon.zniku",
    )
    application = _application(tmp_path, store)
    run_id, _, _ = _start_waiting_run(application)

    rerun = authoring_command(
        application,
        {
            "operation": "rerun_from_here",
            "run_id": run_id,
            "node_id": "source",
        },
    )
    assert rerun.active_run_id == run_id
    assert application.wait_until_idle(timeout=5)
    before = application.inspect_run_detail(run_id)
    attempts_before = {(item.node_id, item.attempt): item for item in before.run.node_runs}
    assert set(attempts_before) == {
        ("source", 1),
        ("source", 2),
        ("manual", 1),
        ("manual", 2),
        ("sink", 1),
        ("sink", 2),
    }
    assert attempts_before[("manual", 1)].state is NodeRunState.WAITING_EXTERNAL
    assert attempts_before[("manual", 2)].state is NodeRunState.WAITING_EXTERNAL
    assert attempts_before[("sink", 1)].state is NodeRunState.PENDING
    assert attempts_before[("sink", 2)].state is NodeRunState.PENDING
    assert attempts_before[("manual", 1)].external_handoff is not None
    assert attempts_before[("manual", 2)].external_handoff is not None
    before_tree = _tree(application.work_root)

    authoring_command(application, {"operation": "abandon_run", "run_id": run_id})
    after = application.inspect_run_detail(run_id)
    attempts_after = {(item.node_id, item.attempt): item for item in after.run.node_runs}

    assert after.run.state is RunState.FAILED
    assert after.run.error is not None
    assert after.run.error.reason == "cancelled"
    for key in (("source", 1), ("source", 2), ("manual", 1), ("sink", 1)):
        assert attempts_after[key] == attempts_before[key]
    assert (
        attempts_after[("manual", 1)].external_handoff
        == attempts_before[("manual", 1)].external_handoff
    )
    assert attempts_after[("manual", 2)].state is NodeRunState.FAILED
    assert attempts_after[("manual", 2)].error is not None
    assert attempts_after[("manual", 2)].error.reason == "cancelled"
    assert (
        attempts_after[("manual", 2)].external_handoff
        == attempts_before[("manual", 2)].external_handoff
    )
    assert attempts_after[("sink", 2)].state is NodeRunState.FAILED
    assert attempts_after[("sink", 2)].error is not None
    assert attempts_after[("sink", 2)].error.reason == "cancelled"
    assert before.artifacts == after.artifacts
    assert _tree(application.work_root) == before_tree

    reopened = RuntimeRepository(ProjectStore.open(store.path)).get_run(run_id)
    assert reopened == after.run


def test_abandon_queued_pending_run_materializes_cancelled_attempts_without_io(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, include_manual=True, include_sink=True)
    snapshot = store.load()
    queued = Run.pending(
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
    )
    RuntimeRepository(store).create_run(queued)
    application = _application(tmp_path, store)
    before = application.inspect_run_detail(queued.run_id)
    assert before.run.state is RunState.PENDING
    assert before.run.node_runs == ()
    summary = next(
        item for item in application.inspect().run_summaries if item.run_id == queued.run_id
    )
    assert summary.node_count == 3
    assert summary.state_counts.pending == 3

    authoring_command(application, {"operation": "abandon_run", "run_id": queued.run_id})
    after = application.inspect_run_detail(queued.run_id)
    assert after.run.state is RunState.FAILED
    assert after.run.started_at == after.run.ended_at
    assert len(after.run.node_runs) == 3
    assert {item.node_id for item in after.run.node_runs} == {"source", "manual", "sink"}
    for item in after.run.node_runs:
        assert item.attempt == 1
        assert item.state is NodeRunState.FAILED
        assert item.error is not None
        assert item.error.reason == "cancelled"
        assert item.created_at == after.run.ended_at
        assert item.started_at == after.run.ended_at
        assert item.ended_at == after.run.ended_at
        assert not Path(item.work_dir).exists()
    assert after.artifacts == ()


@contextmanager
def _running_host(application: ProjectServiceApplication) -> Iterator[str]:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_project_service_handler(application),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        raw_host, port = server.server_address[:2]
        host = raw_host.decode("ascii") if isinstance(raw_host, bytes) else raw_host
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _get_json(url: str) -> tuple[int, dict[str, Any]]:
    try:
        with urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_routes_bind_status_detail_logs_history_and_readiness(tmp_path: Path) -> None:
    store = _store(tmp_path, include_manual=True)
    application = _application(tmp_path, store)
    terminal_id = _run_and_wait(
        application,
        {"operation": "run_to", "node_id": "source"},
    )
    waiting_id, waiting_run, waiting = _start_waiting_run(application)
    source = _latest(waiting_run, "source")

    with _running_host(application) as base_url:
        requests = (
            f"{base_url}/api/studio/status?view_run_id={waiting_id}",
            f"{base_url}/api/studio/runs?limit=20",
            f"{base_url}/api/studio/runs/{waiting_id}",
            (f"{base_url}/api/studio/runs/{waiting_id}/node-runs/{source.node_run_id}/logs"),
            (
                f"{base_url}/api/studio/runs/{waiting_id}/node-runs/"
                f"{waiting.node_run_id}/handoff-readiness?probe=false"
            ),
        )
        responses = [_get_json(url) for url in requests]

        assert all(status == 200 for status, _ in responses)
        assert all(payload["contract_version"] == "0.3.0" for _, payload in responses)
        assert responses[0][1]["run_summaries"][0]["run_id"] == waiting_id
        assert [item["run_id"] for item in responses[1][1]["run_summaries"]] == [terminal_id]
        assert responses[2][1]["run"]["run_id"] == waiting_id
        assert responses[3][1]["run_id"] == waiting_id
        assert responses[3][1]["log"]["node_run_id"] == source.node_run_id
        assert responses[4][1]["node_run_id"] == waiting.node_run_id
        assert responses[4][1]["targets"][0]["state"] == "missing"

        status, payload = _get_json(f"{base_url}/api/studio/status?unknown=true")
        assert status in {400, 422}
        assert payload["error"]["code"] != ""
