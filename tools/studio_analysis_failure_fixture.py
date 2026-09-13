"""以不均匀时间戳合成素材复现正式 Source 分析失败，不伪造 Runtime 状态。

使用 production React、正式 Source validator、SQLite 与 Runtime，只替代原生路径选择。
所有媒体和工程均限定在本测试的临时目录；不接受外部路径，不接触用户工程或服务。
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from run_av27_smoke import _base_argv, _run_tool, _signal_argv, require_tools
from studio_production_fixture import SyntheticPlatform

from zniku.desktop import DesktopServer, build_desktop_application


def main() -> None:
    """生成 120 帧交替长短间隔，再由浏览器显式发起分析与节点重试。"""
    repository_root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".test-tmp-analysis-failure-", dir=repository_root) as temporary:
        root = Path(temporary)
        _run_tool(
            [
                *_base_argv(require_tools()),
                "-f",
                "lavfi",
                "-i",
                "color=c=0x305080:size=1920x1080:rate=30000/1001",
                "-frames:v",
                "120",
                "-vf",
                r"settb=1/120000,setpts=N*4004+1001*mod(N\,2)",
                "-fps_mode",
                "passthrough",
                "-enc_time_base",
                "filter",
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-pix_fmt",
                "yuv420p10le",
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
