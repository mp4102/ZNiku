"""验证 Phase 3 单一 Graph 草稿、Studio 隔离、schema 迁移和并发保存边界。

全部工程、媒体替身和运行历史均为临时目录中的合成数据；CAS 失败不能写回任何部分结果。
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier, Event
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from zniku.graph import (
    Cardinality,
    Edge,
    ExecutionMode,
    Graph,
    GraphValidator,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
    UiPosition,
)
from zniku.project import (
    GroupViewState,
    NodeViewState,
    Project,
    ProjectStore,
    ProjectStoreError,
    StudioState,
    StudioViewport,
)
from zniku.project_service import ProjectServiceApplication, ProjectServiceError
from zniku.project_service.models import parse_project_service_command
from zniku.runtime import (
    PythonAdapterContext,
    PythonAdapterResult,
    RuntimeConflictError,
    RuntimeService,
    RuntimeServiceError,
    utc_now,
)


def _definition(*, required_input: bool = False) -> NodeDefinition:
    return NodeDefinition(
        type_id="test.authoring.Data",
        version="1.0.0",
        input_ports=(PortSpec(port_id="in", data_type="DataFile", required=True),)
        if required_input
        else (),
        output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        parameter_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1}},
            "required": ["text"],
            "additionalProperties": False,
        },
        executor=PythonExecutorSpec(adapter="tests.authoring:data"),
    )


def _project(*, parameters: dict[str, Any] | None = None, two: bool = False) -> Project:
    definition = _definition()
    first = NodeInstance(
        node_id="source",
        type_id=definition.type_id,
        definition_version=definition.version,
        parameters={"text": "original"} if parameters is None else parameters,
    )
    return Project(
        project_id="project.authoring",
        name="合成编辑工程",
        graph=Graph(
            nodes=(first, first.model_copy(update={"node_id": "other"})) if two else (first,)
        ),
    )


def _store(tmp_path: Path) -> ProjectStore:
    return ProjectStore.create(tmp_path / "authoring.zniku", _project(), (_definition(),))


def _state(name: str = "素材") -> StudioState:
    return StudioState(
        viewport=StudioViewport(x=12.0, y=-8.0, zoom=1.5),
        groups=(GroupViewState(group_id="group", title="第一章", color_token="blue"),),
        node_views=(
            NodeViewState(node_id="source", display_name=name, group_id="group", collapsed=True),
        ),
    )


def _adapter(context: PythonAdapterContext) -> PythonAdapterResult:
    path = context.outputs[0].path
    path.write_text(str(context.node.parameters["text"]), encoding="utf-8")
    return PythonAdapterResult()


def _runtime(store: ProjectStore, root: Path) -> RuntimeService:
    return RuntimeService(
        store,
        root,
        python_adapters={"tests.authoring:data": _adapter},
        artifact_quick_probe=lambda _artifact: True,
    )


def _binding(application: ProjectServiceApplication) -> dict[str, Any]:
    status = application.inspect()
    return {
        "project_session_id": status.project_session_id,
        "expected_storage_revision": status.storage_revision,
    }


def _as_schema2(store: ProjectStore) -> None:
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("DROP TABLE project_storage")
        connection.execute("DROP TABLE studio_state")
        connection.execute("PRAGMA user_version = 2")


def _rows(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    with closing(sqlite3.connect(path)) as connection, connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT IN ('studio_state', 'project_storage')"
            )
        ]
        return {name: connection.execute(f"SELECT * FROM {name}").fetchall() for name in names}


@pytest.mark.parametrize("missing_input", [False, True])
def test_required_draft_round_trip_cannot_create_any_run(
    tmp_path: Path, missing_input: bool
) -> None:
    definition = _definition(required_input=missing_input)
    project = _project(parameters={} if not missing_input else {"text": "valid"})
    store = ProjectStore.create(tmp_path / "draft.zniku", project, (definition,))
    before = _rows(store.path)
    reopened = ProjectStore.open(store.path)
    assert reopened.load().project == project
    violations = GraphValidator((definition,)).inspect(project.graph)
    assert len(violations) == 1 and violations[0].authoring_recoverable
    assert violations[0].validator_keyword == (None if missing_input else "required")
    runtime = _runtime(reopened, tmp_path / "work")
    with pytest.raises(RuntimeServiceError, match="E_SERVICE_GRAPH_INVALID"):
        runtime.create_run()
    assert _rows(store.path) == before
    assert not list((tmp_path / "work").rglob("attempt*"))


@pytest.mark.parametrize("parameters", [{"text": 1}, {"text": ""}, {"other": 1}])
def test_other_parameter_violations_never_enter_storage(
    tmp_path: Path, parameters: dict[str, Any]
) -> None:
    store = _store(tmp_path)
    before = store.path.read_bytes()
    with pytest.raises(ProjectStoreError, match="E_PROJECT_GRAPH_INVALID"):
        store.save(_project(parameters=parameters), (_definition(),), expected_storage_revision=1)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    "kind",
    [
        "unknown_definition",
        "duplicate",
        "dangling",
        "bad_port",
        "cycle",
        "multiple",
        "ordinal",
        "type",
    ],
)
def test_structural_drafts_fail_closed(tmp_path: Path, kind: str) -> None:
    store = _store(tmp_path)
    before = store.path.read_bytes()
    nodes = list(_project(two=True).graph.nodes)
    definition = _definition(required_input=True)
    edge = Edge(
        source_node_id="source", source_port_id="out", target_node_id="other", target_port_id="in"
    )
    edges = [edge]
    definitions = (definition,)
    if kind == "unknown_definition":
        nodes[0] = nodes[0].model_copy(update={"definition_version": "9.0.0"})
    elif kind == "duplicate":
        nodes[1] = nodes[0]
    elif kind == "dangling":
        edges[0] = edge.model_copy(update={"source_node_id": "missing"})
    elif kind == "bad_port":
        edges[0] = edge.model_copy(update={"source_port_id": "missing"})
    elif kind == "cycle":
        edges.append(
            edge.model_copy(update={"source_node_id": "other", "target_node_id": "source"})
        )
    elif kind == "multiple":
        edges.append(edge)
    elif kind == "ordinal":
        edges[0] = edge.model_copy(update={"ordinal": 0})
    elif kind == "type":
        definitions = (
            definition.model_copy(
                update={"output_ports": (PortSpec(port_id="out", data_type="VideoFile"),)}
            ),
        )
    project = _project().model_copy(update={"graph": Graph(nodes=tuple(nodes), edges=tuple(edges))})
    with pytest.raises(ProjectStoreError, match="E_PROJECT_GRAPH_INVALID"):
        store.save(project, definitions, expected_storage_revision=1)
    assert store.path.read_bytes() == before


def test_studio_state_round_trip_isolated_from_run_and_reuse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store, tmp_path / "work")
    run = runtime.create_run()
    completed = runtime.run_until_blocked(run.run_id)
    assert completed.state.value == "completed"
    before = _rows(store.path)
    assert (
        store.save(_project(), (_definition(),), expected_storage_revision=1, studio_state=_state())
        == 2
    )
    authoring = ProjectStore.open(store.path).load_authoring()
    assert authoring.studio_state == _state()
    assert authoring.storage_revision == 2
    assert _rows(store.path) == before
    assert "studio_state" not in completed.model_dump()
    assert completed.graph_snapshot == _project().graph
    next_run = runtime.run_until_blocked(runtime.create_run().run_id)
    assert next_run.node_runs[0].reused_from_result_id is not None
    assert store.load_authoring().storage_revision == 2


@pytest.mark.parametrize("ordinals", [(None, None), (0, 0), (0, 2)])
def test_ordered_many_drafts_require_complete_contiguous_ordinals(
    tmp_path: Path, ordinals: tuple[int | None, int | None]
) -> None:
    store = _store(tmp_path)
    before = store.path.read_bytes()
    definition = _definition().model_copy(
        update={
            "input_ports": (
                PortSpec(
                    port_id="in",
                    data_type="DataFile",
                    required=True,
                    cardinality=Cardinality.ORDERED_MANY,
                ),
            )
        }
    )
    nodes = _project(two=True).graph.nodes
    graph = Graph(
        nodes=nodes,
        edges=tuple(
            Edge(
                source_node_id="source",
                source_port_id="out",
                target_node_id="other",
                target_port_id="in",
                ordinal=ordinal,
            )
            for ordinal in ordinals
        ),
    )
    with pytest.raises(ProjectStoreError, match="E_ORDINAL_"):
        store.save(
            _project().model_copy(update={"graph": graph}),
            (definition,),
            expected_storage_revision=1,
        )
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    "payload",
    [
        {"contract_version": "0.4.0"},
        {"unknown": 1},
        {"viewport": {"x": 0, "y": 0, "zoom": 0}},
        {"viewport": {"x": float("nan"), "y": 0, "zoom": 1}},
        {"node_views": [{"node_id": "source", "group_id": "missing"}]},
        {"node_views": [{"node_id": "source"}, {"node_id": "source"}]},
        {"groups": [{"group_id": "g", "title": "标题", "color_token": "javascript"}]},
    ],
)
def test_studio_state_rejects_unsafe_shape(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        StudioState.model_validate(payload)


def test_dangling_view_save_rejected_and_deleted_node_view_cleared(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = store.path.read_bytes()
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STUDIO_INVALID"):
        store.save(
            _project(),
            (_definition(),),
            studio_state=StudioState(node_views=(NodeViewState(node_id="absent"),)),
        )
    assert store.path.read_bytes() == before
    store.save(_project(), (_definition(),), studio_state=_state())
    store.save(_project().model_copy(update={"graph": Graph()}), (_definition(),))
    assert store.load_authoring().studio_state.node_views == ()


@pytest.mark.parametrize(
    "payload", ["{broken", '{"unknown":true}', '{"node_views":[{"node_id":"absent"}]}']
)
def test_corrupt_studio_falls_back_without_touching_core(tmp_path: Path, payload: str) -> None:
    store = _store(tmp_path)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE studio_state SET payload_json = ?", (payload,))
    before = store.path.read_bytes()
    authoring = store.load_authoring()
    assert authoring.studio_state == StudioState()
    assert authoring.studio_warnings[0].code == "W_PROJECT_STUDIO_STATE_RESET"
    assert authoring.snapshot.project == _project()
    assert store.path.read_bytes() == before


def test_schema2_migration_preserves_history_and_produces_recoverable_backup(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store, tmp_path / "work")
    runtime.run_until_blocked(runtime.create_run().run_id)
    runtime.repository.mark_rerun_stale("source", updated_at=utc_now())
    _as_schema2(store)
    before = _rows(store.path)
    old = ProjectStore.open(store.path)
    assert old.load_authoring().storage_revision == 0
    assert not list(tmp_path.glob(".zniku-schema2-backup-*.zniku"))
    old.save(_project(), (_definition(),), expected_storage_revision=0, studio_state=_state())
    backups = list(tmp_path.glob(".zniku-schema2-backup-*.zniku"))
    assert len(backups) == 1
    assert _rows(backups[0]) == before == _rows(store.path)
    assert ProjectStore.open(backups[0]).load_authoring().storage_revision == 0
    assert old.load_authoring().storage_revision == 1
    old.save(_project(), (_definition(),), expected_storage_revision=1)
    assert list(tmp_path.glob(".zniku-schema2-backup-*.zniku")) == backups


def test_migration_postcheck_failure_rolls_back_every_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _as_schema2(store)
    before = store.path.read_bytes()
    original = ProjectStore._assert_schema_shape

    def fail(
        connection: sqlite3.Connection, *, expected_tables: frozenset[str], expected_columns: Any
    ) -> None:
        if "studio_state" in expected_tables:
            raise ProjectStoreError("E_TEST_MIGRATION", "合成迁移后失败")
        original(connection, expected_tables=expected_tables, expected_columns=expected_columns)

    monkeypatch.setattr(ProjectStore, "_assert_schema_shape", staticmethod(fail))
    with pytest.raises(ProjectStoreError, match="E_TEST_MIGRATION"):
        store.save(_project(), (_definition(),), expected_storage_revision=0, studio_state=_state())
    assert store.path.read_bytes() == before
    backups = list(tmp_path.glob(".zniku-schema2-backup-*.zniku"))
    assert len(backups) == 1 and _rows(backups[0]) == _rows(store.path)


def test_schema2_can_run_without_migration_or_revision_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _as_schema2(store)
    runtime = _runtime(ProjectStore.open(store.path), tmp_path / "work")
    run = runtime.create_run(expected_storage_revision=0)
    assert runtime.run_until_blocked(run.run_id).state.value == "completed"
    assert store.load_authoring().storage_revision == 0
    with closing(sqlite3.connect(store.path)) as connection, connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_migration_backup_collision_never_overwrites_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _as_schema2(store)
    before = store.path.read_bytes()
    backup = tmp_path / ".zniku-schema2-backup-00000000000000000000000000000000.zniku"
    backup.write_bytes(b"existing backup must remain intact")
    monkeypatch.setattr("zniku.project.store.uuid.uuid4", lambda: UUID(int=0))
    with pytest.raises(ProjectStoreError, match="E_PROJECT_MIGRATION_BACKUP"):
        store.save(_project(), (_definition(),), expected_storage_revision=0)
    assert backup.read_bytes() == b"existing backup must remain intact"
    assert store.path.read_bytes() == before


def test_nested_required_uses_keyword_and_composite_errors_stay_closed(tmp_path: Path) -> None:
    nested = _definition().model_copy(
        update={
            "parameter_schema": {
                "type": "object",
                "properties": {
                    "settings": {
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    }
                },
                "required": ["settings"],
                "additionalProperties": False,
            }
        }
    )
    project = _project(parameters={"settings": {}})
    diagnostic = GraphValidator((nested,)).inspect(project.graph)[0]
    assert diagnostic.path == "nodes[0].parameters.settings"
    assert diagnostic.validator_keyword == "required" and diagnostic.authoring_recoverable
    store = ProjectStore.create(tmp_path / "nested.zniku", project, (nested,))
    before = store.path.read_bytes()
    composite = nested.model_copy(
        update={
            "parameter_schema": {
                "type": "object",
                "oneOf": [{"required": ["a"]}, {"required": ["b"]}],
            }
        }
    )
    violation = GraphValidator((composite,)).inspect(project.graph)[0]
    assert violation.validator_keyword == "oneOf" and not violation.authoring_recoverable
    with pytest.raises(ProjectStoreError, match="E_PROJECT_GRAPH_INVALID"):
        store.save(project, (composite,), expected_storage_revision=1)
    assert store.path.read_bytes() == before


def test_cas_concurrent_writers_keep_graph_and_studio_atomic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    barrier = Barrier(2)

    def save(name: str) -> str:
        other = ProjectStore.open(store.path)
        barrier.wait(timeout=5)
        try:
            other.save(
                _project(parameters={"text": name}),
                (_definition(),),
                expected_storage_revision=1,
                studio_state=_state(name),
            )
            return name
        except ProjectStoreError as error:
            assert error.code == "E_PROJECT_STORAGE_CONFLICT"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ("A", "B")))
    assert results.count("conflict") == 1
    current = store.load_authoring()
    assert current.storage_revision == 2
    assert (
        current.snapshot.project.graph.nodes[0].parameters["text"]
        == current.studio_state.node_views[0].display_name
    )


@pytest.mark.parametrize(
    "operation", ["save_project", "run_all", "run_to", "rerun_from_here", "expand_av_enhance_v27"]
)
def test_authoring_commands_require_explicit_binding(operation: str) -> None:
    with pytest.raises(ValidationError) as failure:
        parse_project_service_command({"operation": operation})
    missing_fields = {
        error["loc"][-1] for error in failure.value.errors() if error["type"] == "missing"
    }
    assert {"expected_storage_revision", "project_session_id"} <= missing_fields


def test_session_and_revision_conflicts_do_not_write_or_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    application.command({"operation": "open_project", "path": str(store.path)})
    old = _binding(application)
    application.command({"operation": "open_project", "path": str(store.path)})
    with pytest.raises(ProjectServiceError, match="E_PROJECT_SESSION_CONFLICT"):
        application.command({"operation": "run_all", **old})
    current = _binding(application)
    store.save(_project(), (_definition(),), expected_storage_revision=1)
    before = store.path.read_bytes()
    with pytest.raises(ProjectServiceError, match="E_PROJECT_STORAGE_CONFLICT"):
        application.command(
            {
                "operation": "save_project",
                "project": _project(),
                "studio_state": _state(),
                **current,
            }
        )
    assert store.path.read_bytes() == before
    assert application.inspect().run_summaries == ()


def test_run_cas_is_checked_inside_insert_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store, tmp_path / "work")
    original = runtime.repository.start_run

    def race(*args: Any, **kwargs: Any) -> Any:
        store.save(_project(), (_definition(),), expected_storage_revision=1, studio_state=_state())
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.repository, "start_run", race)
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_CONFLICT"):
        runtime.create_run(expected_storage_revision=1)
    assert runtime.repository.list_runs() == ()


def test_running_snapshot_does_not_change_when_graph_autosaves(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entered, release = Event(), Event()

    def blocked(context: PythonAdapterContext) -> PythonAdapterResult:
        entered.set()
        assert release.wait(timeout=10)
        return _adapter(context)

    application = ProjectServiceApplication(
        work_root=tmp_path / "work", python_adapters={"tests.authoring:data": blocked}
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    started = application.command({"operation": "run_all", **_binding(application)})
    assert entered.wait(timeout=5)
    try:
        saved = application.command(
            {
                "operation": "save_project",
                **_binding(application),
                "project": _project(parameters={"text": "edited"}),
                "studio_state": _state(),
            }
        )
        assert saved.storage_revision == 2
    finally:
        release.set()
    assert application.wait_until_idle(timeout=10)
    assert started.active_run_id is not None
    run = application.inspect_run_detail(started.active_run_id).run
    assert run.graph_snapshot.nodes[0].parameters["text"] == "original"
    assert store.load().project.graph.nodes[0].parameters["text"] == "edited"


def test_authoring_reads_and_migration_release_all_sqlite_handles(tmp_path: Path) -> None:
    """Windows 下不用等待 GC 即可重命名刚读过的工程及迁移备份。"""

    store = _store(tmp_path)
    store.load_authoring()
    moved = tmp_path / "moved.zniku"
    store.path.rename(moved)
    moved.rename(store.path)
    _as_schema2(store)
    store.save(_project(), (_definition(),), expected_storage_revision=0)
    for path in (store.path, *tmp_path.glob(".zniku-schema2-backup-*.zniku")):
        path.rename(moved)
        moved.rename(path)


def test_stale_rerun_service_binding_cannot_run_new_parameters(tmp_path: Path) -> None:
    store = _store(tmp_path)
    application = ProjectServiceApplication(
        work_root=tmp_path / "work", python_adapters={"tests.authoring:data": _adapter}
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    old = _binding(application)
    started = application.command({"operation": "run_all", **old})
    assert application.wait_until_idle(timeout=10)
    store.save(
        _project(parameters={"text": "changed-elsewhere"}),
        (_definition(),),
        expected_storage_revision=1,
    )
    before = _rows(store.path)
    with pytest.raises(ProjectServiceError, match="E_PROJECT_STORAGE_CONFLICT"):
        application.command(
            {
                "operation": "rerun_from_here",
                "run_id": started.active_run_id,
                "node_id": "source",
                **old,
            }
        )
    assert _rows(store.path) == before


def test_new_rerun_cas_conflict_does_not_mark_results_stale(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store, tmp_path / "work")
    runtime.run_until_blocked(runtime.create_run().run_id)
    store.save(_project(), (_definition(),), expected_storage_revision=1, studio_state=_state())
    before = _rows(store.path)
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_CONFLICT"):
        runtime.create_rerun_run("source", expected_storage_revision=1)
    assert _rows(store.path) == before
    assert not runtime.repository.list_latest()[0].stale


def test_rerun_insert_failure_rolls_back_stale_and_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store, tmp_path / "work")
    runtime.run_until_blocked(runtime.create_run().run_id)
    before = _rows(store.path)

    def fail_insert(*_args: Any, **_kwargs: Any) -> None:
        raise sqlite3.IntegrityError("合成插入失败")

    monkeypatch.setattr(runtime.repository, "_insert_run", fail_insert)
    with pytest.raises(RuntimeConflictError, match="E_RUN_START_CONFLICT"):
        runtime.create_rerun_run("source", expected_storage_revision=1)
    assert _rows(store.path) == before


def test_existing_run_rerun_cas_conflict_leaves_attempts_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store, tmp_path / "work")
    running = runtime.create_run()
    store.save(_project(), (_definition(),), expected_storage_revision=1, studio_state=_state())
    before = _rows(store.path)
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_CONFLICT"):
        runtime.rerun_from_start(running.run_id, "source", expected_storage_revision=1)
    assert _rows(store.path) == before


def test_missing_project_during_binding_check_returns_structured_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    application = ProjectServiceApplication(work_root=tmp_path / "work")
    application.command({"operation": "open_project", "path": str(store.path)})
    binding = _binding(application)
    store.path.rename(tmp_path / "moved.zniku")
    with pytest.raises(ProjectServiceError) as failure:
        application.command({"operation": "run_all", **binding})
    assert failure.value.code == "E_PROJECT_NOT_FOUND"
    assert failure.value.http_status == 422


@pytest.mark.parametrize("move_position", [False, True])
def test_rerun_uses_same_running_snapshot_before_and_after_canvas_move(
    tmp_path: Path, move_position: bool
) -> None:
    """布局保存不得把同一 Run 的从头重跑悄悄切换为创建另一 Run。"""

    store = _store(tmp_path)
    runtime = _runtime(store, tmp_path / "work")
    running = runtime.create_run()
    application = ProjectServiceApplication(
        work_root=tmp_path / "work", python_adapters={"tests.authoring:data": _adapter}
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    if move_position:
        project = store.load().project
        moved = project.model_copy(
            update={
                "graph": project.graph.model_copy(
                    update={
                        "nodes": (
                            project.graph.nodes[0].model_copy(
                                update={"ui_position": UiPosition(x=123.0, y=-456.0)}
                            ),
                        )
                    }
                )
            }
        )
        application.command(
            {
                "operation": "save_project",
                "project": moved,
                "studio_state": _state(),
                **_binding(application),
            }
        )
    response = application.command(
        {
            "operation": "rerun_from_here",
            "run_id": running.run_id,
            "node_id": "source",
            **_binding(application),
        }
    )
    assert response.active_run_id == running.run_id
    assert application.wait_until_idle(timeout=10)
    finished = runtime.repository.get_run(running.run_id)
    assert len(runtime.repository.list_runs()) == 1
    assert finished.graph_snapshot == running.graph_snapshot
    assert finished.node_runs[-1].attempt == 2
    assert finished.node_runs[-1].state.value == "completed"
    assert application.inspect().error is None


def test_existing_run_rerun_still_rejects_changed_parameters_with_current_revision(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store, tmp_path / "work")
    running = runtime.create_run()
    revision = store.save(
        _project(parameters={"text": "changed"}), (_definition(),), expected_storage_revision=1
    )
    before = _rows(store.path)
    with pytest.raises(RuntimeConflictError, match="E_RUN_SNAPSHOT_MISMATCH"):
        runtime.rerun_from_start(running.run_id, "source", expected_storage_revision=revision)
    assert _rows(store.path) == before
