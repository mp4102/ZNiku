import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { FormalWorkflowNode } from './graph-model'

export function FormalWorkflowNodeCard({ data, selected }: NodeProps<FormalWorkflowNode>) {
  const categoryLabel = { source: 'SOURCE', engine: 'ENGINE', final: 'FINAL' }[data.category]
  const icon = { source: '◉', engine: '◆', final: '✓' }[data.category]
  const portTop = (index: number, total: number) => `${58 + (index - (total - 1) / 2) * 22}%`

  return (
    <article
      className={`workflow-node workflow-node--${data.category} ${selected ? 'is-selected' : ''}`}
      aria-label={`${data.label} 节点`}
    >
      {data.inputs.map((port, index) => (
        <Handle
          id={port.portId}
          key={port.portId}
          type="target"
          position={Position.Left}
          className="typed-handle typed-handle--input"
          style={{ top: portTop(index, data.inputs.length) }}
          aria-label={`输入 ${port.portId} ${port.artifactType} ${port.mediaKind ?? 'none'}`}
        />
      ))}

      <div className="node-kicker">
        <span className="node-icon">{icon}</span>
        <span>{categoryLabel}</span>
        <span className="node-scope">{data.scope}</span>
      </div>
      <strong>{data.label}</strong>
      <span className="node-subtitle">{data.nodeId}</span>
      {(data.inputs.length > 1 || data.outputs.length > 1) && (
        <div className="multi-port-summary">
          {data.inputs.length > 1 && (
            <span>IN · {data.inputs.map((port) => port.portId).join(' + ')}</span>
          )}
          {data.outputs.length > 1 && (
            <span>OUT · {data.outputs.map((port) => port.portId).join(' + ')}</span>
          )}
        </div>
      )}

      {data.outputs.map((port, index) => (
        <Handle
          id={port.portId}
          key={port.portId}
          type="source"
          position={Position.Right}
          className="typed-handle typed-handle--output"
          style={{ top: portTop(index, data.outputs.length) }}
          aria-label={`输出 ${port.portId} ${port.artifactType} ${port.mediaKind ?? 'none'}`}
        />
      ))}
    </article>
  )
}
