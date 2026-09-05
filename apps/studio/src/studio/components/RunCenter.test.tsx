/** 创作者唯一主操作只调用显式 intent，高级精确命令与状态通道仍可查。 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RunCanvasOverlays, RunCenter, type RunCenterProps } from './RunCenter'
import { handoffRun, handoffSummary } from '../test-fixtures'

afterEach(cleanup)
function props(overrides: Partial<RunCenterProps> = {}): RunCenterProps {
  const fresh = { stale: false, lastSuccess: null }
  return { health: { status: fresh, detail: fresh, log: fresh, readiness: fresh }, status: null,
    summaries: [handoffSummary()], viewRunId: handoffSummary().run_id,
    runBlocked: false, runToBlocked: true, rerunBlocked: true, onSelectRun: vi.fn(),
    onRunAll: vi.fn(), onRunTo: vi.fn(), onRerun: vi.fn(), ...overrides }
}
describe('创作者运行中心', () => {
  it('突出唯一上下文主操作，默认关闭精确命令与 ID', () => {
    const action = vi.fn()
    const { container } = render(<RunCenter {...props({ primaryAction: { label: '继续外部处理', disabled: false, onAction: action } })} />)
    expect(container.querySelectorAll('.button--primary')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Run all' })).not.toBeVisible()
    expect(screen.getByRole('combobox', { name: '处理记录' })).toHaveTextContent('完整工作流')
    expect(screen.getByRole('combobox', { name: '处理记录' })).not.toHaveTextContent(handoffSummary().run_id)
    const advancedDetails = container.querySelector('details')!
    advancedDetails.open = true
    fireEvent.click(screen.getByRole('button', { name: '继续外部处理' }))
    expect(action).toHaveBeenCalledTimes(1)
    expect(advancedDetails.open).toBe(false)
  })
  it('高级模式保留精确命令，同时给禁用理由', () => {
    const options = props({ advanced: true, blockedReasons: { runTo: '请先选择一个步骤。' } })
    render(<RunCenter {...options} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all' }))
    expect(options.onRunAll).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Run to here' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Run to here' })).toHaveAccessibleDescription('请先选择一个步骤。')
    expect(screen.getByRole('combobox', { name: '查看 Run' })).toHaveTextContent(handoffSummary().run_id)
  })
  it('禁用主操作有可读说明和恢复入口，不调用执行意图', () => {
    const action = vi.fn(); const recover = vi.fn()
    render(<RunCenter {...props({ primaryAction: { label: '开始处理', disabled: true, reason: '请应用未保存设置。', recoveryLabel: '查看设置', onAction: action, onRecover: recover } })} />)
    const button = screen.getByRole('button', { name: '开始处理' })
    expect(button).toHaveAccessibleDescription('请应用未保存设置。')
    fireEvent.click(button)
    expect(action).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '查看设置' }))
    expect(recover).toHaveBeenCalledTimes(1)
  })
  it('连接失效明确区分上次状态，并提供重新连接', () => {
    const recover = vi.fn(); const base = props()
    render(<RunCenter {...base} health={{ ...base.health, status: { stale: true, lastSuccess: null } }} onRecoverService={recover} />)
    expect(screen.getByRole('status')).toHaveTextContent('上次状态')
    fireEvent.click(screen.getByRole('button', { name: '重新连接' }))
    expect(recover).toHaveBeenCalledTimes(1)
  })
  it('仅详情频道失效也提示陈旧状态，但展示层不擅自更改命令资格', () => {
    const base = props()
    render(<RunCenter {...base} health={{ ...base.health, detail: { stale: true, lastSuccess: 'synthetic-time' } }}
      primaryAction={{ label: '查看当前进度', disabled: false, onAction: vi.fn() }} />)
    expect(screen.getByRole('status')).toHaveTextContent('步骤详情暂未更新，仍保留上次可信状态')
    expect(screen.getByRole('button', { name: '查看当前进度' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '重新连接' })).not.toBeInTheDocument()
  })
  it('外部摘要没有伪造百分比、倒计时或默认绝对路径', () => {
    const node = handoffRun().node_runs.find((item) => item.state === 'waiting_external')!
    const locate = vi.fn()
    render(<RunCanvasOverlays viewedSummary={handoffSummary()} firstWaiting={node}
      firstWaitingInputPaths={['C:\\synthetic\\private-source.mkv']} firstWaitingReadinessLabel="已发现目标文件，尚未完成检查"
      firstWaitingElapsedLabel="已等待 8 分钟" globalActionSummary={null} firstFailed={null} sameRun onLocateNode={locate} onSelectRun={vi.fn()} nodeLabel={() => '画质增强'} />)
    expect(screen.getByRole('status')).toHaveTextContent('已等待 8 分钟')
    expect(screen.getByRole('status')).not.toHaveTextContent(/%|倒计时|C:\\/)
    expect(screen.getByLabelText('Run summary')).not.toHaveTextContent('%')
    fireEvent.click(screen.getByRole('button', { name: '查看外部处理步骤' }))
    expect(locate).toHaveBeenCalledWith(node.node_id)
  })
})
