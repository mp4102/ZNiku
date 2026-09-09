/** 验证读模型只取实际 ReactFlow 几何，使用真实 handle 外缘而非估算行位置。 */
import { describe, expect, it } from 'vitest'
import type { ReactFlowState } from '@xyflow/react'
import type { WorkflowNode } from '../../model'
import { selectCanvasMeasurement } from './CanvasGeometryObserver'

function state(progress = '00:01', collapsed = false, measured = true): Pick<ReactFlowState, 'nodeLookup'> {
  const userNode: WorkflowNode = { id: 'node', type: 'workflow', position: { x: 50, y: 80 }, data: {
    label: '合成节点', instanceId: 'node', typeId: 'synthetic', definitionVersion: '0.2.0', executorKind: 'python',
    summaries: [], inputs: [{ port_id: 'in', data_type: 'DataFile', cardinality: 'one', required: true }],
    outputs: [{ port_id: 'out', data_type: 'DataFile', cardinality: 'one', required: true }], collapsed,
    nodeRun: null, latestResult: null, progress: { mode: 'none', fraction: null, measurement: null, elapsed: progress },
  } }
  return { nodeLookup: new Map([['node', { ...userNode, measured: measured ? { width: 248, height: 180 } : {}, internals: {
    userNode, positionAbsolute: { x: 50.5, y: 80.25 }, z: 0, handleBounds: {
      source: [{ id: 'out', x: 242, y: 64, width: 12, height: 12, position: 'right', nodeId: 'node', type: 'source' }],
      target: [{ id: 'in', x: -6, y: 96, width: 12, height: 12, position: 'left', nodeId: 'node', type: 'target' }],
    },
  } }]]) as ReactFlowState['nodeLookup'] }
}

describe('ReactFlow 几何读取', () => {
  it('保持分数坐标、左右 handle 外缘与原始节点矩形，未测量不猜尺寸', () => {
    const geometry = selectCanvasMeasurement(state())
    expect(geometry.nodes[0]).toMatchObject({ id: 'node', x: 50.5, y: 80.25, width: 248, height: 180,
      outputs: { out: { x: 304.5, y: 150.25 } }, inputs: { in: { x: 44.5, y: 182.25 } } })
    expect(selectCanvasMeasurement(state('00:01', false, false)).nodes).toEqual([])
  })
  it('普通进度不改变几何身份，同位置不同结构会隔离旧测量', () => {
    const first = selectCanvasMeasurement(state())
    expect(selectCanvasMeasurement(state('01:00')).key).toBe(first.key)
    expect(selectCanvasMeasurement(state('00:01', true)).shapeKey).not.toBe(first.shapeKey)
    expect(first.nodes[0]).not.toHaveProperty('progress')
    expect(first.nodes[0]).not.toHaveProperty('parameters')
  })
})
