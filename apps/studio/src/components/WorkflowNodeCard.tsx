/** 人类可读节点卡与实际端口行；测量只更新 ReactFlow 几何，不产生 Graph 编辑或运行命令。 */

import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/react'
import { Fragment, memo, useLayoutEffect, useRef } from 'react'
import type { WorkflowNode } from '../model'
import type { PortSpecWire } from '../studio/contracts'
import { creatorElapsedLabel, failurePresentation, nodeStateLabel } from '../studio/run-presentation'
import './workflow-node-card.css'

const statusLabels = {
  pending: 'Pending', running: 'Running', waiting_external: 'Waiting external',
  completed: 'Completed', failed: 'Failed',
} as const

const iconLabels: Readonly<Record<string, string>> = {
  source: '导入', media: '媒体', video: '视频', audio: '音频', transform: '处理',
  split: '切分', merge: '合并', encode: '编码', mux: '封装', output: '输出', check: '检查',
}

export const WorkflowNodeCard = memo(function WorkflowNodeCard({ id, data, selected }: NodeProps<WorkflowNode>) {
  const element = useRef<HTMLElement>(null)
  const updateNodeInternals = useUpdateNodeInternals()
  const nodeId = id ?? data.instanceId
  const stale = data.latestResult?.stale === true
  const state = data.nodeRun?.state
  const advanced = data.advanced !== false
  const manual = data.executorKind === 'manual_external' || state === 'waiting_external'
  const summaries = data.summaries.slice(0, 3)
  const problem = data.problemSummary ?? (data.nodeRun?.error
    ? failurePresentation(data.nodeRun.error.reason).title : null)
  const portLabel = (direction: 'input' | 'output', portId: string) => data.portLabels?.[direction][portId] ?? portId
  // 结构相同的新投影、进度/耗时文本和选择不主动废弃端口测量。实际尺寸变化由 Observer 捕获。
  const structure = JSON.stringify([data.label, advanced, data.collapsed, data.groupLabel,
    data.collapsed ? [] : summaries, problem, data.inputs, data.outputs, data.portLabels])

  useLayoutEffect(() => { updateNodeInternals(nodeId) }, [nodeId, structure, updateNodeInternals])
  useLayoutEffect(() => {
    const card = element.current
    if (!card) return
    let active = true
    let lastSize = ''
    const measure = () => { if (active) updateNodeInternals(nodeId) }
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect
      if (!box) return
      const size = `${box.width}:${box.height}`
      if (size === lastSize) return
      lastSize = size
      measure()
    })
    observer?.observe(card)
    // 字体替换可改变内部行高，即使卡片外框相同也重新读取真实 Handle 中心。
    const fonts = document.fonts
    fonts?.addEventListener('loadingdone', measure)
    void fonts?.ready.then(measure)
    return () => { active = false; observer?.disconnect(); fonts?.removeEventListener('loadingdone', measure) }
  }, [nodeId, updateNodeInternals])

  const portRow = (port: PortSpecWire | undefined, direction: 'input' | 'output') => {
    if (!port) return <span className="node-port-placeholder" aria-hidden="true" />
    const label = portLabel(direction, port.port_id)
    const compatible = (direction === 'input' ? data.compatibleInputPortIds : data.compatibleOutputPortIds)?.includes(port.port_id)
    const directionLabel = direction === 'input' ? '输入' : '输出'
    const fullLabel = `${directionLabel}：${label}${advanced ? ` · ${port.data_type}` : ''}`
    return <div className={`node-port-row node-port-row--${direction}`} data-port-direction={direction}
      data-port-id={port.port_id} data-node-id={nodeId}>
      <Handle id={port.port_id} type={direction === 'input' ? 'target' : 'source'}
        position={direction === 'input' ? Position.Left : Position.Right}
        className={`typed-handle typed-handle--${direction} ${data.connecting ? compatible ? 'is-compatible' : 'is-incompatible' : ''}`}
        aria-hidden="true" data-port-direction={direction} data-port-id={port.port_id} data-node-id={nodeId}
        title={`${fullLabel}${compatible ? ' · 可以连接' : ''}`} />
      {/* 名称可获焦追踪但不是执行按钮；创建连接仍使用原拖线或“连接节点”对话框。 */}
      <span className="node-port-label nodrag nopan" tabIndex={0} title={fullLabel} aria-label={fullLabel}
        data-port-direction={direction} data-port-id={port.port_id} data-node-id={nodeId}>
        <span className="node-port-name">{label}</span>
        {advanced && <small className="node-port-type">{port.data_type}</small>}
      </span>
    </div>
  }

  return (
    <article ref={element}
      className={`workflow-node workflow-node-card workflow-node--${data.executorKind} ${selected ? 'is-selected' : ''} ${
        state ? `status-${state}` : ''
      } ${stale ? 'status-stale' : ''} ${data.collapsed ? 'is-collapsed' : ''} ${data.connecting ? 'is-connecting' : ''} ${data.groupLabel ? 'has-ui-group' : ''}`}
      data-node-id={nodeId} data-group-color={data.groupColorToken}
      aria-label={`${advanced ? data.instanceId : data.label} 节点`}>
      <div className="node-card-content">
        {data.groupLabel && <span className="node-group-label" title={data.groupLabel}>{data.groupLabel}</span>}
        <div className="node-kicker">
          <span className="node-icon">{advanced ? data.executorKind === 'manual_external' ? 'ME' : data.executorKind === 'command' ? 'CM' : 'PY' : iconLabels[data.iconToken ?? ''] ?? '节点'}</span>
          {advanced && <><span>{data.executorKind.replace('_', ' ')}</span><span className="node-version">{data.definitionVersion}</span></>}
        </div>
        <strong className="node-card-title" title={data.label}>{data.label}</strong>
        {advanced && !data.collapsed && <span className="node-subtitle" title={data.typeId}>{data.typeId}</span>}
        {!data.collapsed && summaries.length > 0 && <div className="node-parameter-summary" role="list" aria-label="关键设置">
          {summaries.map((summary, index) => <span role="listitem" title={summary} key={`${index}-${summary}`}>{summary}</span>)}
        </div>}
        <div className="node-card-status">
          {state && <span className={`run-chip run-chip--${state}`}>{advanced ? statusLabels[state] : nodeStateLabel(state)}</span>}
          {data.nodeRun?.reused_from_result_id && <span className="run-chip run-chip--reused">{advanced ? 'Reused' : nodeStateLabel('reused')}</span>}
          {stale && <span className="run-chip run-chip--stale">{advanced ? 'Stale' : nodeStateLabel('stale')}</span>}
        </div>
        {problem && <span className="node-card-problem" role="note" title={problem} aria-label={`需要处理：${problem}`}>! {problem}</span>}
        {!manual && data.progress.mode === 'determinate' && data.progress.fraction !== null && <span className="node-progress">{Math.round(data.progress.fraction * 100)}%</span>}
        {!manual && data.progress.mode === 'indeterminate' && <span className="node-progress node-progress--indeterminate" aria-label="进度不确定"><i aria-hidden="true" /> {advanced ? 'Working…' : '正在处理…'}</span>}
        {((!manual && data.progress.measurement) || data.progress.elapsed) && <span className="node-progress-detail">
          {!manual && data.progress.measurement && <span>{data.progress.measurement.current} / {data.progress.measurement.total}{' '}
            {advanced ? data.progress.measurement.unit : data.progress.measurement.unit === 'frames' ? '帧' : data.progress.measurement.unit === 'bytes' ? '字节' : data.progress.measurement.unit === 'items' ? '项' : data.progress.measurement.unit === 'microseconds' ? '微秒' : ''}</span>}
          {data.progress.elapsed && <span>{advanced ? data.progress.elapsed : creatorElapsedLabel(data.progress.elapsed, state === 'waiting_external')}</span>}
        </span>}
      </div>
      {(data.inputs.length > 0 || data.outputs.length > 0) && <div className="node-ports" role="group" aria-label="节点端口">
        <span className="node-ports-direction" aria-hidden="true">{data.inputs.length > 0 ? '→ 输入' : ''}</span>
        <span className="node-ports-direction node-ports-direction--output" aria-hidden="true">{data.outputs.length > 0 ? '输出 →' : ''}</span>
        {Array.from({ length: Math.max(data.inputs.length, data.outputs.length) }, (_, index) => <Fragment key={index}>
          {portRow(data.inputs[index], 'input')}{portRow(data.outputs[index], 'output')}
        </Fragment>)}
      </div>}
    </article>
  )
}, (previous, next) => previous.id === next.id && previous.data === next.data && previous.selected === next.selected)
