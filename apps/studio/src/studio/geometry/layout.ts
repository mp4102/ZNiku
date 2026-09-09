/** 显式整理布局：稳定依赖层级与真实尺寸，不重排边/ordinal，不自动保存或制造 Undo。 */
import type { GeometryNode, Point } from './types'

export function layoutMeasuredGraph(
  nodes: readonly GeometryNode[], edges: readonly { readonly source: string; readonly target: string }[],
): Readonly<Record<string, Point>> {
  const incoming = new Map(nodes.map((node) => [node.id, 0]))
  const next = new Map(nodes.map((node) => [node.id, new Set<string>()]))
  const rank = new Map(nodes.map((node) => [node.id, 0]))
  for (const edge of edges) {
    if (!next.has(edge.source) || !incoming.has(edge.target)) return Object.fromEntries(nodes.map((node) => [node.id, { x: node.x, y: node.y }]))
    if (!next.get(edge.source)!.has(edge.target)) {
      next.get(edge.source)!.add(edge.target)
      incoming.set(edge.target, incoming.get(edge.target)! + 1)
    }
  }
  const ready = nodes.filter((node) => !incoming.get(node.id)).map((node) => node.id)
  for (let index = 0; index < ready.length; index += 1) {
    const source = ready[index]
    for (const target of next.get(source)!) {
      rank.set(target, Math.max(rank.get(target)!, rank.get(source)! + 1))
      incoming.set(target, incoming.get(target)! - 1)
      if (!incoming.get(target)) ready.push(target)
    }
  }
  // Project 的合法性仍由 Python 判断；非法/未测量投影只能保留原位置，不能由几何层修补 Graph。
  if (ready.length !== nodes.length || nodes.some((n) => !Number.isFinite(n.width) || !Number.isFinite(n.height) || n.width <= 0 || n.height <= 0)) {
    return Object.fromEntries(nodes.map((node) => [node.id, { x: node.x, y: node.y }]))
  }
  const widths = new Map<number, number>()
  for (const node of nodes) widths.set(rank.get(node.id)!, Math.max(widths.get(rank.get(node.id)!) ?? 0, node.width))
  const columns = new Map<number, number>()
  let column = 80
  for (let index = 0; index < widths.size; index += 1) { columns.set(index, column); column += widths.get(index)! + 144 }
  const rows = new Map<number, number>()
  return Object.fromEntries(nodes.map((node) => {
    const layer = rank.get(node.id)!, row = rows.get(layer) ?? 100
    rows.set(layer, row + node.height + 64)
    return [node.id, { x: columns.get(layer)!, y: row }]
  }))
}
