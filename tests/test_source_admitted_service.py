"""以短合成媒体验证新源准入、换源 CAS、旧图保护与真实分切，不运行外部 AI。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from authoring_helpers import authoring_command
from test_av27_template import _prepare_payload
from test_project_service_host import _request, _serve
from test_source_aligned_media import media
from zniku.avenhance_v27 import av27_python_adapters, av27_validators
from zniku.avenhance_v27.probe import Av27MediaError, probe_header
from zniku.chapter_batch import definitions as chapter_batch
from zniku.chapter_batch.contracts import NAMESPACE as BATCH_NAMESPACE
from zniku.chapter_batch.final_publish import TYPE_ID as FINAL_PUBLISH_TYPE_ID
from zniku.chapter_batch.final_publish import Metadata as PublishedMetadata
from zniku.media import media_python_adapters, media_validators
from zniku.project import Project, ProjectStore, ProjectStoreError
from zniku.project_service.models import StatusEnvelope
from zniku.project_service.service import ProjectServiceApplication
from zniku.project_service.source_admitted import (
    CANCEL_ROUTE,
    CREATE_ROUTE,
    EXPAND_ROUTE,
    FULL_PREVIEW_ROUTE,
    REPLACE_ROUTE,
    SourceAdmittedError,
    SourceAdmittedFullEnvelope,
)
from zniku.project_service.source_admitted_application import dispatch
from zniku.runtime import FailureReason, NodeRunState, RunState, RuntimeRepositoryError
from zniku.source_admission import adapters as admitted_adapters
from zniku.source_admission import definitions
from zniku.source_admission.contracts import NAMESPACE, SOURCE_NAMESPACE, OverlapMetadata
from zniku.source_admission.process import probe_lines

TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def application(tmp_path: Path) -> ProjectServiceApplication:
    return ProjectServiceApplication(
        work_root=tmp_path / "fallback",
        definition_catalog=(
            *definitions.built_in_definitions(),
            *chapter_batch.built_in_definitions(),
        ),
        python_adapters={
            **media_python_adapters(),
            **av27_python_adapters(),
            **definitions.python_adapters(),
            **chapter_batch.python_adapters(),
        },
        validators={
            **media_validators(),
            **av27_validators(),
            **definitions.validators(),
            **chapter_batch.validators(),
        },
    )


def create(tmp_path: Path, *, real: bool = False) -> tuple[ProjectServiceApplication, Path, Path]:
    payload = _prepare_payload(tmp_path)
    source = Path(payload["sources"][0]["source_path"])
    if real:
        source.unlink()
        media(source, frames=60)
    app = application(tmp_path)
    result = dispatch(app, CREATE_ROUTE, {"contract_version": "0.3.5", "request": payload})
    assert isinstance(result, StatusEnvelope)
    assert result.snapshot is not None
    assert {n.definition_version for n in result.snapshot.project.graph.nodes} == {"0.3.5"}
    return app, source, Path(payload["project_path"])


def analyze(app: ProjectServiceApplication) -> str:
    result = authoring_command(app, {"operation": "run_all"})
    assert result.active_run_id is not None
    assert app.wait_until_idle(timeout=30)
    _store, runtime = app._require_session()
    run = runtime.repository.get_run(result.active_run_id)
    assert run.state is RunState.COMPLETED, [(n.state, n.error) for n in run.node_runs]
    return run.run_id


def full_request(app: ProjectServiceApplication, run_id: str, tmp_path: Path) -> dict[str, Any]:
    status = app.inspect()
    return {
        "contract_version": "0.3.5",
        "project_session_id": status.project_session_id,
        "expected_storage_revision": status.storage_revision,
        "preparation_run_id": run_id,
        "processing": {
            "settings": {"chapter_selector": {"mode": "average", "count": 3}},
            "enhancement": {"model_name": "Synthetic"},
            "program_encode": {"encoder": "cpu"},
        },
        "publication": {
            "output_root": str(tmp_path),
            "title": "Synthetic",
            "year": "2026",
            "overwrite": False,
        },
    }


def test_create_is_new_exact_no_media_artifact_and_old_file_untouched(tmp_path: Path) -> None:
    app, source, path = create(tmp_path)
    assert source.read_bytes() == b"synthetic-source"
    assert not app._require_session()[1].repository.list_runs()
    assert ProjectStore.open(path).load().project.graph == app.inspect().snapshot.project.graph  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "field,value",
    [
        ("reference_change_confirmed", 1),
        ("unexpected", True),
        ("expected_storage_revision", 999),
        ("project_session_id", str(uuid4())),
        ("source_path", "bad\x00path"),
        ("source_path", " leading-space"),
    ],
)
def test_replace_rejects_stale_unknown_unconfirmed_without_mutation(
    tmp_path: Path, field: str, value: Any
) -> None:
    app, source, path = create(tmp_path)
    status = app.inspect()
    payload = {
        "contract_version": "0.3.5",
        "project_session_id": status.project_session_id,
        "expected_storage_revision": status.storage_revision,
        "source_path": str(source),
        "reference_change_confirmed": True,
        field: value,
    }
    before = path.read_bytes()
    with pytest.raises(SourceAdmittedError):
        dispatch(app, REPLACE_ROUTE, payload)
    assert path.read_bytes() == before
    assert source.read_bytes() == b"synthetic-source"


def test_http_create_and_closed_wire_error_version(tmp_path: Path) -> None:
    app = application(tmp_path)
    with _serve(app) as (url, _server):
        status, response, _ = _request(
            url,
            CREATE_ROUTE,
            method="POST",
            payload={"contract_version": "0.3.5", "request": _prepare_payload(tmp_path)},
        )
        assert status == 200 and response["contract_version"] == "0.3.0"
        status, response, _ = _request(
            url,
            REPLACE_ROUTE,
            method="POST",
            payload={"contract_version": "0.3.5", "source_path": "missing"},
        )
        assert status == 422 and response["contract_version"] == "0.3.5"


def test_cancel_analysis_is_bound_and_retains_files_without_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, source, _path = create(tmp_path)
    entered = threading.Event()

    def blocked(*args: Any, **kwargs: Any) -> Any:
        # 使用真实受控子进程与正式取消检查，不用直接抛 RunnerCancelled 遮蔽错误映射。
        list(
            probe_lines(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cancel_event=kwargs["cancel_event"],
                heartbeat=entered.set,
            )
        )
        pytest.fail("受控进程应被取消")

    monkeypatch.setattr(admitted_adapters, "admit_source", blocked)
    started = authoring_command(app, {"operation": "run_all"})
    assert entered.wait(5)
    assert started.active_run_id is not None
    status = app.inspect()
    with pytest.raises(SourceAdmittedError):
        dispatch(
            app,
            CANCEL_ROUTE,
            {
                "contract_version": "0.3.5",
                "project_session_id": str(uuid4()),
                "run_id": started.active_run_id,
            },
        )
    dispatch(
        app,
        CANCEL_ROUTE,
        {
            "contract_version": "0.3.5",
            "project_session_id": status.project_session_id,
            "run_id": started.active_run_id,
        },
    )
    assert app.wait_until_idle(timeout=10)
    run = app._require_session()[1].repository.get_run(started.active_run_id)
    assert not any(node.output_artifact_ids for node in run.node_runs)
    source_attempt = next(node for node in run.node_runs if node.node_id == "source.program")
    assert (
        source_attempt.error is not None and source_attempt.error.reason is FailureReason.CANCELLED
    )
    assert source.read_bytes() == b"synthetic-source"
    assert not admitted_adapters._active


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_failed_analysis_can_explicitly_replace_and_reanalyze(tmp_path: Path) -> None:
    app, source, _path = create(tmp_path)
    started = authoring_command(app, {"operation": "run_all"})
    assert app.wait_until_idle(timeout=10)
    assert started.active_run_id is not None
    runtime = app._require_session()[1]
    failed = runtime.repository.get_run(started.active_run_id)
    assert any(node.state is NodeRunState.FAILED for node in failed.node_runs)
    assert not any(node.output_artifact_ids for node in failed.node_runs)
    replacement = tmp_path / "fixed.mp4"
    media(replacement, frames=24)
    status = app.inspect()
    dispatch(
        app,
        REPLACE_ROUTE,
        {
            "contract_version": "0.3.5",
            "project_session_id": status.project_session_id,
            "expected_storage_revision": status.storage_revision,
            "source_path": str(replacement),
            "reference_change_confirmed": True,
        },
    )
    second = analyze(app)
    assert second != failed.run_id
    assert source.read_bytes() == b"synthetic-source"
    assert runtime.repository.get_run(failed.run_id).state is RunState.FAILED


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_position_only_edit_does_not_invalidate_analysis_but_source_edit_does(
    tmp_path: Path,
) -> None:
    app, _source, _path = create(tmp_path, real=True)
    run_id = analyze(app)
    snapshot = app.inspect().snapshot
    assert snapshot is not None
    data = snapshot.project.model_dump()
    data["graph"]["nodes"][0]["ui_position"] = {"x": 999, "y": 777}
    authoring_command(app, {"operation": "save_project", "project": Project.model_validate(data)})
    preview = dispatch(app, FULL_PREVIEW_ROUTE, full_request(app, run_id, tmp_path))
    assert isinstance(preview, SourceAdmittedFullEnvelope)
    latest = app._require_session()[1].repository.get_latest("source.program")
    assert latest is not None and not latest.stale
    # 在同一图改真实 source 参数，不能以相同节点 shape 或仅坐标判断为仍然有效。
    replacement = tmp_path / "changed.mkv"
    replacement.write_bytes(b"different")
    data["graph"]["nodes"][0]["parameters"]["source_path"] = str(replacement)
    authoring_command(app, {"operation": "save_project", "project": Project.model_validate(data)})
    with pytest.raises(SourceAdmittedError, match="STALE"):
        dispatch(app, FULL_PREVIEW_ROUTE, full_request(app, run_id, tmp_path))


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
def test_real_short_source_preview_replace_new_reference_and_split(tmp_path: Path) -> None:
    app, source, path = create(tmp_path, real=True)
    first_run = analyze(app)
    preview = dispatch(app, FULL_PREVIEW_ROUTE, full_request(app, first_run, tmp_path))
    assert isinstance(preview, SourceAdmittedFullEnvelope)
    assert preview.plan.source.frame_count == 60
    assert preview.plan.chapter_count == 3
    for selector, counts in (
        ({"mode": "exact_times", "times": ["00:00:01"]}, [30, 30]),
        ({"mode": "exact_frames", "frames": [20, 40]}, [20, 20, 20]),
    ):
        configured = full_request(app, first_run, tmp_path)
        configured["processing"]["settings"]["chapter_selector"] = selector
        selected = dispatch(app, FULL_PREVIEW_ROUTE, configured)
        assert isinstance(selected, SourceAdmittedFullEnvelope)
        assert [chapter.frame_count for chapter in selected.plan.chapters] == counts
    replacement = tmp_path / "candidate.mp4"
    media(replacement, frames=90, rate="25")
    before = source.read_bytes()
    status = app.inspect()
    dispatch(
        app,
        REPLACE_ROUTE,
        {
            "contract_version": "0.3.5",
            "project_session_id": status.project_session_id,
            "expected_storage_revision": status.storage_revision,
            "source_path": str(replacement),
            "reference_change_confirmed": True,
        },
    )
    assert source.read_bytes() == before
    second_run = analyze(app)
    request = full_request(app, second_run, tmp_path)
    preview = dispatch(app, FULL_PREVIEW_ROUTE, request)
    assert isinstance(preview, SourceAdmittedFullEnvelope)
    assert (preview.plan.source.frame_count, preview.plan.source.frame_rate) == (90, "25/1")
    dispatch(app, EXPAND_ROUTE, request)
    result = authoring_command(app, {"operation": "run_to", "node_id": "overlap.split"})
    assert app.wait_until_idle(timeout=120)
    _store, runtime = app._require_session()
    run = runtime.repository.get_run(result.active_run_id or "")
    assert run.state is RunState.COMPLETED, [(n.state, n.error) for n in run.node_runs]
    latest_split = runtime.repository.get_latest("overlap.split")
    assert latest_split is not None
    leaves = runtime.repository.get_result(latest_split.result_id).outputs
    assert len(leaves) == 3
    metadata = [OverlapMetadata.model_validate(a.media_info[NAMESPACE]) for a in leaves]
    assert sum(value.frame_count for value in metadata) == 90
    assert all(value.producer_version == "0.3.5" for value in metadata)
    assert all("Synthetic" not in a.path for a in leaves)
    reopened = ProjectStore.open(path).load()
    assert reopened.project.graph == app.inspect().snapshot.project.graph  # type: ignore[union-attr]
    artifacts = [
        runtime.repository.get_artifact(identity)
        for node in run.node_runs
        for identity in node.output_artifact_ids
    ]
    assert any(SOURCE_NAMESPACE in artifact.media_info for artifact in artifacts)
    request["expected_storage_revision"] = app.inspect().storage_revision
    with pytest.raises(SourceAdmittedError, match="ALREADY_EXPANDED"):
        dispatch(app, EXPAND_ROUTE, request)


@pytest.mark.skipif(not TOOLS, reason="requires ffmpeg/ffprobe")
@pytest.mark.parametrize("mr,registration_failure", [(False, False), (True, False), (False, True)])
def test_synthetic_manual_chain_to_final_preserves_three_chapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mr: bool, registration_failure: bool
) -> None:
    """合成替身仅证明媒体接线与帧守恒，不代表 Aion/Jasna 画质或相位验收。"""
    app, _source, _path = create(tmp_path, real=True)
    if not mr:
        # AAC 编码预滚不应再次被旧 SourceAligned 额外准入规则挡住。
        audio_source = tmp_path / "audio-reference.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=64x36:rate=30:duration=2",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                str(audio_source),
            ],
            check=True,
            capture_output=True,
        )
        status = app.inspect()
        dispatch(
            app,
            REPLACE_ROUTE,
            {
                "contract_version": "0.3.5",
                "project_session_id": status.project_session_id,
                "expected_storage_revision": status.storage_revision,
                "source_path": str(audio_source),
                "reference_change_confirmed": True,
            },
        )
    run_id = analyze(app)
    request = full_request(app, run_id, tmp_path)
    if mr:
        request["processing"]["mr"] = {
            "mode": "external",
            "model_name": "Synthetic",
            "declared_container": "mp4",
            "operator_frame_order_confirmed": True,
        }
    dispatch(app, EXPAND_ROUTE, request)
    started = authoring_command(app, {"operation": "run_all"})
    assert started.active_run_id is not None
    _store, runtime = app._require_session()
    register_result = runtime.repository.register_result

    def register_or_fail(result: Any, **kwargs: Any) -> Any:
        attempt = runtime.repository.get_node_run(result.node_run_id)
        if registration_failure and attempt.node_id == "overlap.final":
            assert len(result.outputs) == 1 and Path(result.outputs[0].path).is_file()
            raise RuntimeRepositoryError(
                "E_RUNTIME_STORAGE_UNAVAILABLE", "synthetic publication commit failure"
            )
        return register_result(result, **kwargs)

    monkeypatch.setattr(runtime.repository, "register_result", register_or_fail)
    for _iteration in range(12):
        assert app.wait_until_idle(timeout=120)
        run = runtime.repository.get_run(started.active_run_id)
        if run.state is RunState.COMPLETED:
            break
        if registration_failure and any(
            node.node_id == "overlap.final" and node.state is NodeRunState.FAILED
            for node in run.node_runs
        ):
            break
        assert not any(node.state is NodeRunState.FAILED for node in run.node_runs), [
            (n.node_id, n.error) for n in run.node_runs
        ]
        waiting = [node for node in run.node_runs if node.state is NodeRunState.WAITING_EXTERNAL]
        assert waiting
        attempt = waiting[0]
        handoff = attempt.external_handoff
        assert handoff is not None
        source_artifact = next(
            runtime.repository.get_artifact(i)
            for i in attempt.input_artifact_ids
            if runtime.repository.get_artifact(i).kind == "VideoFile"
        )
        target = Path(handoff.output_targets[0].path)
        target.parent.mkdir(parents=True, exist_ok=True)
        header = probe_header(Path(source_artifact.path), count_frames=True)
        count = header.video.frame_count
        assert count is not None
        rate = header.video.frame_rate
        is_fi = attempt.node_id.startswith("overlap.fi.")
        if is_fi:
            count, rate = 2 * count - 1, 2 * rate
        codec = (
            ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
            if target.suffix == ".mp4"
            else ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
        )
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                source_artifact.path,
                "-map",
                "0:v:0",
                "-vf",
                f"fps={rate.numerator}/{rate.denominator}",
                "-frames:v",
                str(count),
                "-an",
                "-map_metadata",
                "-1",
                *codec,
                "-color_range",
                "tv",
                "-colorspace",
                "bt709",
                "-color_trc",
                "bt709",
                "-color_primaries",
                "bt709",
                "-chroma_sample_location",
                "left",
                str(target),
            ],
            check=True,
            capture_output=True,
        )
        app.command(
            {
                "operation": "submit_external",
                "run_id": run.run_id,
                "node_run_id": attempt.node_run_id,
                "handoff_id": handoff.handoff_id,
            }
        )
    else:
        pytest.fail("合成链没有收敛到 Final")
    if registration_failure:
        failed = next(node for node in run.node_runs if node.node_id == "overlap.final")
        final_node = next(
            node for node in run.graph_snapshot.nodes if node.node_id == "overlap.final"
        )
        target = Path(str(final_node.parameters["target_path"]))
        assert target.is_file() and probe_header(target).video.codec == "hevc"
        assert failed.state is NodeRunState.FAILED and failed.output_artifact_ids == ()
        assert (
            failed.error is not None
            and "synthetic publication commit failure" in failed.error.message
        )
        assert str(target) in failed.error.message
        assert "登记未确认" in failed.error.message
        assert "不要盲目覆盖重跑" in failed.error.message
        assert runtime.repository.get_latest("overlap.final") is None
        assert runtime.repository.get_latest("output") is None
        service_error = app.inspect().error
        assert service_error is not None
        assert service_error.code == "E_RUNTIME_STORAGE_UNAVAILABLE"
        assert str(target) in service_error.message
        assert "登记未确认" in service_error.message
        assert all(
            node.state is NodeRunState.COMPLETED
            for node in run.node_runs
            if node.node_id not in {"overlap.final", "output"}
        )
        return
    final_result_id = runtime.repository.get_latest("overlap.final")
    assert final_result_id is not None
    final = runtime.repository.get_result(final_result_id.result_id).outputs[0]
    assert probe_header(Path(final.path), count_frames=True).video.frame_count == 120
    assert (
        PublishedMetadata.model_validate(final.media_info[BATCH_NAMESPACE]).producer_version
        == "0.3.5"
    )
    final_node = next(node for node in run.graph_snapshot.nodes if node.node_id == "overlap.final")
    assert final_node.type_id == FINAL_PUBLISH_TYPE_ID
    assert final.path == final_node.parameters["target_path"]
    published_result_id = runtime.repository.get_latest("output")
    assert published_result_id is not None
    published = runtime.repository.get_result(published_result_id.result_id).outputs[0]
    assert published.path == final.path
    assert not any(
        list(Path(attempt.work_dir).rglob("*.mkv"))
        for attempt in run.node_runs
        if attempt.node_id in {"overlap.final", "output"}
    )
    assert NAMESPACE not in final.media_info
    # 新默认每章恰好一个真实增强 NodeRun，三章分别完成，而不是仅隐藏逐叶卡片。
    assert (
        sum(
            node.type_id.startswith("zniku.source-admitted.enhancement-batch.")
            for node in run.graph_snapshot.nodes
        )
        == 3
    )
    assert len(probe_header(Path(final.path)).audios) == (0 if mr else 1)
    assert Path(request["publication"]["output_root"]).exists()
    before_stat = Path(final.path).stat()
    reused = runtime.run_until_blocked(runtime.create_run().run_id)
    assert reused.state is RunState.COMPLETED
    by_node = {attempt.node_id: attempt for attempt in reused.node_runs}
    assert by_node["overlap.final"].reused_from_result_id == final_result_id.result_id
    assert by_node["output"].reused_from_result_id == published_result_id.result_id
    after_stat = Path(final.path).stat()
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (
        before_stat.st_size,
        before_stat.st_mtime_ns,
    )


@pytest.mark.parametrize("failure_step", ["abandon", "save"])
def test_replace_failure_never_reports_error_after_swapping_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_step: str
) -> None:
    app, source, _path = create(tmp_path)

    def failed_scan(*args: Any, **kwargs: Any) -> Any:
        raise Av27MediaError("E_SYNTHETIC_SOURCE", "synthetic failed analysis")

    monkeypatch.setattr(admitted_adapters, "admit_source", failed_scan)
    started = authoring_command(app, {"operation": "run_all"})
    assert app.wait_until_idle(timeout=10)
    assert started.active_run_id is not None
    store, runtime = app._require_session()
    before = store.load_authoring()
    replacement = tmp_path / "candidate.mp4"
    replacement.write_bytes(b"synthetic candidate")
    status = app.inspect()

    def broken(*args: Any, **kwargs: Any) -> Any:
        if failure_step == "abandon":
            raise RuntimeRepositoryError("E_SYNTHETIC_ABANDON", "injected")
        raise ProjectStoreError("E_SYNTHETIC_SAVE", "injected")

    monkeypatch.setattr(
        runtime if failure_step == "abandon" else store,
        "abandon_run" if failure_step == "abandon" else "save",
        broken,
    )
    with pytest.raises(SourceAdmittedError) as caught:
        dispatch(
            app,
            REPLACE_ROUTE,
            {
                "contract_version": "0.3.5",
                "project_session_id": status.project_session_id,
                "expected_storage_revision": status.storage_revision,
                "source_path": str(replacement),
                "reference_change_confirmed": True,
            },
        )
    assert store.load_authoring() == before
    assert source.read_bytes() == b"synthetic-source"
    assert replacement.read_bytes() == b"synthetic candidate"
    current_run = runtime.repository.get_run(started.active_run_id)
    if failure_step == "save":
        assert caught.value.envelope.error.code == "E_SOURCE_ADMITTED_SAVE_AFTER_ABANDON"
        assert current_run.state is RunState.FAILED
    else:
        assert current_run.state is RunState.RUNNING
