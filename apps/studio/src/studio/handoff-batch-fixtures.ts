/** 只供自动化测试使用的合成交接；不引用真实媒体或工程。 */
import type { HandoffBatchBinding, HandoffBatchObserveEnvelope, HandoffBatchPreviewEnvelope } from './handoff-batch-contracts'
import { handoffDetailEnvelope } from './test-fixtures'
import type { ExternalHandoffReadiness } from './contracts'

export const batchBinding: HandoffBatchBinding = { contract_version: '0.3.0',
  project_session_id: '10000000-0000-4000-8000-000000000001', run_id: '10000000-0000-4000-8000-000000000002',
  node_run_id: '10000000-0000-4000-8000-000000000003', handoff_id: '10000000-0000-4000-8000-000000000004' }
export const batchObservation: HandoffBatchObserveEnvelope = { ...batchBinding, inbox_path: 'D:/synthetic/incoming', complete: false,
  rows: [1, 2].map((number) => ({ port_id: `leaf-${number}`, ordinal: null, target_name: `A.leaf-${number}.mov`, display_label: `A 章 · 第 ${number} 段`,
    target_path: `D:/synthetic/A.leaf-${number}.mov`, incoming_path: `D:/synthetic/incoming/leaf-${number}/A.mov`, collected: false, target_exists: false, size: null })) }
export const batchPreview: HandoffBatchPreviewEnvelope = { ...batchObservation, batch_id: 'batch_1234567890_1234567890', expires_in_seconds: 300,
  candidates: [1, 2].map((number) => ({ candidate_handle: `candidate_1234567890_123456789${number}`, name: `A.leaf-${number}.mov`, size: 1000, action: 'copy', path: `D:/synthetic/external/A.leaf-${number}.mov`, unchanged_port_ids: [] })),
  matches: [1, 2].map((number) => ({ port_id: `leaf-${number}`, candidate_handle: `candidate_1234567890_123456789${number}`, state: 'matched', basis: 'canonical_name', reason: '规范名唯一对应' })) }
const original = handoffDetailEnvelope().run.node_runs.find((node) => node.external_handoff)!
export const batchNodeRun = { ...original, run_id: batchBinding.run_id, node_run_id: batchBinding.node_run_id,
  external_handoff: { ...original.external_handoff!, handoff_id: batchBinding.handoff_id, node_run_id: batchBinding.node_run_id,
    output_targets: batchObservation.rows.map((row) => ({ port_id: row.port_id, ordinal: row.ordinal, path: row.target_path })) } }
export const batchReadiness: ExternalHandoffReadiness = { contract_version: '0.3.0', run_id: batchBinding.run_id,
  node_run_id: batchBinding.node_run_id, handoff_id: batchBinding.handoff_id, checked_at: '2026-09-16T00:00:00Z',
  probe_requested: true, ready_for_submit: true, targets: batchObservation.rows.map((row) => ({ port_id: row.port_id, ordinal: null,
    path: row.target_path, state: 'probe_passed', size: 1000, mtime_ns: 1000, message: null })) }
