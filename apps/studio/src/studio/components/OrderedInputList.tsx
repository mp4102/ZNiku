/** 将同一 ordered_many 输入的边显示为可排序列表；顺序 mutation 仍由调用者提交 Python。 */

import { useRef, useState } from 'react'
import type { EdgeWire } from '../contracts'
import { edgeId } from '../graph'

export interface OrderedInputListProps {
  readonly edges: ReadonlyArray<EdgeWire>
  readonly disabled?: boolean
  readonly nodeLabel: (nodeId: string) => string
  readonly portLabel?: (nodeId: string, portId: string) => string
  readonly onReorder: (edgeId: string, index: number) => void
}

export function OrderedInputList({ edges, disabled = false, nodeLabel, portLabel, onReorder }: OrderedInputListProps) {
  const ordered = [...edges].sort((left, right) => (left.ordinal ?? 0) - (right.ordinal ?? 0))
  const occurrences = new Map<string, number>()
  const stableKeys = new Map(edges.map((edge) => {
    const identity = JSON.stringify([edge.source_node_id, edge.source_port_id, edge.target_node_id, edge.target_port_id])
    const occurrence = occurrences.get(identity) ?? 0
    occurrences.set(identity, occurrence + 1)
    return [edge, `${identity}:${occurrence}`]
  }))
  const signature = ordered.map(edgeId).join('\n')
  const dragging = useRef<{ readonly id: string; readonly signature: string } | null>(null)
  const [announcement, setAnnouncement] = useState('')
  const move = (id: string, index: number) => {
    if (disabled || index < 0 || index >= ordered.length) return
    const item = ordered.find((edge) => edgeId(edge) === id)
    if (!item) return
    onReorder(id, index)
    setAnnouncement(`${nodeLabel(item.source_node_id)} 已移到第 ${index + 1} 项。`)
  }
  return (
    <div className="ordered-input-editor">
      <p>按列表顺序合并。拖动条目，或使用上移、下移按钮。</p>
      <ol aria-label="输入顺序" className="ordered-input-list">
        {ordered.map((edge, index) => {
          const id = edgeId(edge)
          const title = `${nodeLabel(edge.source_node_id)}${portLabel ? ` · ${portLabel(edge.source_node_id, edge.source_port_id)}` : ''}`
          return <li key={stableKeys.get(edge)} draggable={!disabled} onDragStart={(event) => {
            if (disabled) { event.preventDefault(); return }
            dragging.current = { id, signature }
            event.dataTransfer.effectAllowed = 'move'
            event.dataTransfer.setData('text/plain', id)
          }} onDragOver={(event) => { if (!disabled && dragging.current) event.preventDefault() }} onDrop={(event) => {
            event.preventDefault()
            const previous = dragging.current
            dragging.current = null
            if (previous?.signature === signature) move(previous.id, index)
          }} onDragEnd={() => { dragging.current = null }}>
            <span className="ordered-input-index" aria-hidden="true">{index + 1}</span>
            <span className="ordered-input-title">{title}</span>
            <div className="ordered-input-actions">
              <button type="button" disabled={disabled || index === 0} aria-label={`上移 ${title}`} onClick={() => move(id, index - 1)}>↑</button>
              <button type="button" disabled={disabled || index === ordered.length - 1} aria-label={`下移 ${title}`} onClick={() => move(id, index + 1)}>↓</button>
            </div>
          </li>
        })}
      </ol>
      <span className="canvas-live-message" role="status">{announcement}</span>
    </div>
  )
}
