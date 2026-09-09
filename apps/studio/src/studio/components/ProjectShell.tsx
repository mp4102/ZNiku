/** 顶栏只组织已有操作与状态；工程、参数、保存和运行的唯一所有者仍在 Workspace。 */

import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import './project-shell.css'

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
  readonly parameterDirty?: boolean
  readonly libraryOpen?: boolean
  readonly onUndo?: () => void
  readonly onRedo?: () => void
  readonly onToggleAdvanced?: () => void
  readonly onReloadProject?: () => void
  readonly onOpenStorage?: () => void
  readonly onOpenHistory?: () => void
  readonly onToggleLibrary?: () => void
  readonly onOpenDiagnostics?: () => void
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
  parameterDirty = false,
  libraryOpen = true,
  onUndo,
  onRedo,
  onToggleAdvanced,
  onReloadProject,
  onOpenStorage,
  onOpenHistory,
  onToggleLibrary,
  onOpenDiagnostics,
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
  const [openMenu, setOpenMenu] = useState<'project' | 'view' | null>(null)
  const menuId = useId()
  const rootRef = useRef<HTMLElement>(null)
  const projectTriggerRef = useRef<HTMLButtonElement>(null)
  const viewTriggerRef = useRef<HTMLButtonElement>(null)
  const projectMenuRef = useRef<HTMLDivElement>(null)
  const viewMenuRef = useRef<HTMLDivElement>(null)
  const trigger = (menu: 'project' | 'view') => menu === 'project' ? projectTriggerRef.current : viewTriggerRef.current
  const closeMenu = (restoreFocus = true) => {
    if (openMenu && restoreFocus) trigger(openMenu)?.focus()
    setOpenMenu(null)
  }
  // DesktopExit 自己拥有确认框；菜单既不卸载它，也不截获它的 Escape/焦点循环。
  const hasOwnedDialog = () => !!rootRef.current?.querySelector('[role="dialog"][aria-modal="true"]')
  const invoke = (action: (() => void) | undefined) => {
    closeMenu()
    action?.()
  }
  const toggleMenu = (menu: 'project' | 'view') => {
    if (!hasOwnedDialog()) setOpenMenu((current) => current === menu ? null : menu)
  }
  useEffect(() => {
    if (!openMenu) return
    const outside = (event: PointerEvent) => {
      const panel = openMenu === 'project' ? projectMenuRef.current : viewMenuRef.current
      if (!(event.target instanceof Element) || panel?.contains(event.target) || projectTriggerRef.current?.contains(event.target) || viewTriggerRef.current?.contains(event.target) || hasOwnedDialog()) return
      // 点击外部输入仍让浏览器按原意移焦；点击非交互空白才回到原触发按钮。
      const focusable = event.target.closest('button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')
      if (!focusable) (openMenu === 'project' ? projectTriggerRef : viewTriggerRef).current?.focus()
      setOpenMenu(null)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [openMenu])
  const onMenuKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (!openMenu || hasOwnedDialog()) return
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      closeMenu()
    } else if (event.key === 'ArrowDown' && event.target === trigger(openMenu)) {
      event.preventDefault()
      const panel = openMenu === 'project' ? projectMenuRef.current : viewMenuRef.current
      panel?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
    }
  }
  const saveState = saveError ? '保存未完成' : saving ? '正在保存…' : dirty ? '有未保存更改' : draftBlocked ? '已保存，但暂不可运行' : '已保存'
  return (
    <header ref={rootRef} className="topbar project-shell" onKeyDown={onMenuKeyDown}>
      <div className="project-shell-brand" role="img" aria-label="ZNIKU Studio" title="ZNIKU Studio"><span aria-hidden="true">ZN</span></div>
      <div className="project-shell-menus">
        <div className="project-shell-menu-anchor">
          <button ref={projectTriggerRef} type="button" className="project-shell-trigger" aria-expanded={openMenu === 'project'} aria-controls={`${menuId}-project`} onClick={() => toggleMenu('project')}>工程 <span aria-hidden="true">▾</span></button>
          <div ref={projectMenuRef} id={`${menuId}-project`} className="project-shell-menu" role="group" aria-label="工程菜单" hidden={openMenu !== 'project'}>
            <button type="button" disabled={serviceBusy} onClick={() => invoke(onHome)}>新建工程…</button>
            <button type="button" disabled={projectSwitchBlocked || statusStale || !hostBridgeAvailable} onClick={() => invoke(onOpenWithPicker)}>打开工程</button>
            <button type="button" disabled={serviceBusy} onClick={() => invoke(onHome)}>最近工程</button>
            <div className="project-shell-menu-separator" />
            <button type="button" disabled={serviceBusy || !canSave} onClick={() => invoke(onSaveProject)}>保存</button>
            {onOpenStorage && <button type="button" disabled={serviceBusy || statusStale || !projectId} onClick={() => invoke(onOpenStorage)}>工程数据</button>}
            <button type="button" disabled={serviceBusy || statusStale || !projectId || !canResumeGuided} onClick={() => invoke(onOpenTemplates)} title={projectId && !canResumeGuided ? '此工程不是可继续分析的增强视频工程' : undefined}>{canResumeGuided ? '继续处理向导' : '处理向导'}</button>
            {profile && <p className="project-shell-profile" role="status">{profile.modified ? '增强工作流已调整 · 运行前请重新检查' : profile.compatible ? '增强工作流已就绪' : '增强工作流需要检查'}</p>}
            <button type="button" disabled={serviceBusy} onClick={() => invoke(onHome)}>工程首页</button>
            <div className="project-shell-menu-separator" />
            <details className="project-shell-developer-entry">
              <summary>开发入口</summary>
              <label>工程路径<input aria-label="工程路径" value={projectPath} onChange={(event) => onProjectPathChange(event.target.value)} placeholder="D:\\Projects\\example.zniku" /></label>
              <label>工程名称<input aria-label="开发入口工程名称" value={projectNameDraft} readOnly /></label>
              <label>Project ID<input aria-label="开发入口 Project ID" value={projectIdDraft} readOnly /></label>
              <div>
                <button aria-label="打开" type="button" disabled={projectSwitchBlocked || statusStale || !projectPath.trim()} onClick={() => invoke(onOpenProject)}>按路径打开</button>
                <button aria-label="新建" type="button" disabled={projectSwitchBlocked || statusStale || !projectPath.trim() || !projectNameDraft.trim()} onClick={() => invoke(onCreateProject)}>按路径新建空白工程</button>
              </div>
            </details>
            {desktopControls}
          </div>
        </div>
        <div className="project-shell-menu-anchor">
          <button ref={viewTriggerRef} type="button" className="project-shell-trigger" aria-expanded={openMenu === 'view'} aria-controls={`${menuId}-view`} onClick={() => toggleMenu('view')}>视图 <span aria-hidden="true">▾</span></button>
          <div ref={viewMenuRef} id={`${menuId}-view`} className="project-shell-menu project-shell-menu--view" role="group" aria-label="视图菜单" hidden={openMenu !== 'view'}>
            <button type="button" aria-pressed={advanced} onClick={() => invoke(onToggleAdvanced)}>{advanced ? '返回创作者模式' : '高级节点图'}</button>
            {onToggleLibrary && <button type="button" aria-pressed={libraryOpen} onClick={() => invoke(onToggleLibrary)}>{libraryOpen ? '收起节点库' : '展开节点库'}</button>}
            {onOpenDiagnostics && <button type="button" onClick={() => invoke(onOpenDiagnostics)}>高级诊断</button>}
          </div>
        </div>
      </div>
      <div className="project-shell-identity">
        <strong title={projectName ?? undefined}>{projectName ?? '打开或新建 .zniku 工程'}</strong>
        <span className="project-shell-save-state" role="status" aria-label="工程保存状态" aria-live="polite" aria-atomic="true">
          {projectId ? `${nodeCount} 个节点 · ${saveState}` : '选择已有工程，或创建新的工作流'}
          {statusStale && ' · 连接待恢复'}
        </span>
        {parameterDirty && <span className="project-shell-parameter-dirty" role="status">参数未应用</span>}
      </div>
      <div className="project-shell-history-actions"><button aria-label="撤销" title="撤销 Ctrl+Z" type="button" disabled={serviceBusy || !canUndo} onClick={onUndo}>撤销</button><button aria-label="重做" title="重做 Ctrl+Shift+Z" type="button" disabled={serviceBusy || !canRedo} onClick={onRedo}>重做</button></div>
      {onOpenHistory && <button className="project-shell-history" type="button" onClick={onOpenHistory}>处理记录</button>}
      <div className="project-shell-run-center">{runCenter}</div>
      {saveError && <div className="project-shell-save-error" role="alert"><span>{saveError}</span><button type="button" disabled={serviceBusy || !canSave} onClick={onSaveProject}>重试保存</button>{onReloadProject && <button onClick={onReloadProject} type="button">重新载入磁盘版本</button>}</div>}
    </header>
  )
}
