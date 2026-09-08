"""用临时合成来件验证收件投影、精确绑定、原位收纳和不自动提交的边界。

不弹原生窗口、不读取真实媒体；使用现有单值 DataFile 人工任务及严格 basename validator。
文件变化、目录逃逸、过期、并发目标、校验失败和未知字段均不得推进 Run 或登记 Artifact。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from authoring_helpers import authoring_command
from test_handoff_import import Setup, _persistent_state, _setup, _validator
from zniku.project_service import HostBridgeFailure, ProjectServiceError
from zniku.project_service.handoff_inbox import (
    HandoffInboxConfirmEnvelope,
    HandoffInboxConfirmRequest,
    HandoffInboxManager,
    HandoffInboxObserveEnvelope,
    HandoffInboxObserveRequest,
    HandoffInboxPreviewEnvelope,
)
from zniku.runtime import NodeValidatorContext, NodeValidatorResult
from zniku.runtime.paths import incoming_directory_name


@dataclass
class InboxSetup:
    base: Setup
    manager: HandoffInboxManager

    def binding(self, node_id: str = "a") -> dict[str, Any]:
        node = self.base.nodes[node_id]
        assert node.external_handoff
        return {
            "contract_version": "0.3.0",
            "project_session_id": self.base.application.inspect().project_session_id,
            "run_id": node.run_id,
            "node_run_id": node.node_run_id,
            "handoff_id": node.external_handoff.handoff_id,
            "port_id": "video",
            "ordinal": None,
        }

    def inbox(self, node_id: str = "a") -> Path:
        return (
            Path(self.base.nodes[node_id].work_dir) / "incoming" / incoming_directory_name("video")
        )

    def observe(self, node_id: str = "a") -> HandoffInboxObserveEnvelope:
        return self.manager.observe(
            self.binding(node_id), session=self.base.session, application=self.base.application
        )

    def preview(
        self, node_id: str = "a", *, handle: str | None = None
    ) -> HandoffInboxPreviewEnvelope:
        handle = handle or self.observe(node_id).candidates[0].candidate_handle
        return self.manager.preview(
            {**self.binding(node_id), "candidate_handle": handle},
            session=self.base.session,
            application=self.base.application,
        )

    def confirm(
        self, preview: HandoffInboxPreviewEnvelope, *, overwrite: bool = False
    ) -> HandoffInboxConfirmEnvelope:
        return self.manager.confirm(
            {"contract_version": "0.3.0", "inbox_id": preview.inbox_id, "overwrite": overwrite},
            session=self.base.session,
            application=self.base.application,
        )


def _inbox_setup(tmp_path: Path, **kwargs: Any) -> InboxSetup:
    base = _setup(tmp_path, **kwargs)
    value = InboxSetup(base, HandoffInboxManager())
    for node_id in base.nodes:
        # 独立测试收件 manager；目录准备本身由 NodeRunner 的另一组测试负责。
        value.inbox(node_id).mkdir(parents=True, exist_ok=True)
    return value


def _put(value: InboxSetup, name: str = "Topaz_export_001.mov", content: bytes = b"899") -> Path:
    path = value.inbox() / name
    path.write_bytes(content)
    return path


def test_zero_one_many_observation_is_sorted_bounded_and_read_only(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    before = _persistent_state(value.base)
    assert value.observe().candidates == ()
    _put(value, "b.mov")
    one = value.observe()
    assert one.allowed_suffix == ".mov" and len(one.candidates) == 1
    assert one.candidates[0].name == "b.mov" and one.candidates[0].size == 3
    _put(value, "A.MOV")
    _put(value, "notes.txt")
    _put(value, "unfinished.mov", b"")
    nested = value.inbox() / "nested.mov"
    nested.mkdir()
    (nested / "not-scanned.mov").write_bytes(b"899")
    files = sorted(tmp_path.rglob("*"))
    many = value.observe()
    assert [item.name for item in many.candidates] == ["A.MOV", "b.mov"]
    assert many.rejected_count == 3 and many.expires_in_seconds == 300
    assert sorted(tmp_path.rglob("*")) == files
    assert _persistent_state(value.base) == before
    assert not value.base.target().exists()


def test_collect_arbitrary_filename_moves_without_copy_or_submit(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    source_inode = source.stat().st_ino
    before = _persistent_state(value.base)
    files = sorted(tmp_path.rglob("*"))
    preview = value.preview()
    assert preview.source_name == source.name and preview.action == "move"
    assert not preview.replace_existing and preview.target_path == str(value.base.target())
    assert sorted(tmp_path.rglob("*")) == files
    result = value.confirm(preview)
    assert result.status == "collected" and result.source_size == 3
    assert not source.exists()
    assert value.base.target().read_bytes() == b"899"
    assert value.base.target().stat().st_ino == source_inode
    assert value.base.target().stat().st_nlink == 1
    assert not value.base.target("b").exists()
    assert _persistent_state(value.base) == before
    assert not list(Path(value.base.nodes["a"].work_dir).glob(".handoff-inbox-*"))
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_EXPIRED"
    ):
        value.confirm(preview)


def test_wrong_candidate_retains_both_source_and_old_target(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value, content=b"902")
    value.base.target().write_bytes(b"old")
    before = _persistent_state(value.base)
    with pytest.raises(HostBridgeFailure, match="E_SYNTHETIC_FRAME_COUNT"):
        value.confirm(value.preview(), overwrite=True)
    assert source.read_bytes() == b"902" and source.stat().st_nlink == 1
    assert value.base.target().read_bytes() == b"old"
    assert _persistent_state(value.base) == before


def test_overwrite_requires_explicit_confirmation_and_preserves_inode(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    source_inode = source.stat().st_ino
    value.base.target().write_bytes(b"old")
    preview = value.preview()
    assert preview.replace_existing
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_OVERWRITE_REQUIRED"
    ):
        value.confirm(preview)
    assert source.exists() and value.base.target().read_bytes() == b"old"
    value.confirm(value.preview(), overwrite=True)
    assert not source.exists()
    assert value.base.target().read_bytes() == b"899"
    assert value.base.target().stat().st_ino == source_inode


@pytest.mark.parametrize("change", ["source", "source_inode", "target", "target_appears"])
def test_changed_source_or_target_rejects_collection(tmp_path: Path, change: str) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    if change != "target_appears":
        value.base.target().write_bytes(b"old")
    preview = value.preview()
    if change == "source":
        source.write_bytes(b"902")
    elif change == "source_inode":
        source.unlink()
        source.write_bytes(b"899")
    else:
        value.base.target().write_bytes(b"changed target")
    expected = value.base.target().read_bytes()
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_CHANGED"
    ):
        value.confirm(preview, overwrite=True)
    assert source.exists() and value.base.target().read_bytes() == expected


def test_observed_candidate_changed_before_preview_is_rejected(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    observed = value.observe()
    source.write_bytes(b"now still writing")
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_CHANGED"
    ):
        value.preview(handle=observed.candidates[0].candidate_handle)
    assert not value.base.target().exists()


def test_candidate_handles_are_exact_task_bound_and_consumed(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    _put(value)
    handle = value.observe().candidates[0].candidate_handle
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_EXPIRED"
    ):
        value.preview("b", handle=handle)
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_EXPIRED"
    ):
        value.preview(handle=handle)
    handle = value.observe().candidates[0].candidate_handle
    value.preview(handle=handle)
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_EXPIRED"
    ):
        value.preview(handle=handle)


def test_refresh_and_expiry_invalidate_old_handles_and_confirmation(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    now = [1.0]
    value.manager = HandoffInboxManager(clock=lambda: now[0])
    _put(value)
    first = value.observe().candidates[0].candidate_handle
    second = value.observe().candidates[0].candidate_handle
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_EXPIRED"
    ):
        value.preview(handle=first)
    now[0] += 300
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_EXPIRED"
    ):
        value.preview(handle=second)
    preview = value.preview()
    now[0] += 300
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_EXPIRED"
    ):
        value.confirm(preview)
    assert not value.base.target().exists()


def test_reopen_and_superseded_handoff_reject_pending_confirmation(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    preview = value.preview()
    value.base.application.command(
        {"operation": "open_project", "path": str(value.base.project_path)}
    )
    with pytest.raises(ProjectServiceError, match="E_PROJECT_SESSION_CONFLICT"):
        value.confirm(preview)
    preview = value.preview()
    authoring_command(
        value.base.application,
        {"operation": "rerun_from_here", "run_id": value.base.nodes["a"].run_id, "node_id": "a"},
    )
    assert value.base.application.wait_until_idle()
    with pytest.raises(ProjectServiceError):
        value.confirm(preview)
    assert source.exists() and not value.base.target().exists()


@pytest.mark.parametrize("field", ["project_session_id", "run_id", "node_run_id", "handoff_id"])
def test_unknown_runtime_binding_cannot_observe(tmp_path: Path, field: str) -> None:
    value = _inbox_setup(tmp_path)
    binding = value.binding()
    binding[field] = str(uuid4())
    with pytest.raises(ProjectServiceError):
        value.manager.observe(
            binding, session=value.base.session, application=value.base.application
        )


def test_unknown_raw_paths_and_non_boolean_overwrite_fail_closed(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    for key in ("path", "inbox_path", "source_path", "target_path", "candidate_handle"):
        with pytest.raises(
            HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_REQUEST"
        ):
            value.manager.observe(
                {**value.binding(), key: str(tmp_path)},
                session=value.base.session,
                application=value.base.application,
            )
    with pytest.raises(ValidationError):
        HandoffInboxConfirmRequest.model_validate(
            {"contract_version": "0.3.0", "inbox_id": "x" * 24, "overwrite": 1}, strict=True
        )
    with pytest.raises(ValidationError):
        HandoffInboxObserveRequest.model_validate({**value.binding(), "ordinal": 0.0}, strict=True)


def test_multiple_output_authority_remains_fail_closed(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path, multiple_outputs=True)
    with pytest.raises(ProjectServiceError, match="E_HANDOFF_IMPORT_TARGET"):
        value.observe()
    assert not value.base.target().exists()


def test_candidate_and_entry_limit_reject_without_partial_handle_list(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    for index in range(65):
        _put(value, f"candidate-{index:03}.mov")
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_LIMIT"
    ):
        value.observe()
    for index in range(200):
        _put(value, f"notes-{index:03}.txt")
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_LIMIT"
    ):
        value.observe()
    assert not value.base.target().exists()


def test_hardlinked_candidates_are_not_observed(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    os.link(source, value.inbox() / "second.mov")
    observed = value.observe()
    assert observed.candidates == () and observed.rejected_count == 2


def test_missing_inbox_does_not_create_directories(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    value.inbox().rmdir()
    before = sorted(tmp_path.rglob("*"))
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_PATH"
    ):
        value.observe()
    assert sorted(tmp_path.rglob("*")) == before


def test_target_created_at_publication_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    preview = value.preview()
    original = os.link

    def collide(candidate: Path, target: Path) -> None:
        if target == value.base.target():
            target.write_bytes(b"concurrent output")
        original(candidate, target)

    monkeypatch.setattr(os, "link", collide)
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_IO"):
        value.confirm(preview)
    assert source.read_bytes() == b"899"
    assert value.base.target().read_bytes() == b"concurrent output"


def test_source_unlink_failure_rolls_back_only_own_new_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    preview = value.preview()
    original = Path.unlink

    def occupied(path: Path, missing_ok: bool = False) -> None:
        if path == source:
            raise PermissionError("synthetic source still open")
        original(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", occupied)
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_IO"):
        value.confirm(preview)
    assert source.read_bytes() == b"899" and source.stat().st_nlink == 1
    assert not value.base.target().exists()


def test_source_replaced_at_no_replace_publication_is_never_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    preview = value.preview()
    original = os.link

    def changing(candidate: Path, target: Path) -> None:
        if target == value.base.target():
            source.unlink()
            source.write_bytes(b"other arrival")
        original(candidate, target)

    monkeypatch.setattr(os, "link", changing)
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_CHANGED"
    ):
        value.confirm(preview)
    assert source.read_bytes() == b"other arrival"
    assert not value.base.target().exists()


def test_unsupported_hardlink_does_not_fall_back_to_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    preview = value.preview()

    def unsupported(source: Path, target: Path) -> None:
        raise OSError("synthetic filesystem without hardlinks")

    monkeypatch.setattr(os, "link", unsupported)
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_IO"):
        value.confirm(preview)
    assert source.read_bytes() == b"899" and not value.base.target().exists()


def test_unlink_deletes_then_reports_failure_preserves_published_recovery_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    before = _persistent_state(value.base)
    preview = value.preview()
    original = Path.unlink

    def uncertain(path: Path, missing_ok: bool = False) -> None:
        original(path, missing_ok=missing_ok)
        if path == source:
            raise OSError("synthetic deleted then error")

    monkeypatch.setattr(Path, "unlink", uncertain)
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_PARTIAL"
    ):
        value.confirm(preview)
    assert not source.exists()
    assert value.base.target().read_bytes() == b"899"
    assert value.base.target().stat().st_nlink == 1
    assert _persistent_state(value.base) == before


def test_cleanup_never_deletes_replacement_at_temporary_alias(tmp_path: Path) -> None:
    replacements: list[Path] = []

    def replacing(context: NodeValidatorContext) -> NodeValidatorResult:
        result = _validator(context)
        alias = context.outputs[0].path
        alias.unlink()
        alias.write_bytes(b"unrelated replacement")
        replacements.append(alias)
        return result

    value = _inbox_setup(tmp_path, validator=replacing)
    source = _put(value)
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_CHANGED"
    ):
        value.confirm(value.preview())
    assert replacements[0].read_bytes() == b"unrelated replacement"
    assert source.read_bytes() == b"899" and not value.base.target().exists()


def test_uncertain_publication_keeps_alias_when_no_other_copy_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    preview = value.preview()
    original = Path.unlink

    def lost_names(path: Path, missing_ok: bool = False) -> None:
        original(path, missing_ok=missing_ok)
        if path == source:
            original(value.base.target())
            raise OSError("synthetic ambiguous names after I/O failure")

    monkeypatch.setattr(Path, "unlink", lost_names)
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_IO"):
        value.confirm(preview)
    aliases = list(Path(value.base.nodes["a"].work_dir).glob(".handoff-inbox-*/enhancement.mov"))
    assert len(aliases) == 1 and aliases[0].read_bytes() == b"899"


def test_overwrite_rename_failure_preserves_source_and_old_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    value.base.target().write_bytes(b"old target")
    preview = value.preview()

    def occupied(source: Path, target: Path) -> None:
        raise PermissionError("synthetic occupied target")

    monkeypatch.setattr(os, "replace", occupied)
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_IO"):
        value.confirm(preview, overwrite=True)
    assert source.read_bytes() == b"899" and source.stat().st_nlink == 1
    assert value.base.target().read_bytes() == b"old target"


def test_overwrite_does_not_unlink_incoming_after_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _inbox_setup(tmp_path)
    source = _put(value)
    value.base.target().write_bytes(b"old target")
    preview = value.preview()
    original = Path.unlink

    def cannot_unlink_original(path: Path, missing_ok: bool = False) -> None:
        if path == source:
            raise AssertionError("覆盖必须使用同盘原子移动，不能覆盖后再删原来件")
        original(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", cannot_unlink_original)
    result = value.confirm(preview, overwrite=True)
    assert result.status == "collected" and not source.exists()
    assert value.base.target().read_bytes() == b"899"
    assert value.base.target().stat().st_nlink == 1


def test_target_changed_during_validator_is_not_replaced(tmp_path: Path) -> None:
    targets: list[Path] = []

    def changing(context: NodeValidatorContext) -> NodeValidatorResult:
        targets[0].write_bytes(b"external changed target")
        return _validator(context)

    value = _inbox_setup(tmp_path, validator=changing)
    source = _put(value)
    targets.append(value.base.target())
    value.base.target().write_bytes(b"old")
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_CHANGED"
    ):
        value.confirm(value.preview(), overwrite=True)
    assert source.read_bytes() == b"899"
    assert value.base.target().read_bytes() == b"external changed target"


def test_source_changed_during_validator_is_not_collected(tmp_path: Path) -> None:
    sources: list[Path] = []

    def changing(context: NodeValidatorContext) -> NodeValidatorResult:
        result = _validator(context)
        sources[0].write_bytes(b"external still writing")
        return result

    value = _inbox_setup(tmp_path, validator=changing)
    source = _put(value)
    sources.append(source)
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_INBOX_CHANGED"
    ):
        value.confirm(value.preview())
    assert source.read_bytes() == b"external still writing" and not value.base.target().exists()


def test_collection_releases_application_operation_after_failure(tmp_path: Path) -> None:
    value = _inbox_setup(tmp_path)
    _put(value, content=b"902")
    with pytest.raises(HostBridgeFailure, match="E_SYNTHETIC_FRAME_COUNT"):
        value.confirm(value.preview())
    assert value.base.application.inspect().active_operation is None
    assert value.base.application.desktop_lifecycle() == (False, False)
    assert len(value.observe().candidates) == 1
