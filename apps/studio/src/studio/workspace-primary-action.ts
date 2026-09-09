/** 只决定主操作文案与导航目的地；资格由 Workspace 的既有服务门禁传入，不执行命令。 */
export type WorkspaceActionTarget = 'reconnect' | 'home' | 'tasks' | 'files' | 'problems' | 'outputs' | 'settings' | 'save' | 'run'
export interface WorkspaceActionContext {
  readonly connected: boolean
  readonly loading: boolean
  readonly hasProject: boolean
  readonly historical: boolean
  readonly viewedState: string | null
  readonly viewedWaiting: number
  readonly viewedFailed: number
  readonly viewedRunning: number
  readonly activeWaiting: number
  readonly activeFailed: number
  readonly activeRunning: number
  readonly parameterDirty: boolean
  readonly saveError: boolean
  readonly diagnostics: number
  readonly executionChanged: boolean
  readonly staleResults: boolean
  readonly runBlocked: boolean
  readonly runReason?: string
}
export interface WorkspaceActionView {
  readonly target: WorkspaceActionTarget
  readonly label: string
  readonly disabled: boolean
  readonly reason?: string
}
export function workspacePrimaryAction(context: WorkspaceActionContext): WorkspaceActionView {
  const action = (target: WorkspaceActionTarget, label: string): WorkspaceActionView => ({ target, label, disabled: false })
  if (!context.connected) return { target: 'reconnect', label: '重新连接', disabled: context.loading }
  if (!context.hasProject) return { target: 'home', label: '新建或打开工程', disabled: context.loading }
  // 历史主操作永远属于被查看对象，另一活动 Run 或下一次处理的草稿不改变它。
  if (context.historical) {
    if (context.viewedFailed) return action('problems', '查看本次问题')
    if (context.viewedWaiting) return action('files', context.viewedWaiting === 1 ? '处理外部文件' : `处理外部文件（${context.viewedWaiting}）`)
    if (context.viewedState === 'completed') return action('outputs', '查看本次输出')
    return action('tasks', '查看本次进度')
  }
  if (context.activeRunning) return action('tasks', '查看进度')
  if (context.activeWaiting) return action('files', context.activeWaiting === 1 ? '处理外部文件' : `处理外部文件（${context.activeWaiting}）`)
  if (context.activeFailed) return action('problems', '查看问题')
  if (context.parameterDirty) return action('settings', '完成设置')
  if (context.saveError) return action('save', '查看保存问题')
  if (context.diagnostics) return action('problems', '检查工作流')
  if (context.viewedFailed && !context.executionChanged) return action('problems', '查看问题')
  if (context.viewedState === 'completed' && !context.executionChanged && !context.staleResults) return action('outputs', '查看输出')
  return { target: 'run', label: '开始处理', disabled: context.runBlocked, reason: context.runReason }
}
