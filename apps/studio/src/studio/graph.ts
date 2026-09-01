/**
 * 提供 Studio 的纯画布编辑辅助函数。
 *
 * 这些函数只做即时交互提示和不可变编辑，不能替代 Python GraphValidator。保存与运行仍必须由
 * Project Service 完整重验；未知 definition、port 或 cardinality 一律拒绝。
 */

import type { Connection } from '@xyflow/react'
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js'
import type {
  EdgeWire,
  GraphWire,
  JsonObject,
  JsonValue,
  NodeDefinitionWire,
  NodeInstanceWire,
  ProjectSnapshotWire,
} from './contracts'

export interface StudioDiagnostic {
  readonly code: string
  readonly message: string
  readonly node_id: string | null
  readonly edge_id: string | null
}

const parameterAjv = new Ajv2020({ allErrors: true, strict: false, validateFormats: false })
const parameterValidators = new WeakMap<NodeDefinitionWire, ValidateFunction | Error>()

function parameterValidator(definition: NodeDefinitionWire): ValidateFunction | Error {
  const cached = parameterValidators.get(definition)
  if (cached) return cached
  try {
    const validator = parameterAjv.compile(definition.parameter_schema)
    parameterValidators.set(definition, validator)
    return validator
  } catch (error) {
    const failure = error instanceof Error ? error : new Error('未知 JSON Schema compile 错误')
    parameterValidators.set(definition, failure)
    return failure
  }
}

export function definitionKey(typeId: string, version: string): string {
  return `${typeId}@${version}`
}

export function edgeId(edge: EdgeWire): string {
  const part = (value: string) => encodeURIComponent(value)
  return `${part(edge.source_node_id)}:${part(edge.source_port_id)}>${part(edge.target_node_id)}:${part(edge.target_port_id)}#${edge.ordinal ?? 'one'}`
}

export function definitionForNode(
  node: NodeInstanceWire,
  definitions: ReadonlyArray<NodeDefinitionWire>,
): NodeDefinitionWire | null {
  return (
    definitions.find(
      (definition) =>
        definition.type_id === node.type_id && definition.version === node.definition_version,
    ) ?? null
  )
}

function graphHasCycle(graph: GraphWire): boolean {
  const adjacency = new Map(graph.nodes.map((node) => [node.node_id, new Set<string>()]))
  const indegree = new Map(graph.nodes.map((node) => [node.node_id, 0]))
  for (const edge of graph.edges) {
    const targets = adjacency.get(edge.source_node_id)
    if (!targets || !indegree.has(edge.target_node_id) || targets.has(edge.target_node_id)) continue
    targets.add(edge.target_node_id)
    indegree.set(edge.target_node_id, (indegree.get(edge.target_node_id) ?? 0) + 1)
  }
  const ready = graph.nodes.filter((node) => indegree.get(node.node_id) === 0).map((node) => node.node_id)
  let visited = 0
  for (let index = 0; index < ready.length; index += 1) {
    const nodeId = ready[index]
    visited += 1
    for (const targetId of adjacency.get(nodeId) ?? []) {
      const next = (indegree.get(targetId) ?? 0) - 1
      indegree.set(targetId, next)
      if (next === 0) ready.push(targetId)
    }
  }
  return visited !== graph.nodes.length
}

function resolveConnection(
  connection: Connection,
  graph: GraphWire,
  definitions: ReadonlyArray<NodeDefinitionWire>,
) {
  if (
    !connection.source ||
    !connection.target ||
    !connection.sourceHandle ||
    !connection.targetHandle ||
    connection.source === connection.target
  ) {
    return null
  }
  const source = graph.nodes.find((node) => node.node_id === connection.source)
  const target = graph.nodes.find((node) => node.node_id === connection.target)
  if (!source || !target) return null
  const sourceDefinition = definitionForNode(source, definitions)
  const targetDefinition = definitionForNode(target, definitions)
  const output = sourceDefinition?.output_ports.find(
    (port) => port.port_id === connection.sourceHandle,
  )
  const input = targetDefinition?.input_ports.find((port) => port.port_id === connection.targetHandle)
  if (!output || !input || output.data_type !== input.data_type) return null
  return { source, target, output, input }
}

export function isStudioConnectionValid(
  connection: Connection,
  graph: GraphWire,
  definitions: ReadonlyArray<NodeDefinitionWire>,
): boolean {
  const resolved = resolveConnection(connection, graph, definitions)
  if (!resolved) return false
  const incoming = graph.edges.filter(
    (edge) =>
      edge.target_node_id === connection.target && edge.target_port_id === connection.targetHandle,
  )
  if (resolved.input.cardinality === 'one' && incoming.length > 0) return false
  const candidate: EdgeWire = {
    source_node_id: resolved.source.node_id,
    source_port_id: resolved.output.port_id,
    target_node_id: resolved.target.node_id,
    target_port_id: resolved.input.port_id,
    ordinal: resolved.input.cardinality === 'ordered_many' ? incoming.length : null,
  }
  return !graphHasCycle({ ...graph, edges: [...graph.edges, candidate] })
}

export function connectGraph(
  connection: Connection,
  graph: GraphWire,
  definitions: ReadonlyArray<NodeDefinitionWire>,
): GraphWire | null {
  if (!isStudioConnectionValid(connection, graph, definitions)) return null
  const resolved = resolveConnection(connection, graph, definitions)
  if (!resolved || !connection.sourceHandle || !connection.targetHandle) return null
  const incomingCount = graph.edges.filter(
    (edge) =>
      edge.target_node_id === connection.target && edge.target_port_id === connection.targetHandle,
  ).length
  return {
    ...graph,
    edges: [
      ...graph.edges,
      {
        source_node_id: connection.source,
        source_port_id: connection.sourceHandle,
        target_node_id: connection.target,
        target_port_id: connection.targetHandle,
        ordinal: resolved.input.cardinality === 'ordered_many' ? incomingCount : null,
      },
    ],
  }
}

function normalizeOrdinals(edges: ReadonlyArray<EdgeWire>): EdgeWire[] {
  const groups = new Map<string, EdgeWire[]>()
  for (const edge of edges) {
    if (edge.ordinal === null) continue
    const key = `${edge.target_node_id}\u0000${edge.target_port_id}`
    const group = groups.get(key) ?? []
    group.push(edge)
    groups.set(key, group)
  }
  const ordinals = new Map<string, number>()
  for (const group of groups.values()) {
    const ordered = [...group].sort(
      (left, right) => (left.ordinal ?? 0) - (right.ordinal ?? 0),
    )
    ordered.forEach((edge, index) => ordinals.set(edgeId(edge), index))
  }
  return edges.map((edge) =>
    edge.ordinal === null ? edge : { ...edge, ordinal: ordinals.get(edgeId(edge)) ?? edge.ordinal },
  )
}

export function deleteSelection(
  graph: GraphWire,
  selectedNodeIds: ReadonlySet<string>,
  selectedEdgeIds: ReadonlySet<string>,
): GraphWire {
  const nodes = graph.nodes.filter((node) => !selectedNodeIds.has(node.node_id))
  const edges = graph.edges.filter(
    (edge) =>
      !selectedNodeIds.has(edge.source_node_id) &&
      !selectedNodeIds.has(edge.target_node_id) &&
      !selectedEdgeIds.has(edgeId(edge)),
  )
  return { nodes, edges: normalizeOrdinals(edges) }
}

export function copySelection(
  graph: GraphWire,
  selectedNodeIds: ReadonlySet<string>,
  nodeIdFactory: () => string,
): { readonly graph: GraphWire; readonly copied_node_ids: ReadonlySet<string> } {
  const selected = graph.nodes.filter((node) => selectedNodeIds.has(node.node_id))
  const existingIds = new Set(graph.nodes.map((node) => node.node_id))
  const mapping = new Map<string, string>()
  const copies: NodeInstanceWire[] = selected.map((node) => {
    let nodeId = nodeIdFactory()
    while (existingIds.has(nodeId)) nodeId = nodeIdFactory()
    existingIds.add(nodeId)
    mapping.set(node.node_id, nodeId)
    return {
      ...node,
      node_id: nodeId,
      parameters: cloneJsonObject(node.parameters),
      ui_position: {
        x: (node.ui_position?.x ?? 0) + 48,
        y: (node.ui_position?.y ?? 0) + 48,
      },
    }
  })
  const copiedEdges = graph.edges.flatMap((edge): EdgeWire[] => {
    const source = mapping.get(edge.source_node_id)
    const target = mapping.get(edge.target_node_id)
    return source && target ? [{ ...edge, source_node_id: source, target_node_id: target }] : []
  })
  return {
    graph: { nodes: [...graph.nodes, ...copies], edges: [...graph.edges, ...copiedEdges] },
    copied_node_ids: new Set(copies.map((node) => node.node_id)),
  }
}

export function reorderEdge(graph: GraphWire, selectedEdgeId: string, ordinal: number): GraphWire {
  const selected = graph.edges.find((edge) => edgeId(edge) === selectedEdgeId)
  if (!selected || selected.ordinal === null) return graph
  const matching = graph.edges
    .filter(
      (edge) =>
        edge.target_node_id === selected.target_node_id &&
        edge.target_port_id === selected.target_port_id &&
        edge.ordinal !== null,
    )
    .sort((left, right) => (left.ordinal ?? 0) - (right.ordinal ?? 0))
  const without = matching.filter((edge) => edgeId(edge) !== selectedEdgeId)
  const nextIndex = Math.max(0, Math.min(Math.trunc(ordinal), without.length))
  without.splice(nextIndex, 0, selected)
  const replacements = new Map(without.map((edge, index) => [edgeId(edge), index]))
  return {
    ...graph,
    edges: graph.edges.map((edge) =>
      replacements.has(edgeId(edge)) ? { ...edge, ordinal: replacements.get(edgeId(edge))! } : edge,
    ),
  }
}

function cloneJson(value: JsonValue): JsonValue {
  if (Array.isArray(value)) return value.map(cloneJson)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneJson(item)]))
  }
  return value
}

export function cloneJsonObject(value: JsonObject): JsonObject {
  return cloneJson(value) as JsonObject
}

export function defaultParameters(definition: NodeDefinitionWire): JsonObject {
  const properties = definition.parameter_schema.properties
  if (properties === null || typeof properties !== 'object' || Array.isArray(properties)) return {}
  return Object.fromEntries(
    Object.entries(properties).flatMap(([key, field]) => {
      if (field === null || typeof field !== 'object' || Array.isArray(field) || !('default' in field)) {
        return []
      }
      const value = field.default
      return value === undefined ? [] : [[key, cloneJson(value)]]
    }),
  )
}

export function inspectGraph(snapshot: ProjectSnapshotWire): ReadonlyArray<StudioDiagnostic> {
  const graph = snapshot.project.graph
  const nodesById = new Map<string, NodeInstanceWire>()
  const definitionsByNode = new Map<string, NodeDefinitionWire>()
  const diagnostics: StudioDiagnostic[] = []
  for (const node of graph.nodes) {
    if (nodesById.has(node.node_id)) {
      diagnostics.push({
        code: 'E_NODE_DUPLICATE',
        message: `node_id ${node.node_id} 重复。`,
        node_id: node.node_id,
        edge_id: null,
      })
      continue
    }
    nodesById.set(node.node_id, node)
    const definition = definitionForNode(node, snapshot.definitions)
    if (definition) {
      definitionsByNode.set(node.node_id, definition)
      const validator = parameterValidator(definition)
      if (validator instanceof Error) {
        diagnostics.push({
          code: 'E_PARAMETER_SCHEMA_INVALID',
          message: `${definitionKey(node.type_id, node.definition_version)} parameter_schema 无法编译：${validator.message}`,
          node_id: node.node_id,
          edge_id: null,
        })
      } else if (!validator(node.parameters)) {
        const details = (validator.errors ?? [])
          .slice(0, 3)
          .map((error) => `${error.instancePath || '/'} ${error.message ?? error.keyword}`)
          .join('; ')
        diagnostics.push({
          code: 'E_PARAMETERS_INVALID',
          message: `${node.node_id} 参数不符合绑定 definition Schema：${details}`,
          node_id: node.node_id,
          edge_id: null,
        })
      }
    } else {
      diagnostics.push({
        code: 'E_DEFINITION_UNKNOWN',
        message: `没有 ${definitionKey(node.type_id, node.definition_version)} 的精确定义。`,
        node_id: node.node_id,
        edge_id: null,
      })
    }
  }

  const incoming = new Map<string, EdgeWire[]>()
  for (const edge of graph.edges) {
    const id = edgeId(edge)
    const sourceDefinition = definitionsByNode.get(edge.source_node_id)
    const targetDefinition = definitionsByNode.get(edge.target_node_id)
    if (!nodesById.has(edge.source_node_id) || !nodesById.has(edge.target_node_id)) {
      diagnostics.push({
        code: 'E_EDGE_NODE_UNKNOWN',
        message: 'Edge 引用了不存在的 node。',
        node_id: null,
        edge_id: id,
      })
      continue
    }
    const output = sourceDefinition?.output_ports.find((port) => port.port_id === edge.source_port_id)
    const input = targetDefinition?.input_ports.find((port) => port.port_id === edge.target_port_id)
    if (!output || !input) {
      diagnostics.push({
        code: 'E_EDGE_PORT_UNKNOWN',
        message: 'Edge 引用了不存在的 port。',
        node_id: null,
        edge_id: id,
      })
      continue
    }
    if (output.data_type !== input.data_type) {
      diagnostics.push({
        code: 'E_PORT_TYPE_INCOMPATIBLE',
        message: `${output.data_type} output 不能连接到 ${input.data_type} input。`,
        node_id: edge.target_node_id,
        edge_id: id,
      })
    }
    const key = `${edge.target_node_id}\u0000${edge.target_port_id}`
    const bindings = incoming.get(key) ?? []
    bindings.push(edge)
    incoming.set(key, bindings)
  }

  for (const [nodeId, definition] of definitionsByNode) {
    for (const input of definition.input_ports) {
      const bindings = incoming.get(`${nodeId}\u0000${input.port_id}`) ?? []
      if (input.required && bindings.length === 0) {
        diagnostics.push({
          code: 'E_REQUIRED_INPUT_MISSING',
          message: `required input ${nodeId}.${input.port_id} 未连接。`,
          node_id: nodeId,
          edge_id: null,
        })
      }
      if (input.cardinality === 'one') {
        if (bindings.length > 1) {
          diagnostics.push({
            code: 'E_INPUT_MULTIPLE_EDGES',
            message: `one input ${nodeId}.${input.port_id} 只能有一条入边。`,
            node_id: nodeId,
            edge_id: null,
          })
        }
        if (bindings.some((edge) => edge.ordinal !== null)) {
          diagnostics.push({
            code: 'E_ORDINAL_NOT_ALLOWED',
            message: `one input ${nodeId}.${input.port_id} 不接受 ordinal。`,
            node_id: nodeId,
            edge_id: null,
          })
        }
      } else {
        const ordinals = bindings.flatMap((edge) => (edge.ordinal === null ? [] : [edge.ordinal]))
        if (
          ordinals.length !== bindings.length ||
          new Set(ordinals).size !== ordinals.length ||
          [...ordinals].sort((left, right) => left - right).some((value, index) => value !== index)
        ) {
          diagnostics.push({
            code: 'E_ORDINAL_INVALID',
            message: `ordered_many input ${nodeId}.${input.port_id} 的 ordinal 必须从 0 连续且唯一。`,
            node_id: nodeId,
            edge_id: null,
          })
        }
      }
    }
  }
  if (graphHasCycle(graph)) {
    diagnostics.push({
      code: 'E_GRAPH_CYCLE',
      message: 'Graph 必须是有向无环图。',
      node_id: null,
      edge_id: null,
    })
  }
  return diagnostics
}
