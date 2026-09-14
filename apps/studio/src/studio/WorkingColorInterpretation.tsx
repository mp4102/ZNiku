/** 操作者选择仅是待提交的 Graph 参数；可用性、准入及依据始终来自 Python。 */
import type { ColorInterpretationPolicy, ColorPreparedSourceViewEnvelope } from './prepared-color-contracts'

export function WorkingColorInterpretation({ view, value, disabled, onChange, ordinary = false }: {
  readonly view: Pick<ColorPreparedSourceViewEnvelope, 'color_interpretation_available' | 'color_interpretation_required' | 'color_interpretation_reason' | 'working_signal_basis' | 'state'>
  readonly value: ColorInterpretationPolicy
  readonly disabled: boolean
  readonly onChange: (policy: ColorInterpretationPolicy) => void
  readonly ordinary?: boolean
}) {
  return <section aria-label="本工程工作色彩解释">
    <h4>本工程工作色彩解释</h4>
    <p>这是后续处理采用的解释，不是探测出的原片事实，也不是色彩转换。</p>
    <fieldset disabled={disabled}><legend>选择工作色彩依据</legend>
      <label className="source-preparation-option"><input type="radio" name="working-color-interpretation" checked={value === 'declared_only'} onChange={() => onChange('declared_only')} /><span><strong>要求素材已明确声明（默认）</strong><small>声明不足时不进入后续处理，不自动采用 BT.709。</small></span></label>
      <label className="source-preparation-option"><input type="radio" name="working-color-interpretation" disabled={!view.color_interpretation_available} checked={value === 'operator_confirmed_bt709_limited_left'} onChange={() => onChange('operator_confirmed_bt709_limited_left')} /><span><strong>我确认本工程采用 SDR BT.709 / 有限范围 / left</strong><small>仅补足服务已确认未指定的解释；不能覆盖冲突、解析失败或不支持的 HDR。</small></span></label>
    </fieldset>
    <p>{view.color_interpretation_reason}</p>
    <p>{ordinary ? '这不会证明原片本来就是 BT.709，也不会改写原件。缺少声明本身不要求制作副本；后续派生产物按你确认的解释处理并标注，不能把另一种已知色彩空间正确转换成 BT.709。' : '这不会证明原片本来就是 BT.709，不会改写原件或保内容修复副本。后续派生的分叶和成品将按此解释处理并标注；它不能把另一种已知色彩空间正确转换成 BT.709。'}</p>
    {view.color_interpretation_required && value === 'declared_only' && <p role="status">工作色彩尚未明确。只有你确认该解释适用时才选择继续；不能确认时请保留阻断，使用外部专业确认或另建新工作源。</p>}
    {view.working_signal_basis && <p>服务记录的工作解释依据：{view.working_signal_basis}</p>}
    {view.state === 'ready' && <p>{ordinary ? '当前解释已保存在工程中。更改解释需要重新检查，旧结果不会自动获得新解释。' : '当前解释已绑定到准入记录。需要更改时，请返回节点图修改准入节点参数并启动新的运行；旧结果不会自动获得新解释。'}</p>}
  </section>
}
