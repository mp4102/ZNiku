/**
 * 管理只属于当前浏览器的最近工程偏好。
 *
 * 这里只保存 Project Service 已确认打开的路径、显示名和最近时间；不保存 Project ID、Graph、Run、
 * Artifact、HostBridge token 或任何媒体路径。损坏、未知版本或不可用的 Storage 会安全降级为空列表，
 * 绝不影响正式 Project authority。
 */

const STORAGE_KEY = 'zniku.studio.recent-projects.v1'
const MAX_RECENT_PROJECTS = 8
const MAX_PATH_LENGTH = 4096
const MAX_NAME_LENGTH = 200

export interface RecentProject {
  readonly path: string
  readonly name: string
  readonly opened_at: string
}

interface RecentProjectDocument {
  readonly version: 1
  readonly projects: ReadonlyArray<RecentProject>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isAbsoluteProjectPath(value: string): boolean {
  if (
    value.length === 0 ||
    value.length > MAX_PATH_LENGTH ||
    !value.toLowerCase().endsWith('.zniku') ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) return false
  const absolute = /^[A-Za-z]:[\\/]/.test(value) || /^\\\\[^\\/]+[\\/][^\\/]+/.test(value) || value.startsWith('/')
  if (!absolute) return false
  return !value.split(/[\\/]+/).some((segment) => segment === '.' || segment === '..')
}

function isDisplayName(value: string): boolean {
  return value.length > 0 && value.length <= MAX_NAME_LENGTH && value.trim() === value && !/[\u0000-\u001f\u007f]/.test(value)
}

function isRecentProject(value: unknown): value is RecentProject {
  if (!isRecord(value)) return false
  if (Object.keys(value).sort().join('\u0000') !== ['name', 'opened_at', 'path'].join('\u0000')) {
    return false
  }
  return (
    typeof value.path === 'string' &&
    isAbsoluteProjectPath(value.path) &&
    typeof value.name === 'string' &&
    isDisplayName(value.name) &&
    typeof value.opened_at === 'string' &&
    value.opened_at.length <= 64 &&
    Number.isFinite(Date.parse(value.opened_at))
  )
}

export function readRecentProjects(storage: Storage | null = safeLocalStorage()): RecentProject[] {
  if (!storage) return []
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (raw === null) return []
    const value: unknown = JSON.parse(raw)
    if (
      !isRecord(value) ||
      Object.keys(value).sort().join('\u0000') !== ['projects', 'version'].join('\u0000') ||
      value.version !== 1 ||
      !Array.isArray(value.projects) ||
      value.projects.some((item) => !isRecentProject(item))
    ) {
      return []
    }
    const seen = new Set<string>()
    return [...value.projects]
      .sort((left, right) => right.opened_at.localeCompare(left.opened_at))
      .filter((item) => {
        const key = item.path.toLocaleLowerCase()
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
      .slice(0, MAX_RECENT_PROJECTS)
  } catch {
    return []
  }
}

export function rememberRecentProject(
  project: Omit<RecentProject, 'opened_at'> & { readonly opened_at?: string },
  storage: Storage | null = safeLocalStorage(),
  previous?: ReadonlyArray<RecentProject>,
): RecentProject[] {
  if (!storage) return []
  const openedAt = project.opened_at ?? new Date().toISOString()
  if (
    !isAbsoluteProjectPath(project.path) ||
    !isDisplayName(project.name) ||
    openedAt.length > 64 ||
    !Number.isFinite(Date.parse(openedAt))
  ) {
    return readRecentProjects(storage)
  }
  const normalizedPath = project.path.toLocaleLowerCase()
  const projects = [
    { path: project.path, name: project.name, opened_at: openedAt },
    ...(previous ?? readRecentProjects(storage)).filter(
      (item) => item.path.toLocaleLowerCase() !== normalizedPath,
    ),
  ].slice(0, MAX_RECENT_PROJECTS)
  const document: RecentProjectDocument = { version: 1, projects }
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(document))
    return projects
  } catch {
    return readRecentProjects(storage)
  }
}

function safeLocalStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}
