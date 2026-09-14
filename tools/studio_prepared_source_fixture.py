"""为素材准备 production E2E 启动隔离 host 和纯合成源。

只替代原生路径选择。Graph、Python 诊断、保内容验证、SQLite、Runtime 与 production
Studio 均为正式实现；不启用未晋级 T1，不模拟修复成功，不读真实媒体或用户配置。
关闭 stdin 后只关闭本次服务并清理本工具创建的 TemporaryDirectory。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from studio_production_fixture import SyntheticPlatform

from zniku.desktop import DesktopServer, build_desktop_application
from zniku.prepared_source.definitions import built_in_overlap_definitions, external_definition
from zniku.project import Project, ProjectStore
from zniku.project_service.host_bridge import HostCapability, HostDialogArguments
from zniku.source_preparation import build_preparation_graph, source_preparation_definitions


def generate_sources(root: Path) -> tuple[Path, Path, Path]:
    """以完整已知帧生成正常源、有限时钟偏差源及保内容外部交付；不修改普通媒体。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or shutil.which("ffprobe") is None:
        raise RuntimeError("E_STUDIO_FIXTURE_TOOL: 合成 E2E 需要 FFmpeg/FFprobe")
    normal, problem, repaired = (
        root / name
        for name in (
            "av27-source.mkv",
            "synthetic-clock-issue.mkv",
            "source-repaired.mkv",
        )
    )
    commands = (
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30",
            "-frames:v",
            "120",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            str(normal),
        ],
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(normal),
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-bsf:v",
            "setts=pts=PTS*1.0015:dts=DTS*1.0015",
            str(problem),
        ],
    )
    for command in commands:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            timeout=45,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("E_STUDIO_FIXTURE_MEDIA: 合成媒体生成失败")
    # 同一编码帧序的正常副本可修复本 fixture 的时间戳差异，不伪造媒体合同或 producer metadata。
    shutil.copyfile(normal, repaired)
    return normal, problem, repaired


class PreparedPlatform(SyntheticPlatform):
    """前两次选素材返回正常/异常源，之后保持异常；外部每次选真正的保内容副本。"""

    def __init__(
        self, root: Path, projects: Sequence[Path], source: Path, problem: Path, repaired: Path
    ) -> None:
        super().__init__(projects, root)
        self.wizard_source = source
        self.problem_source = problem
        self.repaired = repaired
        self.source_picker_calls = 0

    def choose_paths(
        self,
        capability: HostCapability,
        arguments: HostDialogArguments,
    ) -> Sequence[str] | None:
        if capability == "open_file" and arguments.title == "选择视频素材":
            self.source_picker_calls += 1
            return (
                str(self.wizard_source if self.source_picker_calls == 1 else self.problem_source),
            )
        if capability == "open_file" and (arguments.title or "").startswith("选择处理好的文件"):
            return (str(self.repaired),)
        return super().choose_paths(capability, arguments)


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".test-tmp-prepared-studio-", dir=repository) as temporary:
        root = Path(temporary)
        normal, problem, repaired = generate_sources(root)
        definitions = (
            *source_preparation_definitions(),
            *built_in_overlap_definitions(),
            external_definition("mp4"),
            external_definition("mov"),
            external_definition("mkv"),
        )
        problem_project = root / "synthetic-clock-issue.zniku"
        external_project = root / "synthetic-source-repair.zniku"
        projects: tuple[tuple[Path, Literal["diagnose", "external"]], ...] = (
            (problem_project, "diagnose"),
            (external_project, "external"),
        )
        for path, route in projects:
            graph = build_preparation_graph(
                str(problem), route=route, target_frame_rate="30/1", audio_policy="none"
            )
            ProjectStore.create(
                path,
                Project(project_id=f"synthetic-{route}", name="合成素材准备工程", graph=graph),
                definitions,
            )
        platform = PreparedPlatform(
            root, (problem_project, external_project), normal, problem, repaired
        )
        application = build_desktop_application(root / "attempts")
        server = DesktopServer(
            application,
            repository / "apps/studio/dist",
            platform=platform,
            data_root=root / "desktop-data",
        )
        server.start()
        print(
            json.dumps(
                {
                    "origin": server.origin,
                    "wizard_project": str(platform.wizard_project),
                    "output_root": str(platform.output_root),
                    "output_collision": str(platform.output_collision),
                    "data_parent": str(platform.data_parent),
                    "external_project": str(external_project),
                    "small_project": str(problem_project),
                    "large_project": str(problem_project),
                    "media_project": str(problem_project),
                    "geometry_project": str(problem_project),
                    "prepared_normal_source": str(normal),
                    "prepared_problem_source": str(problem),
                    "prepared_external_candidate": str(repaired),
                    "source_preparation_problem": str(problem),
                    "source_preparation_repaired": str(repaired),
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
