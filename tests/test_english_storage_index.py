"""用合成文本工程核对英文索引与轮次，不访问真实媒体或把索引变成运行依据。"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_project_storage import _runtime, _store
from zniku.project import ProjectStore, ProjectStoreError
from zniku.project.storage import new_project_storage
from zniku.project_service.storage_index import export_storage_index
from zniku.project_service.storage_paths import prepare_storage_location
from zniku.runtime import RuntimeService


def _project(tmp_path: Path) -> tuple[ProjectStore, RuntimeService, Path]:
    storage = new_project_storage(tmp_path / "synthetic.zniku")
    prepare_storage_location(storage, current=None)
    store = _store(tmp_path, storage=storage)
    root = Path(storage.data_root)
    return store, _runtime(store, root), root


def _export(store: ProjectStore, root: Path) -> Path:
    result = export_storage_index(
        store,
        legacy_root=root,
        project_session_id="synthetic-index-session",
        expected_storage_revision=store.load_authoring().storage_revision,
    )
    return Path(result.path)


def test_english_index_uses_task_round_and_no_extra_media_for_reuse(tmp_path: Path) -> None:
    store, runtime, root = _project(tmp_path)
    first = runtime.run_until_blocked(runtime.create_run().run_id)
    reused = runtime.run_until_blocked(runtime.create_run().run_id)
    assert reused.node_runs[0].reused_from_result_id is not None
    storage = store.load_storage()
    assert storage is not None
    assert len(storage.english_layout_state.attempts) == 1
    before_database = store.path.read_bytes()
    before_media = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    index = _export(store, root)

    assert index == root / "index.html"
    assert not (root / "文件目录.html").exists()
    html = index.read_text(encoding="utf-8")
    assert "task" in html and "round-001" in html
    assert 'href="task/round-001/outputs/' in html
    assert "R001-A001" not in html and "R002-A001" not in html and "round-002" not in html
    for node_run in (*first.node_runs, *reused.node_runs):
        assert (
            node_run.node_run_id not in html and node_run.node_run_id.replace("-", "") not in html
        )
    assert store.path.read_bytes() == before_database
    assert all(path.read_bytes() == value for path, value in before_media.items())
    assert {path for path in root.rglob("*") if path.is_file()} == {*before_media, index}
    assert _export(store, root) == index
    assert not list(root.glob(".zniku-index-*.tmp"))


def test_cancelled_unstarted_english_task_does_not_advertise_uuid_directory(tmp_path: Path) -> None:
    store, runtime, root = _project(tmp_path)
    run = runtime.create_run()
    abandoned = runtime.abandon_run(run.run_id)
    assert all(not Path(item.work_dir).exists() for item in abandoned.node_runs)
    before = store.path.read_bytes()

    html = _export(store, root).read_text(encoding="utf-8")

    assert "未开始处理" in html and "未创建处理目录" in html
    assert "round-001" not in html
    for node_run in abandoned.node_runs:
        assert node_run.node_run_id.replace("-", "") not in html
    assert store.path.read_bytes() == before
    assert not any(path.is_dir() for path in root.iterdir())


def test_english_index_never_overwrites_user_index_file(tmp_path: Path) -> None:
    store, runtime, root = _project(tmp_path)
    runtime.run_until_blocked(runtime.create_run().run_id)
    index = root / "index.html"
    index.write_text("user-owned index", encoding="utf-8")
    before = store.path.read_bytes()

    with pytest.raises(ProjectStoreError, match="INDEX_TARGET"):
        _export(store, root)

    assert index.read_text(encoding="utf-8") == "user-owned index"
    assert store.path.read_bytes() == before


def test_english_index_leaves_legacy_named_user_file_untouched(tmp_path: Path) -> None:
    store, runtime, root = _project(tmp_path)
    runtime.run_until_blocked(runtime.create_run().run_id)
    user_file = root / "文件目录.html"
    user_file.write_text("a separately retained document", encoding="utf-8")

    assert _export(store, root) == root / "index.html"
    assert user_file.read_text(encoding="utf-8") == "a separately retained document"
