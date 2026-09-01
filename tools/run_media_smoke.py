"""运行 ZNIKU 0.2.0 的短合成媒体端到端 smoke。

本工具在一次临时目录中生成 12 帧 FFV1 视频，随后通过正式 ``RuntimeService`` 执行两条自由 DAG
分支：Source→Transform→Output，以及 Source→Split→独立 Transform→Merge→Output。它只验证真实
FFmpeg/FFprobe、帧区间、发布 Artifact 与 completed 复用，不读取用户媒体、不保留运行目录，也不建立
Evidence、receipt、digest 或固定产品 workflow。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from create_media_smoke_project import build_graph

from zniku.media import (
    built_in_media_definitions,
    exact_video_frame_count,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.project import Project, ProjectStore
from zniku.runtime import FrameRange, NodeRun, NodeRunState, RunState, RuntimeService

_FRAME_COUNT = 12
_SPLIT_FRAME = 5


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"E_SMOKE_TOOL_UNAVAILABLE: 找不到 {name!r}")
    return executable


def _generate_source(path: Path) -> None:
    """生成极短确定性视频；命令参数闭合且不接受用户 shell 字符串。"""

    completed = subprocess.run(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=12",
            "-frames:v",
            str(_FRAME_COUNT),
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-f",
            "matroska",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"E_SMOKE_SOURCE_FAILED: FFmpeg 退出码 {completed.returncode}: {detail}")


def _latest_by_node(node_runs: tuple[NodeRun, ...]) -> dict[str, NodeRun]:
    latest: dict[str, NodeRun] = {}
    for node_run in node_runs:
        current = latest.get(node_run.node_id)
        if current is None or node_run.attempt > current.attempt:
            latest[node_run.node_id] = node_run
    return latest


def run_smoke(root: Path) -> dict[str, object]:
    """执行短媒体 DAG 两次；第二次必须完整复用第一次的已验收结果。"""

    _tool("ffprobe")
    source = root / "source.mkv"
    output_directory = root / "published"
    output_directory.mkdir()
    _generate_source(source)
    source_frames = exact_video_frame_count(source)
    if source_frames != _FRAME_COUNT:
        raise RuntimeError(f"E_SMOKE_SOURCE_FRAME_COUNT: 期望 {_FRAME_COUNT}，实际 {source_frames}")

    definitions = built_in_media_definitions()
    graph = build_graph(
        source,
        output_directory,
        input_frames=source_frames,
        split_frame=_SPLIT_FRAME,
        overwrite=False,
    )
    store = ProjectStore.create(
        root / "smoke.zniku",
        Project(project_id="smoke.ci", name="ZNIKU CI Media Smoke", graph=graph),
        definitions,
    )
    service = RuntimeService(
        store,
        root / "attempts",
        python_adapters=media_python_adapters(),
        validators=media_validators(),
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )

    first = service.run_until_blocked(service.create_run().run_id)
    first_latest = _latest_by_node(first.node_runs)
    if first.state is not RunState.COMPLETED or any(
        item.state is not NodeRunState.COMPLETED for item in first_latest.values()
    ):
        raise RuntimeError("E_SMOKE_FIRST_RUN: 首次 Run 未全部 completed")

    split_run = first_latest["split"]
    split_artifacts = tuple(
        service.repository.get_artifact(artifact_id)
        for artifact_id in split_run.output_artifact_ids
    )
    expected_ranges = (
        FrameRange(start_frame=0, end_frame=_SPLIT_FRAME),
        FrameRange(start_frame=_SPLIT_FRAME, end_frame=_FRAME_COUNT),
    )
    if tuple(item.frame_range for item in split_artifacts) != expected_ranges:
        raise RuntimeError("E_SMOKE_SPLIT_RANGE: Split Artifact frame_range 不正确")

    output_paths = (
        output_directory / "phase4-simple.mkv",
        output_directory / "phase4-split-merge.mkv",
    )
    output_counts = tuple(exact_video_frame_count(path) for path in output_paths)
    if output_counts != (_FRAME_COUNT, _FRAME_COUNT):
        raise RuntimeError(f"E_SMOKE_OUTPUT_FRAMES: 发布帧数不正确：{output_counts}")

    second = service.run_until_blocked(service.create_run().run_id)
    second_latest = _latest_by_node(second.node_runs)
    if second.state is not RunState.COMPLETED or any(
        item.state is not NodeRunState.COMPLETED for item in second_latest.values()
    ):
        raise RuntimeError("E_SMOKE_SECOND_RUN: 复用 Run 未全部 completed")
    reused_nodes = tuple(
        node_id
        for node_id, node_run in sorted(second_latest.items())
        if node_run.reused_from_result_id is not None
    )
    expected_nodes = tuple(sorted(node.node_id for node in graph.nodes))
    if reused_nodes != expected_nodes:
        raise RuntimeError(
            f"E_SMOKE_REUSE: 第二次 Run 未完整复用；期望 {expected_nodes}，实际 {reused_nodes}"
        )

    return {
        "schema_version": 1,
        "source_frame_count": source_frames,
        "split_ranges": [[value.start_frame, value.end_frame] for value in expected_ranges],
        "output_frame_counts": list(output_counts),
        "completed_nodes": list(expected_nodes),
        "reused_nodes": list(reused_nodes),
    }


def main() -> int:
    """在自动清理的临时目录执行 smoke，并打印稳定 JSON 摘要。"""

    with tempfile.TemporaryDirectory(prefix="zniku-media-smoke-") as temporary:
        summary = run_smoke(Path(temporary))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
