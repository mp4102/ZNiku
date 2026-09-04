/** 将 Run 选择、命令入口和画布运行摘要集中为只读/命令投影，不推导 Runtime 状态。 */

import type { ReactNode } from 'react'
import type { NodeRunWire, RunSummaryWire, StatusEnvelope } from '../contracts'

export interface ResourceHealthView {
  readonly stale: boolean
  readonly lastSuccess: string | null
}
export type ResourceChannel = 'status' | 'detail' | 'readiness' | 'log'

export function targetLabel(summary: RunSummaryWire): string {
  return summary.target_mode === 'all'
    ? 'Run all'
    : `Run to ${summary.selected_targets.join(', ')}`
}

export function summaryOptionLabel(summary: RunSummaryWire): string {
  const counts = summary.state_counts
  return `${summary.created_at} · ${targetLabel(summary)} · ${summary.state} · ${counts.completed}/${summary.node_count} completed · ${counts.running} running · ${counts.waiting_external} waiting external · ${counts.failed} failed`
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
}

export function RunCenter({
  health,
  status,
  summaries,
  viewRunId,
  runBlocked,
  runToBlocked,
  rerunBlocked,
  onSelectRun,
  onRunAll,
  onRunTo,
  onRerun,
}: RunCenterProps) {
  return (
    <div className="top-actions" aria-label="Run center">
      <span className={`authority-badge ${health.status.stale ? 'is-unavailable' : ''}`}>
        {health.status.stale ? 'STATUS STALE' : status?.active_operation ? `HOST · ${status.active_operation.toUpperCase()}` : 'PROJECT SERVICE'}
      </span>
      <div className="channel-health" aria-label="Resource channel health">
        {(Object.entries(health) as Array<[ResourceChannel, ResourceHealthView]>).map(([channel, value]) => (
          <span className={value.stale ? 'is-stale' : ''} key={channel}>
            {channel.toUpperCase()} {value.stale ? 'STALE' : 'OK'} · {value.lastSuccess ?? 'never'}
          </span>
        ))}
      </div>
      <label className="run-selector">
        <span>查看 Run</span>
        <select aria-label="查看 Run" value={viewRunId ?? ''} onChange={(event) => event.target.value && onSelectRun(event.target.value)} disabled={summaries.length === 0}>
          {summaries.length === 0 && <option value="">No Runs</option>}
          {summaries.map((summary) => <option key={summary.run_id} value={summary.run_id}>{summaryOptionLabel(summary)}</option>)}
        </select>
      </label>
      <button className="button button--primary" type="button" disabled={runBlocked} onClick={onRunAll}>Run all</button>
      <button className="button button--ghost" type="button" disabled={runToBlocked} onClick={onRunTo}>Run to here</button>
      <button className="button button--ghost" type="button" disabled={rerunBlocked} onClick={onRerun}>Rerun from here</button>
    </div>
  )
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
}

export function RunCanvasOverlays({
  viewedSummary,
  firstWaiting,
  firstWaitingInputPaths,
  firstWaitingReadinessLabel,
  firstWaitingElapsedLabel,
  globalActionSummary,
  firstFailed,
  sameRun,
  onLocateNode,
  onSelectRun,
}: RunCanvasOverlaysProps): ReactNode {
  return (
    <>
      {viewedSummary && (
        <div className="run-summary-strip" aria-label="Run summary">
          <strong>{targetLabel(viewedSummary)}</strong>
          <span>{viewedSummary.state_counts.completed}/{viewedSummary.node_count} completed</span>
          <span>{viewedSummary.state_counts.running} running</span>
          <span>{viewedSummary.state_counts.waiting_external} waiting external</span>
          <span>{viewedSummary.state_counts.failed} failed</span>
        </div>
      )}
      {firstWaiting && (
        <div className="next-action-banner" role="status">
          <span className="eyebrow">NEXT ACTION</span>
          <div className="next-action-copy">
            <strong>{firstWaiting.node_id} 等待人工外部输出</strong>
            {firstWaiting.external_handoff?.instructions && <small>{firstWaiting.external_handoff.instructions}</small>}
            <code>{firstWaitingInputPaths.join(', ')} → {firstWaiting.external_handoff?.output_targets.map((target) => target.path).join(', ')}</code>
          </div>
          <span>{firstWaitingReadinessLabel} · {firstWaitingElapsedLabel}</span>
          <button type="button" onClick={() => onLocateNode(firstWaiting.node_id)}>定位等待节点</button>
        </div>
      )}
      {!firstWaiting && globalActionSummary && (
        <div className="next-action-banner" role="status">
          <span className="eyebrow">NEXT ACTION</span>
          <strong>{globalActionSummary.run_id} 需要操作者处理</strong>
          <span>{globalActionSummary.state_counts.waiting_external} waiting external · {globalActionSummary.state_counts.failed} failed</span>
          <button type="button" onClick={() => sameRun && firstFailed ? onLocateNode(firstFailed.node_id) : onSelectRun(globalActionSummary.run_id)}>
            {sameRun && firstFailed ? '定位失败节点' : '查看需处理 Run'}
          </button>
        </div>
      )}
    </>
  )
}
