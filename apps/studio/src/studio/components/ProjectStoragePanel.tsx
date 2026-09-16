/** 工程数据维护只消费 Python 投影；换盘与复制迁移都是显式动作，不改变节点图或自动删除资产。 */
import { useEffect, useRef, useState } from 'react'
import { HostBridgeError, type HostBridge, type StorageIndexResult, type StorageInspection, type StorageMigrationPreview } from '../host-bridge'
import { formatHostBridgeError } from '../host-error-presentation'
import { CanvasDialog } from './ConnectNodesDialog'
import './ProjectStoragePanel.css'

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
  if (code === 'E_PROJECT_STORAGE_RESTORE_MISMATCH') return '所选目录不能与当前工程的已登记记录对应。请确认选中的是本工程已经搬迁的 .data 文件夹，不是父目录；源文件与当前工程未被覆盖。'
  if (code === 'E_PROJECT_STORAGE_RESTORE_UNPROVEN') return '无法安全确认本工程副本的归属与登记文件。原位置缺失时，副本必须带有本工程的数据归属标记；未建立绑定的旧工程请先恢复原位置，再执行“复制整理”。不会按文件夹名猜测或自动补造标记。'
  if (code === 'E_PROJECT_STORAGE_OVERLAP') return '新数据目录与当前位置不能相同或互相包含，也不能包含工程文件。请选择另一个独立的父文件夹；原件不会被移动。'
  if (code === 'E_PROJECT_STORAGE_INDEX_LOCATION') return '当前仍在旧版共享工作目录。请先预览并确认整理到本工程专属 .data 目录，再生成文件目录；不会写入其他工程的位置。'
  if (code === 'E_PROJECT_STORAGE_INDEX_TARGET') return '目录索引文件的位置已被其他文件占用，未覆盖原文件。请检查该文件并自行保留或改名后再生成。'
  if (code === 'E_PROJECT_STORAGE_TICKET' || code === 'E_PROJECT_STORAGE_CHANGED' || code === 'E_PROJECT_STORAGE_CONFLICT') return '工程或文件在预览后发生变化，请刷新检查并重新预览；不会自动使用旧清单重试。'
  return '本次工程数据操作未完成。原始文件不会被自动删除；迁移失败时目标可能留有副本，请查看技术详情后重试。'
}

export function ProjectStoragePanel({ bridge, projectSessionId, storageRevision, onChanged, onClose, onBusyChange }: ProjectStoragePanelProps) {
  const [inspection, setInspection] = useState<StorageInspection | null>(null)
  const [choice, setChoice] = useState<LocationChoice | null>(null)
  const [preview, setPreview] = useState<StorageMigrationPreview | null>(null)
  const [index, setIndex] = useState<StorageIndexResult | null>(null)
  const [busy, setBusy] = useState<string | null>('正在读取工程数据位置…')
  const [operationBusy, setOperationBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const busyRef = useRef(false)
  const previewElement = useRef<HTMLElement>(null)
  const indexElement = useRef<HTMLDivElement>(null)
  const generation = useRef(0)
  const callbacks = useRef({ onChanged, onBusyChange })
  callbacks.current = { onChanged, onBusyChange }
  const isBusy = !!busy || operationBusy

  // 只在明确请求完成后定位结果，长对话框不能把新的确认区留在视野外。
  useEffect(() => { if (preview) previewElement.current?.focus() }, [preview])
  useEffect(() => { if (index) indexElement.current?.focus() }, [index])

  useEffect(() => {
    const current = ++generation.current
    setInspection(null); setChoice(null); setPreview(null); setIndex(null); setError(null); setNotice(null)
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
    setOperationBusy(true)
    callbacks.current.onBusyChange?.(true)
    const current = generation.current
    setBusy(label); setError(null); setNotice(null)
    try { await action(current) } catch (failure) {
      if (generation.current === current) { setError(failure); setPreview(null) }
    } finally {
      busyRef.current = false
      // 即使会话已经切换，也要解除本组件的可见忙碌状态；不能只改 ref 而让按钮卡住。
      setOperationBusy(false)
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
  const confirm = () => perform(preview?.operation === 'restore' ? '正在重新核对登记文件；完成后才切换工程引用，请勿退出…' : '正在复制和逐文件比对；完成后才切换工程引用，请勿退出…', async (current) => {
    if (!preview || !bridge.confirmStorageMigration) return
    const result = await bridge.confirmStorageMigration({ project_session_id: projectSessionId, expected_storage_revision: storageRevision, ticket_id: preview.ticket_id })
    if (generation.current !== current) return
    setInspection(result); setPreview(null); setChoice(null); setIndex(null)
    setNotice(preview.operation === 'restore'
      ? '工程数据已重新定位。没有复制、删除或清理文件；工程外的素材与成品仍需按依赖清单核对。'
      : preview.operation === 'organize'
        ? '可读目录整理完成。工程现在引用新位置，旧位置的原件全部保留；节点图、参数和历史结果没有重跑。'
        : '工程数据迁移完成。工程现在引用新位置，旧位置的原件全部保留，请不要误认为已经自动清理。')
    callbacks.current.onChanged()
  })
  const chooseMaintenance = (operation: 'organize' | 'restore') => perform(
    operation === 'organize' ? '请选择可读数据目录的新父文件夹，然后预览整理清单…' : '请选择已经搬迁的本工程 .data 文件夹，然后核对引用…',
    async (current) => {
      const selections = await bridge.pick('select_directory', { title: operation === 'organize' ? '选择可读工程数据的新父文件夹' : '选择已经搬迁的工程 .data 文件夹' })
      if (generation.current !== current || !selections?.length) return
      setPreview(null); setChoice(null)
      const request = { project_session_id: projectSessionId, expected_storage_revision: storageRevision, selection_handle: selections[0]!.selection_handle }
      const handler = operation === 'organize' ? bridge.previewStorageOrganization : bridge.previewStorageRestore
      if (!handler) throw new Error('当前入口不支持此工程数据操作，请启动更新后的桌面入口。')
      const result = await handler.call(bridge, request)
      if (generation.current === current) setPreview(result)
    },
  )
  const generateIndex = () => perform('正在生成当前工程的文件目录…', async (current) => {
    if (!bridge.generateStorageIndex) throw new Error('当前入口不支持文件目录导出')
    const result = await bridge.generateStorageIndex({ project_session_id: projectSessionId, expected_storage_revision: storageRevision })
    if (generation.current === current) { setIndex(result); setNotice('文件目录已生成。它只用于浏览，运行、复用和失效仍以工程记录为准。') }
  })
  const revealIndex = () => perform('正在文件管理器中定位目录索引…', async (current) => {
    if (!index || generation.current !== current) return
    await bridge.launch('reveal_in_file_manager', { kind: 'storage_index', project_session_id: projectSessionId })
  })

  return <CanvasDialog title="工程数据与归档检查" onClose={() => { if (!busyRef.current) onClose() }}>
    <div className="retry-impact project-storage-panel" aria-busy={isBusy}>
      <p><strong>工程资产长期保留。</strong>中间产物、外部交付文件和历史执行结果不会因完成、退出或重跑自动删除。</p>
      {busy && <p role="status">{busy}</p>}
      {!busy && operationBusy && <p role="status">正在等待上一次工程数据操作结束，请先完成或取消已经打开的路径选择窗口。</p>}
      {notice && <p role="status">{notice}</p>}
      {error != null && <div role="alert"><p>{failureMessage(error)}</p><details><summary>技术详情</summary><pre>{error instanceof Error ? error.message : String(error)}</pre></details></div>}
      {inspection && <>
        <dl className="handoff-import-review">
          <div><dt>当前位置</dt><dd style={{ overflowWrap: 'anywhere' }}>{inspection.storage.data_root}</dd></div>
          {inspection.storage.attempts_root !== inspection.storage.data_root && <div><dt>工作产物目录</dt><dd style={{ overflowWrap: 'anywhere' }}>{inspection.storage.attempts_root}</dd></div>}
          <div><dt>位置类型</dt><dd>{inspection.storage.mode === 'legacy' ? '旧版工作目录：保留原位置，尚未迁移' : inspection.storage.mode === 'adjacent' ? '工程文件旁' : '自定义磁盘位置'}</dd></div>
          <div><dt>目录组织</dt><dd>{inspection.storage.layout === 'english'
            ? '英文目录：任务／章节／处理轮次'
            : inspection.storage.layout === 'readable'
              ? '可读目录：章节／工序、任务名与执行批次'
              : 'UUID 旧布局：保留原有绑定，不会自动整理'}</dd></div>
          <div><dt>已登记资产</dt><dd>{inspection.registered_file_count} 个文件 · {bytes(inspection.registered_bytes)}</dd></div>
          <div><dt>工程工作资产</dt><dd>{inspection.managed_file_count} 个文件 · {bytes(inspection.managed_bytes)} · {inspection.attempt_count} 次执行记录</dd></div>
        </dl>
        <p>上方大小只统计已登记文件，不是整个磁盘或数据文件夹的占用。迁移预览会另外统计该工程执行目录里的日志、收件和其他文件。</p>
        <section aria-label="可读目录与文件索引"><h3>找文件与整理目录</h3>
          <p>新英文目录按任务、可选章节和处理轮次组织，例如 enhancement/A/round-001。incoming 是外部结果交回区，outputs 是任务输出区，logs 是处理记录；文件所在位置不代表验收通过，外部结果仍需检查并明确提交。</p>
          <p>round-001 表示该任务第 1 次处理，跨运行连续计数；复用已有结果不会新建媒体副本。旧工程保留原目录，同名任务和重试仍各自隔离，打开工程不会自动改名。</p>
          <div className="dialog-actions">
            <button type="button" disabled={isBusy || !bridge.generateStorageIndex} onClick={() => void generateIndex()}>生成文件目录</button>
            <button type="button" disabled={isBusy || !bridge.previewStorageOrganization || !!inspection.missing.length} onClick={() => void chooseMaintenance('organize')}>选择新父目录并预览整理</button>
          </div>
          <p>整理会复制到所选父文件夹下新的同名 .data 目录；逐文件比对后才切换引用，原目录全部保留。请先备份 .zniku 工程，并完成或放弃运行和外部等待任务；不会直接重命名正在使用的目录。</p>
          {index && <div ref={indexElement} tabIndex={-1} role="region" aria-label="已生成的文件目录">
            <p style={{ overflowWrap: 'anywhere' }}>文件目录：{index.path}</p>
            <p>包含 {index.artifact_count} 条已登记资产；{index.external_dependency_count} 条工程外依赖需另外保留。目录索引不是完整归档或媒体验收证明。</p>
            {index.warnings.map((warning) => <p key={warning}>{warning}</p>)}
            <button type="button" disabled={isBusy} onClick={() => void revealIndex()}>在文件管理器中定位目录索引</button>
          </div>}
        </section>
        <section aria-label="归档依赖检查"><h3>归档前请核对</h3>
          {inspection.missing.length ? <><p role="alert">有 {inspection.missing.length} 个已登记文件缺失或不可读，不能迁移。</p><ul>{inspection.missing.map((item) => <li key={item.path} style={{ overflowWrap: 'anywhere' }}>{item.path}</li>)}</ul></> : <p>已登记文件未发现缺失。</p>}
          {inspection.external_dependencies.length ? <><p>以下 {inspection.external_dependencies.length} 个文件在工程工作目录外，需要另外保留；不会被本次迁移移动：</p><ul>{inspection.external_dependencies.map((item) => <li key={item.path} style={{ overflowWrap: 'anywhere' }}>{item.path}</li>)}</ul></> : <p>已登记文件未发现工程工作目录外的依赖。</p>}
          {inspection.warnings.map((warning) => <p key={warning}>{warning}</p>)}
        </section>
        <section aria-label="更改工程数据位置"><h3>仅更改位置，保留目录结构</h3>
          <p>只想换盘而不整理目录时使用这里；如需把旧目录整理为英文名称，请使用上方“预览整理”。整理不会重跑媒体、改动节点参数或自动清理历史。</p>
          <p>选择专用磁盘上的父文件夹，系统会在里面建立工程同名 .data 子目录。磁盘不可用时会明确报错，不会转存到 C 盘。</p>
          {inspection.attempt_count > 0 && <p>已有执行记录，必须先预览再明确确认复制迁移；请先完成或放弃所有运行和外部等待任务。原目录保留，不会删除。</p>}
          <div className="dialog-actions"><button type="button" disabled={isBusy} onClick={() => void choose()}>选择数据父目录</button><button type="button" disabled={isBusy} onClick={() => { setChoice({ selection_handle: null, label: '工程文件旁的默认 .data 目录' }); setPreview(null); setError(null) }}>恢复工程旁默认</button></div>
          {choice && <p style={{ overflowWrap: 'anywhere' }}>待设置位置：{choice.label}</p>}
          <button type="button" disabled={isBusy || !choice || !!inspection.missing.length} onClick={() => void prepare()}>{inspection.attempt_count > 0 ? '预览迁移到新位置' : '保存数据位置'}</button>
        </section>
        <details><summary>数据已经搬到另一位置？</summary>
          <section aria-label="重新定位已搬迁数据"><h3>重新定位已搬迁数据</h3>
            <p>只用于你已完整复制或搬迁的数据：选择本工程现有的 .data 文件夹本身，不是它的父目录。先核对登记文件和路径映射，确认后才更新工程引用。</p>
            <p>此操作不复制文件，不修复缺失内容，也不搬动工程外素材或成片；不能将检查通过解释为所有未登记文件都已归档。</p>
            <p>原位置缺失时，副本须保留本工程的数据归属标记。未建立绑定的旧工程应先恢复原位置，再使用上方“预览整理”复制到可读目录，不能只靠文件夹名重新定位。</p>
            <button type="button" disabled={isBusy || !bridge.previewStorageRestore} onClick={() => void chooseMaintenance('restore')}>选择已有 .data 目录并预览重新定位</button>
          </section>
        </details>
      </>}
      {preview && <section ref={previewElement} tabIndex={-1} aria-label="确认数据迁移"><h3>{preview.operation === 'restore' ? '确认重新定位' : preview.operation === 'organize' ? '确认整理为可读目录' : '确认复制迁移'}</h3>
        <p>{preview.operation === 'restore' ? '已核对' : '将复制'} {preview.attempt_count} 个执行目录中的 {preview.file_count} 个文件，共 {bytes(preview.byte_count)}。</p>
        <p style={{ overflowWrap: 'anywhere' }}>新位置：{preview.target.data_root}</p>
        {preview.target.layout === 'english' && <p>目标使用英文目录：任务／章节／处理轮次；媒体文件名保持原约定，不按目录名称推断节点身份。</p>}
        <p>{preview.operation === 'restore' ? '重新核对已登记记录后才更新引用，不复制、不删除。工程外的源素材和成品路径不自动变化；这不是对未登记文件的完整性证明。' : '逐文件复制与比对通过后才切换工程引用。全部原件保留，外部源素材和成品输出不移动。目标必须是新目录，已有同名目录请换一个父文件夹。'}</p>
        {preview.warnings?.map((warning) => <p key={warning}>{warning}</p>)}
        {!!preview.path_mappings?.length && <details><summary>查看目录对照（{preview.path_mappings.length} 项）</summary>
          {preview.path_mappings.length > 20 && <p>界面展示前 20 项；确认仍以服务端完整预览清单为准。</p>}
          <ol>{preview.path_mappings.slice(0, 20).map((mapping) => <li key={mapping.source} style={{ overflowWrap: 'anywhere' }}><div>原目录：{mapping.source}</div><div>新目录：{mapping.target}</div></li>)}</ol>
        </details>}
        <div className="dialog-actions"><button type="button" disabled={isBusy} onClick={() => setPreview(null)}>取消本次迁移</button><button type="button" disabled={isBusy} onClick={() => void confirm()}>{preview.operation === 'restore' ? '确认重新定位，不复制文件' : preview.operation === 'organize' ? '确认复制整理并保留原件' : '确认复制迁移并保留原件'}</button></div>
      </section>}
      <div className="dialog-actions"><button type="button" disabled={isBusy} onClick={() => void refresh()}>刷新归档检查</button><button type="button" disabled={operationBusy} onClick={onClose}>完成</button></div>
    </div>
  </CanvasDialog>
}
