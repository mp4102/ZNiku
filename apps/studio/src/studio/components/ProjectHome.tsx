/** 提供不暴露路径、ID 或 CLI 的创作者工程首页；所有副作用由上层显式回调执行。 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { RecentProject } from '../recent-projects'
import { dialogFocusTargets } from './focus-management'

export interface ProjectHomeProps {
  readonly desktopControls?: ReactNode
  readonly open: boolean
  readonly loading: boolean
  readonly serviceUnavailable: boolean
  readonly serviceMessage: string | null
  readonly serviceErrorDetails?: string | null
  readonly hostBridgeAvailable: boolean
  readonly busy: boolean
  readonly hasOpenProject: boolean
  readonly recentProjects: ReadonlyArray<RecentProject>
  readonly onClose: () => void
  readonly onCreateGuided: () => void
  readonly onCreateBlank: (name: string) => Promise<void>
  readonly onOpenExisting: () => Promise<void>
  readonly onOpenRecent: (path: string) => Promise<void>
  readonly onRetryService: () => void
}

function recentTime(value: string): string {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return '最近使用'
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path
}

export function ProjectHome({
  desktopControls,
  open,
  loading,
  serviceUnavailable,
  serviceMessage,
  serviceErrorDetails,
  hostBridgeAvailable,
  busy,
  hasOpenProject,
  recentProjects,
  onClose,
  onCreateGuided,
  onCreateBlank,
  onOpenExisting,
  onOpenRecent,
  onRetryService,
}: ProjectHomeProps) {
  const wasOpen = useRef(false)
  const dialogRef = useRef<HTMLElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const blankButtonRef = useRef<HTMLButtonElement | null>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)
  const [blankOpen, setBlankOpen] = useState(false)
  const [blankName, setBlankName] = useState('未命名视频工程')

  useEffect(() => {
    if (open && !wasOpen.current) {
      previousFocusRef.current = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
      setBlankOpen(false)
      setBlankName('未命名视频工程')
      const initial = closeButtonRef.current ?? dialogRef.current?.querySelector<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
      ) ?? dialogRef.current
      initial?.focus()
    } else if (!open && wasOpen.current) {
      setBlankOpen(false)
      previousFocusRef.current?.focus()
      previousFocusRef.current = null
    }
    wasOpen.current = open
  }, [open])

  useEffect(() => {
    if (!open) return
    const dialog = dialogRef.current
    if (!dialog) return
    const active = document.activeElement
    if (active instanceof HTMLElement && dialog.contains(active) && active !== dialog) return
    const initial = closeButtonRef.current ?? dialog.querySelector<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
    )
    initial?.focus()
  }, [hasOpenProject, hostBridgeAvailable, loading, open, serviceUnavailable])

  useEffect(() => {
    if (!open) return
    const handleKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      if (event.key === 'Escape') {
        if (hasOpenProject && !busy) onClose()
        return
      }
      if (event.key !== 'Tab') return
      const dialog = dialogRef.current
      if (!dialog) return
      const focusable = dialogFocusTargets(dialog)
      if (focusable.length === 0) {
        event.preventDefault()
        return
      }
      const first = focusable[0]!
      const last = focusable.at(-1)!
      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [busy, hasOpenProject, onClose, open])

  if (!open) return null

  return (
    <div className="project-home-backdrop" role="presentation">
      <section
        aria-label="ZNIKU Studio 工程首页"
        aria-modal="true"
        className="project-home"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="project-home-header">
          <div className="brand-lockup project-home-brand">
            <div className="brand-mark">ZN</div>
            <div><span className="brand-name">ZNIKU</span><span className="brand-subtitle">Studio</span></div>
          </div>
          {desktopControls}
          {hasOpenProject && (
            <button className="template-wizard-close" aria-label="关闭工程首页" disabled={busy} onClick={onClose} ref={closeButtonRef} type="button">×</button>
          )}
        </header>

        <div className="project-home-intro">
          <span className="eyebrow">从素材到作品</span>
          <h1>从视频开始，不从工程术语开始。</h1>
          <p>选择素材和处理目标，ZNIKU 会把它变成仍可自由编辑的普通节点工作流。</p>
        </div>

        {loading ? (
          <section className="project-home-service" aria-live="polite">
            <span className="project-home-service-dot" />
            <div><strong>正在连接本机服务</strong><p>正在读取工程与节点目录…</p></div>
          </section>
        ) : serviceUnavailable ? (
          <section className="project-home-service is-offline" role="alert">
            <span className="project-home-service-dot" />
            <div>
              <strong>本机服务暂时不可用</strong>
              <p>{serviceMessage ?? '无法读取本机工程服务。工程和媒体都没有被修改。'}</p>
              <button className="button button--primary" disabled={busy} onClick={onRetryService} type="button">重新连接</button>
            </div>
          </section>
        ) : (
          <>
            {serviceMessage && <p className="project-home-inline-error" role="alert">{serviceMessage}</p>}
            {serviceMessage && serviceErrorDetails && <details><summary>高级 → 选择窗口原始详情</summary><pre>{serviceErrorDetails}</pre></details>}
            {!hostBridgeAvailable && (
              <section className="project-home-host-recovery" aria-live="polite">
                <span>桌面文件选择器暂时不可用；仍可查看当前工程。</span>
                <button className="button button--ghost" disabled={busy} onClick={onRetryService} type="button">重试桌面连接</button>
              </section>
            )}
            <div className="project-home-actions">
              <button className="project-home-action is-primary" disabled={busy} onClick={onCreateGuided} type="button">
                <span className="project-home-action-icon">＋</span>
                <strong>新建视频工程</strong>
                <small>选择素材并使用引导式增强流程</small>
              </button>
              <button className="project-home-action" disabled={busy || !hostBridgeAvailable} onClick={() => void onOpenExisting()} type="button">
                <span className="project-home-action-icon">↗</span>
                <strong>打开已有工程</strong>
                <small>{hostBridgeAvailable ? '从本机选择 .zniku 文件' : '桌面文件选择器未连接'}</small>
              </button>
              <button className="project-home-action" disabled={busy || !hostBridgeAvailable} onClick={() => setBlankOpen(true)} ref={blankButtonRef} type="button">
                <span className="project-home-action-icon">◇</span>
                <strong>空白工作流</strong>
                <small>自由添加素材、处理步骤、分支与汇合</small>
              </button>
            </div>

            {blankOpen && (
              <form
                className="project-home-blank"
                onSubmit={(event) => {
                  event.preventDefault()
                  if (blankName.trim()) void onCreateBlank(blankName.trim())
                }}
              >
                <label>
                  工程名称
                  <input aria-label="空白工程名称" autoFocus disabled={busy} maxLength={200} onChange={(event) => setBlankName(event.target.value)} value={blankName} />
                </label>
                <div>
                  <button className="button button--ghost" disabled={busy} onClick={() => { setBlankOpen(false); blankButtonRef.current?.focus() }} type="button">取消</button>
                  <button className="button button--primary" disabled={busy || !blankName.trim()} type="submit">选择保存位置</button>
                </div>
              </form>
            )}

            <section className="project-home-recent" aria-label="最近工程">
              <header><h2>最近工程</h2><span>仅保存在这台电脑</span></header>
              {recentProjects.length === 0 ? (
                <p className="project-home-empty">还没有最近工程。打开或创建后会出现在这里。</p>
              ) : (
                <div className="project-home-recent-list">
                  {recentProjects.map((project) => (
                    <button disabled={busy} key={project.path.toLocaleLowerCase()} onClick={() => void onOpenRecent(project.path)} type="button">
                      <span><strong>{project.name}</strong><small>{fileName(project.path)}</small></span>
                      <time dateTime={project.opened_at}>{recentTime(project.opened_at)}</time>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </section>
    </div>
  )
}
