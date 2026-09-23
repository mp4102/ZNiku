/** 瞬时请求提示与正式运行投影保持分离；不创建 Runtime 状态、ETA 或完成结论。 */
import type { WorkflowProgressData } from '../model'
import type { NodeRunWire } from './contracts'
import { creatorElapsedLabel } from './run-presentation'

export interface LocalActivity {
  readonly token: symbol
  readonly projectSessionId: string
  readonly nodeRun: NodeRunWire
  readonly label: string
  readonly phase: OperationView['phase']
  readonly message: string
  readonly fraction?: number | null
  /** 本机发起时间不是服务起时；只用于明确标记的请求等待时间。 */
  readonly requestedAt?: number
}
export type ActivityListener = (activity: LocalActivity, replace?: boolean) => void
export interface OperationView {
  readonly phase: 'requesting' | 'running' | 'settling' | 'needs_user' | 'uncertain'
  readonly label: string
  readonly message: string
  readonly fraction: number | null
  readonly elapsed?: string | null
}
/** 跟随既有 Workspace/进度投影刷新，不创建计时器或后台轮询。 */
export function localActivityView(activity: LocalActivity, now = Date.now()): OperationView {
  const timing = ['requesting', 'running', 'settling'].includes(activity.phase)
  const seconds = timing && activity.requestedAt !== undefined && Number.isFinite(activity.requestedAt) && Number.isFinite(now)
    ? Math.max(0, Math.floor((now - activity.requestedAt) / 1_000)) : null
  const duration = seconds === null ? null : seconds < 60 ? `${seconds} 秒`
    : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟 ${seconds % 60} 秒` : `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分钟`
  return { phase: activity.phase, label: activity.label, message: activity.message,
    fraction: ['running', 'settling'].includes(activity.phase) ? activity.fraction ?? null : null,
    elapsed: duration === null ? null : `请求等待时间 ${duration}（本机计时）` }
}
export function boundActivity(activity: LocalActivity | null, session: string | null, node: NodeRunWire | null, now = Date.now()): OperationView | null {
  const before = activity?.nodeRun
  if (!activity || !node || !before || activity.projectSessionId !== session || node.state !== 'waiting_external' ||
      before.run_id !== node.run_id || before.node_id !== node.node_id || before.node_run_id !== node.node_run_id ||
      before.attempt !== node.attempt || before.external_handoff?.handoff_id !== node.external_handoff?.handoff_id) return null
  return localActivityView(activity, now)
}
export function runtimeOperation(node: NodeRunWire | null, progress: WorkflowProgressData | null, label: string): OperationView | null {
  if (!node) return null
  if (node.state === 'waiting_external') return { phase: 'needs_user', label, message: '等待你交回处理结果、检查并提交', fraction: null }
  if (node.state !== 'running') return null
  const fraction = progress?.mode === 'determinate' ? progress.fraction : null
  return { phase: fraction === 1 ? 'settling' : 'running', label, fraction,
    elapsed: progress?.elapsed ? `${creatorElapsedLabel(progress.elapsed)}（服务起时）` : null,
    message: fraction === 1 ? `${progress?.stage ?? '媒体处理已到总量'}，等待完成确认` : progress?.stage ?? '正在处理，请稍候' }
}
