/** Palette 只展示严格 exact Presentation；缺失条目使用通用 Schema 降级，不推测业务分类。 */

import type { StudioCatalogGroup } from '../catalog'
import type { NodeDefinitionWire } from '../contracts'

export interface NodePaletteProps {
  readonly definitionCount: number
  readonly projectId: string
  readonly projectName: string
  readonly query: string
  readonly groups: ReadonlyArray<StudioCatalogGroup>
  readonly canEditGraph: boolean
  readonly busy: boolean
  readonly selectedNodeCount: number
  readonly selectedEdgeCount: number
  readonly onProjectIdChange: (value: string) => void
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
  definitionCount,
  projectId,
  projectName,
  query,
  groups,
  canEditGraph,
  busy,
  selectedNodeCount,
  selectedEdgeCount,
  onProjectIdChange,
  onProjectNameChange,
  onQueryChange,
  onAddDefinition,
  onCopySelection,
  onDeleteSelection,
}: NodePaletteProps) {
  return (
    <aside className="palette-panel">
      <div className="panel-heading">
        <span className="eyebrow">NODE CATALOG</span><h2>节点面板</h2>
        <span className="registry-state"><i /> {definitionCount} exact versions</span>
      </div>
      <div className="project-fields">
        <label>Project ID<input aria-label="Project ID" value={projectId} onChange={(event) => onProjectIdChange(event.target.value)} /></label>
        <label>Project name<input aria-label="Project name" value={projectName} onChange={(event) => onProjectNameChange(event.target.value)} /></label>
      </div>
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
                aria-label={`${presentation?.title ?? definition.type_id} · ${definition.type_id}@${definition.version}`}
                className={`palette-item palette-item--${definition.executor.kind}`}
                type="button"
                key={`${definition.type_id}@${definition.version}`}
                disabled={!canEditGraph || busy}
                onClick={() => onAddDefinition(definition)}
              >
                <span className="palette-icon">{presentation ? iconText[presentation.icon_token] : 'JS'}</span>
                <span>
                  <strong>{presentation?.title ?? definition.type_id}</strong>
                  <small>{presentation?.description ?? `通用 Schema · ${role}`}</small>
                </span>
                <em>{presentation?.palette_level ?? 'advanced'}</em>
              </button>
            ))}
          </section>
        ))}
        {groups.length === 0 && <p className="palette-empty">没有匹配的 exact definition。</p>}
      </div>
      <div className="selection-actions">
        <button className="button button--ghost" type="button" disabled={selectedNodeCount === 0 || busy || !canEditGraph} onClick={onCopySelection}>复制所选</button>
        <button className="button button--danger" type="button" disabled={(selectedNodeCount === 0 && selectedEdgeCount === 0) || busy || !canEditGraph} onClick={onDeleteSelection}>删除所选</button>
      </div>
      <div className="palette-note"><span>自由 DAG</span><p>节点来自 Python definition 与独立 Presentation。Run snapshot 只读；切回当前 Graph 后才能编辑。</p></div>
    </aside>
  )
}
