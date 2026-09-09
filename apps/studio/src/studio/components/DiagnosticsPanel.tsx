/** 问题卡只翻译权威错误并提供定位；保留高级原文，不据文案改变任何运行资格。 */
import type { StudioDiagnostic } from '../graph'
import type { StudioServiceError } from '../contracts'
import { failurePresentation } from '../run-presentation'

export interface RuntimeProblem {
  readonly code: string
  readonly message: string
  readonly node_id?: string | null
  readonly edge_id?: string | null
}
export interface DiagnosticsPanelProps {
  readonly embedded?: boolean
  readonly open: boolean
  readonly diagnostics: ReadonlyArray<StudioDiagnostic>
  readonly serviceError: StudioServiceError | null
  readonly hasOlderRuns: boolean
  readonly historyBusy: boolean
  readonly onToggle: () => void
  readonly onLocateNode: (nodeId: string) => void
  readonly onLocateEdge: (edgeId: string) => void
  readonly onLoadOlderRuns: () => void
  readonly advanced?: boolean
  readonly nodeLabel?: (nodeId: string) => string
  readonly runtimeProblems?: ReadonlyArray<RuntimeProblem>
  readonly onRecoverService?: () => void
  readonly onLocateRuntimeNode?: (nodeId: string) => void
}
export function DiagnosticsPanel({ open, diagnostics, serviceError, hasOlderRuns, historyBusy, onToggle, embedded = false,
  onLocateNode, onLocateEdge, onLoadOlderRuns, advanced = false, nodeLabel,
  runtimeProblems = [], onRecoverService, onLocateRuntimeNode }: DiagnosticsPanelProps) {
  const problems: ReadonlyArray<RuntimeProblem & { readonly origin: string; readonly service?: boolean }> = [
    ...diagnostics.map((item) => ({ ...item, origin: 'Graph Core' })),
    ...runtimeProblems.map((item) => ({ ...item, origin: 'Runtime' })),
    ...(serviceError ? [{ ...serviceError, origin: 'Project Service', service: true }] : []),
  ]
  return <section className={`${embedded ? 'task-problems' : 'bottom-drawer'} creator-problems ${open ? 'is-open' : ''}`} aria-label="问题与恢复">
    {!embedded && <button className="drawer-toggle" type="button" onClick={onToggle} aria-expanded={open}>
      <span>{advanced ? 'Graph diagnostics' : '问题与恢复'}</span><strong>{problems.length}</strong><i>{open ? '收起' : '展开'}</i>
    </button>}
    {open && <div className="diagnostic-list">
      {problems.length === 0 && <div className="diagnostic-empty">目前没有需要处理的问题。</div>}
      {problems.map((problem, index) => {
        const explanation = failurePresentation(problem.code)
        return <article className="problem-card" key={`${problem.origin}-${problem.code}-${problem.node_id ?? problem.edge_id ?? 'project'}-${index}`}>
          {embedded && <p className="problem-context">{problem.origin === 'Graph Core' ? '当前编辑的工作流问题' : problem.origin === 'Runtime' ? '被查看的处理记录问题' : '工程服务问题'}</p>}
          <header><strong>{explanation.title}</strong>{problem.node_id && <span>{nodeLabel?.(problem.node_id) ?? '相关步骤'}</span>}</header>
          <dl><dt>发生了什么</dt><dd>{explanation.cause}</dd>
            <dt>保留的内容</dt><dd>{explanation.preserved}</dd>
            <dt>下一步</dt><dd>{explanation.recovery}</dd></dl>
          <div className="problem-actions">
            {problem.node_id ? <button type="button" onClick={() => (problem.origin === 'Runtime' ? onLocateRuntimeNode ?? onLocateNode : onLocateNode)(problem.node_id!)}>定位步骤与设置</button>
              : problem.edge_id ? <button type="button" onClick={() => onLocateEdge(problem.edge_id!)}>定位连接</button>
                : <span>此问题影响当前工程，请按上面的建议处理。</span>}
            {problem.service && onRecoverService && <button type="button" onClick={onRecoverService}>重新读取状态</button>}
          </div>
          <details className="problem-advanced" open={advanced || undefined}><summary>高级详情{!explanation.known ? '（保留原始错误）' : ''}</summary>
            <p>{problem.origin} · <code>{problem.code}</code></p><pre>{problem.message}</pre>
            {problem.node_id && <p>Node ID：<code>{problem.node_id}</code></p>}
            {problem.edge_id && <p>Edge ID：<code>{problem.edge_id}</code></p>}
          </details>
        </article>
      })}
      {!embedded && hasOlderRuns && <div className="problem-history-action"><button className="button button--ghost" type="button" disabled={historyBusy} onClick={onLoadOlderRuns}>{historyBusy ? '读取历史…' : advanced ? '加载更早 Run' : '加载更早处理记录'}</button>
        {historyBusy && <p role="status">正在读取更早记录，请等待完成。</p>}</div>}
    </div>}
  </section>
}
