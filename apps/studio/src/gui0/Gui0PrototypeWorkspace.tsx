import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type EdgeChange,
  type EdgeMouseHandler,
  type NodeChange,
  type NodeMouseHandler,
} from '@xyflow/react'
import { AudioLaneEdge } from '../components/AudioLaneEdge'
import { FreezeDialog } from '../components/FreezeDialog'
import { WorkflowNodeCard } from '../components/WorkflowNodeCard'
import {
  compiledDiagnostics,
  draftDiagnostics,
  getRunNodes,
  initialDesignerEdges,
  initialDesignerNodes,
  paletteItems,
  planEdges,
} from '../mock-data'
import type {
  Diagnostic,
  PaletteItem,
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
  WorkspaceMode,
} from '../model'

const nodeTypes = { workflow: WorkflowNodeCard }
const edgeTypes = { audioLane: AudioLaneEdge }

const modeLabels: Record<WorkspaceMode, string> = {
  designer: 'Designer',
  plan: 'Compile Preview',
  run: 'Mock Run Monitor',
}

interface Gui0PrototypeWorkspaceProps {
  readonly onExit: () => void
}

/**
 * GUI-0 只在浏览器内验证交互模型。端口 type 与 scope 必须精确一致，cardinality
 * 按正式合同兼容矩阵判断；未知 handle 或任何已占用输入均拒绝连接。此判断不是正式 Compiler。
 */
export function isGui0ConnectionValid(
  connection: Connection | WorkflowEdge,
  nodes: ReadonlyArray<WorkflowNode>,
  edges: ReadonlyArray<WorkflowEdge>,
): boolean {
  if (
    !connection.source ||
    !connection.target ||
    !connection.sourceHandle ||
    !connection.targetHandle ||
    connection.source === connection.target
  ) {
    return false
  }

  const sourceNode = nodes.find((node) => node.id === connection.source)
  const targetNode = nodes.find((node) => node.id === connection.target)
  const output = sourceNode?.data.outputs.find((port) => port.id === connection.sourceHandle)
  const input = targetNode?.data.inputs.find((port) => port.id === connection.targetHandle)
  if (!output || !input) return false

  const targetIsOccupied = edges.some(
    (edge) => edge.target === connection.target && edge.targetHandle === connection.targetHandle,
  )
  const cardinalityIsCompatible =
    (output.cardinality === 'one' &&
      (input.cardinality === 'one' || input.cardinality === 'optional')) ||
    (output.cardinality === 'optional' && input.cardinality === 'optional') ||
    (output.cardinality === 'set' && input.cardinality === 'set')

  return (
    !targetIsOccupied &&
    output.artifactType === input.artifactType &&
    output.scope === input.scope &&
    cardinalityIsCompatible
  )
}

/**
 * 仅为 GUI-0 的浏览器内预览执行最小 fail-closed 图检查。它证明当前 mock Draft 自洽，
 * 但不替代 Python Workflow Compiler，也不生成正式 ExecutionPlan。
 */
export function validateGui0Graph(
  nodes: ReadonlyArray<WorkflowNode>,
  edges: ReadonlyArray<WorkflowEdge>,
): string | null {
  const nodesById = new Map(nodes.map((node) => [node.id, node]))
  if (nodesById.size !== nodes.length) return '存在重复 node ID。'
  if (nodes.filter((node) => node.data.category === 'source').length !== 1) {
    return 'GUI-0 Draft 必须恰好包含一个 Source。'
  }
  if (nodes.filter((node) => node.data.category === 'final').length !== 1) {
    return 'GUI-0 Draft 必须恰好包含一个 Final。'
  }

  const occupiedInputs = new Set<string>()
  const indegree = new Map(nodes.map((node) => [node.id, 0]))
  const outgoing = new Map(nodes.map((node) => [node.id, new Array<string>()]))

  for (const edge of edges) {
    const source = nodesById.get(edge.source)
    const target = nodesById.get(edge.target)
    if (!source || !target || edge.source === edge.target) return `Edge ${edge.id} 的端点非法。`
    if (!edge.sourceHandle || !edge.targetHandle) return `Edge ${edge.id} 缺少稳定 port ID。`

    const output = source.data.outputs.find((port) => port.id === edge.sourceHandle)
    const input = target.data.inputs.find((port) => port.id === edge.targetHandle)
    if (!output || !input) return `Edge ${edge.id} 引用了未知 port。`
    const cardinalityIsCompatible =
      (output.cardinality === 'one' &&
        (input.cardinality === 'one' || input.cardinality === 'optional')) ||
      (output.cardinality === 'optional' && input.cardinality === 'optional') ||
      (output.cardinality === 'set' && input.cardinality === 'set')
    if (
      output.artifactType !== input.artifactType ||
      output.scope !== input.scope ||
      !cardinalityIsCompatible
    ) {
      return `Edge ${edge.id} 的 typed port 不兼容。`
    }

    const inputKey = `${edge.target}|${edge.targetHandle}`
    if (occupiedInputs.has(inputKey)) return `输入 ${inputKey} 存在多条入边。`
    occupiedInputs.add(inputKey)
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1)
    outgoing.get(edge.source)?.push(edge.target)
  }

  for (const node of nodes) {
    for (const input of node.data.inputs) {
      if (input.cardinality !== 'optional' && !occupiedInputs.has(`${node.id}|${input.id}`)) {
        return `必需输入 ${node.id}.${input.id} 尚未连接。`
      }
    }
    if (node.data.category === 'final' && (outgoing.get(node.id)?.length ?? 0) !== 0) {
      return 'Final 不得存在输出边。'
    }
  }

  const queue = nodes.filter((node) => indegree.get(node.id) === 0).map((node) => node.id)
  let visited = 0
  for (let index = 0; index < queue.length; index += 1) {
    const nodeId = queue[index]
    visited += 1
    for (const targetId of outgoing.get(nodeId) ?? []) {
      const next = (indegree.get(targetId) ?? 0) - 1
      indegree.set(targetId, next)
      if (next === 0) queue.push(targetId)
    }
  }
  return visited === nodes.length ? null : 'GUI-0 Draft 必须保持有向无环。'
}

/**
 * 可运行的 GUI-0 原型工作区。所有 Registry、Compiler、Freeze 与 Run 数据均为内存 mock，
 * 不调用 Engine、不执行媒体 I/O，也绝不冒充 Python authority。
 */
export function Gui0PrototypeWorkspace({ onExit }: Gui0PrototypeWorkspaceProps) {
  const [mode, setMode] = useState<WorkspaceMode>('designer')
  const [designerNodes, setDesignerNodes, onDesignerNodesChange] = useNodesState(
    initialDesignerNodes,
  )
  const [designerEdges, setDesignerEdges, onDesignerEdgesChange] = useEdgesState(
    initialDesignerEdges,
  )
  const [selectedNode, setSelectedNode] = useState<WorkflowNode | null>(
    initialDesignerNodes.find((item) => item.id === 'enhance') ?? null,
  )
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [compiled, setCompiled] = useState(false)
  const [compiledGraph, setCompiledGraph] = useState<{
    readonly nodes: WorkflowNode[]
    readonly edges: WorkflowEdge[]
  } | null>(null)
  const [compileIssue, setCompileIssue] = useState<string | null>(null)
  const [frozen, setFrozen] = useState(false)
  const [showFreeze, setShowFreeze] = useState(false)
  const [runStep, setRunStep] = useState(0)
  const [bottomOpen, setBottomOpen] = useState(true)
  const [query, setQuery] = useState('')
  const addedNodeCount = useRef(0)

  const runNodes = useMemo(() => getRunNodes(runStep), [runStep])
  const activeNodes =
    mode === 'designer'
      ? designerNodes
      : mode === 'plan'
        ? compiledGraph?.nodes ?? []
        : runNodes
  const activeEdges =
    mode === 'designer'
      ? designerEdges
      : mode === 'plan'
        ? compiledGraph?.edges ?? []
        : planEdges
  const diagnostics: ReadonlyArray<Diagnostic> = compileIssue
    ? [
        ...draftDiagnostics,
        {
          code: 'MOCK_COMPILE_BLOCKED',
          severity: 'error',
          title: '模拟编译已失败关闭',
          message: compileIssue,
        },
      ]
    : compiled
      ? compiledDiagnostics
      : draftDiagnostics

  const filteredPalette = paletteItems.filter((item) =>
    `${item.label} ${item.description}`.toLowerCase().includes(query.toLowerCase()),
  )

  const connectionIsValid = useCallback(
    (connection: Connection | WorkflowEdge) =>
      isGui0ConnectionValid(connection, designerNodes, designerEdges),
    [designerEdges, designerNodes],
  )

  const invalidateCompilation = useCallback(() => {
    setCompiled(false)
    setCompiledGraph(null)
    setCompileIssue(null)
  }, [])

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (mode !== 'designer' || frozen || !connectionIsValid(connection)) return
      setDesignerEdges((edges) =>
        addEdge({ ...connection, type: 'smoothstep', animated: false }, edges),
      )
      invalidateCompilation()
    },
    [connectionIsValid, frozen, invalidateCompilation, mode, setDesignerEdges],
  )

  const handleDesignerNodesChange = useCallback(
    (changes: NodeChange<WorkflowNode>[]) => {
      onDesignerNodesChange(changes)
      const removedIds = new Set(
        changes.filter((change) => change.type === 'remove').map((change) => change.id),
      )
      if (removedIds.size > 0) {
        if (selectedNode && removedIds.has(selectedNode.id)) setSelectedNode(null)
        invalidateCompilation()
      }
    },
    [invalidateCompilation, onDesignerNodesChange, selectedNode],
  )

  const handleDesignerEdgesChange = useCallback(
    (changes: EdgeChange<WorkflowEdge>[]) => {
      onDesignerEdgesChange(changes)
      const removedIds = new Set(
        changes.filter((change) => change.type === 'remove').map((change) => change.id),
      )
      if (selectedEdgeId && removedIds.has(selectedEdgeId)) setSelectedEdgeId(null)
      if (changes.some((change) => change.type !== 'select')) invalidateCompilation()
    },
    [invalidateCompilation, onDesignerEdgesChange, selectedEdgeId],
  )

  const handleNodeClick: NodeMouseHandler<WorkflowNode> = useCallback((_event, clickedNode) => {
    setSelectedNode(clickedNode)
    setSelectedEdgeId(null)
  }, [])

  const handleEdgeClick: EdgeMouseHandler<WorkflowEdge> = useCallback((_event, clickedEdge) => {
    setSelectedNode(null)
    setSelectedEdgeId(clickedEdge.id)
  }, [])

  const selectedEdge = designerEdges.find((edge) => edge.id === selectedEdgeId) ?? null

  const addPaletteNode = (item: PaletteItem) => {
    if (frozen) return
    addedNodeCount.current += 1
    const id = `draft-${item.id}-${addedNodeCount.current}`
    const data: WorkflowNodeData = {
      label: item.label,
      protocolId: `mock.${item.id}`,
      subtitle: 'New GUI-0 draft node',
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
      position: {
        x: 780 + addedNodeCount.current * 28,
        y: 620 + addedNodeCount.current * 20,
      },
      data,
    }
    setDesignerNodes((nodes) => [...nodes, newNode])
    setSelectedNode(newNode)
    setSelectedEdgeId(null)
    invalidateCompilation()
    setMode('designer')
  }

  const deleteSelectedNode = useCallback(() => {
    if (!selectedNode || frozen || mode !== 'designer') return
    const deletedId = selectedNode.id
    setDesignerNodes((nodes) => nodes.filter((node) => node.id !== deletedId))
    setDesignerEdges((edges) =>
      edges.filter((edge) => edge.source !== deletedId && edge.target !== deletedId),
    )
    setSelectedNode(null)
    setSelectedEdgeId(null)
    invalidateCompilation()
  }, [
    frozen,
    invalidateCompilation,
    mode,
    selectedNode,
    setDesignerEdges,
    setDesignerNodes,
  ])

  const deleteSelectedEdge = useCallback(() => {
    if (!selectedEdge || frozen || mode !== 'designer') return
    setDesignerEdges((edges) => edges.filter((edge) => edge.id !== selectedEdge.id))
    setSelectedEdgeId(null)
    invalidateCompilation()
  }, [frozen, invalidateCompilation, mode, selectedEdge, setDesignerEdges])

  useEffect(() => {
    const handleDelete = (event: KeyboardEvent) => {
      const target = event.target
      const isEditing =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      if (
        !isEditing &&
        (event.key === 'Delete' || event.key === 'Backspace') &&
        (selectedNode || selectedEdge) &&
        mode === 'designer' &&
        !frozen
      ) {
        event.preventDefault()
        if (selectedNode) deleteSelectedNode()
        else deleteSelectedEdge()
      }
    }
    window.addEventListener('keydown', handleDelete)
    return () => window.removeEventListener('keydown', handleDelete)
  }, [deleteSelectedEdge, deleteSelectedNode, frozen, mode, selectedEdge, selectedNode])

  const compilePreview = () => {
    const issue = validateGui0Graph(designerNodes, designerEdges)
    if (issue) {
      setCompiled(false)
      setCompiledGraph(null)
      setCompileIssue(issue)
      setMode('designer')
      return
    }
    setCompileIssue(null)
    setCompiledGraph({
      nodes: designerNodes.map((node) => ({
        ...node,
        position: { ...node.position },
        data: { ...node.data },
      })),
      edges: designerEdges.map((edge) => ({ ...edge })),
    })
    setCompiled(true)
    setMode('plan')
    setSelectedNode(designerNodes[0] ?? null)
    setSelectedEdgeId(null)
  }

  const confirmFreeze = () => {
    if (!compiled || !compiledGraph) return
    setShowFreeze(false)
    setFrozen(true)
    setMode('run')
    setSelectedNode(getRunNodes(0)[2] ?? null)
    setSelectedEdgeId(null)
  }

  const advanceRun = () => {
    const next = Math.min(runStep + 1, 3)
    const nextNodes = getRunNodes(next)
    setRunStep(next)
    setSelectedNode(
      nextNodes.find((item) => item.data.runStatus !== 'complete') ?? nextNodes.at(-1) ?? null,
    )
  }

  return (
    <main className={`app-shell gui0-workspace ${bottomOpen ? 'has-bottom-drawer' : ''}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">ZN</div>
          <div>
            <span className="brand-name">ZNIKU</span>
            <span className="brand-subtitle">GUI-0 Prototype</span>
          </div>
        </div>

        <div className="workflow-identity">
          <span className="eyebrow">GUI-0 PROTOTYPE WORKFLOW</span>
          <strong>自由节点编排 · Chapter A Decensor Demo</strong>
          <span className="identity-meta">browser memory only · draft-gui0</span>
        </div>

        <nav className="mode-tabs" aria-label="GUI-0 工作区视图">
          {(Object.keys(modeLabels) as WorkspaceMode[]).map((item) => (
            <button
              key={item}
              type="button"
              className={mode === item ? 'is-active' : ''}
              onClick={() => setMode(item)}
              disabled={(item === 'plan' && !compiled) || (item === 'run' && !frozen)}
            >
              {modeLabels[item]}
            </button>
          ))}
          <button type="button" onClick={onExit}>返回正式工作区</button>
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
          <span className="eyebrow">MOCK REGISTRY</span>
          <h2>节点面板</h2>
          <span className="registry-state"><i /> {paletteItems.length} mock entries</span>
        </div>

        <label className="search-box">
          <span>⌕</span>
          <input
            aria-label="搜索 GUI-0 节点"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 Engine 或 Operator"
          />
        </label>

        <div className="palette-list" aria-label="GUI-0 mock Registry">
          {filteredPalette.map((item) => (
            <button
              className={`palette-item palette-item--${item.category}`}
              type="button"
              key={item.id}
              onClick={() => addPaletteNode(item)}
              disabled={frozen}
            >
              <span className="palette-icon">{item.icon}</span>
              <span><strong>{item.label}</strong><small>{item.description}</small></span>
              <em>{item.scope}</em>
            </button>
          ))}
        </div>

        <div className="palette-note gui0-boundary-note">
          <span>MOCK · NO MEDIA I/O</span>
          <p>点击 Registry 添加节点；拖动节点；从彩色 typed handle 连线；Delete 或 Inspector 可删除。</p>
          <p>这里不会调用正式 Compiler、Runtime 或 Engine，也不会写入媒体。</p>
        </div>
      </aside>

      <section className="canvas-panel" aria-label={`${modeLabels[mode]} 画布`}>
        <div className="canvas-context">
          <div>
            <span className="context-mode">{modeLabels[mode]}</span>
            <strong>
              {mode === 'designer' && (frozen ? 'Revision 只读视图' : '自由编排 · drag · typed connect · delete')}
              {mode === 'plan' && compiledGraph && `只读 mock compile snapshot · ${compiledGraph.nodes.length} nodes · ${compiledGraph.edges.length} edges`}
              {mode === 'run' && `mock Run Monitor · step ${runStep + 1}/4`}
            </strong>
            {compileIssue && <span className="gui0-compile-error" role="alert">{compileIssue}</span>}
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
          onNodesChange={mode === 'designer' && !frozen ? handleDesignerNodesChange : undefined}
          onEdgesChange={mode === 'designer' && !frozen ? handleDesignerEdgesChange : undefined}
          onConnect={handleConnect}
          isValidConnection={connectionIsValid}
          onNodeClick={handleNodeClick}
          onEdgeClick={handleEdgeClick}
          nodesDraggable={mode === 'designer' && !frozen}
          nodesConnectable={mode === 'designer' && !frozen}
          edgesReconnectable={false}
          deleteKeyCode={null}
          defaultViewport={mode === 'designer' ? { x: 42, y: 0, zoom: 0.72 } : { x: 28, y: 35, zoom: 0.58 }}
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
          <span className="eyebrow">GUI-0 INSPECTOR</span>
          <h2>{selectedNode?.data.label ?? (selectedEdge ? 'Data edge' : '未选择实体')}</h2>
          {(selectedNode || selectedEdge) && (
            <code>{selectedNode?.data.protocolId ?? selectedEdge?.id}</code>
          )}
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
                {selectedNode.data.engineVersion && <div><dt>Version</dt><dd>{selectedNode.data.engineVersion}</dd></div>}
              </dl>
            </section>

            <section>
              <h3>Typed ports</h3>
              {(['inputs', 'outputs'] as const).map((direction) => (
                <div className="port-group" key={direction}>
                  <span className="port-group-label"><i className={`port-dot ${direction === 'inputs' ? 'input' : 'output'}`} /> {direction}</span>
                  {selectedNode.data[direction].length > 0 ? selectedNode.data[direction].map((port) => (
                    <div className="port-summary" key={`${direction}-${port.id}`}>
                      <span>{port.label}</span>
                      <code>{port.artifactType} · {port.scope} · {port.cardinality}</code>
                    </div>
                  )) : <div className="port-empty">none</div>}
                </div>
              ))}
            </section>

            <section>
              <h3>媒体变化</h3>
              <div className="media-change-list">
                {selectedNode.data.mediaChanges.map((change) => (
                  <div className={`media-change media-change--${change.tone ?? 'neutral'}`} key={`${change.label}-${change.value}`}>
                    <span>{change.label}</span><strong>{change.value}</strong>
                  </div>
                ))}
              </div>
            </section>

            {mode === 'designer' && !frozen && (
              <section>
                <button className="button button--danger inspector-action" type="button" onClick={deleteSelectedNode}>
                  删除所选节点
                </button>
              </section>
            )}

            {selectedNode.data.runStatus && (
              <section>
                <h3>Mock Runtime snapshot</h3>
                <div className={`runtime-status runtime-status--${selectedNode.data.runStatus}`}>
                  {selectedNode.data.runStatus.replace('_', ' ')}
                </div>
                <p className="muted">状态来自浏览器内模拟事件序列，不是正式 Runtime authority。</p>
              </section>
            )}
          </div>
        ) : selectedEdge ? (
          <div className="inspector-content">
            <section>
              <h3>Mock data edge</h3>
              <p>{selectedEdge.source}.{selectedEdge.sourceHandle} → {selectedEdge.target}.{selectedEdge.targetHandle}</p>
              {mode === 'designer' && !frozen && (
                <button className="button button--danger inspector-action" type="button" onClick={deleteSelectedEdge}>
                  删除所选连接
                </button>
              )}
            </section>
          </div>
        ) : <div className="empty-inspector">在画布中选择一个节点或连接查看属性。</div>}
        {mode === 'designer' && (
          <div className="inspector-content">
            <section>
              <h3>当前连接</h3>
              <label>
                Edge
                <select
                  aria-label="GUI-0 connection list"
                  value={selectedEdgeId ?? ''}
                  onChange={(event) => {
                    const edgeId = event.target.value || null
                    setSelectedEdgeId(edgeId)
                    if (edgeId) setSelectedNode(null)
                  }}
                >
                  <option value="">选择一条连接</option>
                  {designerEdges.map((edge) => (
                    <option value={edge.id} key={edge.id}>
                      {edge.source}.{edge.sourceHandle} → {edge.target}.{edge.targetHandle}
                    </option>
                  ))}
                </select>
              </label>
            </section>
          </div>
        )}
      </aside>

      <section className={`bottom-drawer ${bottomOpen ? 'is-open' : ''}`}>
        <button className="drawer-toggle" type="button" onClick={() => setBottomOpen((open) => !open)}>
          <span>Mock Diagnostics</span><strong>{diagnostics.length}</strong><i>{bottomOpen ? '收起' : '展开'}</i>
        </button>
        {bottomOpen && (
          <div className="diagnostic-list">
            {diagnostics.map((diagnostic, index) => (
              <article className={`diagnostic diagnostic--${diagnostic.severity}`} key={`${diagnostic.code}-${diagnostic.entity ?? index}`}>
                <span className="diagnostic-icon">{diagnostic.severity === 'warning' ? '!' : diagnostic.severity === 'error' ? '×' : 'i'}</span>
                <div><span className="diagnostic-code">{diagnostic.code}</span><strong>{diagnostic.title}</strong><p>{diagnostic.message}</p></div>
                {diagnostic.entity && <button type="button">定位 {diagnostic.entity}</button>}
              </article>
            ))}
          </div>
        )}
      </section>

      {showFreeze && compiledGraph && (
        <FreezeDialog
          draftNodeCount={compiledGraph.nodes.length}
          draftEdgeCount={compiledGraph.edges.length}
          onCancel={() => setShowFreeze(false)}
          onConfirm={confirmFreeze}
        />
      )}
    </main>
  )
}
