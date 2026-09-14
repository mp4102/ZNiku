"""启动仅监听 loopback 的 ZNIKU Studio 0.3.0 Project Service 开发 host。"""

from __future__ import annotations

import argparse
from pathlib import Path

from zniku.avenhance_v27 import (
    av27_python_adapters,
    av27_validators,
    built_in_av27_definitions,
)
from zniku.chapter_overlap.definitions import (
    built_in_overlap_definitions,
    overlap_python_adapters,
    overlap_validators,
)
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.prepared_color import definitions as prepared_color
from zniku.prepared_source import definitions as prepared_source
from zniku.prepared_source import work_definitions as prepared_work
from zniku.project_service import ProjectServiceApplication, serve_project_service
from zniku.source_aligned import definitions as source_aligned
from zniku.source_color import definitions as source_color
from zniku.source_preparation import (
    register_source_preparation_adapters,
    register_source_preparation_validators,
    source_preparation_definitions,
)
from zniku.source_preparation import work_definitions as work_source


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
    """构造本地 host；Python 普通媒体与 v2.7 节点目录是唯一合同权威。"""

    return ProjectServiceApplication(
        work_root=work_root,
        definition_catalog=(
            *built_in_media_definitions(),
            *built_in_av27_definitions(),
            *built_in_overlap_definitions(),
            *source_aligned.built_in_overlap_definitions(),
            source_aligned.external_definition("mp4"),
            source_aligned.external_definition("mov"),
            source_aligned.external_definition("mkv"),
            *source_preparation_definitions(),
            *prepared_source.built_in_overlap_definitions(),
            prepared_source.external_definition("mp4"),
            prepared_source.external_definition("mov"),
            prepared_source.external_definition("mkv"),
            *source_color.source_preparation_definitions(),
            *prepared_color.built_in_overlap_definitions(),
            prepared_color.external_definition("mp4"),
            prepared_color.external_definition("mov"),
            prepared_color.external_definition("mkv"),
            *work_source.source_preparation_definitions(),
            *prepared_work.built_in_overlap_definitions(),
            prepared_work.external_definition("mp4"),
            prepared_work.external_definition("mov"),
            prepared_work.external_definition("mkv"),
        ),
        python_adapters={
            **media_python_adapters(),
            **av27_python_adapters(),
            **overlap_python_adapters(),
            **source_aligned.overlap_python_adapters(),
            **register_source_preparation_adapters(),
            **prepared_source.overlap_python_adapters(),
            **source_color.register_source_preparation_adapters(),
            **prepared_color.overlap_python_adapters(),
            **work_source.register_source_preparation_adapters(),
            **prepared_work.overlap_python_adapters(),
        },
        validators={
            **media_validators(),
            **av27_validators(),
            **overlap_validators(),
            **source_aligned.overlap_validators(),
            **register_source_preparation_validators(),
            **prepared_source.overlap_validators(),
            **source_color.register_source_preparation_validators(),
            **prepared_color.overlap_validators(),
            **work_source.register_source_preparation_validators(),
            **prepared_work.overlap_validators(),
        },
        media_probe=runner_media_probe,
        artifact_quick_probe=media_artifact_quick_probe,
    )


if __name__ == "__main__":
    raise SystemExit(main())
