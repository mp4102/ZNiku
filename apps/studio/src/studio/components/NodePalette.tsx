/** Palette 只展示严格 exact Presentation；缺失条目使用通用 Schema 降级，不推测业务分类。 */

import type { StudioCatalogGroup } from '../catalog'
import type { NodeDefinitionWire } from '../contracts'

export interface NodePaletteProps {
  readonly advanced?: boolean
  readonly definitionCount: number
  readonly projectId: string
  readonly projectName: string
  readonly query: string
  readonly groups: ReadonlyArray<StudioCatalogGroup>
  readonly canEditGraph: boolean
  readonly busy: boolean
  readonly selectedNodeCount: number
  readonly selectedEdgeCount: number
  readonly onProjectNameChange: (value: string) => void
  readonly onQueryChange: (value: string) => void
  readonly onAddDefinition: (definition: NodeDefinitionWire) => void
  readonly onCopySelection: () => void
  readonly onDeleteSelection: () => void
}
const iconText = {
  source: 'IN', media: 'MD', video: 'VD', audio: 'AU', transform: 'FX', split: 'SP',
  merge: 'MG', encode: 'EN', mux: 'MX', output: 'OUT', check: 'QC',
} as const

export function NodePalette({
  advanced = false,
  definitionCount,
  projectId,
  projectName,
  query,
  groups,
  canEditGraph,
  busy,
  selectedNodeCount,
  selectedEdgeCount,
  onProjectNameChange,
  onQueryChange,
  onAddDefinition,
  onCopySelection,
  onDeleteSelection,
}: NodePaletteProps) {
  return (
    <aside className="palette-panel" aria-label="添加与管理步骤" id="node-palette" tabIndex={-1}>
      <div className="panel-heading">
        <span className="eyebrow">{advanced ? 'NODE CATALOG' : '添加处理步骤'}</span><h2>节点面板</h2>
        <span className="registry-state"><i aria-hidden="true" /> {definitionCount} {advanced ? 'exact versions' : '种可用节点'}</span>
      </div>
      <details className="project-fields project-fields--advanced">
        <summary>高级工程信息</summary>
        <label>工程名称<input aria-label="Project name" value={projectName} onChange={(event) => onProjectNameChange(event.target.value)} /></label>
        <label>Project ID<code>{projectId || '由服务生成'}</code></label>
      </details>
      <label className="search-box">
        <span>⌕</span>
        <input aria-label="搜索节点" value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="名称、用途或端口" />
      </label>
      <div className="palette-list" aria-label="节点定义列表">
        {groups.map((group) => (
          <section className="palette-group" aria-label={group.label} key={group.id}>
            <header><span><strong>{group.label}</strong><small>{group.description}</small></span><em>{group.entries.length}</em></header>
            {group.entries.map(({ definition, presentation, role }) => (
              <button
                aria-label={advanced ? `${presentation?.title ?? definition.type_id} · ${definition.type_id}@${definition.version}` : presentation?.title ?? definition.type_id}
                className={`palette-item palette-item--${definition.executor.kind}`}
                type="button"
                key={`${definition.type_id}@${definition.version}`}
                disabled={!canEditGraph || busy}
                onClick={() => onAddDefinition(definition)}
              >
                <span className="palette-icon" aria-hidden="true">{presentation ? iconText[presentation.icon_token] : 'JS'}</span>
                <span>
                  <strong>{presentation?.title ?? definition.type_id}</strong>
                  <small>{presentation?.description ?? `通用 Schema · ${role}`}</small>
                </span>
                {advanced && <em>{presentation?.palette_level ?? 'advanced'}</em>}
              </button>
            ))}
          </section>
        ))}
        {groups.length === 0 && <p className="palette-empty">没有匹配节点，请更换名称或用途关键词。</p>}
      </div>
      <div className="selection-actions">
        <button className="button button--ghost" type="button" disabled={selectedNodeCount === 0 || busy || !canEditGraph} onClick={onCopySelection}>复制所选</button>
        <button className="button button--danger" type="button" disabled={(selectedNodeCount === 0 && selectedEdgeCount === 0) || busy || !canEditGraph} onClick={onDeleteSelection}>删除所选</button>
      </div>
      <div className="palette-note"><span>{advanced ? '自由 DAG' : '自由编排'}</span><p>{advanced ? '节点来自 Python definition 与独立 Presentation。Run snapshot 只读；切回当前 Graph 后才能编辑。' : '可以添加多份素材、自由连接分支与合并步骤。查看历史任务时不能编辑，请先返回当前工作流。'}</p></div>
    </aside>
  )
}
