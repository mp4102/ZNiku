"""使用纯合成 SQLite、文本产物和临时目录验证工程存储与显式迁移，不访问用户媒体。"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from zniku.graph import (
    ExecutionMode,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.project import Project, ProjectStore, ProjectStoreError
from zniku.project.storage import ProjectStorage, legacy_project_storage, new_project_storage
from zniku.project_service.storage import StorageMigrationManager, inspect_storage
from zniku.runtime import PythonAdapterContext, PythonAdapterResult, RuntimeService


def _store(
    tmp_path: Path, *, storage: ProjectStorage | None = None, manual: bool = False
) -> ProjectStore:
    definition = NodeDefinition(
        type_id="test.storage",
        version="1.0.0",
        output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
        execution_mode=ExecutionMode.MANUAL_EXTERNAL if manual else ExecutionMode.AUTOMATIC,
        executor=ManualExternalExecutorSpec(instructions="合成手工导入")
        if manual
        else PythonExecutorSpec(adapter="tests.storage:source"),
    )
    return ProjectStore.create(
        tmp_path / "synthetic.zniku",
        Project(
            project_id="project.storage",
            name="存储合成工程",
            graph=Graph(
                nodes=(
                    NodeInstance(
                        node_id="source",
                        type_id=definition.type_id,
                        definition_version=definition.version,
                    ),
                )
            ),
        ),
        (definition,),
        storage=storage,
    )


def _adapter(context: PythonAdapterContext) -> PythonAdapterResult:
    context.outputs[0].path.write_bytes(b"synthetic data\x00" * 8)
    context.stdout_log_path.write_text("合成日志，不是媒体", encoding="utf-8")
    return PythonAdapterResult()


def _runtime(store: ProjectStore, root: Path) -> RuntimeService:
    return RuntimeService(store, root, python_adapters={"tests.storage:source": _adapter})


def _version(store: ProjectStore) -> int:
    with closing(sqlite3.connect(store.path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _as_version(store: ProjectStore, version: int) -> None:
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("DROP TABLE project_storage")
        if version == 2:
            connection.execute("DROP TABLE studio_state")
        connection.execute(f"PRAGMA user_version = {version}")


def _preview(
    manager: StorageMigrationManager, store: ProjectStore, root: Path, target: Path
) -> Any:
    return manager.preview(
        store,
        legacy_root=root,
        target=new_project_storage(store.path, data_root=target),
        project_session_id="session-synthetic",
        expected_storage_revision=store.load_authoring().storage_revision,
    )


def _confirm(manager: StorageMigrationManager, store: ProjectStore, ticket: Any) -> Any:
    return manager.confirm(
        store,
        ticket_id=ticket.ticket_id,
        project_session_id=ticket.project_session_id,
        expected_storage_revision=ticket.expected_storage_revision,
    )


def test_default_adjacent_custom_and_legacy_paths_are_pure(tmp_path: Path) -> None:
    project = tmp_path / "synthetic.zniku"
    adjacent = new_project_storage(project, media_basename="TITLE (2026)")
    assert adjacent.mode == "adjacent" and Path(adjacent.data_root) == tmp_path / "synthetic.data"
    assert Path(adjacent.attempts_root) == tmp_path / "synthetic.data" / "attempts"
    assert not Path(adjacent.data_root).exists()
    custom = new_project_storage(project, data_root=tmp_path / "other")
    assert custom.mode == "custom"
    assert legacy_project_storage(tmp_path / "old").attempts_root == str(tmp_path / "old")
    assert ProjectStorage.model_validate_json(adjacent.model_dump_json()) == adjacent


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "cache"),
        ("retention", "delete"),
        ("data_root", "relative/data"),
        ("attempts_root", "../attempts"),
        ("contract_version", "unknown"),
        ("media_basename", "../escape"),
        ("media_basename", "CON"),
        ("media_basename", "name."),
        ("media_basename", "bad\x00name"),
        ("unknown", "value"),
    ],
)
def test_storage_rejects_unknown_and_unsafe_inputs(tmp_path: Path, field: str, value: str) -> None:
    payload = new_project_storage(tmp_path / "a.zniku").model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        ProjectStorage.model_validate(payload)


def test_new_store_and_atomic_create_keep_final_name_config(tmp_path: Path) -> None:
    config = new_project_storage(tmp_path / "synthetic.zniku")
    store = _store(tmp_path, storage=config)
    assert _version(store) == 4 and store.load_storage() == config
    other_path = tmp_path / "other.zniku"
    snapshot = store.load()
    other_config = new_project_storage(other_path)
    other = ProjectStore.create_atomically(
        other_path, snapshot.project, snapshot.definitions, storage=other_config
    )
    assert other.load_storage() == other_config
    assert not Path(other_config.data_root).exists()


@pytest.mark.parametrize("version", [2, 3])
def test_legacy_read_is_byte_unchanged_and_explicit_write_backs_up(
    tmp_path: Path, version: int
) -> None:
    store = _store(tmp_path)
    _as_version(store, version)
    original = store.path.read_bytes()
    snapshot = store.load()
    assert ProjectStore.open(store.path).load_storage() is None
    inspection = inspect_storage(store, tmp_path / "old")
    assert inspection.storage.mode == "legacy" and not inspection.configured
    assert store.path.read_bytes() == original and not list(
        tmp_path.glob(".zniku-schema*-backup-*")
    )
    revision = store.load_authoring().storage_revision
    config = new_project_storage(store.path)
    assert store.configure_storage(config, expected_storage_revision=revision) == revision + 1
    assert store.load_storage() == config and _version(store) == 4
    assert store.load() == snapshot
    backups = list(tmp_path.glob(f".zniku-schema{version}-backup-*.zniku"))
    assert len(backups) == 1
    backup = ProjectStore.open(backups[0])
    assert _version(backup) == version and backup.load() == snapshot
    assert backup.load_storage() is None
    store.configure_storage(config, expected_storage_revision=revision + 1)
    assert list(tmp_path.glob(f".zniku-schema{version}-backup-*.zniku")) == backups


def test_storage_cas_failure_preserves_old_schema_and_creates_no_backup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _as_version(store, 3)
    original = store.path.read_bytes()
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_CONFLICT"):
        store.configure_storage(new_project_storage(store.path), expected_storage_revision=99)
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob(".zniku-schema*-backup-*"))


def test_storage_configuration_rejects_existing_attempts_without_changing_graph(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    runtime = _runtime(store, tmp_path / "old")
    runtime.run_until_blocked(runtime.create_run().run_id)
    before = store.path.read_bytes()
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_MIGRATION_REQUIRED"):
        store.configure_storage(
            new_project_storage(store.path),
            expected_storage_revision=store.load_authoring().storage_revision,
        )
    assert store.path.read_bytes() == before


def test_corrupt_storage_fails_closed_not_defaulted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE project_storage SET payload_json = ?", ('{"unknown":true}',))
    before = store.path.read_bytes()
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_INVALID"):
        ProjectStore.open(store.path)
    assert store.path.read_bytes() == before


def test_migration_moves_locators_not_ids_history_or_sources(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = tmp_path / "old"
    runtime = _runtime(store, root)
    first = runtime.run_until_blocked(runtime.create_run().run_id)
    node = first.node_runs[0]
    artifact = runtime.repository.get_artifact(node.output_artifact_ids[0])
    original_file = Path(artifact.path)
    original_content = original_file.read_bytes()
    original_logs = Path(node.log_path or "")
    unrelated = root / "unrelated-user-data"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("not this project", encoding="utf-8")
    snapshot = store.load()
    latest = runtime.repository.list_latest()
    manager = StorageMigrationManager()
    target = tmp_path / "archive.data"
    preview = _preview(manager, store, root, target)
    assert preview.file_count >= 3 and not target.exists()
    inspection = _confirm(manager, store, preview)
    assert inspection.configured and not inspection.missing
    moved = runtime.repository.get_artifact(artifact.artifact_id)
    expected_path = target / "attempts" / original_file.relative_to(root)
    assert moved.path == str(expected_path) and expected_path.read_bytes() == original_content
    assert original_file.read_bytes() == original_content and original_logs.exists()
    assert not (target / "attempts" / unrelated.name).exists()
    assert store.load() == snapshot and runtime.repository.list_latest() == latest
    after = runtime.repository.get_run(first.run_id)
    assert after.graph_snapshot == first.graph_snapshot and after.state == first.state
    assert after.node_runs[0].node_run_id == node.node_run_id
    assert after.node_runs[0].started_at == node.started_at
    reopened = _runtime(ProjectStore.open(store.path), target / "attempts")
    reused = reopened.run_until_blocked(reopened.create_run().run_id)
    assert reused.node_runs[0].reused_from_result_id is not None


def test_completed_manual_handoff_targets_relocate(tmp_path: Path) -> None:
    store = _store(tmp_path, manual=True)
    root = tmp_path / "old"
    runtime = _runtime(store, root)
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    node = run.node_runs[0]
    assert node.external_handoff is not None
    output = Path(node.external_handoff.output_targets[0].path)
    output.write_bytes(b"synthetic manual")
    runtime.submit_external(node.node_run_id)
    manager = StorageMigrationManager()
    target = tmp_path / "manual.data"
    _confirm(manager, store, _preview(manager, store, root, target))
    changed = runtime.repository.get_node_run(node.node_run_id)
    assert changed.external_handoff is not None
    assert Path(changed.external_handoff.output_targets[0].path).is_relative_to(target)
    assert output.read_bytes() == b"synthetic manual"


@pytest.mark.parametrize("manual", [False, True])
def test_active_or_waiting_run_blocks_migration(tmp_path: Path, manual: bool) -> None:
    store = _store(tmp_path, manual=manual)
    root = tmp_path / "old"
    runtime = _runtime(store, root)
    run = runtime.create_run()
    if manual:
        runtime.run_until_blocked(run.run_id)
    before = store.path.read_bytes()
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_ACTIVE"):
        _preview(StorageMigrationManager(), store, root, tmp_path / "no-create")
    assert store.path.read_bytes() == before and not (tmp_path / "no-create").exists()


@pytest.mark.parametrize("failure", ["source_changed", "copy_failed", "copy_corrupt", "db_changed"])
def test_migration_failures_never_switch_references_or_delete_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    store = _store(tmp_path)
    root = tmp_path / "old"
    runtime = _runtime(store, root)
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    artifact = runtime.repository.get_artifact(run.node_runs[0].output_artifact_ids[0])
    manager = StorageMigrationManager()
    preview = _preview(manager, store, root, tmp_path / "failure.data")
    original = store.path.read_bytes()
    copy = shutil.copy2

    def failing_copy(source: Any, destination: Any, **kwargs: Any) -> Any:
        if failure == "copy_failed":
            raise OSError("synthetic disk failure")
        result = copy(source, destination, **kwargs)
        if failure == "copy_corrupt":
            Path(destination).write_bytes(b"corrupt")
        if failure == "db_changed":
            snapshot = store.load()
            store.save(snapshot.project, snapshot.definitions)
        return result

    if failure == "source_changed":
        Path(artifact.path).write_bytes(b"changed")
    else:
        monkeypatch.setattr(shutil, "copy2", failing_copy)
    with pytest.raises(ProjectStoreError):
        _confirm(manager, store, preview)
    assert store.load_storage() is None
    assert runtime.repository.get_artifact(artifact.artifact_id).path == artifact.path
    assert Path(artifact.path).exists()
    if failure != "db_changed":
        assert store.path.read_bytes() == original


def test_inspection_lists_external_and_missing_registered_artifacts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = tmp_path / "old"
    runtime = _runtime(store, root)
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    artifact = runtime.repository.get_artifact(run.node_runs[0].output_artifact_ids[0])
    external = tmp_path / "original.dat"
    external.write_bytes(b"external")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE artifacts SET path = ?", (str(external),))
    inspection = inspect_storage(store, root)
    assert inspection.registered_file_count == 1 and inspection.managed_file_count == 0
    assert inspection.external_dependencies[0].path == str(external)
    assert inspection.coverage == "registered_artifacts" and inspection.warnings
    external.unlink()
    assert inspect_storage(store, root).missing[0].artifact_ids == (artifact.artifact_id,)
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_MISSING"):
        _preview(StorageMigrationManager(), store, root, tmp_path / "bad.data")


@pytest.mark.parametrize("target_kind", ["exists", "overlap", "contains_project"])
def test_migration_rejects_unsafe_target(tmp_path: Path, target_kind: str) -> None:
    store = _store(tmp_path)
    root = tmp_path / "old"
    root.mkdir()
    if target_kind == "exists":
        target = tmp_path / "existing.data"
        target.mkdir()
    elif target_kind == "overlap":
        target = root / "nested.data"
    else:
        target = tmp_path
    with pytest.raises(ProjectStoreError):
        _preview(StorageMigrationManager(), store, root, target)


def test_expired_or_cross_session_ticket_never_creates_data_directory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = tmp_path / "old"
    time = [0.0]
    manager = StorageMigrationManager(clock=lambda: time[0])
    target = tmp_path / "future.data"
    preview = _preview(manager, store, root, target)
    time[0] = 901.0
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_TICKET"):
        _confirm(manager, store, preview)
    assert not target.exists()


@pytest.mark.parametrize("change", ["modify", "replace", "junction"])
def test_changed_verified_target_aborts_before_database_references_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """第一份副本验证后、其他文件复制时发生替换或目录联接变化，仍须拒绝切引用。"""

    store = _store(tmp_path)
    root = tmp_path / "old"
    runtime = _runtime(store, root)
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    artifact = runtime.repository.get_artifact(run.node_runs[0].output_artifact_ids[0])
    source_before = Path(artifact.path).read_bytes()
    manager = StorageMigrationManager()
    target_root = tmp_path / "target-change.data"
    preview = _preview(manager, store, root, target_root)
    database_before = store.path.read_bytes()
    copied_paths: list[Path] = []
    original_copy = shutil.copy2
    original_lstat = Path.lstat

    def changed_copy(source: Any, destination: Any, **kwargs: Any) -> Any:
        result = original_copy(source, destination, **kwargs)
        copied_paths.append(Path(destination))
        if len(copied_paths) == 2:
            previous = copied_paths[0]
            if change == "modify":
                previous.write_bytes(previous.read_bytes() + b"changed after validation")
            elif change == "replace":
                original = previous.read_bytes()
                previous.unlink()
                previous.write_bytes(original)
        return result

    def changed_lstat(path: Path) -> os.stat_result:
        if change == "junction" and len(copied_paths) >= 2 and path == target_root:
            return cast(
                os.stat_result, SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            )
        return original_lstat(path)

    monkeypatch.setattr(shutil, "copy2", changed_copy)
    monkeypatch.setattr(Path, "lstat", changed_lstat)
    with pytest.raises(ProjectStoreError, match=r"E_PROJECT_STORAGE_(CHANGED|PATH)"):
        _confirm(manager, store, preview)
    assert store.path.read_bytes() == database_before and store.load_storage() is None
    assert Path(artifact.path).read_bytes() == source_before
    assert runtime.repository.get_artifact(artifact.artifact_id).path == artifact.path
    assert target_root.exists()
