import type { Edge, Node } from '@xyflow/react'

export type WorkspaceMode = 'designer' | 'plan' | 'run'
export type NodeCategory = 'source' | 'operator' | 'engine' | 'final'
export type RunStatus =
  | 'pending'
  | 'ready'
  | 'running'
  | 'waiting_operator'
  | 'verifying'
  | 'complete'

export interface MediaChange {
  label: string
  value: string
  tone?: 'neutral' | 'changed' | 'warning'
}

export interface MediaPort {
  id: string
  label: string
  artifactType: string
  scope: 'program' | 'chapter' | 'leaf'
  cardinality: 'one' | 'optional' | 'set'
}

export interface WorkflowNodeData extends Record<string, unknown> {
  label: string
  protocolId: string
  subtitle: string
  category: NodeCategory
  icon: string
  scope: 'program' | 'chapter' | 'leaf'
  inputs: MediaPort[]
  outputs: MediaPort[]
  engineVersion?: string
  execution?: 'automatic' | 'manual external' | 'runtime operator'
  description: string
  mediaChanges: MediaChange[]
  runStatus?: RunStatus
  instanceLabel?: string
}

export type WorkflowNode = Node<WorkflowNodeData, 'workflow'>
export type WorkflowEdge = Edge

export interface PaletteItem {
  id: string
  label: string
  category: NodeCategory
  icon: string
  scope: 'program' | 'chapter'
  description: string
  inputs: MediaPort[]
  outputs: MediaPort[]
}

export interface Diagnostic {
  code: string
  severity: 'info' | 'warning' | 'error'
  title: string
  message: string
  entity?: string
}
