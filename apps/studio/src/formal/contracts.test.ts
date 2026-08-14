import { describe, expect, it } from 'vitest'
import fixture from '../test/fixtures/python-authority.json'
import studioAuthorityFixture from '../generated/studio-authority.json'
import {
  ContractBoundaryError,
  parseAuthoringCommand,
  parseAuthoringResponse,
  parseEngineManifest,
  parseStudioAuthority,
  sha256Digest,
  verifyManifestBinding,
  verifyProjectionAuthority,
  verifySnapshot,
} from './contracts'
import { projectDefaultWorkflowGraph } from './graph'

describe('Python wire Schema runtime boundary', () => {
  it('解析并复核 Python 生成的 snapshot、Manifest 与 digest', async () => {
    const response = parseAuthoringResponse(fixture.initial)
    expect(response.result_kind).toBe('draft_snapshot')
    if (response.result_kind !== 'draft_snapshot') throw new Error('fixture 不是 snapshot')
    const manifest = parseEngineManifest(fixture.manifest)
    const engine = response.spec.nodes.find((node) => node.kind === 'engine_stage')
    if (!engine || engine.kind !== 'engine_stage') throw new Error('fixture 缺少 EngineStage')

    await expect(verifyProjectionAuthority()).resolves.toBeUndefined()
    await expect(verifySnapshot(response)).resolves.toBeUndefined()
    await expect(verifyManifestBinding(engine.engine, manifest)).resolves.toBeUndefined()
  })

  it('未知字段、缺失版本和未知 result_kind 默认 fail closed', () => {
    expect(() => parseAuthoringResponse({ ...fixture.initial, unexpected: true })).toThrow(
      ContractBoundaryError,
    )
    const { authoring_contract_version: _ignored, ...missingVersion } = fixture.initial
    expect(() => parseAuthoringResponse(missingVersion)).toThrow(ContractBoundaryError)
    expect(() => parseAuthoringResponse({ ...fixture.initial, result_kind: 'future' })).toThrow(
      ContractBoundaryError,
    )
  })

  it('outbound AuthoringCommand 也由同一 Python Schema 驱动', () => {
    const command = parseAuthoringCommand({
      authoring_contract_version: '0.1.0',
      command_id: 'command.contract.test',
      draft_id: 'draft.synthetic.program',
      base_revision: 0,
      intent: {
        intent_kind: 'connect_ports',
        source: { node_id: 'node.engine.filter', port_id: 'program_out' },
        target: { node_id: 'node.final.program', port_id: 'program' },
      },
    })

    expect(command.intent.intent_kind).toBe('connect_ports')
    expect(() => parseAuthoringCommand({ ...command, shell: 'unsafe' })).toThrow(
      ContractBoundaryError,
    )
  })

  it('JCS digest 保留 JSON boolean 与 number 的类型差异', async () => {
    await expect(sha256Digest({ value: true })).resolves.not.toBe(
      await sha256Digest({ value: 1 }),
    )
  })

  it('解析 Python 生成的 Core Operator、Plan 与 Runtime Studio authority', () => {
    const authority = parseStudioAuthority(studioAuthorityFixture)
    expect(authority.workflow_spec.workflow_contract_version).toBe('0.2.0')
    expect(authority.operator_contracts).toHaveLength(12)
    expect(authority.execution_plan.nodes).toHaveLength(13)
    expect(authority.runtime_snapshot.runtime.nodes.find((node) => node.state === 'ready')?.plan_node_id).toBe('plan.node.demux')
    expect(() => parseStudioAuthority({ ...studioAuthorityFixture, shell: 'unsafe' })).toThrow(
      ContractBoundaryError,
    )
  })

  it('默认 Designer 图使用 Python operator ports 和显式原始音轨边', () => {
    const graph = projectDefaultWorkflowGraph()
    expect(graph.nodes.filter((node) => node.data.category === 'operator')).toHaveLength(4)
    expect(graph.nodes.find((node) => node.id === 'node.partition')?.data.outputs[0]).toMatchObject({
      portId: 'out',
      scope: 'chapter',
      cardinality: 'set',
    })
    expect(graph.edges.find((edge) => edge.id === 'edge.original-audio-mux')).toMatchObject({
      source: 'node.demux',
      sourceHandle: 'audio_out',
      target: 'node.mux',
      targetHandle: 'audio_in',
    })
  })
})
