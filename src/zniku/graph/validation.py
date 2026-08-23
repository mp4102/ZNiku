"""实现 ZNIKU 0.2.0 Graph Core 的确定性、失败关闭验证器。

验证器只检查基线冻结的图结构规则。它不会生成 ExecutionPlan、canonical digest 或媒体 Evidence，
也不会把 ``MediaFile`` 等端口类型做隐式继承或转换；两个端口类型只有精确相等才兼容。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from .models import Cardinality, Edge, Graph, NodeDefinition, NodeInstance, PortSpec


@dataclass(frozen=True, slots=True)
class GraphViolation:
    """表示一个具有稳定代码、位置与中文说明的图错误。"""

    code: str
    path: str
    message: str


class GraphValidationError(ValueError):
    """聚合一次 Graph 验证中发现的全部确定性错误。"""

    def __init__(self, violations: tuple[GraphViolation, ...]) -> None:
        if not violations:
            raise ValueError("GraphValidationError 至少需要一个 violation")
        self.violations = violations
        details = "; ".join(f"{item.code} ({item.path}): {item.message}" for item in violations)
        super().__init__(details)


class GraphValidator:
    """使用一组精确版本 NodeDefinition 验证 Graph。

    Definition catalog 在构造时快照化。相同 ``type_id/version`` 重复声明会立即失败，确保节点实例永远
    只解析到一个定义；相同 ``type_id`` 的多个精确版本可以并存。
    """

    def __init__(self, definitions: Iterable[NodeDefinition]) -> None:
        catalog: dict[tuple[str, str], NodeDefinition] = {}
        for definition in definitions:
            key = (definition.type_id, definition.version)
            if key in catalog:
                raise ValueError(
                    "E_DEFINITION_DUPLICATE: "
                    f"NodeDefinition {definition.type_id}@{definition.version} 重复"
                )
            catalog[key] = definition
        self._definitions: Mapping[tuple[str, str], NodeDefinition] = catalog

    def validate(self, graph: Graph) -> None:
        """验证 Graph；存在任何错误时失败且不返回部分合法结果。"""

        violations = self.inspect(graph)
        if violations:
            raise GraphValidationError(violations)

    def inspect(self, graph: Graph) -> tuple[GraphViolation, ...]:
        """按结构、边、输入约束、参数和 DAG 的固定顺序返回全部错误。"""

        violations: list[GraphViolation] = []
        nodes_by_id: dict[str, NodeInstance] = {}
        duplicate_node_ids: set[str] = set()
        for index, node in enumerate(graph.nodes):
            if node.node_id in nodes_by_id:
                duplicate_node_ids.add(node.node_id)
                violations.append(
                    GraphViolation(
                        "E_NODE_DUPLICATE",
                        f"nodes[{index}].node_id",
                        f"node_id {node.node_id!r} 重复",
                    )
                )
            else:
                nodes_by_id[node.node_id] = node

        definitions_by_node: dict[str, NodeDefinition] = {}
        for index, node in enumerate(graph.nodes):
            if node.node_id in duplicate_node_ids:
                continue
            definition = self._definitions.get((node.type_id, node.definition_version))
            if definition is None:
                violations.append(
                    GraphViolation(
                        "E_DEFINITION_UNKNOWN",
                        f"nodes[{index}].definition_version",
                        f"没有 {node.type_id}@{node.definition_version} 的精确定义",
                    )
                )
                continue
            definitions_by_node[node.node_id] = definition
            violations.extend(self._parameter_violations(node, definition, index=index))

        incoming: dict[tuple[str, str], list[tuple[int, Edge]]] = defaultdict(list)
        valid_adjacency_edges: list[Edge] = []
        for index, edge in enumerate(graph.edges):
            source = nodes_by_id.get(edge.source_node_id)
            target = nodes_by_id.get(edge.target_node_id)
            edge_path = f"edges[{index}]"
            if source is None or edge.source_node_id in duplicate_node_ids:
                violations.append(
                    GraphViolation(
                        "E_EDGE_SOURCE_NODE_UNKNOWN",
                        f"{edge_path}.source_node_id",
                        f"source node {edge.source_node_id!r} 不存在或不唯一",
                    )
                )
            if target is None or edge.target_node_id in duplicate_node_ids:
                violations.append(
                    GraphViolation(
                        "E_EDGE_TARGET_NODE_UNKNOWN",
                        f"{edge_path}.target_node_id",
                        f"target node {edge.target_node_id!r} 不存在或不唯一",
                    )
                )
            if (
                source is None
                or target is None
                or edge.source_node_id in duplicate_node_ids
                or edge.target_node_id in duplicate_node_ids
            ):
                continue

            source_definition = definitions_by_node.get(source.node_id)
            target_definition = definitions_by_node.get(target.node_id)
            if source_definition is None or target_definition is None:
                continue
            output_port = self._find_port(source_definition.output_ports, edge.source_port_id)
            input_port = self._find_port(target_definition.input_ports, edge.target_port_id)
            if output_port is None:
                violations.append(
                    GraphViolation(
                        "E_EDGE_SOURCE_PORT_UNKNOWN",
                        f"{edge_path}.source_port_id",
                        f"{source.type_id} 没有 output port {edge.source_port_id!r}",
                    )
                )
            if input_port is None:
                violations.append(
                    GraphViolation(
                        "E_EDGE_TARGET_PORT_UNKNOWN",
                        f"{edge_path}.target_port_id",
                        f"{target.type_id} 没有 input port {edge.target_port_id!r}",
                    )
                )
            if output_port is None or input_port is None:
                continue

            if output_port.data_type != input_port.data_type:
                violations.append(
                    GraphViolation(
                        "E_PORT_TYPE_INCOMPATIBLE",
                        edge_path,
                        f"{output_port.data_type} output 不能连接到 {input_port.data_type} input",
                    )
                )
            incoming[(target.node_id, input_port.port_id)].append((index, edge))
            valid_adjacency_edges.append(edge)

        violations.extend(
            self._input_violations(
                graph, definitions_by_node=definitions_by_node, incoming=incoming
            )
        )
        if self._has_cycle(tuple(nodes_by_id), valid_adjacency_edges):
            violations.append(GraphViolation("E_GRAPH_CYCLE", "edges", "Graph 必须是有向无环图"))
        return tuple(violations)

    @staticmethod
    def _find_port(ports: tuple[PortSpec, ...], port_id: str) -> PortSpec | None:
        return next((port for port in ports if port.port_id == port_id), None)

    @staticmethod
    def _parameter_violations(
        node: NodeInstance,
        definition: NodeDefinition,
        *,
        index: int,
    ) -> tuple[GraphViolation, ...]:
        validator = Draft202012Validator(definition.parameter_schema)
        errors = sorted(
            validator.iter_errors(node.parameters),
            key=lambda error: (
                tuple(str(part) for part in error.absolute_path),
                tuple(str(part) for part in error.absolute_schema_path),
            ),
        )
        violations: list[GraphViolation] = []
        for error in errors:
            parameter_path = ".".join(str(part) for part in error.absolute_path)
            path = f"nodes[{index}].parameters"
            if parameter_path:
                path = f"{path}.{parameter_path}"
            violations.append(GraphViolation("E_PARAMETERS_INVALID", path, error.message))
        return tuple(violations)

    @staticmethod
    def _input_violations(
        graph: Graph,
        *,
        definitions_by_node: Mapping[str, NodeDefinition],
        incoming: Mapping[tuple[str, str], list[tuple[int, Edge]]],
    ) -> tuple[GraphViolation, ...]:
        violations: list[GraphViolation] = []
        for node_index, node in enumerate(graph.nodes):
            definition = definitions_by_node.get(node.node_id)
            if definition is None:
                continue
            for port in definition.input_ports:
                bindings = incoming.get((node.node_id, port.port_id), [])
                path = f"nodes[{node_index}].inputs.{port.port_id}"
                if port.required and not bindings:
                    violations.append(
                        GraphViolation(
                            "E_REQUIRED_INPUT_MISSING",
                            path,
                            f"required input {port.port_id!r} 未连接",
                        )
                    )
                    continue
                if port.cardinality is Cardinality.ONE:
                    if len(bindings) > 1:
                        violations.append(
                            GraphViolation(
                                "E_INPUT_MULTIPLE_EDGES",
                                path,
                                f"one input {port.port_id!r} 只能有一条入边",
                            )
                        )
                    for edge_index, edge in bindings:
                        if edge.ordinal is not None:
                            violations.append(
                                GraphViolation(
                                    "E_ORDINAL_NOT_ALLOWED",
                                    f"edges[{edge_index}].ordinal",
                                    "ordinal 只用于 ordered_many input",
                                )
                            )
                    continue

                ordinals: list[int] = []
                for edge_index, edge in bindings:
                    if edge.ordinal is None:
                        violations.append(
                            GraphViolation(
                                "E_ORDINAL_REQUIRED",
                                f"edges[{edge_index}].ordinal",
                                "ordered_many input 的每条入边都必须声明 ordinal",
                            )
                        )
                    else:
                        ordinals.append(edge.ordinal)
                if len(ordinals) != len(set(ordinals)):
                    violations.append(
                        GraphViolation(
                            "E_ORDINAL_DUPLICATE",
                            path,
                            "ordered_many ordinal 必须唯一",
                        )
                    )
                if len(ordinals) == len(bindings) and sorted(ordinals) != list(
                    range(len(bindings))
                ):
                    violations.append(
                        GraphViolation(
                            "E_ORDINAL_NON_CONTIGUOUS",
                            path,
                            "ordered_many ordinal 必须从 0 开始连续",
                        )
                    )
        return tuple(violations)

    @staticmethod
    def _has_cycle(node_ids: tuple[str, ...], edges: list[Edge]) -> bool:
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        indegree = dict.fromkeys(node_ids, 0)
        for edge in edges:
            targets = adjacency[edge.source_node_id]
            if edge.target_node_id not in targets:
                targets.add(edge.target_node_id)
                indegree[edge.target_node_id] += 1

        ready = [node_id for node_id in node_ids if indegree[node_id] == 0]
        visited = 0
        while ready:
            node_id = ready.pop()
            visited += 1
            for target in adjacency[node_id]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        return visited != len(node_ids)
