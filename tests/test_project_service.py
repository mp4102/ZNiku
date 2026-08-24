"""验证 Phase 3 Project Service 只委托正式 Graph、Project 与 Runtime authority。

测试只使用临时 ``.zniku``、纯合成 DataFile adapter 和受控 attempt 目录；不读取真实媒体，也不把
HTTP/Studio 投影当作第二套状态机。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

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
)
from zniku.project import Project, ProjectStore
from zniku.project_service import (
    ProjectServiceApplication,
    ProjectServiceError,
    parse_project_service_command,
)
from zniku.runtime import (
    NodeRun,
    NodeRunState,
    PythonAdapterContext,
    PythonAdapterResult,
    Run,
    RunState,
)


def _data_output() -> tuple[PortSpec, ...]:
    return (PortSpec(port_id="out", data_type="DataFile"),)


def _data_input() -> tuple[PortSpec, ...]:
    return (PortSpec(port_id="in", data_type="DataFile", required=True),)


def _source_definition(*, outputs: tuple[PortSpec, ...] | None = None) -> NodeDefinition:
    return NodeDefinition(
        type_id="test.phase3.Source",
        version="1.0.0",
        output_ports=_data_output() if outputs is None else outputs,
        parameter_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.phase3:source"),
    )


def _copy_definition() -> NodeDefinition:
    return NodeDefinition(
        type_id="test.phase3.Copy",
        version="1.0.0",
        input_ports=_data_input(),
        output_ports=_data_output(),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.phase3:copy"),
    )


def _manual_definition() -> NodeDefinition:
    return NodeDefinition(
        type_id="test.phase3.Manual",
        version="1.0.0",
        input_ports=_data_input(),
        output_ports=_data_output(),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(instructions="把输入复制到声明目标后提交"),
    )


def _project(
    *,
    include_manual: bool = False,
    source_outputs: tuple[PortSpec, ...] | None = None,
) -> tuple[Project, tuple[NodeDefinition, ...]]:
    source = _source_definition(outputs=source_outputs)
    if source_outputs == ():
        graph = Graph(
            nodes=(
                NodeInstance(
                    node_id="source",
                    type_id=source.type_id,
                    definition_version=source.version,
                    parameters={"text": "alpha"},
                    ui_position=UiPosition(x=20.0, y=30.0),
                ),
            )
        )
        return Project(project_id="project.phase3", name="Phase 3 合成工程", graph=graph), (source,)

    copy = _copy_definition()
    nodes = [
        NodeInstance(
            node_id="source",
            type_id=source.type_id,
            definition_version=source.version,
            parameters={"text": "alpha"},
            ui_position=UiPosition(x=20.0, y=30.0),
        ),
        NodeInstance(
            node_id="copy",
            type_id=copy.type_id,
            definition_version=copy.version,
            ui_position=UiPosition(x=260.0, y=30.0),
        ),
    ]
    edges = [
        Edge(
            source_node_id="source",
            source_port_id="out",
            target_node_id="copy",
            target_port_id="in",
        )
    ]
    definitions: list[NodeDefinition] = [source, copy]
    if include_manual:
        manual = _manual_definition()
        nodes.append(
            NodeInstance(
                node_id="manual",
                type_id=manual.type_id,
                definition_version=manual.version,
                ui_position=UiPosition(x=500.0, y=30.0),
            )
        )
        edges.append(
            Edge(
                source_node_id="copy",
                source_port_id="out",
                target_node_id="manual",
                target_port_id="in",
            )
        )
        definitions.append(manual)
    return (
        Project(
            project_id="project.phase3",
            name="Phase 3 合成工程",
            graph=Graph(nodes=tuple(nodes), edges=tuple(edges)),
        ),
        tuple(definitions),
    )


def _store(
    tmp_path: Path,
    *,
    include_manual: bool = False,
    source_outputs: tuple[PortSpec, ...] | None = None,
    filename: str = "phase3.zniku",
) -> ProjectStore:
    project, definitions = _project(
        include_manual=include_manual,
        source_outputs=source_outputs,
    )
    return ProjectStore.create(tmp_path / filename, project, definitions)


def _adapters(
    calls: dict[str, list[str]],
    *,
    source_stdout: str = "source stdout\n",
) -> dict[str, Callable[[PythonAdapterContext], PythonAdapterResult]]:
    def source(context: PythonAdapterContext) -> PythonAdapterResult:
        calls.setdefault("source", []).append(context.node_run_id)
        context.stdout_log_path.write_text(source_stdout, encoding="utf-8")
        context.stderr_log_path.write_text("source stderr\n", encoding="utf-8")
        if context.outputs:
            context.outputs[0].path.write_text(
                str(context.node.parameters["text"]), encoding="utf-8"
            )
        return PythonAdapterResult()

    def copy(context: PythonAdapterContext) -> PythonAdapterResult:
        calls.setdefault("copy", []).append(context.node_run_id)
        content = context.inputs[0].path.read_text(encoding="utf-8")
        context.outputs[0].path.write_text(f"{content}-copy", encoding="utf-8")
        context.stdout_log_path.write_text("copy stdout\n", encoding="utf-8")
        return PythonAdapterResult()

    return {
        "tests.phase3:source": source,
        "tests.phase3:copy": copy,
    }


def _latest(run: Run, node_id: str) -> NodeRun:
    return max(
        (item for item in run.node_runs if item.node_id == node_id),
        key=lambda item: item.attempt,
    )


@pytest.mark.parametrize(
    "payload",
    (
        {"operation": "unknown"},
        {"operation": "run_all", "unexpected": True},
        {"operation": "run_to", "node_id": 1},
        {"operation": "create_project", "path": "x.zniku", "project_id": 1, "name": "x"},
    ),
)
def test_commands_reject_unknown_operations_fields_and_type_coercion(payload: object) -> None:
    with pytest.raises(ValidationError):
        parse_project_service_command(payload)


def test_command_without_project_fails_with_stable_conflict(tmp_path: Path) -> None:
    application = ProjectServiceApplication(work_root=tmp_path / "work")

    with pytest.raises(ProjectServiceError) as captured:
        application.command({"operation": "run_all"})

    assert captured.value.code == "E_PROJECT_SERVICE_NO_PROJECT"
    assert captured.value.http_status == 409


def test_create_save_and_reopen_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "created.zniku"
    first = ProjectServiceApplication(work_root=tmp_path / "work-first")

    created = first.command(
        {
            "operation": "create_project",
            "path": str(path),
            "project_id": "project.created",
            "name": "新建工程",
        }
    )

    assert created.project_path == str(path)
    assert created.snapshot is not None
    assert created.snapshot.project.graph == Graph()
    replacement = Project(
        project_id="project.created",
        name="已保存工程",
        graph=Graph(),
    )
    saved = first.command({"operation": "save_project", "project": replacement})
    assert saved.snapshot is not None
    assert saved.snapshot.project == replacement

    reopened = ProjectServiceApplication(work_root=tmp_path / "work-second").command(
        {"operation": "open_project", "path": str(path)}
    )
    assert reopened.snapshot is not None
    assert reopened.snapshot.project == replacement
    assert reopened.snapshot.definitions == ()


def test_create_project_uses_injected_definition_catalog(tmp_path: Path) -> None:
    """新建 Project 固定启动时目录，但打开旧 Project 不会暗中追加定义。"""

    definition = _source_definition()
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        definition_catalog=(definition,),
    )
    path = tmp_path / "catalog.zniku"

    created = application.command(
        {
            "operation": "create_project",
            "path": str(path),
            "project_id": "project.catalog",
            "name": "目录工程",
        }
    )

    assert created.snapshot is not None
    assert created.snapshot.definitions == (definition,)
    reopened = ProjectServiceApplication(work_root=tmp_path / "reopen").command(
        {"operation": "open_project", "path": str(path)}
    )
    assert reopened.snapshot is not None
    assert reopened.snapshot.definitions == (definition,)


def test_project_service_rejects_duplicate_definition_catalog(tmp_path: Path) -> None:
    definition = _source_definition()

    with pytest.raises(ProjectServiceError) as captured:
        ProjectServiceApplication(
            work_root=tmp_path / "work",
            definition_catalog=(definition, definition),
        )

    assert captured.value.code == "E_PROJECT_SERVICE_DEFINITION_CATALOG"


def test_save_reuses_python_definition_catalog_and_persists_layout(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = store.load()
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    application.command({"operation": "open_project", "path": str(store.path)})
    moved_nodes = tuple(
        node.model_copy(update={"ui_position": UiPosition(x=901.0, y=502.0)})
        if node.node_id == "copy"
        else node
        for node in before.project.graph.nodes
    )
    replacement = before.project.model_copy(
        update={
            "name": "保存后的工程",
            "graph": Graph(nodes=moved_nodes, edges=before.project.graph.edges),
        }
    )

    application.command({"operation": "save_project", "project": replacement})
    after = store.load()

    assert after.project == replacement
    assert after.definitions == before.definitions


def test_invalid_save_fails_closed_without_changing_project(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = store.load()
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    application.command({"operation": "open_project", "path": str(store.path)})
    invalid = before.project.model_copy(
        update={"graph": Graph(nodes=before.project.graph.nodes, edges=())}
    )

    with pytest.raises(ProjectServiceError) as captured:
        application.command({"operation": "save_project", "project": invalid})

    assert captured.value.code == "E_PROJECT_GRAPH_INVALID"
    assert captured.value.http_status == 422
    assert store.load() == before


def test_run_to_executes_only_target_ancestor_closure(tmp_path: Path) -> None:
    store = _store(tmp_path, include_manual=True)
    calls: dict[str, list[str]] = {}
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters=_adapters(calls),
    )
    application.command({"operation": "open_project", "path": str(store.path)})

    started = application.command({"operation": "run_to", "node_id": "copy"})
    assert started.active_run_id is not None
    assert application.wait_until_idle(timeout=5)
    completed = application.inspect()
    run = completed.runs[-1]

    assert run.state is RunState.COMPLETED
    assert run.selected_targets == ("copy",)
    assert tuple(item.node_id for item in run.node_runs) == ("source", "copy")
    assert len(calls["source"]) == 1
    assert len(calls["copy"]) == 1
    artifacts = {item.artifact_id: item for item in completed.artifacts}
    copy_artifact = artifacts[_latest(run, "copy").output_artifact_ids[0]]
    assert Path(copy_artifact.path).read_text(encoding="utf-8") == "alpha-copy"
    logs = {item.node_run_id: item for item in completed.logs}
    assert logs[_latest(run, "source").node_run_id].stdout.splitlines() == ["source stdout"]
    assert logs[_latest(run, "source").node_run_id].stderr.splitlines() == ["source stderr"]
    assert logs[_latest(run, "copy").node_run_id].stdout.splitlines() == ["copy stdout"]


def test_run_all_waits_for_manual_handoff_then_submit_registers_output(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, include_manual=True)
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters=_adapters({}),
    )
    application.command({"operation": "open_project", "path": str(store.path)})

    application.command({"operation": "run_all"})
    assert application.wait_until_idle(timeout=5)
    waiting_envelope = application.inspect()
    waiting_run = waiting_envelope.runs[-1]
    waiting = _latest(waiting_run, "manual")

    assert waiting_run.state is RunState.RUNNING
    assert waiting.state is NodeRunState.WAITING_EXTERNAL
    assert waiting.external_handoff is not None
    assert waiting.external_handoff.instructions == "把输入复制到声明目标后提交"
    assert len(waiting.external_handoff.input_artifact_ids) == 1
    input_artifact = next(
        item
        for item in waiting_envelope.artifacts
        if item.artifact_id == waiting.external_handoff.input_artifact_ids[0]
    )
    assert Path(input_artifact.path).read_text(encoding="utf-8") == "alpha-copy"
    output_target = waiting.external_handoff.output_targets[0]
    Path(output_target.path).write_text("manual-result", encoding="utf-8")

    submitted = application.command(
        {"operation": "submit_external", "node_run_id": waiting.node_run_id}
    )
    assert submitted.active_run_id == waiting_run.run_id
    assert application.wait_until_idle(timeout=5)
    completed_envelope = application.inspect()
    completed_run = completed_envelope.runs[-1]
    completed = _latest(completed_run, "manual")

    assert completed_run.state is RunState.COMPLETED
    assert completed.state is NodeRunState.COMPLETED
    assert completed.external_handoff == waiting.external_handoff
    output = next(
        item
        for item in completed_envelope.artifacts
        if item.artifact_id == completed.output_artifact_ids[0]
    )
    assert output.path == output_target.path
    assert Path(output.path).read_text(encoding="utf-8") == "manual-result"


def test_terminal_rerun_creates_new_run_and_reuses_unaffected_source(tmp_path: Path) -> None:
    store = _store(tmp_path)
    calls: dict[str, list[str]] = {}
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters=_adapters(calls),
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    application.command({"operation": "run_all"})
    assert application.wait_until_idle(timeout=5)
    first_envelope = application.inspect()
    first = first_envelope.runs[-1]
    first_copy = _latest(first, "copy")
    assert first.state is RunState.COMPLETED

    application.command(
        {
            "operation": "rerun_from_here",
            "run_id": first.run_id,
            "node_id": "copy",
        }
    )
    assert application.wait_until_idle(timeout=5)
    rerun_envelope = application.inspect()
    second = rerun_envelope.runs[-1]
    second_source = _latest(second, "source")
    second_copy = _latest(second, "copy")

    assert tuple(item.run_id for item in rerun_envelope.runs) == (first.run_id, second.run_id)
    assert second.run_id != first.run_id
    assert first.state is RunState.COMPLETED
    assert second.state is RunState.COMPLETED
    assert second_source.attempt == 1
    assert second_source.reused_from_result_id is not None
    assert second_copy.attempt == 1
    assert second_copy.reused_from_result_id is None
    assert second_copy.output_artifact_ids != first_copy.output_artifact_ids
    assert len(calls["source"]) == 1
    assert len(calls["copy"]) == 2


def test_terminal_rerun_rejects_node_outside_referenced_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters=_adapters({}),
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    application.command({"operation": "run_to", "node_id": "source"})
    assert application.wait_until_idle(timeout=5)
    first = application.inspect().runs[-1]
    assert first.state is RunState.COMPLETED
    assert tuple(item.node_id for item in first.node_runs) == ("source",)

    with pytest.raises(ProjectServiceError) as captured:
        application.command(
            {
                "operation": "rerun_from_here",
                "run_id": first.run_id,
                "node_id": "copy",
            }
        )

    assert captured.value.code == "E_PROJECT_SERVICE_RERUN_NODE_OUTSIDE_RUN"
    assert captured.value.http_status == 409
    assert tuple(item.run_id for item in application.inspect().runs) == (first.run_id,)


def test_rerun_after_project_edit_uses_new_snapshot_instead_of_waiting_run(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, include_manual=True)
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters=_adapters({}),
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    application.command({"operation": "run_all"})
    assert application.wait_until_idle(timeout=5)
    first = application.inspect().runs[-1]
    assert _latest(first, "manual").state is NodeRunState.WAITING_EXTERNAL

    snapshot = store.load()
    changed_nodes = tuple(
        node.model_copy(update={"parameters": {"text": "beta"}})
        if node.node_id == "source"
        else node
        for node in snapshot.project.graph.nodes
    )
    changed = snapshot.project.model_copy(
        update={
            "graph": Graph(nodes=changed_nodes, edges=snapshot.project.graph.edges),
        }
    )
    application.command({"operation": "save_project", "project": changed})
    application.command(
        {
            "operation": "rerun_from_here",
            "run_id": first.run_id,
            "node_id": "source",
        }
    )
    assert application.wait_until_idle(timeout=5)
    inspected = application.inspect()
    second = inspected.runs[-1]

    assert second.run_id != first.run_id
    assert second.graph_snapshot == changed.graph
    assert _latest(first, "manual").state is NodeRunState.WAITING_EXTERNAL
    assert _latest(second, "manual").state is NodeRunState.WAITING_EXTERNAL
    source_output = next(
        artifact
        for artifact in inspected.artifacts
        if artifact.artifact_id == _latest(second, "source").output_artifact_ids[0]
    )
    assert Path(source_output.path).read_text("utf-8") == "beta"


def test_rerun_after_graph_edit_rejects_new_node_absent_from_referenced_run(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, include_manual=True)
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters=_adapters({}),
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    application.command({"operation": "run_all"})
    assert application.wait_until_idle(timeout=5)
    first = application.inspect().runs[-1]
    assert first.state is RunState.RUNNING

    snapshot = store.load()
    added = NodeInstance(
        node_id="new-source",
        type_id="test.phase3.Source",
        definition_version="1.0.0",
        parameters={"text": "new"},
        ui_position=UiPosition(x=20.0, y=180.0),
    )
    changed = snapshot.project.model_copy(
        update={
            "graph": Graph(
                nodes=(*snapshot.project.graph.nodes, added),
                edges=snapshot.project.graph.edges,
            )
        }
    )
    application.command({"operation": "save_project", "project": changed})

    with pytest.raises(ProjectServiceError) as captured:
        application.command(
            {
                "operation": "rerun_from_here",
                "run_id": first.run_id,
                "node_id": "new-source",
            }
        )

    assert captured.value.code == "E_PROJECT_SERVICE_RERUN_NODE_OUTSIDE_RUN"
    assert captured.value.http_status == 409
    assert tuple(item.run_id for item in application.inspect().runs) == (first.run_id,)


def test_log_projection_rejects_tampered_work_dir_outside_host_root(tmp_path: Path) -> None:
    store = _store(tmp_path, source_outputs=(), filename="logs.zniku")
    application = ProjectServiceApplication(
        work_root=tmp_path / "work",
        python_adapters=_adapters({}),
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    application.command({"operation": "run_all"})
    assert application.wait_until_idle(timeout=5)
    run = application.inspect().runs[-1]
    source = _latest(run, "source")
    assert source.state is NodeRunState.COMPLETED

    outside = tmp_path / "outside-attempt"
    outside_logs = outside / "logs"
    outside_logs.mkdir(parents=True)
    (outside_logs / "stdout.log").write_text("不得泄露", encoding="utf-8")
    (outside_logs / "stderr.log").write_text("不得泄露", encoding="utf-8")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE node_runs SET work_dir = ?, log_path = ? WHERE node_run_id = ?",
            (str(outside), str(outside_logs), source.node_run_id),
        )

    inspected = application.inspect()
    projection = next(item for item in inspected.logs if item.node_run_id == source.node_run_id)

    assert projection.stdout == ""
    assert projection.stderr == ""
    assert projection.stdout_available is False
    assert projection.stderr_available is False
