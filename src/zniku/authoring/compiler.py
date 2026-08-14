"""实现只读、无媒体副作用的 Workflow Compiler front-end。

Compiler 只读取结构合法的 WorkflowSpec、精确 Manifest authority 与 Python core node contracts，返回
确定性 diagnostics。它不会修改草稿、注入参数默认值、创建 ExecutionPlan 或访问媒体文件。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Protocol

from zniku.contracts import (
    ArtifactType,
    Cardinality,
    ContractViolation,
    EngineBinding,
    EngineManifest,
    PortSpec,
    assert_ports_compatible,
)
from zniku.workflow import (
    CoreOperatorKind,
    CoreOperatorNodeSpec,
    operator_input_ports,
    operator_output_ports,
)

from .models import (
    CoreNodeContractSet,
    Diagnostic,
    DiagnosticPhase,
    DiagnosticSeverity,
    EdgeEntityRef,
    EngineStageNodeSpec,
    FinalNodeSpec,
    NodeEntityRef,
    ParameterEntityRef,
    PortDirection,
    PortEntityRef,
    SourceNodeSpec,
    SpecValidationResult,
    ValidationOutcome,
    WorkflowEdgeSpec,
    WorkflowEntityRef,
    WorkflowNodeSpec,
    WorkflowSpec,
)


class ManifestCatalog(Protocol):
    """Compiler 所需的最小只读 Manifest authority 接口。"""

    def resolve(self, binding: EngineBinding) -> EngineManifest | None:
        """只在 engine ID、精确版本与 digest 全部匹配时返回 Manifest。"""


class InMemoryManifestCatalog:
    """以已校验 Manifest 构造的纯内存 authority；不实现安装或发现。"""

    def __init__(self, manifests: Iterable[EngineManifest]) -> None:
        by_binding: dict[tuple[str, str, str], EngineManifest] = {}
        for manifest in manifests:
            key = (manifest.engine_id, manifest.engine_version, manifest.sha256_digest())
            if key in by_binding:
                raise ValueError("E_MANIFEST_AUTHORITY_DUPLICATE: 精确 Manifest authority 不得重复")
            by_binding[key] = manifest
        self._by_binding = by_binding

    def resolve(self, binding: EngineBinding) -> EngineManifest | None:
        return self._by_binding.get(
            (binding.engine_id, binding.engine_version, binding.manifest_digest)
        )

    def manifests(self) -> tuple[EngineManifest, ...]:
        """返回按稳定身份排序的只读 Manifest 集合，供受控投影使用。"""

        return tuple(
            sorted(
                self._by_binding.values(),
                key=lambda item: (item.engine_id, item.engine_version, item.sha256_digest()),
            )
        )


def _occurrence(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"occ.{hashlib.sha256(payload).hexdigest()[:24]}"


def _error(
    *,
    code: str,
    occurrence: str,
    phase: DiagnosticPhase,
    entity: WorkflowEntityRef | NodeEntityRef | PortEntityRef | EdgeEntityRef | ParameterEntityRef,
    message: str,
    related: tuple[
        WorkflowEntityRef | NodeEntityRef | PortEntityRef | EdgeEntityRef | ParameterEntityRef,
        ...,
    ] = (),
    details: Mapping[str, str | int | bool | None] | None = None,
) -> Diagnostic:
    return Diagnostic.create(
        stable_code=code,
        occurrence_key=occurrence,
        severity=DiagnosticSeverity.ERROR,
        phase=phase,
        entity_ref=entity,
        related_refs=related,
        message=message,
        details=details,
    )


def _strongly_connected_components(
    node_ids: tuple[str, ...], edges: tuple[WorkflowEdgeSpec, ...]
) -> tuple[tuple[str, ...], ...]:
    """按稳定 node ID 运行 Tarjan，避免枚举指数级环路。"""

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    node_set = set(node_ids)
    for edge in edges:
        if edge.source.node_id in node_set and edge.target.node_id in node_set:
            adjacency[edge.source.node_id].append(edge.target.node_id)
    for targets in adjacency.values():
        targets.sort()

    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[tuple[str, ...]] = []

    def visit(node_id: str) -> None:
        nonlocal index
        indices[node_id] = index
        lowlinks[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)

        for target_id in adjacency[node_id]:
            if target_id not in indices:
                visit(target_id)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target_id])
            elif target_id in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indices[target_id])

        if lowlinks[node_id] != indices[node_id]:
            return
        component: list[str] = []
        while True:
            item = stack.pop()
            on_stack.remove(item)
            component.append(item)
            if item == node_id:
                break
        components.append(tuple(sorted(component)))

    for node_id in sorted(node_ids):
        if node_id not in indices:
            visit(node_id)
    return tuple(sorted(components))


class WorkflowCompiler:
    """执行 Phase 2A parse 后的 graph 与 manifest 静态校验。"""

    def __init__(
        self,
        manifest_catalog: ManifestCatalog,
        core_contracts: CoreNodeContractSet,
    ) -> None:
        self._manifest_catalog = manifest_catalog
        self._core_contracts = core_contracts

    @property
    def core_contracts(self) -> CoreNodeContractSet:
        return self._core_contracts

    def validate(self, spec: WorkflowSpec) -> SpecValidationResult:
        """对相同 authority 输入返回确定排序的纯验证结果。"""

        diagnostics: list[Diagnostic] = []
        nodes = {node.node_id: node for node in spec.nodes}
        manifests: dict[str, EngineManifest | None] = {}

        for node in spec.nodes:
            if isinstance(node, EngineStageNodeSpec) or (
                isinstance(node, CoreOperatorNodeSpec)
                and node.operator_kind is CoreOperatorKind.MAP
                and node.engine is not None
            ):
                engine_binding = node.engine
                parameters = node.parameters
            else:
                continue
            assert engine_binding is not None
            manifest = self._manifest_catalog.resolve(engine_binding)
            manifests[node.node_id] = manifest
            if manifest is None:
                diagnostics.append(
                    _error(
                        code="E_ENGINE_BINDING_UNKNOWN",
                        occurrence=_occurrence(node.node_id, engine_binding.manifest_digest),
                        phase=DiagnosticPhase.MANIFEST,
                        entity=NodeEntityRef(kind="node", node_id=node.node_id),
                        message="EngineBinding 无法解析到精确 Manifest authority。",
                        details={
                            "engine_id": engine_binding.engine_id,
                            "engine_version": engine_binding.engine_version,
                            "manifest_digest": engine_binding.manifest_digest,
                        },
                    )
                )
                continue
            try:
                manifest.validate_parameters(parameters)
            except ContractViolation as error:
                diagnostics.append(
                    _error(
                        code="E_ENGINE_PARAMETERS_INVALID",
                        occurrence=_occurrence(node.node_id, error.code),
                        phase=DiagnosticPhase.MANIFEST,
                        entity=ParameterEntityRef(
                            kind="parameter", node_id=node.node_id, json_pointer=""
                        ),
                        message="Engine 参数不满足绑定 Manifest 的 Schema。",
                        details={"reason_code": error.code},
                    )
                )
            if isinstance(node, CoreOperatorNodeSpec) and not self._map_manifest_compatible(
                node, manifest
            ):
                diagnostics.append(
                    _error(
                        code="E_OPERATOR_MAP_ENGINE_CONTRACT",
                        occurrence=_occurrence(node.node_id, manifest.sha256_digest()),
                        phase=DiagnosticPhase.MANIFEST,
                        entity=NodeEntityRef(kind="node", node_id=node.node_id),
                        message=(
                            "Map Engine 必须是同 media kind、chapter scope 的单值一入一出合同。"
                        ),
                    )
                )

        sources = tuple(node for node in spec.nodes if isinstance(node, SourceNodeSpec))
        finals = tuple(node for node in spec.nodes if isinstance(node, FinalNodeSpec))
        workflow_ref = WorkflowEntityRef(kind="workflow", workflow_id=spec.workflow_id)
        if not sources:
            diagnostics.append(
                _error(
                    code="E_GRAPH_SOURCE_REQUIRED",
                    occurrence="source.required",
                    phase=DiagnosticPhase.GRAPH,
                    entity=workflow_ref,
                    message="WorkflowSpec 至少需要一个 Source。",
                )
            )
        if len(finals) != 1:
            diagnostics.append(
                _error(
                    code="E_GRAPH_FINAL_CARDINALITY",
                    occurrence=f"final.count.{len(finals)}",
                    phase=DiagnosticPhase.GRAPH,
                    entity=workflow_ref,
                    related=tuple(
                        NodeEntityRef(kind="node", node_id=node.node_id) for node in finals
                    ),
                    message="WorkflowSpec 必须恰好包含一个 Final。",
                    details={"actual_count": len(finals)},
                )
            )

        resolved_edges: list[tuple[WorkflowEdgeSpec, PortSpec | None, PortSpec | None]] = []
        endpoint_pairs: dict[tuple[str, str, str, str], list[WorkflowEdgeSpec]] = defaultdict(list)
        for edge in spec.edges:
            source_node = nodes.get(edge.source.node_id)
            target_node = nodes.get(edge.target.node_id)
            edge_ref = EdgeEntityRef(kind="edge", edge_id=edge.edge_id)
            if source_node is None:
                diagnostics.append(
                    _error(
                        code="E_GRAPH_SOURCE_NODE_UNKNOWN",
                        occurrence=_occurrence(edge.edge_id, edge.source.node_id),
                        phase=DiagnosticPhase.GRAPH,
                        entity=edge_ref,
                        message="Edge source 引用了未知 node。",
                        details={"node_id": edge.source.node_id},
                    )
                )
            if target_node is None:
                diagnostics.append(
                    _error(
                        code="E_GRAPH_TARGET_NODE_UNKNOWN",
                        occurrence=_occurrence(edge.edge_id, edge.target.node_id),
                        phase=DiagnosticPhase.GRAPH,
                        entity=edge_ref,
                        message="Edge target 引用了未知 node。",
                        details={"node_id": edge.target.node_id},
                    )
                )
            if edge.source.node_id == edge.target.node_id:
                diagnostics.append(
                    _error(
                        code="E_GRAPH_SELF_EDGE",
                        occurrence=edge.edge_id,
                        phase=DiagnosticPhase.GRAPH,
                        entity=edge_ref,
                        message="Workflow 不允许 node 自连接。",
                    )
                )

            source_port = self._resolve_port(
                source_node, edge.source.port_id, PortDirection.OUTPUT, manifests
            )
            target_port = self._resolve_port(
                target_node, edge.target.port_id, PortDirection.INPUT, manifests
            )
            if (
                source_node is not None
                and self._port_authority_available(source_node, manifests)
                and source_port is None
            ):
                diagnostics.append(
                    _error(
                        code="E_GRAPH_SOURCE_PORT_UNKNOWN",
                        occurrence=_occurrence(edge.edge_id, edge.source.port_id),
                        phase=DiagnosticPhase.GRAPH,
                        entity=PortEntityRef(
                            kind="port",
                            node_id=edge.source.node_id,
                            port_id=edge.source.port_id,
                            direction=PortDirection.OUTPUT,
                        ),
                        related=(edge_ref,),
                        message="Edge source 不是该 node 的已知 output port。",
                    )
                )
            if (
                target_node is not None
                and self._port_authority_available(target_node, manifests)
                and target_port is None
            ):
                diagnostics.append(
                    _error(
                        code="E_GRAPH_TARGET_PORT_UNKNOWN",
                        occurrence=_occurrence(edge.edge_id, edge.target.port_id),
                        phase=DiagnosticPhase.GRAPH,
                        entity=PortEntityRef(
                            kind="port",
                            node_id=edge.target.node_id,
                            port_id=edge.target.port_id,
                            direction=PortDirection.INPUT,
                        ),
                        related=(edge_ref,),
                        message="Edge target 不是该 node 的已知 input port。",
                    )
                )
            if source_port is not None and target_port is not None:
                try:
                    assert_ports_compatible(source_port, target_port)
                except ContractViolation as error:
                    diagnostics.append(
                        _error(
                            code=error.code,
                            occurrence=_occurrence(edge.edge_id, error.code),
                            phase=DiagnosticPhase.GRAPH,
                            entity=edge_ref,
                            related=(
                                PortEntityRef(
                                    kind="port",
                                    node_id=edge.source.node_id,
                                    port_id=edge.source.port_id,
                                    direction=PortDirection.OUTPUT,
                                ),
                                PortEntityRef(
                                    kind="port",
                                    node_id=edge.target.node_id,
                                    port_id=edge.target.port_id,
                                    direction=PortDirection.INPUT,
                                ),
                            ),
                            message="Edge 两端 typed port 不兼容。",
                            details={"reason_code": error.code},
                        )
                    )
            resolved_edges.append((edge, source_port, target_port))
            endpoint_pairs[
                (
                    edge.source.node_id,
                    edge.source.port_id,
                    edge.target.node_id,
                    edge.target.port_id,
                )
            ].append(edge)

        for endpoint_pair, duplicate_edges in sorted(endpoint_pairs.items()):
            if len(duplicate_edges) < 2:
                continue
            diagnostics.append(
                _error(
                    code="E_GRAPH_ENDPOINT_PAIR_DUPLICATE",
                    occurrence=_occurrence(*endpoint_pair),
                    phase=DiagnosticPhase.GRAPH,
                    entity=workflow_ref,
                    related=tuple(
                        EdgeEntityRef(kind="edge", edge_id=edge.edge_id) for edge in duplicate_edges
                    ),
                    message="同一 source/target endpoint pair 不得重复连接。",
                )
            )

        incoming: dict[tuple[str, str], list[WorkflowEdgeSpec]] = defaultdict(list)
        outgoing_by_node: dict[str, list[WorkflowEdgeSpec]] = defaultdict(list)
        incoming_by_node: dict[str, list[WorkflowEdgeSpec]] = defaultdict(list)
        for edge, _source_port, target_port in resolved_edges:
            outgoing_by_node[edge.source.node_id].append(edge)
            incoming_by_node[edge.target.node_id].append(edge)
            if target_port is not None:
                incoming[(edge.target.node_id, edge.target.port_id)].append(edge)

        for node in spec.nodes:
            for port in self._input_ports(node, manifests):
                count = len(incoming[(node.node_id, port.port_id)])
                valid = count <= 1 if port.cardinality is Cardinality.OPTIONAL else count == 1
                if valid:
                    continue
                diagnostics.append(
                    _error(
                        code="E_GRAPH_INPUT_CARDINALITY",
                        occurrence=_occurrence(node.node_id, port.port_id, str(count)),
                        phase=DiagnosticPhase.GRAPH,
                        entity=PortEntityRef(
                            kind="port",
                            node_id=node.node_id,
                            port_id=port.port_id,
                            direction=PortDirection.INPUT,
                        ),
                        related=tuple(
                            EdgeEntityRef(kind="edge", edge_id=edge.edge_id)
                            for edge in incoming[(node.node_id, port.port_id)]
                        ),
                        message="Input port 的连接数量不满足 cardinality。",
                        details={
                            "cardinality": port.cardinality.value,
                            "actual_count": count,
                        },
                    )
                )

        for source in sources:
            if incoming_by_node[source.node_id]:
                diagnostics.append(
                    _error(
                        code="E_GRAPH_SOURCE_HAS_INPUT",
                        occurrence=source.node_id,
                        phase=DiagnosticPhase.GRAPH,
                        entity=NodeEntityRef(kind="node", node_id=source.node_id),
                        related=tuple(
                            EdgeEntityRef(kind="edge", edge_id=edge.edge_id)
                            for edge in incoming_by_node[source.node_id]
                        ),
                        message="Source 不得有入边。",
                    )
                )
        for final in finals:
            if outgoing_by_node[final.node_id]:
                diagnostics.append(
                    _error(
                        code="E_GRAPH_FINAL_HAS_OUTPUT",
                        occurrence=final.node_id,
                        phase=DiagnosticPhase.GRAPH,
                        entity=NodeEntityRef(kind="node", node_id=final.node_id),
                        related=tuple(
                            EdgeEntityRef(kind="edge", edge_id=edge.edge_id)
                            for edge in outgoing_by_node[final.node_id]
                        ),
                        message="Final 不得有出边。",
                    )
                )

        for node in spec.nodes:
            if not isinstance(node, FinalNodeSpec) and not outgoing_by_node[node.node_id]:
                diagnostics.append(
                    _error(
                        code="E_GRAPH_NON_FINAL_TERMINAL",
                        occurrence=node.node_id,
                        phase=DiagnosticPhase.GRAPH,
                        entity=NodeEntityRef(kind="node", node_id=node.node_id),
                        message="Final 必须是全图唯一终端类别。",
                    )
                )

        self._append_cycle_diagnostics(spec, diagnostics)
        if sources and len(finals) == 1:
            self._append_path_diagnostics(
                spec,
                tuple(source.node_id for source in sources),
                finals[0].node_id,
                diagnostics,
            )

        outcome = (
            ValidationOutcome.INVALID
            if any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics)
            else ValidationOutcome.AUTHORING_VALID
        )
        return SpecValidationResult(
            workflow_contract_version=spec.workflow_contract_version,
            compiler_contract_version="0.1.0",
            diagnostic_contract_version="0.1.0",
            spec_digest=spec.sha256_digest(),
            core_node_contract_digest=self._core_contracts.sha256_digest(),
            outcome=outcome,
            diagnostics=tuple(diagnostics),
        )

    def _resolve_port(
        self,
        node: WorkflowNodeSpec | None,
        port_id: str,
        direction: PortDirection,
        manifests: Mapping[str, EngineManifest | None],
    ) -> PortSpec | None:
        if node is None:
            return None
        ports: tuple[PortSpec, ...]
        if isinstance(node, SourceNodeSpec):
            ports = self._core_contracts.source_outputs if direction is PortDirection.OUTPUT else ()
        elif isinstance(node, FinalNodeSpec):
            ports = self._core_contracts.final_inputs if direction is PortDirection.INPUT else ()
        elif isinstance(node, CoreOperatorNodeSpec):
            ports = (
                operator_input_ports(node)
                if direction is PortDirection.INPUT
                else operator_output_ports(node)
            )
        else:
            manifest = manifests.get(node.node_id)
            if manifest is None:
                return None
            contracts = manifest.inputs if direction is PortDirection.INPUT else manifest.outputs
            ports = tuple(contract.port for contract in contracts)
        return next((port for port in ports if port.port_id == port_id), None)

    def _input_ports(
        self,
        node: WorkflowNodeSpec,
        manifests: Mapping[str, EngineManifest | None],
    ) -> tuple[PortSpec, ...]:
        if isinstance(node, SourceNodeSpec):
            return ()
        if isinstance(node, FinalNodeSpec):
            return self._core_contracts.final_inputs
        if isinstance(node, CoreOperatorNodeSpec):
            return operator_input_ports(node)
        manifest = manifests.get(node.node_id)
        if manifest is None:
            return ()
        return tuple(contract.port for contract in manifest.inputs)

    @staticmethod
    def _port_authority_available(
        node: WorkflowNodeSpec,
        manifests: Mapping[str, EngineManifest | None],
    ) -> bool:
        return not isinstance(node, EngineStageNodeSpec) or manifests.get(node.node_id) is not None

    @staticmethod
    def _map_manifest_compatible(node: CoreOperatorNodeSpec, manifest: EngineManifest) -> bool:
        if len(manifest.inputs) != 1 or len(manifest.outputs) != 1:
            return False
        ports = (manifest.inputs[0].port, manifest.outputs[0].port)
        return all(
            port.artifact_type is ArtifactType.MEDIA
            and port.media_kind is node.media_kind
            and port.scope.value == "chapter"
            and port.cardinality is Cardinality.ONE
            for port in ports
        )

    def _append_cycle_diagnostics(self, spec: WorkflowSpec, diagnostics: list[Diagnostic]) -> None:
        self_edges = {
            edge.source.node_id for edge in spec.edges if edge.source.node_id == edge.target.node_id
        }
        for component in _strongly_connected_components(
            tuple(node.node_id for node in spec.nodes), spec.edges
        ):
            if len(component) == 1 and component[0] not in self_edges:
                continue
            diagnostics.append(
                _error(
                    code="E_GRAPH_CYCLE",
                    occurrence=_occurrence(*component),
                    phase=DiagnosticPhase.GRAPH,
                    entity=NodeEntityRef(kind="node", node_id=component[0]),
                    related=tuple(
                        NodeEntityRef(kind="node", node_id=node_id) for node_id in component[1:]
                    ),
                    message="WorkflowSpec 必须保持有向无环。",
                    details={"member_count": len(component)},
                )
            )

    def _append_path_diagnostics(
        self,
        spec: WorkflowSpec,
        source_ids: tuple[str, ...],
        final_id: str,
        diagnostics: list[Diagnostic],
    ) -> None:
        adjacency: dict[str, set[str]] = defaultdict(set)
        reverse: dict[str, set[str]] = defaultdict(set)
        node_ids = {node.node_id for node in spec.nodes}
        for edge in spec.edges:
            if edge.source.node_id in node_ids and edge.target.node_id in node_ids:
                adjacency[edge.source.node_id].add(edge.target.node_id)
                reverse[edge.target.node_id].add(edge.source.node_id)

        def reachable(starts: Iterable[str], graph: Mapping[str, set[str]]) -> set[str]:
            seen: set[str] = set()
            stack = sorted(starts, reverse=True)
            while stack:
                node_id = stack.pop()
                if node_id in seen:
                    continue
                seen.add(node_id)
                stack.extend(sorted(graph.get(node_id, set()), reverse=True))
            return seen

        from_source = reachable(source_ids, adjacency)
        to_final = reachable((final_id,), reverse)
        valid_path_nodes = from_source & to_final
        for node in spec.nodes:
            if node.node_id in valid_path_nodes:
                continue
            diagnostics.append(
                _error(
                    code="E_GRAPH_NODE_NOT_ON_SOURCE_FINAL_PATH",
                    occurrence=node.node_id,
                    phase=DiagnosticPhase.GRAPH,
                    entity=NodeEntityRef(kind="node", node_id=node.node_id),
                    message="每个正式 node 都必须位于某个 Source 通往唯一 Final 的路径上。",
                )
            )
