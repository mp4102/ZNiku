/** 普通准备只展示后端结论；选择和确认是显式 Graph 意图，不能推断媒体合法性。 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SourcePreparationPanel } from './SourcePreparationPanel'
import { workView } from './working-source.test-fixtures'
import type { WorkViewEnvelope } from './working-source-contracts'
afterEach(cleanup)
const handlers = () => ({ onChoose: vi.fn(), onOpenExternal: vi.fn(), onNewSource: vi.fn(), onRetry: vi.fn(), onCancel: vi.fn() })
const operator = /我确认本工程采用 SDR BT.709/
const builtin: WorkViewEnvelope['available_actions'] = [{ route: 'builtin', enabled: true, label: '准备工作副本', reason: '当前时间轴需重新定时', strategy_id: 'frame-retime-ffv1/1', estimated_additional_bytes: 1234 }]
const external: WorkViewEnvelope['available_actions'] = [{ route: 'external', enabled: true, label: '使用外部新参考', reason: '当前处理器不能建立工作源', strategy_id: null, estimated_additional_bytes: null }]
const retimeView = (): WorkViewEnvelope => workView({ decision: 'preparation_required', available_actions: builtin, color_interpretation_required: false,
  required_confirmations: [{ id: 'retime', routes: ['builtin'], label: '我确认保留帧数并重新定时', description: '保留解码帧数和顺序，但改变节奏与时长，不恢复未知拍摄时钟。' }],
  impacts: [{ id: 'timing', routes: ['builtin'], title: '时长与节奏', description: '新时间轴为逐帧等距；不是原时间戳保全。' }, { id: 'audio', routes: ['builtin'], title: '音频来源', description: '保留原音轨，不自动裁尾、拉伸或补静音。' }] })
describe('普通素材准备的三结果与显式确认', () => {
  it('缺色彩只确认解释，不诱导生成副本、不暴露准入状态机', () => {
    const actions = handlers()
    render(<SourcePreparationPanel view={workView()} disabled={false} {...actions} />)
    expect(screen.getByText('可直接处理')).toBeVisible()
    const button = screen.getByRole('button', { name: '确认并使用原件' })
    expect(button).toBeDisabled()
    expect(screen.getByRole('region', { name: '本工程工作色彩解释' }).closest('details')).toBeNull()
    fireEvent.click(screen.getByRole('radio', { name: operator }))
    expect(actions.onChoose).not.toHaveBeenCalled()
    fireEvent.click(button)
    expect(actions.onChoose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'direct', interpretation_policy: 'operator_confirmed_bt709_limited_left', confirmations: ['color_interpretation'] }))
    expect(screen.queryByText(/准入|T1|全片码流审计/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '开始生成工作副本' })).not.toBeInTheDocument()
  })
  it('内置保帧重定时必须确认影响，未确认不得开始；改变率后确认失效', () => {
    const actions = handlers()
    render(<SourcePreparationPanel view={retimeView()} disabled={false} {...actions} />)
    expect(screen.getByText('需要准备工作副本')).toBeVisible()
    const button = screen.getByRole('button', { name: '开始生成工作副本' })
    expect(button).toBeDisabled()
    expect(screen.getByRole('region', { name: '本次操作的影响与确认' })).toHaveTextContent('1,234')
    expect(screen.getByText(/保留原音轨，不自动裁尾/)).toBeVisible()
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认保留帧数/ }))
    expect(actions.onChoose).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('修复目标精确帧率'), { target: { value: '30000/1001' } })
    expect(button).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认保留帧数/ }))
    fireEvent.click(button)
    expect(actions.onChoose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'builtin', confirmations: ['retime'] }))
    expect(screen.queryByText(/保内容恒等|策略：|T1/)).not.toBeInTheDocument()
  })
  it('当前不支持不等于坏片；外部新参考说明重新规划和音频，明确确认才建任务', () => {
    const actions = handlers()
    render(<SourcePreparationPanel disabled={false} {...actions} view={workView({ decision: 'unsupported', inspection_scope: 'header_only', color_interpretation_available: false, color_interpretation_required: false, available_actions: external,
      required_confirmations: [{ id: 'external_reference', routes: ['external'], label: '我确认采用外部新参考', description: '允许帧数与时间轴变化，以候选自身音频为来源。' }],
      impacts: [{ id: 'audio', routes: ['external'], title: '音频来源', description: '采用候选自身音频，不沿用不对应的原音轨。' }] })} />)
    expect(screen.getByText('当前处理器不支持')).toBeVisible()
    expect(screen.getByText('仅完成头信息读取，未通过全片检查')).toBeVisible()
    const button = screen.getByRole('button', { name: '建立外部工作源任务' })
    expect(button).toBeDisabled()
    expect(screen.queryByRole('radio', { name: operator })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('外部工作源格式'), { target: { value: 'mov' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认采用外部新参考/ }))
    fireEvent.click(button)
    expect(actions.onChoose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'external', external_format: 'mov', confirmations: ['external_reference'], interpretation_policy: 'declared_only' }))
    expect(actions.onOpenExternal).not.toHaveBeenCalled()
  })
  it('required confirmations只按当前路线显示，不把外部改变许可要求到direct', () => {
    const actions = handlers()
    render(<SourcePreparationPanel view={workView({ color_interpretation_required: false, required_confirmations: [{ id: 'external_reference', routes: ['external'], label: '外部改变', description: '只适用于外部' }] })} disabled={false} {...actions} />)
    expect(screen.queryByText('外部改变')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认并使用原件' }))
    expect(actions.onChoose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ confirmations: [] }))
  })
  it('原件已选择operator后切到未知外部新参考，清除原件解释且不能预授权候选', () => {
    const actions = handlers()
    render(<SourcePreparationPanel disabled={false} {...actions} view={workView({ available_actions: [...workView().available_actions, ...external],
      required_confirmations: [...workView().required_confirmations, { id: 'external_reference', routes: ['external'], label: '确认外部新参考', description: '重新检查其自身色彩和音频' }] })} />)
    fireEvent.click(screen.getByRole('radio', { name: operator }))
    fireEvent.click(screen.getByRole('radio', { name: /使用外部新参考/ }))
    expect(screen.queryByRole('region', { name: '本工程工作色彩解释' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: /确认外部新参考/ }))
    fireEvent.click(screen.getByRole('button', { name: '建立外部工作源任务' }))
    expect(actions.onChoose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'external', interpretation_policy: 'declared_only', confirmations: ['external_reference'] }))
  })
  it.each(['mp4', 'mov'] as const)('外部%s已完成后从backend current_settings恢复，仅更新解释不重等', (format) => {
    const actions = handlers(), view = workView({ route: 'external', state: 'failed', admission_status: 'failed', available_actions: external,
      current_settings: { route: 'external', external_format: format, target_frame_rate: '30/1', interpretation_policy: 'declared_only', confirmations: ['external_reference'], audio_source: 'reference' },
      required_confirmations: [{ id: 'color_interpretation', routes: ['external'], label: '确认候选工作色彩', description: '候选仍缺声明' }] })
    const first = render(<SourcePreparationPanel view={view} disabled={false} {...actions} />)
    first.unmount()
    render(<SourcePreparationPanel view={view} disabled={false} {...actions} />)
    expect(screen.getByLabelText('外部工作源格式')).toHaveValue(format)
    expect(screen.getByLabelText('外部工作源格式')).toBeDisabled()
    expect(screen.getByLabelText('修复目标精确帧率')).toBeDisabled()
    fireEvent.click(screen.getByRole('radio', { name: operator }))
    fireEvent.click(screen.getByRole('button', { name: '结束失败批次并重新检查' }))
    expect(actions.onChoose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ external_format: format, target_frame_rate: '30/1', interpretation_policy: 'operator_confirmed_bt709_limited_left' }))
  })
  it.each(['failed', 'ready'] as const)('外部候选已确认解释且%s时，不能改格式或帧率把解释转给未知候选', (state) => {
    const actions = handlers(), view = workView({ route: 'external', state, admission_status: state === 'ready' ? 'completed' : 'failed', frame_rate: '30/1', frame_rate_choices: ['30/1'],
      reference_path: state === 'ready' ? 'C:/synthetic/candidate-a.mov' : null,
      available_actions: [...workView().available_actions, ...builtin, ...external],
      current_settings: { route: 'external', external_format: 'mov', target_frame_rate: '30/1', interpretation_policy: 'operator_confirmed_bt709_limited_left', confirmations: ['external_reference', 'color_interpretation'], audio_source: 'reference' },
      required_confirmations: [{ id: 'color_interpretation', routes: ['external'], label: '确认候选工作色彩', description: '只针对已经检查的候选 A' }] })
    render(<SourcePreparationPanel view={view} disabled={false} {...actions} />)
    if (state === 'ready') {
      expect(screen.queryByRole('group', { name: '准备方式' })).not.toBeInTheDocument()
      expect(screen.queryByLabelText('外部工作源格式')).not.toBeInTheDocument()
      expect(screen.queryByLabelText('修复目标精确帧率')).not.toBeInTheDocument()
      expect(actions.onChoose).not.toHaveBeenCalled()
      return
    }
    for (const route of within(screen.getByRole('group', { name: '准备方式' })).getAllByRole('radio')) expect(route).toBeDisabled()
    expect(screen.getByLabelText('外部工作源格式')).toBeDisabled()
    expect(screen.getByLabelText('外部工作源格式')).toHaveValue('mov')
    expect(screen.getByLabelText('修复目标精确帧率')).toBeDisabled()
    expect(screen.getByLabelText('修复目标精确帧率')).toHaveValue('30/1')
    fireEvent.click(screen.getByRole('button', { name: '结束失败批次并重新检查' }))
    expect(actions.onChoose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'external', external_format: 'mov', target_frame_rate: '30/1', interpretation_policy: 'operator_confirmed_bt709_limited_left' }))
  })
  it('known conflict不可通过operator覆写；已失效确认不会越过backend禁用', () => {
    const actions = handlers()
    render(<SourcePreparationPanel view={workView({ color_interpretation_available: false, available_actions: workView().available_actions.map((item) => ({ ...item, enabled: false })) })} disabled={false} {...actions} />)
    expect(screen.getByRole('radio', { name: operator })).toBeDisabled()
    expect(screen.getByRole('button', { name: '确认并使用原件' })).toBeDisabled()
    expect(actions.onChoose).not.toHaveBeenCalled()
  })
  it('外部等待不自动提交，运行中只显示测量并可独立停止', () => {
    const actions = handlers(), view = workView({ state: 'waiting_external', route: 'external', handoff: { run_id: workView().run_id, node_run_id: workView().run_id, node_id: 'actual-external' } })
    const mounted = render(<SourcePreparationPanel view={view} disabled={false} {...actions} />)
    fireEvent.click(screen.getByRole('button', { name: '打开当前外部工作源助手' }))
    expect(actions.onOpenExternal).toHaveBeenCalledExactlyOnceWith(view.run_id)
    expect(actions.onChoose).not.toHaveBeenCalled()
    mounted.rerender(<SourcePreparationPanel view={workView({ state: 'preparing' })} disabled cancelDisabled={false} {...actions} />)
    fireEvent.click(screen.getByRole('button', { name: '停止当前检查或验证' }))
    expect(actions.onCancel).toHaveBeenCalledOnce()
    expect(within(screen.getByRole('region', { name: '当前素材准备进度' })).queryByText(/准入|100%|保内容/)).not.toBeInTheDocument()
  })
})
