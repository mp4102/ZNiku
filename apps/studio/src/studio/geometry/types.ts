/** 会话内纯几何投影；没有参数、运行状态或持久化语义。坐标均为画布单位。 */
export interface Point { readonly x: number; readonly y: number }
export interface GeometryNode extends Point {
  readonly id: string
  readonly width: number
  readonly height: number
}
export interface GeometryEdge {
  readonly id: string
  readonly source: string
  readonly target: string
  readonly sourcePort: string
  readonly targetPort: string
  readonly start: Point
  readonly end: Point
  readonly ordinal: number | null
}
export interface GeometryGraph {
  readonly nodes: readonly GeometryNode[]
  readonly edges: readonly GeometryEdge[]
}
export interface Bounds { readonly left: number; readonly right: number; readonly top: number; readonly bottom: number }
export type RouteReason = 'blocked_port' | 'channel_budget' | 'work_budget' | 'time_budget' |
  'no_channel' | 'invalid_geometry' | 'geometry_budget'
export interface GeometryRoute {
  readonly id: string
  readonly path: string
  /** 正交成功路径的完整点列；降级仍有真实 SVG，但不伪装成避障成功点列。 */
  readonly points: readonly Point[]
  readonly reason: RouteReason | null
  readonly bounds: Bounds
}
export interface RouterBudget {
  readonly maxCells: number
  readonly maxExpanded: number
  readonly maxTotalExpanded: number
  readonly maxMs: number
  readonly maxEdgeMs: number
  readonly maxNodes: number
  readonly maxEdges: number
}
export interface RouteStats {
  cells: number
  expanded: number
  maxEdgeExpanded: number
  maxEdgeMs: number
  degraded: number
  cacheHits: number
  routed: number
  elapsedMs: number
}
export interface GeometryResult { readonly routes: readonly GeometryRoute[]; readonly stats: RouteStats }
