/** 只有显式预览和确认可维护未登记中转；不后台扫描，不接收任意删除路径，不自动重放副作用。 */
import { useEffect, useRef, useState } from 'react'
import type { HostBridge } from '../host-bridge'
import { assertScratchBinding, type ScratchCategory, type ScratchConfirmEnvelope, type ScratchPreviewEnvelope } from '../storage-scratch-contracts'

interface Props {
  readonly bridge: HostBridge
  readonly projectSessionId: string
  readonly storageRevision: number
  readonly disabled: boolean
  readonly perform: (label: string, action: (generation: number) => Promise<void>) => Promise<void>
}
const categoryLabels: Record<ScratchCategory, string> = {
  archive: '归档成果（保留）', registered_recreatable: '已登记的可重建媒体（保留）',
  internal_scratch: '内部计算中转候选', unknown: '未知或无法确认（保留）',
}
function size(value: number): string {
  return value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(2)} MiB` : `${(value / 1024 ** 3).toFixed(2)} GiB`
}

export function ProjectScratchMaintenance({ bridge, projectSessionId, storageRevision, disabled, perform }: Props) {
  const [preview, setPreview] = useState<ScratchPreviewEnvelope | null>(null)
  const [result, setResult] = useState<ScratchConfirmEnvelope | null>(null)
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [irreversible, setIrreversible] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(0)
  const generation = useRef(0)
  useEffect(() => {
    generation.current++
    setPreview(null); setResult(null); setSelected(new Set()); setIrreversible(false); setError(null); setPage(0)
    return () => { generation.current++ }
  }, [projectSessionId, storageRevision])
  useEffect(() => {
    if (!preview) return
    const timer = window.setTimeout(() => {
      setPreview(null); setSelected(new Set()); setIrreversible(false)
      setError('维护预览已过期，请重新扫描；没有自动删除任何文件。')
    }, preview.expires_in_seconds * 1000)
    return () => window.clearTimeout(timer)
  }, [preview])
  const request = { contract_version: '0.3.0' as const, project_session_id: projectSessionId, expected_storage_revision: storageRevision }
  const scan = () => perform('正在请求扫描工程内部中转，请等待服务响应…', async () => {
    const current = generation.current
    setPreview(null); setResult(null); setSelected(new Set()); setIrreversible(false); setError(null)
    try {
      if (!bridge.previewStorageScratch) throw new Error('当前入口不支持内部中转维护。')
      const value = await bridge.previewStorageScratch(request)
      if (generation.current !== current) return
      assertScratchBinding(request, value)
      setPreview(value); setPage(0)
    } catch (failure) {
      if (generation.current === current) setError(`未能生成安全维护清单；没有清理文件。${failure instanceof Error ? failure.message : String(failure)}`)
    }
  })
  const candidates = preview?.entries.filter((entry) => entry.candidate_id !== null && entry.category === 'internal_scratch') ?? []
  const chosen = candidates.filter((entry) => selected.has(entry.candidate_id!))
  const confirm = () => {
    if (!preview || !chosen.length || !irreversible) return
    const ticket = preview.ticket_id
    const ids = chosen.map((entry) => entry.candidate_id!)
    return perform('正在请求清理已确认的内部中转；结果返回前请勿重复操作…', async () => {
      const current = generation.current
      // 票据仅消费一次；网络结果未知时也不保留一个可再次发送的确认按钮。
      setPreview(null); setSelected(new Set()); setIrreversible(false); setError(null)
      try {
        if (!bridge.confirmStorageScratch) throw new Error('当前入口不支持内部中转清理。')
        const value = await bridge.confirmStorageScratch({ ...request, ticket_id: ticket, candidate_ids: ids, confirm_irreversible: true })
        if (generation.current !== current) return
        assertScratchBinding(request, value)
        if (value.ticket_id !== ticket || value.entries.length !== ids.length ||
          new Set(value.entries.map((entry) => entry.candidate_id)).size !== ids.length ||
          value.entries.some((entry) => !ids.includes(entry.candidate_id))) throw new Error('清理回执与本次确认清单不一致。')
        setResult(value)
      } catch (failure) {
        if (generation.current === current) setError(`清理结果待确认，请勿重复删除。可能已有部分项目完成；先重新扫描核对，不会自动重试。${failure instanceof Error ? failure.message : String(failure)}`)
      }
    })
  }
  const pages = Math.max(1, Math.ceil((preview?.entries.length ?? 0) / 40))
  return <section className="project-scratch-maintenance" aria-label="内部中转维护">
    <h3>查看占用与维护内部中转</h3>
    <p>原片、外部增强与补帧成果、已登记媒体、日志和成片都保留。只有服务端证明安全的内部计算中转才可由你选择清理；不会因关闭、完成或重跑自动删除。</p>
    <button type="button" disabled={disabled || !bridge.previewStorageScratch} onClick={() => void scan()}>扫描占用并预览内部中转</button>
    <p>仅在没有运行或等待交付任务时可维护；扫描不读取媒体内容。大小为文件逻辑占用，不代表 NAS 实际释放空间。</p>
    {error && <p role="alert">{error}</p>}
    {preview && <>
      <dl>{preview.summary.map((item) => <div key={item.category}><dt>{categoryLabels[item.category]}</dt><dd>{item.file_count} 个文件 · {size(item.byte_count)}</dd></div>)}</dl>
      {preview.warnings.map((warning) => <p key={warning}>{warning}</p>)}
      {preview.truncated && <p>本次清单达到展示或扫描上限；未列出的项目没有获准清理。</p>}
      <ul aria-label="工程数据角色与保留原因">{preview.entries.slice(page * 40, page * 40 + 40).map((entry) => <li key={entry.path}>
        <strong>{entry.task_label}{entry.chapter_label ? ` · ${entry.chapter_label} 章` : ''} · {entry.round_label}</strong>
        <p>{entry.role} · {size(entry.byte_count)} · {categoryLabels[entry.category]}</p>
        <p>{entry.reason}</p>
        {entry.candidate_id !== null && entry.category === 'internal_scratch' && <label><input type="checkbox" disabled={disabled} checked={selected.has(entry.candidate_id)}
          onChange={(event) => { const id = entry.candidate_id!; const checked = event.target.checked; setSelected((before) => { const next = new Set(before); if (checked) next.add(id); else next.delete(id); return next }); setIrreversible(false) }} />选择此内部中转</label>}
        <details><summary>查看文件位置</summary><code style={{ overflowWrap: 'anywhere' }}>{entry.path}</code></details>
      </li>)}</ul>
      {pages > 1 && <div className="dialog-actions"><button type="button" disabled={page === 0 || disabled} onClick={() => setPage(page - 1)}>上一页文件</button><span>{page + 1}/{pages}</span><button type="button" disabled={page + 1 >= pages || disabled} onClick={() => setPage(page + 1)}>下一页文件</button></div>}
      <p>已选择 {chosen.length} 项，待清理逻辑大小 {size(chosen.reduce((sum, entry) => sum + entry.byte_count, 0))}。默认不选择任何文件。</p>
      {chosen.length > 0 && <label><input type="checkbox" disabled={disabled} checked={irreversible} onChange={(event) => setIrreversible(event.target.checked)} />我确认只删除所选内部中转，删除后不可恢复；不重算、不清理正式成果。</label>}
      <div className="dialog-actions"><button type="button" disabled={disabled} onClick={() => { setPreview(null); setSelected(new Set()); setIrreversible(false) }}>取消本次清理</button>
        <button className="button button--danger" type="button" disabled={disabled || !chosen.length || !irreversible || !bridge.confirmStorageScratch} onClick={() => void confirm()}>确认删除所选中转</button></div>
    </>}
    {result && <div role="status">
      <p>{result.complete ? '本次清理已处理完毕' : '本次清理有跳过或失败项'}：成功删除 {result.deletion_count} 项，逻辑大小 {size(result.deleted_bytes)}。NAS 快照可能仍占空间。</p>
      <ul>{result.entries.map((entry) => <li key={entry.candidate_id}>{entry.status === 'deleted' ? '已删除' : entry.status === 'skipped' ? '已跳过' : '删除失败'} · {size(entry.byte_count)} · {entry.message}<details><summary>查看结果文件位置</summary><code style={{ overflowWrap: 'anywhere' }}>{entry.path}</code></details></li>)}</ul>
      {result.warnings.map((warning) => <p key={warning}>{warning}</p>)}
      <p>工程、历史记录和正式成果未由清理操作改写。其他文件保留；需要再次维护时请重新扫描并确认。</p>
    </div>}
  </section>
}
