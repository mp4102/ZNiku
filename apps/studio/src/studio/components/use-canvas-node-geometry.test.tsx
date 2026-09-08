/** 画布测量回归：拖动不丢尺寸，临时状态不进入 Graph 编辑，相同 ID 的视图不串用位置。 */
import { act, cleanup, renderHook } from '@testing-library/react'
import type { NodeChange } from '@xyflow/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowNode } from '../../model'
import { useCanvasNodeGeometry } from './use-canvas-node-geometry'

afterEach(cleanup)

function node(id = 'source', x = 40): WorkflowNode {
  return { id, type: 'workflow', position: { x, y: 50 }, data: {
    label: '导入视频', instanceId: id, typeId: 'synthetic.source', definitionVersion: '0.2.0',
    executorKind: 'python', inputs: [], outputs: [], summaries: ['素材：合成短片'],
    nodeRun: null, latestResult: null, progress: { mode: 'none', fraction: null, measurement: null, elapsed: null },
  } }
}
const measured = (id = 'source', width = 248, height = 180): NodeChange<WorkflowNode> => ({
  id, type: 'dimensions', dimensions: { width, height },
})

describe('ReactFlow session 测量缓存', () => {
  it('连续 40 次父级位置投影保留所有节点尺寸，尺寸事件不形成 Graph mutation', () => {
    const onChange = vi.fn()
    const initial = [node(), node('other', 400)]
    const { result, rerender } = renderHook(({ nodes }) => useCanvasNodeGeometry(nodes, true, onChange), {
      initialProps: { nodes: initial },
    })
    act(() => result.current.onNodesChange([measured(), measured('other')]))
    expect(onChange).not.toHaveBeenCalled()
    for (let index = 1; index <= 40; index++) {
      const position: NodeChange<WorkflowNode> = { type: 'position', id: 'source', position: { x: 40 + index * 5, y: 50 }, dragging: true }
      act(() => result.current.onNodesChange([position]))
      rerender({ nodes: [node('source', 40 + index * 5), node('other', 400)] })
      expect(result.current.nodes.map((value) => value.measured)).toEqual([
        { width: 248, height: 180 }, { width: 248, height: 180 },
      ])
      expect(result.current.nodes[0]?.dragging).toBe(true)
      expect(result.current.nodes[0]?.position.x).toBe(40 + index * 5)
      expect(result.current.nodes[1]?.position.x).toBe(400)
      expect(onChange).toHaveBeenLastCalledWith([position])
    }
    const stop: NodeChange<WorkflowNode> = { type: 'position', id: 'source', dragging: false }
    act(() => result.current.onNodesChange([stop]))
    expect(result.current.nodes[0]?.dragging).toBe(false)
    expect(onChange).toHaveBeenCalledTimes(41)
    expect(initial[0]).not.toHaveProperty('measured')
    expect(initial[0]).not.toHaveProperty('dragging')
    expect(initial[0]?.position.x).toBe(40)
  })

  it('混合事件只透传原始 authoring 意图，不保存尺寸、改位置或创建额外拖动动作', () => {
    const onChange = vi.fn()
    const { result } = renderHook(() => useCanvasNodeGeometry([node()], true, onChange))
    const position: NodeChange<WorkflowNode> = { type: 'position', id: 'source', position: { x: 900, y: 500 }, dragging: true }
    act(() => result.current.onNodesChange([measured(), position]))
    expect(onChange).toHaveBeenCalledExactlyOnceWith([position])
    expect(onChange.mock.calls[0]?.[0][0]).toBe(position)
    expect(result.current.nodes[0]?.position).toEqual({ x: 40, y: 50 })
    expect(result.current.nodes[0]?.measured).toEqual({ width: 248, height: 180 })
  })

  it.each([
    ['折叠', { collapsed: true }],
    ['分组', { groupLabel: '第一章' }],
    ['显示密度', { advanced: true }],
    ['精确定义版本', { definitionVersion: '0.2.1' }],
    ['定义类型', { typeId: 'synthetic.other' }],
    ['动态端口', { outputs: [{ port_id: 'out', data_type: 'VideoFile', cardinality: 'one', required: true }] }],
  ] as const)('%s 改变时丢弃旧结构的测量并接受新测量', (_name, change) => {
    const onChange = vi.fn()
    const initial = node()
    const { result, rerender } = renderHook(({ nodes }) => useCanvasNodeGeometry(nodes, true, onChange), {
      initialProps: { nodes: [initial] },
    })
    act(() => result.current.onNodesChange([measured()]))
    rerender({ nodes: [{ ...initial, data: { ...initial.data, ...change } }] })
    expect(result.current.nodes[0]?.measured).toBeUndefined()
    act(() => result.current.onNodesChange([measured('source', 260, 120)]))
    expect(result.current.nodes[0]?.measured).toEqual({ width: 260, height: 120 })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('仅 Runtime 进度刷新保留测量，ResizeObserver 后续仍可更新真实尺寸', () => {
    const onChange = vi.fn()
    const initial = node()
    const { result, rerender } = renderHook(({ nodes }) => useCanvasNodeGeometry(nodes, true, onChange), {
      initialProps: { nodes: [initial] },
    })
    act(() => result.current.onNodesChange([measured()]))
    rerender({ nodes: [{ ...initial, data: { ...initial.data, progress: { ...initial.data.progress, elapsed: '00:12' } } }] })
    expect(result.current.nodes[0]?.measured).toEqual({ width: 248, height: 180 })
    expect(result.current.nodes[0]?.data.progress.elapsed).toBe('00:12')
    act(() => result.current.onNodesChange([measured('source', 248, 204)]))
    expect(result.current.nodes[0]?.measured).toEqual({ width: 248, height: 204 })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('current/snapshot 相同 ID 使用各自传入位置，并在只读视图清除旧拖动标记', () => {
    const onChange = vi.fn()
    const { result, rerender } = renderHook(({ nodes, editable }) => useCanvasNodeGeometry(nodes, editable, onChange), {
      initialProps: { nodes: [node('source', 900)], editable: true },
    })
    act(() => result.current.onNodesChange([measured(), { id: 'source', type: 'position', dragging: true }]))
    rerender({ nodes: [node('source', 40)], editable: false })
    expect(result.current.nodes[0]?.position.x).toBe(40)
    expect(result.current.nodes[0]?.dragging).toBe(false)
    act(() => result.current.onNodesChange([measured('source', 260, 200)]))
    rerender({ nodes: [node('source', 900)], editable: true })
    expect(result.current.nodes[0]?.position.x).toBe(900)
    expect(result.current.nodes[0]?.dragging).toBe(false)
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('删除节点释放缓存；未知节点和非正非有限尺寸不能污染画布', () => {
    const onChange = vi.fn()
    const { result, rerender } = renderHook(({ nodes }) => useCanvasNodeGeometry(nodes, true, onChange), {
      initialProps: { nodes: [node()] },
    })
    act(() => result.current.onNodesChange([measured(), measured('unknown')]))
    for (const invalid of [0, -1, Infinity, NaN]) {
      act(() => result.current.onNodesChange([measured('source', invalid)]))
      expect(result.current.nodes[0]?.measured).toEqual({ width: 248, height: 180 })
    }
    rerender({ nodes: [] })
    rerender({ nodes: [node()] })
    expect(result.current.nodes[0]?.measured).toBeUndefined()
    expect(onChange).not.toHaveBeenCalled()
  })
})
