/** 承载单一 Project Graph/Run snapshot 的 React Flow 画布，不保存第二份 Graph。 */

import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type Connection,
  type EdgeChange,
  type EdgeMouseHandler,
  type NodeChange,
  type NodeMouseHandler,
  type OnSelectionChangeParams,
} from '@xyflow/react'
import type { ReactNode } from 'react'
import { WorkflowNodeCard } from '../../components/WorkflowNodeCard'
import type { WorkflowEdge, WorkflowNode } from '../../model'

const nodeTypes = { workflow: WorkflowNodeCard }

export interface GraphCanvasProps {
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
  nodes,
  edges,
  editable,
  busy,
  modeLabel,
  contextLabel,
  snapshotChanged,
  canToggleSnapshot,
  loading,
  boundaryError,
  hasProject,
  overlays,
  onToggleSnapshot,
  onNodesChange,
  onEdgesChange,
  onSelectionChange,
  onNodeClick,
  onEdgeClick,
  onConnect,
  isValidConnection,
}: GraphCanvasProps) {
  return (
    <section className="canvas-panel" aria-label="Studio Designer 画布">
      <div className="canvas-context">
        <div><span className="context-mode">{modeLabel}</span><strong>{contextLabel}</strong></div>
        <div className="canvas-context-actions">
          {canToggleSnapshot && <button type="button" onClick={onToggleSnapshot}>{modeLabel === 'Run snapshot' ? '查看当前 Graph' : '查看 Run snapshot'}</button>}
          {snapshotChanged && <span>Run snapshot / 当前 Graph 已变化</span>}
        </div>
      </div>
      {overlays}
      {loading ? (
        <div className="authority-empty" role="status"><strong>正在连接 Project Service…</strong></div>
      ) : boundaryError && !hasProject ? (
        <div className="authority-empty" role="alert"><strong>Project Service 不可用</strong><p>{boundaryError}</p><p>Studio 不会回退浏览器内存数据。</p></div>
      ) : !hasProject ? (
        <div className="authority-empty"><strong>尚未打开工程</strong><p>输入本地 .zniku 路径后选择“打开”或“新建”。</p></div>
      ) : (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onSelectionChange={onSelectionChange}
          onNodeClick={onNodeClick}
          onEdgeClick={onEdgeClick}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          nodesDraggable={!busy && editable}
          nodesConnectable={!busy && editable}
          edgesReconnectable={false}
          deleteKeyCode={null}
          selectionOnDrag
          multiSelectionKeyCode={['Control', 'Meta']}
          fitView
          minZoom={0.2}
          maxZoom={1.8}
          colorMode="dark"
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.1} color="#263344" />
          <Controls position="bottom-left" showInteractive={false} />
          <MiniMap position="bottom-right" pannable zoomable nodeColor="#d89b45" />
        </ReactFlow>
      )}
    </section>
  )
}
