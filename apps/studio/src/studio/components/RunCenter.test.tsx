/** 唯一主操作与只读诊断分离；展示层不推导运行资格或持有另一套命令。 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrimaryRunAction, ServiceDiagnostics, RunCenter, type RunCenterProps } from './RunCenter'
import { handoffEnvelope, handoffSummary } from '../test-fixtures'

afterEach(cleanup)
function props(overrides: Partial<RunCenterProps> = {}): RunCenterProps {
  const fresh = { stale: false, lastSuccess: null }
  return { health: { status: fresh, detail: fresh, log: fresh, readiness: fresh }, status: null,
    summaries: [handoffSummary()], viewRunId: handoffSummary().run_id,
    runBlocked: false, runToBlocked: true, rerunBlocked: true, onSelectRun: vi.fn(),
    onRunAll: vi.fn(), onRunTo: vi.fn(), onRerun: vi.fn(), ...overrides }
}
describe('上下文主操作与只读服务诊断', () => {
  it('只展示一个主操作，不再夹带历史、精确命令和内部身份', () => {
    const action = vi.fn()
    const base = props()
    const { container } = render(<PrimaryRunAction health={base.health} status={handoffEnvelope()}
      action={{ label: '处理外部文件（2）', disabled: false, onAction: action }} />)
    expect(container.querySelectorAll('.button--primary')).toHaveLength(1)
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByText(/Run all|Run to|Rerun|高级运行操作/)).not.toBeInTheDocument()
    expect(container).not.toHaveTextContent(handoffSummary().run_id)
    fireEvent.click(screen.getByRole('button', { name: '处理外部文件（2）' }))
    expect(action).toHaveBeenCalledTimes(1)
    expect(base.onRunAll).not.toHaveBeenCalled()
  })
  it('兼容入口的高级模式不再自动展开命令或诊断', () => {
    const options = props({ advanced: true })
    const { container } = render(<RunCenter {...options} />)
    expect(container.querySelector('details')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Run to here' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '开始处理' }))
    expect(options.onRunAll).toHaveBeenCalledTimes(1)
    expect(options.onRunTo).not.toHaveBeenCalled()
    expect(options.onRerun).not.toHaveBeenCalled()
  })
  it('禁用主操作有可读说明和恢复入口，不调用执行意图', () => {
    const action = vi.fn(), recover = vi.fn(), base = props()
    render(<PrimaryRunAction health={base.health} status={null}
      action={{ label: '开始处理', disabled: true, reason: '请应用未保存设置。', recoveryLabel: '查看设置', onAction: action, onRecover: recover }} />)
    const button = screen.getByRole('button', { name: '开始处理' })
    expect(button).toHaveAccessibleDescription('请应用未保存设置。')
    fireEvent.click(button)
    expect(action).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '查看设置' }))
    expect(recover).toHaveBeenCalledTimes(1)
  })
  it('连接提示保留最后可信状态；重新连接仍只调用传入主操作', () => {
    const recover = vi.fn(), base = props()
    render(<PrimaryRunAction health={{ ...base.health, status: { stale: true, lastSuccess: null } }} status={null}
      action={{ label: '重新连接', disabled: false, onAction: recover }} />)
    expect(screen.getByRole('status')).toHaveTextContent('显示的是上次状态')
    fireEvent.click(screen.getByRole('button', { name: '重新连接' }))
    expect(recover).toHaveBeenCalledTimes(1)
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })
  it('详情频道失效提示陈旧状态，但不擅自改变只读主操作资格', () => {
    const base = props()
    render(<PrimaryRunAction health={{ ...base.health, detail: { stale: true, lastSuccess: 'synthetic-time' } }}
      status={null} action={{ label: '查看进度', disabled: false, onAction: vi.fn() }} />)
    expect(screen.getByRole('status')).toHaveTextContent('步骤详情暂未更新，仍保留上次可信状态')
    expect(screen.getByRole('button', { name: '查看进度' })).toBeEnabled()
  })
  it('高级诊断默认关闭、纯只读，原状态和精确身份按需可查', () => {
    const base = props(), status = handoffEnvelope()
    const { container, rerender } = render(<ServiceDiagnostics health={base.health} status={status} viewRunId={handoffSummary().run_id} />)
    const details = container.querySelector('details')!
    expect(details.open).toBe(false)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    for (const value of screen.getAllByText(handoffSummary().run_id, { selector: 'code' })) expect(value).not.toBeVisible()
    fireEvent.click(screen.getByText('服务高级诊断'))
    expect(details.open).toBe(true)
    rerender(<ServiceDiagnostics health={{ ...base.health, readiness: { stale: true, lastSuccess: 'synthetic-time' } }}
      status={{ ...status, active_operation: 'import_external' }} viewRunId={handoffSummary().run_id} />)
    expect(details.open).toBe(true)
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('READINESS STALE')
    expect(screen.getByText('import_external')).toBeVisible()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
