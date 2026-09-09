/** 显示临时路线及可见降级；路径过期时直接使用 ReactFlow 当帧真实端点，不沿用旧锚点。 */
import { BaseEdge, getSmoothStepPath, type EdgeProps } from '@xyflow/react'
import { memo } from 'react'
import type { WorkflowEdge } from '../../model'
import type { Point } from '../geometry/types'

/** 有序输入标签沿实际折线路程居中，不压到目标端口。 */
export function routeLabelPosition(points: readonly Point[]): Point | undefined {
  if (points.length < 2) return undefined
  const lengths = points.slice(1).map((point, index) => Math.abs(point.x - points[index].x) + Math.abs(point.y - points[index].y))
  let distance = lengths.reduce((sum, length) => sum + length, 0) / 2
  for (let index = 0; index < lengths.length; index++) {
    if (distance <= lengths[index] && lengths[index] > 0) {
      const before = points[index], after = points[index + 1], fraction = distance / lengths[index]
      return { x: before.x + (after.x - before.x) * fraction, y: before.y + (after.y - before.y) * fraction }
    }
    distance -= lengths[index]
  }
  return points[0]
}

export const RoutedWorkflowEdge = memo(function RoutedWorkflowEdge(props: EdgeProps<WorkflowEdge>) {
  const { id, source, target, sourceHandleId, targetHandleId, sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition, data, markerEnd, selected, label, labelStyle, labelBgStyle } = props
  const current = data?.start && data.end && Math.abs(data.start.x - sourceX) < .01 &&
    Math.abs(data.start.y - sourceY) < .01 && Math.abs(data.end.x - targetX) < .01 && Math.abs(data.end.y - targetY) < .01
  const route = current ? data?.route : undefined
  const fallback = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
  const path = route ? data?.displayPath ?? route.path : fallback[0]
  const reason = route?.reason ?? (route ? null : 'measuring')
  const middle = routeLabelPosition(route?.points ?? [])
  return <g className={`routed-workflow-edge ${reason ? 'is-degraded' : ''} ${data?.highlighted || selected ? 'is-highlighted' : ''} ${data?.subdued && !selected ? 'is-subdued' : ''}`}
    data-edge-id={id} data-source={source} data-target={target}
    data-source-port={sourceHandleId ?? ''} data-target-port={targetHandleId ?? ''}
    data-route-status={reason ? reason === 'measuring' ? 'pending' : 'degraded' : 'routed'} data-route-reason={reason ?? ''}>
    <title>{data?.accessibleLabel ?? `${source} → ${target}`}{reason && reason !== 'measuring' ? '；简化路线，建议整理布局' : ''}</title>
    {/* 辅助描边覆盖未进入有界交叉细算的交点；不新增路径身份或连接圆点。 */}
    <path className="route-crossing-halo" d={path} aria-hidden="true" />
    <BaseEdge id={id} path={path} markerEnd={markerEnd} interactionWidth={20}
      label={label} labelX={middle?.x ?? fallback[1]} labelY={middle?.y ?? fallback[2]}
      labelStyle={labelStyle} labelBgStyle={labelBgStyle} />
  </g>
})
