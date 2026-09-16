/** 单文件交回测试只使用合成路径与身份。 */
import { handoffDetailEnvelope } from './test-fixtures'
import type { HandoffImportBinding } from './host-bridge'
import type { HandoffIntakeObserveEnvelope, HandoffIntakeSelectEnvelope, HandoffIntakeJobEnvelope } from './handoff-intake-contracts'
export const intakeDetail = handoffDetailEnvelope()
export const intakeNodeRun = intakeDetail.run.node_runs.find((item) => item.state === 'waiting_external')!
export const intakeBinding: HandoffImportBinding = { contract_version: '0.3.0', project_session_id: '00000000-0000-4000-8000-000000000001',
  run_id: intakeNodeRun.run_id, node_run_id: intakeNodeRun.node_run_id, handoff_id: intakeNodeRun.external_handoff!.handoff_id,
  port_id: intakeNodeRun.external_handoff!.output_targets[0]!.port_id, ordinal: null }
export const intakeCandidate = { candidate_handle: 'candidate_1234567890_1234567890', name: 'result.mov', size: 1024, container: 'mov' }
export const intakeObservation: HandoffIntakeObserveEnvelope = { ...intakeBinding, inbox_path: 'D:\\synthetic\\source-repair\\round-001\\incoming',
  candidates: [intakeCandidate], rejected_count: 0, message: null }
export const intakeSelection: HandoffIntakeSelectEnvelope = { ...intakeBinding, ticket_id: 'ticket_1234567890_1234567890',
  source_name: intakeCandidate.name, source_path: `${intakeObservation.inbox_path}\\${intakeCandidate.name}`, source_size: intakeCandidate.size,
  container: 'mov', archive_name: 'synthetic.repaired.RM.mov', incoming_path: `${intakeObservation.inbox_path}\\synthetic.repaired.RM.mov`,
  output_path: 'D:\\synthetic\\source-repair\\round-001\\outputs\\synthetic.repaired.RM.mov', replace_existing: false, action: 'rename', expires_in_seconds: 300 }
export const intakeJob: HandoffIntakeJobEnvelope = { contract_version: '0.3.0', job_id: 'job_1234567890_1234567890', phase: 'ready',
  bytes_done: 1024, total_bytes: 1024, ready_id: 'ready_1234567890_1234567890', message: null }
