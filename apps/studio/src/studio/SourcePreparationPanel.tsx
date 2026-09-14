/** 素材准备结果与显式路线选择；仅展示后端发现、动作资格和真实任务绑定。 */
import { useEffect, useState } from 'react'
import { SourcePreparationProgress } from './SourcePreparationProgress'
import type { PreparedSourceChoice, PreparedSourceRoute, PreparedSourceViewEnvelope } from './prepared-source-contracts'
import type { ColorInterpretationPolicy, ColorPreparedSourceViewEnvelope } from './prepared-color-contracts'
import { WorkingColorInterpretation } from './WorkingColorInterpretation'
import { WORK_SOURCE_VERSION, type WorkViewEnvelope, type WorkChoice, type WorkConfirmationId } from './working-source-contracts'
import './source-preparation.css'

const stateLabels = {
  checking: '正在检查素材', needs_choice: '素材需要准备', preparing: '正在准备工作参考',
  waiting_external: '等待外部保内容修复', ready: '工作源已通过准入，可以继续设置', failed: '素材准备尚未完成',
} as const
const unitLabels: Readonly<Record<string, string>> = { frames: '帧', packets: '包', bytes: '字节', tracks: '音轨', samples: '样本', microseconds: '微秒', items: '项' }
function byteEstimate(bytes: number | null): string {
  return bytes === null ? '暂不能可靠估算；请预留候选和验证所需空间' : `约 ${bytes.toLocaleString('zh-CN')} 字节；不是最终文件大小承诺`
}
export function SourcePreparationPanel({ view, recordedExternalFormat = null, disabled, cancelDisabled = disabled, onChoose, onOpenExternal, onNewSource, onRetry, onCancel }: {
  readonly view: PreparedSourceViewEnvelope | ColorPreparedSourceViewEnvelope | WorkViewEnvelope
  readonly disabled: boolean
  readonly recordedExternalFormat?: 'mkv' | 'mp4' | 'mov' | null
  readonly cancelDisabled?: boolean
  readonly onChoose: (choice: PreparedSourceChoice | WorkChoice) => void
  readonly onOpenExternal: (runId: string) => void
  readonly onNewSource: () => void
  readonly onRetry: () => void
  readonly onCancel?: () => void
}) {
  const preferred = view.available_actions.find((action) => action.enabled && action.route === view.route)
    ?? view.available_actions.find((action) => action.enabled && action.route === 'builtin')
    ?? view.available_actions.find((action) => action.enabled)
  const workView = view.contract_version === WORK_SOURCE_VERSION ? view : null
  const recordedRate = workView?.current_settings.target_frame_rate ?? view.frame_rate ?? ''
  const currentFormat = workView?.current_settings.external_format ?? recordedExternalFormat
  const [selectedRoute, setSelectedRoute] = useState<PreparedSourceRoute | null>(preferred?.route ?? null)
  const [rate, setRate] = useState(recordedRate)
  const [format, setFormat] = useState<'mp4' | 'mov' | 'mkv'>(currentFormat ?? 'mkv')
  const [page, setPage] = useState(0)
  const colorView = view.contract_version === '0.3.4-color.1' ? view : workView
  const recordedPolicy = workView?.current_settings.interpretation_policy ?? colorView?.interpretation_policy ?? 'declared_only'
  const [interpretationPolicy, setInterpretationPolicy] = useState<ColorInterpretationPolicy>(recordedPolicy)
  const [confirmations, setConfirmations] = useState<ReadonlyArray<WorkConfirmationId>>(workView?.current_settings.confirmations ?? [])
  const confirmationBinding = JSON.stringify([view.run_id, workView?.current_settings, workView?.required_confirmations])
  useEffect(() => { setConfirmations(workView?.current_settings.confirmations ?? []) }, [confirmationBinding]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setInterpretationPolicy(recordedPolicy) }, [view.run_id, recordedPolicy])
  useEffect(() => { setFormat(currentFormat ?? 'mkv') }, [view.run_id, currentFormat])
  useEffect(() => { setSelectedRoute(preferred?.route ?? null); setRate(recordedRate); setPage(0) }, [view.run_id, recordedRate, preferred?.route])
  const confirm = (id: WorkConfirmationId, checked: boolean) => setConfirmations((items) => checked ? [...new Set([...items, id])] : items.filter((item) => item !== id))
  const requirements = workView?.required_confirmations.filter((item) => selectedRoute !== null && item.routes.includes(selectedRoute)) ?? []
  const selectedConfirmations = requirements.filter((item) => confirmations.includes(item.id)).map((item) => item.id)
  const confirmed = requirements.every((item) => confirmations.includes(item.id))
  const selected = view.available_actions.find((action) => action.route === selectedRoute)
  const findingsPages = Math.max(1, Math.ceil(view.findings.length / 20))
  const currentPage = Math.min(page, findingsPages - 1)
  const policyAvailable = interpretationPolicy === 'declared_only' || colorView?.color_interpretation_available === true
  const interpretationRequired = workView ? requirements.some((item) => item.id === 'color_interpretation') : selectedRoute === 'direct' && colorView?.color_interpretation_required
  const interpretationReady = policyAvailable && (!interpretationRequired || interpretationPolicy === 'operator_confirmed_bt709_limited_left')
  const needsRate = selectedRoute !== 'direct' || view.frame_rate === null
  const retryAdmission = colorView !== null && view.admission_status === 'failed' && selectedRoute === view.route
  const missingRecordedFormat = retryAdmission && selectedRoute === 'external' && currentFormat === null
  const canChoose = !disabled && !missingRecordedFormat && selected?.enabled && interpretationReady && confirmed && (view.state === 'needs_choice' || view.state === 'failed')
  const heading = !workView ? stateLabels[view.state] : view.state === 'ready' ? '工作素材已准备好，可以继续' : view.state === 'waiting_external' ? '等待外部工作源' : view.state === 'needs_choice'
    ? workView.decision === 'direct' ? '可直接处理' : workView.decision === 'preparation_required' ? '需要准备工作副本' : workView.decision === 'unsupported' ? '当前处理器不支持' : '等待素材检查结果'
    : view.state === 'preparing' ? '正在准备工作素材' : stateLabels[view.state]
  return <section className="source-preparation" aria-label="素材检查与准备">
    <div className={`creator-profile-result is-${view.state}`} role="status"><strong>{heading}</strong><span>{view.source_name}</span></div>
    <p>原件只读保留。兼容性问题不等于文件损坏；正常素材不必复制或转码。报告、成功工作副本和外部产物长期保存在工程工作数据中。</p>
    {(view.state === 'checking' || view.state === 'preparing') && <>
      <SourcePreparationProgress mode={workView ? 'working' : 'strict'} measurement={view.stage_progress ? {
        stage: view.stage_progress.stage, current: view.stage_progress.current, total: view.stage_progress.total,
        unit: view.stage_progress.unit ? unitLabels[view.stage_progress.unit] ?? view.stage_progress.unit : null,
        elapsed_seconds: view.stage_progress.elapsed_seconds, speed: view.stage_progress.rate_per_second === null ? null
          : `${view.stage_progress.rate_per_second.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ${view.stage_progress.unit ? unitLabels[view.stage_progress.unit] ?? view.stage_progress.unit : '项'}/秒`,
      } : { stage: view.stage, current: view.progress?.current ?? null,
        total: view.progress?.total ?? null, unit: view.progress?.unit ? unitLabels[view.progress.unit] ?? view.progress.unit : null, elapsed_seconds: null, speed: null }} />
      {onCancel && <button type="button" className="button button--ghost" disabled={cancelDisabled} onClick={onCancel}>停止当前检查或验证</button>}
    </>}
    <dl className="source-preparation-summary">
      <div><dt>素材检查</dt><dd>{workView ? workView.inspection_scope === 'frames_eof' ? '当前处理所需的全片观察已完成' : workView.inspection_scope === 'header_only' ? '仅完成头信息读取，未通过全片检查' : '尚未完成' : view.diagnosis_status === 'completed' ? '已完成完整检查' : view.diagnosis_status === 'failed' ? '未完成' : '尚未完成'}</dd></div>
      <div><dt>{workView ? '能否继续' : '工作源准入'}</dt><dd>{view.admission_status === 'completed' ? '已通过' : view.admission_status === 'failed' ? '未通过' : '尚未通过'}</dd></div>
      {view.source_frame_count !== null && <div><dt>服务确认帧数</dt><dd>{view.source_frame_count.toLocaleString('zh-CN')} 帧</dd></div>}
      {view.frame_rate && <div><dt>服务确认精确帧率</dt><dd>{view.frame_rate} fps</dd></div>}
    </dl>
    {view.findings.length > 0 && <section aria-label="素材问题与建议"><h4>哪里不符合要求</h4>
      {view.findings.slice(currentPage * 20, (currentPage + 1) * 20).map((finding, index) => <article className="source-preparation-finding" key={`${finding.code}-${index}`}>
        <h5>{finding.reason}</h5><dl><div><dt>会影响什么</dt><dd>{finding.impact}</dd></div><div><dt>推荐怎么做</dt><dd>{finding.recommendation}</dd></div></dl>
        <details><summary>高级详情 · 问题代码</summary><code>{finding.code}</code></details>
      </article>)}
      {findingsPages > 1 && <nav aria-label="素材问题分页"><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button><span>第 {currentPage + 1} / {findingsPages} 页</span><button type="button" disabled={currentPage + 1 === findingsPages} onClick={() => setPage(currentPage + 1)}>下一页</button></nav>}
    </section>}
    {view.error && <section className="template-error-stack" aria-label="准备未完成的原因" role="alert"><strong>{view.error.message}</strong><p>原件、已复制文件和已完成结果均保留。不会自动换策略、覆盖原件或跳过验证。</p><details><summary>高级详情 · 原始错误</summary><code>{view.error.code}</code></details></section>}
    {colorView && (!workView || (selectedRoute === 'external' ? requirements.some((item) => item.id === 'color_interpretation') : workView.color_interpretation_required || interpretationPolicy !== 'declared_only')) && <>
      {workView && view.route === 'external' && view.admission_status === 'failed' && <p>以下解释针对本次提交的新参考文件，不是重复确认原片；请按该新文件实际适用的工作解释选择。</p>}
      <WorkingColorInterpretation view={colorView} ordinary={workView !== null} value={interpretationPolicy} disabled={disabled || !['needs_choice', 'failed'].includes(view.state)} onChange={(policy) => { setInterpretationPolicy(policy); confirm('color_interpretation', policy === 'operator_confirmed_bt709_limited_left') }} />
    </>}
    {(view.state === 'needs_choice' || view.state === 'failed') && <section aria-label="选择素材准备方式">
      <h4>下一步怎么做</h4>
      <fieldset><legend>准备方式</legend>{view.available_actions.map((action) => <label className="source-preparation-option" key={action.route}>
        <input type="radio" name="source-preparation-route" value={action.route} checked={selectedRoute === action.route} disabled={disabled || !action.enabled || retryAdmission} onChange={() => {
          setSelectedRoute(action.route)
          const newReference = workView !== null && action.route === 'external' && !workView.required_confirmations.some((item) => item.id === 'color_interpretation' && item.routes.includes('external'))
          // 原件解释不能预授权尚未见到的新参考；只有后端报告实际候选后才能确认其色彩。
          if (newReference) setInterpretationPolicy('declared_only')
          setConfirmations((items) => items.filter((item) => item === 'target_frame_rate' || (!newReference && item === 'color_interpretation')))
        }} />
        <span><strong>{action.label}{!action.enabled ? '（尚不可用）' : ''}</strong><small>{action.reason}</small>{!workView && action.strategy_id && <small>策略：{action.strategy_id}</small>}</span>
      </label>)}</fieldset>
      {needsRate && selected && <>
        <label>目标精确帧率<select aria-label="修复目标精确帧率" disabled={disabled || retryAdmission} value={rate} onChange={(event) => { setRate(event.target.value); setConfirmations((items) => [...items.filter((item) => item !== 'retime' && item !== 'target_frame_rate'), ...(event.target.value ? ['target_frame_rate' as const] : [])]) }}><option value="">尚无已确认目标</option>{Array.from(new Set([...(view.frame_rate ? [view.frame_rate] : []), ...view.frame_rate_choices])).map((value) => <option key={value} value={value}>{value} fps</option>)}</select></label>
        {!rate && <p>服务尚未提供可确认的目标帧率，不能猜测或强制修复；可返回选择素材或另建新工作源。</p>}
      </>}
      {selectedRoute === 'direct' && selected && <p>{workView ? '不需要制作整片副本。完成必要的工作解释后，直接使用原件建立本工程的处理参考。' : '直接路线不生成工作副本；按确认帧率重新检查和准入，不会强制放行。'}</p>}
      {!workView && selectedRoute !== 'direct' && selected && <>
        <p>额外空间：{byteEstimate(selected.estimated_additional_bytes)}</p>
        <p>将生成新的持久工作副本，原件不修改；完整视频内容和音频关系验证通过后，还需独立工作源准入。</p>
        {colorView?.color_interpretation_required && interpretationPolicy === 'declared_only' && <p>可以先保内容修复，不必先采用色彩解释。但修复成功不代表可以进入增强；工作解释仍不足时，会停在独立准入步骤。</p>}
      </>}
      {workView && selected && <section aria-label="本次操作的影响与确认"><h4>本次操作会改变什么</h4>
        <dl>{workView.impacts.filter((item) => selectedRoute !== null && item.routes.includes(selectedRoute)).map((item) => <div key={item.id}><dt>{item.title}</dt><dd>{item.description}</dd></div>)}</dl>
        {selectedRoute !== 'direct' && <p>额外空间：{byteEstimate(selected.estimated_additional_bytes)}</p>}
        {requirements.map((item) => item.id === 'color_interpretation' || item.id === 'target_frame_rate' ? <p key={item.id}>{item.label}：{item.description}</p> : <label className="source-preparation-option" key={item.id}><input type="checkbox" disabled={disabled} checked={confirmations.includes(item.id)} onChange={(event) => confirm(item.id, event.target.checked)} /><span><strong>{item.label}</strong><small>{item.description}</small></span></label>)}
        {selectedRoute === 'external' && <p>外部结果将作为新的工作参考，重新检查和规划；不继承旧分章、增强或外部完成结果。文件出现不会自动提交，音频来源按本次说明绑定。</p>}
      </section>}
      {selectedRoute === 'external' && <label>外部结果格式<select aria-label={workView ? '外部工作源格式' : '外部保内容修复格式'} disabled={disabled || retryAdmission} value={format} onChange={(event) => { setFormat(event.target.value as typeof format); confirm('external_reference', false) }}><option value="mkv">MKV</option><option value="mp4">MP4</option><option value="mov">MOV</option></select></label>}
      {retryAdmission && <p>{workView ? '结束失败批次并建立新的检查运行；历史记录、原件和已完成产物保留。保持准备路线、帧率和格式，只更新工作解释，不要求再次外部处理。' : '本次只更新工作解释并重新检查准入；保持已完成准备的路线、帧率和格式，不要求再次外部处理。'}</p>}
      {missingRecordedFormat && <p role="alert">当前工程没有可唯一识别的外部格式记录，不能猜成 MKV；请返回节点图检查。</p>}
      <button type="button" className="button button--primary" disabled={!canChoose || (needsRate && !rate)} onClick={() => selectedRoute && onChoose({ run_id: view.run_id, route: selectedRoute, target_frame_rate: rate || null, external_format: format, ...(colorView ? { interpretation_policy: interpretationPolicy } : {}), ...(workView ? { confirmations: selectedConfirmations } : {}) })}>{retryAdmission ? workView ? '结束失败批次并重新检查' : '更新工作解释并重新检查准入' : selectedRoute === 'builtin' ? '开始生成工作副本' : selectedRoute === 'external' ? workView ? '建立外部工作源任务' : '建立外部保内容修复任务' : workView ? '确认并使用原件' : '使用原件并检查准入'}</button>
      {view.state === 'failed' && (!workView || !retryAdmission) && <button type="button" className="button button--ghost" disabled={disabled} onClick={onRetry}>{workView?.retry_target ? '结束失败批次并重新检查' : workView ? '查看失败原因' : '从头重新检查当前步骤'}</button>}
      {workView?.retry_target && !retryAdmission && <p>只重新执行失败步骤；已完成且仍有效的上游产物可复用。若创建替代运行，会结束没有活动任务的失败批次，历史记录与产物保留。</p>}
      <button type="button" className="button button--ghost" disabled={disabled} onClick={onNewSource}>作为新工作源新建工程</button>
      <p>“新工作源”会重新选择素材并新建工程，不继承当前分章、旧增强或外部任务；不会绕过素材检查。</p>
    </section>}
    {view.state === 'waiting_external' && <section aria-label="当前外部素材修复任务"><h4>在外部工具处理后返回</h4><p>助手会显示原件、目标要求和本次任务的专属收件目录。直接复制文件或选择文件导入均不会自动提交；仍需检查并显式提交。</p><button type="button" className="button button--primary" disabled={disabled || !view.handoff} onClick={() => view.handoff && onOpenExternal(view.handoff.run_id)}>{workView ? '打开当前外部工作源助手' : '打开当前外部修复助手'}</button></section>}
    {view.state === 'ready' && <p>{workView ? '现在可以设置分章、分叶、增强、补帧和成片。规划使用检查完成的工作参考；外部新参考的帧数和时钟重新计算，不沿用旧计划。' : '请继续设置分章、分叶、增强、补帧和成片。规划使用当前已准入工作参考，不沿用未修复原件的错误时钟。'}</p>}
    <details><summary>高级详情 · 来源与保存位置</summary><dl><div><dt>只读原件</dt><dd>{view.original_path}</dd></div><div><dt>当前工作参考</dt><dd>{view.reference_path ?? '尚未建立'}</dd></div></dl><p>工程保存与工作副本不等于已归档原件；请保留原件路径或另做显式归档。</p></details>
  </section>
}
