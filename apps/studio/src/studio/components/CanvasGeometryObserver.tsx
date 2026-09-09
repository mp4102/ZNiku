/**
 * 只订阅 ReactFlow 已测量的几何，不订阅参数、运行进度或历史内容。
 * 端点与锁定 ReactFlow 的 handle 外缘规则一致；未测量时不猜端口位置。
 */
import { useStore, type ReactFlowState } from '@xyflow/react'
import { useEffect } from 'react'
import type { GeometryNode, Point } from '../geometry/types'
import type { WorkflowNode } from '../../model'
import { geometryShape } from './use-canvas-node-geometry'

export interface MeasuredCanvasNode extends GeometryNode {
  readonly inputs: Readonly<Record<string, Point>>
  readonly outputs: Readonly<Record<string, Point>>
}
export interface CanvasMeasurement {
  readonly nodes: readonly MeasuredCanvasNode[]
  readonly dragging: boolean
  readonly key: string
  readonly shapeKey: string
  readonly readMs: number
}

export function selectCanvasMeasurement(state: Pick<ReactFlowState, 'nodeLookup'>): CanvasMeasurement {
  const started = performance.now()
  const nodes: MeasuredCanvasNode[] = []
  const shapes: readonly string[][] = [...state.nodeLookup.values()].map((node) => [node.id, geometryShape(node.internals.userNode as WorkflowNode)])
  let dragging = false
  for (const node of state.nodeLookup.values()) {
    dragging ||= node.dragging === true
    const { width, height } = node.measured
    const { x, y } = node.internals.positionAbsolute
    if (![x, y, width, height].every((value) => typeof value === 'number' && Number.isFinite(value)) || !width || !height) continue
    const inputs: Record<string, Point> = {}, outputs: Record<string, Point> = {}
    for (const handle of node.internals.handleBounds?.target ?? []) {
      if (handle.id) inputs[handle.id] = { x: x + handle.x, y: y + handle.y + handle.height / 2 }
    }
    for (const handle of node.internals.handleBounds?.source ?? []) {
      if (handle.id) outputs[handle.id] = { x: x + handle.x + handle.width, y: y + handle.y + handle.height / 2 }
    }
    nodes.push({ id: node.id, x, y, width, height, inputs, outputs })
  }
  // 数字几何相等即跳过；这不是存储版本或领域 digest，DPR/缩放不改变画布坐标。
  const shapeKey = JSON.stringify(shapes)
  return { nodes, dragging, shapeKey, key: JSON.stringify([nodes, dragging, shapeKey]), readMs: performance.now() - started }
}

export function CanvasGeometryObserver({ viewKey, onMeasure }: {
  readonly viewKey: string
  readonly onMeasure: (viewKey: string, measurement: CanvasMeasurement) => void
}) {
  const measurement = useStore(selectCanvasMeasurement, (before, after) => before.key === after.key)
  useEffect(() => { onMeasure(viewKey, measurement) }, [viewKey, measurement, onMeasure])
  return null
}
