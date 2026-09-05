/** 画布测试验证建议、批量宏与纯 UI 分组都不直接改写 Graph 或运行状态。 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReactFlowProps, Viewport } from '@xyflow/react'
import type { WorkflowEdge, WorkflowNode } from '../../model'
import type { GraphWire } from '../contracts'
import { GraphCanvas, type GraphCanvasProps } from './GraphCanvas'
import { definitionForNode, isStudioConnectionValid } from '../graph'
import { projectSnapshot } from '../test-fixtures'

let flowProps: ReactFlowProps<WorkflowNode, WorkflowEdge> = {}
let currentViewport: Viewport = { x: 0, y: 0, zoom: 1 }
const fitView = vi.fn(async () => true)
const setViewport = vi.fn(async (value: Viewport) => { currentViewport = value; return true })

vi.mock('@xyflow/react', async () => {
  const { useEffect } = await import('react')
  return {
    BackgroundVariant: { Dots: 'dots' }, Background: () => null, Controls: () => null, MiniMap: () => null,
    ReactFlow: (props: ReactFlowProps<WorkflowNode, WorkflowEdge>) => {
      flowProps = props
      useEffect(() => {
        props.onInit?.({ fitView, setViewport, getViewport: () => currentViewport,
          screenToFlowPosition: (point: { x: number; y: number }) => ({ x: point.x - 10, y: point.y - 20 }),
        } as never)
      }, [props.onInit])
      return <div className="react-flow__pane" data-testid="flow-pane">{props.children}</div>
    },
  }
})

afterEach(() => { cleanup(); vi.clearAllMocks(); currentViewport = { x: 0, y: 0, zoom: 1 } })
const graph: GraphWire = { ...projectSnapshot.project.graph, edges: [] }
const nodes: WorkflowNode[] = graph.nodes.map((node) => {
  const definition = definitionForNode(node, projectSnapshot.definitions)!
  return { id: node.node_id, type: 'workflow', position: { x: 50, y: 70 }, selected: node.node_id === 'source', data: {
    label: node.node_id === 'source' ? '导入视频' : node.node_id === 'transform' ? '画质增强' : '输出成片',
    instanceId: node.node_id, summaries: [], typeId: definition.type_id, definitionVersion: definition.version,
    executorKind: definition.executor.kind, inputs: definition.input_ports, outputs: definition.output_ports,
    nodeRun: null, latestResult: null, progress: { mode: 'none', fraction: null, measurement: null, elapsed: null },
  } }
})

function props(overrides: Partial<GraphCanvasProps> = {}): GraphCanvasProps {
  return { nodes, edges: [], graph, definitions: projectSnapshot.definitions, editable: true, busy: false,
    modeLabel: '当前工作流', contextLabel: '合成工程', snapshotChanged: false, canToggleSnapshot: false,
    loading: false, boundaryError: null, hasProject: true, onToggleSnapshot: vi.fn(),
    onNodesChange: vi.fn(), onEdgesChange: vi.fn(), onSelectionChange: vi.fn(), onNodeClick: vi.fn(), onEdgeClick: vi.fn(),
    onConnect: vi.fn(), isValidConnection: (connection) => isStudioConnectionValid({ ...connection, sourceHandle: connection.sourceHandle ?? null, targetHandle: connection.targetHandle ?? null }, graph, projectSnapshot.definitions),
    definitionLabel: (definition) => definition.type_id.includes('Transform') ? '画质增强' : '输出成片',
    ...overrides }
}

describe('Phase 3 自由画布', () => {
  it('连接时只高亮可接端口；从输出拖到空白处建议并一次回传添加连接 intent', async () => {
    const onAddConnectedNodes = vi.fn()
    render(<GraphCanvas {...props({ onAddConnectedNodes })} />)
    await waitFor(() => expect(flowProps.onConnectStart).toBeDefined())
    fireEvent(screen.getByTestId('flow-pane'), new MouseEvent('mousedown', { bubbles: true }))
    const { act } = await import('@testing-library/react')
    act(() => { flowProps.onConnectStart?.(new MouseEvent('mousedown'), { nodeId: 'source', handleId: 'out', handleType: 'source' }) })
    expect(flowProps.nodes?.find((node) => node.id === 'transform')?.data.compatibleInputPortIds).toEqual(['in'])
    expect(flowProps.nodes?.find((node) => node.id === 'source')?.data.compatibleInputPortIds).toEqual([])
    const event = new MouseEvent('mouseup', { clientX: 300, clientY: 200 })
    Object.defineProperty(event, 'target', { value: screen.getByTestId('flow-pane') })
    act(() => { flowProps.onConnectEnd?.(event, { isValid: false, toNode: null,
      fromNode: { id: 'source' }, fromHandle: { id: 'out', type: 'source' },
    } as never) })
    const dialog = await screen.findByRole('dialog', { name: '添加下一步' })
    expect(dialog).toBeInTheDocument()
    const buttons = dialog.querySelectorAll('.compatible-node-list button')
    expect(buttons.length).toBeGreaterThan(0)
    await userEvent.click(buttons[0]!)
    expect(onAddConnectedNodes).toHaveBeenCalledTimes(1)
    expect(onAddConnectedNodes.mock.calls[0]![0]).toMatchObject({ sources: [{ sourceNodeId: 'source', sourcePortId: 'out' }], position: { x: 290, y: 180 } })
    expect(graph.edges).toEqual([])
  })

  it('纯 UI 分组折叠保留节点和连接，初始 viewport 恢复不触发保存', async () => {
    const onViewportChange = vi.fn()
    const onToggleGroup = vi.fn()
    const edges: WorkflowEdge[] = [{ id: 'visible-edge', source: 'source', target: 'transform', type: 'smoothstep' }]
    render(<GraphCanvas {...props({ edges, viewport: { x: 30, y: 50, zoom: .8 }, onViewportChange, onToggleGroup,
      groups: [{ group_id: 'chapter-a', title: '第一章', color_token: 'blue', collapsed: true, node_ids: ['source', 'transform'] }],
    })} />)
    await waitFor(() => expect(setViewport).toHaveBeenCalledWith({ x: 30, y: 50, zoom: .8 }, { duration: 0 }))
    expect(onViewportChange).not.toHaveBeenCalled()
    expect(flowProps.nodes).toHaveLength(3)
    expect(flowProps.edges).toBe(edges)
    expect(flowProps.nodes?.find((node) => node.id === 'transform')?.data.collapsed).toBe(true)
    expect(flowProps.nodes?.find((node) => node.id === 'transform')?.data.inputs).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: '展开分组 第一章' }))
    expect(onToggleGroup).toHaveBeenCalledExactlyOnceWith('chapter-a')
    await userEvent.click(screen.getByRole('button', { name: '适应画布' }))
    expect(fitView).toHaveBeenCalled()
    expect(onViewportChange).toHaveBeenCalledExactlyOnceWith(currentViewport)
  })

  it('查找和缩放定位同一节点，自动布局与批量宏均通过回调提交一次', async () => {
    const onSelectionChange = vi.fn()
    const onAutoLayout = vi.fn()
    const onAddConnectedNodes = vi.fn()
    render(<GraphCanvas {...props({ onSelectionChange, onAutoLayout, onAddConnectedNodes })} />)
    await userEvent.type(screen.getByRole('textbox', { name: '查找画布节点' }), '画质')
    await userEvent.click(screen.getByRole('button', { name: '画质增强' }))
    expect(onSelectionChange).toHaveBeenCalledExactlyOnceWith({ nodes: [nodes[1]], edges: [] })
    expect(fitView).toHaveBeenCalledWith(expect.objectContaining({ nodes: [{ id: 'transform' }] }))
    await userEvent.click(screen.getByRole('button', { name: '自动布局' }))
    expect(onAutoLayout).toHaveBeenCalledOnce()
    await userEvent.click(screen.getByRole('button', { name: '为所有输出添加下一步' }))
    expect(screen.getByRole('dialog', { name: '添加下一步' })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(onAddConnectedNodes).not.toHaveBeenCalled()
  })
})
