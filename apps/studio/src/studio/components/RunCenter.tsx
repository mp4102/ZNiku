/** 顶栏只呈现唯一主操作；服务诊断只读，执行资格与上下文仍由 Workspace 提供。 */
import { useId } from 'react'
import type { RunSummaryWire, StatusEnvelope } from '../contracts'
import { runHistoryLabel, runTargetLabel } from '../run-presentation'

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
export interface PrimaryRunActionProps {
  readonly action: CreatorRunAction
  readonly health: Readonly<Record<ResourceChannel, ResourceHealthView>>
  readonly status: StatusEnvelope | null
}
export function PrimaryRunAction({ action, health, status }: PrimaryRunActionProps) {
  const reasonId = useId()
  return <div className="creator-run-center" aria-label="运行中心">
    <div className="creator-run-action">
      <button className="button button--primary" type="button" disabled={action.disabled}
        aria-describedby={action.disabled ? reasonId : undefined} onClick={action.onAction}>{action.label}</button>
      {action.disabled && <p id={reasonId} className="action-disabled-reason">{action.reason ?? '此操作暂不可用，请先检查当前工程状态。'}</p>}
      {action.disabled && action.onRecover && <button className="button button--ghost" type="button" onClick={action.onRecover}>{action.recoveryLabel ?? '查看恢复方法'}</button>}
    </div>
    {health.status.stale && <p className="run-service-warning" role="status">连接已中断，显示的是上次状态。请确认本机服务仍在运行，连接恢复后再操作。</p>}
    {!health.status.stale && health.detail.stale && <p className="run-service-warning" role="status">步骤详情暂未更新，仍保留上次可信状态。操作前请等待详情恢复。</p>}
    {status?.active_operation === 'import_external' && <p role="status">正在导入外部文件…请等待复制和验证完成。</p>}
  </div>
}
export interface ServiceDiagnosticsProps {
  readonly health: Readonly<Record<ResourceChannel, ResourceHealthView>>
  readonly status: StatusEnvelope | null
  readonly viewRunId: string | null
}
/** 精确身份和通道原文按需展开；高级图形密度不自动展开，也不引入执行入口。 */
export function ServiceDiagnostics({ health, status, viewRunId }: ServiceDiagnosticsProps) {
  return <details className="service-diagnostics">
    <summary>服务高级诊断</summary>
    <p>以下是当前页面持有的只读服务投影，不代表新的运行资格。</p>
    <dl>
      <dt>服务状态</dt><dd>{health.status.stale ? 'STATUS STALE' : 'PROJECT SERVICE'}</dd>
      <dt>当前服务操作</dt><dd>{status?.active_operation ?? '无'}</dd>
      <dt>工程会话 ID</dt><dd><code>{status?.project_session_id ?? '无'}</code></dd>
      <dt>被查看 Run ID</dt><dd><code>{viewRunId ?? '无'}</code></dd>
      <dt>服务活动 Run ID</dt><dd><code>{status?.active_run_id ?? '无'}</code></dd>
    </dl>
    <div className="channel-health" aria-label="Resource channel health">
      {(Object.entries(health) as Array<[ResourceChannel, ResourceHealthView]>).map(([channel, value]) =>
        <span className={value.stale ? 'is-stale' : ''} key={channel}>{channel.toUpperCase()} {value.stale ? 'STALE' : 'OK'} · {value.lastSuccess ?? 'never'}</span>)}
    </div>
  </details>
}
/** 迁移期间保留调用类型；旧入口不再拥有历史、精确命令或画布浮层。 */
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
export function RunCenter(props: RunCenterProps) {
  const action = props.primaryAction ?? { label: '开始处理', disabled: props.runBlocked, onAction: props.onRunAll,
    reason: props.runBlocked ? '当前还不能开始。请检查工程、未应用设置和问题清单。' : undefined }
  return <PrimaryRunAction action={action} health={props.health} status={props.status} />
}
