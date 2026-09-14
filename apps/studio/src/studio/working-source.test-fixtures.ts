/** 普通工作源的纯合成 wire 值；不生成媒体或伪装实际观察。 */
import { colorView, colorPreview } from './prepared-color.test-fixtures'
import { WORK_SOURCE_VERSION, parseWorkFullEnvelope, type WorkViewEnvelope } from './working-source-contracts'
export function workView(patch: Partial<WorkViewEnvelope> = {}): WorkViewEnvelope {
  return { ...colorView(), contract_version: WORK_SOURCE_VERSION, profile_id: 'zniku.prepared-work-overlap', profile_version: WORK_SOURCE_VERSION,
    decision: 'direct', inspection_scope: 'frames_eof', impacts: [{ id: 'frames', routes: ['direct'], title: '画面帧', description: '使用原件，不制作副本。' }],
    required_confirmations: [{ id: 'color_interpretation', routes: ['direct', 'builtin'], label: '确认工作色彩解释', description: '只解释未声明项，不证明原件本来是 BT.709。' }],
    findings: [{ code: 'E_WORK_COLOR_UNSPECIFIED', reason: '处理所需声明不完整', impact: '尚未确定工作解释', recommendation: '明确本工程解释后继续' }],
    available_actions: [{ route: 'direct', label: '使用原件', enabled: true, reason: '无需制作工作副本', strategy_id: null, estimated_additional_bytes: 0 }],
    current_settings: { route: 'diagnose', target_frame_rate: null, external_format: 'mkv', interpretation_policy: 'declared_only', confirmations: [], audio_source: 'original' }, retry_target: null, ...patch }
}
export function workPreview() {
  return parseWorkFullEnvelope({ ...colorPreview(), contract_version: WORK_SOURCE_VERSION, profile_id: 'zniku.prepared-work-overlap', profile_version: WORK_SOURCE_VERSION })
}
