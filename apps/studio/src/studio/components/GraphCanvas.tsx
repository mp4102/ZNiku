/** 承载同一 Project Graph 的画布。布局、搜索、连接建议和折叠均只收集编辑意图，不创建运行权威。 */

import {
  Background, BackgroundVariant, MiniMap, ReactFlow,
  type Connection, type EdgeChange, type EdgeMouseHandler, type NodeChange,
  type NodeMouseHandler, type OnSelectionChangeParams, type OnNodeDrag,
  type ReactFlowInstance, type Viewport, type OnConnectStartParams,
} from '@xyflow/react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { WorkflowNodeCard } from '../../components/WorkflowNodeCard'
import type { WorkflowEdge, WorkflowNode } from '../../model'
import type { GraphWire, NodeDefinitionWire } from '../contracts'
import { compatibleNodeSuggestions, type ConnectionSource } from '../graph'
import { CanvasDialog, ConnectNodesDialog } from './ConnectNodesDialog'
import { geometryShape, useCanvasNodeGeometry } from './use-canvas-node-geometry'
import { CanvasGeometryObserver, type CanvasMeasurement } from './CanvasGeometryObserver'
import { useCanvasRouting } from './use-canvas-routing'
import { RoutedWorkflowEdge } from './RoutedWorkflowEdge'
import { layoutMeasuredGraph } from '../geometry/layout'
import type { Point } from '../geometry/types'
import { emphasizedEdges, type PathFocus, type TraceMode } from './canvas-path-emphasis'
import { crossingDisplayPaths } from './canvas-edge-gaps'
import './canvas-routing.css'

const nodeTypes = { workflow: WorkflowNodeCard }
const edgeTypes = { routed: RoutedWorkflowEdge }
const emptyDefinitions: ReadonlyArray<NodeDefinitionWire> = []
const emptyGroups: ReadonlyArray<CanvasGroupView> = []

export interface CanvasGroupView {
  readonly group_id: string
  readonly title: string
  readonly color_token: string
  readonly collapsed: boolean
  readonly node_ids: ReadonlyArray<string>
}

export interface AddConnectedNodesRequest {
  readonly sources: ReadonlyArray<ConnectionSource>
  readonly definition: NodeDefinitionWire
  readonly targetPortId: string
  readonly position: { readonly x: number; readonly y: number }
}

export interface GraphCanvasProps {
  readonly advanced?: boolean
  readonly showingSnapshot?: boolean
  readonly nodes: WorkflowNode[]
  readonly edges: WorkflowEdge[]
  readonly editable: boolean
  readonly busy: boolean
  readonly modeLabel: string
  readonly contextLabel: string
  readonly snapshotChanged: boolean
  readonly canToggleSnapshot: boolean
  readonly loading: boolean
  readonly boundaryError: string | null
  readonly hasProject: boolean
  readonly overlays?: ReactNode
  readonly graph?: GraphWire
  readonly definitions?: ReadonlyArray<NodeDefinitionWire>
  readonly groups?: ReadonlyArray<CanvasGroupView>
  readonly viewport?: Viewport | null
  readonly definitionLabel?: (definition: NodeDefinitionWire) => string
  readonly portLabel?: (definition: NodeDefinitionWire, direction: 'input' | 'output', portId: string) => string
  readonly viewKey?: string
  readonly onAutoLayout?: (positions: Readonly<Record<string, Point>>) => void
  readonly onToggleLibrary?: () => void
  readonly libraryOpen?: boolean
  readonly onViewportChange?: (viewport: Viewport) => void
  readonly onToggleGroup?: (groupId: string) => void
  readonly onAddConnectedNodes?: (request: AddConnectedNodesRequest) => void
  readonly onNodeDragStart?: OnNodeDrag<WorkflowNode>
  readonly onNodeDragStop?: OnNodeDrag<WorkflowNode>
  readonly onToggleSnapshot: () => void
  readonly onNodesChange: (changes: NodeChange<WorkflowNode>[]) => void
  readonly onEdgesChange: (changes: EdgeChange<WorkflowEdge>[]) => void
  readonly onSelectionChange: (selection: OnSelectionChangeParams) => void
  readonly onNodeClick: NodeMouseHandler<WorkflowNode>
  readonly onEdgeClick: EdgeMouseHandler<WorkflowEdge>
  readonly onConnect: (connection: Connection) => void
  readonly isValidConnection: (connection: Connection | WorkflowEdge) => boolean
}

export function GraphCanvas({
  advanced = true, showingSnapshot,
  nodes, edges, editable, busy, modeLabel, contextLabel, snapshotChanged,
  canToggleSnapshot, loading, boundaryError, hasProject, overlays,
  graph, definitions = emptyDefinitions, groups = emptyGroups, viewport, definitionLabel, portLabel,
  viewKey: explicitViewKey,
  onAutoLayout, onToggleLibrary, libraryOpen, onViewportChange, onToggleGroup, onAddConnectedNodes, onNodeDragStart, onNodeDragStop,
  onToggleSnapshot, onNodesChange, onEdgesChange, onSelectionChange, onNodeClick, onEdgeClick,
  onConnect, isValidConnection,
}: GraphCanvasProps) {
  const [instance, setInstance] = useState<ReactFlowInstance<WorkflowNode, WorkflowEdge> | null>(null)
  const [query, setQuery] = useState('')
  const [miniMapOpen, setMiniMapOpen] = useState(false)
  const [connecting, setConnecting] = useState<OnConnectStartParams | null>(null)
  const disabled = !editable || busy || !hasProject
  const groupsByNode = useMemo(() => new Map(groups.flatMap((group) => group.node_ids.map((id) => [id, group] as const))), [groups])
  const decoratedNodes = useMemo(() => nodes.map((node): WorkflowNode => {
    const group = groupsByNode.get(node.id)
    // 无分组/连接高亮时保持原 data 引用；位置拖动不必重绘每张卡片的正文。
    if (!group && !connecting) return node
    const inputIds = connecting?.handleType === 'source' ? node.data.inputs.filter((port) => isValidConnection({
      source: connecting.nodeId ?? '', sourceHandle: connecting.handleId ?? '', target: node.id, targetHandle: port.port_id,
    })).map((port) => port.port_id) : []
    const outputIds = connecting?.handleType === 'target' ? node.data.outputs.filter((port) => isValidConnection({
      source: node.id, sourceHandle: port.port_id, target: connecting.nodeId ?? '', targetHandle: connecting.handleId ?? '',
    })).map((port) => port.port_id) : []
    return { ...node, data: {
      ...node.data,
      collapsed: node.data.collapsed || group?.collapsed,
      groupLabel: group?.title,
      groupColorToken: group?.color_token,
      connecting: connecting !== null,
      compatibleInputPortIds: inputIds,
      compatibleOutputPortIds: outputIds,
    } }
  }), [nodes, groupsByNode, connecting, isValidConnection])
  const viewKey = explicitViewKey ?? (showingSnapshot ? 'snapshot' : 'current')
  const currentNodes = useRef(nodes)
  currentNodes.current = nodes
  const expectedShape = useRef('')
  expectedShape.current = JSON.stringify(decoratedNodes.map((node) => [node.id, geometryShape(node)]))
  const [measured, setMeasured] = useState<{ readonly viewKey: string; readonly value: CanvasMeasurement } | null>(null)
  const onMeasure = useCallback((measuredView: string, value: CanvasMeasurement) => {
    // ReactFlow 更新内部表可能晚一个 effect。相同 ID 的旧视图位置不能进入本次路由/布局。
    const positions = new Map(currentNodes.current.map((node) => [node.id, node.position]))
    if (value.shapeKey !== expectedShape.current || value.nodes.some((node) => positions.get(node.id)?.x !== node.x || positions.get(node.id)?.y !== node.y)) return
    setMeasured((before) => before?.viewKey === measuredView && before.value.key === value.key ? before : { viewKey: measuredView, value })
  }, [])
  const positions = useMemo(() => new Map(nodes.map((node) => [node.id, node.position])), [nodes])
  // 父级意图先于 ReactFlow 测量到达时立即失效；不能等下一次 observer 回调才拦截旧布局确认。
  const measurement = measured?.viewKey === viewKey && measured.value.shapeKey === expectedShape.current &&
    measured.value.nodes.every((node) => positions.get(node.id)?.x === node.x && positions.get(node.id)?.y === node.y)
    ? measured.value : null
  const routing = useCanvasRouting(viewKey, measurement, edges)
  const [pointerFocus, setPointerFocus] = useState<PathFocus | null>(null)
  const [keyboardFocus, setKeyboardFocus] = useState<PathFocus | null>(null)
  const [traceMode, setTraceMode] = useState<TraceMode>('direct')
  const [layoutState, setLayoutState] = useState<{ readonly identity: string; readonly positions: Readonly<Record<string, Point>> | null } | null>(null)
  const layoutFrame = useRef<number | null>(null)
  const layoutSequence = useRef(0)
  const latestIdentity = useRef(routing.identity)
  latestIdentity.current = routing.identity
  const [connectOpen, setConnectOpen] = useState(false)
  const [suggestion, setSuggestion] = useState<{
    readonly sources: ReadonlyArray<ConnectionSource>
    readonly position: { readonly x: number; readonly y: number }
  } | null>(null)
  const selectedNodes = nodes.filter((node) => node.selected)
  const selectedFocus: PathFocus | null = selectedNodes.length === 1 ? { nodeId: selectedNodes[0].id } : null
  const focus = keyboardFocus ?? pointerFocus ?? selectedFocus
  const emphasized = useMemo(() => emphasizedEdges(edges, focus, focus?.portId ? 'direct' : traceMode), [edges, focus?.nodeId, focus?.portId, focus?.direction, traceMode])
  const routesById = useMemo(() => new Map(routing.result?.routes.map((route) => [route.id, route]) ?? []), [routing.result])
  const displayPaths = useMemo(() => crossingDisplayPaths(routing.result?.routes ?? []), [routing.result])
  const geometryEdgesById = useMemo(() => new Map(routing.graph.edges.map((edge) => [edge.id, edge])), [routing.graph])
  const routedEdges = useMemo<WorkflowEdge[]>(() => {
    const dataById = new Map(nodes.map((node) => [node.id, node.data]))
    return edges.map((edge) => {
      const source = dataById.get(edge.source), target = dataById.get(edge.target)
      const label = `${source?.label ?? edge.source}（${source?.portLabels?.output[edge.sourceHandle ?? ''] ?? edge.sourceHandle ?? '输出'}）→ ${target?.label ?? edge.target}（${target?.portLabels?.input[edge.targetHandle ?? ''] ?? edge.targetHandle ?? '输入'}）${edge.data?.ordinal == null ? '' : `，第 ${edge.data.ordinal + 1} 路输入`}`
      return { ...edge, type: 'routed', ariaLabel: label,
      data: { ...edge.data, ordinal: edge.data?.ordinal ?? null,
        route: routesById.get(edge.id), start: geometryEdgesById.get(edge.id)?.start, end: geometryEdgesById.get(edge.id)?.end,
        displayPath: displayPaths.get(edge.id), highlighted: emphasized.has(edge.id), subdued: !!focus && !emphasized.has(edge.id),
        accessibleLabel: label,
      } } })
  }, [edges, nodes, routesById, geometryEdgesById, displayPaths, emphasized, !!focus])
  const selectedSource = selectedNodes.length === 1 && selectedNodes[0]!.data.outputs.length > 0 ? selectedNodes[0]! : null
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const matches = normalizedQuery ? nodes.filter((node) => [node.data.label, node.data.typeId, ...node.data.summaries]
    .some((text) => text.toLocaleLowerCase().includes(normalizedQuery))) : []
  const suggestions = graph && suggestion ? compatibleNodeSuggestions(graph, definitions, suggestion.sources) : []
  const cancelLayout = useCallback(() => {
    layoutSequence.current += 1
    if (layoutFrame.current !== null) cancelAnimationFrame(layoutFrame.current)
    layoutFrame.current = null
    setLayoutState(null)
  }, [])
  useEffect(() => { cancelLayout(); setPointerFocus(null); setKeyboardFocus(null) }, [viewKey, cancelLayout])
  useEffect(() => {
    if (layoutState && layoutState.identity !== routing.identity) cancelLayout()
  }, [routing.identity, layoutState, cancelLayout])
  useEffect(() => () => { layoutSequence.current += 1; if (layoutFrame.current !== null) cancelAnimationFrame(layoutFrame.current) }, [])
  const prepareLayout = () => {
    if (disabled || measurement?.nodes.length !== nodes.length || !nodes.length) return
    const identity = routing.identity
    const sequence = ++layoutSequence.current
    setLayoutState({ identity, positions: null })
    layoutFrame.current = requestAnimationFrame(() => {
      layoutFrame.current = null
      if (layoutSequence.current !== sequence || latestIdentity.current !== identity) return
      const byId = new Map(routing.graph.nodes.map((node) => [node.id, node]))
      // 整理依赖完整 Graph 边，不依赖尚在测量中的端口锚点；稳定原节点顺序不取决于内部缓存排列。
      const positions = layoutMeasuredGraph(nodes.map((node) => byId.get(node.id)!), edges)
      if (layoutSequence.current === sequence && latestIdentity.current === identity) setLayoutState({ identity, positions })
    })
  }
  const eventFocus = (target: EventTarget | null): PathFocus | null => {
    if (!(target instanceof Element)) return null
    const port = target.closest<HTMLElement>('[data-port-direction][data-port-id]')
    const nodeId = port?.dataset.nodeId ?? target.closest('.react-flow__node')?.getAttribute('data-id')
    if (!nodeId) return null
    return port ? { nodeId, portId: port.dataset.portId, direction: port.dataset.portDirection as 'input' | 'output' } : { nodeId }
  }

  useEffect(() => {
    if (!instance) return
    if (!viewport) { void instance.fitView({ padding: 0.25, maxZoom: 1.2, duration: 0 }); return }
    const current = instance.getViewport()
    if (current.x !== viewport.x || current.y !== viewport.y || current.zoom !== viewport.zoom) {
      void instance.setViewport(viewport, { duration: 0 })
    }
  }, [instance, viewport?.x, viewport?.y, viewport?.zoom])

  const focusNodes = (ids?: ReadonlyArray<string>) => {
    if (!instance) return
    void instance.fitView({ nodes: ids?.map((id) => ({ id })), padding: 0.25, maxZoom: 1.2, duration: 0 })
      .then(() => { if (editable) onViewportChange?.(instance.getViewport()) })
  }
  const zoom = (direction: 'in' | 'out') => {
    if (!instance) return
    void (direction === 'in' ? instance.zoomIn({ duration: 0 }) : instance.zoomOut({ duration: 0 }))
      .then(() => { if (editable) onViewportChange?.(instance.getViewport()) })
  }
  const canvasNodes = useCanvasNodeGeometry(decoratedNodes, !disabled, onNodesChange)

  return (
    <section className="canvas-panel" aria-label="Studio Designer 画布" id="workflow-canvas" tabIndex={-1}
      data-route-pending={routing.pending} data-route-solves={routing.solveCount}
      data-route-cache-hits={routing.result?.stats.cacheHits ?? 0} data-route-degraded={routing.result?.stats.degraded ?? 0}
      data-route-elapsed-ms={routing.result?.stats.elapsedMs ?? 0} data-route-max-slice-ms={routing.maxSliceMs}
      data-route-read-ms={measurement?.readMs ?? 0}
      onMouseOver={(event) => setPointerFocus(eventFocus(event.target))} onMouseLeave={() => setPointerFocus(null)}
      onFocusCapture={(event) => setKeyboardFocus(eventFocus(event.target))}
      onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setKeyboardFocus(null) }}>
      <div className="canvas-context">
        <div><span className="context-mode">{modeLabel}</span><strong>{contextLabel}</strong></div>
        <div className="canvas-context-actions">
          {canToggleSnapshot && <button type="button" onClick={onToggleSnapshot}>{(showingSnapshot ?? modeLabel === 'Run snapshot') ? '返回当前编辑' : '查看本次处理流程'}</button>}
          {snapshotChanged && <span>{advanced ? 'Run snapshot / 当前 Graph 已变化' : '当前编辑与这次处理记录不同'}</span>}
        </div>
      </div>
      {hasProject && !loading && <div className="canvas-edit-toolbar" aria-label="画布工具">
        {onToggleLibrary && <button type="button" aria-expanded={libraryOpen} onClick={onToggleLibrary}>添加节点</button>}
        <button type="button" disabled={!instance || nodes.length === 0} onClick={() => focusNodes()}>适应画布</button>
        <button type="button" disabled={!instance || selectedNodes.length === 0} onClick={() => focusNodes(selectedNodes.map((node) => node.id))}>缩放到选区</button>
        {onAutoLayout && <button type="button" disabled={disabled || nodes.length === 0 || measurement?.nodes.length !== nodes.length || !!layoutState}
          aria-busy={layoutState !== null} title={measurement?.nodes.length !== nodes.length ? '正在测量节点，准备好后可以整理' : '按实际节点尺寸整理，确认后应用，可撤销'} onClick={prepareLayout}>整理布局</button>}
        {layoutState && !layoutState.positions && <button type="button" onClick={cancelLayout}>取消整理</button>}
        <div className="canvas-path-tools" aria-label="连接追踪">
          {(['direct', 'upstream', 'downstream'] as const).map((mode) => <button key={mode} type="button" aria-pressed={traceMode === mode}
            onClick={() => setTraceMode(mode)}>{mode === 'direct' ? '直连' : mode === 'upstream' ? '上游路径' : '下游路径'}</button>)}
        </div>
        {advanced && <details className="canvas-route-diagnostics"><summary>连线诊断</summary>
          <dl><dt>测量读取</dt><dd>{(measurement?.readMs ?? 0).toFixed(2)} ms</dd>
            <dt>本次求解 / 命中缓存</dt><dd>{routing.result?.stats.routed ?? 0} / {routing.result?.stats.cacheHits ?? 0}</dd>
            <dt>最近求解 / 最长切片</dt><dd>{(routing.result?.stats.elapsedMs ?? 0).toFixed(2)} / {routing.maxSliceMs.toFixed(2)} ms</dd>
            <dt>降级连线</dt><dd>{routing.result?.stats.degraded ?? 0}</dd>
            <dt>求解次数</dt><dd>{routing.solveCount}</dd></dl>
          <p>仅临时画布几何。拖动/静止工作预算为 8/32 ms；超时会降级，不是硬实时保证。</p>
        </details>}
        <button type="button" disabled={disabled || nodes.length < 2} onClick={() => setConnectOpen(true)}>连接节点</button>
        {onAddConnectedNodes && <button type="button" disabled={disabled || !selectedSource} title={selectedSource ? '为所选节点的每个输出分别添加一个相同处理步骤' : '先选择一个带有输出的节点'} onClick={() => {
          if (!selectedSource) return
          setSuggestion({
            sources: selectedSource.data.outputs.map((port) => ({ sourceNodeId: selectedSource.id, sourcePortId: port.port_id })),
            position: { x: selectedSource.position.x + 360, y: selectedSource.position.y },
          })
        }}>为所有输出添加下一步</button>}
        <label className="canvas-search"><span>查找</span><input aria-label="查找画布节点" placeholder="节点名称或参数" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
        {normalizedQuery && <div className="canvas-search-results" aria-label="画布搜索结果">
          {matches.length === 0 ? <span role="status">没有匹配节点</span> : matches.map((node, index) => <button type="button" key={node.id} onClick={() => {
            onSelectionChange({ nodes: [node], edges: [] })
            focusNodes([node.id])
            setQuery('')
          }}>{node.data.label}{matches.filter((item) => item.data.label === node.data.label).length > 1 ? `（${index + 1}）` : ''}</button>)}
        </div>}
      </div>}
      {hasProject && groups.length > 0 && <div className="canvas-group-strip" aria-label="画布分组">
        {groups.map((group) => <div key={group.group_id} data-group-color={group.color_token}>
          <button type="button" onClick={() => focusNodes(group.node_ids)}>{group.title} · {group.node_ids.length} 个节点</button>
          {onToggleGroup && <button type="button" disabled={disabled} aria-label={`${group.collapsed ? '展开' : '折叠'}分组 ${group.title}`} onClick={() => onToggleGroup(group.group_id)}>{group.collapsed ? '展开' : '折叠'}</button>}
        </div>)}
      </div>}
      {connecting && <div className="canvas-connection-hint" role="status">亮起的端口可以连接；从输出拖到空白处可添加下一步。</div>}
      {!!routing.result?.stats.degraded && <p className="canvas-route-notice" role="status">{routing.result.stats.degraded} 条连线使用虚线简化路线，可能穿过节点。建议整理布局或移开重叠节点。</p>}
      {overlays}
      {loading ? (
        <div className="authority-empty" role="status"><strong>{advanced ? '正在连接 Project Service…' : '正在连接本机工程服务…'}</strong></div>
      ) : boundaryError && !hasProject ? (
        <div className="authority-empty" role="alert"><strong>本机工程服务暂时不可用</strong><p>{advanced ? boundaryError : '工程与已有媒体仍然保留。请在工程首页重新连接。'}</p>{advanced && <p>Studio 不会回退浏览器内存数据。</p>}</div>
      ) : !hasProject ? (
        <div className="authority-empty"><strong>尚未打开工程</strong><p>在工程首页新建或打开已有工程。</p></div>
      ) : (
        <ReactFlow
          nodes={canvasNodes.nodes} edges={routedEdges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
          onInit={setInstance} onNodesChange={canvasNodes.onNodesChange} onEdgesChange={onEdgesChange}
          onSelectionChange={onSelectionChange} onNodeClick={onNodeClick} onEdgeClick={onEdgeClick}
          onNodeDragStart={onNodeDragStart} onNodeDragStop={onNodeDragStop}
          onConnect={onConnect} isValidConnection={isValidConnection}
          onConnectStart={(_event, params) => { if (!disabled) setConnecting(params) }}
          onConnectEnd={(event, state) => {
            setConnecting(null)
            if (disabled || !onAddConnectedNodes || !graph || state.isValid || state.toNode || state.fromHandle?.type !== 'source' || !state.fromNode || !state.fromHandle.id || !instance) return
            if (!(event.target instanceof Element) || !event.target.closest('.react-flow__pane')) return
            const pointer = 'changedTouches' in event ? event.changedTouches[0] : event
            if (!pointer) return
            setSuggestion({
              sources: [{ sourceNodeId: state.fromNode.id, sourcePortId: state.fromHandle.id }],
              position: instance.screenToFlowPosition({ x: pointer.clientX, y: pointer.clientY }),
            })
          }}
          onMoveEnd={(event, next) => { if (event && editable) onViewportChange?.(next) }}
          nodesDraggable={!disabled} nodesConnectable={!disabled} edgesReconnectable={false}
          deleteKeyCode={null} selectionOnDrag multiSelectionKeyCode={['Control', 'Meta']}
          fitView={!viewport} defaultViewport={viewport ?? undefined} minZoom={0.02} maxZoom={1.8}
          colorMode="dark" proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.1} color="#263344" />
          {miniMapOpen && <MiniMap position="bottom-right" pannable zoomable nodeColor="#d89b45" />}
          <CanvasGeometryObserver viewKey={viewKey} onMeasure={onMeasure} />
        </ReactFlow>
      )}
      {hasProject && !loading && <div className="canvas-zoom-controls" aria-label="画布缩放">
        <button type="button" disabled={!instance} aria-label="放大画布" onClick={() => zoom('in')}>＋</button>
        <button type="button" disabled={!instance} aria-label="缩小画布" onClick={() => zoom('out')}>−</button>
        <button type="button" className="canvas-minimap-toggle" aria-label={miniMapOpen ? '隐藏小地图' : '显示小地图'}
          aria-pressed={miniMapOpen} onClick={() => setMiniMapOpen((before) => !before)}>小地图</button>
      </div>}
      {connectOpen && <ConnectNodesDialog nodes={nodes} initialSourceId={selectedSource?.id} disabled={disabled} isValidConnection={isValidConnection} onConnect={onConnect} onClose={() => setConnectOpen(false)} />}
      {layoutState?.positions && <CanvasDialog title="整理布局" onClose={cancelLayout}>
        <p>将按 {nodes.length} 个节点的实际尺寸整理整个工作流。只调整位置，不更改参数、连接、输入顺序或处理记录；确认后可撤销。</p>
        <div className="canvas-layout-actions"><button type="button" onClick={cancelLayout}>取消整理</button>
          <button type="button" disabled={disabled || layoutState.identity !== routing.identity} onClick={() => {
            if (!disabled && layoutState.identity === latestIdentity.current && layoutState.positions) onAutoLayout?.(layoutState.positions)
            cancelLayout()
          }}>应用布局</button></div>
      </CanvasDialog>}
      {suggestion && <CanvasDialog title="添加下一步" onClose={() => setSuggestion(null)}>
        <p>{suggestion.sources.length > 1 ? `将添加 ${suggestion.sources.length} 个相同步骤，分别连接每个输出；撤销时作为一次操作。` : '选择可以接收此输出的处理步骤，添加后会自动连接。'}</p>
        <div className="compatible-node-list">
          {suggestions.map(({ definition, targetPortId }) => <button type="button" disabled={disabled} key={`${definition.type_id}@${definition.version}:${targetPortId}`} onClick={() => {
            if (!disabled) { onAddConnectedNodes?.({ ...suggestion, definition, targetPortId }); setSuggestion(null) }
          }}>
            <strong>{definitionLabel?.(definition) ?? definition.type_id}</strong>
            <span>{portLabel?.(definition, 'input', targetPortId) ?? targetPortId}</span>
          </button>)}
          {suggestions.length === 0 && <p role="status">没有可同时接收这些输出的节点。请分别连接不同类型的输出。</p>}
        </div>
      </CanvasDialog>}
    </section>
  )
}
