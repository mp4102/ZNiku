import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { WorkflowNode } from '../model'

const statusLabels = {
  pending: 'Pending',
  ready: 'Ready',
  running: 'Running',
  waiting_operator: 'Waiting operator',
  verifying: 'Verifying',
  complete: 'Complete',
}

export function WorkflowNodeCard({ data, selected }: NodeProps<WorkflowNode>) {
  const categoryLabel = {
    source: 'SOURCE',
    operator: 'GRAPH',
    engine: 'ENGINE',
    final: 'FINAL',
  }[data.category]

  const portTop = (index: number, total: number) => `${58 + (index - (total - 1) / 2) * 22}%`

  return (
    <article
      className={`workflow-node workflow-node--${data.category} ${selected ? 'is-selected' : ''} ${
        data.runStatus ? `status-${data.runStatus}` : ''
      }`}
      aria-label={`${data.label} 节点`}
    >
      {data.inputs.map((port, index) => (
        <Handle
          id={port.id}
          key={port.id}
          type="target"
          position={Position.Left}
          className="typed-handle typed-handle--input"
          style={{ top: portTop(index, data.inputs.length) }}
          aria-label={`输入 ${port.label} ${port.artifactType}`}
        />
      ))}

      <div className="node-kicker">
        <span className="node-icon">{data.icon}</span>
        <span>{categoryLabel}</span>
        <span className="node-scope">{data.scope}</span>
      </div>
      <strong>{data.label}</strong>
      <span className="node-subtitle">{data.subtitle}</span>
      {(data.inputs.length > 1 || data.outputs.length > 1) && (
        <div className="multi-port-summary">
          {data.inputs.length > 1 && <span>IN · {data.inputs.map((port) => port.label).join(' + ')}</span>}
          {data.outputs.length > 1 && <span>OUT · {data.outputs.map((port) => port.label).join(' + ')}</span>}
        </div>
      )}
      {data.instanceLabel && <span className="instance-chip">Chapter {data.instanceLabel}</span>}
      {data.runStatus && (
        <span className={`run-chip run-chip--${data.runStatus}`}>{statusLabels[data.runStatus]}</span>
      )}

      {data.outputs.map((port, index) => (
        <Handle
          id={port.id}
          key={port.id}
          type="source"
          position={Position.Right}
          className="typed-handle typed-handle--output"
          style={{ top: portTop(index, data.outputs.length) }}
          aria-label={`输出 ${port.label} ${port.artifactType}`}
        />
      ))}
    </article>
  )
}
