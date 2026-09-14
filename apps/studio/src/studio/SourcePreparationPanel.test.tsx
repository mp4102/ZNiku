/** 只验证用户动作与服务投影，不构造媒体合法性或自动提交规则。 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SourcePreparationPanel } from './SourcePreparationPanel'
import { preparedView, diagnosisRunId } from './prepared-source.test-fixtures'
import type { PreparedSourceViewEnvelope } from './prepared-source-contracts'

afterEach(cleanup)
function setup(patch: Partial<PreparedSourceViewEnvelope> = {}, disabled = false) {
  const handlers = { onChoose: vi.fn(), onOpenExternal: vi.fn(), onNewSource: vi.fn(), onRetry: vi.fn(), onCancel: vi.fn() }
  render(<SourcePreparationPanel view={preparedView(patch)} disabled={disabled} cancelDisabled={false} {...handlers} />)
  return handlers
}
const actions: PreparedSourceViewEnvelope['available_actions'] = [
  { route: 'builtin', label: '内置工作副本', enabled: false, reason: '该策略仍未启用', strategy_id: 't1', estimated_additional_bytes: null },
  { route: 'external', label: '外部保内容修复', enabled: true, reason: '保留内容并完整验证', strategy_id: null, estimated_additional_bytes: null },
]
describe('素材检查与准备的问题、动作和实测进度', () => {
  it('报告完成仍显示准入尚未通过，不推断修复成功', () => {
    setup()
    expect(screen.getByRole('region', { name: '素材检查与准备' })).toHaveTextContent('尚未通过')
    expect(screen.queryByText('工作源已通过准入，可以继续设置')).not.toBeInTheDocument()
  })
  it('未启用内置策略不可选，不自动开始外部修复；显式动作带当前Run与精确率', () => {
    const handlers = setup({ available_actions: actions })
    expect(screen.getByRole('radio', { name: /内置工作副本/ })).toBeDisabled()
    expect(handlers.onChoose).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('外部保内容修复格式'), { target: { value: 'mov' } })
    fireEvent.click(screen.getByRole('button', { name: '建立外部保内容修复任务' }))
    expect(handlers.onChoose).toHaveBeenCalledExactlyOnceWith({ run_id: diagnosisRunId, route: 'external', target_frame_rate: '30000/1001', external_format: 'mov' })
  })
  it('目标未知不猜29.97或强制通过；只能选择服务提供的率', () => {
    setup({ available_actions: actions, frame_rate: null, frame_rate_choices: ['24/1'] })
    expect(screen.getByRole('button', { name: '建立外部保内容修复任务' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('修复目标精确帧率'), { target: { value: '24/1' } })
    expect(screen.getByRole('button', { name: '建立外部保内容修复任务' })).toBeEnabled()
    expect(screen.queryByRole('option', { name: '29.97 fps' })).not.toBeInTheDocument()
  })
  it('direct要求人工确认率时必须显示选择，不产生工作副本承诺', () => {
    const handlers = setup({ frame_rate: null, frame_rate_choices: ['30/1'] })
    const action = screen.getByRole('button', { name: '使用原件并检查准入' })
    expect(action).toBeDisabled()
    fireEvent.change(screen.getByLabelText('修复目标精确帧率'), { target: { value: '30/1' } })
    fireEvent.click(action)
    expect(handlers.onChoose).toHaveBeenCalledExactlyOnceWith({ run_id: diagnosisRunId, route: 'direct', target_frame_rate: '30/1', external_format: 'mkv' })
    expect(screen.getByText(/直接路线不生成工作副本/)).toBeVisible()
    expect(screen.queryByText(/将生成新的持久工作副本/)).not.toBeInTheDocument()
  })
  it('当前外部助手只使用后端handoff绑定，不自动提交', () => {
    const handlers = setup({ state: 'waiting_external', route: 'external', handoff: { run_id: diagnosisRunId, node_run_id: diagnosisRunId, node_id: 'source-preparation-prepare' } })
    expect(handlers.onOpenExternal).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '打开当前外部修复助手' }))
    expect(handlers.onOpenExternal).toHaveBeenCalledExactlyOnceWith(diagnosisRunId)
    expect(handlers.onChoose).not.toHaveBeenCalled()
    expect(screen.getByText(/直接复制文件或选择文件导入均不会自动提交/)).toBeVisible()
  })
  it('缺少当前handoff时不可用旧任务或猜node打开助手', () => {
    setup({ state: 'waiting_external', route: 'external', handoff: null })
    expect(screen.getByRole('button', { name: '打开当前外部修复助手' })).toBeDisabled()
  })
  it('显示实际样本进度；后台忙不妨碍发送停止信号', () => {
    const handlers = setup({ state: 'preparing', stage: '验证外部副本', stage_progress: { stage: '逐样本比较音频', current: 48000, total: null, unit: 'samples', elapsed_seconds: 2.5, rate_per_second: 19200 } }, true)
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.getByRole('region', { name: '当前素材准备进度' })).toHaveTextContent('48,000 样本')
    expect(screen.getByRole('region', { name: '当前素材准备进度' })).toHaveTextContent('19,200 样本/秒')
    fireEvent.click(screen.getByRole('button', { name: '停止当前检查或验证' }))
    expect(handlers.onCancel).toHaveBeenCalledOnce()
    expect(screen.queryByText(/剩余.*分钟/)).not.toBeInTheDocument()
  })
  it('候选写入达到当前分母仍不显示整项100%或准入已通过', () => {
    setup({ state: 'preparing', stage_progress: { stage: '写入工作副本', current: 120, total: 120, unit: 'frames', elapsed_seconds: 10, rate_per_second: 12 } })
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '120')
    expect(screen.getByText(/工作副本写入后仍须完成内容验证/)).toBeVisible()
    expect(screen.queryByText('已通过', { exact: true })).not.toBeInTheDocument()
  })
  it('问题卡解释原因、影响和推荐，20条有界分页', () => {
    setup({ findings: Array.from({ length: 21 }, (_, index) => ({ code: `E_${index}`, reason: `问题 ${index}`, impact: '暂不能分章', recommendation: '先准备工作参考' })) })
    const region = screen.getByRole('region', { name: '素材问题与建议' })
    expect(within(region).getAllByRole('article')).toHaveLength(20)
    expect(within(region).queryByText('问题 20', { exact: true })).not.toBeInTheDocument()
    fireEvent.click(within(region).getByRole('button', { name: '下一页' }))
    expect(within(region).getAllByRole('article')).toHaveLength(1)
    expect(within(region).getByText('问题 20', { exact: true })).toBeVisible()
  })
  it('失败保留原因，不自动重试；新工作源是独立显式操作', () => {
    const handlers = setup({ state: 'failed', error: { code: 'E_CONTENT', message: '候选不保内容', field_path: [], related_run_ids: [diagnosisRunId] } })
    expect(screen.getByRole('alert')).toHaveTextContent('候选不保内容')
    expect(handlers.onRetry).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '从头重新检查当前步骤' }))
    expect(handlers.onRetry).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: '作为新工作源新建工程' }))
    expect(handlers.onNewSource).toHaveBeenCalledOnce()
  })
  it('ready只展示通过状态，不保留已过期的路线选择与提交入口', () => {
    setup({ state: 'ready', route: 'direct', reference_path: 'D:\\Synthetic\\source.mkv', admission_status: 'completed' })
    expect(screen.getByText('工作源已通过准入，可以继续设置')).toBeVisible()
    expect(screen.queryByRole('button', { name: '开始生成工作副本' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '打开当前外部修复助手' })).not.toBeInTheDocument()
  })
})
