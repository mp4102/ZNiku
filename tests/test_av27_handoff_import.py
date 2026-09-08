"""用极短真实 ProRes 验证 AV27 Enhancement 产物导入，不模拟节点验收结论。

只在 pytest 临时目录生成四帧 Source 与三/四帧候选。真实 Project Service、原生选择句柄和
ImportManager 经过固定 basename staging 调用正式 AV27 validator；复制不能替代 Submit，错误
帧数不能覆盖旧目标，源与候选不被移动或改写。不运行 Split、Program 编码或长片流程。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from authoring_helpers import authoring_command
from test_av27_media_integration import _geometry, _make_source_with_two_audio_tracks, _run_tool
from test_project_service_host_bridge import RecordingPlatform
from zniku.avenhance_v27 import (
    av27_python_adapters,
    av27_validators,
    enhancement_definition,
    source_program_definition,
)
from zniku.avenhance_v27.definitions import ENHANCEMENT_VALIDATOR
from zniku.avenhance_v27.probe import probe_header
from zniku.graph import Edge, Graph, NodeInstance
from zniku.media import runner_media_probe
from zniku.project import Project, ProjectStore
from zniku.project_service import (
    HostBridgeFailure,
    ProjectServiceApplication,
    create_project_service_host_bridge_session,
)
from zniku.project_service.handoff_import import HandoffImportManager
from zniku.runtime import NodeRunState, NodeValidatorContext, NodeValidatorResult


def _require_prores_tools() -> str:
    """只要求本用例实际使用的编码器；不因缺少无关的 x265/GPU 跳过验证。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        pytest.skip("AV27 导入集成验证需要 FFmpeg 与 FFprobe")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        check=False,
        timeout=15,
    )
    if result.returncode != 0 or any(
        encoder not in result.stdout for encoder in (b"ffv1", b"prores_ks")
    ):
        pytest.skip("AV27 导入集成验证需要 FFV1 与 ProRes encoder")
    return ffmpeg


def _make_enhancement(ffmpeg: str, path: Path, frames: int) -> None:
    """生成与四帧输入同尺寸、同帧率和色彩的 ProRes HQ；仅帧数可用于错投测试。"""
    _run_tool(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x36:rate=1,format=yuv422p10le,setsar=1/1",
            "-frames:v",
            str(frames),
            "-an",
            "-sn",
            "-dn",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "3",
            "-pix_fmt",
            "yuv422p10le",
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
            "-f",
            "mov",
            str(path),
        ]
    )


def test_av27_enhancement_import_validates_staging_and_never_submits(tmp_path: Path) -> None:
    """错 N 保留旧目标；正确 N 经真实 validator 导入后仍 waiting，且无新增 Artifact。"""
    ffmpeg = _require_prores_tools()
    source = tmp_path / "source.mkv"
    wrong = tmp_path / "operator-wrong-part.mov"
    correct = tmp_path / "operator-correct-part.mov"
    _make_source_with_two_audio_tracks(ffmpeg, source)
    _make_enhancement(ffmpeg, wrong, 3)
    _make_enhancement(ffmpeg, correct, 4)
    original_bytes = {path: path.read_bytes() for path in (source, wrong, correct)}
    source_definition = source_program_definition()
    enhancement = enhancement_definition()
    definitions = (source_definition, enhancement)
    project = Project(
        project_id="av27-import",
        name="合成 AV27 Enhancement 导入",
        graph=Graph(
            nodes=(
                NodeInstance(
                    node_id="source",
                    type_id=source_definition.type_id,
                    definition_version=source_definition.version,
                    parameters={"source_path": str(source), "source_ordinal": 0},
                ),
                NodeInstance(
                    node_id="enhance",
                    type_id=enhancement.type_id,
                    definition_version=enhancement.version,
                    parameters={
                        "model_name": "合成 Enhancement",
                        "actual_scale_factor": 1,
                        "expected_input_geometry": _geometry(),
                        "expected_output_geometry": _geometry(),
                        "expected_frames": 4,
                        "expected_fps": "1/1",
                        "chapter_id": "chapter-A",
                        "chapter_ordinal": 0,
                        "leaf_id": "leaf-A",
                        "leaf_ordinal": 0,
                    },
                ),
            ),
            edges=(
                Edge(
                    source_node_id="source",
                    source_port_id="video",
                    target_node_id="enhance",
                    target_port_id="video",
                ),
            ),
        ),
    )
    store = ProjectStore.create(tmp_path / "av27.zniku", project, definitions)
    validators = dict(av27_validators())
    real_validator = validators[ENHANCEMENT_VALIDATOR]
    checked_paths: list[Path] = []

    def record_real_validation(context: NodeValidatorContext) -> NodeValidatorResult:
        # 仅记录真实调用位置；不替换 FFprobe、媒体属性、判定结果或异常。
        checked_paths.append(context.outputs[0].path)
        return real_validator(context)

    validators[ENHANCEMENT_VALIDATOR] = record_real_validation
    app = ProjectServiceApplication(
        work_root=tmp_path / "attempts",
        definition_catalog=definitions,
        python_adapters=av27_python_adapters(),
        validators=validators,
        media_probe=runner_media_probe,
    )
    app.command({"operation": "open_project", "path": str(store.path)})
    status = authoring_command(app, {"operation": "run_all"})
    assert app.wait_until_idle(timeout=30)
    assert status.active_run_id is not None
    detail = app.inspect_run_detail(status.active_run_id)
    waiting = next(item for item in detail.run.node_runs if item.node_id == "enhance")
    assert waiting.state is NodeRunState.WAITING_EXTERNAL
    handoff = waiting.external_handoff
    assert handoff is not None
    target = Path(handoff.output_targets[0].path)
    assert target.name == "enhancement.mov"
    assert not checked_paths
    target.write_bytes(b"operator-owned previous target")
    platform = RecordingPlatform()
    session = create_project_service_host_bridge_session(
        app,
        studio_origin="http://127.0.0.1:4173",
        platform=platform,
    )
    manager = HandoffImportManager()
    before = app.inspect()

    def import_candidate(candidate: Path) -> None:
        platform.selections["open_file"] = (str(candidate),)
        action = session.issue_user_action({"capability": "open_file"})
        selected = session.invoke(
            {"capability": "open_file", "user_action_id": action.user_action_id, "arguments": {}}
        )
        preview = manager.preview(
            {
                "contract_version": "0.3.0",
                "selection_handle": selected.selections[0].selection_handle,
                "project_session_id": before.project_session_id,
                "run_id": waiting.run_id,
                "node_run_id": waiting.node_run_id,
                "handoff_id": handoff.handoff_id,
                "port_id": "video",
                "ordinal": None,
            },
            session=session,
            application=app,
        )
        assert preview.replace_existing and preview.target_path == str(target)
        assert preview.source_name == candidate.name
        manager.confirm(
            {"contract_version": "0.3.0", "import_id": preview.import_id, "overwrite": True},
            session=session,
            application=app,
        )

    with pytest.raises(HostBridgeFailure, match="E_AV27_ENHANCEMENT_FRAME_COUNT") as refused:
        import_candidate(wrong)
    assert refused.value.http_status == 422
    assert "预期 4 帧，实际 3 帧" in str(refused.value)
    assert target.read_bytes() == b"operator-owned previous target"
    assert app.inspect() == before
    assert app.inspect_run_detail(waiting.run_id) == detail
    import_candidate(correct)
    assert target.read_bytes() == original_bytes[correct]
    assert probe_header(target).video.frame_count == 4
    assert app.inspect() == before
    assert app.inspect_run_detail(waiting.run_id) == detail
    assert len(checked_paths) == 2
    assert all(path.name == "enhancement.mov" for path in checked_paths)
    assert all(path.parent.name.startswith(".handoff-import-") for path in checked_paths)
    assert all(path.is_relative_to(Path(waiting.work_dir)) for path in checked_paths)
    assert not list(Path(waiting.work_dir).glob(".handoff-import-*"))
    assert {path: path.read_bytes() for path in original_bytes} == original_bytes
    observed = app.inspect_external_readiness(
        run_id=waiting.run_id,
        node_run_id=waiting.node_run_id,
        probe=False,
    )
    assert observed.targets[0].state == "present" and not observed.ready_for_submit
    assert app.inspect_run_detail(waiting.run_id) == detail
