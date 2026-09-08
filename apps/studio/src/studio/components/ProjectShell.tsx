/** 提供 Studio 顶层创作者工程入口；路径与身份只留在明确展开的开发入口。 */

import type { ReactNode } from 'react'

export interface ProjectShellProps {
  readonly desktopControls?: ReactNode
  readonly projectName: string | null
  readonly projectId: string | null
  readonly nodeCount: number
  readonly dirty: boolean
  readonly saving?: boolean
  readonly draftBlocked?: boolean
  readonly saveError?: string | null
  readonly canUndo?: boolean
  readonly canRedo?: boolean
  readonly advanced?: boolean
  readonly onUndo?: () => void
  readonly onRedo?: () => void
  readonly onToggleAdvanced?: () => void
  readonly onReloadProject?: () => void
  readonly onOpenStorage?: () => void
  readonly profile: { readonly status: string; readonly compatible: boolean; readonly modified: boolean } | null
  readonly projectPath: string
  readonly projectIdDraft: string
  readonly projectNameDraft: string
  readonly serviceBusy: boolean
  readonly projectSwitchBlocked?: boolean
  readonly statusStale: boolean
  readonly canSave: boolean
  readonly hostBridgeAvailable: boolean
  readonly canResumeGuided: boolean
  readonly runCenter: ReactNode
  readonly onProjectPathChange: (value: string) => void
  readonly onOpenTemplates: () => void
  readonly onHome: () => void
  readonly onOpenWithPicker: () => void
  readonly onOpenProject: () => void
  readonly onCreateProject: () => void
  readonly onSaveProject: () => void
}
export function ProjectShell({
  desktopControls,
  projectName,
  projectId,
  nodeCount,
  dirty,
  saving = false,
  draftBlocked = false,
  saveError,
  canUndo = false,
  canRedo = false,
  advanced = false,
  onUndo,
  onRedo,
  onToggleAdvanced,
  onReloadProject,
  onOpenStorage,
  profile,
  projectPath,
  projectIdDraft,
  projectNameDraft,
  serviceBusy,
  projectSwitchBlocked = serviceBusy,
  statusStale,
  canSave,
  hostBridgeAvailable,
  canResumeGuided,
  runCenter,
  onProjectPathChange,
  onOpenTemplates,
  onHome,
  onOpenWithPicker,
  onOpenProject,
  onCreateProject,
  onSaveProject,
}: ProjectShellProps) {
  return (
    <header className="topbar">
      <div className="brand-lockup">
        <div className="brand-mark">ZN</div>
        <div><span className="brand-name">ZNIKU</span><span className="brand-subtitle">Studio</span></div>
      </div>
      <div className="workflow-identity">
        <span className="eyebrow">{advanced ? 'PROJECT GRAPH' : '当前工程'}</span>
        <strong>{projectName ?? '打开或新建 .zniku 工程'}</strong>
        <span className="identity-meta" role="status" aria-label="工程保存状态" aria-live="polite" aria-atomic="true">
          {projectId ? `${nodeCount} 个节点 · ${saveError ? '保存未完成' : saving ? '正在保存…' : dirty ? '有未保存更改' : draftBlocked ? '已保存，但暂不可运行' : '已保存'}` : '选择已有工程，或创建新的工作流'}
        </span>
        {saveError && <div className="authoring-save-error" role="alert"><span>{saveError}</span><button onClick={onReloadProject} type="button">重新载入磁盘版本</button></div>}
        {profile && (
          <span className={`workflow-profile-state ${profile.compatible ? 'is-compatible' : 'is-unverified'}`} role="status">
            {profile.modified
              ? '增强工作流已调整 · 运行前请重新检查'
              : profile.compatible
                ? '增强工作流已就绪'
                : '增强工作流需要检查'}
          </span>
        )}
      </div>
      <div className="project-location">
        {desktopControls}
        {onOpenStorage && <button className="button button--ghost" type="button" disabled={serviceBusy || statusStale || !projectId} onClick={onOpenStorage}>工程数据</button>}
        <div className="edit-history-actions"><button aria-label="撤销" title="撤销 Ctrl+Z" type="button" disabled={serviceBusy || !canUndo} onClick={onUndo}>撤销</button><button aria-label="重做" title="重做 Ctrl+Shift+Z" type="button" disabled={serviceBusy || !canRedo} onClick={onRedo}>重做</button></div>
        <button type="button" className="button button--ghost" aria-pressed={advanced} onClick={onToggleAdvanced}>{advanced ? '返回创作者模式' : '高级节点图'}</button>
        <button className="button button--ghost" disabled={serviceBusy} onClick={onHome} type="button">工程首页</button>
        <button className="button button--template" disabled={serviceBusy || statusStale || !projectId || !canResumeGuided} onClick={onOpenTemplates} title={projectId && !canResumeGuided ? '此工程不是可继续分析的增强视频工程' : undefined} type="button">{canResumeGuided ? '继续处理向导' : '处理向导'}</button>
        <button className="button button--ghost" type="button" disabled={projectSwitchBlocked || statusStale || !hostBridgeAvailable} onClick={onOpenWithPicker}>打开工程</button>
        <button className="button button--ghost" type="button" disabled={serviceBusy || !canSave} onClick={onSaveProject}>保存</button>
        <details className="project-developer-entry">
          <summary>开发入口</summary>
          <label>工程路径<input aria-label="工程路径" value={projectPath} onChange={(event) => onProjectPathChange(event.target.value)} placeholder="D:\\Projects\\example.zniku" /></label>
          <label>工程名称<input aria-label="开发入口工程名称" value={projectNameDraft} readOnly /></label>
          <label>Project ID<input aria-label="开发入口 Project ID" value={projectIdDraft} readOnly /></label>
          <div>
            <button aria-label="打开" className="button button--ghost" type="button" disabled={projectSwitchBlocked || statusStale || !projectPath.trim()} onClick={onOpenProject}>按路径打开</button>
            <button aria-label="新建" className="button button--ghost" type="button" disabled={projectSwitchBlocked || statusStale || !projectPath.trim() || !projectNameDraft.trim()} onClick={onCreateProject}>按路径新建空白工程</button>
          </div>
        </details>
      </div>
      {runCenter}
    </header>
  )
}
