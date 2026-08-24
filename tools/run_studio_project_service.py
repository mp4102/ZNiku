"""启动仅监听 loopback 的 ZNIKU Studio 0.2.0 Project Service 开发 host。"""

from __future__ import annotations

import argparse
from pathlib import Path

from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.project_service import ProjectServiceApplication, serve_project_service


def build_parser() -> argparse.ArgumentParser:
    """声明 host 进程配置；浏览器请求不能修改 work root 或 port。"""

    parser = argparse.ArgumentParser(description="运行 ZNIKU Studio 本地 Project Service")
    parser.add_argument(
        "--work-root",
        type=Path,
        required=True,
        help="持久 attempt 工作目录；同一 .zniku 跨重启应沿用同一路径",
    )
    parser.add_argument("--project", type=Path, help="可选：启动时打开一个既有 .zniku")
    parser.add_argument("--port", type=int, default=18765, help="loopback port，默认 18765")
    return parser


def main() -> int:
    """建立单一 application session 并持续服务到操作者中断。"""

    arguments = build_parser().parse_args()
    application = build_application(arguments.work_root)
    if arguments.project is not None:
        application.command({"operation": "open_project", "path": str(arguments.project)})
    server = serve_project_service(application, port=arguments.port)
    print(f"ZNIKU Project Service: http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_application(work_root: Path) -> ProjectServiceApplication:
    """构造正式 Phase 4 host；Python 媒体目录是 Studio 节点合同的唯一权威。"""

    return ProjectServiceApplication(
        work_root=work_root,
        definition_catalog=built_in_media_definitions(),
        python_adapters=media_python_adapters(),
        validators=media_validators(),
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )


if __name__ == "__main__":
    raise SystemExit(main())
