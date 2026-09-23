"""为章级批量交付浏览器门禁提供两份短合成媒体与正式多输出人工节点。

仅原生 picker/系统打开使用测试替身；Graph、SQLite、Runtime、媒体检查与 HTTP 都走正式
实现。这里测试通用多输出交接 UI，不伪造 source-admitted producer 或真实 AI 能力。
业务章级定义的完整媒体链另由 test_source_admitted_service 验证。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from zniku.avenhance_v27.probe import probe_header
from zniku.desktop.server import DesktopServer
from zniku.graph import (
    Cardinality,
    Edge,
    ExecutionMode,
    ExecutorOutputPathSpec,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    UiPosition,
    ValidatorSpec,
)
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.project import NodeViewState, Project, ProjectStore, StudioState
from zniku.project_service import ProjectServiceApplication
from zniku.project_service.host_bridge import (
    HOST_CAPABILITIES,
    HostCapability,
    HostDialogArguments,
    HostLaunchCommand,
)
from zniku.runtime import NodeRunState, NodeValidatorContext, NodeValidatorResult

TYPE_ID = "zniku.synthetic.chapter_batch"
VALIDATOR = "zniku.synthetic:validate_chapter_batch"
NAMES = (
    "Synthetic.A.leaf-0001.enhancement.mov",
    "Synthetic.A.leaf-0002.enhancement.mov",
)
COUNTS = (12, 15)


def generate(path: Path, count: int) -> None:
    """仅生成固定尺寸/时长的测试素材；从不接受用户媒体输入或覆盖既有文件。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("合成批量交付测试需要 ffmpeg")
    codec = (
        ["-c:v", "ffv1"]
        if path.suffix == ".mkv"
        else ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
    )
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=12",
            "-frames:v",
            str(count),
            "-an",
            *codec,
            str(path),
        ],
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
    )


def validate_batch(context: NodeValidatorContext) -> NodeValidatorResult:
    """逐个核对真实小媒体，错误输出不能令整个多输出节点完成。"""
    for index, output in enumerate(context.outputs):
        video = probe_header(output.path, count_frames=True).video
        if (
            output.port_id != f"leaf-{index + 1:04d}"
            or video.frame_count != COUNTS[index]
            or video.frame_rate != 12
            or (video.width, video.height) != (160, 90)
        ):
            return NodeValidatorResult(False, message=f"分叶 {index + 1} 帧数或媒体规格不正确")
    return NodeValidatorResult(len(context.outputs) == 2)


def create_fixture(root: Path) -> tuple[ProjectServiceApplication, Path]:
    """从真实 Source 节点开始执行至 waiting，不注入已完成状态或 Artifact。"""
    for directory in ("complete", "invalid", "suffixed"):
        (root / directory).mkdir()
    for index, count in enumerate(COUNTS):
        generate(root / f"input-{index + 1}.mkv", count)
        generate(root / "complete" / NAMES[index], count)
    for name in reversed(NAMES):
        shutil.copyfile(
            root / "complete" / name, root / "suffixed" / name.replace(".mov", "_slp.mov")
        )
    generate(root / "invalid" / NAMES[1], 3)
    definition = NodeDefinition(
        type_id=TYPE_ID,
        version="0.3.5",
        input_ports=(
            PortSpec(port_id="videos", data_type="VideoFile", cardinality=Cardinality.ORDERED_MANY),
        ),
        output_ports=tuple(
            PortSpec(port_id=f"leaf-{index + 1:04d}", data_type="VideoFile") for index in range(2)
        ),
        parameter_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            instructions="本章包含两份合成分叶；可分批收件，齐全检查通过后显式提交整章。",
            output_paths=tuple(
                ExecutorOutputPathSpec(port_id=f"leaf-{index + 1:04d}", relative_path=name)
                for index, name in enumerate(NAMES)
            ),
        ),
        validator=ValidatorSpec(adapter=VALIDATOR),
    )
    nodes = [
        NodeInstance(
            node_id=f"source-{index + 1}",
            type_id="zniku.media.source.video",
            definition_version="0.2.0",
            parameters={"source_path": str(root / f"input-{index + 1}.mkv")},
            ui_position=UiPosition(x=40, y=80 + index * 180),
        )
        for index in range(2)
    ]
    nodes.append(
        NodeInstance(
            node_id="chapter-A",
            type_id=definition.type_id,
            definition_version=definition.version,
            parameters={},
            ui_position=UiPosition(x=420, y=80),
        )
    )
    project = Project(
        project_id="synthetic-chapter-batch",
        name="章节批量交付合成测试",
        graph=Graph(
            nodes=tuple(nodes),
            edges=tuple(
                Edge(
                    source_node_id=f"source-{index + 1}",
                    source_port_id="out",
                    target_node_id="chapter-A",
                    target_port_id="videos",
                    ordinal=index,
                )
                for index in range(2)
            ),
        ),
    )
    definitions = (*built_in_media_definitions(), definition)
    path = root / "chapter-batch.zniku"
    store = ProjectStore.create(path, project, definitions)
    store.save(
        project,
        definitions,
        studio_state=StudioState(
            node_views=(NodeViewState(node_id="chapter-A", display_name="A 章批量增强"),)
        ),
    )
    app = ProjectServiceApplication(
        work_root=root / "attempts",
        definition_catalog=definitions,
        python_adapters=media_python_adapters(),
        validators={**media_validators(), VALIDATOR: validate_batch},
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )
    opened = app.command({"operation": "open_project", "path": str(path)})
    started = app.command(
        {
            "operation": "run_all",
            "project_session_id": opened.project_session_id,
            "expected_storage_revision": opened.storage_revision,
        }
    )
    if not app.wait_until_idle(timeout=30) or started.active_run_id is None:
        raise RuntimeError("合成多输出节点未进入等待")
    detail = app.inspect_run_detail(started.active_run_id)
    if not any(
        node.node_id == "chapter-A" and node.state is NodeRunState.WAITING_EXTERNAL
        for node in detail.run.node_runs
    ):
        raise RuntimeError(
            f"合成多输出节点状态不正确: {[(n.node_id, n.error) for n in detail.run.node_runs]}"
        )
    return app, path


class BatchPlatform:
    """固定测试选择队列，不启动 Windows 对话框，也不输出授权 token。"""

    def __init__(self, root: Path, project: Path) -> None:
        self.root, self.project = root, project
        self.files = iter(
            (
                None,
                (str(root / "complete" / NAMES[0]),),
                (str(root / "invalid" / NAMES[1]),),
                (str(root / "complete" / NAMES[1]),),
            )
        )

    def capability_states(self) -> Mapping[HostCapability, str | None]:
        return dict.fromkeys(HOST_CAPABILITIES)

    def choose_paths(
        self, capability: HostCapability, arguments: HostDialogArguments
    ) -> Sequence[str] | None:
        if capability == "open_files":
            return next(self.files, None)
        if capability == "select_directory":
            return (str(self.root / "suffixed"),)
        if capability == "open_file" and arguments.extensions == (".zniku",):
            return (str(self.project),)
        return None

    def launch(self, command: HostLaunchCommand) -> None:
        assert command.shell is False


def main() -> None:
    """父测试进程关闭 stdin 即结束；只清理本工具创建的合成临时目录。"""
    repository = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".test-tmp-chapter-batch-ui-", dir=repository) as temporary:
        root = Path(temporary)
        application, path = create_fixture(root)
        server = DesktopServer(
            application, repository / "apps/studio/dist", platform=BatchPlatform(root, path)
        )
        server.start()
        print(json.dumps({"origin": server.origin, "project_path": str(path)}), flush=True)
        try:
            input()
        except EOFError:
            pass
        finally:
            server.close()


if __name__ == "__main__":
    main()
