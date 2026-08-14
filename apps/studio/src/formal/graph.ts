import type { PortSpec } from '../generated/engine-manifest.generated'
import type { WorkflowSpec } from '../generated/authoring-wire.generated'
import type { EngineManifest } from '../generated/engine-manifest.generated'
import { coreNodeContracts, studioAuthority } from './contracts'
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
  return projectWorkflowGraph(authority.snapshot.spec, (digest) => manifestByDigest.get(digest))
}

export function projectDefaultWorkflowGraph(): GraphProjection {
  const manifests = studioAuthority.manifests as ReadonlyArray<EngineManifest>
  const nodes = studioAuthority.workflow_spec.nodes
  const bindingByDigest = new Map(
    nodes.flatMap((node) => {
      const binding = node.kind === 'engine_stage' || node.kind === 'core_operator'
        ? node.engine
        : undefined
      if (binding) {
        const manifest = manifests.find(
          (item) =>
            item.engine_id === binding.engine_id &&
            item.engine_version === binding.engine_version,
        )
        return manifest ? [[binding.manifest_digest, manifest] as const] : []
      }
      return []
    }),
  )
  return projectWorkflowGraph(
    studioAuthority.workflow_spec as WorkflowSpec,
    (digest) => bindingByDigest.get(digest),
  )
}

function projectWorkflowGraph(
  spec: WorkflowSpec,
  resolveManifest: (digest: string) => EngineManifest | undefined,
): GraphProjection {
  const kindOrder = { source: 0, engine_stage: 1, core_operator: 2, final: 3 } as const
  const orderedNodes = [...spec.nodes].sort(
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
      const contract = studioAuthority.operator_contracts.find(
        (item) => item.operator_kind === node.operator_kind && item.media_kind === node.media_kind,
      )
      if (!contract) {
        throw new Error(`缺少 Core Operator projection：${node.operator_kind}/${node.media_kind}`)
      }
      const manifest = node.engine ? resolveManifest(node.engine.manifest_digest) : undefined
      if (node.operator_kind === 'map' && !manifest) {
        throw new Error(`Map 缺少 EngineManifest projection：${node.engine?.manifest_digest ?? 'none'}`)
      }
      const label = {
        partition: 'Partition',
        map: `Map · ${manifest?.display_name ?? 'Engine'}`,
        select: 'Select',
        passthrough: 'Passthrough',
        collect: 'Collect',
        reduce: 'Reduce',
      }[node.operator_kind]
      return {
        id: node.node_id,
        type: 'formalWorkflow',
        position: { x: 100 + index * 340, y: 180 },
        data: {
          label,
          nodeId: node.node_id,
          category: 'operator',
          scope: contract.outputs[0]?.scope ?? contract.inputs[0].scope,
          inputs: contract.inputs.map(graphPort),
          outputs: contract.outputs.map(graphPort),
          engine: node.engine ?? undefined,
          executionMode: manifest?.execution_mode,
          parameters: node.parameters,
          parameterSchema: manifest?.parameter_schema,
          description: `Python Core Operator · ${node.operator_kind}`,
        },
      }
    }
    const manifest = resolveManifest(node.engine.manifest_digest)
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
        parameterSchema: manifest.parameter_schema,
        description: `${manifest.engine_id}@${manifest.engine_version}`,
      },
    }
  })

  const edges = spec.edges.map(
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
