/** 提供 Studio 顶层 Project 身份、路径命令与 Run Center 插槽。 */

import type { ReactNode } from 'react'

export interface ProjectShellProps {
  readonly projectName: string | null
  readonly projectId: string | null
  readonly nodeCount: number
  readonly dirty: boolean
  readonly profile: { readonly status: string; readonly compatible: boolean; readonly modified: boolean } | null
  readonly projectPath: string
  readonly projectIdDraft: string
  readonly projectNameDraft: string
  readonly serviceBusy: boolean
  readonly statusStale: boolean
  readonly canSave: boolean
  readonly runCenter: ReactNode
  readonly onProjectPathChange: (value: string) => void
  readonly onOpenTemplates: () => void
  readonly onOpenProject: () => void
  readonly onCreateProject: () => void
  readonly onSaveProject: () => void
}
export function ProjectShell({
  projectName,
  projectId,
  nodeCount,
  dirty,
  profile,
  projectPath,
  projectIdDraft,
  projectNameDraft,
  serviceBusy,
  statusStale,
  canSave,
  runCenter,
  onProjectPathChange,
  onOpenTemplates,
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
        <span className="eyebrow">PROJECT GRAPH</span>
        <strong>{projectName ?? '打开或新建 .zniku 工程'}</strong>
        <span className="identity-meta">
          {projectId ? `${projectId} · ${nodeCount} nodes · ${dirty ? '未保存' : '已保存'}` : '0.3.0 Project Service wire authority'}
        </span>
        {profile && (
          <span className={`workflow-profile-state ${profile.compatible ? 'is-compatible' : 'is-unverified'}`} role="status">
            AVEnhanceFlow 2.7 · {profile.status}{profile.modified ? ' · 自由编辑后已降级' : ''}
          </span>
        )}
      </div>
      <div className="project-location">
        <button className="button button--template" disabled={serviceBusy || statusStale} onClick={onOpenTemplates} type="button">Templates</button>
        <input aria-label="工程路径" value={projectPath} onChange={(event) => onProjectPathChange(event.target.value)} placeholder="D:\\Projects\\example.zniku" />
        <button className="button button--ghost" type="button" disabled={serviceBusy || statusStale || !projectPath.trim()} onClick={onOpenProject}>打开</button>
        <button className="button button--ghost" type="button" disabled={serviceBusy || statusStale || !projectPath.trim() || !projectIdDraft.trim() || !projectNameDraft.trim()} onClick={onCreateProject}>新建</button>
        <button className="button button--ghost" type="button" disabled={!canSave} onClick={onSaveProject}>保存</button>
      </div>
      {runCenter}
    </header>
  )
}
