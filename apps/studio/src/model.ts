/** 定义 ReactFlow 的显示模型；字段全部由 0.3.0 Project Service wire 数据投影而来。 */

import type { Edge, Node } from '@xyflow/react'
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
}

export type WorkflowNode = Node<WorkflowNodeData, 'workflow'>
export type WorkflowEdge = Edge<{ readonly ordinal: number | null }, 'smoothstep'>
