import type { Edge, Node } from '@xyflow/react'
import type { EngineBinding, JsonValue } from '../generated/authoring-wire.generated'
import type {
  ArtifactType,
  Cardinality,
  ExecutionMode,
  MediaKind,
  Scope,
} from '../generated/engine-manifest.generated'

export interface GraphPort {
  readonly portId: string
  readonly artifactType: ArtifactType
  readonly mediaKind: MediaKind | null
  readonly scope: Scope
  readonly cardinality: Cardinality
}

export interface FormalNodeData extends Record<string, unknown> {
  readonly label: string
  readonly nodeId: string
  readonly category: 'source' | 'engine' | 'final'
  readonly scope: Scope
  readonly inputs: ReadonlyArray<GraphPort>
  readonly outputs: ReadonlyArray<GraphPort>
  readonly engine?: EngineBinding
  readonly executionMode?: ExecutionMode
  readonly parameters?: Readonly<Record<string, JsonValue>>
  readonly description: string
}

export type FormalWorkflowNode = Node<FormalNodeData, 'formalWorkflow'>
export type FormalWorkflowEdge = Edge
