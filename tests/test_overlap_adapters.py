"""通过真实注册表、普通 Graph 与 Submit 验证新媒体链，不代表外部 AI 验收。

只生成短合成素材；缺工具明确跳过媒体专项，工具独立运行时缺工具直接失败。
非法参数必须在读取媒体或创建任何输出前失败关闭。
"""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest

from zniku.chapter_overlap import adapters
from zniku.chapter_overlap.definitions import built_in_overlap_definitions, overlap_python_adapters
from zniku.graph import NodeInstance, PythonExecutorSpec
from zniku.runtime import PythonAdapterContext

_smoke = importlib.import_module("tools.run_overlap_smoke")
TOOLS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.parametrize(
    "name", ("atomic_split", "merge_video", "fi_context", "fi_crop", "program_encode", "final_mux")
)
def test_automatic_invalid_binding_has_no_media_side_effects(tmp_path: Path, name: str) -> None:
    definition = next(
        item
        for item in built_in_overlap_definitions()
        if isinstance(item.executor, PythonExecutorSpec)
        and item.executor.adapter.endswith(f":{name}")
    )
    context = PythonAdapterContext(
        node_run_id="synthetic-invalid",
        attempt=1,
        definition=definition,
        node=NodeInstance(
            node_id="invalid",
            type_id=definition.type_id,
            definition_version=definition.version,
            parameters={},
        ),
        work_dir=tmp_path,
        inputs=(),
        outputs=(),
        stdout_log_path=tmp_path / "stdout.log",
        stderr_log_path=tmp_path / "stderr.log",
    )
    with pytest.raises(RuntimeError, match="E_OVERLAP_PARAMETERS"):
        getattr(adapters, name)(context)
    assert not any(tmp_path.iterdir())


def test_all_six_registered_automatic_executors_are_real() -> None:
    registered = overlap_python_adapters()
    assert len(registered) == 6
    for definition in built_in_overlap_definitions():
        if isinstance(definition.executor, PythonExecutorSpec):
            assert callable(registered[definition.executor.adapter])


def test_smoke_refuses_existing_output_without_mutation(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError, match="E_OVERLAP_SMOKE_EXISTS"):
        _smoke.run_smoke(tmp_path)
    assert not any(tmp_path.iterdir())


@pytest.mark.skipif(not TOOLS, reason="完整合成 Graph smoke 需要 FFmpeg/FFprobe")
def test_real_graph_submit_reuse_and_stale_with_short_non1080_source(tmp_path: Path) -> None:
    """640x360源经标准1080p Split，三章raw保留，35帧责任序列只补一次全局尾帧。"""
    report = _smoke.run_smoke(tmp_path / "synthetic-graph")
    assert report["evidence_kind"] == "synthetic_Graph_Runtime_Submit"
    assert report["external_AI_verified"] is False
    assert report["state"] == "completed"
    assert report["source_frames"] == 18
    assert report["source_rate"] == "30000/1001"
    assert report["chapter_frames"] == [1, 2, 15]
    assert report["cropped_frames"] == [2, 4, 29]
    assert report["program_frames"] == report["final_frames"] == 36
    assert len(report["manual_submissions"]) == 6
    assert report["raw_and_source_retained"]
    assert report["config_stale_nodes"] == ["output", "overlap.final", "overlap.program"]
    assert Path(report["project_path"]).is_file()
    assert all(Path(path).is_file() for path in report["raw_paths"])
