import Ajv2020, { type ErrorObject } from 'ajv/dist/2020.js'
import realMediaHostSchema from './real-media-host.schema.json'

export interface RealMonitorNode {
  readonly plan_node_id: string
  readonly stage_spec_id: string
  readonly subject_kind: string
  readonly scope: string
  readonly execution_mode: 'automatic' | 'manual_external' | 'runtime'
  readonly state: string
  readonly attempt: number
  readonly evidence_id: string | null
}

export interface RealMonitorHandoff {
  readonly handoff_id: string
  readonly plan_node_id: string
  readonly attempt: number
  readonly input_artifact_id: string
  readonly input_relative_path: string
  readonly candidate_relative_path: string
  readonly expected_frame_count: number
  readonly expected_frame_rate: string
  readonly fixture_operation: string
  readonly candidate_exists: boolean
}

export interface RealMonitorProjection {
  readonly monitor_contract_version: '0.1.0'
  readonly workflow_run_id: string
  readonly revision_id: string
  readonly execution_plan_digest: string
  readonly reference_filename: string
  readonly reference_digest: string
  readonly chapter_split_source_second: 30
  readonly chapters: ReadonlyArray<{
    readonly member_id: string
    readonly scope_id: string
    readonly start_frame: number
    readonly end_frame: number
  }>
  readonly nodes: ReadonlyArray<RealMonitorNode>
  readonly handoffs: ReadonlyArray<RealMonitorHandoff>
  readonly ready_node_ids: ReadonlyArray<string>
  readonly artifact_count: number
  readonly evidence_count: number
  readonly final_verified: boolean
  readonly final_artifact_id: string | null
  readonly final_digest: string | null
}

export interface RealHostEnvelope {
  readonly host_contract_version: '0.1.0'
  readonly candidate_exists: boolean
  readonly projection: RealMonitorProjection | null
}

export type RealHostCommand =
  | { readonly operation: 'start' | 'advance_automatic' }
  | { readonly operation: 'acceptance_fixture' | 'retry'; readonly plan_node_id: string }

export interface RealMediaGateway {
  inspect(): Promise<RealHostEnvelope>
  command(command: RealHostCommand): Promise<RealHostEnvelope>
}

export class RealMediaBoundaryError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'RealMediaBoundaryError'
  }
}

const ajv = new Ajv2020({ allErrors: true, strict: true })
const validateEnvelope = ajv.compile(realMediaHostSchema)

function validationMessage(errors: ErrorObject[] | null | undefined): string {
  return (errors ?? [])
    .slice(0, 3)
    .map((error) => `${error.instancePath || '/'} ${error.message ?? error.keyword}`)
    .join('; ')
}

export function parseRealHostEnvelope(value: unknown): RealHostEnvelope {
  if (!validateEnvelope(value)) {
    throw new RealMediaBoundaryError(
      `real media host payload 不符合 Python Schema：${validationMessage(validateEnvelope.errors)}`,
    )
  }
  return value as unknown as RealHostEnvelope
}

export class FetchRealMediaGateway implements RealMediaGateway {
  constructor(private readonly baseUrl = 'http://127.0.0.1:8765') {}

  async inspect(): Promise<RealHostEnvelope> {
    return this.request('/api/real-media/status', { method: 'GET' })
  }

  async command(command: RealHostCommand): Promise<RealHostEnvelope> {
    return this.request('/api/real-media/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(command),
    })
  }

  private async request(path: string, init: RequestInit): Promise<RealHostEnvelope> {
    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, init)
    } catch (error) {
      throw new RealMediaBoundaryError(
        `本地 ZNIKU Runtime host 不可用：${error instanceof Error ? error.message : 'network error'}`,
      )
    }
    const value: unknown = await response.json()
    if (!response.ok) {
      const code = typeof value === 'object' && value !== null && 'error' in value
        ? String(value.error)
        : `HTTP ${response.status}`
      throw new RealMediaBoundaryError(`Runtime command 失败：${code}`)
    }
    return parseRealHostEnvelope(value)
  }
}
