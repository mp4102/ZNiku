/** 验证正式画布把 ReactFlow 尺寸事件接到纯 UI 缓存，而不是 Workspace 的 Graph 编辑入口。 */
import { act, cleanup, render } from '@testing-library/react'
import type { ReactFlowProps } from '@xyflow/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowEdge, WorkflowNode } from '../../model'
import { GraphCanvas, type GraphCanvasProps } from './GraphCanvas'

let flowProps: ReactFlowProps<WorkflowNode, WorkflowEdge> = {}
vi.mock('@xyflow/react', () => ({
  BackgroundVariant: { Dots: 'dots' }, Background: () => null, MiniMap: () => null,
  ReactFlow: (props: ReactFlowProps<WorkflowNode, WorkflowEdge>) => { flowProps = props; return null },
}))
afterEach(() => { cleanup(); vi.clearAllMocks(); flowProps = {} })

function source(x = 40): WorkflowNode {
  return { id: 'source', type: 'workflow', position: { x, y: 50 }, data: {
    label: '导入视频', instanceId: 'source', typeId: 'synthetic.source', definitionVersion: '0.2.0',
    executorKind: 'python', inputs: [], outputs: [], summaries: [], nodeRun: null, latestResult: null,
    progress: { mode: 'none', fraction: null, measurement: null, elapsed: null },
  } }
}
function props(): GraphCanvasProps {
  return { nodes: [source()], edges: [], editable: true, busy: false, modeLabel: '当前工作流',
    contextLabel: '自由编辑', snapshotChanged: false, canToggleSnapshot: false,
    loading: false, boundaryError: null, hasProject: true,
    onToggleSnapshot: vi.fn(), onNodesChange: vi.fn(), onEdgesChange: vi.fn(), onSelectionChange: vi.fn(),
    onNodeClick: vi.fn(), onEdgeClick: vi.fn(), onConnect: vi.fn(), isValidConnection: () => false,
    onNodeDragStart: vi.fn(), onNodeDragStop: vi.fn(),
  }
}

describe('GraphCanvas 测量接线', () => {
  it('真实 dimensions 回调在位置重投影后保留，且不污染 authoring 或增加拖动回调', () => {
    const initial = props()
    const { rerender } = render(<GraphCanvas {...initial} />)
    act(() => flowProps.onNodesChange?.([{ id: 'source', type: 'dimensions', dimensions: { width: 248, height: 180 } }]))
    expect(initial.onNodesChange).not.toHaveBeenCalled()
    for (let x = 45; x <= 240; x += 5) {
      act(() => flowProps.onNodesChange?.([{ id: 'source', type: 'position', position: { x, y: 50 }, dragging: true }]))
      rerender(<GraphCanvas {...initial} nodes={[source(x)]} />)
      expect(flowProps.nodes?.[0]?.measured).toEqual({ width: 248, height: 180 })
      expect(flowProps.nodes?.[0]?.dragging).toBe(true)
      expect(flowProps.nodes?.[0]?.position.x).toBe(x)
    }
    expect(initial.onNodesChange).toHaveBeenCalledTimes(40)
    expect(initial.onNodeDragStart).not.toHaveBeenCalled()
    expect(initial.onNodeDragStop).not.toHaveBeenCalled()
    expect(flowProps.onNodeDragStart).toBe(initial.onNodeDragStart)
    expect(flowProps.onNodeDragStop).toBe(initial.onNodeDragStop)
  })

  it('分组装饰先于结构测量，并允许只读 snapshot 获取新尺寸而不改 Graph', () => {
    const initial = props()
    const group = { group_id: 'first', title: '第一章', color_token: 'blue', collapsed: false, node_ids: ['source'] }
    const { rerender } = render(<GraphCanvas {...initial} groups={[group]} />)
    act(() => flowProps.onNodesChange?.([{ id: 'source', type: 'dimensions', dimensions: { width: 248, height: 200 } }]))
    rerender(<GraphCanvas {...initial} groups={[{ ...group, collapsed: true }]} />)
    expect(flowProps.nodes?.[0]?.data.collapsed).toBe(true)
    expect(flowProps.nodes?.[0]?.measured).toBeUndefined()
    act(() => flowProps.onNodesChange?.([{ id: 'source', type: 'dimensions', dimensions: { width: 248, height: 120 } }]))
    rerender(<GraphCanvas {...initial} nodes={[source(900)]} editable={false} showingSnapshot groups={[{ ...group, collapsed: true }]} />)
    expect(flowProps.nodes?.[0]?.position.x).toBe(900)
    expect(flowProps.nodes?.[0]?.measured).toEqual({ width: 248, height: 120 })
    act(() => flowProps.onNodesChange?.([{ id: 'source', type: 'dimensions', dimensions: { width: 260, height: 130 } }]))
    expect(flowProps.nodes?.[0]?.measured).toEqual({ width: 260, height: 130 })
    expect(initial.onNodesChange).not.toHaveBeenCalled()
  })
})
