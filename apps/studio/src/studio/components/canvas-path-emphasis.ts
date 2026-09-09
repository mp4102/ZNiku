/** 路径追踪只返回显示集合；遍历有界于现有节点/边，不改变连接和有序输入。 */
import type { WorkflowEdge } from '../../model'

export interface PathFocus { readonly nodeId: string; readonly portId?: string; readonly direction?: 'input' | 'output' }
export type TraceMode = 'direct' | 'upstream' | 'downstream'
export function emphasizedEdges(edges: readonly WorkflowEdge[], focus: PathFocus | null, mode: TraceMode): ReadonlySet<string> {
  if (!focus) return new Set()
  const matches = (edge: WorkflowEdge) =>
    (edge.source === focus.nodeId && focus.direction !== 'input' && (!focus.portId || edge.sourceHandle === focus.portId)) ||
    (edge.target === focus.nodeId && focus.direction !== 'output' && (!focus.portId || edge.targetHandle === focus.portId))
  if (mode === 'direct') return new Set(edges.filter(matches).map((edge) => edge.id))
  const selected = new Set<string>(), visited = new Set<string>(), queue = [focus.nodeId]
  const adjacency = new Map<string, WorkflowEdge[]>()
  for (const edge of edges) {
    const id = mode === 'upstream' ? edge.target : edge.source
    const items = adjacency.get(id) ?? []
    items.push(edge); adjacency.set(id, items)
  }
  for (let index = 0; index < queue.length; index++) {
    const id = queue[index]
    if (visited.has(id)) continue
    visited.add(id)
    for (const edge of adjacency.get(id) ?? []) {
      if (index === 0 && focus.portId && !matches(edge)) continue
      selected.add(edge.id)
      queue.push(mode === 'upstream' ? edge.source : edge.target)
    }
  }
  return selected
}
