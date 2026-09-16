"""独立回归英文平铺收件的跨候选保护、NAS 发布和失败回退；仅使用临时合成文本。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from authoring_helpers import authoring_command
from test_handoff_batch import _batch, _binding, _check, _preview, _sources, _validate
from test_handoff_import import Setup, _persistent_state, _setup
from test_handoff_inbox import InboxSetup
from test_project_service_host_bridge import RecordingPlatform
from zniku.graph import (
    ExecutionMode,
    ExecutorOutputPathSpec,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    ValidatorSpec,
)
from zniku.project import Project, ProjectStore
from zniku.project.storage import new_project_storage
from zniku.project_service import HostBridgeFailure, ProjectServiceApplication
from zniku.project_service.handoff_batch import HandoffBatchManager
from zniku.project_service.handoff_import import HandoffImportManager
from zniku.project_service.handoff_inbox import HandoffInboxManager
from zniku.project_service.host_bridge import create_project_service_host_bridge_session
from zniku.project_service.storage_paths import prepare_storage_location


def test_flat_cross_mapping_cannot_overwrite_another_selected_candidate(tmp_path: Path) -> None:
    """即使允许替换目的地，也不能销毁同批尚未消费的另一来件。"""

    value = _batch(tmp_path, readable=True)
    manager = HandoffBatchManager()
    observed = manager.observe(
        _binding(value), session=value.session, application=value.application
    )
    sources = _sources(value, Path(observed.inbox_path))
    for index, source in enumerate(sources):
        source.write_text(f"unique-candidate-{index}", encoding="utf-8")
    before_files = {path: path.read_bytes() for path in sources}
    before_runtime = _persistent_state(value)
    preview = _preview(manager, value, ())
    assert len(preview.rows) == 2 and all(match.candidate_handle for match in preview.matches)

    with pytest.raises(HostBridgeFailure):
        manager.confirm(
            {
                "contract_version": "0.3.0",
                "batch_id": preview.batch_id,
                "items": [
                    {
                        "port_id": match.port_id,
                        "candidate_handle": preview.matches[1 - index].candidate_handle,
                        "overwrite": True,
                    }
                    for index, match in enumerate(preview.matches)
                ],
            },
            session=value.session,
            application=value.application,
        )

    assert {path: path.read_bytes() for path in sources} == before_files
    assert _persistent_state(value) == before_runtime
    assert all(not Path(row.target_path).exists() for row in observed.rows)


def test_duplicate_target_basenames_use_distinct_readable_inboxes_and_explicit_mapping(
    tmp_path: Path,
) -> None:
    """两个端口同名文件必须保持独立绑定，不能自动猜测或产生共享收件目标。"""

    definition = NodeDefinition(
        type_id="test.english.collision",
        version="1.0.0",
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        output_ports=tuple(
            PortSpec(port_id=port, data_type="DataFile") for port in ("left", "right")
        ),
        parameter_schema={
            "type": "object",
            "properties": {"expected": {"type": "string"}},
            "required": ["expected"],
            "additionalProperties": False,
        },
        executor=ManualExternalExecutorSpec(
            instructions="交回两个同名合成结果",
            output_paths=tuple(
                ExecutorOutputPathSpec(port_id=port, relative_path=f"{port}/result.mov")
                for port in ("left", "right")
            ),
        ),
        validator=ValidatorSpec(adapter="tests.collision:validate"),
    )
    storage = new_project_storage(tmp_path / "collision.zniku")
    prepare_storage_location(storage, current=None)
    store = ProjectStore.create(
        tmp_path / "collision.zniku",
        Project(
            project_id="test.english.collision",
            name="合成重名端口",
            graph=Graph(
                nodes=(
                    NodeInstance(
                        node_id="a",
                        type_id=definition.type_id,
                        definition_version=definition.version,
                        parameters={"expected": "899"},
                    ),
                )
            ),
        ),
        (definition,),
        storage=storage,
    )
    application = ProjectServiceApplication(
        work_root=tmp_path / "legacy",
        definition_catalog=(definition,),
        validators={"tests.collision:validate": _validate},
    )
    application.command({"operation": "open_project", "path": str(store.path)})
    status = authoring_command(application, {"operation": "run_all"})
    assert application.wait_until_idle() and status.active_run_id
    node = application.inspect_run_detail(status.active_run_id).run.node_runs[0]
    sources = (tmp_path / "external-left/result.mov", tmp_path / "external-right/result.mov")
    for source in sources:
        source.parent.mkdir()
        source.write_bytes(b"899")
    platform = RecordingPlatform({})
    session = create_project_service_host_bridge_session(
        application, studio_origin="http://127.0.0.1:4173", platform=platform
    )
    value = Setup(
        application, session, platform, HandoffImportManager(), {"a": node}, sources[0], store.path
    )
    manager = HandoffBatchManager()
    preview = _preview(manager, value, sources)
    assert all(match.state == "ambiguous" for match in preview.matches)
    assert {Path(row.incoming_path).parent.name for row in preview.rows} == {
        "output-001",
        "output-002",
    }
    assert all("port-" not in row.incoming_path for row in preview.rows)
    before_runtime = _persistent_state(value)

    confirmed = manager.confirm(
        {
            "contract_version": "0.3.0",
            "batch_id": preview.batch_id,
            "items": [
                {
                    "port_id": row.port_id,
                    "candidate_handle": candidate.candidate_handle,
                    "overwrite": False,
                }
                for row, candidate in zip(preview.rows, preview.candidates, strict=True)
            ],
        },
        session=session,
        application=application,
    )
    assert confirmed.complete and all(row.collected for row in confirmed.rows)
    checked = _check(manager, value)
    assert checked.published and checked.readiness and checked.readiness.ready_for_submit
    assert len({row.target_path for row in checked.rows}) == 2
    assert all(Path(row.target_path).read_bytes() == b"899" for row in checked.rows)
    assert all(source.read_bytes() == b"899" for source in sources)
    assert _persistent_state(value) == before_runtime


@pytest.mark.skipif(os.name != "nt", reason="Windows 英文发布使用同盘不覆盖 rename")
def test_english_single_ui_import_does_not_require_nas_hardlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _setup(tmp_path, readable=True)
    before_runtime = _persistent_state(value)
    before_source = value.source.read_bytes()
    preview = value.preview()

    def reject_hardlink(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic filesystem without hardlink support")

    monkeypatch.setattr(os, "link", reject_hardlink)
    value.confirm(preview)

    assert value.source.read_bytes() == before_source
    assert value.target().read_bytes() == before_source
    assert _persistent_state(value) == before_runtime
    assert not list(Path(value.nodes["a"].work_dir).glob(".handoff-import-*"))


@pytest.mark.skipif(os.name != "nt", reason="验证 Windows 英文收件的 rename 失败回退")
@pytest.mark.parametrize("replace_existing", [False, True])
def test_single_inbox_publish_failure_restores_candidate_and_preserves_old_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_existing: bool
) -> None:
    base = _setup(tmp_path, readable=True)
    value = InboxSetup(base, HandoffInboxManager())
    incoming = Path(value.observe().inbox_path)
    source = incoming / "arbitrary-name.mov"
    source.write_bytes(b"899")
    if replace_existing:
        base.target().write_bytes(b"old-target")
    preview = value.preview()
    before_runtime = _persistent_state(base)
    original = os.replace if replace_existing else os.rename

    def fail_publish(source_path: str | Path, target_path: str | Path) -> None:
        if Path(target_path) == base.target():
            raise OSError("synthetic publication failure")
        original(source_path, target_path)

    monkeypatch.setattr(os, "replace" if replace_existing else "rename", fail_publish)
    with pytest.raises(HostBridgeFailure):
        value.confirm(preview, overwrite=replace_existing)

    assert source.read_bytes() == b"899"
    if replace_existing:
        assert base.target().read_bytes() == b"old-target"
    else:
        assert not base.target().exists()
    assert _persistent_state(base) == before_runtime
    assert not list(Path(base.nodes["a"].work_dir).glob(".handoff-inbox-*"))
