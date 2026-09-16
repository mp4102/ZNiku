/** 统一目录来件和选择器结果：选择不搬文件，检查通过才收纳，提交始终显式。 */
import { useEffect, useState } from 'react'
import type { HandoffIntakeController } from '../use-handoff-intake'

export interface HandoffIntakeProps {
  readonly controller: HandoffIntakeController
  readonly disabled: boolean
  readonly canPick: boolean
  readonly canReveal: boolean
  readonly onReveal: () => void
  readonly onCopyPath: (path: string) => void
}
function sizeLabel(value: number): string {
  return value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GiB` : value >= 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MiB` : `${value.toLocaleString()} 字节`
}
export function HandoffIntake(props: HandoffIntakeProps) {
  const { controller } = props, { observation, selected, job } = controller
  const [overwrite, setOverwrite] = useState(false)
  useEffect(() => { setOverwrite(false) }, [selected?.ticket_id])
  const blocked = props.disabled || controller.busy || !controller.available
  const ready = (job?.phase === 'ready' && job.ready_id !== null) || controller.recoveredPath !== null
  return <>
    <li className="handoff-step">
      <h4>交回处理好的文件</h4>
      <p>任选一种方式：放入本任务交回目录后刷新，或直接选择原位置的文件。现在只选择，不复制、不重命名。</p>
      <div className="artifact-host-actions">
        <button type="button" disabled={blocked || !props.canReveal} onClick={props.onReveal}>打开交回目录</button>
        <button type="button" disabled={blocked} onClick={() => void controller.refresh()}>刷新文件列表</button>
        <button type="button" disabled={blocked || !props.canPick} onClick={() => void controller.choose()}>选择处理好的文件</button>
      </div>
      {observation && <p><code className="handoff-target-path">{observation.inbox_path}</code>
        <button type="button" onClick={() => props.onCopyPath(observation.inbox_path)}>复制交回目录</button></p>}
      {!controller.available && <p role="alert">当前服务尚未提供新版交回能力，请使用配套版本启动应用。</p>}
      {observation?.message && <p>{observation.message}</p>}
      {observation?.candidates.length === 0 && <p>目录中尚未发现可识别的视频；也可以直接选择处理好的文件。</p>}
      {observation && observation.candidates.length > 1 && <p>发现多个视频，请选择本任务的处理结果。系统不会猜测最新或最大的文件。</p>}
      {!!observation?.candidates.length && <ul className="handoff-inbox-candidates">
        {observation.candidates.map((candidate) => <li key={candidate.candidate_handle}>
          <strong>{candidate.name}</strong><span>{candidate.container.toUpperCase()} · {sizeLabel(candidate.size)}</span>
          <button type="button" disabled={blocked} aria-label={`选择来件：${candidate.name}`} onClick={() => void controller.choose(candidate)}>选择此文件</button>
        </li>)}
      </ul>}
      {!!observation?.rejected_count && <p>另有 {observation.rejected_count} 个条目无法作为视频候选；未移动或删除这些文件。</p>}
      {selected && <dl className="handoff-import-review">
        <div><dt>已选择</dt><dd>{selected.source_name} · {selected.container.toUpperCase()} · {sizeLabel(selected.source_size)}</dd></div>
        <div><dt>原位置</dt><dd><code>{selected.source_path}</code></dd></div>
        <div><dt>归档名称</dt><dd>{selected.archive_name}</dd></div>
        <div><dt>检查通过后保存到</dt><dd><code>{selected.incoming_path}</code></dd></div>
      </dl>}
      {selected && <p>{selected.action === 'copy' ? '检查通过后才复制到交回目录，外部原件保留。' : selected.action === 'rename' ? '文件已在交回目录，检查通过后按归档名称重命名，不复制第二份。' : '文件已在正确位置，检查通过后保留原位，不重复复制。'}</p>}
      <p>来件不要求预先改名；容器与媒体要求由检查确认。不要导入仍在写入的半成品。</p>
    </li>
    <li className="handoff-step">
      <h4>检查并导入，再由你提交</h4>
      <details><summary>刷新页面后找不到已导入的文件？</summary>
        <p>文件可能已转存到本任务正式输出目录。可直接重新检查，不需要再复制一份。</p>
        <button type="button" disabled={blocked || ready} onClick={() => void controller.checkPublished()}>检查已收纳输出</button>
      </details>
      {controller.recoveredPath && <p>已恢复的输出：<code className="handoff-target-path">{controller.recoveredPath}</code></p>}
      {selected?.replace_existing && <label><input type="checkbox" checked={overwrite} disabled={blocked || ready}
        onChange={(event) => setOverwrite(event.target.checked)} />允许替换本任务已有文件</label>}
      {controller.phase === 'selecting' && <p role="status">正在读取文件信息；没有复制或重命名。</p>}
      {controller.phase === 'checking' && <p role="status">正在原位置检查视频要求；尚未开始复制。</p>}
      {controller.phase === 'copying' && job && <div role="status"><p>检查通过，正在复制：{sizeLabel(job.bytes_done)} / {sizeLabel(job.total_bytes)}</p>
        <progress aria-label="文件复制进度" value={job.bytes_done} max={job.total_bytes} /></div>}
      {controller.message && <p role="status">{controller.message}</p>}
      {controller.error && <p role="alert">{controller.error}</p>}
      {controller.rawError && <details><summary>高级 → 交回原始详情</summary><pre>{controller.rawError}</pre></details>}
      <div className="handoff-check-actions">
        <button type="button" className="button button--ghost" disabled={blocked || !selected || ready || (selected.replace_existing && !overwrite)}
          onClick={() => void controller.check(overwrite)}>检查并导入</button>
        <button type="button" className="button button--primary" disabled={blocked || !ready}
          onClick={() => void controller.submit()}>{controller.phase === 'submitting' ? '正在提交并继续…' : '提交并继续'}</button>
      </div>
      {!selected && !controller.busy && !ready && <p>请先选择处理好的视频，再检查并导入。</p>}
      <p>检查失败不会开始复制；导入成功也不会自动提交。提交时再次核对文件与任务，成功后才继续后续处理。</p>
    </li>
  </>
}
