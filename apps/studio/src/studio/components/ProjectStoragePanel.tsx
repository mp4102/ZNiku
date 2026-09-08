/** 工程数据维护只消费 Python 投影；换盘与复制迁移都是显式动作，不改变节点图或自动删除资产。 */
import { useEffect, useRef, useState } from 'react'
import { HostBridgeError, type HostBridge, type StorageInspection, type StorageMigrationPreview } from '../host-bridge'
import { formatHostBridgeError } from '../host-error-presentation'
import { CanvasDialog } from './ConnectNodesDialog'

export interface ProjectStoragePanelProps {
  readonly bridge: HostBridge
  readonly projectSessionId: string
  readonly storageRevision: number
  readonly onChanged: () => void
  readonly onClose: () => void
  readonly onBusyChange?: (busy: boolean) => void
}

interface LocationChoice {
  readonly selection_handle: string | null
  readonly label: string
}

function bytes(value: number): string {
  if (value < 1024) return `${value.toLocaleString()} 字节`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(2)} MiB`
  return `${(value / 1024 ** 3).toFixed(2)} GiB`
}

function failureMessage(error: unknown): string {
  const knownHost = formatHostBridgeError(error)
  if (knownHost) return knownHost
  const code = error instanceof HostBridgeError ? error.code : null
  if (code === 'E_PROJECT_STORAGE_TARGET_EXISTS') return '目标位置已有同名数据目录。请新建一个空的父文件夹后重新选择；不会覆盖或删除已有数据。'
  if (code === 'E_PROJECT_STORAGE_ACTIVE') return '请先完成或放弃所有运行和外部等待任务，再迁移工程数据。'
  if (code === 'E_PROJECT_STORAGE_MISSING') return '已登记的文件缺失或不可读，请先检查下方缺失清单并恢复文件。'
  if (code === 'E_PROJECT_STORAGE_TICKET' || code === 'E_PROJECT_STORAGE_CHANGED' || code === 'E_PROJECT_STORAGE_CONFLICT') return '工程或文件在预览后发生变化，请刷新检查并重新预览；不会自动使用旧清单重试。'
  return '本次工程数据操作未完成。原始文件不会被自动删除；迁移失败时目标可能留有副本，请查看技术详情后重试。'
}

export function ProjectStoragePanel({ bridge, projectSessionId, storageRevision, onChanged, onClose, onBusyChange }: ProjectStoragePanelProps) {
  const [inspection, setInspection] = useState<StorageInspection | null>(null)
  const [choice, setChoice] = useState<LocationChoice | null>(null)
  const [preview, setPreview] = useState<StorageMigrationPreview | null>(null)
  const [busy, setBusy] = useState<string | null>('正在读取工程数据位置…')
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const busyRef = useRef(false)
  const generation = useRef(0)
  const callbacks = useRef({ onChanged, onBusyChange })
  callbacks.current = { onChanged, onBusyChange }

  useEffect(() => {
    const current = ++generation.current
    setInspection(null); setChoice(null); setPreview(null); setError(null); setNotice(null)
    setBusy('正在读取工程数据位置…')
    if (!bridge.inspectStorage) {
      setError(new Error('当前桌面入口不支持工程数据管理，请退出旧应用并启动新包。'))
      setBusy(null)
      return
    }
    void bridge.inspectStorage(projectSessionId).then((value) => {
      if (generation.current === current) setInspection(value)
    }).catch((failure: unknown) => {
      if (generation.current === current) setError(failure)
    }).finally(() => {
      if (generation.current === current) setBusy(null)
    })
    return () => { generation.current++; callbacks.current.onBusyChange?.(false) }
  }, [bridge, projectSessionId, storageRevision])

  const perform = async (label: string, action: (current: number) => Promise<void>) => {
    if (busyRef.current) return
    busyRef.current = true
    callbacks.current.onBusyChange?.(true)
    const current = generation.current
    setBusy(label); setError(null); setNotice(null)
    try { await action(current) } catch (failure) {
      if (generation.current === current) { setError(failure); setPreview(null) }
    } finally {
      busyRef.current = false
      callbacks.current.onBusyChange?.(false)
      if (generation.current === current) setBusy(null)
    }
  }
  const refresh = () => perform('正在检查已登记的工程资产…', async (current) => {
    if (!bridge.inspectStorage) throw new Error('当前入口不支持工程数据检查')
    const result = await bridge.inspectStorage(projectSessionId)
    if (generation.current === current) { setInspection(result); setPreview(null) }
  })
  const choose = () => perform('请选择工程数据的父文件夹…', async (current) => {
    const selections = await bridge.pick('select_directory', { title: '选择工程数据父文件夹' })
    if (generation.current !== current || !selections?.length) return
    setChoice({ selection_handle: selections[0]!.selection_handle, label: selections[0]!.path })
    setPreview(null)
  })
  const prepare = () => perform(inspection?.attempt_count ? '正在预览工程数据迁移…' : '正在保存工程数据位置…', async (current) => {
    if (!choice || !inspection) return
    const request = { project_session_id: projectSessionId, expected_storage_revision: storageRevision, selection_handle: choice.selection_handle }
    if (inspection.attempt_count > 0) {
      if (!bridge.previewStorageMigration) throw new Error('当前入口不支持迁移预览')
      const result = await bridge.previewStorageMigration(request)
      if (generation.current === current) setPreview(result)
    } else {
      if (!bridge.configureStorage) throw new Error('当前入口不支持存储配置')
      const result = await bridge.configureStorage(request)
      if (generation.current !== current) return
      setInspection(result); setChoice(null); setNotice('新的工程数据位置已保存；节点图和参数没有改动。')
      callbacks.current.onChanged()
    }
  })
  const confirm = () => perform('正在复制和逐文件比对；完成后才切换工程引用，请勿退出…', async (current) => {
    if (!preview || !bridge.confirmStorageMigration) return
    const result = await bridge.confirmStorageMigration({ project_session_id: projectSessionId, expected_storage_revision: storageRevision, ticket_id: preview.ticket_id })
    if (generation.current !== current) return
    setInspection(result); setPreview(null); setChoice(null)
    setNotice('工程数据迁移完成。工程现在引用新位置，旧位置的原件全部保留，请不要误认为已经自动清理。')
    callbacks.current.onChanged()
  })

  return <CanvasDialog title="工程数据与归档检查" onClose={() => { if (!busyRef.current) onClose() }}>
    <div className="retry-impact" aria-busy={!!busy}>
      <p><strong>工程资产长期保留。</strong>中间产物、外部交付文件和历史执行结果不会因完成、退出或重跑自动删除。</p>
      {busy && <p role="status">{busy}</p>}
      {notice && <p role="status">{notice}</p>}
      {error != null && <div role="alert"><p>{failureMessage(error)}</p><details><summary>技术详情</summary><pre>{error instanceof Error ? error.message : String(error)}</pre></details></div>}
      {inspection && <>
        <dl className="handoff-import-review">
          <div><dt>当前位置</dt><dd style={{ overflowWrap: 'anywhere' }}>{inspection.storage.data_root}</dd></div>
          <div><dt>工作产物目录</dt><dd style={{ overflowWrap: 'anywhere' }}>{inspection.storage.attempts_root}</dd></div>
          <div><dt>位置类型</dt><dd>{inspection.storage.mode === 'legacy' ? '旧版工作目录：保留原位置，尚未迁移' : inspection.storage.mode === 'adjacent' ? '工程文件旁' : '自定义磁盘位置'}</dd></div>
          <div><dt>已登记资产</dt><dd>{inspection.registered_file_count} 个文件 · {bytes(inspection.registered_bytes)}</dd></div>
          <div><dt>工程工作资产</dt><dd>{inspection.managed_file_count} 个文件 · {bytes(inspection.managed_bytes)} · {inspection.attempt_count} 次执行记录</dd></div>
        </dl>
        <p>上方大小只统计已登记文件，不是整个磁盘或数据文件夹的占用。迁移预览会另外统计该工程执行目录里的日志、收件和其他文件。</p>
        <section aria-label="归档依赖检查"><h3>归档前请核对</h3>
          {inspection.missing.length ? <><p role="alert">有 {inspection.missing.length} 个已登记文件缺失或不可读，不能迁移。</p><ul>{inspection.missing.map((item) => <li key={item.path} style={{ overflowWrap: 'anywhere' }}>{item.path}</li>)}</ul></> : <p>已登记文件未发现缺失。</p>}
          {inspection.external_dependencies.length ? <><p>以下 {inspection.external_dependencies.length} 个文件在工程工作目录外，需要另外保留；不会被本次迁移移动：</p><ul>{inspection.external_dependencies.map((item) => <li key={item.path} style={{ overflowWrap: 'anywhere' }}>{item.path}</li>)}</ul></> : <p>已登记文件未发现工程工作目录外的依赖。</p>}
          {inspection.warnings.map((warning) => <p key={warning}>{warning}</p>)}
        </section>
        <section aria-label="更改工程数据位置"><h3>更改工程数据位置</h3>
          <p>选择专用磁盘上的父文件夹，系统会在里面建立工程同名 .data 子目录。磁盘不可用时会明确报错，不会转存到 C 盘。</p>
          {inspection.attempt_count > 0 && <p>已有执行记录，必须先预览再明确确认复制迁移；请先完成或放弃所有运行和外部等待任务。原目录保留，不会删除。</p>}
          <div className="dialog-actions"><button type="button" disabled={!!busy} onClick={() => void choose()}>选择数据父目录</button><button type="button" disabled={!!busy} onClick={() => { setChoice({ selection_handle: null, label: '工程文件旁的默认 .data 目录' }); setPreview(null); setError(null) }}>恢复工程旁默认</button></div>
          {choice && <p style={{ overflowWrap: 'anywhere' }}>待设置位置：{choice.label}</p>}
          <button type="button" disabled={!!busy || !choice || !!inspection.missing.length} onClick={() => void prepare()}>{inspection.attempt_count > 0 ? '预览迁移到新位置' : '保存数据位置'}</button>
        </section>
      </>}
      {preview && <section aria-label="确认数据迁移"><h3>确认复制迁移</h3>
        <p>将复制 {preview.attempt_count} 个执行目录中的 {preview.file_count} 个文件，共 {bytes(preview.byte_count)}。</p>
        <p style={{ overflowWrap: 'anywhere' }}>新位置：{preview.target.data_root}</p>
        <p>逐文件复制与比对通过后才切换工程引用。全部原件保留，外部源素材和成品输出不移动。目标必须是新目录，已有同名目录请换一个父文件夹。</p>
        <div className="dialog-actions"><button type="button" disabled={!!busy} onClick={() => setPreview(null)}>取消本次迁移</button><button type="button" disabled={!!busy} onClick={() => void confirm()}>确认复制迁移并保留原件</button></div>
      </section>}
      <div className="dialog-actions"><button type="button" disabled={!!busy} onClick={() => void refresh()}>刷新归档检查</button><button type="button" disabled={busyRef.current} onClick={onClose}>完成</button></div>
    </div>
  </CanvasDialog>
}
