/** 请求、实测进度和正式完成相互独立；展示绑定不随当前选区漂移。 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { batchBinding, batchNodeRun } from './handoff-batch-fixtures'
import { boundActivity, localActivityView, runtimeOperation, type LocalActivity } from './operation-presentation'
import { OperationStatus } from './components/OperationStatus'

afterEach(cleanup)
const activity: LocalActivity = { token: Symbol('synthetic'), projectSessionId: batchBinding.project_session_id,
  nodeRun: batchNodeRun, label: 'A 章增强', phase: 'requesting', message: '正在请求收件，等待服务响应' }
describe('统一真实活动展示', () => {
  it('请求显示等待响应但无假百分比；人工等待不展示忙碌动画', () => {
    const view = render(<OperationStatus value={{ ...activity, fraction: null }} />)
    expect(screen.getByText(/等待服务响应/)).toBeVisible()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    view.rerender(<OperationStatus value={{ label: activity.label, phase: 'needs_user', message: '检查通过，等待你提交', fraction: null }} />)
    expect(screen.getByText(/等待你提交/)).toBeVisible()
    expect(screen.queryByLabelText('正在处理，进度未知')).not.toBeInTheDocument()
  })
  it('仅相同session/run/nodeRun/attempt/handoff的等待节点显示请求', () => {
    expect(boundActivity(activity, batchBinding.project_session_id, batchNodeRun)?.label).toBe(activity.label)
    for (const node of [{ ...batchNodeRun, attempt: batchNodeRun.attempt + 1 }, { ...batchNodeRun, node_run_id: 'other' },
      { ...batchNodeRun, run_id: 'other' }, { ...batchNodeRun, node_id: 'other' },
      { ...batchNodeRun, external_handoff: { ...batchNodeRun.external_handoff, handoff_id: 'other' } },
      { ...batchNodeRun, state: 'completed' as const }]) expect(boundActivity(activity, batchBinding.project_session_id, node)).toBeNull()
    expect(boundActivity(activity, 'other-session', batchNodeRun)).toBeNull()
  })
  it('100%仍显示收尾等待；同百分比阶段更新保留，完成后不再投影进度', () => {
    const node = { ...batchNodeRun, state: 'running' as const, external_handoff: null }
    const progress = { mode: 'determinate' as const, fraction: 1, measurement: null, elapsed: null, stage: '检查 FI 上下文输出' }
    const value = runtimeOperation(node, progress, '上下文')!
    expect(value.phase).toBe('settling')
    expect(value.message).toBe(`${progress.stage}，等待完成确认`)
    render(<OperationStatus value={value} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '1')
    expect(screen.queryByText(/已完成/)).not.toBeInTheDocument()
    expect(runtimeOperation({ ...node, state: 'completed' }, progress, '上下文')).toBeNull()
    expect(runtimeOperation(node, { ...progress, stage: '收尾检查' }, '上下文')?.message).toBe('收尾检查，等待完成确认')
  })
  it('未知自动进度与人工等待均不从历史数字补百分比', () => {
    const running = { ...batchNodeRun, state: 'running' as const }
    expect(runtimeOperation(running, null, '步骤')?.fraction).toBeNull()
    expect(runtimeOperation(batchNodeRun, { mode: 'determinate', fraction: .99, elapsed: null, measurement: null }, '步骤')).toMatchObject({ phase: 'needs_user', fraction: null })
  })
  it('交回字节测量在同绑定节点和右侧共用；检查或人工等待不沿用复制百分比', () => {
    const copying: LocalActivity = { ...activity, phase: 'running', message: '正在收纳已检查文件', fraction: .25 }
    const projected = boundActivity(copying, batchBinding.project_session_id, batchNodeRun)!
    expect(projected.fraction).toBe(.25)
    render(<OperationStatus value={projected} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '0.25')
    expect(boundActivity({ ...copying, phase: 'needs_user' }, batchBinding.project_session_id, batchNodeRun)?.fraction).toBeNull()
    expect(boundActivity(copying, batchBinding.project_session_id, { ...batchNodeRun, attempt: 2 })).toBeNull()
  })
  it('无服务起时的请求与单件job只显示本机请求等待时间；等待用户停止计时', () => {
    for (const phase of ['requesting', 'running', 'settling'] as const) {
      const value: LocalActivity = { ...activity, phase, requestedAt: 10_000 }
      expect(localActivityView(value, 75_500).elapsed).toBe('请求等待时间 1 分钟 5 秒（本机计时）')
      expect(boundActivity(value, batchBinding.project_session_id, batchNodeRun, 75_500)?.elapsed).toBe(localActivityView(value, 75_500).elapsed)
      expect(localActivityView(value, 9_000).elapsed).toContain('0 秒')
    }
    expect(localActivityView({ ...activity, phase: 'needs_user', requestedAt: 10_000 }, 75_500).elapsed).toBeNull()
    expect(localActivityView({ ...activity, phase: 'uncertain', requestedAt: 10_000 }, 75_500).elapsed).toBeNull()
    expect(localActivityView(activity, 75_500).elapsed).toBeNull()
    expect(localActivityView({ ...activity, requestedAt: Number.NaN }, 75_500).elapsed).toBeNull()
  })
  it('自动运行复用已有服务起时用时；不把前端请求时间冒充处理时间', () => {
    const node = { ...batchNodeRun, state: 'running' as const }
    const progress = { mode: 'determinate' as const, fraction: .5, measurement: null, elapsed: 'elapsed 1m 5s' }
    expect(runtimeOperation(node, progress, '自动步骤')?.elapsed).toBe('已用时 1 分钟 5 秒（服务起时）')
    expect(runtimeOperation(node, { ...progress, elapsed: null }, '自动步骤')?.elapsed).toBeNull()
  })
})
