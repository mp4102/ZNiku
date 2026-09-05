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

const iconLabels: Readonly<Record<string, string>> = {
  source: '导入', media: '媒体', video: '视频', audio: '音频', transform: '处理',
  split: '切分', merge: '合并', encode: '编码', mux: '封装', output: '输出', check: '检查',
}

export function WorkflowNodeCard({ data, selected }: NodeProps<WorkflowNode>) {
  const portTop = (index: number, total: number) => `${58 + (index - (total - 1) / 2) * 22}%`
  const stale = data.latestResult?.stale === true
  const state = data.nodeRun?.state
  const advanced = data.advanced !== false
  const inputLabel = (id: string) => data.portLabels?.input[id] ?? id
  const outputLabel = (id: string) => data.portLabels?.output[id] ?? id

  return (
    <article
      className={`workflow-node workflow-node--${data.executorKind} ${selected ? 'is-selected' : ''} ${
        state ? `status-${state}` : ''
      } ${stale ? 'status-stale' : ''} ${data.collapsed ? 'is-collapsed' : ''} ${data.connecting ? 'is-connecting' : ''} ${data.groupLabel ? 'has-ui-group' : ''}`}
      data-group-color={data.groupColorToken}
      aria-label={`${data.instanceId} 节点`}
    >
      {data.inputs.map((port, index) => (
        <Handle
          id={port.port_id}
          key={port.port_id}
          type="target"
          position={Position.Left}
          className={`typed-handle typed-handle--input ${data.connecting ? data.compatibleInputPortIds?.includes(port.port_id) ? 'is-compatible' : 'is-incompatible' : ''}`}
          style={{ top: portTop(index, data.inputs.length) }}
          aria-label={`输入 ${inputLabel(port.port_id)}${advanced ? ` ${port.data_type}` : ''}`}
          title={`${inputLabel(port.port_id)}${data.compatibleInputPortIds?.includes(port.port_id) ? ' · 可以连接' : ''}`}
        />
      ))}

      {data.groupLabel && <span className="node-group-label">{data.groupLabel}</span>}
      <div className="node-kicker">
        <span className="node-icon">
          {advanced ? data.executorKind === 'manual_external' ? 'ME' : data.executorKind === 'command' ? 'CM' : 'PY' : iconLabels[data.iconToken ?? ''] ?? '节点'}
        </span>
        {advanced && <><span>{data.executorKind.replace('_', ' ')}</span><span className="node-version">{data.definitionVersion}</span></>}
      </div>
      <strong>{data.label}</strong>
      {advanced && <span className="node-subtitle">{data.typeId}</span>}
      {!data.collapsed && data.summaries.length > 0 && <div className="node-parameter-summary">{data.summaries.map((summary) => <span key={summary}>{summary}</span>)}</div>}
      {!data.collapsed && (data.inputs.length > 1 || data.outputs.length > 1) && (
        <div className="multi-port-summary">
          {data.inputs.length > 1 && <span>输入 · {data.inputs.map((port) => inputLabel(port.port_id)).join(' + ')}</span>}
          {data.outputs.length > 1 && <span>输出 · {data.outputs.map((port) => outputLabel(port.port_id)).join(' + ')}</span>}
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
          className={`typed-handle typed-handle--output ${data.connecting ? data.compatibleOutputPortIds?.includes(port.port_id) ? 'is-compatible' : 'is-incompatible' : ''}`}
          style={{ top: portTop(index, data.outputs.length) }}
          aria-label={`输出 ${outputLabel(port.port_id)}${advanced ? ` ${port.data_type}` : ''}`}
          title={`${outputLabel(port.port_id)}${data.compatibleOutputPortIds?.includes(port.port_id) ? ' · 可以连接' : ''}`}
        />
      ))}
    </article>
  )
}
