/** 多输出节点的章级收件体验；逐项确认只改变 Python 签发的候选映射。 */
import { useState } from 'react'
import { CanvasDialog } from './ConnectNodesDialog'
import type { HandoffBatchController } from '../use-handoff-batch'
import type { HandoffBatchPreviewEnvelope } from '../handoff-batch-contracts'
import type { ExternalHandoffReadiness } from '../contracts'
import { HandoffPrecheckFailure, ReadinessMessages } from '../HandoffContract'
import './handoff-batch.css'

interface Props {
  readonly controller: HandoffBatchController
  readonly disabled: boolean
  readonly canPickFiles: boolean
  readonly canPickDirectory: boolean
  readonly canReveal: boolean
  readonly checked: boolean
  readonly submitting: boolean
  readonly submitReason: string | null
  readonly readiness: ExternalHandoffReadiness | null
  readonly failure: ExternalHandoffReadiness | null
  readonly onReveal: () => void
  readonly onCopyPath: (path: string) => void
  readonly onSubmit: () => void
}

export function HandoffBatch(props: Props) {
  const { controller } = props
  const [overwritePorts, setOverwritePorts] = useState<ReadonlySet<string>>(new Set())
  const [page, setPage] = useState(0)
  const rows = controller.observation?.rows ?? []
  const pageCount = Math.max(1, Math.ceil(rows.length / 40))
  const currentPage = Math.min(page, pageCount - 1)
  const received = rows.filter((row) => row.collected || row.target_exists).length
  const conflicts = rows.filter((row) => row.collected && row.target_exists)
  const disabled = props.disabled || controller.busy || !controller.available
  const checkDisabled = disabled || !controller.observation?.complete || conflicts.some((row) => !overwritePorts.has(row.port_id))
  return <>
    <li className="handoff-step handoff-batch">
      <h4>按章交回处理好的文件</h4>
      <p>本章使用一套增强参数。可一次选择多个文件或一个目录，也可陆续补件。外部文件复制并保留原件；直接导出到本章收件目录的文件会在确认后原位收纳，不保留原名副本。</p>
      <p className="handoff-batch-count" role="status">{rows.length ? `已收 ${received}/${rows.length}` : '正在读取本章待交回清单'}</p>
      {controller.phase === 'selecting' && <p role="status">正在读取选择并准备匹配预览；尚未收件。</p>}
      <div className="handoff-check-actions">
        <button type="button" disabled={disabled || !props.canPickFiles} onClick={() => void controller.choose('open_files')}>选择本章多个文件</button>
        <button type="button" disabled={disabled || !props.canPickDirectory} onClick={() => void controller.choose('select_directory')}>选择本章文件目录</button>
        <button type="button" disabled={disabled || !props.canReveal} onClick={props.onReveal}>打开本章收件目录</button>
        <button type="button" disabled={disabled} onClick={() => void controller.choose('inbox')}>发现本章收件目录</button>
        <button type="button" disabled={disabled} onClick={() => void controller.refresh()}>刷新收件状态</button>
      </div>
      {!controller.available && <p role="alert">当前服务不支持章级收件，请同步更新桌面应用与服务。此节点不会退回逐叶提交。</p>}
      <p>目录只读取第一层。请按下方规范名导出；不匹配、重名或目标已存在时，必须在预览中逐项确认。</p>
      <ul className="handoff-batch-targets" aria-label="本章待交回清单">
        {rows.slice(currentPage * 40, currentPage * 40 + 40).map((row) => <li key={row.port_id}>
          <strong>{row.target_name}</strong>
          <span>{row.collected ? '已收件，待整章检查' : row.target_exists ? '目标已存在，待检查或已检查' : '待交回'}</span>
          <button type="button" onClick={() => props.onCopyPath(row.target_name)}>复制规范名</button>
          <details><summary>目标路径</summary><code>{row.target_path}</code><button type="button" onClick={() => props.onCopyPath(row.target_path)}>复制目标路径</button></details>
        </li>)}
      </ul>
      {pageCount > 1 && <div className="handoff-check-actions" aria-label="收件清单分页"><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页目标</button><span>{currentPage + 1}/{pageCount}</span><button type="button" disabled={currentPage + 1 === pageCount} onClick={() => setPage(currentPage + 1)}>下一页目标</button></div>}
      {controller.message && <p role="status">{controller.message}</p>}
      {controller.error && <p role="alert" className="handoff-import-error">{controller.error}</p>}
      {controller.rawError && <details><summary>高级 → 收件检查详情</summary><pre>{controller.rawError}</pre></details>}
      {controller.preview && <BatchPreview key={controller.preview.batch_id} preview={controller.preview} controller={controller} />}
    </li>
    <li className="handoff-step handoff-batch">
      <h4>齐全后整章检查，由你提交并继续</h4>
      <p>检查涵盖本章全部文件；缺件或错误会保留等待，不会登记部分输出。提交是独立的整章操作。</p>
      {conflicts.map((row) => <label className="handoff-batch-overwrite" key={row.port_id}>
        <input type="checkbox" disabled={disabled} checked={overwritePorts.has(row.port_id)} onChange={(event) => {
          const checked = event.target.checked
          setOverwritePorts((before) => { const next = new Set(before); if (checked) next.add(row.port_id); else next.delete(row.port_id); return next })
        }} />允许本次检查替换正式目标：{row.target_name}
      </label>)}
      <ReadinessMessages readiness={props.readiness} />
      {props.failure && <HandoffPrecheckFailure failure={props.failure} resolved={props.checked} />}
      <div className="handoff-check-actions">
        <button type="button" disabled={checkDisabled} onClick={() => {
          const allowed = conflicts.filter((row) => overwritePorts.has(row.port_id)).map((row) => row.port_id)
          setOverwritePorts(new Set())
          void controller.check(allowed)
        }}>
          {controller.phase === 'checking' ? '正在检查整章…' : '检查本章全部输出'}
        </button>
        <button className="button button--primary" type="button" disabled={props.submitReason !== null || controller.busy || !props.checked} onClick={props.onSubmit}>
          {props.submitting ? '正在提交整章…' : '提交本章并继续'}
        </button>
      </div>
      <p className="handoff-disabled-reason">{props.checked ? '整章检查通过，等待你提交。' : !controller.observation?.complete ? '请先收齐本章全部文件。' : conflicts.some((row) => !overwritePorts.has(row.port_id)) ? '请逐项确认正式目标覆盖，再检查。' : '请先检查本章全部输出。'}</p>
      {props.checked && props.submitReason && <p>{props.submitReason}</p>}
      <p>提交时仍会重新检查；文件变化后需要重新确认。不会自动继续，也不接管外部工具进度。</p>
    </li>
  </>
}

function BatchPreview({ preview, controller }: { readonly preview: HandoffBatchPreviewEnvelope; readonly controller: HandoffBatchController }) {
  const [mapping, setMapping] = useState<ReadonlyMap<string, string>>(() => new Map(preview.matches.map((match) => [match.port_id, match.candidate_handle ?? ''])))
  const [overwrite, setOverwrite] = useState<ReadonlySet<string>>(new Set())
  const [page, setPage] = useState(0)
  const pageCount = Math.max(1, Math.ceil(preview.rows.length / 20))
  const matches = new Map(preview.matches.map((match) => [match.port_id, match]))
  const selected = preview.rows.flatMap((row) => {
    const candidate = mapping.get(row.port_id)
    return candidate ? [{ port_id: row.port_id, candidate_handle: candidate, overwrite: overwrite.has(row.port_id) }] : []
  })
  const duplicate = new Set(selected.map((item) => item.candidate_handle)).size !== selected.length
  const needsOverwrite = selected.some((item) => preview.rows.some((row) => row.port_id === item.port_id && row.collected) && !item.overwrite)
  const copying = controller.phase === 'copying'
  return <CanvasDialog title="确认本章收件匹配" onClose={controller.cancel}>
    <p>来源 → 本章目标。本次接收已选中的 {selected.length} 个文件；外部文件复制保留原件，本章目录来件原位收纳后不保留原名。未选择的目标可稍后补件；取消不复制或移动。</p>
    <div className="handoff-batch-matches">
      {preview.rows.slice(page * 20, page * 20 + 20).map((row) => {
        const match = matches.get(row.port_id)
        return <div className="handoff-batch-match" key={row.port_id}>
          <label>来源文件 → {row.target_name}
            <select disabled={copying} value={mapping.get(row.port_id) ?? ''} onChange={(event) => {
              const value = event.target.value
              setMapping((before) => new Map(before).set(row.port_id, value))
              setOverwrite((before) => { const next = new Set(before); next.delete(row.port_id); return next })
            }}>
              <option value="">本次不收件（稍后补件）</option>
              {preview.candidates.map((candidate) => <option key={candidate.candidate_handle} value={candidate.candidate_handle}>{candidate.name} · {candidate.size.toLocaleString()} 字节 · {candidate.action === 'move' ? '原位收纳，不保留原名' : '复制，保留原件'}</option>)}
            </select>
          </label>
          <span>{match?.state === 'matched' ? 'Python 已按规范名匹配，请确认' : match?.state === 'ambiguous' ? '同名候选冲突，请明确选择' : '未自动匹配，请核对或稍后补件'}</span>
          <code>{row.target_path}</code>
          {row.collected && mapping.get(row.port_id) && <label className="handoff-batch-overwrite"><input type="checkbox" checked={overwrite.has(row.port_id)} disabled={copying} onChange={(event) => {
            const checked = event.target.checked
            setOverwrite((before) => { const next = new Set(before); if (checked) next.add(row.port_id); else next.delete(row.port_id); return next })
          }} />允许替换已收件：{row.target_name}</label>}
          {row.target_exists && <p>正式目标已存在；本次收件不改变它，整章检查前会单独询问覆盖。</p>}
        </div>
      })}
    </div>
    {pageCount > 1 && <div className="handoff-check-actions" aria-label="匹配预览分页"><button type="button" disabled={copying || page === 0} onClick={() => setPage(page - 1)}>上一页匹配</button><span>{page + 1}/{pageCount}</span><button type="button" disabled={copying || page + 1 === pageCount} onClick={() => setPage(page + 1)}>下一页匹配</button></div>}
    {duplicate && <p role="alert">同一个来源文件不能分配给多个目标。</p>}
    {needsOverwrite && <p>请逐项允许替换已有收件，或取消该项选择。</p>}
    <div className="handoff-check-actions">
      <button type="button" disabled={copying} onClick={controller.cancel}>取消，不复制</button>
      <button type="button" disabled={copying || !selected.length || duplicate || needsOverwrite} onClick={() => void controller.confirm(selected)}>{copying ? '正在收件…' : '确认收件'}</button>
    </div>
  </CanvasDialog>
}
