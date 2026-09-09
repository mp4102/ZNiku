"""为批次 C 提供隔离的真实服务与合成媒体工程，不增加产品测试控制接口。

Graph、SQLite、Runtime、收件与迁移均使用正式实现；只有原生目录选择使用明确的
测试替身。所有源、输出、工作目录和桌面偏好均位于同一次 TemporaryDirectory，
关闭父测试 stdin 后只清理本次合成资源，不接受外部媒体或工程路径。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from run_media_smoke import _generate_source
from studio_production_fixture import create_external_fixture, create_fixture

from zniku.desktop.server import DesktopServer, build_desktop_application
from zniku.graph import Edge, Graph, NodeInstance, UiPosition
from zniku.media import built_in_media_definitions
from zniku.project import Project, ProjectStore
from zniku.project_service.host_bridge import (
    HOST_CAPABILITIES,
    HostCapability,
    HostDialogArguments,
    HostLaunchCommand,
)


class BatchCPlatform:
    """目录选择首次取消，后续返回本次专用合成磁盘；不启动实际 OS 窗口。"""

    def __init__(self, disk: Path) -> None:
        self.disk = disk
        self.cancelled = False

    def capability_states(self) -> Mapping[HostCapability, str | None]:
        return dict.fromkeys(HOST_CAPABILITIES)

    def choose_paths(
        self, capability: HostCapability, arguments: HostDialogArguments
    ) -> Sequence[str] | None:
        if capability == "select_directory" and arguments.title == "选择工程数据父文件夹":
            if not self.cancelled:
                self.cancelled = True
                return None
            return (str(self.disk),)
        return None

    def launch(self, command: HostLaunchCommand) -> None:
        assert command.shell is False


def create_retry(root: Path) -> Path:
    """用已存在的合成输出触发正式禁止覆盖失败，恢复时只移走本测试阻塞物。"""
    source = root / "source.mkv"
    _generate_source(source)
    target = root / "published.mkv"
    target.write_bytes(b"synthetic existing output must not be overwritten")
    graph = Graph(
        nodes=(
            NodeInstance(
                node_id="source",
                type_id="zniku.media.source.video",
                definition_version="0.2.0",
                parameters={"source_path": str(source)},
                ui_position=UiPosition(x=20, y=50),
            ),
            NodeInstance(
                node_id="transform",
                type_id="zniku.media.video_transform.automatic",
                definition_version="0.2.0",
                parameters={"operation": "identity"},
                ui_position=UiPosition(x=380, y=50),
            ),
            NodeInstance(
                node_id="publish",
                type_id="zniku.media.output_file.video",
                definition_version="0.2.0",
                parameters={"target_path": str(target), "mode": "copy", "overwrite": False},
                ui_position=UiPosition(x=740, y=50),
            ),
        ),
        edges=(
            Edge(
                source_node_id="source",
                source_port_id="out",
                target_node_id="transform",
                target_port_id="video",
            ),
            Edge(
                source_node_id="transform",
                source_port_id="video",
                target_node_id="publish",
                target_port_id="in",
            ),
        ),
    )
    path = root / "retry.zniku"
    ProjectStore.create(
        path,
        Project(project_id="batch-c-retry", name="合成失败恢复工程", graph=graph),
        built_in_media_definitions(),
    )
    return path


def main() -> None:
    """真实桌面服务使用随机 loopback 端口；stdout 仅返回合成位置，绝不输出凭据。"""
    repository = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".test-tmp-batch-c-", dir=repository) as temporary:
        root = Path(temporary)
        external_root, storage_root, retry_root, conflict_root, disk = (
            root / name for name in ("external", "storage", "retry", "conflict", "dedicated-disk")
        )
        for directory in (external_root, storage_root, retry_root, conflict_root, disk):
            directory.mkdir()
        external = create_external_fixture(external_root)
        _generate_source(storage_root / "source.mkv")
        storage = create_fixture(storage_root, 2)
        retry = create_retry(retry_root)
        conflict = create_retry(conflict_root)
        application = build_desktop_application(root / "attempts")
        server = DesktopServer(
            application,
            repository / "apps/studio/dist",
            platform=BatchCPlatform(disk),
            data_root=root / "desktop-data",
        )
        server.start()
        print(
            json.dumps(
                {
                    "origin": server.origin,
                    "wizard_project": str(root / "unused-wizard.zniku"),
                    "output_root": str(disk),
                    "output_collision": str(retry_root / "published.mkv"),
                    "external_project": str(external),
                    "small_project": str(storage),
                    "large_project": str(storage),
                    "media_project": str(retry),
                    "geometry_project": str(conflict),
                }
            ),
            flush=True,
        )
        try:
            input()
        except EOFError:
            pass
        finally:
            server.close()


if __name__ == "__main__":
    main()
