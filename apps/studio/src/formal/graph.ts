import type { PortSpec } from '../generated/engine-manifest.generated'
import { coreNodeContracts } from './contracts'
import type { AuthorityState } from './gateway'
import type { FormalWorkflowEdge, FormalWorkflowNode, GraphPort } from './graph-model'

function graphPort(port: PortSpec): GraphPort {
  return {
    portId: port.port_id,
    artifactType: port.artifact_type,
    mediaKind: port.media_kind,
    scope: port.scope,
    cardinality: port.cardinality,
  }
}

export interface GraphProjection {
  readonly nodes: FormalWorkflowNode[]
  readonly edges: FormalWorkflowEdge[]
}

export function projectAuthorityGraph(authority: AuthorityState): GraphProjection {
  const manifestByDigest = new Map(
    authority.manifests.map((resolved) => [resolved.binding.manifest_digest, resolved.manifest]),
  )
  const kindOrder = { source: 0, engine_stage: 1, core_operator: 2, final: 3 } as const
  const orderedNodes = [...authority.snapshot.spec.nodes].sort(
    (left, right) => kindOrder[left.kind] - kindOrder[right.kind] || left.node_id.localeCompare(right.node_id),
  )
  const nodes = orderedNodes.map((node, index): FormalWorkflowNode => {
    if (node.kind === 'source') {
      return {
        id: node.node_id,
        type: 'formalWorkflow',
        position: { x: 100 + index * 340, y: 180 },
        data: {
          label: 'Program Source',
          nodeId: node.node_id,
          category: 'source',
          scope: 'program',
          inputs: [],
          outputs: coreNodeContracts.source_outputs.map(graphPort),
          description: 'Python CoreNodeContractSet 定义的 program source slot。',
        },
      }
    }
    if (node.kind === 'final') {
      return {
        id: node.node_id,
        type: 'formalWorkflow',
        position: { x: 100 + index * 340, y: 180 },
        data: {
          label: 'Final',
          nodeId: node.node_id,
          category: 'final',
          scope: 'program',
          inputs: coreNodeContracts.final_inputs.map(graphPort),
          outputs: [],
          description: '唯一终端类别；Phase 2A 不执行发布。',
        },
      }
    }
    if (node.kind === 'core_operator') {
      throw new Error('当前 Studio projection 尚未接入 Core Operator contract，已失败关闭。')
    }
    const manifest = manifestByDigest.get(node.engine.manifest_digest)
    if (!manifest) throw new Error(`缺少 EngineManifest projection：${node.engine.manifest_digest}`)
    return {
      id: node.node_id,
      type: 'formalWorkflow',
      position: { x: 100 + index * 340, y: 180 },
      data: {
        label: manifest.display_name,
        nodeId: node.node_id,
        category: 'engine',
        scope: manifest.supported_scopes[0],
        inputs: manifest.inputs.map((contract) => graphPort(contract.port)),
        outputs: manifest.outputs.map((contract) => graphPort(contract.port)),
        engine: node.engine,
        executionMode: manifest.execution_mode,
        parameters: node.parameters,
        description: `${manifest.engine_id}@${manifest.engine_version}`,
      },
    }
  })

  const edges = authority.snapshot.spec.edges.map(
    (edge): FormalWorkflowEdge => ({
      id: edge.edge_id,
      source: edge.source.node_id,
      sourceHandle: edge.source.port_id,
      target: edge.target.node_id,
      targetHandle: edge.target.port_id,
      type: 'smoothstep',
    }),
  )
  return { nodes, edges }
}
