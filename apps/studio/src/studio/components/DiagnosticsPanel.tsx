/** 高级诊断只展示 Python/Graph 投影及定位动作，不创建第二套错误或状态 authority。 */

import type { StudioDiagnostic } from '../graph'
import type { StudioServiceError } from '../contracts'

export interface DiagnosticsPanelProps {
  readonly open: boolean
  readonly diagnostics: ReadonlyArray<StudioDiagnostic>
  readonly serviceError: StudioServiceError | null
  readonly hasOlderRuns: boolean
  readonly historyBusy: boolean
  readonly onToggle: () => void
  readonly onLocateNode: (nodeId: string) => void
  readonly onLocateEdge: (edgeId: string) => void
  readonly onLoadOlderRuns: () => void
}
export function DiagnosticsPanel({
  open,
  diagnostics,
  serviceError,
  hasOlderRuns,
  historyBusy,
  onToggle,
  onLocateNode,
  onLocateEdge,
  onLoadOlderRuns,
}: DiagnosticsPanelProps) {
  return (
    <section className={`bottom-drawer ${open ? 'is-open' : ''}`}>
      <button className="drawer-toggle" type="button" onClick={onToggle}>
        <span>Graph diagnostics</span><strong>{diagnostics.length + (serviceError ? 1 : 0)}</strong><i>{open ? '收起' : '展开'}</i>
      </button>
      {open && (
        <div className="diagnostic-list">
          {diagnostics.length === 0 && !serviceError ? <div className="diagnostic-empty">graph_valid · 可保存和运行</div> : diagnostics.map((diagnostic) => (
            <article className="diagnostic diagnostic--error" key={`${diagnostic.code}-${diagnostic.node_id ?? diagnostic.edge_id ?? 'graph'}`}>
              <span className="diagnostic-icon">×</span>
              <div><span className="diagnostic-code">{diagnostic.code}</span><strong>Graph Core</strong><p>{diagnostic.message}</p></div>
              <button type="button" onClick={() => diagnostic.node_id ? onLocateNode(diagnostic.node_id) : diagnostic.edge_id ? onLocateEdge(diagnostic.edge_id) : undefined}>定位</button>
            </article>
          ))}
          {serviceError && (
            <article className="diagnostic diagnostic--error">
              <span className="diagnostic-icon">×</span>
              <div><span className="diagnostic-code">{serviceError.code}</span><strong>Project Service</strong><p>{serviceError.message}</p></div>
            </article>
          )}
          {hasOlderRuns && <button className="button button--ghost" type="button" disabled={historyBusy} onClick={onLoadOlderRuns}>{historyBusy ? '读取历史…' : '加载更早 Run'}</button>}
        </div>
      )}
    </section>
  )
}
