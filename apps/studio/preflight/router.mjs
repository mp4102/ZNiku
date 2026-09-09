/** 有界正交通道试验；仅返回几何，不生成或修改 Graph/Run/StudioState。 */
import { performance } from 'node:perf_hooks'
import { endpoints, PADDING, polylinePath, rect, samePoint, segmentHits, smoothPath, validateGeometry } from './geometry.mjs'

export const DEFAULT_BUDGET = Object.freeze({ maxCells: 60000, maxExpanded: 40000,
  maxTotalExpanded: 400000, maxMs: 1000, maxEdgeMs: 100 })

function config(options) {
  if (!options || Object.getPrototypeOf(options) !== Object.prototype || Object.keys(options).some((key) =>
    !Object.hasOwn(DEFAULT_BUDGET, key))) throw new Error('E_ROUTER_OPTIONS')
  const result = { ...DEFAULT_BUDGET, ...options }
  if (Object.values(result).some((value) => !Number.isSafeInteger(value) || value < 1 || value > 1e7)) {
    throw new Error('E_ROUTER_BUDGET')
  }
  return result
}

class Heap {
  items = []
  less(a, b) { return a.f < b.f || (a.f === b.f && (a.g < b.g || (a.g === b.g && a.id < b.id))) }
  push(value) {
    this.items.push(value)
    let index = this.items.length - 1
    while (index > 0) {
      const parent = (index - 1) >> 1
      if (!this.less(value, this.items[parent])) break
      this.items[index] = this.items[parent]; index = parent
    }
    this.items[index] = value
  }
  pop() {
    const result = this.items[0], last = this.items.pop()
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

function portals(graph) {
  const grouped = new Map()
  return graph.edges.map((edge) => {
    const [start, end] = endpoints(graph, edge)
    const key = JSON.stringify([edge.source, edge.sourcePort, edge.target, edge.targetPort])
    const lane = grouped.get(key) ?? 0
    grouped.set(key, lane + 1)
    // 自动分轨只属于本次派生路径，不是用户 waypoint，不改变 ordinal。
    const a = { x: start.x + PADDING + lane * 8, y: start.y }
    const b = { x: end.x - PADDING - lane * 8, y: end.y }
    const entry = { x: a.x, y: a.y + lane * 10 }
    const exit = { x: b.x, y: b.y + lane * 10 }
    return { edge, start, end, a, b, entry, exit }
  })
}

function createGrid(boxes, paths, budget, now, started) {
  const unique = (values) => [...new Set(values)].sort((a, b) => a - b)
  const xx = boxes.flatMap((box) => [box.left, box.right])
  const yy = boxes.flatMap((box) => [box.top, box.bottom])
  for (const path of paths) { xx.push(path.entry.x, path.exit.x); yy.push(path.entry.y, path.exit.y) }
  if (!xx.length) return { reason: 'channel_budget' }
  xx.push(Math.min(...xx) - 48, Math.max(...xx) + 48)
  yy.push(Math.min(...yy) - 48, Math.max(...yy) + 48)
  const xs = unique(xx), ys = unique(yy), width = xs.length, height = ys.length
  if (width * height > budget.maxCells) return { reason: 'channel_budget' }
  const xIndex = new Map(xs.map((value, index) => [value, index]))
  const yIndex = new Map(ys.map((value, index) => [value, index]))
  const horizontal = new Uint8Array(width * height), vertical = new Uint8Array(width * height)
  for (const box of boxes) {
    if (now() - started > budget.maxMs) return { reason: 'time_budget' }
    const left = xIndex.get(box.left), right = xIndex.get(box.right)
    const top = yIndex.get(box.top), bottom = yIndex.get(box.bottom)
    for (let y = top + 1; y < bottom; y += 1) horizontal.fill(1, y * width + left, y * width + right)
    for (let y = top; y < bottom; y += 1) vertical.fill(1, y * width + left + 1, y * width + right)
  }
  return { xs, ys, width, height, horizontal, vertical, xIndex, yIndex }
}

function solve(grid, start, end, budget, stats, graphStarted, now) {
  const { xs, ys, width, height, horizontal, vertical, xIndex, yIndex } = grid
  const begin = yIndex.get(start.y) * width + xIndex.get(start.x)
  const finish = yIndex.get(end.y) * width + xIndex.get(end.x)
  const distance = new Float64Array(width * height).fill(Infinity)
  const previous = new Int32Array(width * height).fill(-1)
  distance[begin] = 0
  const queue = new Heap()
  queue.push({ id: begin, g: 0, f: Math.abs(start.x - end.x) + Math.abs(start.y - end.y) })
  let expanded = 0
  const started = now()
  while (queue.items.length) {
    if (expanded >= budget.maxExpanded || stats.expanded >= budget.maxTotalExpanded) return { reason: 'work_budget', expanded }
    if ((expanded & 127) === 0 && (now() - started > budget.maxEdgeMs || now() - graphStarted > budget.maxMs)) {
      return { reason: 'time_budget', expanded }
    }
    const current = queue.pop()
    if (current.g !== distance[current.id]) continue
    expanded += 1; stats.expanded += 1
    if (current.id === finish) {
      const points = []
      for (let at = finish; at >= 0; at = previous[at]) points.push({ x: xs[at % width], y: ys[Math.floor(at / width)] })
      return { points: points.reverse(), reason: null, expanded }
    }
    const x = current.id % width, y = Math.floor(current.id / width)
    const neighbors = []
    if (x > 0 && !horizontal[current.id - 1]) neighbors.push(current.id - 1)
    if (x + 1 < width && !horizontal[current.id]) neighbors.push(current.id + 1)
    if (y > 0 && !vertical[current.id - width]) neighbors.push(current.id - width)
    if (y + 1 < height && !vertical[current.id]) neighbors.push(current.id + width)
    for (const target of neighbors) {
      const tx = xs[target % width], ty = ys[Math.floor(target / width)]
      const g = current.g + Math.abs(tx - xs[x]) + Math.abs(ty - ys[y])
      if (g >= distance[target]) continue
      distance[target] = g; previous[target] = current.id
      queue.push({ id: target, g, f: g + Math.abs(tx - end.x) + Math.abs(ty - end.y) })
    }
  }
  return { reason: 'no_channel', expanded }
}

function simplify(points) {
  const result = []
  for (const point of points) {
    if (result.length && samePoint(result.at(-1), point)) continue
    while (result.length > 1) {
      const a = result.at(-2), b = result.at(-1)
      if ((a.x === b.x && b.x === point.x && (b.y - a.y) * (point.y - b.y) >= 0) ||
          (a.y === b.y && b.y === point.y && (b.x - a.x) * (point.x - b.x) >= 0)) result.pop()
      else break
    }
    result.push(point)
  }
  return result
}

export function routeGraph(graph, options = {}, clock = performance.now.bind(performance)) {
  validateGeometry(graph)
  const budget = config(options), started = clock(), paths = portals(graph), boxes = graph.nodes.map((node) => rect(node))
  const prepared = createGrid(boxes, paths, budget, clock, started)
  const grid = prepared.reason ? null : prepared
  const stats = { cells: grid ? grid.width * grid.height : 0, expanded: 0, maxEdgeExpanded: 0,
    maxEdgeMs: 0, degraded: 0, cacheHits: 0, elapsedMs: 0 }
  const routes = paths.map((path) => {
    const edgeStarted = clock()
    let reason = grid ? null : prepared.reason
    const stubSegments = [[path.start, path.a, path.edge.source], [path.a, path.entry, null],
      [path.exit, path.b, null], [path.b, path.end, path.edge.target]]
    if (stubSegments.some(([a, b, exempt]) => boxes.some((box, index) =>
      graph.nodes[index].id !== exempt && segmentHits(a, b, box)))) reason = 'blocked_port'
    const solved = reason ? { reason, expanded: 0 } : solve(grid, path.entry, path.exit, budget, stats, started, clock)
    stats.maxEdgeExpanded = Math.max(stats.maxEdgeExpanded, solved.expanded)
    stats.maxEdgeMs = Math.max(stats.maxEdgeMs, clock() - edgeStarted)
    if (solved.reason) {
      stats.degraded += 1
      return { id: path.edge.id, path: smoothPath(path.start, path.end), reason: solved.reason }
    }
    return { id: path.edge.id, path: polylinePath(simplify([path.start, path.a, ...solved.points, path.b, path.end])), reason: null }
  })
  stats.elapsedMs = clock() - started
  return { routes, stats }
}

/** 有界单项缓存：无几何变化的轮询命中；任何障碍变化使非相邻边也失效。 */
export class GeometrySession {
  cached = null
  sequence = 0
  current = null
  route(graph, options = {}) {
    validateGeometry(graph); config(options)
    const key = JSON.stringify([graph, options])
    if (this.cached?.key === key) return { routes: structuredClone(this.cached.result.routes),
      stats: { ...this.cached.result.stats, cacheHits: graph.edges.length, elapsedMs: 0, expanded: 0, maxEdgeExpanded: 0, maxEdgeMs: 0 } }
    const result = routeGraph(graph, options)
    this.cached = { key, result: structuredClone(result) }
    return result
  }
  issue(viewKey, graph) {
    if (typeof viewKey !== 'string' || !viewKey || viewKey.length > 200) throw new Error('E_VIEW_KEY')
    validateGeometry(graph)
    this.current = Object.freeze({ sequence: ++this.sequence, viewKey, geometry: JSON.stringify(graph) })
    return this.current
  }
  accept(ticket, graph) {
    return ticket !== null && ticket !== undefined && ticket === this.current &&
      ticket.geometry === JSON.stringify(validateGeometry(graph))
  }
  cancel() { this.sequence += 1; this.current = null }
}
