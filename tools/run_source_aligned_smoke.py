"""以原片规划、可选第0步和七次显式交付验证新版完整合成链。

不调用外部 AI，不注入 Run 状态或伪造 producer metadata。Aion / v1.0 只是待真实验收
声明；合成 FI 灰阶形状不能证明真实模型、相位或画质。源、raw 与失败文件全部保留。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any

from zniku.avenhance_v27 import av27_python_adapters, av27_validators, built_in_av27_definitions
from zniku.avenhance_v27.probe import probe_header
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.project import ProjectStore
from zniku.project.storage import new_project_storage
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.models import RunDetailEnvelope
from zniku.project_service.source_aligned_application import full_source_aligned
from zniku.project_service.storage import StorageMigrationManager
from zniku.runtime import Artifact, NodeRun, NodeRunState, RunState
from zniku.source_aligned.definitions import (
    built_in_overlap_definitions,
    external_definition,
    overlap_python_adapters,
    overlap_validators,
)
from zniku.source_aligned.node_contracts import OVERLAP_NAMESPACE, ExternalMetadata, OverlapMetadata


def _tool(ffmpeg: str, argv: list[str]) -> str:
    """只生成本工具固定预算内的合成 fixture，不执行用户命令或输入媒体。"""
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-n", "-loglevel", "error", *argv],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "E_OVERLAP_SMOKE_TOOL: " + result.stderr.decode(errors="replace")[-3000:]
        )
    return result.stdout.decode(errors="replace")


def _fingerprint(path: Path) -> tuple[int, int, str]:
    """测试 oracle 检查保留文件未被改写；hash 不是产品完成条件。"""
    facts = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return facts.st_size, facts.st_mtime_ns, digest


def _signal() -> list[str]:
    return [
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
    ]


def _metadata(artifact: Artifact) -> OverlapMetadata:
    # 保留已由实际 validator 登记的字段，只规范化冻结容器。
    def plain(value: Any) -> Any:
        if hasattr(value, "items"):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [plain(item) for item in value]
        return value

    return OverlapMetadata.model_validate(plain(artifact.media_info[OVERLAP_NAMESPACE]))


def _application(work_root: Path, *, readable_storage: bool = False) -> ProjectServiceApplication:
    return ProjectServiceApplication(
        work_root=work_root,
        project_data_default=readable_storage,
        definition_catalog=(
            *built_in_media_definitions(),
            *built_in_av27_definitions(),
            *built_in_overlap_definitions(),
            external_definition(),
        ),
        python_adapters={
            **media_python_adapters(),
            **av27_python_adapters(),
            **overlap_python_adapters(),
        },
        validators={**media_validators(), **av27_validators(), **overlap_validators()},
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )


def _latest(detail: RunDetailEnvelope) -> dict[str, NodeRun]:
    result: dict[str, NodeRun] = {}
    for item in detail.run.node_runs:
        if item.node_id not in result or result[item.node_id].attempt < item.attempt:
            result[item.node_id] = item
    return result


def _artifact(detail: RunDetailEnvelope, node: str, port: str = "video") -> Artifact:
    ids = _latest(detail)[node].output_artifact_ids
    return next(
        item
        for item in detail.artifacts
        if item.artifact_id in ids and item.producer_port_id == port
    )


def _command(app: ProjectServiceApplication, payload: dict[str, object]) -> RunDetailEnvelope:
    """调用正式产品入口，自动后台结束后检查真实 Run；不写入 completed。"""
    if payload["operation"] in {"run_all", "rerun_from_here"}:
        state = app.inspect()
        payload = {
            "project_session_id": state.project_session_id,
            "expected_storage_revision": state.storage_revision,
            **payload,
        }
    started = app.command(payload)
    if started.active_run_id is None:
        raise AssertionError("E_OVERLAP_SMOKE_RUN: 缺少真实 Run id")
    deadline = monotonic() + 300
    while not app.wait_until_idle(timeout=1):
        if monotonic() > deadline:
            raise TimeoutError("E_OVERLAP_SMOKE_TIMEOUT: 自动阶段超出短合成预算")
    status = app.inspect()
    if status.error is not None:
        raise RuntimeError(f"E_OVERLAP_SMOKE_BACKGROUND: {status.error}")
    detail = app.inspect_run_detail(started.active_run_id)
    failures = {
        key: value.error
        for key, value in _latest(detail).items()
        if value.state is NodeRunState.FAILED
    }
    if failures:
        raise RuntimeError(f"E_OVERLAP_SMOKE_FAILED: {failures}")
    return detail


def _submit(
    app: ProjectServiceApplication,
    detail: RunDetailEnvelope,
    waiting: NodeRun,
    ffmpeg: str,
) -> RunDetailEnvelope:
    """生成手工 fixture 后只读 readiness，再显式 Submit；其间状态不得改变。"""
    handoff = waiting.external_handoff
    assert handoff is not None and waiting.state is NodeRunState.WAITING_EXTERNAL
    before = app.inspect_run_detail(detail.run.run_id)
    missing = app.inspect_external_readiness(
        run_id=detail.run.run_id,
        node_run_id=waiting.node_run_id,
        probe=True,
    )
    assert not missing.ready_for_submit and app.inspect_run_detail(detail.run.run_id) == before
    target = Path(handoff.output_targets[0].path)
    assert target.resolve().is_relative_to(Path(waiting.work_dir).resolve())
    source = next(
        a
        for a in detail.artifacts
        if a.artifact_id in waiting.input_artifact_ids and a.kind == "VideoFile"
    )
    external = waiting.node_id == "source-aligned.mr"
    metadata = None if external else _metadata(source)
    if metadata is None:
        rate = Fraction(probe_header(source.path).video.frame_rate)
    else:
        rate = Fraction(metadata.frame_rate)
    if external:
        _tool(
            ffmpeg,
            [
                "-i",
                source.path,
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                *_signal(),
                "-video_track_timescale",
                str(rate.numerator),
                str(target),
            ],
        )
    elif waiting.node_id.startswith("overlap.enhance."):
        _tool(
            ffmpeg,
            [
                "-i",
                source.path,
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                "format=yuv422p10le,setsar=1/1",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-threads",
                "2",
                *_signal(),
                "-video_track_timescale",
                str(rate.numerator * 4),
                str(target),
            ],
        )
    elif waiting.node_id.startswith("overlap.fi."):
        assert metadata is not None
        binding = metadata.context
        assert binding is not None
        raw_rate = Fraction(2997, 50) if rate == Fraction(30000, 1001) else rate * 2
        _tool(
            ffmpeg,
            [
                "-f",
                "lavfi",
                "-i",
                f"nullsrc=size=1920x1080:rate={raw_rate}",
                "-vf",
                f"geq=lum='16+({binding.context_start_frame * 2}+N)*3':cb=128:cr=128,setsar=1/1",
                "-frames:v",
                str(binding.raw_fi_frame_count),
                "-an",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "3",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv422p10le",
                *_signal(),
                "-video_track_timescale",
                str(raw_rate.numerator * 4),
                "-movflags",
                "frag_keyframe+empty_moov",
                str(target),
            ],
        )
    else:
        raise AssertionError("E_OVERLAP_SMOKE_MANUAL: 未知外部阶段")
    ready = app.inspect_external_readiness(
        run_id=detail.run.run_id,
        node_run_id=waiting.node_run_id,
        probe=True,
    )
    if not ready.ready_for_submit:
        raise RuntimeError(f"E_OVERLAP_SMOKE_READINESS: {ready}")
    assert app.inspect_run_detail(detail.run.run_id) == before
    result = _command(
        app,
        {
            "operation": "submit_external",
            "run_id": detail.run.run_id,
            "node_run_id": waiting.node_run_id,
            "handoff_id": handoff.handoff_id,
        },
    )
    assert result.run.run_id == detail.run.run_id
    assert _latest(result)[waiting.node_id].state is NodeRunState.COMPLETED
    return result


def _retry_external(
    output: Path,
    project_path: Path,
    run_id: str,
    ffmpeg: str,
) -> dict[str, Any]:
    """以正式迁移隔离副本后重试 MR，成功工程及其目录不承接测试 attempt。"""
    copied = output / "mr-retry-check.zniku"
    assert not copied.exists()
    shutil.copyfile(project_path, copied)
    store = ProjectStore.open(copied)
    old_storage = store.load_storage()
    manager = StorageMigrationManager()
    revision = store.load_authoring().storage_revision
    ticket = manager.preview(
        store,
        legacy_root=output / "attempts",
        target=new_project_storage(
            copied,
            media_basename=None if old_storage is None else old_storage.media_basename,
        ),
        project_session_id="synthetic-retry-copy",
        expected_storage_revision=revision,
    )
    manager.confirm(
        store,
        ticket_id=ticket.ticket_id,
        project_session_id=ticket.project_session_id,
        expected_storage_revision=revision,
    )
    check = _application(output / "retry-fallback-unused")
    check.command({"operation": "open_project", "path": str(copied)})
    original = check.inspect_run_detail(run_id)
    old_mr = _artifact(original, "source-aligned.mr")
    old_mr_facts = _fingerprint(Path(old_mr.path))
    changed = _command(
        check,
        {
            "operation": "rerun_from_here",
            "run_id": run_id,
            "node_id": "source-aligned.mr",
        },
    )
    assert changed.run.run_id != run_id
    waiting = _latest(changed)["source-aligned.mr"]
    assert waiting.state is NodeRunState.WAITING_EXTERNAL
    assert waiting.node_run_id != _latest(original)["source-aligned.mr"].node_run_id
    assert waiting.work_dir != _latest(original)["source-aligned.mr"].work_dir
    assert waiting.reused_from_result_id is None
    assert waiting.external_handoff is not None
    assert not Path(waiting.external_handoff.output_targets[0].path).exists()
    assert all(
        _latest(changed)[node].reused_from_result_id is not None
        for node in ("source.program", "admission")
    )
    stale = sorted(item.node_id for item in check.inspect().latest_results if item.stale)
    expected_stale = sorted(
        node.node_id
        for node in original.run.graph_snapshot.nodes
        if node.node_id not in {"source.program", "admission"}
    )
    assert stale == expected_stale
    assert check.inspect_run_detail(run_id).run == original.run
    resumed = _submit(check, changed, waiting, ffmpeg)
    new_mr = _artifact(resumed, "source-aligned.mr")
    assert new_mr.artifact_id != old_mr.artifact_id
    new_leaf = _artifact(resumed, "overlap.split", "leaf-0001")
    assert _metadata(new_leaf).source.effective_video_artifact_id == new_mr.artifact_id
    assert _fingerprint(Path(old_mr.path)) == old_mr_facts
    assert check.inspect_run_detail(run_id).run == original.run
    # 仅观察新 MR→Split 的真实推进；后续增强保持待提交，不制造假成功，再显式结束测试分支。
    assert any(item.state is NodeRunState.WAITING_EXTERNAL for item in _latest(resumed).values())
    check.command({"operation": "abandon_run", "run_id": resumed.run.run_id})
    cancelled = check.inspect_run_detail(resumed.run.run_id).run
    assert cancelled.state is RunState.FAILED
    assert cancelled.error is not None and cancelled.error.reason == "cancelled"
    return {
        "project_path": str(copied),
        "data_root": ticket.target.data_root,
        "old_effective_artifact_id": old_mr.artifact_id,
        "new_effective_artifact_id": new_mr.artifact_id,
        "stale_after_retry": stale,
        "new_split_bound_to_new_effective": True,
        "old_run_and_raw_retained": True,
        "test_run_state": "failed",
        "test_run_end_reason": "cancelled",
    }


def run_smoke(
    output: Path,
    *,
    frame_rate: str = "30000/1001",
    readable_storage: bool = False,
    with_external: bool = True,
) -> dict[str, Any]:
    """十八帧三章含一帧短章，完整普通 Graph / SQLite / Submit / reuse 验证。"""
    if output.exists():
        raise FileExistsError("E_OVERLAP_SMOKE_EXISTS: 必须选择新的保留目录")
    if frame_rate not in {"30/1", "30000/1001"}:
        raise ValueError("E_OVERLAP_SMOKE_RATE: 只允许两种合成 exact FPS")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        raise RuntimeError("E_OVERLAP_SMOKE_TOOLS: 需要 FFmpeg/FFprobe")
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve(strict=True)
    started = perf_counter()
    rate, count = Fraction(frame_rate), 18
    source = output / "synthetic-source.mkv"
    duration = float(Fraction(count) / rate)
    _tool(
        ffmpeg,
        [
            "-f",
            "lavfi",
            "-i",
            f"nullsrc=size=640x360:rate={frame_rate}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration:.12f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            "geq=lum='16+N*6':cb=128:cr=128,setsar=1/1",
            "-frames:v",
            str(count),
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p10le",
            *_signal(),
            "-c:a",
            "pcm_s16le",
            "-metadata:s:a:0",
            "language=jpn",
            "-metadata:s:a:0",
            "title=Synthetic original",
            "-disposition:a:0",
            "default",
            "-f",
            "matroska",
            str(source),
        ],
    )
    original_source_facts = _fingerprint(source)

    project_path = output / "overlap.zniku"
    app = _application(output / "attempts", readable_storage=readable_storage)
    app.command(
        {
            "operation": "create_av_enhance_v27",
            "request": {
                "profile_version": "2.7.0",
                "project_path": str(project_path),
                "project_id": "smoke.overlap",
                "project_name": "重叠 FI 合成测试",
                "source_mode": "program",
                "sources": [{"source_path": str(source), "source_ordinal": 0}],
                "mr": {"mode": "off"},
            },
        }
    )
    preparation = _command(app, {"operation": "run_all"})
    assert preparation.run.state is RunState.COMPLETED
    original_video = _artifact(preparation, "source.program")
    assert {node.node_id for node in preparation.run.graph_snapshot.nodes} == {
        "source.program",
        "admission",
    }
    state = app.inspect()
    full_source_aligned(
        app,
        {
            "contract_version": "0.3.3",
            "project_session_id": state.project_session_id,
            "expected_storage_revision": state.storage_revision,
            "preparation_run_id": preparation.run.run_id,
            "processing": {
                "mr": {
                    "mode": "external",
                    "model_name": "synthetic-not-AI",
                    "declared_container": "mp4",
                    "operator_frame_order_confirmed": True,
                }
                if with_external
                else {"mode": "off"},
                "settings": {
                    "chapter_selector": {"mode": "exact_frames", "frames": [1, 3]},
                    "leaf_max_minutes": 5,
                },
                "enhancement": {"model_name": "synthetic-not-AI", "actual_scale_factor": 1},
                "program_encode": {"encoder": "cpu"},
            },
            "publication": {
                "output_root": str(output),
                "title": "Synthetic overlap",
                "year": "2026",
                "overwrite": False,
                "layout": "title_subdirectory",
            },
        },
        expand=True,
    )
    production = _command(app, {"operation": "run_all"})
    run_id = production.run.run_id
    submitted: list[str] = []
    for _ in range(7 if with_external else 6):
        waiting = next(
            (n for n in _latest(production).values() if n.state is NodeRunState.WAITING_EXTERNAL),
            None,
        )
        if waiting is None:
            raise AssertionError("E_OVERLAP_SMOKE_WAITING: 外部阶段数量或依赖不符")
        production = _submit(app, production, waiting, ffmpeg)
        submitted.append(waiting.node_id)
    assert production.run.state is RunState.COMPLETED
    actual_effective = (
        _artifact(production, "source-aligned.mr") if with_external else original_video
    )
    if with_external:
        restored = ExternalMetadata.model_validate(
            actual_effective.model_dump(mode="json")["media_info"][OVERLAP_NAMESPACE]
        )
        assert restored.source.original_video_artifact_id == original_video.artifact_id
        assert restored.declared_container == "mp4"
    for artifact in production.artifacts:
        if OVERLAP_NAMESPACE not in artifact.media_info:
            continue
        if artifact.artifact_id == actual_effective.artifact_id:
            continue
        binding = _metadata(artifact).source
        assert binding.original_video_artifact_id == original_video.artifact_id
        assert binding.effective_video_artifact_id == actual_effective.artifact_id
    final = _artifact(production, "overlap.final", "media")
    header = probe_header(final.path, count_frames=True)
    assert header.video.frame_count == 36 and len(header.audios) == 1
    raw_paths = [_artifact(production, f"overlap.fi.{label}").path for label in ("A", "B", "C")]
    assert all(Path(path).is_file() for path in raw_paths) and source.is_file()
    retained_paths = [source, *map(Path, raw_paths)]
    if with_external:
        retained_paths.append(Path(actual_effective.path))
    retained = {str(path): _fingerprint(path) for path in retained_paths}
    assert retained[str(source)] == original_source_facts
    cropped_counts = [
        _metadata(_artifact(production, f"overlap.crop.{label}")).frame_count
        for label in ("A", "B", "C")
    ]
    assert cropped_counts == [2, 4, 29]
    saved = ProjectStore.open(project_path).load()
    assert saved.project.graph == production.run.graph_snapshot
    reused = _command(app, {"operation": "run_all"})
    assert reused.run.state is RunState.COMPLETED
    assert all(item.reused_from_result_id is not None for item in _latest(reused).values())
    assert app.inspect_run_detail(run_id).run == production.run
    # 独立 SQLite 副本检查常规参数 stale，不污染供操作者打开的成功工程。
    copied = project_path.with_name("config-stale-check.zniku")
    shutil.copyfile(project_path, copied)
    check = _application(output / "attempts")
    check.command({"operation": "open_project", "path": str(copied)})
    project_data = saved.project.model_dump(mode="json")
    for node in project_data["graph"]["nodes"]:
        if node["node_id"] == "overlap.program":
            node["parameters"]["encoder"] = "gpu"
    current = check.inspect()
    check.command(
        {
            "operation": "save_project",
            "project": project_data,
            "project_session_id": current.project_session_id,
            "expected_storage_revision": current.storage_revision,
            "studio_state": current.studio_state,
        }
    )
    stale = sorted(item.node_id for item in check.inspect().latest_results if item.stale)
    assert stale == ["output", "overlap.final", "overlap.program"]
    assert check.inspect_run_detail(run_id).run == production.run
    assert app.inspect_run_detail(run_id).run == production.run
    retry = _retry_external(output, project_path, run_id, ffmpeg) if with_external else None
    assert app.inspect_run_detail(run_id).run == production.run
    assert app.inspect_run_detail(preparation.run.run_id).run == preparation.run
    assert {str(path): _fingerprint(path) for path in retained_paths} == retained
    report: dict[str, Any] = {
        "evidence_kind": "synthetic_Graph_Runtime_Submit",
        "external_AI_verified": False,
        "source_aligned": True,
        "metadata_namespace": OVERLAP_NAMESPACE,
        "original_video_artifact_id": original_video.artifact_id,
        "effective_video_artifact_id": actual_effective.artifact_id,
        "external_enabled": with_external,
        "GUI_verified": False,
        "state": "completed",
        "source_frames": 18,
        "source_rate": frame_rate,
        "chapter_count": 3,
        "leaf_count": 3,
        "chapter_frames": [1, 2, 15],
        "cropped_frames": cropped_counts,
        "program_frames": 36,
        "final_frames": header.video.frame_count,
        "manual_submissions": submitted,
        "raw_paths": raw_paths,
        "external_raw_path": actual_effective.path if with_external else None,
        "final_path": final.path,
        "source_path": str(source),
        "raw_and_source_retained": True,
        "retained_file_fingerprints": retained,
        "mr_retry": retry,
        "project_path": str(project_path),
        "production_run_id": run_id,
        "reused_nodes": len(_latest(reused)),
        "config_stale_nodes": stale,
        "seconds": perf_counter() - started,
    }
    if readable_storage:
        # 同一真实 Runtime 媒体链只切换物理定位；不能注入状态或把路径名视为结果证明。
        storage = ProjectStore.open(project_path).load_storage()
        assert storage is not None and storage.layout == "english"
        assert (
            storage.english_layout_state.nodes["overlap.fi.A"].relative_dir
            == "frame-interpolation/A"
        )
        report["storage_layout"] = storage.layout
        report["data_root"] = storage.data_root
    with (output / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frame-rate", choices=("30/1", "30000/1001"), default="30000/1001")
    parser.add_argument("--readable-storage", action="store_true")
    parser.add_argument("--without-external", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run_smoke(
                args.output,
                frame_rate=args.frame_rate,
                readable_storage=args.readable_storage,
                with_external=not args.without_external,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
