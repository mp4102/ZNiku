/** 承载同一 Project Graph 的画布。布局、搜索、连接建议和折叠均只收集编辑意图，不创建运行权威。 */

import {
  Background, BackgroundVariant, MiniMap, ReactFlow,
  type Connection, type EdgeChange, type EdgeMouseHandler, type NodeChange,
  type NodeMouseHandler, type OnSelectionChangeParams, type OnNodeDrag,
  type ReactFlowInstance, type Viewport, type OnConnectStartParams,
} from '@xyflow/react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { WorkflowNodeCard } from '../../components/WorkflowNodeCard'
import type { WorkflowEdge, WorkflowNode } from '../../model'
import type { GraphWire, NodeDefinitionWire } from '../contracts'
import { compatibleNodeSuggestions, type ConnectionSource } from '../graph'
import { CanvasDialog, ConnectNodesDialog } from './ConnectNodesDialog'
import { useCanvasNodeGeometry } from './use-canvas-node-geometry'

const nodeTypes = { workflow: WorkflowNodeCard }
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
  readonly onAutoLayout?: () => void
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
  onAutoLayout, onToggleLibrary, libraryOpen, onViewportChange, onToggleGroup, onAddConnectedNodes, onNodeDragStart, onNodeDragStop,
  onToggleSnapshot, onNodesChange, onEdgesChange, onSelectionChange, onNodeClick, onEdgeClick,
  onConnect, isValidConnection,
}: GraphCanvasProps) {
  const [instance, setInstance] = useState<ReactFlowInstance<WorkflowNode, WorkflowEdge> | null>(null)
  const [query, setQuery] = useState('')
  const [connectOpen, setConnectOpen] = useState(false)
  const [connecting, setConnecting] = useState<OnConnectStartParams | null>(null)
  const [suggestion, setSuggestion] = useState<{
    readonly sources: ReadonlyArray<ConnectionSource>
    readonly position: { readonly x: number; readonly y: number }
  } | null>(null)
  const disabled = !editable || busy || !hasProject
  const selectedNodes = nodes.filter((node) => node.selected)
  const selectedSource = selectedNodes.length === 1 && selectedNodes[0]!.data.outputs.length > 0 ? selectedNodes[0]! : null
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const matches = normalizedQuery ? nodes.filter((node) => [node.data.label, node.data.typeId, ...node.data.summaries]
    .some((text) => text.toLocaleLowerCase().includes(normalizedQuery))) : []
  const suggestions = graph && suggestion ? compatibleNodeSuggestions(graph, definitions, suggestion.sources) : []

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
  const canvasNodes = useCanvasNodeGeometry(decoratedNodes, !disabled, onNodesChange)

  return (
    <section className="canvas-panel" aria-label="Studio Designer 画布" id="workflow-canvas" tabIndex={-1}>
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
        {onAutoLayout && <button type="button" disabled={disabled || nodes.length === 0} onClick={onAutoLayout}>自动布局</button>}
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
      {overlays}
      {loading ? (
        <div className="authority-empty" role="status"><strong>{advanced ? '正在连接 Project Service…' : '正在连接本机工程服务…'}</strong></div>
      ) : boundaryError && !hasProject ? (
        <div className="authority-empty" role="alert"><strong>本机工程服务暂时不可用</strong><p>{advanced ? boundaryError : '工程与已有媒体仍然保留。请在工程首页重新连接。'}</p>{advanced && <p>Studio 不会回退浏览器内存数据。</p>}</div>
      ) : !hasProject ? (
        <div className="authority-empty"><strong>尚未打开工程</strong><p>在工程首页新建或打开已有工程。</p></div>
      ) : (
        <ReactFlow
          nodes={canvasNodes.nodes} edges={edges} nodeTypes={nodeTypes}
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
          fitView={!viewport} defaultViewport={viewport ?? undefined} minZoom={0.2} maxZoom={1.8}
          colorMode="dark" proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.1} color="#263344" />
          <MiniMap position="bottom-right" pannable zoomable nodeColor="#d89b45" />
        </ReactFlow>
      )}
      {hasProject && !loading && <div className="canvas-zoom-controls" aria-label="画布缩放">
        <button type="button" disabled={!instance} aria-label="放大画布" onClick={() => zoom('in')}>＋</button>
        <button type="button" disabled={!instance} aria-label="缩小画布" onClick={() => zoom('out')}>−</button>
      </div>}
      {connectOpen && <ConnectNodesDialog nodes={nodes} initialSourceId={selectedSource?.id} disabled={disabled} isValidConnection={isValidConnection} onConnect={onConnect} onClose={() => setConnectOpen(false)} />}
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
