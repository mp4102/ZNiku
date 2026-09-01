"""验证 Phase 2 Runtime Service 的 snapshot、执行、复用与从头重跑语义。"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from zniku.graph import (
    CommandExecutorSpec,
    Edge,
    ExecutionMode,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
    ValidatorSpec,
)
from zniku.project import Project, ProjectStore
from zniku.runtime import (
    FailureReason,
    FrameRange,
    ManualSubmission,
    NodeRun,
    NodeRunState,
    NodeValidatorContext,
    NodeValidatorResult,
    ProducedOutput,
    PythonAdapterContext,
    PythonAdapterResult,
    RunnerCancelled,
    RunnerInterrupted,
    RunState,
    RuntimeDataError,
    RuntimeService,
    RuntimeServiceError,
    StaleReason,
    utc_now,
)


def _data_output() -> tuple[PortSpec, ...]:
    return (PortSpec(port_id="out", data_type="DataFile"),)


def _data_input() -> tuple[PortSpec, ...]:
    return (PortSpec(port_id="in", data_type="DataFile", required=True),)


def _python_definition(
    type_id: str,
    adapter: str,
    *,
    inputs: tuple[PortSpec, ...] = (),
    outputs: tuple[PortSpec, ...] = (),
    with_text_parameter: bool = False,
) -> NodeDefinition:
    schema = None
    if with_text_parameter:
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }
    values: dict[str, object] = {
        "type_id": type_id,
        "version": "1.0.0",
        "input_ports": inputs,
        "output_ports": outputs,
        "execution_mode": ExecutionMode.AUTOMATIC,
        "executor": PythonExecutorSpec(adapter=adapter),
    }
    if schema is not None:
        values["parameter_schema"] = schema
    return NodeDefinition.model_validate(values)


def _manual_definition(type_id: str = "test.manual") -> NodeDefinition:
    return NodeDefinition(
        type_id=type_id,
        version="1.0.0",
        input_ports=_data_input(),
        output_ports=_data_output(),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(instructions="复制输入并提交输出"),
    )


def _store(
    tmp_path: Path,
    graph: Graph,
    definitions: tuple[NodeDefinition, ...],
    *,
    filename: str = "runtime.zniku",
) -> ProjectStore:
    return ProjectStore.create(
        tmp_path / filename,
        Project(project_id="project-runtime", name="Runtime 合成工程", graph=graph),
        definitions,
    )


def _latest_attempt(run_node_runs: tuple[NodeRun, ...], node_id: str) -> NodeRun:
    return max(
        (item for item in run_node_runs if item.node_id == node_id),
        key=lambda item: item.attempt,
    )


def _source_adapter(calls: list[str]) -> Callable[[PythonAdapterContext], PythonAdapterResult]:
    def run(context: PythonAdapterContext) -> PythonAdapterResult:
        text = str(context.node.parameters.get("text", context.node.node_id))
        context.outputs[0].path.write_text(text, encoding="utf-8")
        calls.append(context.node_run_id)
        return PythonAdapterResult()

    return run


def _copy_adapter(calls: list[str]) -> Callable[[PythonAdapterContext], PythonAdapterResult]:
    def run(context: PythonAdapterContext) -> PythonAdapterResult:
        content = context.inputs[0].path.read_text(encoding="utf-8")
        context.outputs[0].path.write_text(content + "-copied", encoding="utf-8")
        calls.append(context.node_run_id)
        return PythonAdapterResult()

    return run


def _manual_graph() -> tuple[Graph, tuple[NodeDefinition, ...]]:
    source = _python_definition(
        "test.source",
        "tests:source",
        outputs=_data_output(),
        with_text_parameter=True,
    )
    transform = _python_definition(
        "test.copy",
        "tests:copy",
        inputs=_data_input(),
        outputs=_data_output(),
    )
    manual = _manual_definition()
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id=source.type_id,
                definition_version=source.version,
                parameters={"text": "alpha"},
            ),
            NodeInstance(
                node_id="copy",
                type_id=transform.type_id,
                definition_version=transform.version,
            ),
            NodeInstance(
                node_id="manual",
                type_id=manual.type_id,
                definition_version=manual.version,
            ),
        ),
        edges=(
            Edge(
                source_node_id="source",
                source_port_id="out",
                target_node_id="copy",
                target_port_id="in",
            ),
            Edge(
                source_node_id="copy",
                source_port_id="out",
                target_node_id="manual",
                target_port_id="in",
            ),
        ),
    )
    return graph, (source, transform, manual)


def test_service_executes_manual_handoff_then_reuses_exact_results(tmp_path: Path) -> None:
    graph, definitions = _manual_graph()
    store = _store(tmp_path, graph, definitions)
    source_calls: list[str] = []
    copy_calls: list[str] = []
    service = RuntimeService(
        store,
        tmp_path / "work",
        python_adapters={
            "tests:source": _source_adapter(source_calls),
            "tests:copy": _copy_adapter(copy_calls),
        },
    )

    first = service.create_run()
    assert len(first.node_runs) == 3
    assert all(item.state is NodeRunState.PENDING for item in first.node_runs)
    first = service.run_until_blocked(first.run_id)
    manual = _latest_attempt(first.node_runs, "manual")
    assert first.state is RunState.RUNNING
    assert manual.state is NodeRunState.WAITING_EXTERNAL
    assert manual.external_handoff is not None
    target = Path(manual.external_handoff.output_targets[0].path)
    target.write_text("operator-result", encoding="utf-8")

    first = service.submit_external(manual.node_run_id)
    assert first.state is RunState.COMPLETED
    assert source_calls and copy_calls
    first_heads = {
        node_id: _latest_attempt(first.node_runs, node_id)
        for node_id in ("source", "copy", "manual")
    }

    second = service.create_run()
    second = service.run_until_blocked(second.run_id)
    assert second.state is RunState.COMPLETED
    assert len(source_calls) == 1
    assert len(copy_calls) == 1
    for node_id in ("source", "copy", "manual"):
        reused = _latest_attempt(second.node_runs, node_id)
        assert reused.state is NodeRunState.COMPLETED
        assert reused.reused_from_result_id is not None
        assert reused.output_artifact_ids == first_heads[node_id].output_artifact_ids


def test_service_persists_runner_external_path_and_frame_range(tmp_path: Path) -> None:
    """Runner 媒体元数据必须原样进入 Project 的普通 Artifact 记录。"""

    definition = _python_definition(
        "test.external-source",
        "tests:external-source",
        outputs=_data_output(),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id=definition.type_id,
                definition_version=definition.version,
            ),
        )
    )
    external = tmp_path / "external-source.bin"
    external.write_bytes(b"source")

    def adapter(_context: PythonAdapterContext) -> PythonAdapterResult:
        return PythonAdapterResult(
            outputs=(
                ProducedOutput(
                    port_id="out",
                    path=external,
                    frame_range=FrameRange(start_frame=0, end_frame=42),
                    allow_external=True,
                ),
            )
        )

    service = RuntimeService(
        _store(tmp_path, graph, (definition,), filename="external-frame-range.zniku"),
        tmp_path / "external-work",
        python_adapters={"tests:external-source": adapter},
    )
    run = service.run_until_blocked(service.create_run().run_id)
    node_run = _latest_attempt(run.node_runs, "source")
    artifact = service.repository.get_artifact(node_run.output_artifact_ids[0])

    assert artifact.path == str(external.resolve())
    assert artifact.frame_range == FrameRange(start_frame=0, end_frame=42)


def test_manual_handoff_respects_definition_input_port_order(tmp_path: Path) -> None:
    """持久绑定使用 canonical edge 顺序，插件输入仍保持 NodeDefinition 声明顺序。"""

    source = _python_definition("test.order-source", "tests:order-source", outputs=_data_output())
    manual = NodeDefinition(
        type_id="test.order-manual",
        version="1.0.0",
        input_ports=(
            PortSpec(port_id="z", data_type="DataFile", required=True),
            PortSpec(port_id="a", data_type="DataFile", required=True),
        ),
        output_ports=_data_output(),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(instructions="按声明顺序接收输入"),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source-z", type_id=source.type_id, definition_version=source.version
            ),
            NodeInstance(
                node_id="source-a", type_id=source.type_id, definition_version=source.version
            ),
            NodeInstance(
                node_id="manual", type_id=manual.type_id, definition_version=manual.version
            ),
        ),
        edges=(
            Edge(
                source_node_id="source-z",
                source_port_id="out",
                target_node_id="manual",
                target_port_id="z",
            ),
            Edge(
                source_node_id="source-a",
                source_port_id="out",
                target_node_id="manual",
                target_port_id="a",
            ),
        ),
    )
    service = RuntimeService(
        _store(tmp_path, graph, (source, manual), filename="input-order.zniku"),
        tmp_path / "input-order-work",
        python_adapters={"tests:order-source": _source_adapter([])},
    )

    waiting_run = service.run_until_blocked(service.create_run().run_id)
    waiting = _latest_attempt(waiting_run.node_runs, "manual")
    assert waiting.state is NodeRunState.WAITING_EXTERNAL
    assert waiting.external_handoff is not None
    Path(waiting.external_handoff.output_targets[0].path).write_text("done", encoding="utf-8")

    completed = service.submit_external(waiting.node_run_id)

    assert completed.state is RunState.COMPLETED
    assert _latest_attempt(completed.node_runs, "manual").state is NodeRunState.COMPLETED


def test_rerun_creates_new_attempts_and_supersedes_old_handoff(tmp_path: Path) -> None:
    graph, definitions = _manual_graph()
    store = _store(tmp_path, graph, definitions)
    source_calls: list[str] = []
    copy_calls: list[str] = []
    service = RuntimeService(
        store,
        tmp_path / "work",
        python_adapters={
            "tests:source": _source_adapter(source_calls),
            "tests:copy": _copy_adapter(copy_calls),
        },
    )
    run = service.run_until_blocked(service.create_run().run_id)
    source_one = _latest_attempt(run.node_runs, "source")
    manual_one = _latest_attempt(run.node_runs, "manual")
    assert manual_one.state is NodeRunState.WAITING_EXTERNAL

    rerun = service.rerun_from_start(run.run_id, "source")
    source_two = _latest_attempt(rerun.node_runs, "source")
    manual_two = _latest_attempt(rerun.node_runs, "manual")
    assert source_two.attempt == 2
    assert manual_two.attempt == 2
    assert source_two.output_artifact_ids != source_one.output_artifact_ids
    assert len(source_calls) == 2
    assert len(copy_calls) == 2
    assert manual_one.state is NodeRunState.WAITING_EXTERNAL
    assert manual_two.state is NodeRunState.WAITING_EXTERNAL
    with pytest.raises(RuntimeServiceError, match="E_SERVICE_HANDOFF_SUPERSEDED"):
        service.submit_external(manual_one.node_run_id)

    assert manual_two.external_handoff is not None
    Path(manual_two.external_handoff.output_targets[0].path).write_text(
        "new-result", encoding="utf-8"
    )
    completed = service.submit_external(manual_two.node_run_id)
    assert completed.state is RunState.COMPLETED
    assert _latest_attempt(completed.node_runs, "manual").attempt == 2


def test_failed_node_keeps_run_open_and_independent_branch_continues(tmp_path: Path) -> None:
    failing = _python_definition("test.fail", "tests:fail", outputs=_data_output())
    independent = _python_definition(
        "test.independent", "tests:independent", outputs=_data_output()
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="fail", type_id=failing.type_id, definition_version=failing.version
            ),
            NodeInstance(
                node_id="independent",
                type_id=independent.type_id,
                definition_version=independent.version,
            ),
        )
    )
    store = _store(tmp_path, graph, (failing, independent))
    should_fail = [True]

    def fail_then_succeed(context: PythonAdapterContext) -> PythonAdapterResult:
        if should_fail[0]:
            raise RuntimeError("synthetic failure")
        context.outputs[0].path.write_text("recovered", encoding="utf-8")
        return PythonAdapterResult()

    service = RuntimeService(
        store,
        tmp_path / "work",
        python_adapters={
            "tests:fail": fail_then_succeed,
            "tests:independent": _source_adapter([]),
        },
    )
    run = service.run_until_blocked(service.create_run().run_id)
    failed = _latest_attempt(run.node_runs, "fail")
    independent_run = _latest_attempt(run.node_runs, "independent")
    assert run.state is RunState.RUNNING
    assert failed.state is NodeRunState.FAILED
    assert failed.error is not None
    assert failed.error.reason is FailureReason.EXECUTION_ERROR
    assert independent_run.state is NodeRunState.COMPLETED

    should_fail[0] = False
    completed = service.rerun_from_start(run.run_id, "fail")
    assert completed.state is RunState.COMPLETED
    assert _latest_attempt(completed.node_runs, "fail").attempt == 2
    assert _latest_attempt(completed.node_runs, "independent").attempt == 1


def test_manual_prepare_failure_becomes_failed_attempt_and_can_rerun(tmp_path: Path) -> None:
    """handoff 尚未建立时失败，不得借用 automatic running 过渡或留下 pending。"""

    manual = NodeDefinition(
        type_id="test.manual-prepare-failure",
        version="1.0.0",
        output_ports=_data_output(),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(instructions="提交合成输出"),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="manual",
                type_id=manual.type_id,
                definition_version=manual.version,
            ),
        )
    )
    service = RuntimeService(_store(tmp_path, graph, (manual,)), tmp_path / "work")
    run = service.create_run()
    first = _latest_attempt(run.node_runs, "manual")
    # 预占 attempt 目录，合成 prepare_manual 的安全失败，不触碰任何用户文件。
    Path(first.work_dir).mkdir(parents=True)

    failed_run = service.run_until_blocked(run.run_id)

    failed = _latest_attempt(failed_run.node_runs, "manual")
    assert failed_run.state is RunState.RUNNING
    assert failed.state is NodeRunState.FAILED
    assert failed.error is not None
    assert failed.error.reason is FailureReason.EXECUTION_ERROR
    assert failed.started_at == failed.ended_at
    assert failed.progress == 0.0
    assert failed.external_handoff is None

    rerun = service.rerun_from_start(run.run_id, "manual")
    second = _latest_attempt(rerun.node_runs, "manual")
    assert second.attempt == 2
    assert second.state is NodeRunState.WAITING_EXTERNAL
    assert second.external_handoff is not None


@pytest.mark.parametrize(
    ("control", "expected_reason"),
    (
        ("cancelled", FailureReason.CANCELLED),
        ("interrupted", FailureReason.INTERRUPTED),
    ),
)
def test_manual_validator_control_signal_fails_waiting_attempt(
    tmp_path: Path,
    control: str,
    expected_reason: FailureReason,
) -> None:
    """人工提交时的取消或中断只终止当前 attempt，并形成可重跑失败记录。"""

    manual = NodeDefinition(
        type_id=f"test.manual-{control}",
        version="1.0.0",
        output_ports=_data_output(),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(instructions="提交合成输出"),
        validator=ValidatorSpec(adapter=f"tests:manual-{control}"),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="manual",
                type_id=manual.type_id,
                definition_version=manual.version,
            ),
        )
    )

    def validator(_context: NodeValidatorContext) -> NodeValidatorResult:
        if control == "cancelled":
            raise RunnerCancelled("synthetic cancel")
        raise RunnerInterrupted("synthetic interrupt")

    service = RuntimeService(
        _store(tmp_path, graph, (manual,), filename=f"manual-{control}.zniku"),
        tmp_path / f"manual-{control}-work",
        validators={f"tests:manual-{control}": validator},
    )
    waiting_run = service.run_until_blocked(service.create_run().run_id)
    waiting = _latest_attempt(waiting_run.node_runs, "manual")
    assert waiting.external_handoff is not None
    Path(waiting.external_handoff.output_targets[0].path).write_text("candidate", encoding="utf-8")

    failed_run = service.submit_external(waiting.node_run_id)

    failed = _latest_attempt(failed_run.node_runs, "manual")
    assert failed_run.state is RunState.RUNNING
    assert failed.state is NodeRunState.FAILED
    assert failed.error is not None
    assert failed.error.reason is expected_reason
    assert failed.output_artifact_ids == ()


def test_malformed_manual_submission_fails_waiting_attempt(tmp_path: Path) -> None:
    """公开 Submit 边界拒绝畸形字段，并把 waiting attempt 收敛为失败。"""

    manual = NodeDefinition(
        type_id="test.manual-malformed-submission",
        version="1.0.0",
        output_ports=_data_output(),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="manual",
                type_id=manual.type_id,
                definition_version=manual.version,
            ),
        )
    )
    service = RuntimeService(
        _store(tmp_path, graph, (manual,), filename="manual-malformed.zniku"),
        tmp_path / "manual-malformed-work",
    )
    waiting_run = service.run_until_blocked(service.create_run().run_id)
    waiting = _latest_attempt(waiting_run.node_runs, "manual")
    assert waiting.external_handoff is not None

    failed_run = service.submit_external(
        waiting.node_run_id,
        submission=ManualSubmission(outputs=(cast(Any, object()),)),
    )

    failed = _latest_attempt(failed_run.node_runs, "manual")
    assert failed.state is NodeRunState.FAILED
    assert failed.error is not None
    assert failed.error.reason is FailureReason.EXTERNAL_SUBMISSION_INVALID
    assert failed.output_artifact_ids == ()


def test_service_startup_recovers_running_as_interrupted_and_can_rerun(tmp_path: Path) -> None:
    source = _python_definition("test.recovery", "tests:recovery", outputs=_data_output())
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source", type_id=source.type_id, definition_version=source.version
            ),
        )
    )
    store = _store(tmp_path, graph, (source,))
    first_service = RuntimeService(
        store,
        tmp_path / "work",
        python_adapters={"tests:recovery": _source_adapter([])},
    )
    run = first_service.create_run()
    pending = _latest_attempt(run.node_runs, "source")
    first_service.repository.transition_node_run(
        pending.node_run_id,
        NodeRunState.RUNNING,
        occurred_at=utc_now(),
        log_path=str(Path(pending.work_dir) / "logs"),
    )

    restarted = RuntimeService(
        store,
        tmp_path / "work",
        python_adapters={"tests:recovery": _source_adapter([])},
    )
    recovered = restarted.repository.get_node_run(pending.node_run_id)
    assert recovered.state is NodeRunState.FAILED
    assert recovered.error is not None
    assert recovered.error.reason is FailureReason.INTERRUPTED
    completed = restarted.rerun_from_start(run.run_id, "source")
    assert completed.state is RunState.COMPLETED
    assert _latest_attempt(completed.node_runs, "source").attempt == 2


def test_active_run_reuses_start_time_candidate_after_project_edit(tmp_path: Path) -> None:
    source = _python_definition(
        "test.snapshot",
        "tests:snapshot",
        outputs=_data_output(),
        with_text_parameter=True,
    )
    old_node = NodeInstance(
        node_id="source",
        type_id=source.type_id,
        definition_version=source.version,
        parameters={"text": "old"},
    )
    store = _store(tmp_path, Graph(nodes=(old_node,)), (source,))
    calls: list[str] = []
    service = RuntimeService(
        store,
        tmp_path / "work",
        python_adapters={"tests:snapshot": _source_adapter(calls)},
    )
    first = service.run_until_blocked(service.create_run().run_id)
    first_node_run = _latest_attempt(first.node_runs, "source")
    run = service.create_run()
    new_node = old_node.model_copy(update={"parameters": {"text": "new"}})
    store.save(
        Project(
            project_id="project-runtime",
            name="Runtime 合成工程",
            graph=Graph(nodes=(new_node,)),
        ),
        (source,),
    )

    completed = service.run_until_blocked(run.run_id)
    node_run = _latest_attempt(completed.node_runs, "source")
    assert node_run.reused_from_result_id is not None
    assert node_run.output_artifact_ids == first_node_run.output_artifact_ids
    assert len(calls) == 1
    artifact = service.repository.get_artifact(node_run.output_artifact_ids[0])
    assert Path(artifact.path).read_text(encoding="utf-8") == "old"
    latest = service.repository.get_latest("source")
    assert latest is not None
    assert latest.stale is True
    assert latest.stale_reason is StaleReason.GRAPH_CHANGED


def test_active_run_can_reuse_historical_candidate_after_head_replacement(
    tmp_path: Path,
) -> None:
    graph, definitions = _manual_graph()
    store = _store(tmp_path, graph, definitions)
    source_calls: list[str] = []
    copy_calls: list[str] = []
    service = RuntimeService(
        store,
        tmp_path / "work",
        python_adapters={
            "tests:source": _source_adapter(source_calls),
            "tests:copy": _copy_adapter(copy_calls),
        },
    )
    first = service.run_until_blocked(service.create_run().run_id)
    source_one = _latest_attempt(first.node_runs, "source")
    copy_one = _latest_attempt(first.node_runs, "copy")
    waiting_one = _latest_attempt(first.node_runs, "manual")
    assert waiting_one.state is NodeRunState.WAITING_EXTERNAL

    second = service.create_run()
    first_after_rerun = service.rerun_from_start(first.run_id, "source")
    source_two = _latest_attempt(first_after_rerun.node_runs, "source")
    assert source_two.output_artifact_ids != source_one.output_artifact_ids
    assert len(source_calls) == 2
    assert len(copy_calls) == 2

    second = service.run_until_blocked(second.run_id)
    reused_source = _latest_attempt(second.node_runs, "source")
    reused_copy = _latest_attempt(second.node_runs, "copy")
    assert reused_source.reused_from_result_id is not None
    assert reused_copy.reused_from_result_id is not None
    assert reused_source.output_artifact_ids == source_one.output_artifact_ids
    assert reused_copy.output_artifact_ids == copy_one.output_artifact_ids
    assert len(source_calls) == 2
    assert len(copy_calls) == 2
    current_source_head = service.repository.get_latest("source")
    assert current_source_head is not None
    assert current_source_head.result_id != reused_source.reused_from_result_id


def test_stale_head_before_run_creation_is_not_reused(tmp_path: Path) -> None:
    source = _python_definition("test.stale", "tests:stale", outputs=_data_output())
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source", type_id=source.type_id, definition_version=source.version
            ),
        )
    )
    store = _store(tmp_path, graph, (source,))
    calls: list[str] = []
    service = RuntimeService(
        store,
        tmp_path / "work",
        python_adapters={"tests:stale": _source_adapter(calls)},
    )
    first = service.run_until_blocked(service.create_run().run_id)
    service.repository.mark_latest_stale(
        ("source",),
        StaleReason.RERUN_REQUESTED,
        updated_at=utc_now(),
    )

    second = service.run_until_blocked(service.create_run().run_id)
    assert _latest_attempt(second.node_runs, "source").reused_from_result_id is None
    assert len(calls) == 2
    assert (
        _latest_attempt(second.node_runs, "source").output_artifact_ids
        != _latest_attempt(first.node_runs, "source").output_artifact_ids
    )


def test_terminal_rerun_marks_source_and_downstream_with_distinct_reasons(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    source = _python_definition("test.source", "tests:source", outputs=_data_output())
    copy = _python_definition(
        "test.copy",
        "tests:copy",
        inputs=_data_input(),
        outputs=_data_output(),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id=source.type_id,
                definition_version=source.version,
            ),
            NodeInstance(
                node_id="copy",
                type_id=copy.type_id,
                definition_version=copy.version,
            ),
        ),
        edges=(
            Edge(
                source_node_id="source",
                source_port_id="out",
                target_node_id="copy",
                target_port_id="in",
            ),
        ),
    )
    service = RuntimeService(
        _store(tmp_path, graph, (source, copy)),
        tmp_path / "work",
        python_adapters={
            "tests:source": _source_adapter(calls),
            "tests:copy": _copy_adapter(calls),
        },
    )
    first = service.run_until_blocked(service.create_run().run_id)
    assert first.state is RunState.COMPLETED

    service.create_rerun_run("source")
    latest = {item.node_id: item for item in service.repository.list_latest()}

    assert latest["source"].stale_reason is StaleReason.RERUN_REQUESTED
    assert latest["copy"].stale_reason is StaleReason.UPSTREAM_CHANGED


def test_quick_probe_system_exit_blocks_reuse_without_terminating_service(
    tmp_path: Path,
) -> None:
    """注入 probe 的 SystemExit 视为不可复用，随后正常执行新 attempt。"""

    calls: list[str] = []
    source = _python_definition(
        "test.quick-probe-system-exit",
        "tests:quick-probe-source",
        outputs=_data_output(),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id=source.type_id,
                definition_version=source.version,
            ),
        )
    )
    store = _store(tmp_path, graph, (source,), filename="quick-probe-system-exit.zniku")
    adapter = _source_adapter(calls)
    first_service = RuntimeService(
        store,
        tmp_path / "quick-probe-work",
        python_adapters={"tests:quick-probe-source": adapter},
    )
    first = first_service.run_until_blocked(first_service.create_run().run_id)
    assert first.state is RunState.COMPLETED

    def exit_probe(_artifact: object) -> bool:
        raise SystemExit(13)

    restarted = RuntimeService(
        store,
        tmp_path / "quick-probe-work",
        python_adapters={"tests:quick-probe-source": adapter},
        artifact_quick_probe=exit_probe,
    )
    second = restarted.run_until_blocked(restarted.create_run().run_id)

    assert second.state is RunState.COMPLETED
    assert len(calls) == 2
    assert _latest_attempt(second.node_runs, "source").reused_from_result_id is None


def test_truthy_non_bool_quick_probe_cannot_authorize_reuse(tmp_path: Path) -> None:
    """quick probe 只接受精确 bool True，拒绝插件返回的宽松 truthy 值。"""

    calls: list[str] = []
    source = _python_definition(
        "test.quick-probe-truthy",
        "tests:quick-probe-truthy",
        outputs=_data_output(),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id=source.type_id,
                definition_version=source.version,
            ),
        )
    )
    store = _store(tmp_path, graph, (source,), filename="quick-probe-truthy.zniku")
    adapter = _source_adapter(calls)
    first_service = RuntimeService(
        store,
        tmp_path / "quick-probe-truthy-work",
        python_adapters={"tests:quick-probe-truthy": adapter},
    )
    first = first_service.run_until_blocked(first_service.create_run().run_id)
    assert first.state is RunState.COMPLETED

    def invalid_probe(_artifact: object) -> bool:
        return cast(bool, "passed")

    second_service = RuntimeService(
        store,
        tmp_path / "quick-probe-truthy-work",
        python_adapters={"tests:quick-probe-truthy": adapter},
        artifact_quick_probe=invalid_probe,
    )
    second = second_service.run_until_blocked(second_service.create_run().run_id)

    assert second.state is RunState.COMPLETED
    assert len(calls) == 2
    assert _latest_attempt(second.node_runs, "source").reused_from_result_id is None


def test_command_executor_runs_through_service_with_exit_code_zero(tmp_path: Path) -> None:
    command = NodeDefinition(
        type_id="test.command",
        version="1.0.0",
        output_ports=_data_output(),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=CommandExecutorSpec(
            executable=sys.executable,
            argv=(
                "-c",
                "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('command')",
                "{output:out}",
            ),
        ),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="command",
                type_id=command.type_id,
                definition_version=command.version,
            ),
        )
    )
    service = RuntimeService(_store(tmp_path, graph, (command,)), tmp_path / "work")
    completed = service.run_until_blocked(service.create_run().run_id)
    node_run = _latest_attempt(completed.node_runs, "command")
    assert node_run.state is NodeRunState.COMPLETED
    assert node_run.exit_code == 0
    artifact = service.repository.get_artifact(node_run.output_artifact_ids[0])
    assert Path(artifact.path).read_text(encoding="utf-8") == "command"


def test_subprocess_value_error_fails_current_attempt_without_leaving_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = NodeDefinition(
        type_id="test.command-value-error",
        version="1.0.0",
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=CommandExecutorSpec(executable=sys.executable, argv=("-V",)),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="command",
                type_id=command.type_id,
                definition_version=command.version,
            ),
        )
    )
    service = RuntimeService(_store(tmp_path, graph, (command,)), tmp_path / "work")

    def raise_value_error(*_args: object, **_kwargs: object) -> None:
        raise ValueError("synthetic subprocess configuration error")

    monkeypatch.setattr("zniku.runtime.runner.subprocess.run", raise_value_error)

    run = service.run_until_blocked(service.create_run().run_id)

    node_run = _latest_attempt(run.node_runs, "command")
    assert run.state is RunState.RUNNING
    assert node_run.state is NodeRunState.FAILED
    assert node_run.error is not None
    assert node_run.error.reason is FailureReason.EXECUTION_ERROR
    assert node_run.output_artifact_ids == ()


def test_malformed_python_adapter_result_fails_current_attempt(tmp_path: Path) -> None:
    source = _python_definition(
        "test.malformed-adapter-result",
        "tests:malformed-adapter-result",
        outputs=_data_output(),
    )
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id=source.type_id,
                definition_version=source.version,
            ),
        )
    )

    def malformed(context: PythonAdapterContext) -> PythonAdapterResult:
        context.outputs[0].path.write_text("candidate", encoding="utf-8")
        return PythonAdapterResult(outputs=(cast(Any, object()),))

    service = RuntimeService(
        _store(tmp_path, graph, (source,)),
        tmp_path / "work",
        python_adapters={"tests:malformed-adapter-result": malformed},
    )

    run = service.run_until_blocked(service.create_run().run_id)

    node_run = _latest_attempt(run.node_runs, "source")
    assert node_run.state is NodeRunState.FAILED
    assert node_run.error is not None
    assert node_run.error.reason is FailureReason.EXECUTION_ERROR
    assert node_run.output_artifact_ids == ()


@pytest.mark.parametrize("boundary", ("adapter", "validator"))
def test_plugin_system_exit_fails_attempt_without_terminating_service(
    tmp_path: Path,
    boundary: str,
) -> None:
    """SystemExit 属于插件失败，只能影响当前 NodeRun。"""

    adapter_name = f"tests:{boundary}-system-exit"
    definition_values: dict[str, object] = {
        "type_id": f"test.{boundary}-system-exit",
        "version": "1.0.0",
        "output_ports": _data_output(),
        "execution_mode": ExecutionMode.AUTOMATIC,
        "executor": PythonExecutorSpec(adapter=adapter_name),
    }
    if boundary == "validator":
        definition_values["validator"] = ValidatorSpec(adapter="tests:validator-system-exit")
    definition = NodeDefinition.model_validate(definition_values)
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="plugin",
                type_id=definition.type_id,
                definition_version=definition.version,
            ),
        )
    )

    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        if boundary == "adapter":
            raise SystemExit(11)
        context.outputs[0].path.write_text("candidate", encoding="utf-8")
        return PythonAdapterResult()

    def validator(_context: NodeValidatorContext) -> NodeValidatorResult:
        raise SystemExit(12)

    service = RuntimeService(
        _store(
            tmp_path,
            graph,
            (definition,),
            filename=f"{boundary}-system-exit.zniku",
        ),
        tmp_path / f"{boundary}-system-exit-work",
        python_adapters={adapter_name: adapter},
        validators={"tests:validator-system-exit": validator},
    )

    run = service.run_until_blocked(service.create_run().run_id)

    node_run = _latest_attempt(run.node_runs, "plugin")
    assert run.state is RunState.RUNNING
    assert node_run.state is NodeRunState.FAILED
    assert node_run.error is not None
    expected_reason = (
        FailureReason.VALIDATION_FAILED
        if boundary == "validator"
        else FailureReason.EXECUTION_ERROR
    )
    assert node_run.error.reason is expected_reason
    assert node_run.output_artifact_ids == ()


def test_non_json_runner_summary_fails_attempt_without_artifact_registration(
    tmp_path: Path,
) -> None:
    source = _python_definition("test.bad-summary", "tests:bad-summary", outputs=_data_output())
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source", type_id=source.type_id, definition_version=source.version
            ),
        )
    )

    def bad_summary(context: PythonAdapterContext) -> PythonAdapterResult:
        context.outputs[0].path.write_text("partial", encoding="utf-8")
        return PythonAdapterResult(media_summary={"not_json": object()})

    service = RuntimeService(
        _store(tmp_path, graph, (source,)),
        tmp_path / "work",
        python_adapters={"tests:bad-summary": bad_summary},
    )
    run = service.run_until_blocked(service.create_run().run_id)
    node_run = _latest_attempt(run.node_runs, "source")
    assert run.state is RunState.RUNNING
    assert node_run.state is NodeRunState.FAILED
    assert node_run.output_artifact_ids == ()
    assert service.repository.get_latest("source") is None


def test_corrupt_handoff_input_binding_is_rejected_before_submit(tmp_path: Path) -> None:
    graph, definitions = _manual_graph()
    store = _store(tmp_path, graph, definitions)
    service = RuntimeService(
        store,
        tmp_path / "work",
        python_adapters={
            "tests:source": _source_adapter([]),
            "tests:copy": _copy_adapter([]),
        },
    )
    run = service.run_until_blocked(service.create_run().run_id)
    manual = _latest_attempt(run.node_runs, "manual")
    assert manual.external_handoff is not None
    with sqlite3.connect(store.path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT external_handoff_json FROM node_runs WHERE node_run_id = ?",
                (manual.node_run_id,),
            ).fetchone()[0]
        )
        payload["input_artifact_ids"] = [str(uuid4())]
        connection.execute(
            "UPDATE node_runs SET external_handoff_json = ? WHERE node_run_id = ?",
            (json.dumps(payload), manual.node_run_id),
        )

    with pytest.raises(RuntimeDataError, match="E_HANDOFF_RELATION_CORRUPT"):
        service.submit_external(manual.node_run_id)
    with sqlite3.connect(store.path) as connection:
        persisted_state = connection.execute(
            "SELECT state FROM node_runs WHERE node_run_id = ?",
            (manual.node_run_id,),
        ).fetchone()[0]
    assert persisted_state == NodeRunState.WAITING_EXTERNAL.value
