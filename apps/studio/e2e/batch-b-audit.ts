/** 独立审计最终可见 SVG；不导入生产路由器，不用稀疏采样掩盖薄障碍或圆角侵入。 */
export interface AuditPoint { readonly x: number; readonly y: number }
export interface AuditBox { readonly id: string; readonly left: number; readonly right: number; readonly top: number; readonly bottom: number }
export interface AuditSegment { readonly start: AuditPoint; readonly end: AuditPoint; readonly control?: AuditPoint }
export interface AuditRoute {
  readonly id: string; readonly source: string; readonly target: string; readonly path: string
  readonly start: AuditPoint; readonly end: AuditPoint
  readonly matrix: readonly [number, number, number, number, number, number]
  readonly status: string; readonly reason: string
}
const NUMBER = '[+-]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][+-]?\\d+)?'
const TOKEN = new RegExp(`[MLQ]|${NUMBER}`, 'g')

/** 只接受生产使用的绝对 M/L/Q。未知命令、缺参、空路径和非有限数直接使门禁失败。 */
export function pathSegments(path: string): AuditSegment[] {
  const tokens = [...path.matchAll(TOKEN)]
  let cursor = 0
  for (const token of tokens) {
    if (path.slice(cursor, token.index).replace(/[\s,]/g, '')) throw new Error('unsupported SVG path')
    cursor = token.index! + token[0].length
  }
  if (path.slice(cursor).replace(/[\s,]/g, '') || !tokens.length) throw new Error('empty or unsupported SVG path')
  const values = tokens.map((token) => token[0]), result: AuditSegment[] = []
  let index = 0, position: AuditPoint | null = null
  const point = (): AuditPoint => {
    const x = Number(values[index++]), y = Number(values[index++])
    if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error('invalid SVG coordinate')
    return { x, y }
  }
  while (index < values.length) {
    const command = values[index++]
    if (command === 'M') position = point()
    else if ((command === 'L' || command === 'Q') && position) {
      const control = command === 'Q' ? point() : undefined, end = point()
      result.push({ start: position, end, ...(control ? { control } : {}) }); position = end
    } else throw new Error('unsupported SVG command sequence')
  }
  if (!result.length) throw new Error('SVG contains no visible segment')
  return result
}

function roots(a: number, b: number, c: number): number[] {
  if (Math.abs(a) < 1e-12) return Math.abs(b) < 1e-12 ? [] : [-c / b]
  const discriminant = b * b - 4 * a * c
  if (discriminant < 0) return []
  return [(-b - Math.sqrt(discriminant)) / (2 * a), (-b + Math.sqrt(discriminant)) / (2 * a)]
}
function at(segment: AuditSegment, t: number): AuditPoint {
  const { start: a, end: b, control: c } = segment
  return c ? { x: (1 - t) ** 2 * a.x + 2 * (1 - t) * t * c.x + t * t * b.x,
    y: (1 - t) ** 2 * a.y + 2 * (1 - t) * t * c.y + t * t * b.y }
    : { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
}

/** 全部边界根切开参数区间；每段内外关系恒定，所以不会漏掉任意薄的穿越。 */
export function segmentIntersects(segment: AuditSegment, box: AuditBox, epsilon = 1e-7): boolean {
  const inside = (p: AuditPoint) => p.x > box.left + epsilon && p.x < box.right - epsilon &&
    p.y > box.top + epsilon && p.y < box.bottom - epsilon
  const cuts = [0, 1]
  for (const [axis, minimum, maximum] of [['x', box.left, box.right], ['y', box.top, box.bottom]] as const) {
    const a = segment.start[axis], b = segment.end[axis], c = segment.control?.[axis]
    for (const boundary of [minimum, maximum]) {
      const intersections = c === undefined ? roots(0, b - a, a - boundary) : roots(a - 2 * c + b, 2 * (c - a), a - boundary)
      cuts.push(...intersections.filter((value) => value > 0 && value < 1))
    }
  }
  cuts.sort((a, b) => a - b)
  return cuts.some((t) => inside(at(segment, t))) || cuts.slice(1).some((t, i) => inside(at(segment, (cuts[i]! + t) / 2)))
}

export function transformedSegments(route: Pick<AuditRoute, 'path' | 'matrix'>): AuditSegment[] {
  const [a, b, c, d, e, f] = route.matrix
  if (!route.matrix.every(Number.isFinite)) throw new Error('invalid SVG transformation')
  const transform = (p: AuditPoint) => ({ x: a * p.x + c * p.y + e, y: b * p.x + d * p.y + f })
  return pathSegments(route.path).map((segment) => ({ start: transform(segment.start), end: transform(segment.end),
    ...(segment.control ? { control: transform(segment.control) } : {}) }))
}
export interface RouteAudit { readonly id: string; readonly endpointError: number; readonly collisions: readonly string[]; readonly segments: number }
export function auditRoute(route: AuditRoute, nodes: readonly AuditBox[], padding: number): RouteAudit {
  const segments = transformedSegments(route)
  const first = segments[0]!, last = segments.at(-1)!
  const endpointError = Math.max(Math.hypot(first.start.x - route.start.x, first.start.y - route.start.y),
    Math.hypot(last.end.x - route.end.x, last.end.y - route.end.y))
  const collisions = nodes.filter((node) => segments.some((segment, index) => {
    // 只豁免正确出入端口对应的第一/最后水平朝右段；同节点的后续回穿仍被审计。
    const outward = !segment.control && Math.abs(segment.start.y - segment.end.y) < .02 && segment.end.x >= segment.start.x
    if (outward && ((node.id === route.source && index === 0) || (node.id === route.target && index === segments.length - 1))) return false
    return segmentIntersects(segment, { id: node.id, left: node.left - padding, right: node.right + padding,
      top: node.top - padding, bottom: node.bottom + padding }, .025)
  })).map((node) => node.id)
  return { id: route.id, endpointError, collisions, segments: segments.length }
}

/** 共线可读性指标：短的共同端口出口可豁免，但不把整条第一段当作合法总线。 */
export function collinearOverlap(routes: readonly AuditRoute[], sharedTerminalAllowance: number) {
  const visible = routes.filter((route) => route.status === 'routed').map((route) => ({ route, segments: transformedSegments(route) }))
  let rawMaximum = 0, nonterminalMaximum = 0, nonterminalCount = 0
  const near = (a: number, b: number) => Math.abs(a - b) < .025
  for (let i = 0; i < visible.length; i++) for (let j = i + 1; j < visible.length; j++) {
    const a = visible[i]!, b = visible[j]!
    for (const s of a.segments) for (const t of b.segments) {
      if (s.control || t.control) continue
      const horizontal = near(s.start.y, s.end.y) && near(t.start.y, t.end.y) && near(s.start.y, t.start.y)
      const vertical = near(s.start.x, s.end.x) && near(t.start.x, t.end.x) && near(s.start.x, t.start.x)
      if (!horizontal && !vertical) continue
      const axis = horizontal ? 'x' : 'y'
      const low = Math.max(Math.min(s.start[axis], s.end[axis]), Math.min(t.start[axis], t.end[axis]))
      const high = Math.min(Math.max(s.start[axis], s.end[axis]), Math.max(t.start[axis], t.end[axis]))
      if (high - low < .025) continue
      rawMaximum = Math.max(rawMaximum, high - low)
      let intervals = [[low, high]]
      const exempt = (left: number, right: number) => {
        intervals = intervals.flatMap(([l, r]) => [[l!, Math.min(r!, left)], [Math.max(l!, right), r!]].filter(([x, y]) => y! > x!))
      }
      if (horizontal) {
        if (a.route.source === b.route.source && near(a.route.start.x, b.route.start.x) && near(a.route.start.y, b.route.start.y) && near(s.start.y, a.route.start.y))
          exempt(a.route.start.x, a.route.start.x + sharedTerminalAllowance)
        if (a.route.target === b.route.target && near(a.route.end.x, b.route.end.x) && near(a.route.end.y, b.route.end.y) && near(s.start.y, a.route.end.y))
          exempt(a.route.end.x - sharedTerminalAllowance, a.route.end.x)
      }
      for (const [l, r] of intervals) if (r! - l! > .025) { nonterminalCount++; nonterminalMaximum = Math.max(nonterminalMaximum, r! - l!) }
    }
  }
  return { rawMaximum, nonterminalMaximum, nonterminalCount, sharedTerminalAllowance }
}
