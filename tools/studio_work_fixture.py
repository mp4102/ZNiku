"""为普通工作源 production E2E 提供隔离 host 与纯合成三路线素材。

复用合成媒体和原生选择器替身；Graph、工作检查、持久化、Runtime、HTTP、production Studio
均为实际实现。只使用 work.1 定义，不伪造旧 wire，不启用 T1，不访问真实媒体/用户服务。
stdin 关闭时只停止本次 host，并清理本工具排他创建的临时根。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

from studio_prepared_color_fixture import PreparedPlatform, generate_sources

from zniku.desktop import DesktopServer, build_desktop_application
from zniku.prepared_source.work_definitions import built_in_overlap_definitions, external_definition
from zniku.project import Project, ProjectStore
from zniku.source_color.models import T1_PROMOTED as COLOR_T1_PROMOTED
from zniku.source_preparation.models import T1_PROMOTED
from zniku.source_preparation.work_definitions import source_preparation_definitions
from zniku.source_preparation.work_inspection import inspect_source
from zniku.source_preparation.work_template import build_preparation_graph


def working_sources(root: Path) -> tuple[Path, Path, Path]:
    """复用编码源，再制作显著重定时及不同 N 的新参考，不能冒称保内容修复。"""
    normal, _old_problem, _old_repaired = generate_sources(root)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("E_STUDIO_FIXTURE_TOOL: 合成 E2E 需要 FFmpeg")
    retimed, reference = root / "synthetic-work-retime.mkv", root / "synthetic-new-reference.mkv"
    for target, extra in (
        (retimed, ["-bsf:v", "setts=pts=PTS*1.1:dts=DTS*1.1"]),
        (reference, ["-frames:v", "90"]),
    ):
        result = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(normal),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                *extra,
                str(target),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            timeout=45,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("E_STUDIO_FIXTURE_MEDIA: 普通工作源合成失败")
    return normal, retimed, reference


def check_sources(sources: tuple[Path, Path, Path]) -> dict[str, object]:
    """真实普通检查验证 fixture 预期，检查不伪造准入或 Runtime 完成。"""
    reports = [inspect_source(path, str(uuid4()), target_frame_rate="30/1") for path in sources]
    if tuple(r.status for r in reports) != ("direct", "preparation_required", "direct"):
        raise RuntimeError("E_STUDIO_FIXTURE_CLASSIFICATION: 合成三路线分类不符")
    counts = tuple(r.video.frame_count if r.video else 0 for r in reports)
    if counts != (120, 120, 90):
        raise RuntimeError("E_STUDIO_FIXTURE_FRAMES: 合成帧数不符")
    if T1_PROMOTED or COLOR_T1_PROMOTED:
        raise RuntimeError("E_STUDIO_FIXTURE_T1: 普通路线不能改变 T1 晋级状态")
    return {
        "contract_version": "0.3.4-work.1",
        "status": "synthetic_passed",
        "decisions": [r.status for r in reports],
        "frame_counts": list(counts),
        "inspection_scopes": [r.inspection_scope for r in reports],
        "t1_promoted": False,
        "real_acceptance": "pending_real_acceptance",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="普通工作源的隔离 production E2E fixture")
    parser.add_argument("--check", action="store_true", help="仅运行合成素材自检，不启动服务")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".test-tmp-work-studio-", dir=repository) as temporary:
        root = Path(temporary)
        sources = working_sources(root)
        if args.check:
            print(json.dumps(check_sources(sources), ensure_ascii=False), flush=True)
            return
        normal, retimed, reference = sources
        definitions = (
            *source_preparation_definitions(),
            *built_in_overlap_definitions(),
            external_definition("mp4"),
            external_definition("mov"),
            external_definition("mkv"),
        )
        retime_project = root / "synthetic-work-retime.zniku"
        external_project = root / "synthetic-new-reference.zniku"
        projects: tuple[tuple[Path, Literal["diagnose", "external"]], ...] = (
            (retime_project, "diagnose"),
            (external_project, "external"),
        )
        for path, route in projects:
            graph = build_preparation_graph(
                str(retimed),
                route=route,
                target_frame_rate="30/1",
                audio_policy="reference" if route == "external" else "none",
                reference_change_confirmed=route == "external",
            )
            ProjectStore.create(
                path,
                Project(
                    project_id=f"synthetic-work-{route}", name="合成普通工作源工程", graph=graph
                ),
                definitions,
            )
        platform = PreparedPlatform(
            root, (retime_project, external_project), normal, retimed, reference
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
                    "contract_version": "0.3.4-work.1",
                    "wizard_project": str(platform.wizard_project),
                    "output_root": str(platform.output_root),
                    "output_collision": str(platform.output_collision),
                    "data_parent": str(platform.data_parent),
                    "external_project": str(external_project),
                    "small_project": str(retime_project),
                    "large_project": str(retime_project),
                    "media_project": str(retime_project),
                    "geometry_project": str(retime_project),
                    "work_normal_source": str(normal),
                    "work_retime_source": str(retimed),
                    "work_external_candidate": str(reference),
                    "source_preparation_problem": str(retimed),
                    "source_preparation_repaired": str(reference),
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
