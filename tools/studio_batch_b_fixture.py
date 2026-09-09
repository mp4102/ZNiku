"""建立批次 B 的正式合法合成图与隔离桌面服务，不读取用户工程或真实素材。

几何工程先经过完整 GraphValidator，再由 ProjectStore 保存；不是把 preflight 的矩形
数据冒充可运行 Graph。几何工程只设计，媒体执行证据使用独立两节点短合成工程。
--validate-only 只建立和检查临时 SQLite/Graph，不生成媒体、不启动 HTTP 或浏览器。
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

from run_media_smoke import _generate_source
from studio_production_fixture import SyntheticPlatform, create_external_fixture, create_fixture

from zniku.avenhance_v27 import av27_python_adapters, av27_validators, built_in_av27_definitions
from zniku.desktop.server import DesktopServer
from zniku.graph import (
    Cardinality,
    Edge,
    ExecutionMode,
    ExecutorOutputPathSpec,
    Graph,
    GraphValidator,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    UiPosition,
)
from zniku.media import (
    built_in_media_definitions,
    media_artifact_quick_probe,
    media_python_adapters,
    media_validators,
    runner_media_probe,
)
from zniku.presentation import (
    CategoryPresentation,
    IconToken,
    NodePresentation,
    PaletteLevel,
    ParameterGroupPresentation,
    ParameterPresentation,
    PortPresentation,
    PresentationCatalog,
    resolve_presentation_catalog,
)
from zniku.project import NodeViewState, Project, ProjectStore, StudioState
from zniku.project_service import ProjectServiceApplication
from zniku.runtime import Run, RunState, RuntimeRepository


def definition(inputs: int, outputs: int, *, ordered: bool = False) -> NodeDefinition:
    """只声明 typed ports 与普通人工交接，不注入未知 Python adapter 或可执行代码。"""
    kind = "ordered" if ordered else "one"
    return NodeDefinition(
        type_id=f"zniku.synthetic.batch_b.{inputs}.{outputs}.{kind}",
        version="0.2.0",
        input_ports=tuple(
            PortSpec(
                port_id=f"in-{index + 1:02}",
                data_type="VideoFile",
                cardinality=Cardinality.ORDERED_MANY if ordered else Cardinality.ONE,
                required=True,
            )
            for index in range(inputs)
        ),
        output_ports=tuple(
            PortSpec(port_id=f"out-{index + 1:02}", data_type="VideoFile")
            for index in range(outputs)
        ),
        parameter_schema={
            "type": "object",
            "properties": {
                "strength": {"type": "integer", "minimum": 1, "maximum": 9},
                "options": {
                    "type": "object",
                    "properties": {"description": {"type": "string"}},
                    "additionalProperties": False,
                },
                "steps": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["strength"],
            "additionalProperties": False,
        },
        execution_mode=ExecutionMode.MANUAL_EXTERNAL,
        executor=ManualExternalExecutorSpec(
            instructions="纯合成图形验收定义；此工程仅设计，不提交外部媒体。",
            output_paths=tuple(
                ExecutorOutputPathSpec(
                    port_id=f"out-{index + 1:02}", relative_path=f"part-{index + 1:02}.mkv"
                )
                for index in range(outputs)
            ),
        ),
    )


def node(identifier: str, kind: NodeDefinition, x: float, y: float) -> NodeInstance:
    return NodeInstance(
        node_id=identifier,
        type_id=kind.type_id,
        definition_version=kind.version,
        parameters={"strength": 3},
        ui_position=UiPosition(x=x, y=y),
    )


def edge(source: str, target: str, index: int = 0, *, ordinal: int | None = None) -> Edge:
    return Edge(
        source_node_id=source,
        source_port_id=f"out-{index + 1:02}",
        target_node_id=target,
        target_port_id=f"in-{index + 1:02}",
        ordinal=ordinal,
    )


def create_graphs(root: Path) -> dict[str, dict[str, str | int]]:
    """每个变体单独保存；1000 历史为合法空图 Run，不写入几何 DTO。"""
    source, transform, sink, merge = (
        definition(0, 1),
        definition(1, 1),
        definition(1, 0),
        definition(1, 1, ordered=True),
    )
    catalog = {item.type_id: item for item in (source, transform, sink, merge)}
    graphs: dict[str, Graph] = {}
    graphs["G01"] = Graph(
        nodes=(
            node("source", source, 40, 100),
            node("first", transform, 400, 100),
            node("second", transform, 760, 100),
            node("output", sink, 1120, 100),
        ),
        edges=(edge("source", "first"), edge("first", "second"), edge("second", "output")),
    )
    for name, x, y in (
        ("G02", 500, 100),
        ("G07", 500, 440),
        ("G09-overlap", 200, 100),
        ("G09-surround", 302, 100),
    ):
        graphs[name] = Graph(
            nodes=(
                node("source", source, 40, 100),
                node("obstacle", source, x, y),
                node("target", sink, 1060, 100),
            ),
            edges=(edge("source", "target"),),
        )
    # 合法的不连边孤立节点也是真实障碍；相异 xy 产生超过 60000 通道格。
    # 唯一连接远离它们、端口无遮挡，故必须是 channel_budget 而非 blocked_port。
    graphs["G09-budget"] = Graph(
        nodes=(
            node("source", source, 40, 40),
            node("target", sink, 1000, 40),
            *(
                node(f"obstacle-{index:03}", source, 2000 + index * 300, 1000 + index * 250)
                for index in range(150)
            ),
        ),
        edges=(edge("source", "target"),),
    )
    graphs["G03"] = Graph(
        nodes=tuple(
            node(identifier, kind, 40 + column * 400, 80 + row * 280)
            for identifier, kind, column, row in (
                ("source", source, 0, 0),
                ("a", transform, 1, 0),
                ("b", transform, 1, 1),
                ("c", merge, 2, 0),
                ("d", transform, 2, 1),
                ("e", merge, 3, 0),
                ("f", merge, 4, 0),
                ("output", sink, 5, 0),
            )
        ),
        edges=(
            edge("source", "a"),
            edge("source", "b"),
            edge("a", "c", ordinal=0),
            edge("b", "c", ordinal=1),
            edge("b", "d"),
            edge("c", "e", ordinal=0),
            edge("d", "e", ordinal=1),
            edge("e", "f", ordinal=0),
            edge("a", "f", ordinal=1),
            edge("f", "output"),
        ),
    )
    # Output 指正式 OutputFile，而不是用节点名字或“没有输出端口”替代产品语义。
    media = {item.type_id: item for item in built_in_media_definitions()}
    catalog.update(media)
    media_source = NodeInstance(
        node_id="source-1",
        type_id="zniku.media.source.video",
        definition_version="0.2.0",
        parameters={"source_path": str(root / "source.mkv")},
        ui_position=UiPosition(x=40, y=80),
    )
    media_merge = NodeInstance(
        node_id="merge",
        type_id="zniku.media.merge_video",
        definition_version="0.2.0",
        parameters={},
        ui_position=UiPosition(x=500, y=200),
    )
    graphs["G04-multi"] = Graph(
        nodes=(
            media_source,
            media_source.model_copy(
                update={"node_id": "source-2", "ui_position": UiPosition(x=40, y=380)}
            ),
            media_merge,
            *(
                NodeInstance(
                    node_id=f"output-{index + 1}",
                    type_id="zniku.media.output_file.video",
                    definition_version="0.2.0",
                    parameters={"mode": "reference", "overwrite": False},
                    ui_position=UiPosition(x=1000, y=80 + index * 300),
                )
                for index in range(2)
            ),
        ),
        edges=(
            *(
                Edge(
                    source_node_id=f"source-{index + 1}",
                    source_port_id="out",
                    target_node_id="merge",
                    target_port_id="videos",
                    ordinal=index,
                )
                for index in range(2)
            ),
            *(
                Edge(
                    source_node_id="merge",
                    source_port_id="video",
                    target_node_id=f"output-{index + 1}",
                    target_port_id="in",
                )
                for index in range(2)
            ),
        ),
    )
    graphs["G04-zero"] = Graph(
        nodes=(
            media_source,
            NodeInstance(
                node_id="transform",
                type_id="zniku.media.video_transform.automatic",
                definition_version="0.2.0",
                parameters={"operation": "identity"},
                ui_position=UiPosition(x=500, y=80),
            ),
        ),
        edges=(
            Edge(
                source_node_id="source-1",
                source_port_id="out",
                target_node_id="transform",
                target_port_id="video",
            ),
        ),
    )
    # 只有本次临时目录内的故意缺失输入；真实 Runtime 报错用于测量错误摘要带来的高度变化。
    graphs["G06-error"] = Graph(
        nodes=(
            media_source.model_copy(
                update={"parameters": {"source_path": str(root / "deliberately-missing.mkv")}}
            ),
            graphs["G04-zero"].nodes[1],
        ),
        edges=graphs["G04-zero"].edges,
    )
    for count in (1, 2, 6, 8, 16):
        start, middle, end = definition(0, count), definition(count, count), definition(count, 0)
        for item in (start, middle, end):
            catalog[item.type_id] = item
        graphs[f"G05-{count}"] = Graph(
            nodes=(
                node("source", start, 40, 80),
                node("ports", middle, 580, 80),
                node("target", end, 1120, 80),
            ),
            edges=tuple(
                edge(left, right, index)
                for left, right in (("source", "ports"), ("ports", "target"))
                for index in range(count)
            ),
        )
    graphs["G06"] = graphs["G03"].model_copy(
        update={
            "nodes": tuple(
                item.model_copy(
                    update={
                        "parameters": {
                            "strength": 3,
                            "options": {"description": "不应在卡片展示的对象原文"},
                            "steps": list(range(20)),
                        }
                    }
                )
                for item in graphs["G03"].nodes
            )
        }
    )
    graphs["G08"] = Graph(
        nodes=(node("source", source, 40, 120), node("merge", merge, 800, 120)),
        edges=tuple(edge("source", "merge", ordinal=index) for index in range(4)),
    )
    for count in (50, 200):
        nodes = tuple(
            node(
                f"n-{index:03}",
                source if index < 10 else merge,
                40 + index // 10 * 400,
                40 + index % 10 * 300,
            )
            for index in range(count)
        )
        edges = [
            edge(f"n-{index - 10:03}", f"n-{index:03}", ordinal=0) for index in range(10, count)
        ]
        for column in range(0, count // 10 - 3, 3):
            edges.append(edge(f"n-{column * 10:03}", f"n-{(column + 3) * 10 + 9:03}", ordinal=1))
        graphs[f"G10-{count}"] = Graph(nodes=nodes, edges=tuple(edges))
    definitions = tuple(catalog.values())
    validator = GraphValidator(definitions)
    result: dict[str, dict[str, str | int]] = {}
    for name, graph in graphs.items():
        validator.validate(graph)
        path = root / f"batch-b-{name}.zniku"
        project = Project(
            project_id=f"synthetic-{name.lower()}", name=f"合成图形 {name}", graph=Graph()
        )
        store = ProjectStore.create(path, project, ())
        if name == "G10-200":
            repository = RuntimeRepository(store)
            clock = datetime(2026, 1, 1, tzinfo=UTC)
            for index in range(1000):
                timestamp = clock + timedelta(seconds=index)
                run = Run.pending(
                    project_id=project.project_id,
                    graph_snapshot=Graph(),
                    definitions_snapshot=(),
                    created_at=timestamp,
                )
                repository.start_run(run, (), started_at=timestamp)
                repository.transition_run(run.run_id, RunState.COMPLETED, occurred_at=timestamp)
        current = project.model_copy(update={"graph": graph})
        views = tuple(
            NodeViewState(
                node_id=item.node_id,
                display_name=(
                    f"长名称分支 {item.node_id} · "
                    "这是纯合成图形中需要完整可访问但不能挤出端口的节点名称"
                    if name == "G06"
                    else f"{name} · {item.node_id}"
                ),
            )
            for item in graph.nodes
        )
        store.save(current, definitions, studio_state=StudioState(node_views=views))
        loaded = store.load()
        validator.validate(loaded.project.graph)
        assert loaded.project.graph == graph
        result[name] = {"path": str(path), "nodes": len(graph.nodes), "edges": len(graph.edges)}
    return result


def presentation_catalog(definitions: tuple[NodeDefinition, ...]) -> PresentationCatalog:
    """用正式纯数据展示目录测试长端口及复合参数摘要，不往前端注入组件或脚本。"""
    catalog = PresentationCatalog(
        categories=(
            CategoryPresentation(category_id="synthetic-b", title="合成图形验收", order=0),
        ),
        nodes=tuple(
            NodePresentation(
                type_id=item.type_id,
                definition_version=item.version,
                title=f"合成节点 {len(item.input_ports)} / {len(item.output_ports)}",
                description="仅供合成生产页面几何验收的正式节点合同。",
                category_id="synthetic-b",
                icon_token=IconToken.TRANSFORM,
                palette_level=PaletteLevel.ADVANCED,
                parameter_groups=(
                    ParameterGroupPresentation(group_id="main", title="处理设置", order=0),
                ),
                parameters=tuple(
                    ParameterPresentation(
                        parameter_pointer=f"/{key}", label=label, group_id="main", order=index
                    )
                    for index, (key, label) in enumerate(
                        (("strength", "处理强度"), ("options", "详细方案"), ("steps", "步骤列表"))
                    )
                ),
                ports=tuple(
                    PortPresentation(
                        direction=cast(Literal["input", "output"], direction),
                        port_id=port.port_id,
                        label=f"第 {index + 1} 路合成视频与保留的完整端口名称",
                    )
                    for direction, ports in (
                        ("input", item.input_ports),
                        ("output", item.output_ports),
                    )
                    for index, port in enumerate(ports)
                ),
                card_summary_paths=("/strength", "/options", "/steps"),
            )
            for item in definitions
            if item.type_id.startswith("zniku.synthetic.batch_b.")
        ),
    )
    resolution = resolve_presentation_catalog(definitions, third_party_catalogs=(catalog,))
    assert not resolution.diagnostics
    return catalog


def main() -> None:
    """仅接受纯检查开关；所有路径由测试生成，关闭 stdin 后清理本次服务和临时目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    options = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".test-tmp-batch-b-", dir=repository_root) as temporary:
        root = Path(temporary)
        projects = create_graphs(root)
        definitions = ProjectStore(root / "batch-b-G06.zniku").load().definitions
        presentations = presentation_catalog(definitions)
        if options.validate_only:
            print(
                json.dumps(
                    {
                        "validated": [
                            {"id": key, "nodes": value["nodes"], "edges": value["edges"]}
                            for key, value in projects.items()
                        ],
                        "history_rows": 1000,
                    }
                )
            )
            return
        _generate_source(root / "source.mkv")
        media = create_fixture(root, 2)
        external = create_external_fixture(root)
        platform = SyntheticPlatform((), root)
        application = ProjectServiceApplication(
            work_root=root / "attempts",
            project_data_default=True,
            definition_catalog=(
                *definitions,
                *built_in_av27_definitions(),
            ),
            python_adapters={**media_python_adapters(), **av27_python_adapters()},
            validators={**media_validators(), **av27_validators()},
            media_probe=runner_media_probe,
            artifact_quick_probe=media_artifact_quick_probe,
            third_party_presentation_catalogs=(presentations,),
        )
        server = DesktopServer(
            application,
            repository_root / "apps/studio/dist",
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
                    "external_project": str(external),
                    "media_project": str(media),
                    "small_project": projects["G10-50"]["path"],
                    "large_project": projects["G10-200"]["path"],
                    "geometry_project": projects["G05-16"]["path"],
                    "batch_b_projects": projects,
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
