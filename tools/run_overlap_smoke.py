"""保留短合成媒体，通过普通 Graph、Project Service / Runtime 与显式 Submit 闭环。

不调用外部 AI，不注入 Run 状态或伪造 producer metadata。Aion / v1.0 只是待真实验收
声明；合成 FI 灰阶形状不能证明真实模型、相位或画质。源、raw 与失败文件全部保留。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any

from zniku.avenhance_v27 import av27_python_adapters, av27_validators, built_in_av27_definitions
from zniku.avenhance_v27.probe import probe_header
from zniku.chapter_overlap.definitions import (
    built_in_overlap_definitions,
    overlap_python_adapters,
    overlap_validators,
)
from zniku.chapter_overlap.node_contracts import OVERLAP_NAMESPACE, OverlapMetadata
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.project import ProjectStore
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.models import RunDetailEnvelope
from zniku.project_service.overlap_application import full_overlap
from zniku.runtime import Artifact, NodeRun, NodeRunState, RunState


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


def _application(work_root: Path) -> ProjectServiceApplication:
    return ProjectServiceApplication(
        work_root=work_root,
        definition_catalog=(
            *built_in_media_definitions(),
            *built_in_av27_definitions(),
            *built_in_overlap_definitions(),
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
    if payload["operation"] == "run_all":
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
    metadata = _metadata(source)
    rate = Fraction(metadata.frame_rate)
    if waiting.node_id.startswith("overlap.enhance."):
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


def run_smoke(output: Path, *, frame_rate: str = "30000/1001") -> dict[str, Any]:
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

    project_path = output / "overlap.zniku"
    app = _application(output / "attempts")
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
    state = app.inspect()
    full_overlap(
        app,
        {
            "contract_version": "0.3.2",
            "project_session_id": state.project_session_id,
            "expected_storage_revision": state.storage_revision,
            "preparation_run_id": preparation.run.run_id,
            "processing": {
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
    for _ in range(6):
        waiting = next(
            (n for n in _latest(production).values() if n.state is NodeRunState.WAITING_EXTERNAL),
            None,
        )
        if waiting is None:
            raise AssertionError("E_OVERLAP_SMOKE_WAITING: 外部阶段数量或依赖不符")
        production = _submit(app, production, waiting, ffmpeg)
        submitted.append(waiting.node_id)
    assert production.run.state is RunState.COMPLETED
    final = _artifact(production, "overlap.final", "media")
    header = probe_header(final.path, count_frames=True)
    assert header.video.frame_count == 36 and len(header.audios) == 1
    raw_paths = [_artifact(production, f"overlap.fi.{label}").path for label in ("A", "B", "C")]
    assert all(Path(path).is_file() for path in raw_paths) and source.is_file()
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
    report: dict[str, Any] = {
        "evidence_kind": "synthetic_Graph_Runtime_Submit",
        "external_AI_verified": False,
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
        "final_path": final.path,
        "source_path": str(source),
        "raw_and_source_retained": True,
        "project_path": str(project_path),
        "production_run_id": run_id,
        "reused_nodes": len(_latest(reused)),
        "config_stale_nodes": stale,
        "seconds": perf_counter() - started,
    }
    with (output / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frame-rate", choices=("30/1", "30000/1001"), default="30000/1001")
    args = parser.parse_args()
    print(
        json.dumps(run_smoke(args.output, frame_rate=args.frame_rate), ensure_ascii=False, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
