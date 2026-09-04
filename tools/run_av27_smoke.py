"""通过正式 Project Service 执行 v2.7 的短合成媒体闭环。

只生成 1080p 合成 Source 与 FFmpeg 外部输出 fixture，不调用真实 Jasna/Topaz，也不伪造
Run、Artifact 或模型证明。两段模板创建、readiness、Submit、自动继续、双章补尾、原音轨和
completed reuse 均使用产品 API。默认只使用自动清理临时目录；显式 ``--keep-root`` 必须指向
不存在的独立目录，可保留完成工程或 Enhancement waiting 工程供生产浏览器验收。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from time import monotonic
from typing import Literal
from unittest.mock import patch

import zniku.avenhance_v27.adapters as av27_adapters
from zniku.avenhance_v27 import (
    av27_python_adapters,
    av27_validators,
    built_in_av27_definitions,
)
from zniku.avenhance_v27.probe import (
    canonical_fraction,
    metadata_frame_count,
    metadata_rate,
    probe_header,
)
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.models import RunDetailEnvelope
from zniku.runtime import Artifact, NodeRun, NodeRunState, PythonAdapterContext, Run, RunState

_SOURCE_FRAMES = 24
_CHAPTER_BOUNDARY = 10
_SOURCE_RATE = Fraction(30000, 1001)
_TOOL_TIMEOUT_SECONDS = 120
_RUN_TIMEOUT_SECONDS = 300


def require_tools() -> str:
    """检查必要 CPU encoder；缺工具是 smoke 失败，不能冒充通过。"""

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        raise RuntimeError("E_AV27_SMOKE_TOOL: 需要 PATH 中的 FFmpeg 与 FFprobe")
    encoders = _run_tool([ffmpeg, "-hide_banner", "-encoders"])
    if any(name not in encoders for name in ("ffv1", "prores_ks", "libx265")):
        raise RuntimeError("E_AV27_SMOKE_ENCODER: 需要 ffv1/prores_ks/libx265")
    return ffmpeg


def _run_tool(argv: Sequence[str]) -> str:
    completed = subprocess.run(
        list(argv),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        check=False,
        timeout=_TOOL_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"E_AV27_SMOKE_TOOL_FAILED: {completed.returncode}: {detail}")
    return completed.stdout.decode("utf-8", errors="replace")


def _base_argv(ffmpeg: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]


def _signal_argv() -> list[str]:
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


def _generate_source(ffmpeg: str, target: Path) -> None:
    """生成短 1080p Source；两条音轨的 codec header 与 metadata 可区分排序。"""

    duration = f"{float(_SOURCE_FRAMES / _SOURCE_RATE):.12f}"
    _run_tool(
        [
            *_base_argv(ffmpeg),
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x305080:size=1920x1080:rate={canonical_fraction(_SOURCE_RATE)}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:sample_rate=48000:duration={duration}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
            "-frames:v",
            str(_SOURCE_FRAMES),
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p10le",
            *_signal_argv(),
            "-c:a",
            "pcm_s16le",
            "-metadata:s:a:0",
            "language=jpn",
            "-metadata:s:a:0",
            "title=Original Primary",
            "-disposition:a:0",
            "default",
            "-metadata:s:a:1",
            "language=eng",
            "-metadata:s:a:1",
            "title=Original Alternate",
            "-disposition:a:1",
            "0",
            "-map_chapters",
            "-1",
            "-f",
            "matroska",
            str(target),
        ]
    )


def _application(work_root: Path) -> ProjectServiceApplication:
    """与本地 host 使用相同的正式目录、adapter、validator 与 quick probe。"""

    return ProjectServiceApplication(
        work_root=work_root,
        definition_catalog=(*built_in_media_definitions(), *built_in_av27_definitions()),
        python_adapters={**media_python_adapters(), **av27_python_adapters()},
        validators={**media_validators(), **av27_validators()},
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )


def _latest(run: Run) -> dict[str, NodeRun]:
    latest: dict[str, NodeRun] = {}
    for item in run.node_runs:
        if item.node_id not in latest or item.attempt > latest[item.node_id].attempt:
            latest[item.node_id] = item
    return latest


def _command(application: ProjectServiceApplication, payload: object) -> RunDetailEnvelope:
    started = application.command(payload)
    if started.active_run_id is None:
        raise RuntimeError("E_AV27_SMOKE_RUN_ID: mutation 未返回 Run identity")
    deadline = monotonic() + _RUN_TIMEOUT_SECONDS
    while not application.wait_until_idle(timeout=1):
        if monotonic() > deadline:
            raise RuntimeError("E_AV27_SMOKE_TIMEOUT: automatic Run 未在期限内结束")
    status = application.inspect()
    if status.error is not None:
        raise RuntimeError(f"E_AV27_SMOKE_BACKGROUND: {status.error}")
    return application.inspect_run_detail(started.active_run_id)


def _require_no_failed(detail: RunDetailEnvelope) -> None:
    failures = {
        node_id: str(item.error)
        for node_id, item in _latest(detail.run).items()
        if item.state is NodeRunState.FAILED
    }
    if failures:
        raise RuntimeError(f"E_AV27_SMOKE_NODE_FAILED: {failures}")


def _artifact(detail: RunDetailEnvelope, node_id: str, port_id: str) -> Artifact:
    ids = _latest(detail.run)[node_id].output_artifact_ids
    return next(
        item
        for item in detail.artifacts
        if item.artifact_id in ids and item.producer_port_id == port_id
    )


def _video_input(detail: RunDetailEnvelope, waiting: NodeRun) -> Artifact:
    return next(
        item
        for item in detail.artifacts
        if item.artifact_id in waiting.input_artifact_ids and item.kind == "VideoFile"
    )


def _make_external_fixture(
    ffmpeg: str, source: Artifact, target: Path, *, stage: str, missing_sar: bool = False
) -> None:
    """只产生可验收文件，不触碰 Runtime；FI fixture 是重复采样，不是真实 AI 插帧。"""

    frames = metadata_frame_count(source.media_info)
    rate = metadata_rate(source.media_info)
    if stage == "mr":
        duration = f"{float(frames / rate):.12f}"
        # MR 带一条明显不同的合成音轨，Final 仍必须取回 Source 的两条原音轨。
        _run_tool(
            [
                *_base_argv(ffmpeg),
                "-i",
                source.path,
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=1760:sample_rate=48000:duration={duration}",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "pcm_s16le",
                "-metadata:s:a:0",
                "title=External MR Not Audio Authority",
                "-map_chapters",
                "-1",
                "-f",
                "matroska",
                str(target),
            ]
        )
        return
    output_frames = frames if stage == "enhance" else 2 * frames - 1
    output_rate = rate if stage == "enhance" else rate * 2
    # 外部工具可能不写 SAR；混合缺失与显式 square 输入，防止 Program 只靠继承标签通过。
    sar = "0/1" if missing_sar else "1/1"
    video_filter = (
        f"fps={canonical_fraction(output_rate)},trim=end_frame={output_frames},"
        f"settb=expr={output_rate.denominator}/{output_rate.numerator},setpts=N,"
        f"format=yuv422p10le,setsar={sar}"
    )
    _run_tool(
        [
            *_base_argv(ffmpeg),
            "-filter_threads",
            "1",
            "-i",
            source.path,
            "-map",
            "0:v:0",
            "-vf",
            video_filter,
            "-frames:v",
            str(output_frames),
            "-fps_mode",
            "passthrough",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv422p10le",
            *_signal_argv(),
            "-video_track_timescale",
            str(output_rate.numerator),
            "-f",
            "mov",
            str(target),
        ]
    )


def _submit_fixture(
    application: ProjectServiceApplication, detail: RunDetailEnvelope, node_id: str, ffmpeg: str
) -> RunDetailEnvelope:
    """readiness 前后比对 authority；只有显式 Submit 才登记并自动继续当前 Run。"""

    waiting = _latest(detail.run)[node_id]
    handoff = waiting.external_handoff
    if waiting.state is not NodeRunState.WAITING_EXTERNAL or handoff is None:
        raise RuntimeError(f"E_AV27_SMOKE_HANDOFF: {node_id} 未 waiting")
    if waiting.progress is not None or len(handoff.output_targets) != 1:
        raise RuntimeError("E_AV27_SMOKE_HANDOFF_SHAPE: manual 不得伪造进度或多输出")
    before = application.inspect_run_detail(detail.run.run_id)
    missing = application.inspect_external_readiness(
        run_id=detail.run.run_id, node_run_id=waiting.node_run_id, probe=True
    )
    assert [target.state for target in missing.targets] == ["missing"]
    assert not missing.ready_for_submit
    assert application.inspect_run_detail(detail.run.run_id) == before
    target = Path(handoff.output_targets[0].path)
    assert target.resolve().is_relative_to(Path(waiting.work_dir).resolve())
    stage = node_id.split(".", 1)[0]
    assert stage in {"mr", "enhance", "fi"}
    _make_external_fixture(
        ffmpeg, _video_input(detail, waiting), target, stage=stage, missing_sar=node_id == "fi.A"
    )
    if stage == "fi":
        expected_sar = None if node_id == "fi.A" else "1:1"
        assert probe_header(target).video.sample_aspect_ratio == expected_sar
    ready = application.inspect_external_readiness(
        run_id=detail.run.run_id, node_run_id=waiting.node_run_id, probe=True
    )
    if not ready.ready_for_submit:
        raise RuntimeError(f"E_AV27_SMOKE_READINESS: {ready}")
    assert application.inspect_run_detail(detail.run.run_id) == before
    result = _command(
        application,
        {
            "operation": "submit_external",
            "run_id": detail.run.run_id,
            "node_run_id": waiting.node_run_id,
            "handoff_id": handoff.handoff_id,
        },
    )
    assert result.run.run_id == detail.run.run_id
    assert _latest(result.run)[node_id].state is NodeRunState.COMPLETED
    _require_no_failed(result)
    return result


def _assert_config_stale(application: ProjectServiceApplication, completed: Run) -> list[str]:
    """用独立 SQLite 副本的正式 save 检查 stale，不污染供浏览器检查的成功工程。"""

    status = application.inspect()
    snapshot = status.snapshot
    assert snapshot is not None
    assert status.project_path is not None
    project_path = Path(status.project_path)
    copied_project = project_path.with_name("config-stale-check.zniku")
    shutil.copyfile(project_path, copied_project)
    check_application = _application(application.work_root)
    check_application.command({"operation": "open_project", "path": str(copied_project)})
    project_data = snapshot.project.model_dump(mode="json")
    for node in project_data["graph"]["nodes"]:
        if node["node_id"] == "program":
            node["parameters"]["encoder"] = "gpu"
    check_application.command({"operation": "save_project", "project": project_data})
    stale = sorted(
        item.node_id for item in check_application.inspect().latest_results if item.stale
    )
    assert stale == ["final", "output", "program"]
    assert check_application.inspect_run_detail(completed.run_id).run == completed
    assert application.inspect().snapshot == snapshot
    assert all(not item.stale for item in application.inspect().latest_results)
    return stale


def run_smoke(
    root: Path, *, stop_at: Literal["completed", "enhancement"] = "completed"
) -> dict[str, object]:
    """在调用方新建的空目录执行一次烟测；所有 retained 路径只指向合成工程。"""

    root = root.resolve(strict=True)
    if not root.is_dir() or any(root.iterdir()):
        raise RuntimeError("E_AV27_SMOKE_ROOT: root 必须是已存在的空目录")
    started_at = monotonic()
    ffmpeg = require_tools()
    source_path = root / "synthetic-source.mkv"
    _generate_source(ffmpeg, source_path)
    source_stat = source_path.stat()
    output_root = root / "published"
    (output_root / "Synthetic AV27 (2026)").mkdir(parents=True)
    application = _application(root / "attempts")
    project_path = root / "synthetic-av27.zniku"
    prepare = {
        "profile_version": "2.7.0",
        "project_path": str(project_path),
        "project_id": "smoke.av27",
        "project_name": "AV27 合成媒体 E2E",
        "source_mode": "program",
        "sources": [{"source_path": str(source_path), "source_ordinal": 0}],
        "mr": {"mode": "external", "model_name": "synthetic-mr-fixture", "model_version": "test-1"},
    }
    preview = application.preview_av_enhance_v27({"action": "prepare", "request": prepare})
    assert preview.profile.status == "preparation-compatible" and not project_path.exists()
    application.command({"operation": "create_av_enhance_v27", "request": prepare})
    preparation = _command(application, {"operation": "run_all"})
    _require_no_failed(preparation)
    assert set(_latest(preparation.run)) == {"source.program", "admission", "mr.A"}
    assert _latest(preparation.run)["admission"].state is NodeRunState.COMPLETED
    preparation = _submit_fixture(application, preparation, "mr.A", ffmpeg)
    assert preparation.run.state is RunState.COMPLETED
    source = _artifact(preparation, "source.program", "source_media")
    mr = _artifact(preparation, "mr.A", "video")
    assert metadata_frame_count(source.media_info) == _SOURCE_FRAMES
    assert metadata_rate(source.media_info) == _SOURCE_RATE
    assert len(probe_header(Path(mr.path)).audios) == 1

    expand = {
        "profile_version": "2.7.0",
        "preparation_run_id": preparation.run.run_id,
        "chapter_selector": {"mode": "exact_frames", "frames": [_CHAPTER_BOUNDARY]},
        "leaf_duration_minutes": 1,
        "enhancement": {"model_name": "synthetic-enhancement-fixture", "actual_scale_factor": 1},
        "frame_interpolation": {"model_name": "synthetic-fi-fixture"},
        "program_encode": {"encoder": "cpu"},
        "publication": {
            "output_root": str(output_root),
            "title": "Synthetic AV27",
            "year": "2026",
            "overwrite": False,
        },
    }
    expanded = application.preview_av_enhance_v27({"action": "expand", "request": expand})
    assert expanded.profile.status == "expanded-compatible"
    assert expanded.plan.effective_video_artifact_ids == (mr.artifact_id,)
    assert expanded.plan.chapter_count == expanded.plan.leaf_count == 2
    application.command({"operation": "expand_av_enhance_v27", "request": expand})

    # 只记录真实 producer 调用，原函数与进程、validator、登记路径都不替换。
    real_run_ffmpeg = av27_adapters._run_ffmpeg
    producers: Counter[str] = Counter()

    def record_producer(
        context: PythonAdapterContext,
        argv: Sequence[str],
        *,
        progress: av27_adapters._ProgressContract | None = None,
    ) -> int | None:
        producers[context.node.node_id] += 1
        return real_run_ffmpeg(context, argv, progress=progress)

    with patch.object(av27_adapters, "_run_ffmpeg", record_producer):
        production = _command(application, {"operation": "run_all"})
        _require_no_failed(production)
        for node_id in ("source.program", "admission", "mr.A"):
            assert _latest(production.run)[node_id].reused_from_result_id is not None
        assert {
            item.node_id
            for item in _latest(production.run).values()
            if item.state is NodeRunState.WAITING_EXTERNAL
        } == {"enhance.A.001", "enhance.B.001"}
        if stop_at == "enhancement":
            return {
                "evidence_kind": "synthetic_only",
                "state": "waiting_external",
                "project_path": str(project_path),
                "work_root": str(application.work_root),
                "preparation_run_id": preparation.run.run_id,
                "production_run_id": production.run.run_id,
                "waiting_nodes": ["enhance.A.001", "enhance.B.001"],
            }
        submitted: list[str] = []
        while production.run.state is not RunState.COMPLETED:
            waiting = sorted(
                item.node_id
                for item in _latest(production.run).values()
                if item.state is NodeRunState.WAITING_EXTERNAL
            )
            if not waiting:
                raise RuntimeError("E_AV27_SMOKE_BLOCKED: Run 无可提交的 external 节点")
            node_id = waiting[0]
            production = _submit_fixture(application, production, node_id, ffmpeg)
            submitted.append(node_id)
    assert set(submitted) == {"enhance.A.001", "enhance.B.001", "fi.A", "fi.B"}
    assert dict(producers) == {
        "split.atomic": 1,
        "merge.A": 1,
        "merge.B": 1,
        "program": 1,
        "final": 1,
    }
    fi_counts = [
        metadata_frame_count(_artifact(production, node_id, "video").media_info)
        for node_id in ("fi.A", "fi.B")
    ]
    assert fi_counts == [2 * _CHAPTER_BOUNDARY - 1, 2 * (_SOURCE_FRAMES - _CHAPTER_BOUNDARY) - 1]
    program = _artifact(production, "program", "video")
    program_header = probe_header(Path(program.path), count_frames=True)
    assert (
        metadata_frame_count(program.media_info)
        == program_header.video.frame_count
        == 2 * _SOURCE_FRAMES
    )
    assert program_header.video.frame_rate == 2 * _SOURCE_RATE
    assert program_header.video.sample_aspect_ratio == "1:1"
    assert program_header.video.codec == "hevc" and program_header.video.profile == "Main 10"
    assert (program_header.video.width, program_header.video.height) == (1920, 1080)
    assert (
        len(program_header.videos) == 1 and not program_header.audios and not program_header.others
    )
    final = _artifact(production, "final", "media")
    final_header = probe_header(Path(final.path))
    final_rate = metadata_rate(final.media_info)
    assert final_rate == 2 * _SOURCE_RATE
    # MKV DefaultDuration 是整数纳秒；只接受该容器量化，不把观测 rational 改写成 exact authority。
    final_period_error = max(
        abs(1 / observed - 1 / final_rate)
        for observed in (
            final_header.video.frame_rate,
            final_header.video.avg_frame_rate,
            final_header.video.r_frame_rate,
        )
    )
    assert final_period_error <= Fraction(1, 1_000_000_000)
    source_header = probe_header(source_path)
    assert len(final_header.audios) == 2
    assert [item.signature() for item in final_header.audios] == [
        item.signature() for item in source_header.audios
    ]
    assert final_header.chapter_count == 0 and not final_header.others
    assert len(final_header.videos) == 1
    assert expanded.plan.output_target_path is not None
    assert Path(expanded.plan.output_target_path).is_file()
    output = _artifact(production, "output", "published")
    assert output.path == expanded.plan.output_target_path
    assert source_path.stat().st_size == source_stat.st_size
    assert source_path.stat().st_mtime_ns == source_stat.st_mtime_ns
    for node_id in ("split.atomic", "merge.A", "merge.B", "program", "final"):
        item = _latest(production.run)[node_id]
        assert item.progress == 1.0
        logs = application.inspect_node_logs(production.run.run_id, item.node_run_id).log
        assert logs.stdout_available and "progress=end" in logs.stdout

    reuse = _command(application, {"operation": "run_all"})
    assert reuse.run.state is RunState.COMPLETED
    assert all(item.reused_from_result_id is not None for item in _latest(reuse.run).values())
    reopened = _application(root / "attempts")
    reopened.command({"operation": "open_project", "path": str(project_path)})
    assert reopened.inspect_run_detail(production.run.run_id).run == production.run
    stale_nodes = _assert_config_stale(application, production.run)
    return {
        "evidence_kind": "synthetic_only",
        "state": "completed",
        "source_frame_count": _SOURCE_FRAMES,
        "source_fps": canonical_fraction(_SOURCE_RATE),
        "chapter_count": 2,
        "leaf_count": 2,
        "fi_frame_counts": fi_counts,
        "program_frame_count": 2 * _SOURCE_FRAMES,
        "program_fps": canonical_fraction(_SOURCE_RATE * 2),
        "fi_input_sars": [None, "1:1"],
        "program_sar": program_header.video.sample_aspect_ratio,
        "final_canonical_fps": canonical_fraction(final_rate),
        "final_observed_fps": canonical_fraction(final_header.video.frame_rate),
        "final_header_period_error_ns": str(final_period_error * 1_000_000_000),
        "producer_calls": dict(producers),
        "original_audio_track_count": len(final_header.audios),
        "submitted_nodes": submitted,
        "reused_nodes": sorted(_latest(reuse.run)),
        "config_stale_nodes": stale_nodes,
        "same_production_run": True,
        "project_path": str(project_path),
        "work_root": str(application.work_root),
        "preparation_run_id": preparation.run.run_id,
        "production_run_id": production.run.run_id,
        "elapsed_seconds": round(monotonic() - started_at, 2),
    }


def main() -> int:
    """默认清理全部合成产物；保留模式拒绝已有目录，不覆盖用户文件。"""

    parser = argparse.ArgumentParser(description="运行 AV27 Project Service 合成媒体 E2E")
    parser.add_argument("--keep-root", type=Path, help="保留合成工程的绝对新目录；必须不存在")
    parser.add_argument("--stop-at", choices=("completed", "enhancement"), default="completed")
    arguments = parser.parse_args()
    stop_at: Literal["completed", "enhancement"] = arguments.stop_at
    if arguments.keep_root is not None:
        root: Path = arguments.keep_root
        if not root.is_absolute() or root.exists():
            parser.error("--keep-root 必须是不存在的绝对目录")
        root = root.parent.resolve(strict=True) / root.name
        root.mkdir()
        summary = run_smoke(root, stop_at=stop_at)
    else:
        if stop_at != "completed":
            parser.error("--stop-at enhancement 必须同时指定 --keep-root")
        root = Path(tempfile.mkdtemp(prefix="zniku-av27-smoke-")).resolve(strict=True)
        try:
            summary = run_smoke(root)
        except BaseException:
            # 失败或中断可能仍有自动进程；保留自己创建的完整诊断目录，不与 producer 竞争删除。
            print(f"合成烟测未完成，诊断目录保留：{root}", file=sys.stderr, flush=True)
            raise
        # 只有成功且所有 worker idle 才清理本次 mkdtemp；从不接受用户路径作递归清理目标。
        shutil.rmtree(root)
        # 默认产物已经清理，不输出已失效的本机路径或 Run identity。
        for key in ("project_path", "work_root", "preparation_run_id", "production_run_id"):
            summary.pop(key, None)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
