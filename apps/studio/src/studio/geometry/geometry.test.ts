import { describe, expect, it } from 'vitest'
import {
  GeometrySession, boundsOverlap, geometryKey, geometryPortals, layoutMeasuredGraph, nodeBounds, pointBounds,
  pathWithCrossingGaps, polylinePath, routeCollisions, routeCrossings, routeGraph, segmentHits, simplify,
  type GeometryEdge, type GeometryGraph, type GeometryNode, type GeometryRoute,
} from './index'

const generous = { maxMs: 1000, maxEdgeMs: 100 }
const node = (id: string, x = 0, y = 0, width = 240, height = 180): GeometryNode => ({ id, x, y, width, height })
const edge = (source: GeometryNode, target: GeometryNode, id = 'edge', ordinal: number | null = null, port = 0): GeometryEdge => ({
  id, source: source.id, target: target.id, sourcePort: `out-${port}`, targetPort: `in-${port}`, ordinal,
  start: { x: source.x + source.width, y: source.y + 60 + port * 24 }, end: { x: target.x, y: target.y + 60 + port * 24 },
})
function graph(nodes: GeometryNode[], connections: [string, string][], arrange = false): GeometryGraph {
  const pairs = connections.map(([source, target]) => ({ source, target }))
  const positions = arrange ? layoutMeasuredGraph(nodes, pairs) : null
  const measured = nodes.map((n) => ({ ...n, ...(positions?.[n.id] ?? {}) }))
  const byId = new Map(measured.map((n) => [n.id, n]))
  return { nodes: measured, edges: connections.map(([source, target], i) => edge(byId.get(source)!, byId.get(target)!, `e${i}`)) }
}
function assertClear(value: GeometryGraph): void {
  const before = JSON.stringify(value), solved = routeGraph(value, generous)
  expect(solved.routes).toHaveLength(value.edges.length)
  expect(solved.stats.degraded).toBe(0)
  for (const route of solved.routes) {
    const binding = value.edges.find((e) => e.id === route.id)!
    expect(route.points[0]).toEqual(binding.start)
    expect(route.points.at(-1)).toEqual(binding.end)
    expect(routeCollisions(route, value.nodes, binding.source, binding.target)).toEqual([])
    expect(route.path.startsWith('M')).toBe(true)
    expect(route.points.slice(1).every((p, i) => p.x === route.points[i].x || p.y === route.points[i].y)).toBe(true)
    expect(route.points.slice(2).some((c, i) => {
      const a = route.points[i], b = route.points[i + 1]
      return (a.x === b.x && b.x === c.x && (b.y - a.y) * (c.y - b.y) < 0) ||
        (a.y === b.y && b.y === c.y && (b.x - a.x) * (c.x - b.x) < 0)
    })).toBe(false)
  }
  expect(JSON.stringify(value)).toBe(before)
  expect(routeGraph(value, generous).routes).toEqual(solved.routes)
}

describe('生产几何 G01–G10：独立于 Project 与 Runtime', () => {
  it('G01 稳定单链整理，真实宽高累计且无障碍相交', () => {
    const value = graph(['s', 'a', 'b', 'o'].map((id) => node(id)), [['s', 'a'], ['a', 'b'], ['b', 'o']], true)
    assertClear(value)
    expect(value.nodes.map((n) => n.x)).toEqual([80, 464, 848, 1232])
  })
  it('G02 跨层长边绕过无关障碍而不是改写节点', () => {
    const value = graph([node('s'), node('m', 400), node('t', 800)], [['s', 't']])
    assertClear(value)
    expect(routeGraph(value, generous).routes[0].points.length).toBeGreaterThan(2)
  })
  it('G03 两分支多层汇合保持边数及源目标', () => {
    const value = graph(['s', 'a', 'b', 'c', 'd', 'e', 'f', 'o'].map((id) => node(id)),
      [['s', 'a'], ['s', 'b'], ['a', 'c'], ['b', 'c'], ['b', 'd'], ['c', 'e'], ['d', 'e'], ['e', 'f'], ['f', 'o'], ['a', 'f']], true)
    assertClear(value)
    const { routes } = routeGraph(value, generous)
    // 此标准夹具共享端口短 stub 最长 20，不能把长边中段画成未声明的汇合总线。
    for (let i = 0; i < routes.length; i += 1) for (let j = i + 1; j < routes.length; j += 1) {
      for (let a = 1; a < routes[i].points.length; a += 1) for (let b = 1; b < routes[j].points.length; b += 1) {
        const p = routes[i].points[a - 1], q = routes[i].points[a], r = routes[j].points[b - 1], s = routes[j].points[b]
        if (p.y === q.y && r.y === s.y && p.y === r.y) {
          expect(Math.min(Math.max(p.x, q.x), Math.max(r.x, s.x)) - Math.max(Math.min(p.x, q.x), Math.min(r.x, s.x))).toBeLessThanOrEqual(24)
        } else if (p.x === q.x && r.x === s.x && p.x === r.x) {
          expect(Math.min(Math.max(p.y, q.y), Math.max(r.y, s.y)) - Math.max(Math.min(p.y, q.y), Math.min(r.y, s.y))).toBeLessThanOrEqual(24)
        }
      }
    }
  })
  it('G03 同输出分支及不同来源汇入同输入分别保留独立出口/入口通道', () => {
    const value = graph(['s', 'a', 'b', 't'].map((id) => node(id)), [['s', 'a'], ['s', 'b'], ['a', 't'], ['b', 't']], true)
    const portals = geometryPortals(value)
    expect(portals.map((p) => [p.sourceLane, p.targetLane])).toEqual([[0, 0], [1, 0], [0, 0], [0, 1]])
    expect(portals[0].entry).not.toEqual(portals[1].entry)
    expect(portals[2].exit).not.toEqual(portals[3].exit)
    assertClear(value)
  })
  it('G03 删除分支/汇合边使另一个目标/来源的lane缓存失效，不改ordinal', () => {
    const value = graph(['s', 'a', 'b', 't'].map((id) => node(id)), [['s', 'a'], ['s', 'b'], ['a', 't'], ['b', 't']], true)
    const session = new GeometrySession()
    const first = session.route('current', value, generous)
    const removed = { ...value, edges: value.edges.filter((e) => e.id !== 'e0' && e.id !== 'e2') }
    const result = session.route('current', removed, generous)
    expect(result.stats.cacheHits).toBe(0)
    expect(result.routes).toEqual(routeGraph(removed, generous).routes)
    expect(result.routes[0].path).not.toEqual(first.routes[1].path)
    expect(result.routes[1].path).not.toEqual(first.routes[3].path)
    expect(removed.edges.map((e) => e.ordinal)).toEqual([null, null])
  })
  it.each(['source', 'target'] as const)('G03 %s侧lane朝对端展开，端点跨越同高线会使缓存方向失效', (side) => {
    const value = side === 'source'
      ? graph([node('s'), node('a', 600), node('b', 600, 300)], [['s', 'a'], ['s', 'b']])
      : graph([node('a'), node('b', 0, 300), node('t', 600)], [['a', 't'], ['b', 't']])
    const session = new GeometrySession()
    session.route('current', value, generous)
    const first = geometryPortals(value)[1]
    expect(side === 'source' ? first.entry.y : first.exit.y).toBe(70)
    const nodes = value.nodes.map((n) => n.id === 'b' ? { ...n, y: -300 } : n)
    const moved = { nodes, edges: value.edges.map((e) => ({ ...e,
      ...(side === 'source' && e.target === 'b' ? { end: { ...e.end, y: -240 } } : {}),
      ...(side === 'target' && e.source === 'b' ? { start: { ...e.start, y: -240 } } : {}),
    })) }
    const second = geometryPortals(moved)[1]
    expect(side === 'source' ? second.entry.y : second.exit.y).toBe(50)
    const result = session.route('current', moved, generous)
    expect(result.stats.cacheHits).toBe(1)
    expect(result.routes[1]).toEqual(routeGraph(moved, generous).routes[1])
    assertClear(moved)
  })
  it.each([
    [['s1', 's2', 'm', 'o1', 'o2'], [['s1', 'm'], ['s2', 'm'], ['m', 'o1'], ['m', 'o2']]],
    [['s', 'a'], [['s', 'a']]],
  ])('G04 多来源/多输出/零领域输出不增加全局限制：%j', (ids, connections) => {
    assertClear(graph((ids as string[]).map((id) => node(id)), connections as [string, string][], true))
  })
  it.each([1, 2, 6, 8, 16, 24])('G05 %i 个真实行端口不受试验16上限约束', (count) => {
    const nodes = [node('s', 0, 0, 260, 80 + count * 24), node('t', 540, 0, 260, 80 + count * 24)]
    const value = { nodes, edges: Array.from({ length: count }, (_, i) => edge(nodes[0], nodes[1], `e${i}`, null, i)) }
    assertClear(value)
    for (const e of value.edges) expect(e.start.y).toBeLessThan(nodes[0].height)
  })
  it.each([88, 560])('G06 高度%i的卡片累计布局不重叠', (height) => {
    const value = graph([node('s', 0, 0, 290, height), node('a', 0, 0, 300, height), node('b', 0, 0, 340, height), node('o')],
      [['s', 'a'], ['s', 'b'], ['a', 'o'], ['b', 'o']], true)
    assertClear(value)
    for (let i = 0; i < value.nodes.length; i += 1) for (let j = i + 1; j < value.nodes.length; j += 1) {
      expect(boundsOverlap(nodeBounds(value.nodes[i], 0), nodeBounds(value.nodes[j], 0))).toBe(false)
    }
  })
  it('G07 无关障碍移入旧线包围盒必须重算，远处边命中细粒度缓存', () => {
    const session = new GeometrySession()
    const before = graph([node('s'), node('m', 400, 380), node('t', 800), node('x', 0, 1000), node('y', 800, 1000)], [['s', 't'], ['x', 'y']])
    const first = session.route('current', before, generous)
    const moved = { ...before, nodes: before.nodes.map((n) => n.id === 'm' ? { ...n, y: 0 } : n) }
    const after = session.route('current', moved, generous)
    expect(after.stats.cacheHits).toBe(1)
    expect(after.stats.routed).toBe(1)
    expect(after.routes[0].path).not.toEqual(first.routes[0].path)
    expect(after.routes[1]).toBe(first.routes[1])
    expect(routeCollisions(after.routes[0], moved.nodes, 's', 't')).toEqual([])
    const removed = session.route('current', { ...moved, nodes: moved.nodes.filter((n) => n.id !== 'm') }, generous)
    expect(removed.stats.cacheHits).toBe(1)
    expect(removed.routes[0].path).toEqual(first.routes[0].path)
  })
  it('G08 平行边轨道可辨且不排序/更改 ordered_many ordinal', () => {
    const nodes = [node('s'), node('t', 620)]
    const value = { nodes, edges: [3, 1, 0, 2].map((ordinal) => edge(nodes[0], nodes[1], `e${ordinal}`, ordinal)) }
    assertClear(value)
    const result = routeGraph(value, generous)
    expect(new Set(result.routes.map((r) => r.path)).size).toBe(4)
    expect(value.edges.map((e) => e.ordinal)).toEqual([3, 1, 0, 2])
    const session = new GeometrySession()
    session.route('current', value, generous)
    const fewer = { ...value, edges: value.edges.slice(1) }
    const updated = session.route('current', fewer, generous)
    expect(updated.stats.routed).toBe(3)
    expect(updated.routes).toEqual(routeGraph(fewer, generous).routes)
  })
  it.each([node('m', 150), node('m', 245, -50, 80, 300)])('G09 端口受阻仍用当前端点可见降级', (obstacle) => {
    const value = graph([node('s'), obstacle, node('t', 700)], [['s', 't']])
    const result = routeGraph(value, generous)
    expect(result.routes[0].reason).toBe('blocked_port')
    expect(result.routes[0].path).toMatch(/^M240 60/)
    expect(result.routes[0].path).toMatch(/700 60$/)
    expect(result.routes[0].points).toEqual([])
  })
  it.each([
    [{ maxExpanded: 1 }, 'work_budget'], [{ maxTotalExpanded: 0 }, 'work_budget'],
    [{ maxCells: 1 }, 'channel_budget'], [{ maxNodes: 1 }, 'geometry_budget'], [{ maxEdges: 0 }, 'geometry_budget'],
  ] as const)('G09 硬预算%s不删除边且报告%s', (budget, reason) => {
    const value = graph([node('s'), node('m', 400), node('t', 800)], [['s', 't']])
    const result = routeGraph(value, { ...generous, ...budget })
    expect(result.routes[0].reason).toBe(reason)
    expect(result.routes[0].path).toMatch(/^M240 60/)
    expect(result.routes[0].path).toMatch(/800 60$/)
  })
  it('G09 注入时钟耗尽后剩余全部正确端点降级，下一次有预算可重试', () => {
    const value = graph([node('s'), node('a', 500), node('b', 1000)], [['s', 'a'], ['a', 'b']])
    const session = new GeometrySession()
    let ticks = 0
    const timed = session.route('current', value, { maxMs: 1 }, () => ticks += 10)
    expect(timed.routes.every((r) => r.reason === 'time_budget')).toBe(true)
    const retried = session.route('current', value, { maxMs: 1 }, () => 0)
    expect(retried.stats.cacheHits).toBe(0)
    expect(retried.stats.degraded).toBe(0)
  })
  it.each([50, 200, 220])('G10 %i节点不将试验200限制变为合法图限制', (count) => {
    const nodes = Array.from({ length: count }, (_, i) => node(`n${i}`, Math.floor(i / 10) * 440, (i % 10) * 270, 220 + (i % 3) * 24, 160 + (i % 4) * 20))
    const pairs: [string, string][] = []
    for (let i = 10; i < count; i += 1) pairs.push([`n${i - 10}`, `n${i}`])
    const value = graph(nodes, pairs, true)
    assertClear(value)
    const session = new GeometrySession()
    session.route('current', value, generous)
    const cached = session.route('current', structuredClone(value), generous)
    expect(cached.stats.cacheHits).toBe(value.edges.length)
    expect(cached.stats.expanded).toBe(0)
    expect(cached.stats.cells).toBe(0)
  })
})

describe('几何缓存、票据和线段审计', () => {
  it('无几何进度/摘要字段更新不求解也不改变几何签名', () => {
    const value = graph([node('s'), node('t', 600)], [['s', 't']])
    const session = new GeometrySession()
    session.route('current', value, generous)
    const projected = { nodes: value.nodes.map((n) => ({ ...n, progress: 0.6 })), edges: value.edges.map((e) => ({ ...e, selected: true })) }
    expect(geometryKey(projected)).toBe(geometryKey(value))
    expect(session.route('current', projected, generous).stats.cacheHits).toBe(1)
  })
  it('移动端口/改变真实尺寸即使包围盒不碰旧边也失效', () => {
    const value = graph([node('s'), node('t', 600)], [['s', 't']]), session = new GeometrySession()
    session.route('current', value, generous)
    const anchorChanged = { ...value, edges: value.edges.map((e) => ({ ...e, start: { ...e.start, y: 90 } })) }
    const result = session.route('current', anchorChanged, generous)
    expect(result.stats.routed).toBe(1)
    expect(result.routes[0].points[0].y).toBe(90)
    const heightChanged = { ...anchorChanged, nodes: value.nodes.map((n) => ({ ...n, height: 250 })) }
    expect(session.route('current', heightChanged, generous).stats.routed).toBe(1)
  })
  it('真实 handle 左右外缘及分数测量仍精确贴端点、绕过无关卡片', () => {
    const value = graph([node('s', 0.25, 0.5), node('obstacle', 400.25, 0.5), node('t', 800.25, 0.5)], [['s', 't']])
    const outerAnchors = { ...value, edges: value.edges.map((e) => ({ ...e,
      start: { x: e.start.x + 5.5, y: e.start.y + 0.125 }, end: { x: e.end.x - 5.5, y: e.end.y + 0.125 } })) }
    assertClear(outerAnchors)
    expect(routeGraph(outerAnchors, generous).routes[0].points[0]).toEqual({ x: 245.75, y: 60.625 })
  })
  it('同一位置的端口结构变化也更新边绑定，不能复用旧端口缓存', () => {
    const value = graph([node('s'), node('t', 600)], [['s', 't']]), session = new GeometrySession()
    session.route('current', value, generous)
    const renamed = { ...value, edges: value.edges.map((e) => ({ ...e, targetPort: 'different-input' })) }
    expect(session.route('current', renamed, generous).stats.cacheHits).toBe(0)
    const ticket = session.issue('current', value)
    expect(session.accept(ticket, 'current', renamed)).toBe(false)
  })
  it('不相关障碍在远处移动全部命中缓存', () => {
    const value = graph([node('s'), node('t', 600), node('far', 1000, 1000)], [['s', 't']]), session = new GeometrySession()
    session.route('current', value, generous)
    const moved = { ...value, nodes: value.nodes.map((n) => n.id === 'far' ? { ...n, x: 1500 } : n) }
    expect(session.route('current', moved, generous).stats.cacheHits).toBe(1)
  })
  it('同 node_id 不同 current/snapshot视图不复用旧路线/票据，旧请求及取消全部拒绝', () => {
    const value = graph([node('s'), node('t', 600)], [['s', 't']]), session = new GeometrySession()
    const first = session.issue('current', value), newer = session.issue('current', value)
    expect(session.accept(first, 'current', value)).toBe(false)
    expect(session.accept(newer, 'current', value)).toBe(true)
    expect(session.accept({ ...newer }, 'current', value)).toBe(false)
    expect(session.accept(newer, 'history', value)).toBe(false)
    expect(session.accept(null, 'current', value)).toBe(false)
    session.cancel()
    expect(session.accept(newer, 'current', value)).toBe(false)
    session.route('current', value, generous)
    expect(session.route('history', value, generous).stats.cacheHits).toBe(0)
    session.clear()
    expect(session.route('history', value, generous).stats.cacheHits).toBe(0)
  })
  it('几何版本改变拒绝迟到结果，取消整理仅票据失效不会提交任何位置', () => {
    const value = graph([node('s'), node('t', 600)], [['s', 't']]), session = new GeometrySession()
    const ticket = session.issue('current', value), before = JSON.stringify(value)
    const moved = { ...value, nodes: value.nodes.map((n) => ({ ...n, x: n.x + 100 })) }
    expect(session.accept(ticket, 'current', moved)).toBe(false)
    session.cancel()
    expect(JSON.stringify(value)).toBe(before)
  })
  it('未知预算配置拒绝，非法测量不会移动节点', () => {
    const value = graph([node('s'), node('t', 600)], [['s', 't']])
    expect(() => routeGraph(value, { maxMs: -1 })).toThrow('E_ROUTER_BUDGET')
    expect(() => routeGraph(value, { unknown: 1 } as never)).toThrow('E_ROUTER_BUDGET')
    const invalid = { ...value, nodes: value.nodes.map((n) => ({ ...n, width: 0 })) }
    expect(routeGraph(invalid).routes[0].reason).toBe('invalid_geometry')
    expect(layoutMeasuredGraph(invalid.nodes, invalid.edges)).toEqual({ s: { x: 0, y: 0 }, t: { x: 600, y: 0 } })
    expect(layoutMeasuredGraph(value.nodes, [...value.edges, { source: 't', target: 's' }])).toEqual({ s: { x: 0, y: 0 }, t: { x: 600, y: 0 } })
  })
  it('超预算仍为每条合法边生成fallback，重复观察不会漏缓存抛异常', () => {
    const source = node('s'), target = node('t', 600)
    const manyEdges = { nodes: [source, target], edges: Array.from({ length: 3001 }, (_, i) => edge(source, target, `e${i}`, i)) }
    const session = new GeometrySession()
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const result = session.route('current', manyEdges)
      expect(result.routes).toHaveLength(3001)
      expect(result.routes.every((r) => r.reason === 'geometry_budget' && r.path.startsWith('M240 60') && r.path.endsWith('600 60'))).toBe(true)
      expect(result.stats.cells).toBe(0)
      expect(result.stats.expanded).toBe(0)
    }
    const manyNodes = { nodes: [source, target, ...Array.from({ length: 1999 }, (_, i) => node(`far${i}`, i * 300, 1000))], edges: [manyEdges.edges[0]] }
    const large = session.route('current', manyNodes)
    expect(large.routes[0].reason).toBe('geometry_budget')
    expect(session.route('current', manyNodes).routes[0].path).toBe(large.routes[0].path)
  })
  it('超预算早退的耗时必须包含可见fallback生成', () => {
    const source = node('s'), target = node('t', 600)
    let generationStarted = false
    const guarded = { ...edge(source, target), get start() { generationStarted = true; return { x: 240, y: 60 } } }
    const result = routeGraph({ nodes: [source, target], edges: [guarded] }, { maxNodes: 0 }, () => generationStarted ? 7 : 0)
    expect(result.stats.elapsedMs).toBe(7)
  })
  it('薄障碍精确命中、边界相切不算穿越；简化不抹去掉头', () => {
    const box = { left: 1.001, right: 1.002, top: -0.01, bottom: 0.01 }
    expect(segmentHits({ x: 0, y: 0 }, { x: 10, y: 0 }, box)).toBe(true)
    expect(segmentHits({ x: 0, y: -0.01 }, { x: 10, y: -0.01 }, box)).toBe(false)
    expect(simplify([{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 10, y: 0 }, { x: 0, y: 0 }])).toEqual([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 0, y: 0 }])
  })
  it('源目标仅豁免合法出入口，后段回穿源仍被独立审计发现', () => {
    const points = [{ x: 240, y: 60 }, { x: 270, y: 60 }, { x: 270, y: 100 }, { x: 20, y: 100 }, { x: 20, y: 220 }, { x: 600, y: 220 }]
    const route: GeometryRoute = { id: 'bad', path: polylinePath(points), points, bounds: pointBounds(points), reason: null }
    expect(routeCollisions(route, [node('s')], 's', 't')).toEqual(['s'])
  })
  it('内部交叉只标注跨线层，不标端口相遇，比较预算有界', () => {
    const make = (id: string, points: { x: number; y: number }[]): GeometryRoute => ({ id, points, path: polylinePath(points), reason: null, bounds: pointBounds(points) })
    const horizontal = make('a', [{ x: 0, y: 50 }, { x: 100, y: 50 }]), vertical = make('b', [{ x: 50, y: 0 }, { x: 50, y: 100 }])
    expect(routeCrossings([horizontal, vertical])).toEqual([{ under: 'a', over: 'b', point: { x: 50, y: 50 } }])
    expect(routeCrossings([horizontal, make('c', [{ x: 100, y: 50 }, { x: 100, y: 100 }])])).toEqual([])
    expect(routeCrossings([horizontal, vertical], 0)).toEqual([])
    const crossings = routeCrossings([horizontal, vertical])
    expect(pathWithCrossingGaps(horizontal, crossings)).toBe('M0 50 L45 50 M55 50 L100 50')
    expect(pathWithCrossingGaps(vertical, crossings)).toBe(vertical.path)
    expect(horizontal.path).toBe('M0 50 L100 50')
  })
  it('相邻交叉的断口合并，反向线保留真实端点与方向', () => {
    const points = [{ x: 100, y: 50 }, { x: 0, y: 50 }]
    const route = { id: 'a', points, path: polylinePath(points), reason: null, bounds: pointBounds(points) }
    const crosses = [48, 52].map((x) => ({ under: 'a', over: `b${x}`, point: { x, y: 50 } }))
    expect(pathWithCrossingGaps(route, crosses)).toBe('M100 50 L57 50 M43 50 L0 50')
  })
})
