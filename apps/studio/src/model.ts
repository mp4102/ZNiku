/** 定义 ReactFlow 的显示模型；字段全部由 0.3.0 Project Service wire 数据投影而来。 */

import type { Edge, Node } from '@xyflow/react'
import type { GeometryRoute, Point } from './studio/geometry/types'
import type {
  LatestResultWire,
  NodeProgressProjectionWire,
  NodeRunWire,
  PortSpecWire,
} from './studio/contracts'

export interface WorkflowProgressData {
  readonly mode: 'determinate' | 'indeterminate' | 'completed' | 'none'
  readonly fraction: number | null
  readonly measurement: NodeProgressProjectionWire | null
  readonly elapsed: string | null
}

export interface WorkflowNodeData extends Record<string, unknown> {
  readonly label: string
  readonly instanceId: string
  readonly summaries: ReadonlyArray<string>
  readonly typeId: string
  readonly definitionVersion: string
  readonly executorKind: 'python' | 'command' | 'manual_external'
  readonly inputs: ReadonlyArray<PortSpecWire>
  readonly outputs: ReadonlyArray<PortSpecWire>
  readonly nodeRun: NodeRunWire | null
  readonly progress: WorkflowProgressData
  readonly latestResult: LatestResultWire | null
  readonly advanced?: boolean
  readonly collapsed?: boolean
  readonly iconToken?: string
  readonly portLabels?: {
    readonly input: Readonly<Record<string, string>>
    readonly output: Readonly<Record<string, string>>
  }
  readonly connecting?: boolean
  readonly compatibleInputPortIds?: ReadonlyArray<string>
  readonly compatibleOutputPortIds?: ReadonlyArray<string>
  readonly groupLabel?: string
  readonly groupColorToken?: string
  readonly problemSummary?: string
}

export type WorkflowNode = Node<WorkflowNodeData, 'workflow'>
/** 路线仅是当前画布的临时投影，不进入 Graph/StudioState。 */
export interface WorkflowEdgeData extends Record<string, unknown> {
  readonly ordinal: number | null
  readonly route?: GeometryRoute
  readonly start?: Point
  readonly end?: Point
  readonly displayPath?: string
  readonly highlighted?: boolean
  readonly subdued?: boolean
  readonly accessibleLabel?: string
}
export type WorkflowEdge = Edge<WorkflowEdgeData, 'smoothstep' | 'routed'>
