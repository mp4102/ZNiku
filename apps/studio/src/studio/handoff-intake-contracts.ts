/** 单文件交回只投影 Python 的实际容器、规范命名和检查任务，不在前端猜测业务规则。 */
import type { NodeRunWire, RunDetailEnvelope } from './contracts'
import type { HandoffImportBinding } from './host-bridge'

export interface HandoffIntakeCandidate {
  readonly candidate_handle: string
  readonly name: string
  readonly size: number
  readonly container: string
}
export interface HandoffIntakeObserveEnvelope extends HandoffImportBinding {
  readonly inbox_path: string
  readonly candidates: ReadonlyArray<HandoffIntakeCandidate>
  readonly rejected_count: number
  readonly message: string | null
}
export interface HandoffIntakeSelectRequest extends HandoffImportBinding {
  readonly selection_handle: string | null
  readonly candidate_handle: string | null
}
export interface HandoffIntakeSelectEnvelope extends HandoffImportBinding {
  readonly ticket_id: string
  readonly source_name: string
  readonly source_path: string
  readonly source_size: number
  readonly container: string
  readonly archive_name: string
  readonly incoming_path: string
  readonly output_path: string
  readonly replace_existing: boolean
  readonly action: 'copy' | 'rename' | 'none'
  readonly expires_in_seconds: 300
}
export interface HandoffIntakeCheckRequest {
  readonly contract_version: '0.3.0'
  readonly ticket_id: string
  readonly overwrite: boolean
}
export interface HandoffIntakeJobRequest {
  readonly contract_version: '0.3.0'
  readonly job_id: string
}
export interface HandoffIntakeJobEnvelope extends HandoffIntakeJobRequest {
  readonly phase: 'checking' | 'copying' | 'ready' | 'failed'
  readonly bytes_done: number
  readonly total_bytes: number
  readonly ready_id: string | null
  readonly message: string | null
}
export interface HandoffIntakePublishRequest {
  readonly contract_version: '0.3.0'
  readonly ready_id: string
}
export interface HandoffIntakePublishEnvelope {
  readonly contract_version: '0.3.0'
  readonly output_path: string
}
export function assertIntakeBinding(binding: HandoffImportBinding, value: HandoffImportBinding): void {
  const keys = ['contract_version', 'project_session_id', 'run_id', 'node_run_id', 'handoff_id', 'port_id', 'ordinal'] as const
  if (keys.some((key) => binding[key] !== value[key])) throw new Error('交回响应不属于当前工程与任务。')
}
export function isIntakeHandoff(nodeRun: NodeRunWire, detail: RunDetailEnvelope | null): boolean {
  const handoff = nodeRun.external_handoff
  return !!handoff && nodeRun.state === 'waiting_external' && handoff.output_targets.length === 1 &&
    handoff.output_targets[0]?.ordinal === null && detail?.run.run_id === nodeRun.run_id &&
    detail.handoff_contracts.some((item) => item.node_run_id === nodeRun.node_run_id &&
      item.handoff_id === handoff.handoff_id && item.intake_supported === true)
}
