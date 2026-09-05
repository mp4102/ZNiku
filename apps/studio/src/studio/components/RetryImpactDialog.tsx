/** 仅呈现 Python 给出的重跑/复用预检清单；不在前端计算下游或决定复用资格。 */
import type { RerunPreviewEnvelope } from '../contracts'
import { CanvasDialog } from './ConnectNodesDialog'

export interface RetryImpactDialogProps {
  readonly preview: RerunPreviewEnvelope | null
  readonly nodeLabel: (nodeId: string) => string
  readonly busy: boolean
  readonly error: string | null
  readonly onConfirm: () => void
  readonly onCancel: () => void
}
export function RetryImpactDialog({ preview, nodeLabel, busy, error, onConfirm, onCancel }: RetryImpactDialogProps) {
  const blocked = busy || !preview || !!error
  return <CanvasDialog title="确认重新处理的影响" onClose={onCancel}>
    <div className="retry-impact">
      <p>受影响的步骤会从头运行，不能接续中间进度。已有结果不会因为打开此窗口被删除。</p>
      {busy && <p role="status">{preview ? '正在启动重新处理，请稍候…' : '正在读取运行服务的重试影响，请稍候…'}</p>}
      {error && <div className="retry-impact-error" role="alert"><strong>暂时不能确认重试</strong><p>{error}</p><p>请取消并返回当前任务，恢复连接或刷新状态后重新查看影响。</p></div>}
      {preview && <>
        <section aria-label="将重新处理的步骤"><h3>将从头重新处理 · {preview.rerun_node_ids.length} 步</h3>
          {preview.rerun_node_ids.length ? <ol>{preview.rerun_node_ids.map((id) => <li key={id}>{nodeLabel(id)}</li>)}</ol> : <p>运行服务未列出需要重跑的步骤。</p>}</section>
        <section aria-label="可以复用的步骤"><h3>当前可复用的已完成结果 · {preview.reusable_node_ids.length} 步</h3>
          {preview.reusable_node_ids.length ? <ul>{preview.reusable_node_ids.map((id) => <li key={id}>{nodeLabel(id)}</li>)}</ul> : <p>当前没有确认可复用的步骤。</p>}</section>
        <p>{preview.mode === 'new_run' ? '将创建新的处理记录；旧记录仍保留。' : '将在当前处理记录内创建新的尝试；旧尝试仍保留。'}执行前仍会重新检查工程与结果；过期清单不能直接启动。</p>
      </>}
      {!preview && !busy && !error && <p role="status">尚未取得正式影响清单，请取消后重新查看重试影响。</p>}
      <div className="retry-impact-actions"><button type="button" onClick={onCancel}>取消</button><button className="primary-action" type="button" disabled={blocked} onClick={() => { if (!blocked) onConfirm() }}>确认从头重新处理</button></div>
    </div>
  </CanvasDialog>
}
