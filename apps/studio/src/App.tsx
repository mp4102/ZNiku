import { useCallback, useMemo, useRef, useState } from 'react'
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  type Connection,
  type NodeMouseHandler,
} from '@xyflow/react'
import { FreezeDialog } from './components/FreezeDialog'
import { AudioLaneEdge } from './components/AudioLaneEdge'
import { WorkflowNodeCard } from './components/WorkflowNodeCard'
import {
  compiledDiagnostics,
  draftDiagnostics,
  getRunNodes,
  initialDesignerEdges,
  initialDesignerNodes,
  paletteItems,
  planEdges,
  planNodes,
} from './mock-data'
import type {
  Diagnostic,
  PaletteItem,
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
  WorkspaceMode,
} from './model'

const nodeTypes = { workflow: WorkflowNodeCard }
const edgeTypes = { audioLane: AudioLaneEdge }

const modeLabels: Record<WorkspaceMode, string> = {
  designer: 'Designer',
  plan: 'Expanded Plan',
  run: 'Run Monitor',
}

function AppContent() {
  const [mode, setMode] = useState<WorkspaceMode>('designer')
  const [designerNodes, setDesignerNodes, onDesignerNodesChange] = useNodesState(initialDesignerNodes)
  const [designerEdges, setDesignerEdges, onDesignerEdgesChange] = useEdgesState(initialDesignerEdges)
  const [selectedNode, setSelectedNode] = useState<WorkflowNode | null>(
    initialDesignerNodes.find((item) => item.id === 'enhance') ?? null,
  )
  const [compiled, setCompiled] = useState(false)
  const [frozen, setFrozen] = useState(false)
  const [showFreeze, setShowFreeze] = useState(false)
  const [runStep, setRunStep] = useState(0)
  const [bottomOpen, setBottomOpen] = useState(true)
  const [query, setQuery] = useState('')
  const addedNodeCount = useRef(0)

  const runNodes = useMemo(() => getRunNodes(runStep), [runStep])
  const activeNodes = mode === 'designer' ? designerNodes : mode === 'plan' ? planNodes : runNodes
  const activeEdges = mode === 'designer' ? designerEdges : planEdges
  const diagnostics: Diagnostic[] = compiled ? compiledDiagnostics : draftDiagnostics

  const filteredPalette = paletteItems.filter((item) =>
    `${item.label} ${item.description}`.toLowerCase().includes(query.toLowerCase()),
  )

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (mode !== 'designer' || frozen || connection.source === connection.target) return
      setDesignerEdges((edges) => addEdge({ ...connection, type: 'smoothstep' }, edges))
      setCompiled(false)
    },
    [frozen, mode, setDesignerEdges],
  )

  const handleNodeClick: NodeMouseHandler<WorkflowNode> = useCallback((_event, clickedNode) => {
    setSelectedNode(clickedNode)
  }, [])

  const addPaletteNode = (item: PaletteItem) => {
    if (frozen) return
    addedNodeCount.current += 1
    const id = `draft-${item.id}-${addedNodeCount.current}`
    const data: WorkflowNodeData = {
      label: item.label,
      protocolId: `mock.${item.id}`,
      subtitle: 'New draft node',
      category: item.category,
      icon: item.icon,
      scope: item.scope,
      description: item.description,
      inputs: item.inputs.map((port) => ({ ...port })),
      outputs: item.outputs.map((port) => ({ ...port })),
      execution: item.category === 'engine' ? 'manual external' : 'runtime operator',
      mediaChanges: [{ label: '媒体合同', value: '待配置', tone: 'warning' }],
    }
    const newNode: WorkflowNode = {
      id,
      type: 'workflow',
      position: { x: 780 + addedNodeCount.current * 28, y: 620 + addedNodeCount.current * 20 },
      data,
    }
    setDesignerNodes((nodes) => [...nodes, newNode])
    setSelectedNode(newNode)
    setCompiled(false)
    setMode('designer')
  }

  const compilePreview = () => {
    setCompiled(true)
    setMode('plan')
    setSelectedNode(planNodes[0])
  }

  const confirmFreeze = () => {
    setShowFreeze(false)
    setFrozen(true)
    setMode('run')
    setSelectedNode(getRunNodes(0)[2])
  }

  const advanceRun = () => {
    const next = Math.min(runStep + 1, 3)
    setRunStep(next)
    setSelectedNode(getRunNodes(next).find((item) => item.data.runStatus !== 'complete') ?? getRunNodes(next).at(-1)!)
  }

  return (
    <main className={`app-shell ${bottomOpen ? 'has-bottom-drawer' : ''}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">ZN</div>
          <div>
            <span className="brand-name">ZNIKU</span>
            <span className="brand-subtitle">Studio</span>
          </div>
        </div>

        <div className="workflow-identity">
          <span className="eyebrow">WORKFLOW</span>
          <strong>Chapter A Decensor · Demo</strong>
          <span className="identity-meta">draft-004 · local mock</span>
        </div>

        <nav className="mode-tabs" aria-label="工作区视图">
          {(Object.keys(modeLabels) as WorkspaceMode[]).map((item) => (
            <button
              key={item}
              type="button"
              className={mode === item ? 'is-active' : ''}
              onClick={() => setMode(item)}
              disabled={item === 'run' && !frozen}
            >
              {modeLabels[item]}
            </button>
          ))}
        </nav>

        <div className="top-actions">
          <span className="mock-badge">MOCK · NO MEDIA I/O</span>
          {mode === 'run' ? (
            <button className="button button--primary" type="button" onClick={advanceRun} disabled={runStep >= 3}>
              {runStep >= 3 ? '模拟运行已完成' : '推进模拟状态'}
            </button>
          ) : (
            <>
              <button className="button button--ghost" type="button" onClick={compilePreview}>
                {compiled ? '重新编译预览' : '编译预览'}
              </button>
              <button
                className="button button--primary"
                type="button"
                onClick={() => setShowFreeze(true)}
                disabled={!compiled || frozen}
              >
                {frozen ? 'Revision 已冻结' : '审阅并冻结'}
              </button>
            </>
          )}
        </div>
      </header>

      <aside className="palette-panel">
        <div className="panel-heading">
          <span className="eyebrow">REGISTRY</span>
          <h2>节点面板</h2>
          <span className="registry-state"><i /> {paletteItems.length} mock entries</span>
        </div>

        <label className="search-box">
          <span>⌕</span>
          <input
            aria-label="搜索节点"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 Engine 或 Operator"
          />
        </label>

        <div className="palette-list">
          {filteredPalette.map((item) => (
            <button
              className={`palette-item palette-item--${item.category}`}
              type="button"
              key={item.id}
              onClick={() => addPaletteNode(item)}
              disabled={frozen}
            >
              <span className="palette-icon">{item.icon}</span>
              <span>
                <strong>{item.label}</strong>
                <small>{item.description}</small>
              </span>
              <em>{item.scope}</em>
            </button>
          ))}
        </div>

        <div className="palette-note">
          <span>Prototype boundary</span>
          <p>节点清单来自内存中的 mock Registry，不代表已冻结 EngineManifest。</p>
        </div>
      </aside>

      <section className="canvas-panel" aria-label={`${modeLabels[mode]} 画布`}>
        <div className="canvas-context">
          <div>
            <span className="context-mode">{modeLabels[mode]}</span>
            <strong>
              {mode === 'designer' && (frozen ? 'Revision 只读视图' : '电影级编排 DAG')}
              {mode === 'plan' && 'Compiler 展开的章节执行图'}
              {mode === 'run' && `Mock run · step ${runStep + 1}/4`}
            </strong>
          </div>
          <div className="canvas-legend">
            <span><i className="legend-dot source" /> Source</span>
            <span><i className="legend-dot operator" /> Graph</span>
            <span><i className="legend-dot engine" /> Engine</span>
            <span><i className="legend-dot final" /> Final</span>
            <span><i className="legend-line audio" /> Audio lane</span>
          </div>
        </div>

        <ReactFlow
          key={mode}
          nodes={activeNodes}
          edges={activeEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodesChange={mode === 'designer' && !frozen ? onDesignerNodesChange : undefined}
          onEdgesChange={mode === 'designer' && !frozen ? onDesignerEdgesChange : undefined}
          onConnect={handleConnect}
          onNodeClick={handleNodeClick}
          nodesDraggable={mode === 'designer' && !frozen}
          nodesConnectable={mode === 'designer' && !frozen}
          edgesReconnectable={mode === 'designer' && !frozen}
          deleteKeyCode={mode === 'designer' && !frozen ? ['Backspace', 'Delete'] : null}
          defaultViewport={
            mode === 'designer'
              ? { x: 42, y: 0, zoom: 0.72 }
              : { x: 28, y: 35, zoom: 0.58 }
          }
          minZoom={0.2}
          maxZoom={1.8}
          colorMode="dark"
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.1} color="#263344" />
          <Controls position="bottom-left" showInteractive={false} />
          <MiniMap
            position="bottom-right"
            pannable
            zoomable
            nodeColor={(item) => {
              const category = (item.data as WorkflowNodeData).category
              return { source: '#53a5c9', operator: '#8d7dc7', engine: '#d89b45', final: '#5fb98a' }[category]
            }}
          />
        </ReactFlow>
      </section>

      <aside className="inspector-panel">
        <div className="panel-heading inspector-heading">
          <span className="eyebrow">INSPECTOR</span>
          <h2>{selectedNode?.data.label ?? '未选择节点'}</h2>
          {selectedNode && <code>{selectedNode.data.protocolId}</code>}
        </div>

        {selectedNode ? (
          <div className="inspector-content">
            <section>
              <h3>节点摘要</h3>
              <p>{selectedNode.data.description}</p>
              <dl className="property-list">
                <div><dt>Category</dt><dd>{selectedNode.data.category}</dd></div>
                <div><dt>Scope</dt><dd>{selectedNode.data.scope}</dd></div>
                <div><dt>Execution</dt><dd>{selectedNode.data.execution ?? 'n/a'}</dd></div>
                {selectedNode.data.engineVersion && (
                  <div><dt>Version</dt><dd>{selectedNode.data.engineVersion}</dd></div>
                )}
              </dl>
            </section>

            <section>
              <h3>Typed ports</h3>
              <div className="port-group">
                <span className="port-group-label"><i className="port-dot input" /> inputs</span>
                {selectedNode.data.inputs.length > 0 ? (
                  selectedNode.data.inputs.map((port) => (
                    <div className="port-summary" key={`input-${port.id}`}>
                      <span>{port.label}</span>
                      <code>{port.artifactType} · {port.cardinality}</code>
                    </div>
                  ))
                ) : (
                  <div className="port-empty">none</div>
                )}
              </div>
              <div className="port-group">
                <span className="port-group-label"><i className="port-dot output" /> outputs</span>
                {selectedNode.data.outputs.length > 0 ? (
                  selectedNode.data.outputs.map((port) => (
                    <div className="port-summary" key={`output-${port.id}`}>
                      <span>{port.label}</span>
                      <code>{port.artifactType} · {port.cardinality}</code>
                    </div>
                  ))
                ) : (
                  <div className="port-empty">none</div>
                )}
              </div>
            </section>

            <section>
              <h3>媒体变化</h3>
              <div className="media-change-list">
                {selectedNode.data.mediaChanges.map((change) => (
                  <div className={`media-change media-change--${change.tone ?? 'neutral'}`} key={`${change.label}-${change.value}`}>
                    <span>{change.label}</span>
                    <strong>{change.value}</strong>
                  </div>
                ))}
              </div>
            </section>

            {selectedNode.data.runStatus && (
              <section>
                <h3>Runtime snapshot</h3>
                <div className={`runtime-status runtime-status--${selectedNode.data.runStatus}`}>
                  {selectedNode.data.runStatus.replace('_', ' ')}
                </div>
                <p className="muted">状态来自模拟事件序列，不是正式 authority。</p>
              </section>
            )}
          </div>
        ) : (
          <div className="empty-inspector">在画布中选择一个节点查看属性。</div>
        )}
      </aside>

      <section className={`bottom-drawer ${bottomOpen ? 'is-open' : ''}`}>
        <button className="drawer-toggle" type="button" onClick={() => setBottomOpen((open) => !open)}>
          <span>Diagnostics</span>
          <strong>{diagnostics.length}</strong>
          <i>{bottomOpen ? '收起' : '展开'}</i>
        </button>
        {bottomOpen && (
          <div className="diagnostic-list">
            {diagnostics.map((diagnostic) => (
              <article className={`diagnostic diagnostic--${diagnostic.severity}`} key={diagnostic.code}>
                <span className="diagnostic-icon">
                  {diagnostic.severity === 'warning' ? '!' : diagnostic.severity === 'error' ? '×' : 'i'}
                </span>
                <div>
                  <span className="diagnostic-code">{diagnostic.code}</span>
                  <strong>{diagnostic.title}</strong>
                  <p>{diagnostic.message}</p>
                </div>
                {diagnostic.entity && <button type="button">定位 {diagnostic.entity}</button>}
              </article>
            ))}
          </div>
        )}
      </section>

      {showFreeze && <FreezeDialog onCancel={() => setShowFreeze(false)} onConfirm={confirmFreeze} />}
    </main>
  )
}

export function App() {
  return (
    <ReactFlowProvider>
      <AppContent />
    </ReactFlowProvider>
  )
}
