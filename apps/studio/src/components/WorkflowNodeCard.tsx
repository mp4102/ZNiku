/** 在同一张 Designer 图上显示节点定义、typed ports 与真实 Runtime overlay。 */

import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { WorkflowNode } from '../model'

const statusLabels = {
  pending: 'Pending',
  running: 'Running',
  waiting_external: 'Waiting external',
  completed: 'Completed',
  failed: 'Failed',
} as const

export function WorkflowNodeCard({ data, selected }: NodeProps<WorkflowNode>) {
  const portTop = (index: number, total: number) => `${58 + (index - (total - 1) / 2) * 22}%`
  const stale = data.latestResult?.stale === true
  const state = data.nodeRun?.state

  return (
    <article
      className={`workflow-node workflow-node--${data.executorKind} ${selected ? 'is-selected' : ''} ${
        state ? `status-${state}` : ''
      } ${stale ? 'status-stale' : ''}`}
      aria-label={`${data.instanceId} 节点`}
    >
      {data.inputs.map((port, index) => (
        <Handle
          id={port.port_id}
          key={port.port_id}
          type="target"
          position={Position.Left}
          className="typed-handle typed-handle--input"
          style={{ top: portTop(index, data.inputs.length) }}
          aria-label={`输入 ${port.port_id} ${port.data_type}`}
        />
      ))}

      <div className="node-kicker">
        <span className="node-icon">
          {data.executorKind === 'manual_external' ? 'ME' : data.executorKind === 'command' ? 'CM' : 'PY'}
        </span>
        <span>{data.executorKind.replace('_', ' ')}</span>
        <span className="node-version">{data.definitionVersion}</span>
      </div>
      <strong>{data.label}</strong>
      <span className="node-subtitle">{data.typeId}</span>
      {data.summaries.length > 0 && <div className="node-parameter-summary">{data.summaries.map((summary) => <span key={summary}>{summary}</span>)}</div>}
      {(data.inputs.length > 1 || data.outputs.length > 1) && (
        <div className="multi-port-summary">
          {data.inputs.length > 1 && <span>IN · {data.inputs.map((port) => port.port_id).join(' + ')}</span>}
          {data.outputs.length > 1 && <span>OUT · {data.outputs.map((port) => port.port_id).join(' + ')}</span>}
        </div>
      )}
      {state && <span className={`run-chip run-chip--${state}`}>{statusLabels[state]}</span>}
      {stale && <span className="run-chip run-chip--stale">Stale</span>}
      {data.progress.mode === 'determinate' && data.progress.fraction !== null && (
        <span className="node-progress">{Math.round(data.progress.fraction * 100)}%</span>
      )}
      {data.progress.mode === 'indeterminate' && (
        <span className="node-progress node-progress--indeterminate" aria-label="进度不确定">
          <i aria-hidden="true" /> Working…
        </span>
      )}
      {(data.progress.measurement || data.progress.elapsed) && (
        <span className="node-progress-detail">
          {data.progress.measurement && (
            <span>
              {data.progress.measurement.current} / {data.progress.measurement.total}{' '}
              {data.progress.measurement.unit}
            </span>
          )}
          {data.progress.elapsed && <span>{data.progress.elapsed}</span>}
        </span>
      )}

      {data.outputs.map((port, index) => (
        <Handle
          id={port.port_id}
          key={port.port_id}
          type="source"
          position={Position.Right}
          className="typed-handle typed-handle--output"
          style={{ top: portTop(index, data.outputs.length) }}
          aria-label={`输出 ${port.port_id} ${port.data_type}`}
        />
      ))}
    </article>
  )
}
