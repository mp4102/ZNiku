/** 占位任务抽屉只消费现有投影并回调精确导航；高度、展开和页签不属于工程或运行合同。 */
import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import type { NodeRunWire, RunSummaryWire } from '../contracts'
import { nodeStateLabel, runHistoryLabel, runTargetLabel } from '../run-presentation'
import { focusIfAvailable } from './focus-management'
import './task-drawer.css'

export type TaskDrawerTab = 'current' | 'history' | 'problems'
const tabs: ReadonlyArray<readonly [TaskDrawerTab, string]> = [
  ['current', '当前处理'], ['history', '历史记录'], ['problems', '问题'],
]
const collapsedHeight = 36
const minimumHeight = 120
function maximumHeight(): number { return Math.max(minimumHeight, Math.min(420, Math.floor(window.innerHeight * 0.45))) }

export interface TaskDrawerProps {
  readonly open: boolean
  readonly tab: TaskDrawerTab
  readonly onOpenChange: (open: boolean) => void
  readonly onTabChange: (tab: TaskDrawerTab) => void
  readonly summaries: ReadonlyArray<RunSummaryWire>
  readonly selectedRunId: string | null
  readonly selectedSummary: RunSummaryWire | null
  /** Workspace 传入所选 Run 的最新 attempt；其他 Run 的迟到详情不会在本组件展示或定位。 */
  readonly nodeRuns: ReadonlyArray<NodeRunWire>
  readonly nodeLabel?: (nodeId: string) => string
  readonly onSelectRun: (runId: string) => void
  readonly onLocateNode: (runId: string, nodeId: string) => void
  readonly nextRunCursor?: string | null
  readonly historyBusy?: boolean
  readonly onLoadOlder?: () => void
  readonly diagnostics?: ReactNode
  readonly problemCount?: number
  readonly otherWaitingCount?: number
  readonly outputs?: ReactNode
  readonly outputCount?: number
  readonly currentGraphActions?: ReactNode
  readonly showingSnapshot?: boolean
  readonly onReturnToEditing?: () => void
  readonly onAbandon?: (runId: string) => void
  readonly abandonDisabled?: boolean
  readonly abandonDisabledReason?: string
  readonly returnFocusRef?: RefObject<HTMLElement | null>
}

export function TaskDrawer({ open, tab, onOpenChange, onTabChange, summaries, selectedRunId, selectedSummary,
  nodeRuns, nodeLabel, onSelectRun, onLocateNode, nextRunCursor = null, historyBusy = false, onLoadOlder,
  diagnostics, problemCount, otherWaitingCount = 0, outputs, outputCount, currentGraphActions, showingSnapshot = false,
  onReturnToEditing, onAbandon, abandonDisabled = false, abandonDisabledReason, returnFocusRef }: TaskDrawerProps) {
  const id = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const tabRefs = useRef(new Map<TaskDrawerTab, HTMLButtonElement>())
  const priorOpen = useRef(open)
  const openerRef = useRef<HTMLElement | null>(null)
  const [heightLimit, setHeightLimit] = useState(maximumHeight)
  const [height, setHeight] = useState(() => Math.min(260, maximumHeight()))
  // Resize 只更新本面板的 session 尺寸，不保存、不重新选择任务或创建轮询。
  useEffect(() => {
    const resize = () => {
      const limit = maximumHeight()
      setHeightLimit(limit)
      setHeight((current) => Math.min(current, limit))
    }
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])
  useLayoutEffect(() => {
    if (open && !priorOpen.current) {
      openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      tabRefs.current.get(tab)?.focus()
    } else if (!open && priorOpen.current) {
      const target = returnFocusRef?.current ?? openerRef.current
      if (!focusIfAvailable(target)) triggerRef.current?.focus()
    }
    priorOpen.current = open
  }, [open, returnFocusRef, tab])

  const selected = selectedSummary?.run_id === selectedRunId ? selectedSummary : null
  // 只有所选 snapshot 拥有这份实例名投影；其他历史不能套用当前图的别名。
  const historyLabel = (summary: RunSummaryWire) => runHistoryLabel(summary, summary.run_id === selectedRunId ? nodeLabel : undefined)
  const targetLabel = (summary: RunSummaryWire) => runTargetLabel(summary, summary.run_id === selectedRunId ? nodeLabel : undefined)
  const selectedNodes = selected ? nodeRuns.filter((nodeRun) => nodeRun.run_id === selected.run_id) : []
  const active = summaries.filter((summary) => summary.actionable || summary.state === 'running')
  const waitingCount = active.reduce((count, summary) => count + summary.state_counts.waiting_external, 0)
  const failedCount = active.reduce((count, summary) => count + summary.state_counts.failed, 0)
  const problems = problemCount ?? failedCount
  const summaryText = selected
    ? `${showingSnapshot ? '正在查看本次处理' : '处理记录'} · ${targetLabel(selected)} · 已完成 ${selected.state_counts.completed}/${selected.node_count} 步`
    : active.length > 0 ? `${active.length} 项处理记录正在进行` : '没有正在处理的任务'
  const tabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: TaskDrawerTab) => {
    const index = tabs.findIndex(([key]) => key === current)
    const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
      : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length
        : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null
    if (next === null) return
    event.preventDefault()
    const nextTab = tabs[next]![0]
    onTabChange(nextTab)
    tabRefs.current.get(nextTab)?.focus()
  }
  const returnButton = showingSnapshot && onReturnToEditing
    ? <button className="button button--ghost" type="button" onClick={onReturnToEditing}>返回当前编辑</button> : null

  return <section className={`task-drawer ${open ? 'is-open' : ''}`} aria-label="任务抽屉"
    style={{ height: open ? height : collapsedHeight }} onKeyDown={(event) => {
      if (open && event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); onOpenChange(false) }
    }}>
    <div className="task-drawer-summary" aria-label="任务摘要">
      <button ref={triggerRef} className="task-drawer-trigger" type="button" aria-expanded={open}
        aria-controls={`${id}-content`} aria-label={open ? '收起任务区' : '展开任务区'} onClick={() => onOpenChange(!open)}>
        <span aria-hidden="true">{open ? '⌄' : '⌃'}</span><span>{summaryText}</span>
      </button>
      <span className="task-drawer-counts">{waitingCount} 项等待外部处理 · {problems} 项问题</span>
      {otherWaitingCount > 0 && <span className="task-drawer-other">当前另有 {otherWaitingCount} 项任务待操作</span>}
    </div>
    <div className="task-drawer-content" id={`${id}-content`} hidden={!open}>
      <div className="task-drawer-toolbar">
        <div className="task-drawer-tabs" role="tablist" aria-label="任务内容">
          {tabs.map(([key, label]) => <button key={key} ref={(element) => { if (element) tabRefs.current.set(key, element); else tabRefs.current.delete(key) }}
            id={`${id}-tab-${key}`} role="tab" type="button" aria-selected={tab === key} tabIndex={tab === key ? 0 : -1}
            aria-controls={`${id}-panel-${key}`} onKeyDown={(event) => tabKeyDown(event, key)} onClick={() => onTabChange(key)}>{label}{key === 'problems' && problems > 0 ? `（${problems}）` : ''}</button>)}
        </div>
        <label className="task-drawer-resize">高度<input aria-label="任务区高度" type="range" min={minimumHeight} max={heightLimit} step={10}
          value={height} aria-valuetext={`${height} 像素`} onChange={(event) => {
            const value = Number(event.target.value)
            if (Number.isFinite(value)) setHeight(Math.max(minimumHeight, Math.min(heightLimit, value)))
          }} /></label>
        <button className="task-drawer-close" type="button" aria-label="关闭任务区" onClick={() => onOpenChange(false)}>关闭</button>
      </div>
      <div className="task-drawer-panel" role="tabpanel" id={`${id}-panel-current`} aria-labelledby={`${id}-tab-current`} hidden={tab !== 'current'} tabIndex={0}>
        <div className="task-drawer-columns">
          <section className="task-drawer-active" aria-label="活动任务导航">
            <h3>当前待处理任务</h3>
            {active.length === 0 ? <p>没有正在处理的任务。</p> : <ul>{active.map((summary, index) => <li key={summary.run_id}>
              <button type="button" value={summary.run_id} aria-label={`查看任务 ${index + 1}：${historyLabel(summary)}`}
                aria-current={summary.run_id === selectedRunId ? 'true' : undefined} onClick={() => onSelectRun(summary.run_id)}>
                <strong>任务 {index + 1} · {targetLabel(summary)}</strong>
                <span>{historyLabel(summary)}</span>
              </button>
            </li>)}</ul>}
          </section>
          <section className="task-drawer-detail" aria-label="被查看处理详情">
            <h3>{showingSnapshot ? '本次处理使用的流程（只读）' : '被查看的处理记录'}</h3>
            {selected ? <>
              <p>{historyLabel(selected)}</p>{returnButton}
              <ul className="task-drawer-node-list">{selectedNodes.map((nodeRun) => <li key={nodeRun.node_run_id}>
                <button type="button" onClick={() => onLocateNode(selected.run_id, nodeRun.node_id)}>
                  <strong>{nodeLabel?.(nodeRun.node_id) ?? nodeRun.node_id}</strong><span>{nodeStateLabel(nodeRun.state)}</span>
                  {nodeRun.state === 'running' && nodeRun.progress !== null && <span>{Math.round(nodeRun.progress * 100)}%</span>}
                </button>
              </li>)}</ul>
              {selectedNodes.length === 0 && <p>这份处理记录暂无可显示的步骤详情。</p>}
              {(outputs !== undefined || outputCount !== undefined) && <section className="task-drawer-outputs" aria-label="本次处理输出">
                <h4>本次输出{outputCount !== undefined ? `（${outputCount}）` : ''}</h4>
                {outputCount === 0 ? <p>本次处理没有已登记输出；工作流可以有零个或多个输出。</p> : outputs}
              </section>}
              {onAbandon && <div className="task-drawer-danger">
                <p>此操作只放弃被查看的处理记录，不删除工程或已有媒体；下一步仍需确认。</p>
                <button className="button button--danger" type="button" disabled={abandonDisabled}
                  aria-describedby={abandonDisabled ? `${id}-abandon-reason` : undefined} onClick={() => onAbandon(selected.run_id)}>放弃本次处理</button>
                {abandonDisabled && <p id={`${id}-abandon-reason`}>{abandonDisabledReason ?? '当前不能放弃此处理，请等待进行中的操作完成。'}</p>}
              </div>}
            </> : <p>{selectedRunId ? '所选处理详情尚未就绪，请等待可信详情或明确选择另一任务。' : '选择一项任务查看详情；不会自动启动或提交。'}</p>}
          </section>
        </div>
        {currentGraphActions && <section className="task-drawer-current-graph" aria-label="当前编辑的工作流">
          <h3>当前编辑的工作流</h3><p>下列操作针对当前编辑图，不针对上方被查看的处理记录。</p>{currentGraphActions}
        </section>}
      </div>
      <div className="task-drawer-panel" role="tabpanel" id={`${id}-panel-history`} aria-labelledby={`${id}-tab-history`} hidden={tab !== 'history'} tabIndex={0}>
        <div className="task-drawer-history-heading"><h3>处理历史</h3>{returnButton}</div>
        {summaries.length === 0 ? <p>尚无处理记录。</p> : <ol className="task-drawer-history">{summaries.map((summary, index) => <li key={summary.run_id}>
          <button type="button" value={summary.run_id} aria-label={`查看处理记录 ${index + 1}：${historyLabel(summary)}`}
            aria-current={summary.run_id === selectedRunId ? 'true' : undefined} onClick={() => onSelectRun(summary.run_id)}>{historyLabel(summary)}</button>
        </li>)}</ol>}
        {nextRunCursor && onLoadOlder && <button className="button button--ghost" type="button" disabled={historyBusy} onClick={onLoadOlder}>{historyBusy ? '正在加载历史…' : '加载更早记录'}</button>}
      </div>
      <div className="task-drawer-panel" role="tabpanel" id={`${id}-panel-problems`} aria-labelledby={`${id}-tab-problems`} hidden={tab !== 'problems'} tabIndex={0}>
        <h3>问题与修复建议</h3>{diagnostics ?? <p>当前没有需要显示的问题。</p>}
      </div>
    </div>
  </section>
}
