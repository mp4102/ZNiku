"""以合成双任务验证外部产物导入的精确绑定、原子发布及无 Runtime 推进边界。

只使用 pytest 临时 DataFile、注入 picker 和通用节点 validator；不弹 GUI、不读取真实
媒体。错误候选、换代/链接/占用/过期请求都必须保留源与旧目标，全部副作用只在测试 attempt 内。
"""

from __future__ import annotations

import os
import shutil
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from authoring_helpers import authoring_command
from test_project_service_host_bridge import RecordingPlatform, _request, _serve
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
from zniku.project_service import (
    HostBridgeFailure,
    HostBridgeSession,
    ProjectServiceApplication,
    ProjectServiceError,
    create_project_service_host_bridge_session,
)
from zniku.project_service.handoff_import import (
    HandoffImportConfirmRequest,
    HandoffImportManager,
    HandoffImportPreviewEnvelope,
    HandoffImportPreviewRequest,
)
from zniku.runtime import NodeRun, NodeValidatorContext, NodeValidatorResult, RunnerError
from zniku.runtime.runner import RunnerFailureReason


@dataclass
class Setup:
    application: ProjectServiceApplication
    session: HostBridgeSession
    platform: RecordingPlatform
    manager: HandoffImportManager
    nodes: dict[str, NodeRun]
    source: Path
    project_path: Path

    def request(self, node_id: str = "a", source: Path | None = None) -> dict[str, Any]:
        self.platform.selections["open_file"] = (str(source or self.source),)
        action = self.session.issue_user_action({"capability": "open_file"})
        selection = self.session.invoke(
            {"capability": "open_file", "user_action_id": action.user_action_id, "arguments": {}}
        )
        node = self.nodes[node_id]
        assert node.external_handoff
        return {
            "contract_version": "0.3.0",
            "selection_handle": selection.selections[0].selection_handle,
            "project_session_id": self.application.inspect().project_session_id,
            "run_id": node.run_id,
            "node_run_id": node.node_run_id,
            "handoff_id": node.external_handoff.handoff_id,
            "port_id": "video",
            "ordinal": None,
        }

    def preview(
        self, node_id: str = "a", source: Path | None = None
    ) -> HandoffImportPreviewEnvelope:
        return self.manager.preview(
            self.request(node_id, source), session=self.session, application=self.application
        )

    def confirm(self, preview: HandoffImportPreviewEnvelope, *, overwrite: bool = False) -> object:
        return self.manager.confirm(
            {"contract_version": "0.3.0", "import_id": preview.import_id, "overwrite": overwrite},
            session=self.session,
            application=self.application,
        )

    def target(self, node_id: str = "a") -> Path:
        handoff = self.nodes[node_id].external_handoff
        assert handoff
        return Path(handoff.output_targets[0].path)


def _validator(context: NodeValidatorContext) -> NodeValidatorResult:
    assert context.outputs[0].path.name == "enhancement.mov"
    expected = context.request.node.parameters["expected"]
    actual = context.outputs[0].path.read_text(encoding="utf-8")
    if actual != expected:
        raise RunnerError(
            "E_SYNTHETIC_FRAME_COUNT",
            RunnerFailureReason.VALIDATOR_FAILED,
            f"预期 {expected}，实际 {actual}",
        )
    return NodeValidatorResult(passed=True)


def _setup(tmp_path: Path, *, validator: Any = _validator, multiple_outputs: bool = False) -> Setup:
    definition = NodeDefinition(
        type_id="test.import.external",
        version="0.2.0",
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        output_ports=(PortSpec(port_id="video", data_type="DataFile"),)
        + ((PortSpec(port_id="other", data_type="DataFile"),) if multiple_outputs else ()),
        parameter_schema={
            "type": "object",
            "properties": {"expected": {"type": "string"}},
            "required": ["expected"],
            "additionalProperties": False,
        },
        executor=ManualExternalExecutorSpec(
            instructions="导入本步骤合成产物",
            output_paths=(
                ExecutorOutputPathSpec(port_id="video", relative_path="enhancement.mov"),
            ),
        ),
        validator=ValidatorSpec(adapter="tests.import:validate"),
    )
    project = Project(
        project_id="project.import",
        name="合成双增强任务",
        graph=Graph(
            nodes=tuple(
                NodeInstance(
                    node_id=node_id,
                    type_id=definition.type_id,
                    definition_version=definition.version,
                    parameters={"expected": count},
                )
                for node_id, count in (("a", "899"), ("b", "902"))
            )
        ),
    )
    store = ProjectStore.create(tmp_path / "project.zniku", project, (definition,))
    app = ProjectServiceApplication(
        work_root=tmp_path / "attempts",
        definition_catalog=(definition,),
        validators={"tests.import:validate": validator},
    )
    app.command({"operation": "open_project", "path": str(store.path)})
    status = authoring_command(app, {"operation": "run_all"})
    assert app.wait_until_idle()
    assert status.active_run_id
    nodes = {
        node.node_id: node for node in app.inspect_run_detail(status.active_run_id).run.node_runs
    }
    source = tmp_path / "selected.mov"
    source.write_text("899", encoding="utf-8")
    platform = RecordingPlatform({"open_file": (str(source),)})
    session = create_project_service_host_bridge_session(
        app, studio_origin="http://127.0.0.1:4173", platform=platform
    )
    return Setup(app, session, platform, HandoffImportManager(), nodes, source, store.path)


def _persistent_state(value: Setup) -> object:
    status = value.application.inspect()
    return (
        status.snapshot,
        status.storage_revision,
        status.latest_results,
        value.application.inspect_run_detail(value.nodes["a"].run_id),
    )


def test_preview_cancel_has_no_files_and_confirm_copies_only_selected_task(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    before = _persistent_state(value)
    files = sorted(tmp_path.rglob("*"))
    preview = value.preview()
    assert preview.source_name == value.source.name and preview.source_size == 3
    assert not preview.replace_existing and preview.expires_in_seconds == 300
    assert sorted(tmp_path.rglob("*")) == files
    assert _persistent_state(value) == before
    result = value.confirm(preview)
    assert result.status == "imported"  # type: ignore[attr-defined]
    assert value.target().read_text(encoding="utf-8") == "899"
    assert value.source.read_text(encoding="utf-8") == "899"
    assert not value.target("b").exists()
    assert _persistent_state(value) == before
    assert not list(Path(value.nodes["a"].work_dir).glob(".handoff-import-*"))
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_EXPIRED"
    ):
        value.confirm(preview)


def test_wrong_task_candidate_rejected_before_replacing_old_target(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    value.target("b").write_bytes(b"old target")
    before = _persistent_state(value)
    preview = value.preview("b")
    with pytest.raises(HostBridgeFailure, match=r"E_SYNTHETIC_FRAME_COUNT.*902.*899"):
        value.confirm(preview, overwrite=True)
    assert value.target("b").read_bytes() == b"old target"
    assert value.source.read_bytes() == b"899"
    assert _persistent_state(value) == before
    assert not list(Path(value.nodes["b"].work_dir).glob(".handoff-import-*"))


def test_existing_target_requires_explicit_overwrite_and_uses_atomic_publish(
    tmp_path: Path,
) -> None:
    value = _setup(tmp_path)
    value.target().write_bytes(b"old target")
    preview = value.preview()
    assert preview.replace_existing
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_OVERWRITE_REQUIRED"
    ):
        value.confirm(preview)
    assert value.target().read_bytes() == b"old target"
    value.confirm(value.preview(), overwrite=True)
    assert value.target().read_bytes() == b"899"


@pytest.mark.parametrize("change", ["source", "target", "source_inode", "target_appears"])
def test_changed_files_require_fresh_preview(tmp_path: Path, change: str) -> None:
    value = _setup(tmp_path)
    if change != "target_appears":
        value.target().write_bytes(b"original")
    preview = value.preview()
    if change == "source_inode":
        value.source.unlink()
        value.source.write_bytes(b"899")
    elif change == "source":
        value.source.write_bytes(b"902")
    else:
        value.target().write_bytes(b"new other result")
    before = value.target().read_bytes()
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_CHANGED"
    ):
        value.confirm(preview, overwrite=True)
    assert value.target().read_bytes() == before


def test_samefile_and_hardlink_target_rejected_without_changes(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    value.target().write_bytes(b"899")
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_SAME_FILE"
    ):
        value.preview(source=value.target())
    value.target().unlink()
    os.link(value.source, value.target())
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_PATH"
    ):
        value.preview()
    assert value.source.read_bytes() == value.target().read_bytes() == b"899"


@pytest.mark.parametrize("operation", ["preview", "confirm"])
@pytest.mark.parametrize("escapes_attempt", [False, True])
def test_target_link_is_rejected_by_the_first_authoritative_boundary(
    tmp_path: Path, operation: str, escapes_attempt: bool
) -> None:
    """先绑定请求再改变文件，避免测试辅助 inspect 提前截获实际接口的错误。"""

    value = _setup(tmp_path)
    request = value.request()
    preview = value.preview() if operation == "confirm" else None
    before = _persistent_state(value)
    linked_file = value.source if escapes_attempt else value.target().with_name("linked.mov")
    if not escapes_attempt:
        linked_file.write_bytes(b"old target")
    original_bytes = linked_file.read_bytes()
    try:
        value.target().symlink_to(linked_file)
    except OSError:
        pytest.skip("本机未授权创建测试符号链接")
    # 越界链接先破坏 Core handoff containment；仍位于 attempt 内的链接才到导入路径门禁。
    expected_error = ProjectServiceError if escapes_attempt else HostBridgeFailure
    expected_code = "E_HANDOFF_RELATION_CORRUPT" if escapes_attempt else "E_HANDOFF_IMPORT_PATH"
    with pytest.raises(expected_error, check=lambda error: error.code == expected_code):
        if preview is None:
            value.manager.preview(request, session=value.session, application=value.application)
        else:
            value.confirm(preview, overwrite=True)
    assert value.target().is_symlink()
    assert value.target().readlink() == linked_file
    assert linked_file.read_bytes() == original_bytes
    assert value.source.read_bytes() == b"899"
    assert not list(Path(value.nodes["a"].work_dir).glob(".handoff-import-*"))
    value.target().unlink()
    assert _persistent_state(value) == before


@pytest.mark.parametrize("operation", ["preview", "confirm"])
@pytest.mark.parametrize("component", ["target", "parent"])
@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_import_link_gate_runs_without_native_link_privileges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    component: str,
    kind: str,
) -> None:
    """只注入 lstat 的链接身份；Core containment 仍真实执行，导入必须在复制前拒绝。"""

    value = _setup(tmp_path)
    value.target().write_bytes(b"old target")
    request = value.request()
    preview = value.preview() if operation == "confirm" else None
    before = _persistent_state(value)
    files = sorted(tmp_path.rglob("*"))
    blocked_path = value.target() if component == "target" else value.target().parent
    original = Path.lstat

    def linked_identity(path: Path) -> Any:
        if path == blocked_path:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if kind == "symlink" else original(path).st_mode,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT if kind == "reparse" else 0,
            )
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", linked_identity)
        with pytest.raises(
            HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_PATH"
        ):
            if preview is None:
                value.manager.preview(request, session=value.session, application=value.application)
            else:
                value.confirm(preview, overwrite=True)
    assert value.target().read_bytes() == b"old target"
    assert value.source.read_bytes() == b"899"
    assert sorted(tmp_path.rglob("*")) == files
    assert _persistent_state(value) == before


@pytest.mark.parametrize("operation", ["preview", "confirm"])
def test_core_handoff_containment_precedes_import_path_gate_without_link_privileges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """模拟系统将目标解析到 attempt 外；不可把 Core 关系损坏降为通用导入错误。"""

    value = _setup(tmp_path)
    value.target().write_bytes(b"old target")
    request = value.request()
    preview = value.preview() if operation == "confirm" else None
    before = _persistent_state(value)
    files = sorted(tmp_path.rglob("*"))
    target = value.target()
    outside = value.source.resolve(strict=True)
    original = Path.resolve

    def redirected(path: Path, strict: bool = False) -> Path:
        return outside if path == target else original(path, strict=strict)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", redirected)
        with pytest.raises(
            ProjectServiceError, check=lambda error: error.code == "E_HANDOFF_RELATION_CORRUPT"
        ):
            if preview is None:
                value.manager.preview(request, session=value.session, application=value.application)
            else:
                value.confirm(preview, overwrite=True)
    assert value.target().read_bytes() == b"old target"
    assert value.source.read_bytes() == b"899"
    assert sorted(tmp_path.rglob("*")) == files
    assert _persistent_state(value) == before


@pytest.mark.parametrize(
    "field", ["project_session_id", "run_id", "node_run_id", "handoff_id", "port_id", "ordinal"]
)
def test_unknown_or_stale_identity_cannot_issue_preview(tmp_path: Path, field: str) -> None:
    value = _setup(tmp_path)
    request = value.request()
    request[field] = "other" if field == "port_id" else 0 if field == "ordinal" else str(uuid4())
    with pytest.raises(ProjectServiceError):
        value.manager.preview(request, session=value.session, application=value.application)
    assert not value.target().exists()


def test_preview_expires_and_project_reopen_invalidates_confirmation(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    time = [1.0]
    value.manager = HandoffImportManager(clock=lambda: time[0])
    preview = value.preview()
    time[0] += 300
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_EXPIRED"
    ):
        value.confirm(preview)
    preview = value.preview()
    value.application.command({"operation": "open_project", "path": str(value.project_path)})
    with pytest.raises(ProjectServiceError, match="E_PROJECT_SESSION_CONFLICT"):
        value.confirm(preview)
    assert not value.target().exists()


def test_copy_io_failure_cleans_only_staging_and_preserves_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _setup(tmp_path)
    value.target().write_bytes(b"old")
    preview = value.preview()

    def fail_copy(source: object, destination: object, length: int) -> None:
        raise OSError("synthetic full disk")

    monkeypatch.setattr(shutil, "copyfileobj", fail_copy)
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_IO"):
        value.confirm(preview, overwrite=True)
    assert value.target().read_bytes() == b"old" and value.source.read_bytes() == b"899"
    assert not list(Path(value.nodes["a"].work_dir).glob(".handoff-import-*"))
    assert value.application.desktop_lifecycle() == (False, False)


def test_import_has_same_operation_mutex_but_status_remains_readable(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()

    def slow(context: NodeValidatorContext) -> NodeValidatorResult:
        entered.set()
        assert release.wait(10)
        return _validator(context)

    value = _setup(tmp_path, validator=slow)
    preview = value.preview()
    failures: list[BaseException] = []

    def confirm() -> None:
        try:
            value.confirm(preview)
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=confirm)
    thread.start()
    try:
        assert entered.wait(5)
        assert value.application.inspect().active_operation == "import_external"
        assert value.application.desktop_lifecycle() == (True, False)
        with pytest.raises(ProjectServiceError, match="E_DESKTOP_BUSY"):
            value.application.prepare_desktop_close()
        with pytest.raises(ProjectServiceError, match="E_PROJECT_SERVICE_BUSY"):
            value.application.command(
                {"operation": "open_project", "path": str(value.project_path)}
            )
        with pytest.raises(ProjectServiceError, match="E_PROJECT_SERVICE_BUSY"):
            value.preview("b")
    finally:
        release.set()
        thread.join(10)
    assert not failures and not thread.is_alive()
    assert value.application.inspect().active_operation is None


def test_http_import_uses_exact_host_authorization_and_rejects_get_unknown_fields(
    tmp_path: Path,
) -> None:
    value = _setup(tmp_path)
    payload = value.request()
    with _serve(value.application, value.session) as (base, _):
        route = "/api/studio/handoff-import/preview"
        status, _, _ = _request(base, route, method="POST", payload=payload)
        assert status == 403
        status, _, _ = _request(
            base,
            route,
            method="POST",
            payload=payload,
            token=value.session.token,
            origin="http://127.0.0.1:4174",
        )
        assert status == 403
        status, _, _ = _request(base, route, method="GET", token=value.session.token)
        assert status == 405
        status, _, _ = _request(
            base,
            route,
            method="POST",
            token=value.session.token,
            payload={**payload, "source_path": str(value.source)},
        )
        assert status == 422
        status, preview, _ = _request(
            base, route, method="POST", token=value.session.token, payload=payload
        )
        assert status == 200 and preview
        status, result, _ = _request(
            base,
            "/api/studio/handoff-import/confirm",
            method="POST",
            token=value.session.token,
            payload={
                "contract_version": "0.3.0",
                "import_id": preview["import_id"],
                "overwrite": False,
            },
        )
        assert status == 200 and result and result["status"] == "imported"
        assert value.source.read_bytes() == value.target().read_bytes()


def test_import_models_fail_closed() -> None:
    with pytest.raises(ValidationError):
        HandoffImportConfirmRequest.model_validate(
            {"contract_version": "0.3.0", "import_id": "x" * 24, "overwrite": 1}, strict=True
        )
    with pytest.raises(ValidationError):
        HandoffImportPreviewRequest.model_validate(
            {"contract_version": "0.3.0", "path": "anything"}, strict=True
        )


def test_multi_output_import_fails_before_copy_but_manual_handoff_stays_available(
    tmp_path: Path,
) -> None:
    value = _setup(tmp_path, multiple_outputs=True)
    before = sorted(tmp_path.rglob("*"))
    with pytest.raises(ProjectServiceError, match="E_HANDOFF_IMPORT_TARGET"):
        value.preview()
    assert sorted(tmp_path.rglob("*")) == before
    assert value.nodes["a"].external_handoff is not None
    assert len(value.nodes["a"].external_handoff.output_targets) == 2


def test_target_replaced_during_validator_does_not_overwrite_new_file(tmp_path: Path) -> None:
    target: list[Path] = []

    def changing(context: NodeValidatorContext) -> NodeValidatorResult:
        target[0].write_bytes(b"user replaced while checking")
        return _validator(context)

    value = _setup(tmp_path, validator=changing)
    target.append(value.target())
    value.target().write_bytes(b"original")
    preview = value.preview()
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_CHANGED"
    ):
        value.confirm(preview, overwrite=True)
    assert value.target().read_bytes() == b"user replaced while checking"
    assert not list(Path(value.nodes["a"].work_dir).glob(".handoff-import-*"))


def test_source_replaced_during_copy_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _setup(tmp_path)
    preview = value.preview()
    original = shutil.copyfileobj

    def changing(source: Any, destination: Any, length: int) -> None:
        original(source, destination, length)
        value.source.write_bytes(b"902")

    monkeypatch.setattr(shutil, "copyfileobj", changing)
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_CHANGED"
    ):
        value.confirm(preview)
    assert not value.target().exists()
    assert value.source.read_bytes() == b"902"


def test_candidate_validator_mutation_is_rejected(tmp_path: Path) -> None:
    def changing(context: NodeValidatorContext) -> NodeValidatorResult:
        valid = _validator(context)
        context.outputs[0].path.write_bytes(b"validator changed candidate")
        return valid

    value = _setup(tmp_path, validator=changing)
    preview = value.preview()
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_CHANGED"
    ):
        value.confirm(preview)
    assert not value.target().exists()
    assert value.source.read_bytes() == b"899"


def test_destination_created_at_publication_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _setup(tmp_path)
    preview = value.preview()
    original = os.link

    def collision(source: Path, target: Path) -> None:
        target.write_bytes(b"concurrent target")
        original(source, target)

    monkeypatch.setattr(os, "link", collision)
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_IO"):
        value.confirm(preview)
    assert value.target().read_bytes() == b"concurrent target"
    assert value.source.read_bytes() == b"899"


def test_reparse_component_fails_closed_without_native_link_privileges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zniku.project_service.handoff_import import _safe_path

    path = tmp_path / "junction"
    path.mkdir()
    original = Path.lstat

    def reparse(value: Path) -> Any:
        if value == path:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
        return original(value)

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(
        HostBridgeFailure, check=lambda error: error.code == "E_HANDOFF_IMPORT_PATH"
    ):
        _safe_path(path)


def test_superseded_handoff_rejects_confirmation(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    preview = value.preview()
    authoring_command(
        value.application,
        {"operation": "rerun_from_here", "run_id": value.nodes["a"].run_id, "node_id": "a"},
    )
    assert value.application.wait_until_idle()
    with pytest.raises(ProjectServiceError):
        value.confirm(preview)
    assert not value.target().exists()
