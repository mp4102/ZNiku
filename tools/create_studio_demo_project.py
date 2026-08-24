"""创建 Phase 3 Studio 的纯合成 `.zniku` 验收工程，不读取或提交真实媒体。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zniku.graph import (
    CommandExecutorSpec,
    Edge,
    ExecutionMode,
    Graph,
    ManualExternalExecutorSpec,
    NodeDefinition,
    NodeInstance,
    PortSpec,
    UiPosition,
)
from zniku.project import Project, ProjectStore

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "tools" / "studio_demo_adapter.py"


def definitions() -> tuple[NodeDefinition, ...]:
    """返回只处理 DataFile 的四个 Phase 3 合成定义。"""

    data_in = PortSpec(port_id="data", data_type="DataFile", required=True)
    data_out = PortSpec(port_id="data", data_type="DataFile")
    return (
        NodeDefinition(
            type_id="demo.text_source",
            version="0.2.0",
            output_ports=(data_out,),
            parameter_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"message": {"type": "string", "minLength": 1}},
                "required": ["message"],
                "additionalProperties": False,
            },
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=CommandExecutorSpec(
                executable=sys.executable,
                argv=(str(ADAPTER), "source", "{param:message}", "{output:data}"),
            ),
        ),
        NodeDefinition(
            type_id="demo.text_transform",
            version="0.2.0",
            input_ports=(data_in,),
            output_ports=(data_out,),
            parameter_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"uppercase": {"type": "boolean"}},
                "required": ["uppercase"],
                "additionalProperties": False,
            },
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=CommandExecutorSpec(
                executable=sys.executable,
                argv=(
                    str(ADAPTER),
                    "transform",
                    "{input:data}",
                    "{output:data}",
                    "{param:uppercase}",
                ),
            ),
        ),
        NodeDefinition(
            type_id="demo.text_review",
            version="0.2.0",
            input_ports=(data_in,),
            output_ports=(data_out,),
            execution_mode=ExecutionMode.MANUAL_EXTERNAL,
            executor=ManualExternalExecutorSpec(
                instructions="将输入文本复制或修改到声明目标路径，然后在 Studio 点击 Submit。"
            ),
        ),
        NodeDefinition(
            type_id="demo.text_publish",
            version="0.2.0",
            input_ports=(data_in,),
            output_ports=(data_out,),
            execution_mode=ExecutionMode.AUTOMATIC,
            executor=CommandExecutorSpec(
                executable=sys.executable,
                argv=(str(ADAPTER), "copy", "{input:data}", "{output:data}"),
            ),
        ),
    )


def graph() -> Graph:
    """构造 automatic→automatic→manual_external→automatic 的普通 DAG。"""

    nodes = (
        NodeInstance(
            node_id="source",
            type_id="demo.text_source",
            definition_version="0.2.0",
            parameters={"message": "ZNIKU Phase 3"},
            ui_position=UiPosition(x=80, y=180),
        ),
        NodeInstance(
            node_id="transform",
            type_id="demo.text_transform",
            definition_version="0.2.0",
            parameters={"uppercase": True},
            ui_position=UiPosition(x=380, y=180),
        ),
        NodeInstance(
            node_id="review",
            type_id="demo.text_review",
            definition_version="0.2.0",
            ui_position=UiPosition(x=680, y=180),
        ),
        NodeInstance(
            node_id="publish",
            type_id="demo.text_publish",
            definition_version="0.2.0",
            ui_position=UiPosition(x=980, y=180),
        ),
    )
    edges = tuple(
        Edge(
            source_node_id=source,
            source_port_id="data",
            target_node_id=target,
            target_port_id="data",
        )
        for source, target in (
            ("source", "transform"),
            ("transform", "review"),
            ("review", "publish"),
        )
    )
    return Graph(nodes=nodes, edges=edges)


def main() -> int:
    """创建全新的 demo Project；既有目标由 ProjectStore 拒绝覆盖。"""

    parser = argparse.ArgumentParser(description="创建 ZNIKU Studio Phase 3 合成工程")
    parser.add_argument("path", type=Path)
    arguments = parser.parse_args()
    project = Project(project_id="demo.phase3", name="ZNIKU Phase 3 Demo", graph=graph())
    ProjectStore.create(arguments.path, project, definitions())
    print(arguments.path.resolve(strict=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
