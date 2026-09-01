import { describe, expect, it } from 'vitest'
import type { Connection } from '@xyflow/react'
import type { GraphWire } from './contracts'
import {
  connectGraph,
  copySelection,
  deleteSelection,
  edgeId,
  inspectGraph,
  isStudioConnectionValid,
  nodeExecutionSignatureMatches,
} from './graph'
import {
  dataSourceDefinition,
  mergeDefinition,
  projectSnapshot,
  sinkDefinition,
  sourceDefinition,
  transformDefinition,
} from './test-fixtures'

const connection = (
  source: string,
  target: string,
  sourceHandle = 'out',
  targetHandle = 'in',
): Connection => ({ source, target, sourceHandle, targetHandle })

describe('Studio 0.2.1 Graph interactions', () => {
  it('按精确 data_type、one 占用和 DAG cycle 拒绝连接', () => {
    expect(
      isStudioConnectionValid(
        connection('source', 'sink'),
        projectSnapshot.project.graph,
        projectSnapshot.definitions,
      ),
    ).toBe(false)

    const incompatible: GraphWire = {
      nodes: [
        {
          node_id: 'data',
          type_id: dataSourceDefinition.type_id,
          definition_version: dataSourceDefinition.version,
          parameters: {},
          ui_position: null,
        },
        projectSnapshot.project.graph.nodes[2]!,
      ],
      edges: [],
    }
    expect(
      isStudioConnectionValid(
        connection('data', 'sink'),
        incompatible,
        projectSnapshot.definitions,
      ),
    ).toBe(false)

    const cycleGraph: GraphWire = {
      nodes: ['a', 'b'].map((node_id) => ({
        node_id,
        type_id: transformDefinition.type_id,
        definition_version: transformDefinition.version,
        parameters: { strength: 3 },
        ui_position: null,
      })),
      edges: [
        {
          source_node_id: 'a',
          source_port_id: 'out',
          target_node_id: 'b',
          target_port_id: 'in',
          ordinal: null,
        },
      ],
    }
    expect(
      isStudioConnectionValid(
        connection('b', 'a'),
        cycleGraph,
        [transformDefinition],
      ),
    ).toBe(false)
  })

  it('为 ordered_many 自动创建连续 ordinal，并拒绝错误类型', () => {
    const graph: GraphWire = {
      nodes: [
        ...['left', 'right'].map((node_id) => ({
          node_id,
          type_id: sourceDefinition.type_id,
          definition_version: sourceDefinition.version,
          parameters: {},
          ui_position: null,
        })),
        {
          node_id: 'data',
          type_id: dataSourceDefinition.type_id,
          definition_version: dataSourceDefinition.version,
          parameters: {},
          ui_position: null,
        },
        {
          node_id: 'merge',
          type_id: mergeDefinition.type_id,
          definition_version: mergeDefinition.version,
          parameters: {},
          ui_position: null,
        },
      ],
      edges: [],
    }
    const first = connectGraph(
      connection('left', 'merge', 'out', 'items'),
      graph,
      [sourceDefinition, dataSourceDefinition, mergeDefinition],
    )!
    const second = connectGraph(
      connection('right', 'merge', 'out', 'items'),
      first,
      [sourceDefinition, dataSourceDefinition, mergeDefinition],
    )!
    const repeated = connectGraph(
      connection('left', 'merge', 'out', 'items'),
      second,
      [sourceDefinition, dataSourceDefinition, mergeDefinition],
    )!

    expect(repeated.edges.map((edge) => edge.ordinal)).toEqual([0, 1, 2])
    expect(
      connectGraph(
        connection('data', 'merge', 'out', 'items'),
        repeated,
        [sourceDefinition, dataSourceDefinition, mergeDefinition],
      ),
    ).toBeNull()
  })

  it('复制多选节点及内部边，批量删除时同步清理关联边', () => {
    const ids = ['copy.source', 'copy.transform']
    const copied = copySelection(
      projectSnapshot.project.graph,
      new Set(['source', 'transform']),
      () => ids.shift()!,
    )
    expect(copied.copied_node_ids).toEqual(new Set(['copy.source', 'copy.transform']))
    expect(copied.graph.nodes).toHaveLength(5)
    expect(
      copied.graph.edges.some(
        (edge) => edge.source_node_id === 'copy.source' && edge.target_node_id === 'copy.transform',
      ),
    ).toBe(true)

    const internal = copied.graph.edges.find(
      (edge) => edge.source_node_id === 'copy.source' && edge.target_node_id === 'copy.transform',
    )!
    const deleted = deleteSelection(
      copied.graph,
      new Set(['copy.source']),
      new Set([edgeId(internal)]),
    )
    expect(deleted.nodes.some((node) => node.node_id === 'copy.source')).toBe(false)
    expect(deleted.edges.some((edge) => edge.source_node_id === 'copy.source')).toBe(false)
  })

  it('即时诊断 required input，且不要求唯一 Source 或 Final', () => {
    expect(inspectGraph(projectSnapshot)).toEqual([])
    const partial = {
      ...projectSnapshot,
      project: {
        ...projectSnapshot.project,
        graph: {
          nodes: [
            projectSnapshot.project.graph.nodes[0]!,
            { ...projectSnapshot.project.graph.nodes[0]!, node_id: 'source.second' },
            projectSnapshot.project.graph.nodes[2]!,
          ],
          edges: [],
        },
      },
    }
    expect(inspectGraph(partial).map((item) => item.code)).toEqual(['E_REQUIRED_INPUT_MISSING'])
  })

  it('接受多个 Source、多个 Output、零 Output 以及任意合法分支汇合', () => {
    const node = (
      node_id: string,
      type_id: string,
      definition_version = '0.2.0',
      parameters: Record<string, number> = {},
    ) => ({ node_id, type_id, definition_version, parameters, ui_position: null })
    const graph: GraphWire = {
      nodes: [
        node('source.left', sourceDefinition.type_id),
        node('source.right', sourceDefinition.type_id),
        node('branch.left', transformDefinition.type_id, '0.2.0', { strength: 3 }),
        node('branch.right', transformDefinition.type_id, '0.2.0', { strength: 3 }),
        node('merge', mergeDefinition.type_id),
        node('output.preview', sinkDefinition.type_id),
        node('output.archive', sinkDefinition.type_id),
      ],
      edges: [
        {
          source_node_id: 'source.left',
          source_port_id: 'out',
          target_node_id: 'branch.left',
          target_port_id: 'in',
          ordinal: null,
        },
        {
          source_node_id: 'source.right',
          source_port_id: 'out',
          target_node_id: 'branch.right',
          target_port_id: 'in',
          ordinal: null,
        },
        {
          source_node_id: 'branch.left',
          source_port_id: 'out',
          target_node_id: 'merge',
          target_port_id: 'items',
          ordinal: 0,
        },
        {
          source_node_id: 'branch.right',
          source_port_id: 'out',
          target_node_id: 'merge',
          target_port_id: 'items',
          ordinal: 1,
        },
        {
          source_node_id: 'merge',
          source_port_id: 'out',
          target_node_id: 'output.preview',
          target_port_id: 'in',
          ordinal: null,
        },
        {
          source_node_id: 'merge',
          source_port_id: 'out',
          target_node_id: 'output.archive',
          target_port_id: 'in',
          ordinal: null,
        },
      ],
    }
    const definitions = [sourceDefinition, transformDefinition, mergeDefinition, sinkDefinition]
    expect(
      inspectGraph({
        project: { ...projectSnapshot.project, graph },
        definitions,
      }),
    ).toEqual([])
    expect(
      inspectGraph({
        project: {
          ...projectSnapshot.project,
          graph: { nodes: graph.nodes.slice(0, 2), edges: [] },
        },
        definitions,
      }),
    ).toEqual([])
  })

  it('按精确定义的 parameter_schema 即时失败关闭', () => {
    const invalidParameters = {
      ...projectSnapshot,
      project: {
        ...projectSnapshot.project,
        graph: {
          ...projectSnapshot.project.graph,
          nodes: projectSnapshot.project.graph.nodes.map((node) =>
            node.node_id === 'transform' ? { ...node, parameters: { strength: 99 } } : node,
          ),
        },
      },
    }
    expect(inspectGraph(invalidParameters).map((item) => item.code)).toContain(
      'E_PARAMETERS_INVALID',
    )

    const invalidDefinition = {
      ...transformDefinition,
      parameter_schema: { type: 'not-a-json-schema-type' },
    }
    const invalidSchemaSnapshot = {
      ...projectSnapshot,
      definitions: projectSnapshot.definitions.map((definition) =>
        definition.type_id === transformDefinition.type_id ? invalidDefinition : definition,
      ),
    }
    expect(inspectGraph(invalidSchemaSnapshot).map((item) => item.code)).toContain(
      'E_PARAMETER_SCHEMA_INVALID',
    )
  })

  it('当前 Graph 只有完整 execution signature 一致时才叠加 Run snapshot', () => {
    const runGraph = projectSnapshot.project.graph
    const withMovedUi: GraphWire = {
      ...runGraph,
      nodes: runGraph.nodes.map((node) =>
        node.node_id === 'transform' ? { ...node, ui_position: { x: 999, y: 999 } } : node,
      ),
    }
    expect(nodeExecutionSignatureMatches(withMovedUi, runGraph, 'transform')).toBe(true)

    const withReorderedParameterKeys: GraphWire = {
      ...runGraph,
      nodes: runGraph.nodes.map((node) =>
        node.node_id === 'transform'
          ? { ...node, parameters: { model_name: 'Synthetic Model', strength: 3 } }
          : node,
      ),
    }
    expect(nodeExecutionSignatureMatches(withReorderedParameterKeys, runGraph, 'transform'))
      .toBe(true)

    const changedParameters: GraphWire = {
      ...runGraph,
      nodes: runGraph.nodes.map((node) =>
        node.node_id === 'transform'
          ? { ...node, parameters: { strength: 4, model_name: 'Synthetic Model' } }
          : node,
      ),
    }
    expect(nodeExecutionSignatureMatches(changedParameters, runGraph, 'transform')).toBe(false)

    const changedVersion: GraphWire = {
      ...runGraph,
      nodes: runGraph.nodes.map((node) =>
        node.node_id === 'transform' ? { ...node, definition_version: '0.2.0+changed' } : node,
      ),
    }
    expect(nodeExecutionSignatureMatches(changedVersion, runGraph, 'transform')).toBe(false)

    const changedIncoming: GraphWire = {
      ...runGraph,
      edges: runGraph.edges.map((edge) =>
        edge.target_node_id === 'transform' ? { ...edge, ordinal: 0 } : edge,
      ),
    }
    expect(nodeExecutionSignatureMatches(changedIncoming, runGraph, 'transform')).toBe(false)

    const changedOnlyOutgoing: GraphWire = {
      ...runGraph,
      edges: runGraph.edges.filter((edge) => edge.source_node_id !== 'transform'),
    }
    expect(nodeExecutionSignatureMatches(changedOnlyOutgoing, runGraph, 'transform')).toBe(true)
  })
})
