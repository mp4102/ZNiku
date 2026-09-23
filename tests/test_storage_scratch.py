"""用纯合成工程和字节占位文件验证中转维护，不扫描或删除用户媒体。

Runtime adapter/probe/validator 在夹具中明确为测试替身；测试只证明目录归属、正式引用、
互斥与显式确认边界，不把占位文件当作真实 ProRes 或性能验收。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import stat
from contextlib import closing
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_project_service_host_bridge import RecordingPlatform, _request, _serve, _session
from zniku.chapter_overlap.definitions import fi_context_definition, fi_crop_definition
from zniku.chapter_overlap.media_io import copy_prores_range
from zniku.graph import (
    Edge,
    ExecutionMode,
    Graph,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    PythonExecutorSpec,
)
from zniku.media.scratch import INDEX_NAME, exact_role, read_index, record_internal_scratch
from zniku.project import Project, ProjectStore, ProjectStoreError
from zniku.project.storage import new_project_storage
from zniku.project_service.service import ProjectServiceApplication, ProjectServiceError
from zniku.project_service.storage_api import ScratchConfirmRequest, ScratchPreviewRequest
from zniku.project_service.storage_owner import create_storage_owner
from zniku.project_service.storage_scratch import ScratchManager, ScratchPreview
from zniku.runtime import (
    NodeValidatorResult,
    PythonAdapterContext,
    PythonAdapterResult,
    RuntimeService,
)


@pytest.fixture
def synthetic(
    tmp_path: Path,
) -> tuple[ProjectStore, Path, list[PythonAdapterContext], RuntimeService]:
    source = NodeDefinition(
        type_id="test.scratch.source",
        version="1.0.0",
        output_ports=(PortSpec(port_id="video", data_type="VideoFile"),),
        execution_mode=ExecutionMode.AUTOMATIC,
        executor=PythonExecutorSpec(adapter="tests.scratch:source"),
    )
    definitions = (source, fi_context_definition(), fi_crop_definition())
    parameters: dict[str, Any] = {
        "source": {
            "effective_video_artifact_id": str(uuid4()),
            "admission_artifact_id": str(uuid4()),
            "source_media_artifact_id": str(uuid4()),
            "frame_count": 60,
            "frame_rate": "30/1",
        },
        "chapter": {
            "chapter_id": "chapter-0001",
            "ordinal": 0,
            "count": 1,
            "start_frame": 0,
            "end_frame": 60,
        },
    }
    graph = Graph(
        nodes=tuple(
            NodeInstance(
                node_id=f"n{index}",
                type_id=definition.type_id,
                definition_version=definition.version,
                parameters={} if index == 0 else parameters,
            )
            for index, definition in enumerate(definitions)
        ),
        edges=(
            Edge(
                source_node_id="n0",
                source_port_id="video",
                target_node_id="n1",
                target_port_id="chapters",
                ordinal=0,
            ),
            Edge(
                source_node_id="n1",
                source_port_id="video",
                target_node_id="n2",
                target_port_id="video",
            ),
        ),
    )
    storage = new_project_storage(tmp_path / "synthetic.zniku")
    Path(storage.data_root).mkdir()
    create_storage_owner(storage)
    store = ProjectStore.create(
        tmp_path / "synthetic.zniku",
        Project(project_id="test.scratch", name="合成清理测试", graph=graph),
        definitions,
        storage=storage,
    )
    contexts: list[PythonAdapterContext] = []

    def adapter(context: PythonAdapterContext) -> PythonAdapterResult:
        for output in context.outputs:
            output.path.parent.mkdir(parents=True, exist_ok=True)
            output.path.write_bytes(b"{}" if output.path.suffix == ".json" else b"synthetic-only")
        role = exact_role(context.definition)
        if role == "context":
            part = context.work_dir / "context-parts" / "part-0000.mov"
            part.parent.mkdir()
            part.write_bytes(b"synthetic-part" * 7)
            assert record_internal_scratch(context, part, "context_part")
            contexts.append(context)
        if role == "crop":
            stage = context.outputs[0].path.with_name(
                context.outputs[0].path.stem + ".timescale.mov"
            )
            stage.write_bytes(b"synthetic-timescale" * 5)
            assert record_internal_scratch(context, stage, "timescale")
            contexts.append(context)
        return PythonAdapterResult()

    service = RuntimeService(
        store,
        storage.attempts_root,
        python_adapters={
            definition.executor.adapter: adapter
            for definition in definitions
            if isinstance(definition.executor, PythonExecutorSpec)
        },
        validators={
            definition.validator.adapter: lambda _context: NodeValidatorResult(passed=True)
            for definition in definitions
            if definition.validator is not None
        },
        media_probe=lambda _p, _k: {"synthetic_only": True},
        artifact_quick_probe=lambda artifact: Path(artifact.path).is_file(),
    )
    service.run_until_blocked(service.create_run().run_id)
    return store, Path(storage.attempts_root), contexts, service


def preview(
    manager: ScratchManager,
    synthetic: tuple[ProjectStore, Path, list[PythonAdapterContext], RuntimeService],
) -> ScratchPreview:
    store, root, _, _ = synthetic
    return manager.preview(
        store,
        legacy_root=root,
        project_session_id="test-session",
        expected_storage_revision=store.load_authoring().storage_revision,
    )


def selected(value: ScratchPreview) -> tuple[str, ...]:
    return tuple(item.candidate_id for item in value.entries if item.candidate_id is not None)


def confirm(
    manager: ScratchManager, store: ProjectStore, value: ScratchPreview, **changes: Any
) -> Any:
    arguments: dict[str, Any] = {
        "project_session_id": value.project_session_id,
        "expected_storage_revision": value.expected_storage_revision,
        "ticket_id": value.ticket_id,
        "candidate_ids": selected(value),
    }
    arguments.update(changes)
    return manager.confirm(store, **arguments)


def test_preview_classifies_four_categories_without_mutation_and_confirm_keeps_results(
    synthetic: Any,
) -> None:
    store, root, contexts, service = synthetic
    unknown = contexts[0].work_dir / "user-file.mov"
    unknown.write_bytes(b"keep-user-file")
    before = store.path.read_bytes()
    manager = ScratchManager()
    view = preview(manager, synthetic)
    assert {item.category for item in view.summary} == {
        "archive",
        "registered_recreatable",
        "internal_scratch",
        "unknown",
    }
    assert len(selected(view)) == 2
    assert ScratchPreview.model_validate_json(view.model_dump_json()) == view
    assert store.path.read_bytes() == before
    registered = {
        artifact.path
        for run in service.repository.list_runs()
        for node in run.node_runs
        for artifact in (service.repository.get_artifact(item) for item in node.output_artifact_ids)
    }
    result = confirm(manager, store, view)
    assert result.complete and result.deletion_count == 2
    assert result.deleted_bytes == sum(
        item.byte_count for item in view.entries if item.candidate_id
    )
    assert all(Path(path).is_file() for path in registered)
    assert unknown.read_bytes() == b"keep-user-file" and store.path.read_bytes() == before
    assert not any(Path(item.path).exists() for item in result.entries)
    assert all((context.work_dir / INDEX_NAME).exists() for context in contexts)
    assert selected(preview(manager, synthetic)) == ()
    with pytest.raises(ProjectStoreError, match="SCRATCH_TICKET"):
        confirm(manager, store, view)
    reused = service.run_until_blocked(service.create_run().run_id)
    assert all(node.reused_from_result_id is not None for node in reused.node_runs)
    assert root.is_dir()


@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_identity", "wrong_exact"])
def test_old_or_unreliable_index_is_unknown_not_guessed(synthetic: Any, damage: str) -> None:
    _store, _, contexts, _ = synthetic
    context = contexts[0]
    marker = context.work_dir / INDEX_NAME
    raw = marker.read_bytes()
    if damage == "missing":
        marker.unlink()
    elif damage == "corrupt":
        marker.write_text('{"unknown":true}', encoding="utf-8")
    else:
        data = json.loads(raw)
        if damage == "wrong_identity":
            data["entries"][0]["identity"]["inode"] += 1
        else:
            data["type_id"] = "unknown.custom"
        marker.write_text(json.dumps(data), encoding="utf-8")
    result = preview(ScratchManager(), synthetic)
    entry = next(
        item
        for item in result.entries
        if Path(item.path).name == "part-0000.mov" and item.node_run_id == context.node_run_id
    )
    assert entry.category == "unknown" and entry.candidate_id is None


@pytest.mark.parametrize("change", ["file", "index", "state", "session", "revision", "expired"])
def test_changes_reject_entire_confirmation_before_deletion(synthetic: Any, change: str) -> None:
    store, _, contexts, _ = synthetic
    clock = [1.0]
    manager = ScratchManager(clock=lambda: clock[0])
    value = preview(manager, synthetic)
    candidates = [item for item in value.entries if item.candidate_id]
    changes: dict[str, object] = {}
    if change == "file":
        Path(candidates[0].path).write_bytes(b"changed")
    elif change == "index":
        (contexts[0].work_dir / INDEX_NAME).write_text("{}", encoding="utf-8")
    elif change == "state":
        snapshot = store.load()
        store.save(snapshot.project, snapshot.definitions)
    elif change == "session":
        changes["project_session_id"] = "new-session"
    elif change == "revision":
        changes["expected_storage_revision"] = value.expected_storage_revision + 1
    else:
        clock[0] += 301
    with pytest.raises(ProjectStoreError, match=r"SCRATCH_(CHANGED|TICKET)"):
        confirm(manager, store, value, **changes)
    assert all(Path(item.path).exists() for item in candidates)


@pytest.mark.parametrize("kind", ["empty", "unknown", "duplicate"])
def test_selection_only_accepts_unique_current_ids(synthetic: Any, kind: str) -> None:
    store, _, _, _ = synthetic
    manager = ScratchManager()
    value = preview(manager, synthetic)
    ids = selected(value)
    request = () if kind == "empty" else (str(uuid4()),) if kind == "unknown" else (ids[0], ids[0])
    with pytest.raises(ProjectStoreError, match="SCRATCH_SELECTION"):
        confirm(manager, store, value, candidate_ids=request)
    assert all(Path(item.path).exists() for item in value.entries if item.candidate_id)


def test_hardlink_is_never_candidate(synthetic: Any, tmp_path: Path) -> None:
    _, _, contexts, _ = synthetic
    index = read_index(contexts[0].work_dir)
    assert index is not None
    path = contexts[0].work_dir / index.entries[0].relative_path
    os.link(path, tmp_path / "external-alias.mov")
    entry = next(
        item for item in preview(ScratchManager(), synthetic).entries if item.path == str(path)
    )
    assert entry.candidate_id is None and entry.category == "unknown"


def test_hardlink_created_after_preview_prevents_all_deletions(
    synthetic: Any, tmp_path: Path
) -> None:
    store, _, _, _ = synthetic
    manager = ScratchManager()
    view = preview(manager, synthetic)
    entries = [item for item in view.entries if item.candidate_id]
    os.link(entries[0].path, tmp_path / "late-shared.mov")
    with pytest.raises(ProjectStoreError, match="SCRATCH_CHANGED"):
        confirm(manager, store, view)
    assert all(Path(item.path).is_file() for item in entries)


def test_waiting_external_blocks_scratch_preview(tmp_path: Path) -> None:
    from test_project_storage import _runtime, _store

    store = _store(tmp_path, manual=True)
    service = _runtime(store, tmp_path / "legacy")
    service.run_until_blocked(service.create_run().run_id)
    with pytest.raises(ProjectStoreError, match="STORAGE_ACTIVE"):
        ScratchManager().preview(
            store,
            legacy_root=tmp_path / "legacy",
            project_session_id="test",
            expected_storage_revision=store.load_authoring().storage_revision,
        )


def test_reparse_flag_is_never_followed_or_proposed(
    synthetic: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, contexts, _ = synthetic
    path = contexts[0].work_dir / "context-parts" / "part-0000.mov"
    original = Path.lstat

    def mark_reparse(value: Path, *args: Any, **kwargs: Any) -> Any:
        current = original(value, *args, **kwargs)
        if value == path:
            return SimpleNamespace(
                st_mode=stat.S_IFREG, st_size=current.st_size, st_file_attributes=0x400
            )
        return current

    monkeypatch.setattr(Path, "lstat", mark_reparse)
    entry = next(
        item for item in preview(ScratchManager(), synthetic).entries if item.path == str(path)
    )
    assert entry.category == "unknown" and entry.candidate_id is None


def test_symlink_is_unknown_and_external_target_survives(synthetic: Any, tmp_path: Path) -> None:
    _, _, contexts, _ = synthetic
    target = tmp_path / "external.mov"
    target.write_bytes(b"external-user")
    link = contexts[0].work_dir / "context-parts" / "part-0002.mov"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("当前 Windows 用户不具备符号链接权限")
    assert not record_internal_scratch(contexts[0], link, "context_part")
    entry = next(
        item for item in preview(ScratchManager(), synthetic).entries if item.path == str(link)
    )
    assert entry.candidate_id is None and target.read_bytes() == b"external-user"


def test_registered_historical_artifact_path_is_never_a_candidate(synthetic: Any) -> None:
    store, _, contexts, service = synthetic
    path = contexts[0].work_dir / "context-parts" / "part-0000.mov"
    run = service.repository.list_runs()[0]
    artifact_id = run.node_runs[0].output_artifact_ids[0]
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("UPDATE artifacts SET path = ? WHERE artifact_id = ?", (str(path), artifact_id))
    entry = next(
        item for item in preview(ScratchManager(), synthetic).entries if item.path == str(path)
    )
    assert entry.candidate_id is None and entry.category == "archive"


def test_declared_output_is_retained_even_if_hand_edited_index_claims_it(synthetic: Any) -> None:
    _, _, contexts, _ = synthetic
    context = contexts[0]
    index = context.work_dir / INDEX_NAME
    payload = json.loads(index.read_bytes())
    path = context.work_dir / payload["entries"][0]["relative_path"]
    payload["declared_outputs"].append(str(path))
    index.write_text(json.dumps(payload), encoding="utf-8")
    entry = next(
        item for item in preview(ScratchManager(), synthetic).entries if item.path == str(path)
    )
    assert entry.candidate_id is None and entry.category == "archive"


def test_unknown_input_directory_reference_protects_entire_subtree(synthetic: Any) -> None:
    store, _, contexts, service = synthetic
    context = contexts[0]
    result = service.repository.list_latest()[0]
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(
            "UPDATE node_results SET media_summary_json = ? WHERE result_id = ?",
            (
                json.dumps({"input_directory": str(context.work_dir / "context-parts")}),
                result.result_id,
            ),
        )
    entry = next(
        item
        for item in preview(ScratchManager(), synthetic).entries
        if item.path == str(context.work_dir / "context-parts" / "part-0000.mov")
    )
    assert entry.candidate_id is None


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="需要 FFmpeg/FFprobe"
)
def test_real_short_copy_records_only_verified_internal_output(tmp_path: Path) -> None:

    from test_overlap_media_io import _context

    experiment = importlib.import_module("tools.check_overlap_remux").RemuxExperiment(
        tmp_path / "synthetic-short"
    )
    rate = Fraction(30)
    source = experiment.generate("source.mov", rate, 8, offset=0, step=2)
    definition = fi_context_definition()
    context = replace(_context(experiment.output), definition=definition)
    part = context.work_dir / "context-parts" / "part-0000.mov"
    assert copy_prores_range(context, source, part, 1, 4, rate) == 3
    index = read_index(context.work_dir)
    assert index is not None and len(index.entries) == 1
    assert index.entries[0].relative_path == "context-parts/part-0000.mov"
    assert source.is_file() and part.is_file()


def test_new_reference_protects_even_unregistered_internal_file(synthetic: Any) -> None:
    store, _, contexts, service = synthetic
    path = contexts[0].work_dir / "context-parts" / "part-0000.mov"
    # 历史 NodeResult 的普通摘要引用也受保护，不只检查当前 Artifact.path。
    result = service.repository.list_latest()[0]
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(
            "UPDATE node_results SET media_summary_json = ? WHERE result_id = ?",
            (json.dumps({"keep_dependency": str(path)}), result.result_id),
        )
    entry = next(
        item for item in preview(ScratchManager(), synthetic).entries if item.path == str(path)
    )
    assert entry.candidate_id is None and entry.category == "archive"


@pytest.mark.parametrize("prefix", ["file ", "  file ", "\tfile\t", " \tfile   "])
def test_referenced_concat_dependency_is_preserved(synthetic: Any, prefix: str) -> None:
    store, _, contexts, service = synthetic
    path = contexts[0].work_dir / "context-parts" / "part-0000.mov"
    manifest = contexts[0].work_dir / "protected.ffconcat"
    manifest.write_text(
        "ffconcat version 1.0\n# comment\n" + prefix + "'" + path.as_posix() + "'\n",
        encoding="utf-8",
    )
    result = service.repository.list_latest()[0]
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(
            "UPDATE node_results SET media_summary_json = ? WHERE result_id = ?",
            (json.dumps({"manifest": str(manifest)}), result.result_id),
        )
    entry = next(
        item for item in preview(ScratchManager(), synthetic).entries if item.path == str(path)
    )
    assert entry.candidate_id is None


@pytest.mark.parametrize(
    "line",
    [
        "file 'unfinished",
        "file",
        "stream metadata/path",
        "ffconcat version 9.0",
        "file 'one' 'two'",
    ],
)
def test_ambiguous_concat_syntax_disables_cleanup(synthetic: Any, line: str) -> None:
    store, _, contexts, service = synthetic
    manifest = contexts[0].work_dir / "unsupported.ffconcat"
    manifest.write_text("ffconcat version 1.0\n" + line + "\n", encoding="utf-8")
    result = service.repository.list_latest()[0]
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(
            "UPDATE node_results SET media_summary_json = ? WHERE result_id = ?",
            (json.dumps({"manifest": str(manifest)}), result.result_id),
        )
    with pytest.raises(ProjectStoreError, match="SCRATCH_REFERENCES"):
        preview(ScratchManager(), synthetic)


def test_many_empty_directories_count_against_scan_budget(
    synthetic: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, contexts, _ = synthetic
    for index in range(20):
        (contexts[0].work_dir / f"empty-{index:03d}").mkdir()
    monkeypatch.setattr("zniku.project_service.storage_scratch.MAX_SCAN_ENTRIES", 5)
    view = preview(ScratchManager(), synthetic)
    assert view.truncated and not selected(view)


def test_registered_dependency_append_is_also_bounded(
    synthetic: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "zniku.project_service.storage_scratch._enumerate", lambda _attempts: ([], False, [])
    )
    monkeypatch.setattr("zniku.project_service.storage_scratch.MAX_FILES", 1)
    view = preview(ScratchManager(), synthetic)
    assert len(view.entries) == 1 and view.truncated and not selected(view)


def test_partial_deletion_failure_reports_only_successful_bytes(
    synthetic: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, _, _ = synthetic
    manager = ScratchManager()
    value = preview(manager, synthetic)
    entries = [item for item in value.entries if item.candidate_id]
    failing = Path(entries[1].path)
    unlink = Path.unlink

    def fail_one(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == failing:
            raise PermissionError("synthetic locked")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)
    result = confirm(manager, store, value)
    assert not result.complete and result.deletion_count == 1
    assert [item.status for item in result.entries].count("failed") == 1
    assert result.deleted_bytes == sum(item.byte_count for item in entries) - entries[1].byte_count
    assert failing.exists()


def test_pending_run_prevents_preview_and_confirmation(synthetic: Any) -> None:
    store, _, _, service = synthetic
    manager = ScratchManager()
    value = preview(manager, synthetic)
    service.create_run()
    with pytest.raises(ProjectStoreError, match="STORAGE_ACTIVE"):
        preview(manager, synthetic)
    with pytest.raises(ProjectStoreError, match="STORAGE_ACTIVE"):
        confirm(manager, store, value)


def test_budget_exhaustion_disables_candidates(
    synthetic: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("zniku.project_service.storage_scratch.MAX_FILES", 2)
    result = preview(ScratchManager(), synthetic)
    assert result.truncated and not selected(result)


def test_marker_cannot_claim_declared_output_or_unrelated_path(synthetic: Any) -> None:
    _, _, contexts, _ = synthetic
    context = contexts[0]
    assert not record_internal_scratch(context, context.outputs[0].path, "timescale")
    unowned = context.work_dir / "private.timescale.mov"
    unowned.write_bytes(b"private")
    assert not record_internal_scratch(context, unowned, "timescale")


def test_modified_exact_definition_cannot_create_maintenance_ownership(synthetic: Any) -> None:
    _, _, contexts, _ = synthetic
    context = contexts[0]
    other = context.work_dir / "context-parts" / "part-0001.mov"
    other.write_bytes(b"unknown-producer")
    altered = replace(context, definition=context.definition.model_copy(update={"validator": None}))
    assert not record_internal_scratch(altered, other, "context_part")
    entry = next(
        item for item in preview(ScratchManager(), synthetic).entries if item.path == str(other)
    )
    assert entry.candidate_id is None


def test_bound_maintenance_is_pollable_exclusive_and_does_not_rebuild_runtime(
    synthetic: Any,
) -> None:
    store, root, _, _ = synthetic
    application = ProjectServiceApplication(work_root=root)
    opened = application.command({"operation": "open_project", "path": str(store.path)})
    assert opened.project_session_id is not None
    runtime = application._runtime
    for operation in ("scan_storage", "cleanup_storage"):
        with application.storage_authority(opened.project_session_id, maintenance=operation):
            status = application.inspect()
            assert status.active_operation == operation and status.active_run_id is None
            with pytest.raises(ProjectServiceError, match="E_PROJECT_SERVICE_BUSY"):
                application.command({"operation": "open_project", "path": str(store.path)})
        assert application._runtime is runtime
        assert application.inspect().active_operation is None


def test_http_requires_authentication_strict_payload_and_one_explicit_confirm(
    synthetic: Any,
) -> None:
    store, root, _, _ = synthetic
    application = ProjectServiceApplication(work_root=root)
    opened = application.command({"operation": "open_project", "path": str(store.path)})
    session = _session(RecordingPlatform(), root)
    payload = {
        "project_session_id": opened.project_session_id,
        "expected_storage_revision": opened.storage_revision,
    }
    prefix = "/api/studio/storage/"
    with _serve(application, session) as (base, _):
        for action in ("scratch-preview", "scratch-confirm"):
            assert _request(base, prefix + action, method="POST", payload=payload)[0] == 403
            assert _request(base, prefix + action, method="GET", token=session.token)[0] == 405
        assert (
            _request(
                base,
                prefix + "scratch-preview",
                method="POST",
                token=session.token,
                payload={**payload, "path": str(root)},
            )[0]
            == 422
        )
        status, result, _ = _request(
            base, prefix + "scratch-preview", method="POST", token=session.token, payload=payload
        )
        assert status == 200 and result is not None
        candidate_ids = [item["candidate_id"] for item in result["entries"] if item["candidate_id"]]
        request = {
            **payload,
            "ticket_id": result["ticket_id"],
            "candidate_ids": candidate_ids,
            "confirm_irreversible": True,
        }
        status, result, _ = _request(
            base, prefix + "scratch-confirm", method="POST", token=session.token, payload=request
        )
        assert status == 200 and result is not None and result["complete"]
        assert result["deletion_count"] == 2
        assert (
            _request(
                base,
                prefix + "scratch-confirm",
                method="POST",
                token=session.token,
                payload=request,
            )[0]
            == 409
        )


def test_unknown_same_named_user_file_and_index_failure_are_retained(
    synthetic: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, contexts, _ = synthetic
    context = contexts[0]
    unrecorded = context.work_dir / "context-parts" / "part-0001.mov"
    unrecorded.write_bytes(b"user data")
    monkeypatch.setattr(
        "zniku.media.scratch.os.replace", lambda *_args: (_ for _ in ()).throw(PermissionError())
    )
    assert not record_internal_scratch(context, unrecorded, "context_part")
    entry = next(
        item
        for item in preview(ScratchManager(), synthetic).entries
        if item.path == str(unrecorded)
    )
    assert entry.category == "unknown" and entry.candidate_id is None
    assert unrecorded.read_bytes() == b"user data"


def test_interruption_consumes_ticket_and_leaves_remaining_files(
    synthetic: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, _, _ = synthetic
    manager = ScratchManager()
    value = preview(manager, synthetic)
    entries = [item for item in value.entries if item.candidate_id]
    unlink = Path.unlink

    def interrupt_second(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == Path(entries[1].path):
            raise KeyboardInterrupt()
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        confirm(manager, store, value)
    assert not Path(entries[0].path).exists() and Path(entries[1].path).exists()
    with pytest.raises(ProjectStoreError, match="SCRATCH_TICKET"):
        confirm(manager, store, value)


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_confirmation_requires_boolean_true_and_never_raw_paths(value: Any) -> None:
    payload = {
        "project_session_id": str(uuid4()),
        "expected_storage_revision": 0,
        "ticket_id": str(uuid4()),
        "candidate_ids": [str(uuid4())],
        "confirm_irreversible": value,
    }
    with pytest.raises(ValidationError):
        ScratchConfirmRequest.model_validate(payload, strict=True)
    payload["confirm_irreversible"] = True
    assert ScratchConfirmRequest.model_validate(payload, strict=True).confirm_irreversible is True
    payload["path"] = "C:/user-media.mov"
    with pytest.raises(ValidationError):
        ScratchConfirmRequest.model_validate(payload, strict=True)
    with pytest.raises(ValidationError):
        ScratchPreviewRequest.model_validate(payload, strict=True)
