"""组装 Phase 6 的 Decensoring 分支与 ``new01`` 扩展验收工作流。

本模块只使用既有 WorkflowSpec、Compiler、Core Operator 与 Installed Catalog。Runtime 无需知道任何
扩展 Engine ID；selector、passthrough 语义和 Collect 屏障仍由通用图合同表达。
"""

from __future__ import annotations

from dataclasses import dataclass

from zniku.authoring import (
    CoreNodeContractSet,
    EngineStageNodeSpec,
    FinalNodeSpec,
    PortEndpoint,
    SourceNodeSpec,
    WorkflowCompiler,
    WorkflowEdgeSpec,
    WorkflowSpec,
)
from zniku.contracts import EngineManifest, MediaKind
from zniku.engines import (
    EnginePackage,
    InstalledEngineCatalog,
    builtin_engine_packages,
    phase6_extension_packages,
)
from zniku.workflow import CoreOperatorKind, CoreOperatorNodeSpec


@dataclass(frozen=True, slots=True)
class ExtensionWorkflowBundle:
    """扩展 Workflow、已安装 package authority 与唯一 Compiler 的组装结果。"""

    spec: WorkflowSpec
    packages: tuple[EnginePackage, ...]
    catalog: InstalledEngineCatalog
    compiler: WorkflowCompiler

    @property
    def manifests(self) -> tuple[EngineManifest, ...]:
        return tuple(package.manifest for package in self.packages)


def build_phase6_extension_workflow() -> ExtensionWorkflowBundle:
    """构造只对 Chapter A Decensoring、Collect 后执行 ``new01`` 的合法 DAG。"""

    demux, mux = builtin_engine_packages()
    decensoring, new01 = phase6_extension_packages()
    packages = (demux, mux, decensoring, new01)
    catalog = InstalledEngineCatalog(packages)
    nodes = (
        SourceNodeSpec(kind="source", node_id="node.source"),
        EngineStageNodeSpec(
            kind="engine_stage",
            node_id="node.demux",
            engine=demux.descriptor.engine,
            parameters={},
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.partition",
            operator_kind=CoreOperatorKind.PARTITION,
            media_kind=MediaKind.VIDEO,
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.select.chapter_a",
            operator_kind=CoreOperatorKind.SELECT,
            media_kind=MediaKind.VIDEO,
            selected_member_ids=("chapter.a",),
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.decensoring",
            operator_kind=CoreOperatorKind.MAP,
            media_kind=MediaKind.VIDEO,
            engine=decensoring.descriptor.engine,
            parameters={"model_name": "Jasna Synthetic", "model_version": "0.1.0"},
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.collect",
            operator_kind=CoreOperatorKind.COLLECT,
            media_kind=MediaKind.VIDEO,
        ),
        CoreOperatorNodeSpec(
            kind="core_operator",
            node_id="node.reduce",
            operator_kind=CoreOperatorKind.REDUCE,
            media_kind=MediaKind.VIDEO,
        ),
        EngineStageNodeSpec(
            kind="engine_stage",
            node_id="node.new01",
            engine=new01.descriptor.engine,
            parameters={"profile": "phase6-demo"},
        ),
        EngineStageNodeSpec(
            kind="engine_stage",
            node_id="node.mux",
            engine=mux.descriptor.engine,
            parameters={"container": "matroska"},
        ),
        FinalNodeSpec(kind="final", node_id="node.final"),
    )
    edges = tuple(
        WorkflowEdgeSpec(
            edge_id=f"edge.{edge_id}",
            source=PortEndpoint(node_id=source_node, port_id=source_port),
            target=PortEndpoint(node_id=target_node, port_id=target_port),
        )
        for edge_id, source_node, source_port, target_node, target_port in (
            ("source-demux", "node.source", "program", "node.demux", "program_in"),
            ("demux-partition", "node.demux", "video_out", "node.partition", "in"),
            (
                "partition-select",
                "node.partition",
                "out",
                "node.select.chapter_a",
                "in",
            ),
            (
                "select-decensoring",
                "node.select.chapter_a",
                "selected",
                "node.decensoring",
                "in",
            ),
            (
                "decensoring-collect",
                "node.decensoring",
                "out",
                "node.collect",
                "processed",
            ),
            (
                "remainder-collect",
                "node.select.chapter_a",
                "remainder",
                "node.collect",
                "remainder",
            ),
            ("collect-reduce", "node.collect", "out", "node.reduce", "in"),
            ("reduce-new01", "node.reduce", "out", "node.new01", "video_in"),
            ("new01-mux", "node.new01", "video_out", "node.mux", "video_in"),
            ("audio-mux", "node.demux", "audio_out", "node.mux", "audio_in"),
            ("mux-final", "node.mux", "program_out", "node.final", "program"),
        )
    )
    spec = WorkflowSpec(
        workflow_contract_version="0.2.0",
        workflow_id="workflow.phase6.extension.0.1.0",
        nodes=nodes,
        edges=edges,
    )
    compiler = WorkflowCompiler(catalog, CoreNodeContractSet.phase_2a())
    return ExtensionWorkflowBundle(
        spec=spec,
        packages=packages,
        catalog=catalog,
        compiler=compiler,
    )
