/** 仅供组件与客户端门禁使用的合成服务投影，不是产品媒体报告。 */
import alignedPreview from './__fixtures__/source-aligned-preview.json'
import type { PreparedSourceViewEnvelope, PreparedSourceOperationEnvelope } from './prepared-source-contracts'
import { parsePreparedSourceFullEnvelope } from './prepared-source-contracts'

export const diagnosisRunId = '00000000-0000-4000-8000-000000000041'
export const admissionRunId = '00000000-0000-4000-8000-000000000042'
export const preparedSessionId = '00000000-0000-4000-8000-000000000043'
export function preparedOperation(patch: Partial<PreparedSourceOperationEnvelope> = {}): PreparedSourceOperationEnvelope {
  return { contract_version: '0.3.4', project_session_id: preparedSessionId, run_id: diagnosisRunId, node_run_id: admissionRunId,
    active: true, operation: 'import_external', cancel_requested: false,
    stage_progress: { stage: '完整验证外部视频', current: 10, total: 120, unit: 'frames', elapsed_seconds: 2, rate_per_second: 5 }, ...patch }
}
export function preparedView(patch: Partial<PreparedSourceViewEnvelope> = {}): PreparedSourceViewEnvelope {
  return {
    contract_version: '0.3.4', profile_id: 'zniku.prepared-source-overlap', profile_version: '0.3.4',
    project_session_id: preparedSessionId, storage_revision: 1, run_id: diagnosisRunId,
    route: 'diagnose', state: 'needs_choice', stage: '完整素材检查已完成', current_node_run_id: null,
    progress: null, stage_progress: null, source_name: 'synthetic-source.mkv', original_path: 'D:\\Synthetic\\source.mkv',
    reference_path: null, source_frame_count: 120, frame_rate: '30000/1001', frame_rate_choices: ['30000/1001'],
    audio_track_count: 1, findings: [], available_actions: [{ route: 'direct', label: '使用原件', enabled: true,
      reason: '完整检查已完成；下一步独立检查准入', strategy_id: null, estimated_additional_bytes: 0 }],
    diagnosis_status: 'completed', admission_status: 'not_started', handoff: null, error: null, ...patch,
  }
}
export function preparedPreview() {
  return parsePreparedSourceFullEnvelope({ ...alignedPreview, contract_version: '0.3.4',
    profile_id: 'zniku.prepared-source-overlap', profile_version: '0.3.4', preparation_run_id: admissionRunId,
    project_session_id: preparedSessionId })
}
