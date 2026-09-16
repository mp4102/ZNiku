"""用合成文本产物验证可读整理、归档重新定位和非权威 HTML 目录，不访问真实媒体。"""

from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from zniku.graph import (
    ExecutionMode,
    Graph,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.project import Project, ProjectStore, ProjectStoreError
from zniku.project.storage import ProjectStorage, new_project_storage
from zniku.project_service.storage import StorageMigrationManager, inspect_storage
from zniku.project_service.storage_index import export_storage_index
from zniku.project_service.storage_owner import OWNER_NAME
from zniku.project_service.storage_paths import prepare_storage_location
from zniku.runtime import PythonAdapterContext, PythonAdapterResult, RuntimeService


def _adapter(context: PythonAdapterContext) -> PythonAdapterResult:
    context.outputs[0].path.write_bytes(b"synthetic archival data" * 8)
    context.stdout_log_path.write_text("synthetic log", encoding="utf-8")
    return PythonAdapterResult()


def _project(
    tmp_path: Path, *, readable: bool = False
) -> tuple[ProjectStore, RuntimeService, Path]:
    definition = NodeDefinition(
        type_id="test.archive",
        version="1.0.0",
        execution_mode=ExecutionMode.AUTOMATIC,
        output_ports=(PortSpec(port_id="out", data_type="DataFile"),),
        executor=PythonExecutorSpec(adapter="tests.archive:source"),
    )
    storage = new_project_storage(tmp_path / "archive.zniku") if readable else None
    if storage is not None:
        # 此文件继续回归旧可读布局的显式整理与原样换盘兼容。
        storage = ProjectStorage.model_validate(
            storage.model_dump(mode="python")
            | {
                "contract_version": "0.3.2",
                "layout": "readable",
                "attempts_root": str(Path(storage.data_root) / "attempts"),
            }
        )
    root = Path(storage.attempts_root) if storage else tmp_path / "old"
    store = ProjectStore.create(
        tmp_path / "archive.zniku",
        Project(
            project_id="synthetic.archive",
            name="<script>alert('test')</script>",
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
    if storage is not None:
        prepare_storage_location(storage, current=None)
    runtime = RuntimeService(store, root, python_adapters={"tests.archive:source": _adapter})
    return store, runtime, root


def _confirm(store: ProjectStore, manager: StorageMigrationManager, ticket_id: str) -> None:
    manager.confirm(
        store,
        ticket_id=ticket_id,
        project_session_id="test-session",
        expected_storage_revision=store.load_authoring().storage_revision,
    )


def test_organize_legacy_copies_only_bound_attempts_and_preserves_semantics(tmp_path: Path) -> None:
    store, runtime, root = _project(tmp_path)
    original = runtime.run_until_blocked(runtime.create_run().run_id)
    source = Path(original.node_runs[0].work_dir)
    (root / "unowned").mkdir()
    (root / "unowned" / "private.txt").write_bytes(b"unowned")
    target = new_project_storage(store.path, data_root=tmp_path / "readable.data")
    manager = StorageMigrationManager()
    before_graph, before_latest = store.load(), runtime.repository.list_latest()
    preview = manager.preview(
        store,
        legacy_root=root,
        target=target,
        project_session_id="test-session",
        expected_storage_revision=store.load_authoring().storage_revision,
        organize=True,
    )
    assert preview.operation == "organize" and preview.target.layout == "english"
    assert (
        Path(preview.path_mappings[0].target).relative_to(Path(target.data_root)).as_posix()
        == "task/round-001"
    )
    assert not Path(target.data_root).exists()
    _confirm(store, manager, preview.ticket_id)
    backups = tuple(store.path.parent.glob(f"{store.path.stem}.before-storage-*.zniku"))
    assert len(backups) == 1
    previous = ProjectStore.open(backups[0])
    assert previous.load() == before_graph and previous.load_storage() is None
    assert source.exists() and not (Path(target.attempts_root) / "unowned").exists()
    assert store.load() == before_graph and runtime.repository.list_latest() == before_latest
    after = runtime.repository.get_run(original.run_id)
    assert after.graph_snapshot == original.graph_snapshot
    assert after.node_runs[0].node_run_id == original.node_runs[0].node_run_id
    assert after.node_runs[0].started_at == original.node_runs[0].started_at
    assert Path(after.node_runs[0].work_dir) == Path(preview.path_mappings[0].target)
    inspection = inspect_storage(store, root)
    assert inspection.storage.layout == "english" and not inspection.missing
    reopened = RuntimeService(
        store, Path(target.attempts_root), python_adapters={"tests.archive:source": _adapter}
    )
    reused = reopened.run_until_blocked(reopened.create_run().run_id)
    assert reused.node_runs[0].reused_from_result_id is not None


@pytest.mark.parametrize("missing_source", [False, True])
def test_restore_explicit_copied_root_retains_current_results(
    tmp_path: Path, missing_source: bool
) -> None:
    store, runtime, root = _project(tmp_path, readable=True)
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    target_root = tmp_path / "copied.data"
    shutil.copytree(root.parent, target_root)
    if missing_source:
        root.parent.rename(tmp_path / "retained-original.data")
    before_graph, before_latest = store.load(), runtime.repository.list_latest()
    manager = StorageMigrationManager()
    preview = manager.preview_restore(
        store,
        legacy_root=root,
        target=new_project_storage(store.path, data_root=target_root),
        project_session_id="test-session",
        expected_storage_revision=store.load_authoring().storage_revision,
    )
    assert preview.operation == "restore"
    if missing_source:
        assert "不是内容证明" in preview.warnings[1]
    _confirm(store, manager, preview.ticket_id)
    assert store.load() == before_graph and runtime.repository.list_latest() == before_latest
    artifact = runtime.repository.get_artifact(run.node_runs[0].output_artifact_ids[0])
    assert Path(artifact.path).is_relative_to(target_root)
    assert Path(artifact.path).read_bytes() == b"synthetic archival data" * 8
    assert not inspect_storage(store, root).missing


@pytest.mark.parametrize("failure", ["contents", "missing", "change_after_preview", "cas"])
def test_restore_mismatch_never_changes_database_or_existing_copy(
    tmp_path: Path, failure: str
) -> None:
    store, runtime, root = _project(tmp_path, readable=True)
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    target_root = tmp_path / "copied.data"
    shutil.copytree(root.parent, target_root)
    artifact = runtime.repository.get_artifact(run.node_runs[0].output_artifact_ids[0])
    target_file = target_root / Path(artifact.path).relative_to(root.parent)
    before = store.path.read_bytes()
    if failure == "contents":
        target_file.write_bytes(b"X" * target_file.stat().st_size)
    elif failure == "missing":
        target_file.unlink()
    manager = StorageMigrationManager()
    with pytest.raises(ProjectStoreError):
        preview = manager.preview_restore(
            store,
            legacy_root=root,
            target=new_project_storage(store.path, data_root=target_root),
            project_session_id="test-session",
            expected_storage_revision=store.load_authoring().storage_revision,
        )
        if failure == "change_after_preview":
            target_file.write_bytes(b"modified")
        elif failure == "cas":
            snapshot = store.load()
            store.save(snapshot.project, snapshot.definitions)
        _confirm(store, manager, preview.ticket_id)
    if failure != "cas":
        assert store.path.read_bytes() == before
    assert runtime.repository.get_artifact(artifact.artifact_id).path == artifact.path
    assert Path(artifact.path).read_bytes() == b"synthetic archival data" * 8


def test_missing_source_restore_rejects_stat_mismatch(tmp_path: Path) -> None:
    store, runtime, root = _project(tmp_path, readable=True)
    run = runtime.run_until_blocked(runtime.create_run().run_id)
    target_root = tmp_path / "copied.data"
    shutil.copytree(root.parent, target_root)
    artifact = runtime.repository.get_artifact(run.node_runs[0].output_artifact_ids[0])
    target_file = target_root / Path(artifact.path).relative_to(root.parent)
    target_file.write_bytes(b"different")
    root.parent.rename(tmp_path / "retained-original.data")
    before = store.path.read_bytes()
    with pytest.raises(ProjectStoreError, match="RESTORE_MISMATCH"):
        StorageMigrationManager().preview_restore(
            store,
            legacy_root=root,
            target=new_project_storage(store.path, data_root=target_root),
            project_session_id="test-session",
            expected_storage_revision=store.load_authoring().storage_revision,
        )
    assert store.path.read_bytes() == before


def test_html_index_escapes_user_values_is_rebuildable_and_never_updates_database(
    tmp_path: Path,
) -> None:
    store, runtime, root = _project(tmp_path, readable=True)
    runtime.run_until_blocked(runtime.create_run().run_id)
    before = store.path.read_bytes()
    revision = store.load_authoring().storage_revision
    result = export_storage_index(
        store,
        legacy_root=root,
        project_session_id="test-session",
        expected_storage_revision=revision,
    )
    html = Path(result.path).read_text(encoding="utf-8")
    assert result.artifact_count == 1 and "&lt;script&gt;" in html and "<script>" not in html
    assert 'href="attempts/' in html and "当前有效结果" in html
    assert "不是运行或完整归档的权威" in html
    second = export_storage_index(
        store,
        legacy_root=root,
        project_session_id="test-session",
        expected_storage_revision=revision,
    )
    assert second == result and store.path.read_bytes() == before
    assert not list(root.parent.glob(".zniku-index-*.tmp"))


def test_html_index_external_dependency_is_text_not_arbitrary_link(tmp_path: Path) -> None:
    store, runtime, root = _project(tmp_path, readable=True)
    runtime.run_until_blocked(runtime.create_run().run_id)
    external = tmp_path / "outside.bin"
    external.write_bytes(b"external original")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("UPDATE artifacts SET path = ?", (str(external),))
    result = export_storage_index(
        store,
        legacy_root=root,
        project_session_id="test-session",
        expected_storage_revision=store.load_authoring().storage_revision,
    )
    html = Path(result.path).read_text(encoding="utf-8")
    assert result.external_dependency_count == 1 and str(external) in html
    assert "href=" not in html and "原素材及最终成片不会自动复制" in html


@pytest.mark.parametrize("failure", ["active", "cas", "occupied"])
def test_index_fail_closed_for_active_cas_and_user_file(tmp_path: Path, failure: str) -> None:
    store, runtime, root = _project(tmp_path, readable=True)
    run = runtime.create_run()
    if failure != "active":
        runtime.run_until_blocked(run.run_id)
    target = root.parent / "文件目录.html"
    if failure == "occupied":
        target.write_text("user-owned index", encoding="utf-8")
    before = store.path.read_bytes()
    with pytest.raises(ProjectStoreError):
        export_storage_index(
            store,
            legacy_root=root,
            project_session_id="test-session",
            expected_storage_revision=999
            if failure == "cas"
            else store.load_authoring().storage_revision,
        )
    assert store.path.read_bytes() == before
    if failure == "occupied":
        assert target.read_text(encoding="utf-8") == "user-owned index"
    else:
        assert not target.exists()


@pytest.mark.parametrize("change", ["missing", "different", "after_preview"])
def test_missing_root_restore_requires_matching_persisted_data_owner(
    tmp_path: Path, change: str
) -> None:
    store, runtime, root = _project(tmp_path, readable=True)
    runtime.run_until_blocked(runtime.create_run().run_id)
    copied = tmp_path / "copied.data"
    shutil.copytree(root.parent, copied)
    root.parent.rename(tmp_path / "retained-original.data")
    marker = copied / OWNER_NAME
    before = store.path.read_bytes()
    manager = StorageMigrationManager()
    if change == "missing":
        marker.unlink()
    elif change == "different":
        marker.write_text(
            '{"contract_version":"0.3.2","data_id":"00000000-0000-4000-8000-000000000000"}',
            encoding="utf-8",
        )
    with pytest.raises(ProjectStoreError, match=r"RESTORE_(UNPROVEN|MISMATCH)"):
        preview = manager.preview_restore(
            store,
            legacy_root=root,
            target=new_project_storage(store.path, data_root=copied),
            project_session_id="test-session",
            expected_storage_revision=store.load_authoring().storage_revision,
        )
        if change == "after_preview":
            marker.write_text(
                '{"contract_version":"0.3.2","data_id":"different"}', encoding="utf-8"
            )
        _confirm(store, manager, preview.ticket_id)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("existing", [False, True])
def test_index_does_not_replace_user_file_changed_during_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    store, runtime, root = _project(tmp_path, readable=True)
    runtime.run_until_blocked(runtime.create_run().run_id)
    revision = store.load_authoring().storage_revision
    if existing:
        export_storage_index(
            store,
            legacy_root=root,
            project_session_id="test-session",
            expected_storage_revision=revision,
        )
    destination = root.parent / "文件目录.html"
    before = store.path.read_bytes()
    original = os.fsync

    def replace_index(descriptor: int) -> None:
        original(descriptor)
        destination.write_text("user replaced file", encoding="utf-8")

    monkeypatch.setattr(os, "fsync", replace_index)
    with pytest.raises(ProjectStoreError, match="E_PROJECT_STORAGE_CHANGED"):
        export_storage_index(
            store,
            legacy_root=root,
            project_session_id="test-session",
            expected_storage_revision=revision,
        )
    assert destination.read_text(encoding="utf-8") == "user replaced file"
    assert store.path.read_bytes() == before and not list(root.parent.glob(".zniku-index-*.tmp"))
