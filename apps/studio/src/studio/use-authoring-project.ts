/**
 * 将同一 Graph 和纯展示 StudioState 接入撤销历史及单请求 CAS 自动保存。
 * 异步回执只确认发送的快照；重连与轮询不得丢掉本地未保存编辑。工程切换和冲突
 * 恢复必须显式进行；storage_revision 只是存储并发标记，不是执行身份。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { AuthoringPrecondition, ProjectSnapshotWire, StatusEnvelope, StudioStateWire } from './contracts'
import type { StudioGateway } from './gateway'
import { AuthoringSaveController } from './authoring-save'
import { EditHistory } from './edit-history'

export interface AuthoringDocument {
  readonly snapshot: ProjectSnapshotWire
  readonly studioState: StudioStateWire
}
export const emptyStudioState = (): StudioStateWire => ({
  contract_version: '0.3.0', viewport: null, groups: [], node_views: [],
})
/** 只比较 UI 快照内容；忽略 object 键顺序，不定义执行身份或 digest。 */
function sameJsonData(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true
  if (left === null || right === null || typeof left !== 'object' || typeof right !== 'object') return false
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length &&
      left.every((value, index) => sameJsonData(value, right[index]))
  }
  const a = left as Record<string, unknown>
  const b = right as Record<string, unknown>
  return Object.keys(a).length === Object.keys(b).length &&
    Object.keys(a).every((key) => Object.hasOwn(b, key) && sameJsonData(a[key], b[key]))
}
const equal = (left: AuthoringDocument, right: AuthoringDocument) => sameJsonData(left, right)
type Receipt = { storage_revision: number; status: StatusEnvelope }
interface Session {
  readonly id: string
  readonly path: string | null
  readonly history: EditHistory<AuthoringDocument>
  readonly saves: AuthoringSaveController<AuthoringDocument, Receipt>
}

function normalize(value: AuthoringDocument): AuthoringDocument {
  const ids = new Set(value.snapshot.project.graph.nodes.map((node) => node.node_id))
  return { ...value, studioState: { ...value.studioState, node_views: value.studioState.node_views.filter((view) => ids.has(view.node_id)) } }
}

export function useAuthoringProject(gateway: StudioGateway) {
  const session = useRef<Session | null>(null)
  const [, render] = useState(0)
  const conflictRef = useRef<string | null>(null)
  const onSaved = useRef<(status: StatusEnvelope) => void>(() => undefined)
  const refresh = useCallback(() => render((value) => value + 1), [])

  useEffect(() => () => {
    const old = session.current
    session.current = null
    old?.saves.dispose()
  }, [gateway])

  const block = useCallback((message: string) => {
    conflictRef.current = message
    session.current?.saves.block(new Error(message))
    refresh()
  }, [refresh])

  const ingest = useCallback((status: StatusEnvelope, force = false, macroLabel?: string, preserveUnapplied = false): boolean => {
    const current = session.current
    // 未应用参数是 Workspace session 状态；它同样禁止轮询替换正在编辑的节点来源。
    const pending = current && (current.saves.getSnapshot().dirty || current.history.getSnapshot().transactionActive || preserveUnapplied || conflictRef.current !== null)
    // 明确冲突不能被迟到 status 或“看似新”的轮询响应静默清除。
    if (!force && conflictRef.current !== null) return false
    const changedSession = current && (current.id !== status.project_session_id || current.path !== status.project_path)
    if (!force && changedSession && pending) {
      block('工程会话已在其他窗口切换。本地编辑已保留，请重新载入后继续。')
      return false
    }
    if (!status.snapshot || !status.project_session_id || status.storage_revision === null || !status.studio_state) {
      conflictRef.current = null
      if (current) { session.current = null; current.saves.dispose(); refresh() }
      return true
    }
    const value: AuthoringDocument = { snapshot: status.snapshot, studioState: status.studio_state }
    if (!force && current && !changedSession && status.storage_revision <= current.saves.getSnapshot().storageRevision) {
      return true
    }
    if (macroLabel && current && !changedSession && !pending) {
      current.history.commit(value, macroLabel)
      current.saves.reset(value, status.storage_revision)
      refresh()
      return true
    }
    if (!force && current && !changedSession) {
      const saved = current.saves.getSnapshot()
      // 较旧 status 仍可携带 Run 状态，但绝不回退 authoring 数据或 CAS 标记。
      if (status.storage_revision <= saved.storageRevision || pending) return true
      current.history.reset(value)
      current.saves.reset(value, status.storage_revision)
      refresh()
      return true
    }
    session.current = null
    current?.saves.dispose()
    conflictRef.current = null
    const id = status.project_session_id
    const projectId = status.snapshot.project.project_id
    const history = new EditHistory(value, { equals: equal })
    const saves = new AuthoringSaveController<AuthoringDocument, Receipt>({
      initialValue: value, initialRevision: status.storage_revision, equals: equal,
      save: async (document, revision) => {
        const next = await gateway.command({
          operation: 'save_project', project: document.snapshot.project, studio_state: document.studioState,
          expected_storage_revision: revision, project_session_id: id,
        })
        if (
          next.project_session_id !== id || next.project_path !== status.project_path ||
          next.snapshot?.project.project_id !== projectId || next.storage_revision !== revision + 1 ||
          !sameJsonData(next.snapshot.project, document.snapshot.project) ||
          !sameJsonData(next.studio_state, document.studioState)
        ) {
          throw new Error('保存回执与当前工程会话不匹配；本地编辑已保留。')
        }
        return { storage_revision: next.storage_revision, status: next }
      },
      onChange: () => { if (session.current?.id === id) refresh() },
      onSaved: (_value, receipt) => onSaved.current(receipt.status),
    })
    session.current = { id, path: status.project_path, history, saves }
    refresh()
    return true
  }, [block, gateway, refresh])

  const edit = useCallback((label: string, updater: (value: AuthoringDocument) => AuthoringDocument) => {
    const current = session.current
    if (!current) return
    const next = normalize(updater(current.history.value))
    if (current.history.getSnapshot().transactionActive) current.history.update(next)
    else if (current.history.commit(next, label)) current.saves.update(next)
    refresh()
  }, [refresh])
  const begin = useCallback(() => {
    const history = session.current?.history
    if (history && !history.getSnapshot().transactionActive) { history.begin('移动节点'); refresh() }
  }, [refresh])
  const end = useCallback(() => {
    const current = session.current
    if (current?.history.end()) current.saves.update(current.history.value)
    refresh()
  }, [refresh])
  const travel = useCallback((direction: 'undo' | 'redo') => {
    const current = session.current
    if (!current || current.history.getSnapshot().transactionActive) return
    current.saves.update(current.history[direction]())
    refresh()
  }, [refresh])
  const precondition = useCallback((): AuthoringPrecondition => {
    const current = session.current
    if (!current || conflictRef.current) throw new Error(conflictRef.current ?? '请先打开工程。')
    return { project_session_id: current.id, expected_storage_revision: current.saves.getSnapshot().storageRevision }
  }, [])
  const flush = useCallback(async (): Promise<AuthoringPrecondition> => {
    const current = session.current
    if (!current || conflictRef.current) throw new Error(conflictRef.current ?? '请先打开工程。')
    if (current.history.getSnapshot().transactionActive) throw new Error('请先结束节点拖动。')
    do {
      await current.saves.flush()
      if (session.current !== current) throw new Error('工程会话已切换，请重新操作。')
      if (current.history.getSnapshot().transactionActive) throw new Error('请先结束节点拖动。')
    } while (current.saves.getSnapshot().dirty)
    return precondition()
  }, [precondition])
  const retry = useCallback(async () => {
    if (conflictRef.current) throw new Error(conflictRef.current)
    await session.current?.saves.retry()
  }, [])
  const history = session.current?.history.getSnapshot()
  const saves = session.current?.saves.getSnapshot()
  return {
    document: history?.value ?? null,
    dirty: !!saves?.dirty || !!history?.transactionActive,
    saving: saves?.saving ?? false,
    error: conflictRef.current ?? (saves?.error instanceof Error ? saves.error.message : saves?.error ? '自动保存失败，本地编辑已保留。' : null),
    canUndo: history?.canUndo ?? false, canRedo: history?.canRedo ?? false,
    undoLabel: history?.undoLabel, redoLabel: history?.redoLabel,
    ingest, edit, begin, end, travel, flush, retry, precondition, block, onSaved,
  }
}
