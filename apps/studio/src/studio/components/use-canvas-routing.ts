/**
 * 一个画布只持有一个最新几何请求；每帧最多求解一次，过期帧/视图不发布结果。
 * 拖动 8ms、静止 32ms 是协作式工作预算，不是硬实时承诺；超预算保留明确降级线。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { WorkflowEdge } from '../../model'
import { GeometrySession } from '../geometry/session'
import type { GeometryGraph, GeometryResult } from '../geometry/types'
import type { CanvasMeasurement } from './CanvasGeometryObserver'

export function geometryFromMeasurement(measurement: CanvasMeasurement | null, edges: readonly WorkflowEdge[]): GeometryGraph {
  const nodes = measurement?.nodes ?? []
  const byId = new Map(nodes.map((node) => [node.id, node]))
  return { nodes: nodes.map(({ id, x, y, width, height }) => ({ id, x, y, width, height })), edges: edges.flatMap((edge) => {
    const start = byId.get(edge.source)?.outputs[edge.sourceHandle ?? '']
    const end = byId.get(edge.target)?.inputs[edge.targetHandle ?? '']
    if (!start || !end) return []
    return [{ id: edge.id, source: edge.source, target: edge.target,
      sourcePort: edge.sourceHandle ?? '', targetPort: edge.targetHandle ?? '', start, end, ordinal: edge.data?.ordinal ?? null }]
  }) }
}

export function useCanvasRouting(viewKey: string, measurement: CanvasMeasurement | null, edges: readonly WorkflowEdge[]) {
  const session = useRef<GeometrySession | null>(null)
  if (!session.current) session.current = new GeometrySession()
  const graph = useMemo(() => geometryFromMeasurement(measurement, edges), [measurement, edges])
  const geometryKey = JSON.stringify(graph)
  const measured = measurement !== null
  const dragging = measurement?.dragging ?? false
  const identity = JSON.stringify([viewKey, measured, geometryKey, dragging])
  const latest = useRef({ identity, graph })
  latest.current = { identity, graph }
  const serial = useRef(0)
  const solveCount = useRef(0)
  const maxSlice = useRef(0)
  const [solved, setSolved] = useState<{ readonly identity: string; readonly result: GeometryResult } | null>(null)
  useEffect(() => {
    const request = ++serial.current
    // 暂缺测量不是用户删除了整张图：拒绝旧结果，但不拿空投影清掉可复用的几何缓存。
    // 真正空图有非 null 的完整测量，仍正常清理当前路线。
    if (!measured) return
    const frame = requestAnimationFrame(() => {
      if (request !== serial.current || latest.current.identity !== identity) return
      const result = session.current!.route(viewKey, latest.current.graph, { maxMs: dragging ? 8 : 32, maxEdgeMs: dragging ? 4 : 12 })
      if (request !== serial.current || latest.current.identity !== identity) return
      solveCount.current += 1
      maxSlice.current = Math.max(maxSlice.current, result.stats.elapsedMs)
      setSolved({ identity, result })
    })
    return () => { cancelAnimationFrame(frame); serial.current += 1 }
  }, [identity, viewKey, dragging, measured])
  const result = solved?.identity === identity ? solved.result : null
  return { graph, geometryKey, identity, result, pending: result === null,
    solveCount: solveCount.current, maxSliceMs: maxSlice.current }
}
