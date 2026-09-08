/** 提供与拖线相同的键盘连接路径；候选仍复用同一个即时校验回调，正式校验由 Python 完成。 */

import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import type { Connection } from '@xyflow/react'
import type { WorkflowNode } from '../../model'
import { dialogFocusTargets } from './focus-management'

export function CanvasDialog({ title, children, onClose }: {
  readonly title: string
  readonly children: ReactNode
  readonly onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const previous = document.activeElement
    if (ref.current) (dialogFocusTargets(ref.current)[0] ?? ref.current).focus()
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus() }
  }, [])
  const trap = (event: KeyboardEvent<HTMLDivElement>) => {
    // 对话框里的快捷键不得冒泡成底图的删除、复制或 Undo。
    event.stopPropagation()
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeRef.current(); return }
    if (event.key !== 'Tab') return
    const items = ref.current ? dialogFocusTargets(ref.current) : []
    if (!items.length) { event.preventDefault(); ref.current?.focus(); return }
    const first = items[0]!
    const last = items[items.length - 1]!
    if (event.shiftKey && (document.activeElement === first || !items.includes(document.activeElement as HTMLElement))) {
      event.preventDefault(); last.focus()
    } else if (!event.shiftKey && (document.activeElement === last || !items.includes(document.activeElement as HTMLElement))) { event.preventDefault(); first.focus() }
  }
  return <div className="canvas-dialog-backdrop">
    <div ref={ref} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} className="canvas-dialog" onKeyDown={trap}>
      <div className="canvas-dialog-heading"><h2>{title}</h2><button type="button" aria-label={`关闭${title}`} onClick={onClose}>关闭</button></div>
      {children}
    </div>
  </div>
}

export interface ConnectNodesDialogProps {
  readonly nodes: ReadonlyArray<WorkflowNode>
  readonly disabled?: boolean
  readonly initialSourceId?: string
  readonly isValidConnection: (connection: Connection) => boolean
  readonly onConnect: (connection: Connection) => void
  readonly onClose: () => void
}

export function ConnectNodesDialog({ nodes, disabled = false, initialSourceId, isValidConnection, onConnect, onClose }: ConnectNodesDialogProps) {
  const [sourceId, setSourceId] = useState(initialSourceId ?? '')
  const [outputId, setOutputId] = useState('')
  const [targetId, setTargetId] = useState('')
  const [inputId, setInputId] = useState('')
  const sources = nodes.filter((node) => node.data.outputs.length > 0)
  const source = sources.find((node) => node.id === sourceId) ?? sources[0]
  const output = source?.data.outputs.find((port) => port.port_id === outputId) ?? source?.data.outputs[0]
  const connectionTo = (node: WorkflowNode, portId: string): Connection => ({
    source: source?.id ?? '', sourceHandle: output?.port_id ?? '', target: node.id, targetHandle: portId,
  })
  const targets = source && output ? nodes.filter((node) => node.data.inputs.some((port) => isValidConnection(connectionTo(node, port.port_id)))) : []
  const target = targets.find((node) => node.id === targetId) ?? targets[0]
  const inputs = target?.data.inputs.filter((port) => isValidConnection(connectionTo(target, port.port_id))) ?? []
  const input = inputs.find((port) => port.port_id === inputId) ?? inputs[0]
  const nodeLabel = (node: WorkflowNode) => {
    const duplicates = nodes.filter((item) => item.data.label === node.data.label)
    return duplicates.length > 1 ? `${node.data.label}（${duplicates.findIndex((item) => item.id === node.id) + 1}）` : node.data.label
  }
  const valid = source && output && target && input && isValidConnection(connectionTo(target, input.port_id))
  return <CanvasDialog title="连接节点" onClose={onClose}>
    <p>选择输出，再选择可以接收它的下一步。只列出类型兼容、未占用且不会成环的连接。</p>
    <label>从哪个节点<select aria-label="连接起点" disabled={disabled || !sources.length} value={source?.id ?? ''} onChange={(event) => { setSourceId(event.target.value); setOutputId(''); setTargetId(''); setInputId('') }}>
      {sources.map((node) => <option key={node.id} value={node.id}>{nodeLabel(node)}</option>)}
    </select></label>
    <label>使用哪个输出<select aria-label="连接输出" disabled={disabled || !output} value={output?.port_id ?? ''} onChange={(event) => { setOutputId(event.target.value); setTargetId(''); setInputId('') }}>
      {source?.data.outputs.map((port) => <option key={port.port_id} value={port.port_id}>{source.data.portLabels?.output[port.port_id] ?? port.port_id}</option>)}
    </select></label>
    <label>连接到哪个节点<select aria-label="连接终点" disabled={disabled || !targets.length} value={target?.id ?? ''} onChange={(event) => { setTargetId(event.target.value); setInputId('') }}>
      {targets.map((node) => <option key={node.id} value={node.id}>{nodeLabel(node)}</option>)}
    </select></label>
    <label>连接到哪个输入<select aria-label="连接输入" disabled={disabled || !inputs.length} value={input?.port_id ?? ''} onChange={(event) => setInputId(event.target.value)}>
      {inputs.map((port) => <option key={port.port_id} value={port.port_id}>{target?.data.portLabels?.input[port.port_id] ?? port.port_id}</option>)}
    </select></label>
    {!valid && <p role="status">没有可连接的下一步。请先添加兼容节点，或移除目标已有的连接。</p>}
    <button type="button" className="primary-action" disabled={disabled || !valid} onClick={() => {
      if (!disabled && target && input) {
        const connection = connectionTo(target, input.port_id)
        if (isValidConnection(connection)) { onConnect(connection); onClose() }
      }
    }}>建立连接</button>
  </CanvasDialog>
}
