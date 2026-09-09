/** 真实障碍的有界正交通道搜索。预算不足返回可见当前端点降级，不拒绝合法大图。 */
import { fallbackPath, nodeBounds, pointBounds, polylinePath, ROUTE_PADDING, segmentHits, simplify } from './primitives'
import type { Bounds, GeometryEdge, GeometryGraph, GeometryResult, GeometryRoute, Point, RouteReason, RouterBudget, RouteStats } from './types'

export const DEFAULT_ROUTER_BUDGET: Readonly<RouterBudget> = Object.freeze({
  maxCells: 60000, maxExpanded: 12000, maxTotalExpanded: 60000,
  maxMs: 32, maxEdgeMs: 8, maxNodes: 2000, maxEdges: 3000,
})
export function routerBudget(options: Partial<RouterBudget> = {}): RouterBudget {
  // 只影响绘制的内部配置；错误配置立即报告开发错误，不能流入领域验证。
  const result = { ...DEFAULT_ROUTER_BUDGET, ...options }
  if (Object.keys(options).some((key) => !(key in DEFAULT_ROUTER_BUDGET)) ||
      Object.values(result).some((value) => !Number.isFinite(value) || value < 0 || value > 1e7)) throw new Error('E_ROUTER_BUDGET')
  return result
}
export const geometryKey = (graph: GeometryGraph): string => JSON.stringify([
  graph.nodes.map((n) => [n.id, n.x, n.y, n.width, n.height]),
  graph.edges.map((e) => [e.id, e.source, e.sourcePort, e.target, e.targetPort, e.ordinal, e.start.x, e.start.y, e.end.x, e.end.y]),
])

interface HeapItem { readonly id: number; readonly g: number; readonly f: number }
class Heap {
  readonly items: HeapItem[] = []
  private less(a: HeapItem, b: HeapItem): boolean { return a.f < b.f || (a.f === b.f && (a.g < b.g || (a.g === b.g && a.id < b.id))) }
  push(value: HeapItem): void {
    this.items.push(value)
    let index = this.items.length - 1
    while (index > 0) {
      const parent = (index - 1) >> 1
      if (!this.less(value, this.items[parent])) break
      this.items[index] = this.items[parent]; index = parent
    }
    this.items[index] = value
  }
  pop(): HeapItem {
    const result = this.items[0], last = this.items.pop()!
    if (this.items.length) {
      let index = 0
      while (index * 2 + 1 < this.items.length) {
        let child = index * 2 + 1
        if (child + 1 < this.items.length && this.less(this.items[child + 1], this.items[child])) child += 1
        if (!this.less(this.items[child], last)) break
        this.items[index] = this.items[child]; index = child
      }
      this.items[index] = last
    }
    return result
  }
}
export interface Portal {
  readonly edge: GeometryEdge
  readonly a: Point
  readonly b: Point
  readonly entry: Point
  readonly exit: Point
  readonly sourceLane: number
  readonly targetLane: number
}
export function geometryPortals(graph: GeometryGraph): Portal[] {
  const sources = new Map<string, number>(), targets = new Map<string, number>()
  return graph.edges.map((edge) => {
    const sourceKey = JSON.stringify([edge.source, edge.sourcePort]), targetKey = JSON.stringify([edge.target, edge.targetPort])
    const sourceLane = sources.get(sourceKey) ?? 0, targetLane = targets.get(targetKey) ?? 0
    sources.set(sourceKey, sourceLane + 1); targets.set(targetKey, targetLane + 1)
    // 分支出口与多来源汇入口分别分轨；不能只区分同一对端口的平行边。
    // 两侧沿固定 Edge 顺序计数，绝不排序/写回 ordinal 或制造 waypoint。
    const a = { x: edge.start.x + ROUTE_PADDING + sourceLane * 8, y: edge.start.y }
    const b = { x: edge.end.x - ROUTE_PADDING - targetLane * 8, y: edge.end.y }
    // 朝对端所在的纵向展开；同高固定向上。避免固定相反方向造成端口旁的短折返，
    // 也避免普通 fan-out 与 fan-in 的第一条支路天然共用同一中间行。
    const sourceDirection = edge.end.y > edge.start.y ? 1 : -1
    const targetDirection = edge.start.y > edge.end.y ? 1 : -1
    return { edge, a, b, entry: { x: a.x, y: a.y + sourceDirection * sourceLane * 10 },
      exit: { x: b.x, y: b.y + targetDirection * targetLane * 10 }, sourceLane, targetLane }
  })
}
interface Grid {
  readonly xs: number[]; readonly ys: number[]; readonly width: number; readonly height: number
  readonly horizontal: Uint8Array; readonly vertical: Uint8Array
  readonly xIndex: Map<number, number>; readonly yIndex: Map<number, number>
}
type GridResult = { readonly grid: Grid; readonly reason?: never } | { readonly reason: RouteReason; readonly grid?: never }
function createGrid(boxes: readonly Bounds[], paths: readonly Portal[], budget: RouterBudget, now: () => number, started: number): GridResult {
  const xx: number[] = [], yy: number[] = []
  for (const box of boxes) { xx.push(box.left, box.right); yy.push(box.top, box.bottom) }
  for (const path of paths) { xx.push(path.entry.x, path.exit.x); yy.push(path.entry.y, path.exit.y) }
  if (!xx.length) return { reason: 'channel_budget' }
  const bounds = pointBounds(xx.map((x, i) => ({ x, y: yy[i] })))
  xx.push(bounds.left - 48, bounds.right + 48); yy.push(bounds.top - 48, bounds.bottom + 48)
  const xs = [...new Set(xx)].sort((a, b) => a - b), ys = [...new Set(yy)].sort((a, b) => a - b)
  const width = xs.length, height = ys.length
  if (width * height > budget.maxCells) return { reason: 'channel_budget' }
  const xIndex = new Map(xs.map((value, index) => [value, index])), yIndex = new Map(ys.map((value, index) => [value, index]))
  const horizontal = new Uint8Array(width * height), vertical = new Uint8Array(width * height)
  for (const box of boxes) {
    if (now() - started > budget.maxMs) return { reason: 'time_budget' }
    const left = xIndex.get(box.left)!, right = xIndex.get(box.right)!, top = yIndex.get(box.top)!, bottom = yIndex.get(box.bottom)!
    for (let y = top + 1; y < bottom; y += 1) horizontal.fill(1, y * width + left, y * width + right)
    for (let y = top; y < bottom; y += 1) vertical.fill(1, y * width + left + 1, y * width + right)
  }
  return { grid: { xs, ys, width, height, horizontal, vertical, xIndex, yIndex } }
}
type SolveResult = { readonly points: Point[]; readonly expanded: number; readonly reason: null } |
  { readonly reason: RouteReason; readonly expanded: number; readonly points?: never }
function solve(grid: Grid, start: Point, end: Point, budget: RouterBudget, stats: RouteStats, graphStarted: number, now: () => number): SolveResult {
  const { xs, ys, width, height, horizontal, vertical, xIndex, yIndex } = grid
  const begin = yIndex.get(start.y)! * width + xIndex.get(start.x)!, finish = yIndex.get(end.y)! * width + xIndex.get(end.x)!
  const distance = new Float64Array(width * height).fill(Infinity), previous = new Int32Array(width * height).fill(-1)
  distance[begin] = 0
  const queue = new Heap()
  queue.push({ id: begin, g: 0, f: Math.abs(start.x - end.x) + Math.abs(start.y - end.y) })
  let expanded = 0
  const started = now()
  while (queue.items.length) {
    if (expanded >= budget.maxExpanded || stats.expanded >= budget.maxTotalExpanded) return { reason: 'work_budget', expanded }
    if ((expanded & 127) === 0 && (now() - started > budget.maxEdgeMs || now() - graphStarted > budget.maxMs)) return { reason: 'time_budget', expanded }
    const current = queue.pop()
    if (current.g !== distance[current.id]) continue
    expanded += 1; stats.expanded += 1
    if (current.id === finish) {
      const points: Point[] = []
      for (let at = finish; at >= 0; at = previous[at]) points.push({ x: xs[at % width], y: ys[Math.floor(at / width)] })
      return { points: points.reverse(), reason: null, expanded }
    }
    const x = current.id % width, y = Math.floor(current.id / width), neighbors: number[] = []
    if (x > 0 && !horizontal[current.id - 1]) neighbors.push(current.id - 1)
    if (x + 1 < width && !horizontal[current.id]) neighbors.push(current.id + 1)
    if (y > 0 && !vertical[current.id - width]) neighbors.push(current.id - width)
    if (y + 1 < height && !vertical[current.id]) neighbors.push(current.id + width)
    for (const target of neighbors) {
      const tx = xs[target % width], ty = ys[Math.floor(target / width)], g = current.g + Math.abs(tx - xs[x]) + Math.abs(ty - ys[y])
      if (g >= distance[target]) continue
      distance[target] = g; previous[target] = current.id
      queue.push({ id: target, g, f: g + Math.abs(tx - end.x) + Math.abs(ty - end.y) })
    }
  }
  return { reason: 'no_channel', expanded }
}
export function freshRouteStats(): RouteStats {
  return { cells: 0, expanded: 0, maxEdgeExpanded: 0, maxEdgeMs: 0, degraded: 0, cacheHits: 0, routed: 0, elapsedMs: 0 }
}
export function degradedRoute(edge: GeometryEdge, reason: RouteReason): GeometryRoute {
  return { id: edge.id, path: fallbackPath(edge.start, edge.end), points: [], reason, bounds: pointBounds([edge.start, edge.end]) }
}
/** selectedIds 只限制本次需重算的边；通道仍以完整当前图与完整平行轨道索引生成。 */
export function routeGraph(graph: GeometryGraph, options: Partial<RouterBudget> = {}, clock: () => number = () => performance.now(), selectedIds?: ReadonlySet<string>): GeometryResult {
  const budget = routerBudget(options), started = clock(), stats = freshRouteStats()
  const active = graph.edges.filter((edge) => !selectedIds || selectedIds.has(edge.id))
  if (!active.length) return { routes: [], stats }
  const valid = graph.nodes.every((node) => [node.x, node.y, node.width, node.height,
    node.x + node.width + ROUTE_PADDING, node.y + node.height + ROUTE_PADDING].every(Number.isFinite) && node.width > 0 && node.height > 0)
  const bounded = graph.nodes.length <= budget.maxNodes && graph.edges.length <= budget.maxEdges
  if (!valid || !bounded) {
    const routes = active.map((edge) => degradedRoute(edge, valid ? 'geometry_budget' : 'invalid_geometry'))
    stats.degraded = active.length; stats.routed = active.length; stats.elapsedMs = clock() - started
    return { routes, stats }
  }
  const paths = geometryPortals(graph), boxes = graph.nodes.map((node) => nodeBounds(node))
  const prepared = createGrid(boxes, paths, budget, clock, started)
  const grid = prepared.grid
  stats.cells = grid ? grid.width * grid.height : 0
  const routes = paths.filter((path) => !selectedIds || selectedIds.has(path.edge.id)).map((path): GeometryRoute => {
    const edgeStarted = clock(), { edge } = path
    let reason: RouteReason | null = prepared.reason ?? null
    const stubs: readonly [Point, Point, string | null][] = [
      [edge.start, path.a, edge.source], [path.a, path.entry, null], [path.exit, path.b, null], [path.b, edge.end, edge.target],
    ]
    if (stubs.some(([a, b, exempt]) => boxes.some((box, index) => graph.nodes[index].id !== exempt && segmentHits(a, b, box)))) reason = 'blocked_port'
    if (!reason && clock() - started > budget.maxMs) reason = 'time_budget'
    const solved: SolveResult = reason ? { reason, expanded: 0 } : solve(grid!, path.entry, path.exit, budget, stats, started, clock)
    stats.routed += 1; stats.maxEdgeExpanded = Math.max(stats.maxEdgeExpanded, solved.expanded)
    stats.maxEdgeMs = Math.max(stats.maxEdgeMs, clock() - edgeStarted)
    if (solved.reason) { stats.degraded += 1; return degradedRoute(edge, solved.reason) }
    const points = simplify([edge.start, path.a, ...solved.points, path.b, edge.end])
    return { id: edge.id, path: polylinePath(points), points, reason: null, bounds: pointBounds(points) }
  })
  stats.elapsedMs = clock() - started
  return { routes, stats }
}
