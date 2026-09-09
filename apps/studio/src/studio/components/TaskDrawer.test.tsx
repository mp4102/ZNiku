/** 抽屉是受控视图：精确导航、零任务/多任务、焦点和尺寸都不能产生隐式执行。 */
import { useRef, useState } from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RunSummaryWire } from '../contracts'
import { handoffFixtureIds, handoffRun, handoffSummary } from '../test-fixtures'
import { TaskDrawer, type TaskDrawerProps, type TaskDrawerTab } from './TaskDrawer'

const originalHeight = window.innerHeight
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  Object.defineProperty(window, 'innerHeight', { value: originalHeight, configurable: true, writable: true })
})
function props(overrides: Partial<TaskDrawerProps> = {}): TaskDrawerProps {
  const summary = handoffSummary()
  return { open: false, tab: 'current', onOpenChange: vi.fn(), onTabChange: vi.fn(),
    summaries: [summary], selectedRunId: summary.run_id, selectedSummary: summary,
    nodeRuns: handoffRun().node_runs, nodeLabel: (nodeId) => nodeId === 'transform' ? '画质增强（1）' : `步骤 ${nodeId}`,
    onSelectRun: vi.fn(), onLocateNode: vi.fn(), onAbandon: vi.fn(), ...overrides }
}
function Controlled({ options, externalTrigger = false }: { readonly options: TaskDrawerProps; readonly externalTrigger?: boolean }) {
  const [open, setOpen] = useState(options.open)
  const [tab, setTab] = useState<TaskDrawerTab>(options.tab)
  const externalRef = useRef<HTMLButtonElement>(null)
  return <>
    {externalTrigger && <button ref={externalRef} type="button" onClick={() => setOpen(true)}>打开任务</button>}
    <TaskDrawer {...options} open={open} tab={tab} returnFocusRef={externalTrigger ? externalRef : undefined}
      onOpenChange={(value) => { options.onOpenChange(value); setOpen(value) }}
      onTabChange={(value) => { options.onTabChange(value); setTab(value) }} />
  </>
}
function secondSummary(): RunSummaryWire {
  return { ...handoffSummary(), run_id: '00000000-0000-4000-8000-000000000050',
    target_mode: 'selected', selected_targets: ['other'],
    created_at: '2026-08-24T01:00:00Z', state_counts: { pending: 0, running: 1, waiting_external: 2, completed: 0, failed: 0 } }
}

describe('占位任务抽屉', () => {
  it('收起只保留36px任务摘要，等待和问题不会隐藏，也不会自动导航或执行', () => {
    const options = props({ summaries: [handoffSummary(), secondSummary()], problemCount: 2, otherWaitingCount: 2 })
    render(<TaskDrawer {...options} />)
    expect(screen.getByRole('region', { name: '任务抽屉' })).toHaveStyle({ height: '36px' })
    expect(screen.getByLabelText('任务摘要')).toHaveTextContent('3 项等待外部处理 · 2 项问题')
    expect(screen.getByLabelText('任务摘要')).toHaveTextContent('当前另有 2 项任务待操作')
    expect(screen.queryByRole('tabpanel')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开任务区' })).toHaveAttribute('aria-expanded', 'false')
    expect(options.onSelectRun).not.toHaveBeenCalled()
    expect(options.onLocateNode).not.toHaveBeenCalled()
    expect(options.onAbandon).not.toHaveBeenCalled()
  })

  it('多个活动任务全部列为选择项，不猜最新或第一项身份', () => {
    const options = props({ open: true, summaries: [handoffSummary(), secondSummary()] })
    render(<TaskDrawer {...options} />)
    const navigation = within(screen.getByRole('region', { name: '活动任务导航' }))
    expect(navigation.getAllByRole('button')).toHaveLength(2)
    expect(options.onSelectRun).not.toHaveBeenCalled()
    fireEvent.click(navigation.getByRole('button', { name: /^查看任务 2：/ }))
    expect(options.onSelectRun).toHaveBeenCalledExactlyOnceWith(secondSummary().run_id)
    expect(options.onLocateNode).not.toHaveBeenCalled()
    expect(options.onAbandon).not.toHaveBeenCalled()
  })

  it('步骤导航与危险动作绑定被查看Run，忽略另一Run的迟到节点', () => {
    const other = secondSummary()
    const options = props({ open: true, nodeRuns: [...handoffRun().node_runs,
      { ...handoffRun().node_runs[1]!, run_id: other.run_id, node_id: 'wrong-node' }],
    })
    render(<TaskDrawer {...options} />)
    expect(screen.queryByText('步骤 wrong-node')).not.toBeInTheDocument()
    const detail = within(screen.getByRole('region', { name: '被查看处理详情' }))
    fireEvent.click(detail.getByRole('button', { name: /画质增强（1）/ }))
    expect(options.onLocateNode).toHaveBeenCalledExactlyOnceWith(handoffFixtureIds.run, 'transform')
    fireEvent.click(detail.getByRole('button', { name: '放弃本次处理' }))
    expect(options.onAbandon).toHaveBeenCalledExactlyOnceWith(handoffFixtureIds.run)
    expect(detail.getByText(/不删除工程或已有媒体/)).toBeVisible()
  })

  it('summary与所选ID不符时不展示产物或危险入口，不替用户改选', () => {
    const options = props({ open: true, selectedRunId: secondSummary().run_id,
      outputs: <span>不应串绑的输出</span>, outputCount: 1 })
    render(<TaskDrawer {...options} />)
    expect(screen.getByText(/所选处理详情尚未就绪/)).toBeVisible()
    expect(screen.queryByText('不应串绑的输出')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '放弃本次处理' })).not.toBeInTheDocument()
    expect(options.onSelectRun).not.toHaveBeenCalled()
  })

  it('危险动作保留上游禁用资格和理由；抽屉不另算执行许可', () => {
    const options = props({ open: true, abandonDisabled: true, abandonDisabledReason: '本次任务仍在提交，请等待回执。' })
    render(<TaskDrawer {...options} />)
    const button = screen.getByRole('button', { name: '放弃本次处理' })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription('本次任务仍在提交，请等待回执。')
    fireEvent.click(button)
    expect(options.onAbandon).not.toHaveBeenCalled()
  })

  it('历史使用中文时间范围状态，不暴露UUID或套用其他Run的当前别名', () => {
    const other = secondSummary()
    const options = props({ open: true, tab: 'history', summaries: [handoffSummary(), other],
      nodeLabel: () => '当前节点新别名', nextRunCursor: 'synthetic-cursor', onLoadOlder: vi.fn() })
    render(<TaskDrawer {...options} />)
    const history = within(screen.getByRole('tabpanel', { name: '历史记录' }))
    expect(history.getByRole('button', { name: /^查看处理记录 1：/ })).toHaveTextContent('完整工作流')
    const otherRow = history.getByRole('button', { name: /^查看处理记录 2：/ })
    expect(otherRow).toHaveTextContent('处理到所选步骤')
    expect(otherRow).not.toHaveTextContent('当前节点新别名')
    expect(history.getByRole('button', { name: /^查看处理记录 1：/ })).not.toHaveTextContent(handoffFixtureIds.run)
    expect(options.onLoadOlder).not.toHaveBeenCalled()
    fireEvent.click(otherRow)
    expect(options.onSelectRun).toHaveBeenCalledExactlyOnceWith(other.run_id)
    fireEvent.click(history.getByRole('button', { name: '加载更早记录' }))
    expect(options.onLoadOlder).toHaveBeenCalledTimes(1)
  })

  it('没有活动任务或历史时明确为空；翻页占位只由调用方控制', () => {
    const options = props({ open: true, selectedRunId: null, selectedSummary: null, summaries: [], nodeRuns: [],
      nextRunCursor: 'synthetic-cursor', historyBusy: true, onLoadOlder: vi.fn() })
    render(<Controlled options={options} />)
    expect(screen.getByRole('region', { name: '活动任务导航' })).toHaveTextContent('没有正在处理的任务')
    fireEvent.click(screen.getByRole('tab', { name: '历史记录' }))
    expect(screen.getByText('尚无处理记录。')).toBeVisible()
    expect(screen.getByRole('button', { name: '正在加载历史…' })).toBeDisabled()
    expect(options.onLoadOlder).not.toHaveBeenCalled()
  })

  it('历史输出与当前图高级操作分区，零或多个输出均不假设唯一成品', () => {
    const options = props({ open: true, showingSnapshot: true, outputCount: 0, onReturnToEditing: vi.fn(),
      currentGraphActions: <button type="button">开始新的完整处理</button> })
    const { rerender } = render(<TaskDrawer {...options} />)
    expect(screen.getByRole('region', { name: '本次处理输出' })).toHaveTextContent('零个或多个输出')
    expect(screen.getByRole('region', { name: '当前编辑的工作流' })).toHaveTextContent('不针对上方被查看的处理记录')
    fireEvent.click(screen.getByRole('button', { name: '返回当前编辑' }))
    expect(options.onReturnToEditing).toHaveBeenCalledTimes(1)
    rerender(<TaskDrawer {...options} outputCount={2} outputs={<ul><li>输出一</li><li>输出二</li></ul>} />)
    expect(screen.getByRole('region', { name: '本次处理输出' })).toHaveTextContent('本次输出（2）输出一输出二')
  })

  it('人工等待不显示伪进度或路径，数据刷新不自动切页签或夺焦点', () => {
    const waiting = handoffRun().node_runs.find((item) => item.external_handoff)!
    const options = props({ open: true, nodeRuns: [{ ...waiting, progress: 0.8 }] })
    const { rerender } = render(<TaskDrawer {...options} />)
    expect(screen.getByRole('region', { name: '被查看处理详情' })).not.toHaveTextContent(/%|倒计时|C:\\/)
    const control = screen.getByRole('slider', { name: '任务区高度' })
    control.focus()
    rerender(<TaskDrawer {...options} selectedSummary={{ ...handoffSummary(), latest_activity_at: '2026-08-24T02:00:00Z' }} />)
    expect(control).toHaveFocus()
    expect(options.onTabChange).not.toHaveBeenCalled()
    expect(options.onOpenChange).not.toHaveBeenCalled()
  })

  it('tabs支持方向键HomeEnd，Escape收起并恢复内部trigger焦点', async () => {
    const user = userEvent.setup()
    render(<Controlled options={props()} />)
    const trigger = screen.getByRole('button', { name: '展开任务区' })
    await user.click(trigger)
    expect(screen.getByRole('tab', { name: '当前处理' })).toHaveFocus()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: '历史记录' })).toHaveFocus()
    expect(screen.getByRole('tab', { name: '历史记录' })).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{End}')
    expect(screen.getByRole('tab', { name: '问题' })).toHaveFocus()
    await user.keyboard('{Home}{ArrowLeft}')
    expect(screen.getByRole('tab', { name: '问题' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('由顶栏打开时关闭恢复外部trigger焦点，不启动或放弃任务', async () => {
    const user = userEvent.setup()
    const options = props()
    render(<Controlled options={options} externalTrigger />)
    const trigger = screen.getByRole('button', { name: '打开任务' })
    await user.click(trigger)
    await user.click(screen.getByRole('button', { name: '关闭任务区' }))
    expect(trigger).toHaveFocus()
    expect(options.onAbandon).not.toHaveBeenCalled()
    expect(options.onSelectRun).not.toHaveBeenCalled()
  })

  it('高度可调整且开合保留，视口收缩有界，不写回任务投影', () => {
    const options = props({ open: true })
    const original = JSON.stringify(options.summaries)
    render(<Controlled options={options} />)
    fireEvent.change(screen.getByRole('slider', { name: '任务区高度' }), { target: { value: '300' } })
    expect(screen.getByRole('region', { name: '任务抽屉' })).toHaveStyle({ height: '300px' })
    expect(screen.getByRole('slider', { name: '任务区高度' })).toHaveAttribute('aria-valuetext', '300 像素')
    fireEvent.click(screen.getByRole('button', { name: '关闭任务区' }))
    expect(screen.getByRole('region', { name: '任务抽屉' })).toHaveStyle({ height: '36px' })
    fireEvent.click(screen.getByRole('button', { name: '展开任务区' }))
    expect(screen.getByRole('region', { name: '任务抽屉' })).toHaveStyle({ height: '300px' })
    Object.defineProperty(window, 'innerHeight', { value: 200, configurable: true })
    act(() => { window.dispatchEvent(new Event('resize')) })
    expect(screen.getByRole('region', { name: '任务抽屉' })).toHaveStyle({ height: '120px' })
    expect(JSON.stringify(options.summaries)).toBe(original)
    expect(options.onAbandon).not.toHaveBeenCalled()
  })

  it('问题内容由调用方给出，关闭抽屉不隐藏摘要数量', () => {
    render(<Controlled options={props({ open: true, tab: 'problems', problemCount: 3, diagnostics: <p>请修复已定位的输入连接。</p> })} />)
    expect(screen.getByRole('tabpanel', { name: '问题（3）' })).toHaveTextContent('请修复已定位的输入连接。')
    fireEvent.click(screen.getByRole('button', { name: '关闭任务区' }))
    expect(screen.getByLabelText('任务摘要')).toHaveTextContent('3 项问题')
  })
})
