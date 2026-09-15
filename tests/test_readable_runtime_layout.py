"""用合成文本验证可读目录、事务编号、外部等待和旧 UUID 兼容；不访问用户工程或媒体。"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import zniku.runtime.repository as repository_module
from zniku.graph import (
    ExecutionMode,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.project import Project, ProjectStore
from zniku.project.storage import ProjectStorage, new_project_storage
from zniku.project.storage_layout import (
    AttemptLocationRequest,
    AttemptNamingHint,
    StorageLayoutState,
    StorageNodeLocation,
    allocate_attempt_path,
    allocate_attempt_paths,
    safe_attempt_directory,
)
from zniku.runtime import (
    NodeRun,
    NodeRunState,
    PythonAdapterContext,
    PythonAdapterResult,
    Run,
    RunnerError,
    RunState,
    RuntimeConflictError,
    RuntimeDataError,
    RuntimeService,
    RuntimeServiceError,
    new_runtime_id,
)
from zniku.runtime.runner import NodeExecutionRequest, NodeRunner, OutputPathSpec


def _definition(*, manual: bool = False) -> NodeDefinition:
    return NodeDefinition(
        type_id="test.readable",
        version="1.0.0",
        output_ports=(PortSpec(port_id="data", data_type="DataFile"),),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL if manual else ExecutionMode.AUTOMATIC,
        executor=ManualExternalExecutorSpec(instructions="复制合成文本，确认后提交")
        if manual
        else PythonExecutorSpec(adapter="tests.readable:write"),
    )


def _write(context: PythonAdapterContext) -> PythonAdapterResult:
    context.outputs[0].path.write_text("合成测试产物", encoding="utf-8")
    return PythonAdapterResult()


def _store(tmp_path: Path, *, manual: bool = False, readable: bool = True) -> ProjectStore:
    definition = _definition(manual=manual)
    config = new_project_storage(tmp_path / "p.zniku")
    if not readable:
        payload = config.model_dump(mode="json")
        payload["contract_version"] = "0.3.0"
        payload.pop("layout")
        payload.pop("layout_state")
        payload.pop("data_id")
        config = ProjectStorage.model_validate(payload)
    return ProjectStore.create(
        tmp_path / "p.zniku",
        Project(
            project_id="readable-test",
            name="合成工程",
            graph=Graph(
                nodes=tuple(
                    NodeInstance(
                        node_id=node_id, type_id=definition.type_id, definition_version="1.0.0"
                    )
                    for node_id in ("node-b", "node-a")
                )
            ),
        ),
        (definition,),
        storage=config,
    )


def _runtime(store: ProjectStore, *, label: str = "同名任务") -> RuntimeService:
    config = store.load_storage()
    assert config is not None
    return RuntimeService(
        store,
        config.attempts_root,
        python_adapters={"tests.readable:write": _write},
        attempt_naming_resolver=lambda _run, _node, _definition: AttemptNamingHint(
            category="chapters", task_name=label, chapter_index=1, chapter_name="A"
        ),
    )


def test_missing_storage_fields_remain_uuid_and_read_does_not_write(tmp_path: Path) -> None:
    store = _store(tmp_path, readable=False)
    before = store.path.read_bytes()
    config = store.load_storage()
    assert config is not None and config.layout == "uuid"
    assert store.path.read_bytes() == before
    runtime = _runtime(store)
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    assert run.state is RunState.COMPLETED
    for attempt in run.node_runs:
        assert Path(attempt.work_dir) == Path(config.attempts_root) / UUID(attempt.node_run_id).hex
    assert store.load_storage() == config


def test_bind_before_mkdir_same_name_unique_and_reuse_keeps_original(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store)
    revision = store.load_authoring().storage_revision
    run = runtime.create_run()
    config = store.load_storage()
    assert config is not None
    assert config.layout_state.nodes["node-a"].number == 1
    assert config.layout_state.nodes["node-b"].number == 2
    assert len({item.work_dir for item in run.node_runs}) == 2
    for attempt in run.node_runs:
        assert not Path(attempt.work_dir).exists()
        assert "chapters/0001-A/同名任务__N" in Path(attempt.work_dir).as_posix()
        assert Path(attempt.work_dir).name == "R001-A001"
    assert store.load_authoring().storage_revision == revision
    completed = runtime.run_until_blocked(run.run_id)
    assert completed.state is RunState.COMPLETED
    previous = {item.node_id: item.output_artifact_ids for item in completed.node_runs}
    reopened = _runtime(ProjectStore.open(store.path), label="改名不能移动旧文件")
    again = reopened.run_until_blocked(reopened.create_run().run_id)
    assert again.state is RunState.COMPLETED
    for item in again.node_runs:
        assert item.reused_from_result_id is not None
        assert item.output_artifact_ids == previous[item.node_id]
        assert not Path(item.work_dir).exists()
        assert "同名任务__N" in item.work_dir and "改名" not in item.work_dir
        assert Path(item.work_dir).name == "R002-A001"
    assert store.load_authoring().storage_revision == revision


def test_readable_retry_is_separate_and_late_external_submit_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path, manual=True)
    runtime = _runtime(store)
    waiting = runtime.run_until_blocked(runtime.create_run().run_id)
    old = next(item for item in waiting.node_runs if item.node_id == "node-a")
    assert old.state is NodeRunState.WAITING_EXTERNAL
    assert old.external_handoff is not None
    old_target = Path(old.external_handoff.output_targets[0].path)
    old_target.write_text("保留旧来件", encoding="utf-8")
    retried = runtime.rerun_from_start(waiting.run_id, "node-a")
    new = max(
        (item for item in retried.node_runs if item.node_id == "node-a"),
        key=lambda item: item.attempt,
    )
    assert new.attempt == 2 and new.node_run_id != old.node_run_id
    assert Path(new.work_dir).parent == Path(old.work_dir).parent
    assert Path(new.work_dir).name == "R001-A002"
    assert list((Path(new.work_dir) / "incoming").iterdir())
    assert old_target.read_text(encoding="utf-8") == "保留旧来件"
    with pytest.raises((RuntimeServiceError, RuntimeConflictError)):
        runtime.submit_external(old.node_run_id)
    assert runtime.repository.get_node_run(new.node_run_id).state is NodeRunState.WAITING_EXTERNAL
    runtime.abandon_run(waiting.run_id)
    assert old_target.is_file() and Path(new.work_dir).is_dir()


def test_insert_failure_rolls_back_mapping_run_and_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store)
    original = store.load_storage()
    calls = 0
    insert = runtime.repository._insert_node_run

    def broken(connection: sqlite3.Connection, node_run: NodeRun) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeConflictError("E_SYNTHETIC_INSERT", "模拟第二条 INSERT 失败")
        insert(connection, node_run)

    monkeypatch.setattr(runtime.repository, "_insert_node_run", broken)
    with pytest.raises(RuntimeConflictError, match="E_SYNTHETIC_INSERT"):
        runtime.create_run()
    assert store.load_storage() == original
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM node_runs").fetchone()[0] == 0
    assert original is not None and not list(Path(original.attempts_root).iterdir())


def test_queued_abandon_binds_paths_without_creating_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store)
    snapshot = store.load()
    pending = Run.pending(
        project_id=snapshot.project.project_id,
        graph_snapshot=snapshot.project.graph,
        definitions_snapshot=snapshot.definitions,
    )
    runtime.repository.create_run(pending)
    cancelled = runtime.abandon_run(pending.run_id)
    assert cancelled.state is RunState.FAILED
    assert all(not Path(item.work_dir).exists() for item in cancelled.node_runs)
    config = store.load_storage()
    assert config is not None and config.layout_state.runs[pending.run_id] == 1


def test_concurrent_creations_allocate_different_run_numbers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtimes = (_runtime(store), _runtime(store))
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = tuple(pool.map(lambda runtime: runtime.create_run(), runtimes))
    config = store.load_storage()
    assert config is not None and set(config.layout_state.runs.values()) == {1, 2}
    assert len({item.work_dir for run in runs for item in run.node_runs}) == 4


def test_path_allocation_is_pure_frozen_and_roundtrips(tmp_path: Path) -> None:
    empty = StorageLayoutState()
    run_id = str(uuid4())
    first, path = allocate_attempt_path(
        empty,
        root=tmp_path,
        node_id="n",
        run_id=run_id,
        attempt=1,
        hint=AttemptNamingHint(category="program", task_name="合并/编码:预览"),
    )
    assert not path.exists() and not empty.nodes
    assert "合并_编码_预览__N001" in str(path)
    second, next_path = allocate_attempt_path(
        first,
        root=tmp_path,
        node_id="n",
        run_id=run_id,
        attempt=2,
        hint=AttemptNamingHint(category="common", task_name="改名"),
    )
    assert next_path.parent == path.parent and next_path.name == "R001-A002"
    assert StorageLayoutState.model_validate_json(second.model_dump_json()) == second


@pytest.mark.parametrize(
    "relative",
    [
        ".",
        "custom",
        "custom/task__N001",
        "../escape",
        "custom/CON/R001-A001",
        "custom/task__N001/R001-A001/child",
    ],
)
def test_unsafe_or_non_attempt_paths_are_rejected(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="E_STORAGE_LAYOUT"):
        safe_attempt_directory(tmp_path, tmp_path / relative)


def test_links_and_existing_file_parents_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "custom").write_text("不是目录", encoding="utf-8")
    with pytest.raises(ValueError, match="E_STORAGE_LAYOUT_PATH"):
        safe_attempt_directory(tmp_path, tmp_path / "custom/task__N001/R001-A001")


def test_posix_not_a_directory_is_a_stable_path_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """在Windows也模拟POSIX子路径lstat错误，不能因平台差异泄漏异常类型。"""

    target = tmp_path / "custom/task__N001/R001-A001"
    original = Path.lstat

    def not_a_directory(candidate: Path) -> os.stat_result:
        if candidate == target:
            raise NotADirectoryError("祖先是文件")
        return original(candidate)

    monkeypatch.setattr(Path, "lstat", not_a_directory)
    with pytest.raises(ValueError, match="E_STORAGE_LAYOUT_PATH"):
        safe_attempt_directory(tmp_path, target)
    assert not (tmp_path / "custom").exists()


def test_reparse_parent_is_rejected_before_directory_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.lstat

    def reparse(candidate: Path) -> os.stat_result:
        if candidate == tmp_path / "custom":
            return cast(
                os.stat_result, SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            )
        return original(candidate)

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ValueError, match="E_STORAGE_LAYOUT_REPARSE"):
        safe_attempt_directory(tmp_path, tmp_path / "custom/task__N001/R001-A001")
    assert not (tmp_path / "custom").exists()


def test_readable_root_budget_fails_without_allocating(tmp_path: Path) -> None:
    root = tmp_path / ("x" * 100)
    with pytest.raises(ValueError, match="E_STORAGE_LAYOUT_PATH_BUDGET"):
        allocate_attempt_path(
            StorageLayoutState(), root=root, node_id="n", run_id=str(uuid4()), attempt=1
        )
    assert not root.exists()


def test_layout_maps_reject_duplicate_numbers_and_unknown_fields() -> None:
    location = StorageNodeLocation(number=1, relative_dir="custom/任务__N001")
    with pytest.raises(ValidationError, match="E_STORAGE_LAYOUT_UNIQUE"):
        StorageLayoutState(nodes={"n1": location, "n2": location})
    with pytest.raises(ValidationError):
        StorageLayoutState.model_validate_json(json.dumps({"next": 2}))
    with pytest.raises(ValidationError, match="E_STORAGE_LAYOUT_DIRECTORY"):
        StorageNodeLocation(number=2, relative_dir="custom/任务__N001")


def test_runner_uses_bound_layout_and_refuses_existing_attempt(tmp_path: Path) -> None:
    definition = _definition()
    runner = NodeRunner(tmp_path, python_adapters={"tests.readable:write": _write})
    request = NodeExecutionRequest(
        node_run_id=str(uuid4()),
        attempt=1,
        definition=definition,
        node=NodeInstance(node_id="n", type_id=definition.type_id, definition_version="1.0.0"),
        work_dir=tmp_path / "custom/可读任务__N001/R001-A001",
    )
    result = runner.run_automatic(request)
    assert result.work_dir == request.work_dir
    with pytest.raises(RunnerError, match="E_RUNNER_ATTEMPT_EXISTS"):
        runner.run_automatic(request)
    assert result.artifacts[0].path.read_text(encoding="utf-8") == "合成测试产物"
    with pytest.raises(RunnerError, match="E_RUNNER_PATH_ESCAPE"):
        runner.run_automatic(replace(request, node_run_id=str(uuid4()), work_dir=tmp_path.parent))


def test_long_declared_output_fails_without_silently_renaming(tmp_path: Path) -> None:
    definition = _definition()
    runner = NodeRunner(tmp_path, python_adapters={"tests.readable:write": _write})
    request = NodeExecutionRequest(
        node_run_id=str(uuid4()),
        attempt=1,
        definition=definition,
        node=NodeInstance(node_id="n", type_id=definition.type_id, definition_version="1.0.0"),
        work_dir=tmp_path / "custom/任务__N001/R001-A001",
        output_paths=(OutputPathSpec(port_id="data", relative_path="x" * 220 + ".out"),),
    )
    with pytest.raises(RunnerError, match="E_RUNNER_PATH_BUDGET"):
        runner.run_automatic(request)
    assert request.work_dir is not None and not list((request.work_dir / "outputs").iterdir())


def test_corrupt_mapping_is_not_reconstructed_from_history_or_directory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store)
    run = runtime.create_run()
    config = store.load_storage()
    assert config is not None
    payload = config.model_dump(mode="json")
    payload["layout_state"]["nodes"] = {}
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE project_storage SET payload_json = ?", (json.dumps(payload),))
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_STORAGE_BINDING"):
        runtime.repository.get_run(run.run_id)


def test_bulk_1000_locations_are_unique_stable_and_create_nothing(tmp_path: Path) -> None:
    original = StorageLayoutState()
    run_id = str(uuid4())
    requests = tuple(
        AttemptLocationRequest(f"node-{index:04d}", run_id, 1) for index in range(1000)
    )
    state, paths = allocate_attempt_paths(original, root=tmp_path, requests=requests)
    assert len(state.nodes) == len(set(paths)) == 1000
    assert state.runs == {run_id: 1}
    assert tuple(item.number for item in state.nodes.values()) == tuple(range(1, 1001))
    assert not original.nodes and not list(tmp_path.iterdir())
    repeated, repeated_paths = allocate_attempt_paths(state, root=tmp_path, requests=requests)
    assert repeated == state and repeated_paths == paths
    with pytest.raises(ValueError, match="E_STORAGE_LAYOUT_ATTEMPT"):
        allocate_attempt_paths(
            state,
            root=tmp_path,
            requests=(
                AttemptLocationRequest("new", run_id, 1),
                AttemptLocationRequest("bad", run_id, 0),
            ),
        )
    assert "new" not in state.nodes and not list(tmp_path.iterdir())


def test_bulk_and_single_allocator_produce_the_same_mapping(tmp_path: Path) -> None:
    original = StorageLayoutState()
    identities = (str(uuid4()), str(uuid4()))
    requests = (
        AttemptLocationRequest("n2", identities[0], 1, AttemptNamingHint(category="common")),
        AttemptLocationRequest("n1", identities[0], 1),
        AttemptLocationRequest("n2", identities[0], 2),
        AttemptLocationRequest("n1", identities[1], 1),
    )
    bulk, paths = allocate_attempt_paths(original, root=tmp_path, requests=requests)
    single, single_paths = original, []
    for request in requests:
        single, path = allocate_attempt_path(
            single,
            root=tmp_path,
            node_id=request.node_id,
            run_id=request.run_id,
            attempt=request.attempt,
            hint=request.hint,
        )
        single_paths.append(path)
    assert bulk == single and paths == tuple(single_paths)


def test_runtime_1000_node_bindings_use_one_bulk_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store)
    config = store.load_storage()
    assert config is not None
    run_id = str(uuid4())
    candidates = tuple(
        NodeRun.pending(
            node_run_id=(identity := new_runtime_id()),
            run_id=run_id,
            node_id=f"node-{index:04d}",
            definition_version="1.0.0",
            attempt=1,
            input_artifact_ids=(),
            work_dir=str(Path(config.attempts_root) / UUID(identity).hex),
        )
        for index in range(1000)
    )
    calls = 0

    def counted(
        state: StorageLayoutState,
        *,
        root: Path,
        requests: tuple[AttemptLocationRequest, ...],
    ) -> tuple[StorageLayoutState, tuple[Path, ...]]:
        nonlocal calls
        calls += 1
        return allocate_attempt_paths(state, root=root, requests=requests)

    monkeypatch.setattr(repository_module, "allocate_attempt_paths", counted)
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        bound = runtime.repository._bind_storage_paths(connection, candidates, None)
        assert len({item.work_dir for item in bound}) == 1000 and calls == 1
        persisted = ProjectStore._read_project_storage(connection)
        assert persisted is not None and len(persisted.layout_state.nodes) == 1000
        connection.rollback()
    assert store.load_storage() == config
    assert not list(Path(config.attempts_root).iterdir())


def test_data_root_identity_is_optional_for_legacy_and_strict_for_new(tmp_path: Path) -> None:
    new = new_project_storage(tmp_path / "new.zniku")
    assert new.data_id is not None and UUID(new.data_id).version == 4
    payload = new.model_dump(mode="json")
    payload.pop("data_id")
    assert ProjectStorage.model_validate(payload).data_id is None
    for invalid in ("../root", str(uuid4()).upper(), "00000000-0000-1000-8000-000000000000"):
        with pytest.raises(ValidationError):
            ProjectStorage.model_validate({**payload, "data_id": invalid})
