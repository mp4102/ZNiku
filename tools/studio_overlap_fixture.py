"""仅为重叠补帧 production E2E 提供 120 帧合成媒体与隔离桌面服务。

使用正式 Registry、SQLite、Runtime 和服务端点；只替代原生路径选择，不伪造增强或 Aion 输出。
所有文件落在本工作树新建的临时目录，父测试结束即关闭自己创建的服务，不触碰操作者工程。
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from run_av27_smoke import _base_argv, _run_tool, _signal_argv, require_tools
from studio_production_fixture import SyntheticPlatform

from zniku.desktop import DesktopServer, build_desktop_application


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".test-tmp-overlap-studio-", dir=repository_root) as temporary:
        root = Path(temporary)
        _run_tool(
            [
                *_base_argv(require_tools()),
                "-f",
                "lavfi",
                "-i",
                "color=c=0x305080:size=1920x1080:rate=30000/1001",
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
        platform = SyntheticPlatform((), root)
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
