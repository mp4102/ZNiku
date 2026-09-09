/** 几何相交与绘图工具。矩形边界允许通行，内部穿越才算碰撞。 */
import { getSmoothStepPath, Position } from '@xyflow/react'
import type { Bounds, GeometryNode, GeometryRoute, Point } from './types'

export const ROUTE_PADDING = 12
const EPSILON = 1e-7
export const samePoint = (a: Point, b: Point): boolean =>
  Math.abs(a.x - b.x) < EPSILON && Math.abs(a.y - b.y) < EPSILON
export const nodeBounds = (node: GeometryNode, margin = ROUTE_PADDING): Bounds => ({
  left: node.x - margin, right: node.x + node.width + margin,
  top: node.y - margin, bottom: node.y + node.height + margin,
})
export const boundsOverlap = (a: Bounds, b: Bounds): boolean =>
  a.left <= b.right && a.right >= b.left && a.top <= b.bottom && a.bottom >= b.top
export function pointBounds(points: readonly Point[]): Bounds {
  let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity
  for (const p of points) { left = Math.min(left, p.x); right = Math.max(right, p.x); top = Math.min(top, p.y); bottom = Math.max(bottom, p.y) }
  return { left, right, top, bottom }
}
/** Liang–Barsky 区间法，覆盖很薄的障碍，不能用少量像素采样代替。 */
export function segmentHits(a: Point, b: Point, box: Bounds): boolean {
  let low = 0, high = 1
  for (const [origin, delta, min, max] of [
    [a.x, b.x - a.x, box.left + EPSILON, box.right - EPSILON],
    [a.y, b.y - a.y, box.top + EPSILON, box.bottom - EPSILON],
  ]) {
    if (Math.abs(delta) < 1e-12) { if (origin < min || origin > max) return false }
    else {
      const t1 = (min - origin) / delta, t2 = (max - origin) / delta
      low = Math.max(low, Math.min(t1, t2)); high = Math.min(high, Math.max(t1, t2))
      if (low > high) return false
    }
  }
  return high > 0 && low < 1
}
export function simplify(points: readonly Point[]): Point[] {
  const result: Point[] = []
  for (const point of points) {
    if (result.length && samePoint(result[result.length - 1], point)) continue
    while (result.length > 1) {
      const a = result[result.length - 2], b = result[result.length - 1]
      if ((a.x === b.x && b.x === point.x && (b.y - a.y) * (point.y - b.y) >= 0) ||
          (a.y === b.y && b.y === point.y && (b.x - a.x) * (point.x - b.x) >= 0)) result.pop()
      else break
    }
    result.push(point)
  }
  return result
}
export const polylinePath = (points: readonly Point[]): string =>
  points.map((p, index) => `${index ? 'L' : 'M'}${p.x} ${p.y}`).join(' ')
export function fallbackPath(start: Point, end: Point): string {
  return getSmoothStepPath({ sourceX: start.x, sourceY: start.y, sourcePosition: Position.Right,
    targetX: end.x, targetY: end.y, targetPosition: Position.Left, borderRadius: 5, offset: 20 })[0]
}
/** 成功路径逐段审计：只豁免正确源/目标的水平出入口，回穿自己的卡片仍不合格。 */
export function routeCollisions(route: GeometryRoute, nodes: readonly GeometryNode[], source: string, target: string): string[] {
  return nodes.filter((node) => route.points.slice(1).some((point, index) => {
    const before = route.points[index]
    if (index === 0 && node.id === source && point.x >= before.x && point.y === before.y) return false
    if (index === route.points.length - 2 && node.id === target && point.x >= before.x && point.y === before.y) return false
    return segmentHits(before, point, nodeBounds(node))
  })).map((node) => node.id)
}

export interface RouteCrossing { readonly over: string; readonly under: string; readonly point: Point }
/** 正交线的严格内部交叉，不把端口或相切当连接；上层绘制白边/断口，不创造汇合圆点。 */
export function routeCrossings(routes: readonly GeometryRoute[], maxComparisons = 40000): RouteCrossing[] {
  const crossings: RouteCrossing[] = []
  let comparisons = 0
  for (let i = 0; i < routes.length; i += 1) for (let j = i + 1; j < routes.length; j += 1) {
    // 即使包围盒互不相交也计工作量；不能让大图在双重循环中绕过预算。
    if (++comparisons > maxComparisons) return crossings
    if (!boundsOverlap(routes[i].bounds, routes[j].bounds)) continue
    for (let a = 1; a < routes[i].points.length; a += 1) for (let b = 1; b < routes[j].points.length; b += 1) {
      if (++comparisons > maxComparisons) return crossings
      const p = routes[i].points[a - 1], q = routes[i].points[a], r = routes[j].points[b - 1], s = routes[j].points[b]
      const horizontal = p.y === q.y && r.x === s.x
      const vertical = p.x === q.x && r.y === s.y
      if (!horizontal && !vertical) continue
      const point = horizontal ? { x: r.x, y: p.y } : { x: p.x, y: r.y }
      const interior = (u: Point, v: Point): boolean => !samePoint(point, u) && !samePoint(point, v) &&
        point.x >= Math.min(u.x, v.x) && point.x <= Math.max(u.x, v.x) && point.y >= Math.min(u.y, v.y) && point.y <= Math.max(u.y, v.y)
      if (interior(p, q) && interior(r, s)) crossings.push({ over: routes[j].id, under: routes[i].id, point })
    }
  }
  return crossings
}

/** 仅剪开下穿边的可视 stroke，原几何与真实 Edge 不变；不新增连接圆点或持久走线点。 */
export function pathWithCrossingGaps(route: GeometryRoute, crossings: readonly RouteCrossing[], gap = 5): string {
  if (route.reason || route.points.length < 2 || gap <= 0) return route.path
  const cuts = crossings.filter((crossing) => crossing.under === route.id).map((crossing) => crossing.point)
  if (!cuts.length) return route.path
  const commands = [`M${route.points[0].x} ${route.points[0].y}`]
  for (let index = 1; index < route.points.length; index += 1) {
    const a = route.points[index - 1], b = route.points[index], horizontal = a.y === b.y
    const length = Math.abs(b.x - a.x) + Math.abs(b.y - a.y)
    const forward = horizontal ? Math.sign(b.x - a.x) : Math.sign(b.y - a.y)
    const intervals = cuts.filter((p) => horizontal
      ? p.y === a.y && p.x > Math.min(a.x, b.x) && p.x < Math.max(a.x, b.x)
      : p.x === a.x && p.y > Math.min(a.y, b.y) && p.y < Math.max(a.y, b.y))
      .map((p) => {
        const at = horizontal ? Math.abs(p.x - a.x) : Math.abs(p.y - a.y)
        return [Math.max(EPSILON, at - gap), Math.min(length - EPSILON, at + gap)]
      }).sort((u, v) => u[0] - v[0])
    const merged: number[][] = []
    for (const cut of intervals) {
      const previous = merged[merged.length - 1]
      if (previous && cut[0] <= previous[1]) previous[1] = Math.max(previous[1], cut[1])
      else merged.push([...cut])
    }
    const at = (distance: number): Point => horizontal ? { x: a.x + distance * forward, y: a.y } : { x: a.x, y: a.y + distance * forward }
    for (const [low, high] of merged) {
      const before = at(low), after = at(high)
      commands.push(`L${before.x} ${before.y}`, `M${after.x} ${after.y}`)
    }
    commands.push(`L${b.x} ${b.y}`)
  }
  return commands.join(' ')
}
