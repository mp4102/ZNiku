/** 工作色彩界面的纯合成 DTO；不产生媒体或伪装真实检查。 */
import { preparedOperation, preparedPreview, preparedView } from './prepared-source.test-fixtures'
import { COLOR_PREPARED_VERSION, type ColorPreparedSourceOperationEnvelope, type ColorPreparedSourceViewEnvelope, parseColorPreparedSourceFullEnvelope } from './prepared-color-contracts'

export function colorView(patch: Partial<ColorPreparedSourceViewEnvelope> = {}): ColorPreparedSourceViewEnvelope {
  return { ...preparedView(), contract_version: COLOR_PREPARED_VERSION, profile_id: 'zniku.prepared-color-overlap', profile_version: COLOR_PREPARED_VERSION,
    interpretation_policy: 'declared_only', color_interpretation_available: true, color_interpretation_required: true,
    color_interpretation_reason: '服务确认部分字段未指定；没有明确冲突。', working_signal_basis: null,
    findings: [{ code: 'E_SYNTHETIC_MISSING_COLOR', reason: '处理所需声明不完整', impact: '尚未确定工作解释', recommendation: '明确本工程解释后再准入' }], ...patch }
}
export function colorOperation(patch: Partial<ColorPreparedSourceOperationEnvelope> = {}): ColorPreparedSourceOperationEnvelope {
  return { ...preparedOperation(), contract_version: COLOR_PREPARED_VERSION, ...patch }
}
export function colorPreview() {
  return parseColorPreparedSourceFullEnvelope({ ...preparedPreview(), contract_version: COLOR_PREPARED_VERSION, profile_id: 'zniku.prepared-color-overlap', profile_version: COLOR_PREPARED_VERSION })
}
