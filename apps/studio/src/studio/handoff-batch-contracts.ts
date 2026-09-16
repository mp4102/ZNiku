/** 章级收件只消费 Python 返回的目标与匹配，不按文件名推导章、叶或执行顺序。 */
import type { ExternalHandoffReadiness, NodeRunWire, RunDetailEnvelope } from './contracts'

export interface HandoffBatchBinding {
  readonly contract_version: '0.3.0'
  readonly project_session_id: string
  readonly run_id: string
  readonly node_run_id: string
  readonly handoff_id: string
}
export interface HandoffBatchRow {
  readonly port_id: string
  readonly ordinal: null
  readonly target_name: string
  readonly target_path: string
  readonly incoming_path: string
  readonly collected: boolean
  readonly target_exists: boolean
  readonly size: number | null
}
export interface HandoffBatchObserveEnvelope extends HandoffBatchBinding {
  readonly inbox_path: string
  readonly rows: ReadonlyArray<HandoffBatchRow>
  readonly complete: boolean
}
export interface HandoffBatchPreviewRequest extends HandoffBatchBinding {
  readonly selection_handles: ReadonlyArray<string>
}
export interface HandoffBatchPreviewEnvelope extends HandoffBatchObserveEnvelope {
  readonly batch_id: string
  readonly candidates: ReadonlyArray<{ readonly candidate_handle: string; readonly name: string; readonly size: number; readonly action: 'copy' | 'move' }>
  readonly matches: ReadonlyArray<{ readonly port_id: string; readonly candidate_handle: string | null; readonly state: 'matched' | 'missing' | 'ambiguous' }>
  readonly expires_in_seconds: 300
}
export interface HandoffBatchConfirmRequest {
  readonly contract_version: '0.3.0'
  readonly batch_id: string
  readonly items: ReadonlyArray<{ readonly port_id: string; readonly candidate_handle: string; readonly overwrite: boolean }>
}
export interface HandoffBatchConfirmEnvelope extends HandoffBatchObserveEnvelope {
  readonly batch_id: string
  readonly results: ReadonlyArray<{ readonly port_id: string; readonly status: 'collected' | 'failed'; readonly message: string | null }>
}
export interface HandoffBatchCheckRequest extends HandoffBatchBinding {
  readonly overwrite_ports: ReadonlyArray<string>
}
export interface HandoffBatchCheckEnvelope extends HandoffBatchObserveEnvelope {
  readonly published: boolean
  readonly validation_error: { readonly code: string; readonly message: string } | null
  readonly readiness: ExternalHandoffReadiness | null
}

export function assertBatchObservation(binding: HandoffBatchBinding, value: HandoffBatchObserveEnvelope): void {
  const keys = ['contract_version', 'project_session_id', 'run_id', 'node_run_id', 'handoff_id'] as const
  if (keys.some((key) => binding[key] !== value[key]) || value.rows.length === 0 ||
      new Set(value.rows.map((row) => row.port_id)).size !== value.rows.length ||
      value.complete !== value.rows.every((row) => row.collected || row.target_exists)) throw new Error('批量收件响应与当前任务不一致。')
}

export function assertBatchTargets(value: HandoffBatchObserveEnvelope, nodeRun: NodeRunWire): void {
  const targets = nodeRun.external_handoff?.output_targets ?? []
  const byPort = new Map(targets.map((target) => [target.port_id, target]))
  if (targets.length !== value.rows.length || value.rows.some((row) => {
    const target = byPort.get(row.port_id)
    return !target || target.ordinal !== row.ordinal || target.path !== row.target_path
  })) {
    throw new Error('批量收件目标与当前节点不一致。')
  }
}

/** 此判定仅选择界面，不赋予收件权限；服务独立校验所有目标。旧单输出节点不迁移。 */
export function isBatchHandoff(nodeRun: NodeRunWire, detail: RunDetailEnvelope | null): boolean {
  const targets = nodeRun.external_handoff?.output_targets ?? []
  if (targets.length > 1 && targets.every((target) => target.ordinal === null)) return true
  const node = detail?.run.graph_snapshot.nodes.find((item) => item.node_id === nodeRun.node_id)
  return !!node && node.definition_version === '0.3.5' &&
    /^zniku\.source-admitted\.enhancement-batch\.[1-9][0-9]*$/.test(node.type_id)
}
