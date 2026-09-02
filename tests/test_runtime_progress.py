"""覆盖 v0.2.1 automatic progress 的合同、事务、限频与只读投影。"""

from __future__ import annotations

import math
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from zniku.graph import (
    CommandExecutorSpec,
    ExecutionMode,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.project import Project, ProjectStore
from zniku.project_service import ProjectServiceApplication
from zniku.runtime import (
    FailureReason,
    NodeRunState,
    ProgressError,
    PythonAdapterContext,
    PythonAdapterResult,
    RunState,
    RuntimeConflictError,
    RuntimeFailure,
    RuntimeRepositoryError,
    RuntimeService,
    utc_now,
)
from zniku.runtime.progress import BoundProgressReporter, ProgressInfrastructureError

_ADAPTER = "tests.progress:automatic"


@dataclass(slots=True)
class _Clocks:
    wall: datetime = datetime(2026, 9, 2, tzinfo=UTC)
    monotonic: float = 0.0

    def wall_clock(self) -> datetime:
        return self.wall

    def monotonic_clock(self) -> float:
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.monotonic += seconds
        self.wall += timedelta(seconds=seconds)


def _reporter(
    clocks: _Clocks,
) -> tuple[
    BoundProgressReporter,
    list[tuple[str, str, int]],
    list[tuple[str, str, int, float]],
    list[object],
    list[str],
]:
    validated: list[tuple[str, str, int]] = []
    persisted: list[tuple[str, str, int, float]] = []
    published: list[object] = []
    removed: list[str] = []
    reporter = BoundProgressReporter(
        "run-1",
        "node-run-1",
        2,
        validate_target=lambda run_id, node_run_id, attempt: validated.append(
            (run_id, node_run_id, attempt)
        ),
        persist=lambda run_id, node_run_id, attempt, fraction: persisted.append(
            (run_id, node_run_id, attempt, fraction)
        ),
        publish=published.append,
        remove=removed.append,
        wall_clock=clocks.wall_clock,
        monotonic_clock=clocks.monotonic_clock,
    )
    return reporter, validated, persisted, published, removed


def _definition(*, manual: bool = False) -> NodeDefinition:
    if manual:
        return NodeDefinition(
            type_id="test.progress.manual",
            version="1.0.0",
            output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
            execution_mode=ExecutionMode.MANUAL_EXTERNAL,
            executor=ManualExternalExecutorSpec(instructions="生成输出"),
        )
    return NodeDefinition(
        type_id="test.progress.automatic",
        version="1.0.0",
        output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter=_ADAPTER),
    )


def _store(
    tmp_path: Path, *, manual: bool = False, filename: str = "progress.zniku"
) -> ProjectStore:
    definition = _definition(manual=manual)
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="node",
                type_id=definition.type_id,
                definition_version=definition.version,
            ),
        )
    )
    return ProjectStore.create(
        tmp_path / filename,
        Project(project_id="project-progress", name="Progress 合成工程", graph=graph),
        (definition,),
    )


def test_reporter_validates_measurement_before_projection_or_persistence() -> None:
    invalid_calls: tuple[tuple[str, Callable[[BoundProgressReporter], None]], ...] = (
        ("E_PROGRESS_FRACTION_TYPE", lambda reporter: reporter.report(cast(Any, 1))),
        ("E_PROGRESS_FRACTION_NONFINITE", lambda reporter: reporter.report(math.nan)),
        ("E_PROGRESS_FRACTION_NONFINITE", lambda reporter: reporter.report(math.inf)),
        ("E_PROGRESS_FRACTION_RANGE", lambda reporter: reporter.report(-0.1)),
        ("E_PROGRESS_FRACTION_RANGE", lambda reporter: reporter.report(1.1)),
        (
            "E_PROGRESS_MEASUREMENT_PARTIAL",
            lambda reporter: reporter.report(0.5, current=1),
        ),
        (
            "E_PROGRESS_CURRENT_TYPE",
            lambda reporter: reporter.report(
                0.5,
                current=cast(Any, True),
                total=2,
                unit="items",
            ),
        ),
        (
            "E_PROGRESS_TOTAL_RANGE",
            lambda reporter: reporter.report(0.0, current=0, total=0, unit="items"),
        ),
        (
            "E_PROGRESS_CURRENT_RANGE",
            lambda reporter: reporter.report(1.0, current=2, total=1, unit="items"),
        ),
        (
            "E_PROGRESS_UNIT_INVALID",
            lambda reporter: reporter.report(
                0.5,
                current=1,
                total=2,
                unit=cast(Any, "seconds"),
            ),
        ),
        (
            "E_PROGRESS_FRACTION_MISMATCH",
            lambda reporter: reporter.report(0.500000002, current=1, total=2, unit="items"),
        ),
    )
    for expected_code, invoke in invalid_calls:
        reporter, validated, persisted, published, _removed = _reporter(_Clocks())
        with pytest.raises(ProgressError) as captured:
            invoke(reporter)
        assert captured.value.code == expected_code
        assert validated == []
        assert persisted == []
        assert published == []

    reporter, _validated, persisted, published, _removed = _reporter(_Clocks())
    reporter.report(0.5000000005, current=1, total=2, unit="items")
    assert persisted[-1][-1] == 0.5000000005
    assert len(published) == 1


@pytest.mark.parametrize("unit", ["frames", "bytes", "microseconds", "items"])
def test_reporter_accepts_only_the_four_frozen_measurement_units(unit: str) -> None:
    reporter, _validated, persisted, _published, _removed = _reporter(_Clocks())
    reporter.report(0.5, current=1, total=2, unit=cast(Any, unit))
    assert persisted[-1][-1] == 0.5


def test_reporter_is_monotonic_and_persists_first_time_or_delta_threshold() -> None:
    clocks = _Clocks()
    reporter, validated, persisted, published, _removed = _reporter(clocks)

    reporter.report(0.1)
    clocks.advance(0.1)
    reporter.report(0.105)
    clocks.advance(0.3)
    reporter.report(0.106)
    clocks.advance(0.01)
    reporter.report(0.13)
    clocks.advance(1.0)
    reporter.report(0.13)

    assert [item[-1] for item in persisted] == [0.1, 0.106, 0.13]
    assert len(validated) == 5
    assert len(published) == 5
    assert reporter.last_persisted_fraction == 0.13

    with pytest.raises(ProgressError) as regression:
        reporter.report(0.129)
    assert regression.value.code == "E_PROGRESS_REGRESSION"
    assert len(validated) == 5
    assert len(published) == 5


def test_reporter_persists_exact_decimal_delta_threshold() -> None:
    """十进制 0.01 的二进制表示误差不得误触发限频。"""

    clocks = _Clocks()
    reporter, _validated, persisted, _published, _removed = _reporter(clocks)

    reporter.report(0.1)
    clocks.advance(0.1)
    reporter.report(0.11)

    assert [item[-1] for item in persisted] == [0.1, 0.11]


@pytest.mark.parametrize("attempt", [True, 1.0])
def test_reporter_binding_requires_strict_positive_integer_attempt(attempt: object) -> None:
    clocks = _Clocks()
    with pytest.raises(ValueError, match="有效 run_id/node_run_id/attempt"):
        BoundProgressReporter(
            "run-1",
            "node-run-1",
            cast(Any, attempt),
            validate_target=lambda _run_id, _node_run_id, _attempt: None,
            persist=lambda _run_id, _node_run_id, _attempt, _fraction: None,
            publish=lambda _sample: None,
            remove=lambda _node_run_id: None,
            wall_clock=clocks.wall_clock,
            monotonic_clock=clocks.monotonic_clock,
        )


def test_reporter_close_is_idempotent_and_rejects_late_callback() -> None:
    reporter, _validated, persisted, published, removed = _reporter(_Clocks())
    reporter.report(0.25, current=1, total=4, unit="items")

    sample = reporter.close()
    assert reporter.close() == sample
    assert reporter.terminal is True
    assert removed == ["node-run-1"]
    with pytest.raises(ProgressError) as late:
        reporter.report(0.5)
    assert late.value.code == "E_PROGRESS_TERMINAL"
    assert len(persisted) == 1
    assert len(published) == 1


def test_reporter_serializes_report_against_close_and_rejects_terminal_mutation() -> None:
    """真实线程竞态必须由 reporter 锁串行化，终态之后不得再投影或持久化。"""

    clocks = _Clocks()
    validating = threading.Event()
    release_validation = threading.Event()
    close_started = threading.Event()
    persisted: list[float] = []
    published: list[object] = []
    removed: list[str] = []
    failures: list[BaseException] = []

    def validate(_run_id: str, _node_run_id: str, _attempt: int) -> None:
        validating.set()
        if not release_validation.wait(timeout=10):
            raise RuntimeError("synthetic validator timeout")

    reporter = BoundProgressReporter(
        "run-1",
        "node-run-1",
        1,
        validate_target=validate,
        persist=lambda _run_id, _node_run_id, _attempt, fraction: persisted.append(fraction),
        publish=published.append,
        remove=removed.append,
        wall_clock=clocks.wall_clock,
        monotonic_clock=clocks.monotonic_clock,
    )

    def report() -> None:
        try:
            reporter.report(0.2)
        except BaseException as error:
            failures.append(error)

    def close() -> None:
        close_started.set()
        try:
            reporter.close()
        except BaseException as error:
            failures.append(error)

    report_thread = threading.Thread(target=report)
    close_thread = threading.Thread(target=close)
    report_thread.start()
    assert validating.wait(timeout=10)
    close_thread.start()
    assert close_started.wait(timeout=10)
    release_validation.set()
    report_thread.join(timeout=10)
    close_thread.join(timeout=10)

    assert not report_thread.is_alive()
    assert not close_thread.is_alive()
    assert failures == []
    assert persisted == [0.2]
    assert len(published) == 1
    assert removed == ["node-run-1"]
    terminal_state = (tuple(persisted), tuple(published), tuple(removed))
    with pytest.raises(ProgressError) as late:
        reporter.report(0.3)
    assert late.value.code == "E_PROGRESS_TERMINAL"
    assert (tuple(persisted), tuple(published), tuple(removed)) == terminal_state


def test_reporter_preserves_infrastructure_failure_cause() -> None:
    clocks = _Clocks()
    published: list[object] = []
    failure = RuntimeError("synthetic repository failure")
    reporter = BoundProgressReporter(
        "run-1",
        "node-run-1",
        1,
        validate_target=lambda _run_id, _node_run_id, _attempt: None,
        persist=lambda _run_id, _node_run_id, _attempt, _fraction: (_ for _ in ()).throw(failure),
        publish=published.append,
        remove=lambda _node_run_id: None,
        wall_clock=clocks.wall_clock,
        monotonic_clock=clocks.monotonic_clock,
    )

    with pytest.raises(ProgressInfrastructureError) as captured:
        reporter.report(0.2)
    assert captured.value.cause is failure
    assert len(published) == 1


@pytest.mark.parametrize("invalid", [True, math.nan, math.inf, -0.1, 1.1])
def test_repository_progress_rejects_invalid_values_and_terminal_regression(
    tmp_path: Path,
    invalid: object,
) -> None:
    service = RuntimeService(
        _store(tmp_path, filename=f"invalid-{id(invalid)}.zniku"), tmp_path / "work"
    )
    run = service.create_run()
    pending = run.node_runs[0]
    running = service.repository.transition_node_run(
        pending.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=utc_now(),
    )
    assert running.progress is None

    with pytest.raises(RuntimeConflictError) as captured:
        service.repository.update_progress(pending.node_run_id, cast(Any, invalid))
    assert captured.value.code == "E_NODE_RUN_PROGRESS_INVALID"
    assert service.repository.get_node_run(pending.node_run_id).progress is None

    service.repository.update_progress(pending.node_run_id, 0.5)
    with pytest.raises(RuntimeConflictError) as regression:
        service.repository.transition_node_run(
            pending.node_run_id,
            NodeRunState.FAILED,
            occurred_at=utc_now(),
            error=RuntimeFailure(reason=FailureReason.EXECUTION_ERROR, message="synthetic"),
            progress=0.4,
        )
    assert regression.value.code == "E_NODE_RUN_PROGRESS_REGRESSION"
    unchanged = service.repository.get_node_run(pending.node_run_id)
    assert unchanged.state is NodeRunState.RUNNING
    assert unchanged.progress == 0.5


def test_service_success_failure_and_late_callback_progress_semantics(tmp_path: Path) -> None:
    late_reporters: list[object] = []
    should_fail = [True]

    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        assert context.progress is not None
        context.progress.report(0.25, current=1, total=4, unit="items")
        late_reporters.append(context.progress)
        if should_fail[0]:
            context.progress.report(0.2)
        context.outputs[0].path.write_text("complete", encoding="utf-8")
        return PythonAdapterResult()

    service = RuntimeService(
        _store(tmp_path),
        tmp_path / "work",
        python_adapters={_ADAPTER: adapter},
    )
    first = service.create_run()
    failed_run = service.run_until_blocked(first.run_id)
    failed = failed_run.node_runs[0]
    assert failed.state is NodeRunState.FAILED
    assert failed.error is not None
    assert failed.error.reason is FailureReason.EXECUTION_ERROR
    assert "E_PROGRESS_REGRESSION" in failed.error.message
    assert failed.progress == 0.25
    assert service.progress_snapshot(first.run_id) == ()

    reporter = cast(Any, late_reporters[-1])
    with pytest.raises(ProgressError) as late:
        reporter.report(0.5)
    assert late.value.code == "E_PROGRESS_TERMINAL"
    assert service.repository.get_node_run(failed.node_run_id) == failed

    should_fail[0] = False
    rerun = service.rerun_from_start(first.run_id, "node")
    completed = rerun.node_runs[-1]
    assert completed.state is NodeRunState.COMPLETED
    assert completed.progress == 1.0
    assert service.progress_snapshot(first.run_id) == ()


def test_repository_progress_binding_and_terminal_state_fail_closed(tmp_path: Path) -> None:
    service = RuntimeService(_store(tmp_path), tmp_path / "work")
    run = service.create_run()
    pending = run.node_runs[0]
    service.repository.transition_node_run(
        pending.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=utc_now(),
    )

    with pytest.raises(RuntimeConflictError) as wrong_attempt:
        service.repository.update_progress(
            pending.node_run_id,
            0.25,
            run_id=run.run_id,
            attempt=2,
        )
    assert wrong_attempt.value.code == "E_PROGRESS_BINDING"
    assert service.repository.get_node_run(pending.node_run_id).progress is None

    for binding in ({"run_id": run.run_id}, {"attempt": 1}):
        with pytest.raises(RuntimeConflictError) as partial:
            service.repository.update_progress(
                pending.node_run_id,
                0.25,
                **cast(Any, binding),
            )
        assert partial.value.code == "E_PROGRESS_BINDING_PARTIAL"

    for invalid_attempt in (True, 1.0):
        with pytest.raises(RuntimeConflictError) as invalid_binding:
            service.repository.update_progress(
                pending.node_run_id,
                0.25,
                run_id=run.run_id,
                attempt=cast(Any, invalid_attempt),
            )
        assert invalid_binding.value.code == "E_PROGRESS_BINDING"
    assert service.repository.get_node_run(pending.node_run_id).progress is None

    failed = service.repository.transition_node_run(
        pending.node_run_id,
        NodeRunState.FAILED,
        occurred_at=utc_now(),
        error=RuntimeFailure(reason=FailureReason.EXECUTION_ERROR, message="synthetic"),
        progress=0.3,
    )
    assert failed.progress == 0.3
    with pytest.raises(RuntimeConflictError) as terminal:
        service.repository.inspect_progress_target(run.run_id, pending.node_run_id, 1)
    assert terminal.value.code == "E_PROGRESS_NOT_RUNNING"


@pytest.mark.parametrize(
    ("manual", "state"),
    (
        (False, NodeRunState.RUNNING),
        (True, NodeRunState.WAITING_EXTERNAL),
    ),
)
def test_pending_start_rejects_progress_before_any_projection(
    tmp_path: Path,
    manual: bool,
    state: NodeRunState,
) -> None:
    service = RuntimeService(
        _store(tmp_path, manual=manual, filename=f"start-{state.value}.zniku"),
        tmp_path / f"work-{state.value}",
    )
    pending = service.create_run().node_runs[0]

    with pytest.raises(RuntimeConflictError) as captured:
        service.repository.transition_node_run(
            pending.node_run_id,
            state,
            occurred_at=utc_now(),
            progress=0.1,
        )
    assert captured.value.code == "E_NODE_RUN_START_PROGRESS_UNEXPECTED"
    unchanged = service.repository.get_node_run(pending.node_run_id)
    assert unchanged.state is NodeRunState.PENDING
    assert unchanged.progress is None


def test_runner_failure_flushes_last_throttled_sample_atomically(tmp_path: Path) -> None:
    clocks = _Clocks()
    services: list[RuntimeService] = []

    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        assert context.progress is not None
        context.progress.report(0.2)
        node_run = services[0].repository.get_node_run(context.node_run_id)
        assert node_run.progress == 0.2
        clocks.advance(0.1)
        context.progress.report(0.205)
        throttled = services[0].repository.get_node_run(context.node_run_id)
        assert throttled.progress == 0.2
        raise RuntimeError("synthetic runner failure")

    service = RuntimeService(
        _store(tmp_path),
        tmp_path / "work",
        python_adapters={_ADAPTER: adapter},
        progress_wall_clock=clocks.wall_clock,
        progress_monotonic_clock=clocks.monotonic_clock,
    )
    services.append(service)
    run = service.run_until_blocked(service.create_run().run_id)
    failed = run.node_runs[0]

    assert failed.state is NodeRunState.FAILED
    assert failed.error is not None
    assert failed.error.reason is FailureReason.EXECUTION_ERROR
    assert failed.progress == 0.205
    assert service.progress_snapshot(run.run_id) == ()


def test_repository_progress_failure_keeps_attempt_running_for_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite 写失败不能伪造 failed/completed；恢复入口仍拥有 running attempt。"""

    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        assert context.progress is not None
        context.progress.report(0.2)
        raise AssertionError("progress persistence failure 应立即中止 adapter")

    service = RuntimeService(
        _store(tmp_path),
        tmp_path / "work",
        python_adapters={_ADAPTER: adapter},
    )

    def fail_update(*_args: object, **_kwargs: object) -> None:
        raise RuntimeRepositoryError("E_SYNTHETIC_PROGRESS_WRITE", "synthetic write failure")

    monkeypatch.setattr(service.repository, "update_progress", fail_update)
    run = service.create_run()
    with pytest.raises(RuntimeRepositoryError) as captured:
        service.run_until_blocked(run.run_id)
    assert captured.value.code == "E_SYNTHETIC_PROGRESS_WRITE"
    running = service.repository.get_node_run(run.node_runs[0].node_run_id)
    assert running.state is NodeRunState.RUNNING
    assert running.progress is None
    assert service.progress_snapshot(run.run_id) == ()


def test_manual_external_stays_indeterminate_without_reporter(tmp_path: Path) -> None:
    service = RuntimeService(
        _store(tmp_path, manual=True),
        tmp_path / "work",
    )
    run = service.create_run()
    blocked = service.run_until_blocked(run.run_id)
    waiting = blocked.node_runs[0]

    assert waiting.state is NodeRunState.WAITING_EXTERNAL
    assert waiting.progress is None
    assert service.progress_snapshot(run.run_id) == ()

    with pytest.raises(RuntimeConflictError) as forged:
        service.repository.transition_node_run(
            waiting.node_run_id,
            NodeRunState.FAILED,
            occurred_at=utc_now(),
            error=RuntimeFailure(
                reason=FailureReason.EXECUTION_ERROR,
                message="synthetic manual failure",
            ),
            progress=0.5,
        )
    assert forged.value.code == "E_NODE_RUN_MANUAL_PROGRESS_UNEXPECTED"
    unchanged = service.repository.get_node_run(waiting.node_run_id)
    assert unchanged.state is NodeRunState.WAITING_EXTERNAL
    assert unchanged.progress is None


def test_command_automatic_stays_indeterminate_while_real_process_is_running(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "command-entered.txt"
    release = tmp_path / "command-release.txt"
    script = (
        "import pathlib,sys,time;"
        "marker=pathlib.Path(sys.argv[1]);release=pathlib.Path(sys.argv[2]);"
        "output=pathlib.Path(sys.argv[3]);marker.write_text('entered',encoding='utf-8');"
        "deadline=time.monotonic()+10;"
        "\nwhile not release.exists() and time.monotonic()<deadline: time.sleep(0.01)"
        "\nif not release.exists(): raise SystemExit(9)"
        "\noutput.write_text('complete',encoding='utf-8')"
    )
    definition = NodeDefinition(
        type_id="test.progress.command",
        version="1.0.0",
        output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=CommandExecutorSpec(
            executable=sys.executable,
            argv=("-c", script, str(marker), str(release), "{output:out}"),
        ),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="command",
                type_id=definition.type_id,
                definition_version=definition.version,
            ),
        )
    )
    store = ProjectStore.create(
        tmp_path / "command.zniku",
        Project(project_id="project-command", name="Command 进度工程", graph=graph),
        (definition,),
    )
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    application.command({"operation": "open_project", "path": str(store.path)})
    status = application.command({"operation": "run_all"})
    run_id = status.active_run_id
    assert run_id is not None

    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.is_file()
    live = application.inspect_run_detail(run_id)
    assert live.run.node_runs[0].state is NodeRunState.RUNNING
    assert live.run.node_runs[0].progress is None
    assert live.progress_samples == ()

    release.write_text("release", encoding="utf-8")
    assert application.wait_until_idle(timeout=10)
    terminal = application.inspect_run_detail(run_id)
    assert terminal.run.state is RunState.COMPLETED
    assert terminal.run.node_runs[0].progress == 1.0
    assert terminal.progress_samples == ()


def test_project_service_exposes_only_live_unpersisted_projection(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    clocks = _Clocks()

    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        assert context.progress is not None
        context.progress.report(0.2)
        clocks.advance(0.1)
        context.progress.report(0.205)
        entered.set()
        if not release.wait(timeout=10):
            raise RuntimeError("synthetic progress adapter timeout")
        context.outputs[0].path.write_text("complete", encoding="utf-8")
        return PythonAdapterResult()

    store = _store(tmp_path)
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters={_ADAPTER: adapter},
        progress_wall_clock=clocks.wall_clock,
        progress_monotonic_clock=clocks.monotonic_clock,
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    status = application.command({"operation": "run_all"})
    run_id = status.active_run_id
    assert run_id is not None
    assert entered.wait(timeout=10)

    live = application.inspect_run_detail(run_id)
    running = live.run.node_runs[0]
    assert running.state is NodeRunState.RUNNING
    assert running.progress == 0.2
    assert len(live.progress_samples) == 1
    assert live.progress_samples[0].node_run_id == running.node_run_id
    assert live.progress_samples[0].fraction == 0.205
    assert live.progress_samples[0].fraction >= running.progress

    release.set()
    assert application.wait_until_idle(timeout=10)
    terminal = application.inspect_run_detail(run_id)
    assert terminal.run.state is RunState.COMPLETED
    assert terminal.run.node_runs[0].progress == 1.0
    assert terminal.progress_samples == ()


def test_explicit_recovery_closes_reporter_and_removes_projection(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    clocks = _Clocks()
    reporter_values: list[object] = []
    worker_failures: list[BaseException] = []

    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        assert context.progress is not None
        context.progress.report(0.3)
        clocks.advance(0.1)
        context.progress.report(0.305)
        reporter_values.append(context.progress)
        entered.set()
        release.wait(timeout=10)
        context.outputs[0].path.write_text("late", encoding="utf-8")
        return PythonAdapterResult()

    service = RuntimeService(
        _store(tmp_path),
        tmp_path / "work",
        python_adapters={_ADAPTER: adapter},
        progress_wall_clock=clocks.wall_clock,
        progress_monotonic_clock=clocks.monotonic_clock,
    )
    run = service.create_run()

    def execute() -> None:
        try:
            service.run_until_blocked(run.run_id)
        except BaseException as error:
            worker_failures.append(error)

    worker = threading.Thread(target=execute)
    worker.start()
    assert entered.wait(timeout=10)
    assert service.progress_snapshot(run.run_id)[0].fraction == 0.305
    assert service.repository.get_node_run(run.node_runs[0].node_run_id).progress == 0.3

    with pytest.raises(RuntimeConflictError) as wrong_binding:
        service.repository.recover_interrupted(
            recovered_at=utc_now(),
            final_progress={
                run.node_runs[0].node_run_id: ("wrong-run", 1, 0.305),
            },
        )
    assert wrong_binding.value.code == "E_PROGRESS_BINDING"
    assert (
        service.repository.get_node_run(run.node_runs[0].node_run_id).state is NodeRunState.RUNNING
    )

    with pytest.raises(RuntimeConflictError) as regression:
        service.repository.recover_interrupted(
            recovered_at=utc_now(),
            final_progress={
                run.node_runs[0].node_run_id: (run.run_id, 1, 0.299),
            },
        )
    assert regression.value.code == "E_NODE_RUN_PROGRESS_REGRESSION"
    assert (
        service.repository.get_node_run(run.node_runs[0].node_run_id).state is NodeRunState.RUNNING
    )

    with pytest.raises(RuntimeConflictError) as unknown_target:
        service.repository.recover_interrupted(
            recovered_at=utc_now(),
            final_progress={"unknown-node-run": (run.run_id, 1, 0.305)},
        )
    assert unknown_target.value.code == "E_PROGRESS_RECOVERY_TARGET"

    with pytest.raises(RuntimeConflictError) as malformed_binding:
        service.repository.recover_interrupted(
            recovered_at=utc_now(),
            final_progress=cast(
                Any,
                {run.node_runs[0].node_run_id: (run.run_id, 1)},
            ),
        )
    assert malformed_binding.value.code == "E_PROGRESS_RECOVERY_BINDING"
    assert (
        service.repository.get_node_run(run.node_runs[0].node_run_id).state is NodeRunState.RUNNING
    )

    recovered = service.recover_interrupted()
    assert [item.node_run_id for item in recovered] == [run.node_runs[0].node_run_id]
    assert recovered[0].state is NodeRunState.FAILED
    assert recovered[0].progress == 0.305
    assert service.progress_snapshot(run.run_id) == ()
    with pytest.raises(ProgressError) as late:
        cast(Any, reporter_values[0]).report(0.4)
    assert late.value.code == "E_PROGRESS_TERMINAL"

    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert worker_failures
