"""用合成文本验证英文实体布局、跨 Run 轮次、旧布局兼容及显式迁移，不访问用户数据。"""

from __future__ import annotations

import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import zniku.project_service.storage as storage_module
from test_readable_runtime_layout import _definition, _write
from zniku.graph import Graph, NodeInstance
from zniku.project import Project, ProjectStore, ProjectStoreError
from zniku.project.storage import ProjectStorage, new_project_storage
from zniku.project.storage_english import (
    EnglishStorageState,
    allocate_english_attempt,
    reserve_english_nodes,
)
from zniku.project.storage_layout import (
    AttemptLocationRequest,
    AttemptNamingHint,
    safe_attempt_directory,
)
from zniku.project_service.storage import StorageMigrationManager
from zniku.project_service.storage_paths import prepare_storage_location
from zniku.runtime import (
    NodeRunState,
    RuntimeConflictError,
    RuntimeDataError,
    RuntimeRepositoryError,
    RuntimeService,
    RuntimeServiceError,
)


def _project(tmp_path: Path, *, manual: bool = False) -> tuple[ProjectStore, RuntimeService]:
    definition = _definition(manual=manual)
    storage = new_project_storage(tmp_path / "english.zniku")
    prepare_storage_location(storage, current=None)
    store = ProjectStore.create(
        tmp_path / "english.zniku",
        Project(
            project_id="test.english-storage",
            name="Synthetic",
            graph=Graph(
                nodes=(
                    NodeInstance(
                        node_id="n", type_id=definition.type_id, definition_version="1.0.0"
                    ),
                )
            ),
        ),
        (definition,),
        storage=storage,
    )
    return store, _runtime(store)


def _runtime(store: ProjectStore, *, label: str = "enhancement") -> RuntimeService:
    storage = store.load_storage()
    assert storage is not None
    return RuntimeService(
        store,
        storage.attempts_root,
        python_adapters={"tests.readable:write": _write},
        attempt_naming_resolver=lambda *_: AttemptNamingHint(
            category="chapters", task_name=label, chapter_index=1, chapter_name="A"
        ),
    )


def test_default_root_has_no_attempts_and_rounds_only_for_actual_processing(tmp_path: Path) -> None:
    store, runtime = _project(tmp_path)
    config = store.load_storage()
    assert config is not None and config.attempts_root == config.data_root
    first = runtime.create_run()
    pending = first.node_runs[0]
    assert Path(pending.work_dir).name == UUID(pending.node_run_id).hex
    assert not Path(pending.work_dir).exists()
    configured = store.load_storage()
    assert configured is not None and not configured.english_layout_state.attempts
    completed = runtime.run_until_blocked(first.run_id)
    path = Path(completed.node_runs[0].work_dir)
    assert path == Path(config.data_root) / "enhancement/A/round-001"
    assert not (Path(config.data_root) / "attempts").exists()
    before = store.load_storage()
    reused_runtime = _runtime(ProjectStore.open(store.path), label="renamed")
    reused = reused_runtime.run_until_blocked(reused_runtime.create_run().run_id).node_runs[0]
    assert reused.reused_from_result_id is not None and not Path(reused.work_dir).exists()
    assert store.load_storage() == before
    runtime.abandon_run(runtime.create_run().run_id)
    assert store.load_storage() == before
    rerun = runtime.create_run(rerun_from_node_id="n")
    again = runtime.run_until_blocked(rerun.run_id).node_runs[0]
    assert Path(again.work_dir) == path.parent / "round-002"
    assert path.exists() and "renamed" not in again.work_dir


def test_pending_allocation_is_concurrent_idempotent_and_atomic(tmp_path: Path) -> None:
    store, runtime = _project(tmp_path)
    pending = runtime.create_run().node_runs[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        allocations = tuple(
            pool.map(runtime.repository.allocate_execution_storage, [pending.node_run_id] * 2)
        )
    assert allocations[0] == allocations[1]
    assert Path(allocations[0].work_dir).name == "round-001"
    assert not Path(allocations[0].work_dir).exists()
    storage = store.load_storage()
    assert storage is not None and len(storage.english_layout_state.attempts) == 1


def test_english_manual_retry_retains_old_round_and_rejects_late_submit(tmp_path: Path) -> None:
    _store, runtime = _project(tmp_path, manual=True)
    waiting = runtime.run_until_blocked(runtime.create_run().run_id)
    old = waiting.node_runs[0]
    assert old.external_handoff is not None
    target = Path(old.external_handoff.output_targets[0].path)
    target.write_text("original external result", encoding="utf-8")
    retried = runtime.rerun_from_start(waiting.run_id, "n")
    current = retried.node_runs[-1]
    assert current.state is NodeRunState.WAITING_EXTERNAL
    assert Path(current.work_dir) == Path(old.work_dir).parent / "round-002"
    assert target.read_text(encoding="utf-8") == "original external result"
    with pytest.raises((RuntimeConflictError, RuntimeServiceError), match="E_"):
        runtime.submit_external(old.node_run_id)


def test_english_node_names_unique_safe_and_frozen(tmp_path: Path) -> None:
    run = str(uuid4())
    hint = AttemptNamingHint(
        category="chapters", task_name="enhancement", chapter_index=1, chapter_name="A"
    )
    state = reserve_english_nodes(
        EnglishStorageState(),
        (
            AttemptLocationRequest("a", run, 1, hint),
            AttemptLocationRequest("b", run, 1, hint),
            AttemptLocationRequest("c", run, 1, AttemptNamingHint(task_name="enhancement")),
            AttemptLocationRequest("d", run, 1, hint.model_copy(update={"chapter_name": "CON"})),
        ),
    )
    assert state.nodes["a"].relative_dir == "enhancement/A"
    assert state.nodes["b"].relative_dir == "enhancement-2/A"
    assert state.nodes["c"].relative_dir == "enhancement-3"
    assert state.nodes["d"].relative_dir == "enhancement/chapter-001"
    assert reserve_english_nodes(state, (AttemptLocationRequest("a", run, 2),)) == state
    identity = str(uuid4())
    allocated, path = allocate_english_attempt(
        state, root=tmp_path, node_id="a", node_run_id=identity
    )
    assert allocate_english_attempt(
        allocated, root=tmp_path, node_id="a", node_run_id=identity
    ) == (allocated, path)
    assert EnglishStorageState.model_validate_json(allocated.model_dump_json()) == allocated


@pytest.mark.parametrize(
    "relative",
    [
        "task/round-000",
        "Task/round-001",
        "task/../round-001",
        "task/A/round-001/child",
        "CON/round-001",
        "task/CON/round-001",
    ],
)
def test_english_paths_fail_closed(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError):
        safe_attempt_directory(tmp_path, tmp_path / relative)


def test_english_version_and_bound_round_corruption_fail_closed(tmp_path: Path) -> None:
    store, runtime = _project(tmp_path)
    completed = runtime.run_until_blocked(runtime.create_run().run_id)
    storage = store.load_storage()
    assert storage is not None
    with pytest.raises(ValidationError, match="E_PROJECT_STORAGE_VERSION"):
        ProjectStorage.model_validate(storage.model_dump() | {"contract_version": "0.3.2"})
    payload = storage.model_dump(mode="json")
    payload["english_layout_state"]["attempts"] = {}
    node_run = completed.node_runs[0]
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE project_storage SET payload_json=?", (json.dumps(payload),))
        connection.execute(
            "UPDATE node_runs SET work_dir=?",
            (str(Path(storage.data_root) / UUID(node_run.node_run_id).hex),),
        )
    with pytest.raises(RuntimeDataError, match="E_NODE_RUN_STORAGE_BINDING"):
        runtime.repository.get_run(completed.run_id)


def test_english_relocate_and_restore_preserve_rounds_and_originals(tmp_path: Path) -> None:
    store, runtime = _project(tmp_path)
    completed = runtime.run_until_blocked(runtime.create_run().run_id)
    original = Path(completed.node_runs[0].work_dir)
    source = store.load_storage()
    assert source is not None
    manager = StorageMigrationManager()
    target = new_project_storage(store.path, data_root=tmp_path / "moved.data")
    preview = manager.preview(
        store,
        legacy_root=source.attempts_root,
        target=target,
        project_session_id="test",
        expected_storage_revision=store.load_authoring().storage_revision,
    )
    assert (
        preview.target.layout == "english"
        and preview.target.english_layout_state == source.english_layout_state
    )
    manager.confirm(
        store,
        ticket_id=preview.ticket_id,
        project_session_id="test",
        expected_storage_revision=store.load_authoring().storage_revision,
    )
    assert original.exists()
    assert (
        Path(runtime.repository.get_run(completed.run_id).node_runs[0].work_dir)
        == Path(target.data_root) / "enhancement/A/round-001"
    )
    restored = tmp_path / "restored.data"
    shutil.copytree(target.data_root, restored)
    preview = manager.preview_restore(
        store,
        legacy_root=source.attempts_root,
        target=new_project_storage(store.path, data_root=restored),
        project_session_id="test",
        expected_storage_revision=store.load_authoring().storage_revision,
    )
    manager.confirm(
        store,
        ticket_id=preview.ticket_id,
        project_session_id="test",
        expected_storage_revision=store.load_authoring().storage_revision,
    )
    assert store.load_storage() is not None
    assert (
        _runtime(store)
        .run_until_blocked(_runtime(store).create_run().run_id)
        .node_runs[0]
        .reused_from_result_id
    )


def test_english_storage_binding_transaction_rolls_back(tmp_path: Path) -> None:
    store, runtime = _project(tmp_path)
    pending = runtime.create_run().node_runs[0]
    before = store.load_storage()
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_storage_update BEFORE UPDATE OF work_dir ON node_runs "
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
    with pytest.raises(RuntimeRepositoryError, match="E_RUNTIME_STORAGE_WRITE"):
        runtime.repository.allocate_execution_storage(pending.node_run_id)
    assert store.load_storage() == before
    assert runtime.repository.get_node_run(pending.node_run_id) == pending


def test_waiting_english_task_cannot_be_organized(tmp_path: Path) -> None:
    store, runtime = _project(tmp_path, manual=True)
    waiting = runtime.run_until_blocked(runtime.create_run().run_id)
    before = store.path.read_bytes()
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_ACTIVE"):
        StorageMigrationManager().preview(
            store,
            legacy_root=tmp_path,
            target=new_project_storage(store.path, data_root=tmp_path / "new.data"),
            project_session_id="test",
            expected_storage_revision=store.load_authoring().storage_revision,
            organize=True,
        )
    assert store.path.read_bytes() == before
    assert Path(waiting.node_runs[0].work_dir).exists()


def test_organize_preserves_allocated_missing_round_and_unallocated_cancel(tmp_path: Path) -> None:
    store, runtime = _project(tmp_path)
    first = runtime.create_run()
    bound = runtime.repository.allocate_execution_storage(first.node_runs[0].node_run_id)
    runtime.abandon_run(first.run_id)
    runtime.abandon_run(runtime.create_run().run_id)
    original = store.load_storage()
    assert original is not None and len(original.english_layout_state.attempts) == 1
    manager = StorageMigrationManager()
    target = new_project_storage(store.path, data_root=tmp_path / "organized.data")
    preview = manager.preview(
        store,
        legacy_root=original.attempts_root,
        target=target,
        project_session_id="test",
        expected_storage_revision=store.load_authoring().storage_revision,
        organize=True,
    )
    assert preview.target.english_layout_state == original.english_layout_state
    manager.confirm(
        store,
        ticket_id=preview.ticket_id,
        project_session_id="test",
        expected_storage_revision=store.load_authoring().storage_revision,
    )
    migrated = runtime.repository.get_node_run(bound.node_run_id)
    assert Path(migrated.work_dir) == Path(target.data_root) / "enhancement/A/round-001"
    assert not Path(migrated.work_dir).exists()
    runtime = _runtime(store)
    completed = runtime.run_until_blocked(runtime.create_run().run_id)
    assert Path(completed.node_runs[0].work_dir).name == "round-002"


def test_organize_backup_failure_keeps_original_database_and_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, runtime = _project(tmp_path)
    completed = runtime.run_until_blocked(runtime.create_run().run_id)
    old = Path(completed.node_runs[0].work_dir)
    original = store.load_storage()
    assert original is not None
    manager = StorageMigrationManager()
    target = new_project_storage(store.path, data_root=tmp_path / "organized.data")
    preview = manager.preview(
        store,
        legacy_root=original.attempts_root,
        target=target,
        project_session_id="test",
        expected_storage_revision=store.load_authoring().storage_revision,
        organize=True,
    )
    before = store.path.read_bytes()

    def fail_backup(_store: ProjectStore) -> Path:
        raise ProjectStoreError("E_PROJECT_STORAGE_BACKUP", "synthetic backup failure")

    monkeypatch.setattr(storage_module, "_backup_before_organize", fail_backup)
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_BACKUP"):
        manager.confirm(
            store,
            ticket_id=preview.ticket_id,
            project_session_id="test",
            expected_storage_revision=store.load_authoring().storage_revision,
        )
    assert store.path.read_bytes() == before and old.exists()
    assert Path(target.data_root).exists()


def test_bulk_english_names_remain_unique_without_files_or_rounds(tmp_path: Path) -> None:
    run_id = str(uuid4())
    state = reserve_english_nodes(
        EnglishStorageState(),
        tuple(AttemptLocationRequest(f"node-{index}", run_id, 1) for index in range(1000)),
    )
    assert len({item.relative_dir for item in state.nodes.values()}) == 1000
    assert state.nodes["node-0"].relative_dir == "task"
    assert state.nodes["node-999"].relative_dir == "task-1000"
    assert not state.attempts and not tuple(tmp_path.iterdir())


def test_direct_start_without_english_binding_rejects_before_commit(tmp_path: Path) -> None:
    store, runtime = _project(tmp_path)
    pending = runtime.create_run().node_runs[0]
    before = store.path.read_bytes()
    with pytest.raises(RuntimeConflictError, match="E_RUNTIME_STORAGE_BINDING"):
        runtime.repository.transition_node_run(
            pending.node_run_id, NodeRunState.RUNNING, occurred_at=datetime.now(UTC)
        )
    assert store.path.read_bytes() == before
    assert runtime.repository.get_node_run(pending.node_run_id) == pending
