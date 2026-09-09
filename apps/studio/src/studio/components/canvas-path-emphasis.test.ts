/** 鼠标/键盘投影共用纯追踪集合；不通过强调修改 Graph 或 ordered_many。 */
import { describe, expect, it } from 'vitest'
import type { WorkflowEdge } from '../../model'
import { emphasizedEdges, type PathFocus } from './canvas-path-emphasis'

function edge(id: string, source: string, target: string, sourceHandle = 'video', targetHandle = 'video', ordinal: number | null = null): WorkflowEdge {
  return { id, source, target, sourceHandle, targetHandle, type: 'routed', data: { ordinal } }
}
const edges: WorkflowEdge[] = [
  edge('in-left', 'source-left', 'branch'), edge('in-right', 'source-right', 'branch', 'audio', 'audio'),
  edge('upper', 'branch', 'upper', 'video'), edge('lower', 'branch', 'lower', 'audio'),
  edge('merge-first', 'upper', 'merge', 'video', 'clips', 0), edge('merge-second', 'lower', 'merge', 'video', 'clips', 1),
  edge('final', 'merge', 'final'), edge('unrelated', 'other-source', 'other-output'),
]
const ids = (focus: PathFocus | null, mode: 'direct' | 'upstream' | 'downstream') => [...emphasizedEdges(edges, focus, mode)].sort()

describe('直连与上下游路径强调', () => {
  it('未聚焦不隐藏任何 Edge；选中节点只强调其直接输入和输出', () => {
    expect(ids(null, 'direct')).toEqual([])
    expect(ids({ nodeId: 'branch' }, 'direct')).toEqual(['in-left', 'in-right', 'lower', 'upper'])
  })
  it('鼠标/键盘得到同一输入或输出端口身份时只强调该端口连接，不能串到同名反向端口', () => {
    const keyboardFocus: PathFocus = { nodeId: 'branch', direction: 'input', portId: 'video' }
    const mouseFocus = { ...keyboardFocus }
    expect(ids(keyboardFocus, 'direct')).toEqual(['in-left'])
    expect(ids(mouseFocus, 'direct')).toEqual(ids(keyboardFocus, 'direct'))
    expect(ids({ ...keyboardFocus, direction: 'output' }, 'direct')).toEqual(['upper'])
    expect(ids({ nodeId: 'merge', direction: 'input', portId: 'clips' }, 'direct')).toEqual(['merge-first', 'merge-second'])
  })
  it('上游/下游遍历保留分支汇合，只过滤起点端口而不错误限制后继端口名', () => {
    expect(ids({ nodeId: 'merge' }, 'upstream')).toEqual(['in-left', 'in-right', 'lower', 'merge-first', 'merge-second', 'upper'])
    expect(ids({ nodeId: 'branch', direction: 'output', portId: 'video' }, 'downstream')).toEqual(['final', 'merge-first', 'upper'])
    expect(ids({ nodeId: 'branch', direction: 'input', portId: 'video' }, 'upstream')).toEqual(['in-left'])
    expect(ids({ nodeId: 'merge', direction: 'input', portId: 'clips' }, 'upstream')).toEqual(['in-left', 'in-right', 'lower', 'merge-first', 'merge-second', 'upper'])
  })
  it('不兼容方向、已删除节点/端口都不会强调无关分支', () => {
    expect(ids({ nodeId: 'branch', direction: 'input', portId: 'video' }, 'downstream')).toEqual([])
    expect(ids({ nodeId: 'branch', direction: 'output', portId: 'video' }, 'upstream')).toEqual([])
    expect(ids({ nodeId: 'missing', portId: 'video', direction: 'input' }, 'direct')).toEqual([])
    expect(ids({ nodeId: 'branch', portId: 'missing', direction: 'input' }, 'direct')).toEqual([])
  })
  it('遍历只投影集合、保持全部 Edge/ordinal；即使输入暂含回环也有限结束', () => {
    const before = JSON.stringify(edges)
    for (const mode of ['direct', 'upstream', 'downstream'] as const) ids({ nodeId: 'merge' }, mode)
    expect(JSON.stringify(edges)).toBe(before)
    const cycle = [...edges, edge('synthetic-cycle', 'final', 'branch')]
    const result = emphasizedEdges(cycle, { nodeId: 'branch' }, 'downstream')
    expect([...result].sort()).toEqual(['final', 'lower', 'merge-first', 'merge-second', 'synthetic-cycle', 'upper'])
    expect(cycle).toHaveLength(edges.length + 1)
  })
})
