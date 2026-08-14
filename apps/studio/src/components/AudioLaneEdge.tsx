import { BaseEdge, EdgeLabelRenderer, type EdgeProps } from '@xyflow/react'

/**
 * 将原始音轨旁路固定投影到视频处理链下方，保持长距离数据边可见且不遮挡节点。
 * 这只是 EditorState 层的路由表现；正式 WorkflowSpec 仍保存普通 typed media edge。
 */
export function AudioLaneEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  markerEnd,
  label,
}: EdgeProps) {
  const laneY = Math.max(sourceY, targetY) + 280
  const sourceTurnX = sourceX + 42
  const targetTurnX = targetX - 42
  const labelX = Math.min(sourceTurnX + 150, (sourceTurnX + targetTurnX) / 2)
  const edgePath = [
    `M ${sourceX} ${sourceY}`,
    `L ${sourceTurnX} ${sourceY}`,
    `L ${sourceTurnX} ${laneY}`,
    `L ${targetTurnX} ${laneY}`,
    `L ${targetTurnX} ${targetY}`,
    `L ${targetX} ${targetY}`,
  ].join(' ')

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{ stroke: '#55b0d2', strokeDasharray: '8 5', strokeWidth: 2 }}
      />
      <EdgeLabelRenderer>
        <div
          className="audio-edge-label"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${laneY}px)` }}
        >
          <span>AUDIO LANE</span>
          <strong>{label}</strong>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
