/** 显式确认源文件与唯一目标；复制成功仍不代表 Runtime Submit。 */
import { useState } from 'react'
import type { HandoffImportController } from '../use-handoff-import'
import { CanvasDialog } from './ConnectNodesDialog'

export function HandoffImportDialog({ controller }: { readonly controller: HandoffImportController }) {
  const [overwrite, setOverwrite] = useState(false)
  const preview = controller.preview
  if (!preview) return null
  return <CanvasDialog title="确认导入外部处理文件" onClose={controller.cancel}>
    <p>请核对下方源文件与任务。确认后只复制到此任务的目标位置，不会自动提交或开始下游处理。</p>
    <dl className="handoff-import-review">
      <div><dt>当前任务</dt><dd>{preview.nodeTitle}</dd></div>
      <div><dt>选中的文件</dt><dd>{preview.envelope.source_name}</dd></div>
      <div><dt>来源位置</dt><dd><code>{preview.sourcePath}</code></dd></div>
      <div><dt>文件大小</dt><dd>{preview.envelope.source_size.toLocaleString()} 字节</dd></div>
      <div><dt>将复制到</dt><dd><code>{preview.envelope.target_path}</code></dd></div>
      <div><dt>目标状态</dt><dd>{preview.envelope.replace_existing ? '此任务的目标文件已存在，需要明确允许替换。' : '此任务尚无目标文件，将新增。'}</dd></div>
    </dl>
    {preview.envelope.replace_existing && <label className="handoff-import-overwrite"><input type="checkbox" checked={overwrite} disabled={controller.copying} onChange={(event) => setOverwrite(event.target.checked)} />允许替换此任务已有目标文件</label>}
    {controller.copying && <p role="status">正在导入外部文件…请等待复制和验证完成。</p>}
    <div className="dialog-actions">
      <button type="button" disabled={controller.copying} onClick={controller.cancel}>取消</button>
      <button type="button" disabled={controller.copying || (preview.envelope.replace_existing && !overwrite)} onClick={() => void controller.confirm(overwrite)}>{controller.copying ? '正在复制与验证…' : '确认复制到此任务'}</button>
    </div>
  </CanvasDialog>
}
