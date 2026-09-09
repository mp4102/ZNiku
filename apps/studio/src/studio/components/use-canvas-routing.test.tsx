/** 验证实际 React 调度只保留最新帧，视图和几何变更不会回灌旧路线。 */
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowEdge } from '../../model'
import type { CanvasMeasurement } from './CanvasGeometryObserver'
import { geometryFromMeasurement, useCanvasRouting } from './use-canvas-routing'

let frames = new Map<number, FrameRequestCallback>(), nextFrame = 0
beforeEach(() => {
  frames = new Map(); nextFrame = 0
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { frames.delete(id) })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
function flush() {
  const pending = [...frames.values()]; frames.clear()
  act(() => { for (const callback of pending) callback(0) })
}
function measurement(x = 0, dragging = false): CanvasMeasurement {
  const nodes: CanvasMeasurement['nodes'] = [
    { id: 'source', x, y: 0, width: 100, height: 100, inputs: {}, outputs: { out: { x: x + 106, y: 50 } } },
    { id: 'target', x: 500, y: 0, width: 100, height: 100, inputs: { in: { x: 494, y: 50 } }, outputs: {} },
    { id: 'obstacle', x: 250, y: 200, width: 100, height: 100, inputs: {}, outputs: {} },
  ]
  return { nodes, dragging, key: JSON.stringify([nodes, dragging]), shapeKey: 'synthetic', readMs: 0 }
}
const edges: WorkflowEdge[] = [{ id: 'edge', source: 'source', sourceHandle: 'out', target: 'target', targetHandle: 'in', data: { ordinal: null } }]

describe('有界最新几何调度', () => {
  it('跨帧缺少测量只取消旧请求，不清缓存；真实空图仍完成求解', () => {
    const { result, rerender } = renderHook(({ value }: { value: CanvasMeasurement | null }) => useCanvasRouting('current', value, edges),
      { initialProps: { value: measurement(0, true) as CanvasMeasurement | null } })
    flush()
    expect(result.current.solveCount).toBe(1)
    rerender({ value: measurement(10, true) })
    const stale = [...frames.values()][0]
    rerender({ value: null })
    expect(frames.size).toBe(0)
    act(() => stale(0))
    flush()
    expect(result.current.pending).toBe(true)
    expect(result.current.result).toBeNull()
    expect(result.current.solveCount).toBe(1)
    const next = measurement(0, true)
    rerender({ value: { ...next, nodes: next.nodes.map((node) => node.id === 'obstacle' ? { ...node, y: node.y + 10 } : node) } })
    flush()
    expect(result.current.result?.stats.cacheHits).toBe(1)
    expect(result.current.result?.stats.routed).toBe(0)
    rerender({ value: null })
    flush()
    rerender({ value: { ...measurement(), nodes: [] } })
    flush()
    expect(result.current.pending).toBe(false)
    expect(result.current.result?.routes).toEqual([])
  })

  it('连续位置更新合并为一帧，取消帧即使迟到也不能写入旧位置', () => {
    const { result, rerender } = renderHook(({ value }) => useCanvasRouting('current', value, edges), { initialProps: { value: measurement() } })
    const stale = [...frames.values()][0]
    for (let x = 1; x <= 40; x++) rerender({ value: measurement(x, true) })
    expect(frames.size).toBe(1)
    act(() => stale(0))
    expect(result.current.result).toBeNull()
    flush()
    expect(result.current.solveCount).toBe(1)
    expect(result.current.result?.routes[0].path).toMatch(/^M146[ ,]50/)
    expect(result.current.graph.edges[0].start).toEqual({ x: 146, y: 50 })
    expect(edges[0].data).toEqual({ ordinal: null })
  })

  it('相同几何的新投影和进度类读数不排新任务；松手安排一次完整求解', () => {
    const { result, rerender } = renderHook(({ value, connections }) => useCanvasRouting('current', value, connections),
      { initialProps: { value: measurement(0, true), connections: edges } })
    flush()
    const first = result.current.result
    for (let index = 0; index < 20; index++) {
      rerender({ value: { ...measurement(0, true), readMs: index }, connections: edges.map((edge) => ({ ...edge, selected: !!(index % 2) })) })
    }
    expect(frames.size).toBe(0)
    expect(result.current.result).toBe(first)
    expect(result.current.solveCount).toBe(1)
    rerender({ value: measurement(), connections: edges })
    expect(frames.size).toBe(1)
    flush()
    expect(result.current.solveCount).toBe(2)
  })

  it('current/历史同 ID 不同位置的旧请求拒绝；卸载取消剩余帧', () => {
    const { result, rerender, unmount } = renderHook(({ view, value }) => useCanvasRouting(view, value, edges),
      { initialProps: { view: 'project:current', value: measurement() } })
    const currentFrame = [...frames.values()][0]
    rerender({ view: 'project:run-older', value: measurement(50) })
    act(() => currentFrame(0))
    expect(result.current.result).toBeNull()
    flush()
    expect(result.current.result?.routes[0].path).toMatch(/^M156[ ,]50/)
    rerender({ view: 'project:current', value: measurement() })
    expect(result.current.result).toBeNull()
    unmount()
    expect(frames.size).toBe(0)
  })

  it('缺少测量端口不猜边；独立障碍矩形保留，已知 ordinal 不变', () => {
    const value = measurement()
    const graph = geometryFromMeasurement(value, [...edges, { ...edges[0], id: 'unknown-port', sourceHandle: 'missing' }])
    expect(graph.nodes).toHaveLength(3)
    expect(graph.edges).toHaveLength(1)
    expect(graph.edges[0].ordinal).toBeNull()
    expect(geometryFromMeasurement(null, edges)).toEqual({ nodes: [], edges: [] })
  })
})
