/** 展示主操作、历史和运行摘要；命令资格和上下文由 Workspace/Project Service 提供。 */
import { useId, useRef, type ReactNode } from 'react'
import type { NodeRunWire, RunSummaryWire, StatusEnvelope } from '../contracts'
import { nodeStateLabel, runHistoryLabel, runTargetLabel } from '../run-presentation'

export interface ResourceHealthView { readonly stale: boolean; readonly lastSuccess: string | null }
export type ResourceChannel = 'status' | 'detail' | 'readiness' | 'log'
export const targetLabel = runTargetLabel
export const summaryOptionLabel = runHistoryLabel
export interface CreatorRunAction {
  readonly label: string
  readonly disabled: boolean
  readonly reason?: string
  readonly recoveryLabel?: string
  readonly onAction: () => void
  readonly onRecover?: () => void
}
export interface RunCenterProps {
  readonly health: Readonly<Record<ResourceChannel, ResourceHealthView>>
  readonly status: StatusEnvelope | null
  readonly summaries: ReadonlyArray<RunSummaryWire>
  readonly viewRunId: string | null
  readonly runBlocked: boolean
  readonly runToBlocked: boolean
  readonly rerunBlocked: boolean
  readonly onSelectRun: (runId: string) => void
  readonly onRunAll: () => void
  readonly onRunTo: () => void
  readonly onRerun: () => void
  readonly advanced?: boolean
  readonly nodeLabel?: (nodeId: string) => string
  readonly primaryAction?: CreatorRunAction
  readonly blockedReasons?: { readonly runAll?: string; readonly runTo?: string; readonly rerun?: string }
  readonly onRecoverService?: () => void
}
export function RunCenter({ health, status, summaries, viewRunId, runBlocked, runToBlocked, rerunBlocked,
  onSelectRun, onRunAll, onRunTo, onRerun, advanced = false, nodeLabel, primaryAction,
  blockedReasons, onRecoverService }: RunCenterProps) {
  const reasonId = useId()
  const advancedActionsRef = useRef<HTMLDetailsElement>(null)
  const action = primaryAction ?? { label: '开始处理', disabled: runBlocked, onAction: onRunAll,
    reason: runBlocked ? '当前还不能开始。请检查工程、未应用设置和问题清单。' : undefined }
  const reasons = {
    runAll: blockedReasons?.runAll ?? '当前不能开始新的完整处理，请先处理未应用设置或工程问题。',
    runTo: blockedReasons?.runTo ?? '请在当前工作流中选择一个步骤，并确认工程可运行。',
    rerun: blockedReasons?.rerun ?? '请选中当前处理记录内可重跑的步骤，并确认没有进行中的操作。',
  }
  return <div className="top-actions creator-run-center" aria-label="运行中心">
    <div className="creator-run-action">
      <button className="button button--primary" type="button" disabled={action.disabled}
        aria-describedby={action.disabled ? reasonId : undefined} onClick={() => {
          if (!advanced && advancedActionsRef.current) advancedActionsRef.current.open = false
          action.onAction()
        }}>{action.label}</button>
      {action.disabled && <p id={reasonId} className="action-disabled-reason">{action.reason ?? '此操作暂不可用，请先检查当前工程状态。'}</p>}
      {action.disabled && action.onRecover && <button className="button button--ghost" type="button" onClick={action.onRecover}>{action.recoveryLabel ?? '查看恢复方法'}</button>}
    </div>
    {health.status.stale && <div className="run-service-warning" role="status"><span>连接已中断，显示的是上次状态，暂不能发起处理。</span>
      {onRecoverService && !primaryAction ? <button type="button" onClick={onRecoverService}>重新连接</button> : <small>请确认本机服务仍在运行，连接恢复后再操作。</small>}
    </div>}
    {!health.status.stale && health.detail.stale && <div className="run-service-warning" role="status"><span>步骤详情暂未更新，仍保留上次可信状态。操作前请等待详情恢复。</span></div>}
    <label className="run-selector">
      <span>{advanced ? '查看 Run' : '处理记录'}</span>
      <select aria-label={advanced ? '查看 Run' : '处理记录'} value={viewRunId ?? ''}
        onChange={(event) => event.target.value && onSelectRun(event.target.value)} disabled={summaries.length === 0}>
        {summaries.length === 0 && <option value="">尚无处理记录</option>}
        {summaries.map((summary) => <option key={summary.run_id} value={summary.run_id}>{runHistoryLabel(summary, nodeLabel)}{advanced ? ` · ${summary.run_id}` : ''}</option>)}
      </select>
    </label>
    <details ref={advancedActionsRef} className="run-advanced-actions" open={advanced || undefined}>
      <summary>高级运行操作</summary>
      <div className="run-advanced-body">
        <p>精确命令仍作用于同一工程；重跑会先显示运行服务给出的影响清单。</p>
        <div className="exact-run-actions">
          {([
            ['Run all', runBlocked, onRunAll, reasons.runAll],
            ['Run to here', runToBlocked, onRunTo, reasons.runTo],
            ['Rerun from here', rerunBlocked, onRerun, reasons.rerun],
          ] as const).map(([label, disabled, onClick, reason], index) => <div key={label}>
            <button className="button button--ghost" type="button" disabled={disabled} onClick={onClick} aria-describedby={disabled ? `${reasonId}-${index}` : undefined}>{label}</button>
            {disabled && <p id={`${reasonId}-${index}`} className="action-disabled-reason">{reason}</p>}
          </div>)}
        </div>
        {viewRunId && <p>Run ID：<code>{viewRunId}</code></p>}
        <span className={`authority-badge ${health.status.stale ? 'is-unavailable' : ''}`}>
          {health.status.stale ? 'STATUS STALE' : status?.active_operation ? `HOST · ${status.active_operation.toUpperCase()}` : 'PROJECT SERVICE'}
        </span>
        <div className="channel-health" aria-label="Resource channel health">
          {(Object.entries(health) as Array<[ResourceChannel, ResourceHealthView]>).map(([channel, value]) => <span className={value.stale ? 'is-stale' : ''} key={channel}>{channel.toUpperCase()} {value.stale ? 'STALE' : 'OK'} · {value.lastSuccess ?? 'never'}</span>)}
        </div>
      </div>
    </details>
  </div>
}
export interface RunCanvasOverlaysProps {
  readonly viewedSummary: RunSummaryWire | null
  readonly firstWaiting: NodeRunWire | null
  readonly firstWaitingInputPaths: ReadonlyArray<string>
  readonly firstWaitingReadinessLabel: string
  readonly firstWaitingElapsedLabel: string
  readonly globalActionSummary: RunSummaryWire | null
  readonly firstFailed: NodeRunWire | null
  readonly sameRun: boolean
  readonly onLocateNode: (nodeId: string) => void
  readonly onSelectRun: (runId: string) => void
  readonly advanced?: boolean
  readonly nodeLabel?: (nodeId: string) => string
}
export function RunCanvasOverlays({ viewedSummary, firstWaiting, firstWaitingInputPaths,
  firstWaitingReadinessLabel, firstWaitingElapsedLabel, globalActionSummary, firstFailed, sameRun,
  onLocateNode, onSelectRun, advanced = false, nodeLabel }: RunCanvasOverlaysProps): ReactNode {
  return <div className="canvas-run-overlays">
    {viewedSummary && <div className="run-summary-strip" aria-label="Run summary">
      <strong>{runTargetLabel(viewedSummary, nodeLabel)}</strong>
      <span>已完成 {viewedSummary.state_counts.completed}/{viewedSummary.node_count} 步</span>
      {viewedSummary.state_counts.running > 0 && <span>{viewedSummary.state_counts.running} 步正在处理</span>}
      {viewedSummary.state_counts.waiting_external > 0 && <span>{viewedSummary.state_counts.waiting_external} 步等待外部处理</span>}
      {viewedSummary.state_counts.failed > 0 && <span>{viewedSummary.state_counts.failed} 步需要处理问题</span>}
      {advanced && <span>{viewedSummary.state}</span>}
    </div>}
    {firstWaiting && <div className="next-action-banner" role="status">
      <span className="eyebrow">下一步</span><div className="next-action-copy">
        <strong>{nodeLabel?.(firstWaiting.node_id) ?? '当前步骤'} · 等待外部处理</strong>
        <small>在外部工具完成处理后，返回此处检查输出并显式提交。</small>
        {advanced && <code>{firstWaiting.node_id} · {firstWaitingInputPaths.join(', ')} → {firstWaiting.external_handoff?.output_targets.map((target) => target.path).join(', ')}</code>}
      </div><span>{firstWaitingReadinessLabel} · {firstWaitingElapsedLabel}</span>
      <button type="button" onClick={() => onLocateNode(firstWaiting.node_id)}>查看外部处理步骤</button>
    </div>}
    {!firstWaiting && globalActionSummary && <div className="next-action-banner" role="status">
      <span className="eyebrow">需要处理</span>
      <strong>{sameRun && firstFailed ? `${nodeLabel?.(firstFailed.node_id) ?? '当前步骤'} · ${nodeStateLabel(firstFailed.state)}` : runTargetLabel(globalActionSummary, nodeLabel)}</strong>
      <span>{globalActionSummary.state_counts.waiting_external} 步等待外部处理 · {globalActionSummary.state_counts.failed} 步需要修复</span>
      <button type="button" onClick={() => sameRun && firstFailed ? onLocateNode(firstFailed.node_id) : onSelectRun(globalActionSummary.run_id)}>{sameRun && firstFailed ? '查看失败步骤' : '查看需处理记录'}</button>
    </div>}
  </div>
}
