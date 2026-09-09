"""为 production 浏览器门禁启动真实桌面 HTTP host 与纯合成工程。

仅原生 picker/系统打开由测试 double 替代，Graph、SQLite、Runtime、预览与 production
React 均使用正式实现。此工具不是产品启动入口，也不代表 Windows 原生窗口验收。
所有文件限定 TemporaryDirectory，源与输出不进入 Git；不接受媒体或命令行路径输入。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from run_av27_smoke import _generate_source as _generate_av27_source
from run_av27_smoke import require_tools
from run_media_smoke import _generate_source

from zniku.desktop.server import DesktopServer, build_desktop_application
from zniku.graph import Edge, Graph, NodeInstance, UiPosition
from zniku.media import built_in_media_definitions, split_video_definition
from zniku.project import NodeViewState, Project, ProjectStore, StudioState
from zniku.project_service.host_bridge import (
    HOST_CAPABILITIES,
    HostCapability,
    HostDialogArguments,
    HostLaunchCommand,
)
from zniku.runtime import Run, RunState, RuntimeRepository


def create_fixture(root: Path, count: int, history: int = 0) -> Path:
    """用正式事务创建空图历史，再保存多分支当前图，不修改历史 snapshot。"""
    definitions = built_in_media_definitions()
    path = root / f"synthetic-{count}.zniku"
    empty = Project(project_id=f"synthetic-{count}", name=f"合成 {count} 节点工程", graph=Graph())
    store = ProjectStore.create(path, project=empty, definitions=())
    repository = RuntimeRepository(store)
    clock = datetime.now(UTC) - timedelta(days=1)
    for index in range(history):
        timestamp = clock + timedelta(seconds=index)
        run = Run.pending(
            project_id=empty.project_id,
            graph_snapshot=Graph(),
            definitions_snapshot=(),
            created_at=timestamp,
        )
        repository.start_run(run, (), started_at=timestamp)
        repository.transition_run(run.run_id, RunState.COMPLETED, occurred_at=timestamp)
    source = root / "source.mkv"
    nodes = [
        NodeInstance(
            node_id="source",
            type_id="zniku.media.source.video",
            definition_version="0.2.0",
            parameters={"source_path": str(source)},
            ui_position=UiPosition(x=40, y=40),
        )
    ]
    edges = []
    # 全部 Transform 使用同一 Source，形成合法自由分支，避免伪造领域资料。
    for index in range(1, count):
        nodes.append(
            NodeInstance(
                node_id=f"step-{index}",
                type_id="zniku.media.video_transform.automatic",
                definition_version="0.2.0",
                parameters={"operation": "identity"},
                ui_position=UiPosition(x=380 + (index % 5) * 300, y=40 + (index // 5) * 250),
            )
        )
        edges.append(
            Edge(
                source_node_id="source",
                source_port_id="out",
                target_node_id=f"step-{index}",
                target_port_id="video",
            )
        )
    store.save(
        empty.model_copy(update={"graph": Graph(nodes=tuple(nodes), edges=tuple(edges))}),
        definitions,
    )
    return path


def create_external_fixture(root: Path) -> Path:
    """创建同名、不同输入帧数的普通外部节点；所有候选只是合成视频，不推进 Runtime。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("E_STUDIO_FIXTURE_TOOL: 找不到合成媒体所需的 FFmpeg")
    files = (
        ("input-A-12.mkv", 12, "testsrc2=size=160x90:rate=12"),
        ("input-B-15.mkv", 15, "testsrc2=size=160x90:rate=12"),
        ("external-A-12.mkv", 12, "color=c=green:size=160x90:rate=12"),
        ("external-B-15.mkv", 15, "color=c=blue:size=160x90:rate=12"),
        ("external-B-replacement-15.mkv", 15, "color=c=red:size=160x90:rate=12"),
    )
    for name, frames, pattern in files:
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                pattern,
                "-frames:v",
                str(frames),
                "-an",
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-f",
                "matroska",
                str(root / name),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            check=True,
            timeout=30,
        )
    definitions = built_in_media_definitions()
    nodes: list[NodeInstance] = []
    edges = []
    views = []
    for index, (part, frames) in enumerate((("A", 12), ("B", 15))):
        source_id, external_id = f"source-{part}", f"enhance-{part}"
        nodes.extend(
            (
                NodeInstance(
                    node_id=source_id,
                    type_id="zniku.media.source.video",
                    definition_version="0.2.0",
                    parameters={"source_path": str(root / f"input-{part}-{frames}.mkv")},
                    ui_position=UiPosition(x=40, y=40 + index * 250),
                ),
                NodeInstance(
                    node_id=external_id,
                    type_id="zniku.media.video_transform.enhancement.external",
                    definition_version="0.2.0",
                    parameters={
                        "tool": "合成外部工具",
                        "model": "合成模型",
                        "tool_version": "test-1",
                        "frame_relation": "equal",
                        "expected_width": 160,
                        "expected_height": 90,
                        "expected_frame_rate": "12/1",
                    },
                    ui_position=UiPosition(x=400, y=40 + index * 250),
                ),
            )
        )
        edges.append(
            Edge(
                source_node_id=source_id,
                source_port_id="out",
                target_node_id=external_id,
                target_port_id="video",
            )
        )
        views.append(NodeViewState(node_id=external_id, display_name="画质增强"))
    project = Project(
        project_id="synthetic-external",
        name="合成外部任务区分工程",
        graph=Graph(nodes=tuple(nodes), edges=tuple(edges)),
    )
    path = root / "synthetic-external.zniku"
    store = ProjectStore.create(path, project, definitions)
    store.save(project, definitions, studio_state=StudioState(node_views=tuple(views)))
    return path


def create_geometry_fixture(root: Path) -> Path:
    """为端口几何量测创建合法的 6/8/16 输出定义；只设计，不运行或伪造媒体结果。"""
    definitions = list(built_in_media_definitions())
    source = NodeInstance(
        node_id="geometry-source",
        type_id="zniku.media.source.video",
        definition_version="0.2.0",
        parameters={"source_path": str(root / "source.mkv")},
        ui_position=UiPosition(x=40, y=400),
    )
    nodes = [source]
    edges = []
    views = []
    for index, count in enumerate((6, 8, 16)):
        ports = tuple(f"part-{ordinal + 1:02d}" for ordinal in range(count))
        definition = split_video_definition(ports, type_id=f"zniku.synthetic.split.{count}")
        definitions.append(definition)
        node_id = f"ports-{count}"
        nodes.append(
            NodeInstance(
                node_id=node_id,
                type_id=definition.type_id,
                definition_version=definition.version,
                parameters={
                    "segments": [
                        {"port_id": port, "start_frame": ordinal, "end_frame": ordinal + 1}
                        for ordinal, port in enumerate(ports)
                    ]
                },
                ui_position=UiPosition(x=450 + index * 450, y=400),
            )
        )
        edges.append(
            Edge(
                source_node_id=source.node_id,
                source_port_id="out",
                target_node_id=node_id,
                target_port_id="video",
            )
        )
        views.append(NodeViewState(node_id=node_id, display_name=f"合成 {count} 输出端口"))
    project = Project(
        project_id="synthetic-port-geometry",
        name="合成 6/8/16 端口几何工程",
        graph=Graph(nodes=tuple(nodes), edges=tuple(edges)),
    )
    path = root / "synthetic-port-geometry.zniku"
    store = ProjectStore.create(path, project, tuple(definitions))
    store.save(project, tuple(definitions), studio_state=StudioState(node_views=tuple(views)))
    return path


class SyntheticPlatform:
    """模拟明确选择的路径与取消，不启动真实 OS 窗口；所有其他层保持正式实现。"""

    def __init__(self, projects: Sequence[Path], root: Path) -> None:
        self.projects = iter(projects)
        self.wizard_source = root / "av27-source.mkv"
        self.wizard_project = root / "wizard-output-layout.zniku"
        self.output_root = root / "wizard-output"
        self.output_root.mkdir()
        self.output_collision = root / "wizard-output-collision"
        self.output_collision.write_text("synthetic non-directory collision", encoding="utf-8")
        # 每项对应一次真实点击原生选择；与工程/向导 picker 独立，取消不消费正式 import。
        self.external_candidates = iter(
            (
                None,
                root / "external-B-15.mkv",
                root / "external-B-15.mkv",
                root / "external-B-replacement-15.mkv",
                root / "external-A-12.mkv",
                root / "external-B-replacement-15.mkv",
                root / "external-A-12.mkv",
            )
        )

    def capability_states(self) -> Mapping[HostCapability, str | None]:
        return dict.fromkeys(HOST_CAPABILITIES)

    def choose_paths(
        self, capability: HostCapability, arguments: HostDialogArguments
    ) -> Sequence[str] | None:
        if capability == "open_file" and arguments.extensions == (".zniku",):
            path = next(self.projects, None)
            return None if path is None else (str(path),)
        if capability == "open_file" and arguments.title == "选择视频素材":
            return (str(self.wizard_source),)
        if capability == "open_file" and (arguments.title or "").startswith("选择处理好的文件"):
            candidate = next(self.external_candidates, None)
            return None if candidate is None else (str(candidate),)
        if capability == "save_file" and arguments.title == "保存 ZNIKU 视频工程":
            return (str(self.wizard_project),)
        if capability == "select_directory" and arguments.title == "选择成片输出目录":
            return (str(self.output_root),)
        return None

    def launch(self, command: HostLaunchCommand) -> None:
        # 测试只能核对到 HostBridge 固定 launch command，不伪称真实播放器已打开。
        assert command.shell is False
        # 打开位置本身不创建目录。目录写入只能由正式 OutputFile 执行步骤负责。


def main() -> None:
    """端口由正式服务分配；stdout 提供测试 URL 与合成路径，不输出 token。"""
    repository_root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".test-tmp-phase5-", dir=repository_root) as temporary:
        root = Path(temporary)
        _generate_source(root / "source.mkv")
        _generate_av27_source(require_tools(), root / "av27-source.mkv")
        small = create_fixture(root, 50)
        large = create_fixture(root, 200, 1000)
        media = create_fixture(root, 2)
        external = create_external_fixture(root)
        geometry = create_geometry_fixture(root)
        application = build_desktop_application(root / "attempts")
        platform = SyntheticPlatform((small, large, media), root)
        server = DesktopServer(
            application,
            repository_root / "apps/studio/dist",
            platform=platform,
        )
        server.start()
        # 仅父测试进程接收纯合成位置，便于磁盘只读断言；不是产品 HTTP 控制接口。
        print(
            json.dumps(
                {
                    "origin": server.origin,
                    "wizard_project": str(platform.wizard_project),
                    "output_root": str(platform.output_root),
                    "output_collision": str(platform.output_collision),
                    "external_project": str(external),
                    "small_project": str(small),
                    "large_project": str(large),
                    "media_project": str(media),
                    "geometry_project": str(geometry),
                }
            ),
            flush=True,
        )
        try:
            # 父测试进程关闭 stdin 表示正常退出；不向客户端开放测试控制 endpoint。
            input()
        except EOFError:
            pass
        finally:
            server.close()


if __name__ == "__main__":
    main()
