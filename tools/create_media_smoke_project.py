"""从操作者选定的短媒体创建 Phase 4 自由 DAG 验收工程。

工程同时包含一条 Source→Transform→Output 分支和一条
Source→Split→独立 Transform→Merge→Output 分支。工具只读取源的精确帧数，
不把媒体、Project、attempt 或输出加入 Git。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from zniku.graph import Edge, Graph, NodeInstance, UiPosition
from zniku.media import built_in_media_definitions, exact_video_frame_count
from zniku.project import Project, ProjectStore

_VERSION = "0.2.0"


def build_graph(
    source_path: Path,
    output_directory: Path,
    *,
    input_frames: int,
    split_frame: int,
    overwrite: bool,
) -> Graph:
    """构造两条合法分支；Split 区间在设计时完整覆盖输入。"""

    if input_frames < 2 or not 0 < split_frame < input_frames:
        raise ValueError(f"split_frame 必须位于 1..{input_frames - 1}，当前为 {split_frame}")
    source = str(source_path.resolve(strict=True))
    simple_target = str((output_directory / "phase4-simple.mkv").resolve(strict=False))
    split_target = str((output_directory / "phase4-split-merge.mkv").resolve(strict=False))
    nodes = (
        NodeInstance(
            node_id="source",
            type_id="zniku.media.source.video",
            definition_version=_VERSION,
            parameters={"source_path": source},
            ui_position=UiPosition(x=60, y=260),
        ),
        NodeInstance(
            node_id="simple-transform",
            type_id="zniku.media.video_transform.automatic",
            definition_version=_VERSION,
            parameters={"operation": "identity"},
            ui_position=UiPosition(x=350, y=80),
        ),
        NodeInstance(
            node_id="simple-output",
            type_id="zniku.media.output_file.video",
            definition_version=_VERSION,
            parameters={
                "target_path": simple_target,
                "mode": "copy",
                "overwrite": overwrite,
            },
            ui_position=UiPosition(x=680, y=80),
        ),
        NodeInstance(
            node_id="split",
            type_id="zniku.media.split_video.2",
            definition_version=_VERSION,
            parameters={
                "segments": [
                    {"port_id": "A", "start_frame": 0, "end_frame": split_frame},
                    {
                        "port_id": "B",
                        "start_frame": split_frame,
                        "end_frame": input_frames,
                    },
                ]
            },
            ui_position=UiPosition(x=350, y=330),
        ),
        NodeInstance(
            node_id="branch-a",
            type_id="zniku.media.video_transform.automatic",
            definition_version=_VERSION,
            parameters={"operation": "identity"},
            ui_position=UiPosition(x=650, y=260),
        ),
        NodeInstance(
            node_id="branch-b",
            type_id="zniku.media.video_transform.automatic",
            definition_version=_VERSION,
            parameters={"operation": "identity"},
            ui_position=UiPosition(x=650, y=440),
        ),
        NodeInstance(
            node_id="merge",
            type_id="zniku.media.merge_video",
            definition_version=_VERSION,
            ui_position=UiPosition(x=950, y=350),
        ),
        NodeInstance(
            node_id="split-output",
            type_id="zniku.media.output_file.video",
            definition_version=_VERSION,
            parameters={
                "target_path": split_target,
                "mode": "copy",
                "overwrite": overwrite,
            },
            ui_position=UiPosition(x=1250, y=350),
        ),
    )
    edges = (
        Edge(
            source_node_id="source",
            source_port_id="out",
            target_node_id="simple-transform",
            target_port_id="video",
        ),
        Edge(
            source_node_id="simple-transform",
            source_port_id="video",
            target_node_id="simple-output",
            target_port_id="in",
        ),
        Edge(
            source_node_id="source",
            source_port_id="out",
            target_node_id="split",
            target_port_id="video",
        ),
        Edge(
            source_node_id="split",
            source_port_id="A",
            target_node_id="branch-a",
            target_port_id="video",
        ),
        Edge(
            source_node_id="split",
            source_port_id="B",
            target_node_id="branch-b",
            target_port_id="video",
        ),
        Edge(
            source_node_id="branch-a",
            source_port_id="video",
            target_node_id="merge",
            target_port_id="videos",
            ordinal=0,
        ),
        Edge(
            source_node_id="branch-b",
            source_port_id="video",
            target_node_id="merge",
            target_port_id="videos",
            ordinal=1,
        ),
        Edge(
            source_node_id="merge",
            source_port_id="video",
            target_node_id="split-output",
            target_port_id="in",
        ),
    )
    return Graph(nodes=nodes, edges=edges)


def build_parser() -> argparse.ArgumentParser:
    """声明只作用于本地验收文件的显式参数。"""

    parser = argparse.ArgumentParser(description="创建 ZNIKU Phase 4 短媒体验收 Project")
    parser.add_argument("project", type=Path, help="待创建的 .zniku 路径")
    parser.add_argument("source", type=Path, help="只读本地视频源")
    parser.add_argument("output_directory", type=Path, help="OutputFile 发布目录")
    parser.add_argument(
        "--split-frame",
        type=int,
        help="首段结束帧；默认使用精确总帧数的一半",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="明确允许 OutputFile 覆盖同名验收输出",
    )
    return parser


def main() -> int:
    """计数、验证边界并排他创建新 Project。"""

    arguments = build_parser().parse_args()
    source = arguments.source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size <= 0:
        raise ValueError(f"源必须是非空常规文件：{source}")
    if arguments.project.suffix.lower() != ".zniku":
        raise ValueError("project 必须使用 .zniku 扩展名")
    arguments.project.parent.mkdir(parents=True, exist_ok=True)
    arguments.output_directory.mkdir(parents=True, exist_ok=True)
    output_directory = arguments.output_directory.resolve(strict=True)
    input_frames = exact_video_frame_count(source)
    split_frame = arguments.split_frame or input_frames // 2
    graph = build_graph(
        source,
        output_directory,
        input_frames=input_frames,
        split_frame=split_frame,
        overwrite=arguments.overwrite,
    )
    project = Project(
        project_id="smoke.phase4",
        name="ZNIKU Phase 4 Media Smoke",
        graph=graph,
    )
    ProjectStore.create(arguments.project, project, built_in_media_definitions())
    print(f"project={arguments.project.resolve(strict=True)}")
    print(f"source={source}")
    print(f"frames={input_frames}")
    print(f"segments=[0,{split_frame}) + [{split_frame},{input_frames})")
    print(f"outputs={output_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
