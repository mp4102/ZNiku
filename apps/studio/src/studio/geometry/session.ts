/** 单视图有界路线缓存及 latest-only 票据；与 Project / Run authority 完全无关。 */
import { boundsOverlap, nodeBounds } from './primitives'
import { freshRouteStats, geometryKey, geometryPortals, routeGraph, routerBudget } from './router'
import type { GeometryGraph, GeometryNode, GeometryResult, GeometryRoute, RouterBudget } from './types'

export interface GeometryTicket { readonly sequence: number; readonly viewKey: string; readonly geometry: string }
interface CachedRoute { readonly signature: string; readonly route: GeometryRoute }
const nodeKey = (node: GeometryNode): string => JSON.stringify([node.x, node.y, node.width, node.height])
const transientReason = (route: GeometryRoute): boolean => route.reason === 'time_budget' || route.reason === 'work_budget' || route.reason === 'channel_budget' || route.reason === 'geometry_budget'

export class GeometrySession {
  private view: string | null = null
  private options = ''
  private nodes = new Map<string, GeometryNode>()
  private cache = new Map<string, CachedRoute>()
  private sequence = 0
  private current: GeometryTicket | null = null

  route(viewKey: string, graph: GeometryGraph, options: Partial<RouterBudget> = {}, clock: () => number = () => performance.now()): GeometryResult {
    const started = clock(), budget = routerBudget(options), optionKey = JSON.stringify(budget)
    if (viewKey !== this.view || optionKey !== this.options) { this.cache.clear(); this.nodes.clear() }
    this.view = viewKey; this.options = optionKey
    const currentNodes = new Map(graph.nodes.map((node) => [node.id, node]))
    const changed = new Set<string>(), changedBounds = []
    for (const [id, old] of this.nodes) {
      const next = currentNodes.get(id)
      if (!next || nodeKey(next) !== nodeKey(old)) { changed.add(id); changedBounds.push(nodeBounds(old)); if (next) changedBounds.push(nodeBounds(next)) }
    }
    for (const [id, next] of currentNodes) if (!this.nodes.has(id)) { changed.add(id); changedBounds.push(nodeBounds(next)) }
    const portals = geometryPortals(graph), dirty = new Set<string>(), signatures = new Map<string, string>()
    for (const { edge, sourceLane, targetLane } of portals) {
      // 展开方向只由两端 y 决定，端点与两侧 lane 一起签名即包含全部派生通道输入。
      const signature = JSON.stringify([edge.id, edge.source, edge.sourcePort, edge.target, edge.targetPort,
        edge.ordinal, edge.start.x, edge.start.y, edge.end.x, edge.end.y, sourceLane, targetLane]), cached = this.cache.get(edge.id)
      signatures.set(edge.id, signature)
      if (!cached || cached.signature !== signature || transientReason(cached.route) || changed.has(edge.source) || changed.has(edge.target) ||
          (changed.size > 0 && cached.route.reason !== null) || changedBounds.some((box) => boundsOverlap(box, cached.route.bounds))) dirty.add(edge.id)
    }
    const solved = dirty.size ? routeGraph(graph, budget, clock, dirty) : { routes: [], stats: freshRouteStats() }
    const results = new Map(solved.routes.map((route) => [route.id, route]))
    const routes = graph.edges.map((edge) => results.get(edge.id) ?? this.cache.get(edge.id)!.route)
    // 仅保留当前图且有固定缓存上限；大图仍绘制，但不会留下无界历史几何。
    this.cache = new Map(routes.slice(0, budget.maxEdges).map((route) => [route.id, { route, signature: signatures.get(route.id)! }]))
    this.nodes = graph.nodes.length <= budget.maxNodes ? new Map(graph.nodes.map((node) => [node.id, { ...node }])) : new Map()
    return { routes, stats: { ...solved.stats, cacheHits: graph.edges.length - dirty.size,
      degraded: routes.filter((route) => route.reason !== null).length, elapsedMs: clock() - started } }
  }
  issue(viewKey: string, graph: GeometryGraph): GeometryTicket {
    this.current = Object.freeze({ sequence: ++this.sequence, viewKey, geometry: geometryKey(graph) })
    return this.current
  }
  accept(ticket: GeometryTicket | null | undefined, viewKey: string, graph: GeometryGraph): boolean {
    return ticket != null && ticket === this.current && ticket.viewKey === viewKey && ticket.geometry === geometryKey(graph)
  }
  cancel(): void { this.sequence += 1; this.current = null }
  clear(): void { this.cancel(); this.cache.clear(); this.nodes.clear(); this.view = null; this.options = '' }
}
