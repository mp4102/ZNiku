"""短合成视频验证先检查后收纳、当前任务边界与显式提交；不访问真实工程或 NAS。"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from authoring_helpers import authoring_command
from test_project_service_host_bridge import RecordingPlatform, _request, _serve
from test_source_admitted_service import analyze, create, full_request
from test_source_aligned_media import TOOLS, media
from zniku.project_service import (
    HostBridgeFailure,
    HostBridgeSession,
    ProjectServiceApplication,
    ProjectServiceError,
    create_project_service_host_bridge_session,
)
from zniku.project_service.handoff_intake import HandoffIntakeManager, _Job
from zniku.project_service.intake_contracts import (
    HandoffIntakeJobEnvelope,
    HandoffIntakeJobRequest,
    HandoffIntakeSelectEnvelope,
)
from zniku.project_service.intake_runtime import published_submission
from zniku.project_service.source_admitted import EXPAND_ROUTE, REPLACE_ROUTE
from zniku.project_service.source_admitted_application import dispatch
from zniku.runtime import NodeRun, NodeRunState, RunState
from zniku.source_admission import mosaic_restoration as mr

pytestmark = pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")


@dataclass
class IntakeSetup:
    application: ProjectServiceApplication
    project_path: Path
    source: Path
    node: NodeRun
    platform: RecordingPlatform
    session: HostBridgeSession
    manager: HandoffIntakeManager

    @property
    def binding(self) -> dict[str, Any]:
        assert self.node.external_handoff is not None
        return {
            "contract_version": "0.3.0",
            "project_session_id": self.application.inspect().project_session_id,
            "run_id": self.node.run_id,
            "node_run_id": self.node.node_run_id,
            "handoff_id": self.node.external_handoff.handoff_id,
            "port_id": "video",
            "ordinal": None,
        }

    @property
    def inbox(self) -> Path:
        return Path(self.node.work_dir) / "incoming"

    def select(self, path: Path) -> HandoffIntakeSelectEnvelope:
        self.platform.selections["open_file"] = (str(path),)
        action = self.session.issue_user_action({"capability": "open_file"})
        selection = self.session.invoke(
            {"capability": "open_file", "user_action_id": action.user_action_id, "arguments": {}}
        )
        return self.manager.select(
            {**self.binding, "selection_handle": selection.selections[0].selection_handle},
            session=self.session,
            application=self.application,
        )

    def check(
        self, selected: HandoffIntakeSelectEnvelope, *, overwrite: bool = False
    ) -> HandoffIntakeJobEnvelope:
        job = self.manager.check(
            {"contract_version": "0.3.0", "ticket_id": selected.ticket_id, "overwrite": overwrite},
            session=self.session,
            application=self.application,
        )
        return self.wait(job)

    def wait(self, job: HandoffIntakeJobRequest) -> HandoffIntakeJobEnvelope:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = self.manager.status(
                job.model_dump(), session=self.session, application=self.application
            )
            if status.phase in {"ready", "failed"}:
                return status
            time.sleep(0.01)
        pytest.fail("合成交回检查未在30秒内返回终态")

    def publish(self, ready: HandoffIntakeJobEnvelope) -> Path:
        assert ready.ready_id is not None
        result = self.manager.publish(
            {"contract_version": "0.3.0", "ready_id": ready.ready_id},
            session=self.session,
            application=self.application,
        )
        return Path(result.output_path)

    def persisted(self) -> object:
        runtime = self.application._require_session()[1]
        return (
            runtime.repository.get_run(self.node.run_id),
            self.application.inspect().storage_revision,
        )


def _setup(tmp_path: Path) -> IntakeSetup:
    app, _placeholder, project_path = create(tmp_path)
    source = tmp_path / "Example.repaired.mp4"
    media(source, rate="25", frames=12)
    status = app.inspect()
    dispatch(
        app,
        REPLACE_ROUTE,
        {
            "contract_version": "0.3.5",
            "project_session_id": status.project_session_id,
            "expected_storage_revision": status.storage_revision,
            "source_path": str(source),
            "reference_change_confirmed": True,
        },
    )
    analyzed = analyze(app)
    request = full_request(app, analyzed, tmp_path)
    request["processing"]["mr"] = {
        "mode": "external",
        "model_name": "Synthetic",
        "declared_container": "mp4",
        "operator_frame_order_confirmed": True,
    }
    dispatch(app, EXPAND_ROUTE, request)
    started = authoring_command(app, {"operation": "run_to", "node_id": "source-aligned.mr"})
    assert started.active_run_id is not None
    assert app.wait_until_idle(timeout=30)
    run = app.inspect_run_detail(started.active_run_id).run
    node = next(n for n in run.node_runs if n.node_id == "source-aligned.mr")
    assert node.state is NodeRunState.WAITING_EXTERNAL
    assert node.external_handoff is not None
    assert Path(node.external_handoff.output_targets[0].path).name == "Example.repaired.RM.mp4"
    platform = RecordingPlatform()
    session = create_project_service_host_bridge_session(
        app, studio_origin="http://127.0.0.1:4173", platform=platform
    )
    return IntakeSetup(app, project_path, source, node, platform, session, HandoffIntakeManager())


def _candidate(tmp_path: Path, *, container: str = "mov", frames: int = 12) -> Path:
    path = tmp_path / f"external-result.{container}"
    media(path, rate="25", frames=frames)
    arbitrary = path.with_suffix(".delivery")
    path.rename(arbitrary)
    return arbitrary


def test_selection_does_not_copy_and_copy_publish_still_requires_submit(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path)
    before = value.persisted()
    files = tuple(sorted(Path(value.node.work_dir).rglob("*")))
    selected = value.select(candidate)
    assert selected.action == "copy"
    assert selected.container == "mov"
    assert selected.archive_name == "Example.repaired.RM.mov"
    assert tuple(sorted(Path(value.node.work_dir).rglob("*"))) == files
    assert value.persisted() == before
    ready = value.check(selected)
    assert ready.phase == "ready", ready.message
    incoming = Path(selected.incoming_path)
    assert ready.bytes_done == candidate.stat().st_size
    assert incoming.read_bytes() == candidate.read_bytes()
    assert not Path(selected.output_path).exists()
    assert value.persisted() == before
    output = value.publish(ready)
    assert output.read_bytes() == candidate.read_bytes()
    assert candidate.exists() and not incoming.exists()
    assert value.persisted() == before
    # 模拟发布成功但HTTP响应丢失；旧ready不能重放，但刷新可发现唯一正式输出继续提交。
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code.endswith("EXPIRED")):
        value.publish(ready)
    runtime = value.application._require_session()[1]
    submission, targets = published_submission(runtime, value.node)
    assert submission is not None and Path(targets[0].path) == output
    assert value.node.external_handoff is not None
    value.application.command(
        {
            "operation": "submit_external",
            "run_id": value.node.run_id,
            "node_run_id": value.node.node_run_id,
            "handoff_id": value.node.external_handoff.handoff_id,
        }
    )
    assert value.application.wait_until_idle(timeout=30)
    final = runtime.repository.get_run(value.node.run_id)
    assert final.state is RunState.COMPLETED
    completed = runtime.repository.get_node_run(value.node.node_run_id)
    assert completed.state is NodeRunState.COMPLETED
    artifact = runtime.repository.get_artifact(completed.output_artifact_ids[0])
    assert Path(artifact.path) == output
    metadata = artifact.media_info["zniku.source.admitted"]
    assert isinstance(metadata, Mapping)
    assert metadata["declared_container"] == "mov"


@pytest.mark.parametrize("canonical", [False, True])
def test_incoming_detection_ignores_extension_and_renames_without_copy(
    tmp_path: Path, canonical: bool
) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path, container="mkv")
    incoming = value.inbox / ("Example.repaired.RM.mkv" if canonical else "my-result.anything")
    candidate.rename(incoming)
    initial_inode = incoming.stat().st_ino
    before = value.persisted()
    observed = value.manager.observe(
        value.binding, session=value.session, application=value.application
    )
    assert len(observed.candidates) == 1
    assert observed.candidates[0].container == "mkv"
    selected = value.manager.select(
        {**value.binding, "candidate_handle": observed.candidates[0].candidate_handle},
        session=value.session,
        application=value.application,
    )
    assert selected.action == ("none" if canonical else "rename")
    ready = value.check(selected)
    assert ready.phase == "ready", ready.message
    target = Path(selected.incoming_path)
    assert target.stat().st_ino == initial_inode
    assert list(value.inbox.iterdir()) == [target]
    assert value.persisted() == before


def test_bad_media_contract_does_not_copy_or_replace_existing_files(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path, frames=11)
    incoming = value.inbox / "Example.repaired.RM.mov"
    output = Path(value.node.work_dir) / "outputs" / incoming.name
    incoming.write_bytes(b"existing-incoming")
    output.write_bytes(b"existing-output")
    before = value.persisted()
    selected = value.select(candidate)
    assert selected.replace_existing
    ready = value.check(selected, overwrite=True)
    assert ready.phase == "failed" and "EXTERNAL_CONTRACT" in (ready.message or "")
    assert incoming.read_bytes() == b"existing-incoming"
    assert output.read_bytes() == b"existing-output"
    assert candidate.exists()
    assert value.persisted() == before
    assert not list(Path(value.node.work_dir).glob(".handoff-intake-*"))


def test_collision_requires_confirmation_and_preserves_output_until_publish(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path)
    incoming = value.inbox / "Example.repaired.RM.mov"
    output = Path(value.node.work_dir) / "outputs" / incoming.name
    incoming.write_bytes(b"old-incoming")
    output.write_bytes(b"old-output")
    selected = value.select(candidate)
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code.endswith("OVERWRITE")):
        value.check(selected)
    assert incoming.read_bytes() == b"old-incoming"
    assert output.read_bytes() == b"old-output"
    selected = value.select(candidate)
    ready = value.check(selected, overwrite=True)
    assert ready.phase == "ready", ready.message
    assert output.read_bytes() == b"old-output"
    assert value.publish(ready).read_bytes() == candidate.read_bytes()


@pytest.mark.parametrize("change", ["source", "session"])
def test_changed_selection_fails_before_check_or_copy(tmp_path: Path, change: str) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path)
    selected = value.select(candidate)
    if change == "source":
        with candidate.open("ab") as stream:
            stream.write(b"changed")
    else:
        value.application.command({"operation": "open_project", "path": str(value.project_path)})
    with pytest.raises((HostBridgeFailure, ProjectServiceError)):
        value.check(selected)
    assert not Path(selected.incoming_path).exists()
    assert not Path(selected.output_path).exists()
    assert candidate.exists()


def test_changed_checked_file_requires_new_check_before_publish(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    selected = value.select(_candidate(tmp_path))
    ready = value.check(selected)
    assert ready.phase == "ready", ready.message
    with Path(selected.incoming_path).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code.endswith("CHANGED")):
        value.publish(ready)
    assert Path(selected.incoming_path).exists()
    assert not Path(selected.output_path).exists()


def test_multiple_candidates_require_choice_and_multiple_outputs_are_rejected(
    tmp_path: Path,
) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path)
    shutil.copyfile(candidate, value.inbox / "first.bin")
    shutil.copyfile(candidate, value.inbox / "second.bin")
    observed = value.manager.observe(
        value.binding, session=value.session, application=value.application
    )
    assert len(observed.candidates) == 2
    assert not list((Path(value.node.work_dir) / "outputs").iterdir())
    selected = value.select(candidate)
    ready = value.check(selected)
    assert ready.phase == "ready", ready.message
    alternative = Path(selected.output_path).with_suffix(".mp4")
    alternative.write_bytes(b"other-container-result")
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code.endswith("AMBIGUOUS")):
        value.publish(ready)
    assert Path(selected.incoming_path).exists()
    Path(selected.output_path).write_bytes(b"second-container-result")
    runtime = value.application._require_session()[1]
    with pytest.raises(HostBridgeFailure, check=lambda error: error.code.endswith("AMBIGUOUS")):
        published_submission(runtime, value.node)
    assert (
        runtime.repository.get_node_run(value.node.node_run_id).state
        is NodeRunState.WAITING_EXTERNAL
    )


def test_copy_failure_preserves_external_source_and_existing_incoming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path)
    incoming = value.inbox / "Example.repaired.RM.mov"
    incoming.write_bytes(b"old-incoming")
    selected = value.select(candidate)

    def fail_sync(_fd: int) -> None:
        raise OSError("synthetic-disk-error")

    monkeypatch.setattr("zniku.project_service.handoff_intake.os.fsync", fail_sync)
    ready = value.check(selected, overwrite=True)
    assert ready.phase == "failed" and "synthetic-disk-error" in (ready.message or "")
    assert incoming.read_bytes() == b"old-incoming" and candidate.exists()
    assert not Path(selected.output_path).exists()
    assert not list(Path(value.node.work_dir).glob(".handoff-intake-*"))


def test_source_growing_during_copy_stops_before_progress_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path)
    selected = value.select(candidate)
    update = value.manager._update
    progressed: list[int] = []

    def grow(job: _Job, **changes: object) -> None:
        amount = changes.get("bytes_done")
        if type(amount) is int:
            progressed.append(amount)
            with candidate.open("ab") as stream:
                stream.write(b"still-writing")
        update(job, **changes)

    monkeypatch.setattr(value.manager, "_update", grow)
    status = value.check(selected)
    assert status.phase == "failed" and "增长" in (status.message or "")
    assert progressed and max(progressed) <= selected.source_size
    assert candidate.exists() and not Path(selected.incoming_path).exists()
    assert not Path(selected.output_path).exists()
    assert not list(Path(value.node.work_dir).glob(".handoff-intake-*"))


def test_long_check_exposes_status_but_blocks_project_switch_and_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _setup(tmp_path)
    selected = value.select(_candidate(tmp_path))
    entered, release = threading.Event(), threading.Event()
    original = mr.inspect_candidate

    def slow(*args: Any, **kwargs: Any) -> mr.CandidateInspection:
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(mr, "inspect_candidate", slow)
    job = value.manager.check(
        {"contract_version": "0.3.0", "ticket_id": selected.ticket_id, "overwrite": False},
        session=value.session,
        application=value.application,
    )
    try:
        assert entered.wait(5)
        assert value.application.inspect().active_operation == "import_external"
        status = value.manager.status(
            job.model_dump(), session=value.session, application=value.application
        )
        assert status.phase == "checking"
        with pytest.raises(ProjectServiceError, match="E_DESKTOP_BUSY"):
            value.application.prepare_desktop_close()
        with pytest.raises(ProjectServiceError, match="E_PROJECT_SERVICE_BUSY"):
            value.application.command(
                {"operation": "open_project", "path": str(value.project_path)}
            )
    finally:
        release.set()
    ready = value.wait(job)
    assert ready.phase == "ready", ready.message
    assert value.application.inspect().active_operation is None


def test_candidate_changed_during_scan_is_not_collected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path)
    selected = value.select(candidate)
    original = mr.inspect_candidate

    def change_after_scan(*args: Any, **kwargs: Any) -> mr.CandidateInspection:
        result = original(*args, **kwargs)
        with candidate.open("ab") as stream:
            stream.write(b"changed-after-read")
        return result

    monkeypatch.setattr(mr, "inspect_candidate", change_after_scan)
    ready = value.check(selected)
    assert ready.phase == "failed"
    assert candidate.exists()
    assert not Path(selected.incoming_path).exists()
    assert not Path(selected.output_path).exists()


def test_publish_io_failure_keeps_incoming_and_waiting_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _setup(tmp_path)
    selected = value.select(_candidate(tmp_path))
    ready = value.check(selected)
    assert ready.phase == "ready", ready.message
    before = value.persisted()

    def denied(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("synthetic-output-busy")

    monkeypatch.setattr("zniku.project_service.handoff_intake._publish_file", denied)
    with pytest.raises(PermissionError, match="synthetic-output-busy"):
        value.publish(ready)
    assert Path(selected.incoming_path).exists()
    assert not Path(selected.output_path).exists()
    assert value.persisted() == before


def test_http_intake_authorization_unknown_fields_and_readonly_selection(tmp_path: Path) -> None:
    value = _setup(tmp_path)
    candidate = _candidate(tmp_path)
    shutil.copyfile(candidate, value.inbox / "candidate.arbitrary")
    before = value.persisted()
    with _serve(value.application, value.session) as (base, _server):
        route = "/api/studio/handoff-intake/observe"
        status, _, _ = _request(base, route, method="POST", payload=value.binding)
        assert status == 403
        status, _, _ = _request(base, route, method="GET", token=value.session.token)
        assert status == 405
        status, _, _ = _request(
            base,
            route,
            method="POST",
            token=value.session.token,
            payload={**value.binding, "source_path": str(candidate)},
        )
        assert status == 422
        status, observed, _ = _request(
            base, route, method="POST", token=value.session.token, payload=value.binding
        )
        assert status == 200 and observed is not None
        assert len(observed["candidates"]) == 1
        status, selected, _ = _request(
            base,
            "/api/studio/handoff-intake/select",
            method="POST",
            token=value.session.token,
            payload={
                **value.binding,
                "candidate_handle": observed["candidates"][0]["candidate_handle"],
            },
        )
        assert status == 200 and selected is not None
        assert selected["action"] == "rename"
        assert not Path(selected["incoming_path"]).exists()
        assert not Path(selected["output_path"]).exists()
    assert value.persisted() == before
