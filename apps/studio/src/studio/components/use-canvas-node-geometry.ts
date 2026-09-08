/**
 * 保留 ReactFlow 的测量与拖动标记，避免位置投影更新把已测量节点重新隐藏。
 * 缓存仅属于当前画布，不保存位置、选区、参数或 Graph，也不产生 authoring mutation。
 * 卡片结构变化后重新测量；进度刷新、普通拖动和相同内容的新投影保留既有尺寸。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { NodeChange } from '@xyflow/react'
import type { WorkflowNode } from '../../model'

interface CanvasGeometry {
  readonly shape: string
  readonly measured?: { readonly width: number; readonly height: number }
  readonly dragging?: boolean
}

/** 纯 UI 结构比较，不是领域身份、执行签名或持久化 digest。 */
function geometryShape(node: WorkflowNode): string {
  const data = node.data
  return JSON.stringify([
    node.type, data.typeId, data.definitionVersion, data.executorKind,
    data.advanced, data.collapsed, data.groupLabel, data.label, data.summaries,
    data.inputs, data.outputs, data.portLabels,
  ])
}

export function useCanvasNodeGeometry(
  nodes: WorkflowNode[],
  editable: boolean,
  onNodesChange: (changes: NodeChange<WorkflowNode>[]) => void,
) {
  const [geometry, setGeometry] = useState<ReadonlyMap<string, CanvasGeometry>>(() => new Map())
  const shapes = useMemo(() => new Map(nodes.map((node) => [node.id, geometryShape(node)])), [nodes])

  useEffect(() => {
    // 删除、定义替换与只读视图切换不能遗留旧拖动标记；缓存大小始终不超过画布节点数。
    setGeometry((current) => {
      const next = new Map(current)
      for (const [id, value] of current) {
        if (shapes.get(id) !== value.shape) next.delete(id)
        else if (!editable && value.dragging) next.set(id, { ...value, dragging: false })
      }
      return next.size !== current.size || [...next].some(([id, value]) => current.get(id) !== value)
        ? next : current
    })
  }, [editable, shapes])

  const projectedNodes = useMemo(() => nodes.map((node): WorkflowNode => {
    const cached = geometry.get(node.id)
    if (!cached || cached.shape !== shapes.get(node.id)) return node
    // 位置与内容永远来自本次父级投影，因此相同 ID 的 current/snapshot 不会串用位置。
    return { ...node, ...(cached.measured ? { measured: cached.measured } : {}),
      dragging: editable && (cached.dragging ?? false) }
  }), [editable, geometry, nodes, shapes])

  const handleNodesChange = useCallback((changes: NodeChange<WorkflowNode>[]) => {
    setGeometry((current) => {
      let next: Map<string, CanvasGeometry> | null = null
      for (const change of changes) {
        if (change.type !== 'dimensions' && change.type !== 'position') continue
        const shape = shapes.get(change.id)
        if (shape === undefined) continue
        const previous = (next ?? current).get(change.id)
        const value: CanvasGeometry = previous?.shape === shape ? previous : { shape }
        let updated = value
        if (change.type === 'dimensions' && change.dimensions) {
          const { width, height } = change.dimensions
          if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0 &&
              (value.measured?.width !== width || value.measured?.height !== height)) {
            updated = { ...value, measured: { width, height } }
          }
        } else if (change.type === 'position' && typeof change.dragging === 'boolean') {
          const dragging = editable && change.dragging
          if (value.dragging !== dragging) updated = { ...value, dragging }
        }
        if (updated !== value) {
          next ??= new Map(current)
          next.set(change.id, updated)
        }
      }
      return next ?? current
    })
    // dimensions 不属于 Graph 编辑。其余意图原样交给唯一 authoring 路径，不新增保存/Undo。
    const authoringChanges = changes.filter((change) => change.type !== 'dimensions')
    if (authoringChanges.length > 0) onNodesChange(authoringChanges)
  }, [editable, onNodesChange, shapes])

  return { nodes: projectedNodes, onNodesChange: handleNodesChange }
}
