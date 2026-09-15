"""仅为重叠补帧 production E2E 提供 120 帧合成媒体与隔离桌面服务。

使用正式 Registry、SQLite、Runtime 和服务端点；只替代原生路径选择。MR 候选是同一合成原片的
120 帧 H.264/MP4，不代表真实修复软件能力；不伪造增强或 Aion 输出。
可选修复场景另生成4:3拒绝源和60帧新参考，验证显式换源而非内容等价或真实修复。
所有文件落在本工作树新建的临时目录，父测试结束即关闭自己创建的服务，不触碰操作者工程。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from run_av27_smoke import _base_argv, _run_tool, _signal_argv, require_tools
from studio_production_fixture import SyntheticPlatform

from zniku.desktop import DesktopServer, build_desktop_application
from zniku.project_service.host_bridge import HostCapability, HostDialogArguments


class RepairSelectionPlatform(SyntheticPlatform):
    """只模拟原生选择顺序；媒体检查、换源事务与Runtime均为正式实现。"""

    def __init__(self, root: Path, candidate: Path) -> None:
        super().__init__((), root)
        self.source_choices = iter((self.wizard_source, None, candidate, candidate))

    def choose_paths(
        self, capability: HostCapability, arguments: HostDialogArguments
    ) -> Sequence[str] | None:
        if capability == "open_file" and arguments.title == "选择视频素材":
            selected = next(self.source_choices, None)
            return None if selected is None else (str(selected),)
        return super().choose_paths(capability, arguments)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair-scenario", action="store_true")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".test-tmp-overlap-studio-", dir=repository_root) as temporary:
        root = Path(temporary)
        # 修复场景用明确非16:9的4:3源，让真实准入拒绝；不损坏文件或伪造失败状态。
        geometry = "1440x1080" if arguments.repair_scenario else "1920x1080"
        _run_tool(
            [
                *_base_argv(require_tools()),
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x305080:size={geometry}:rate=30000/1001",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=4.004",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-frames:v",
                "120",
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-pix_fmt",
                "yuv420p10le",
                "-c:a",
                "flac",
                *_signal_argv(),
                str(root / "av27-source.mkv"),
            ]
        )
        mr_candidate = root / "source-aligned-mr.mp4"
        _run_tool(
            [
                *_base_argv(require_tools()),
                "-i",
                str(root / "av27-source.mkv"),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-fps_mode",
                "passthrough",
                *_signal_argv(),
                str(mr_candidate),
            ]
        )
        repair_candidate = root / "source-repaired-60-frames.mkv"
        if arguments.repair_scenario:
            _run_tool(
                [
                    *_base_argv(require_tools()),
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0x508030:size=1920x1080:rate=30",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=660:sample_rate=44100:duration=2",
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-frames:v",
                    "60",
                    "-c:v",
                    "ffv1",
                    "-level",
                    "3",
                    "-pix_fmt",
                    "yuv420p10le",
                    "-c:a",
                    "flac",
                    *_signal_argv(),
                    str(repair_candidate),
                ]
            )
        platform = (
            RepairSelectionPlatform(root, repair_candidate)
            if arguments.repair_scenario
            else SyntheticPlatform((), root)
        )
        platform.external_candidates = iter((None, mr_candidate))
        application = build_desktop_application(root / "attempts")
        server = DesktopServer(application, repository_root / "apps/studio/dist", platform=platform)
        server.start()
        print(
            json.dumps(
                {
                    "origin": server.origin,
                    "wizard_project": str(platform.wizard_project),
                    "output_root": str(platform.output_root),
                    "output_collision": str(platform.output_collision),
                    "external_project": str(platform.wizard_project),
                    "source_aligned_mr": str(mr_candidate),
                    "repair_candidate": str(repair_candidate)
                    if arguments.repair_scenario
                    else None,
                    "original_source": str(root / "av27-source.mkv"),
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
