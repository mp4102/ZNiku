/** 实施前合成几何门禁；Node 内建测试独立运行，不属于正式 Studio UI。 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fixtures } from './fixtures.mjs'
import { anchor, audit, baseline, curveHits, endpoints, layout, pathSegments, polylinePath,
  ranks, rect, segmentHits, validateGeometry } from './geometry.mjs'
import { DEFAULT_BUDGET, GeometrySession, routeGraph } from './router.mjs'

const get = (id) => fixtures().find((fixture) => fixture.id === id)
const arranged = (fixture) => fixture.arrange ? layout(fixture.graph, true) : fixture.graph

test('G01–G10 均有独立、纯合成且闭合的输入', () => {
  const ids = new Set(fixtures().map((fixture) => fixture.id.split('-')[0]))
  assert.equal(ids.size, 10)
  for (const fixture of fixtures()) assert.equal(validateGeometry(fixture.graph), fixture.graph)
})

for (const fixture of fixtures()) {
  test(`${fixture.id}：端点、预算、重复确定性和无语义改写`, () => {
    const graph = arranged(fixture), original = JSON.stringify(graph)
    const result = routeGraph(graph, fixture.options)
    const measurement = audit(graph, result.routes)
    assert.equal(measurement.endpointErrors, 0)
    assert.equal(measurement.outsidePorts, 0)
    assert.equal(result.routes.length, graph.edges.length)
    assert.ok(result.routes.every((route) => route.path.length > 0))
    assert.ok(result.stats.expanded <= (fixture.options.maxTotalExpanded ?? DEFAULT_BUDGET.maxTotalExpanded))
    assert.ok(result.stats.cells <= DEFAULT_BUDGET.maxCells)
    if (fixture.id.startsWith('G09')) {
      assert.equal(result.stats.degraded, 1)
      assert.ok(result.routes.every((route) => route.reason))
    } else {
      assert.equal(result.stats.degraded, 0)
      assert.equal(measurement.collisions, 0)
      assert.equal(measurement.overlaps, 0)
    }
    for (let repeat = 0; repeat < 5; repeat += 1) assert.deepEqual(routeGraph(graph, fixture.options).routes, result.routes)
    assert.equal(JSON.stringify(graph), original)
  })
}

test('冻结旧基准仍使用真实 smoothstep；生产已经切换实际端口行与尺寸布局', () => {
  const graphSource = readFileSync(new URL('../src/studio/graph.ts', import.meta.url), 'utf8')
  const cardSource = readFileSync(new URL('../src/components/WorkflowNodeCard.tsx', import.meta.url), 'utf8')
  const canvasSource = readFileSync(new URL('../src/studio/components/GraphCanvas.tsx', import.meta.url), 'utf8')
  assert.ok(!graphSource.includes('x: 80 + rank * 360, y: 100 + row * 280'))
  assert.ok(!cardSource.includes('58 + (index - (total - 1) / 2) * 22'))
  assert.ok(cardSource.includes('node-port-row'))
  assert.ok(canvasSource.includes('layoutMeasuredGraph'))
  const graph = layout(get('G03').graph)
  assert.ok(baseline(graph).some((route) => route.path.includes('Q')))
  assert.ok(audit(graph, baseline(graph), 'legacy').collisions > 0)
  const tall = layout(get('G06-tall').graph)
  assert.equal(audit(tall, baseline(tall), 'legacy').overlaps, 1)
})

test('旧 6/8/16 端口公式超卡片；新模拟行锚点全部在左右边界', () => {
  for (const count of [1, 2, 6, 8, 16]) {
    const graph = get(`G05-${count}`).graph
    const old = audit(graph, baseline(graph), 'legacy')
    assert.equal(old.outsidePorts, count === 6 ? 4 : count === 8 ? 12 : count === 16 ? 44 : 0)
    for (const node of graph.nodes) for (const [side, ports] of [['source', node.outputs], ['target', node.inputs]]) {
      for (const port of ports) {
        const point = anchor(node, side, port)
        assert.equal(point.x, side === 'source' ? node.x + node.width : node.x)
        assert.ok(point.y >= node.y && point.y <= node.y + node.height)
      }
    }
  }
})

test('相交检查覆盖直线、圆角曲线和只触边；圆角不以采样掩盖细障碍', () => {
  const box = { left: 4.999, right: 5.001, top: 4.999, bottom: 5.001 }
  assert.equal(segmentHits({ x: 0, y: 5 }, { x: 10, y: 5 }, box), true)
  assert.equal(segmentHits({ x: 0, y: box.top }, { x: 10, y: box.top }, box), false)
  assert.equal(curveHits({ a: { x: 0, y: 0 }, c: { x: 5, y: 10 }, b: { x: 10, y: 0 } }, box), true)
  assert.equal(curveHits({ a: { x: 0, y: 0 }, c: { x: 5, y: 2 }, b: { x: 10, y: 0 } }, box), false)
})

test('出入口豁免不放过绕回源矩形或从错误方向离开锚点', () => {
  const graph = get('G07-before').graph, edge = graph.edges[0]
  const [start, end] = endpoints(graph, edge)
  const wrong = polylinePath([start, { x: start.x - 80, y: start.y }, { x: start.x - 80, y: 260 },
    { x: end.x - 20, y: 260 }, { x: end.x - 20, y: end.y }, end])
  assert.ok(audit(graph, [{ id: edge.id, path: wrong }]).collisions > 0)
  const valid = routeGraph(graph).routes[0]
  assert.equal(audit(graph, [valid]).collisions, 0)
})

test('未知字段/版本、缺失/错误端口、非法尺寸、重复 ID、环与超量默认拒绝', () => {
  const graph = get('G01').graph
  const variations = [
    { ...graph, history: [] }, { ...graph, version: 2 }, { ...graph, nodes: null },
    { ...graph, nodes: [...graph.nodes, graph.nodes[0]] },
    { ...graph, nodes: [{ ...graph.nodes[0], executable: 'hidden' }] },
    { ...graph, nodes: [{ ...graph.nodes[0], x: NaN }], edges: [] },
    { ...graph, nodes: [{ ...graph.nodes[0], height: 0 }], edges: [] },
    { ...graph, nodes: Array.from({ length: 201 }, (_, index) => ({ ...graph.nodes[0], id: `n${index}` })), edges: [] },
    { ...graph, edges: [{ ...graph.edges[0], sourcePort: 'missing' }] },
    { ...graph, edges: [{ ...graph.edges[0], ordinal: -1 }] },
    { ...graph, edges: [{ ...graph.edges[0], arbitrary_waypoints: [] }] },
  ]
  for (const invalid of variations) assert.throws(() => routeGraph(invalid), /E_GEOMETRY/)
  assert.throws(() => ranks({ ...graph, edges: [...graph.edges, { ...graph.edges[0], id: 'cycle', source: 'o', target: 's' }] }), /CYCLE/)
  assert.throws(() => routeGraph(graph, { unknown: true }), /E_ROUTER_OPTIONS/)
  assert.throws(() => routeGraph(graph, { maxCells: Infinity }), /E_ROUTER_BUDGET/)
})

test('G07 无关障碍变动失效缓存；无几何进度轮询全部命中；输出不能污染缓存', () => {
  const session = new GeometrySession(), before = get('G07-before').graph, after = get('G07-after').graph
  const first = session.route(before), warm = session.route(before)
  assert.equal(warm.stats.cacheHits, before.edges.length)
  warm.routes[0].path = 'corrupted by caller'
  assert.equal(session.route(before).routes[0].path, first.routes[0].path)
  const moved = session.route(after)
  assert.equal(moved.stats.cacheHits, 0)
  assert.notEqual(moved.routes[0].path, first.routes[0].path)
  assert.equal(audit(after, moved.routes).collisions, 0)
  assert.ok(audit(after, first.routes).collisions > 0)
})

test('G08 平行轨道可区分但绑定与 ordered_many 序号不变', () => {
  const graph = get('G08').graph, edges = structuredClone(graph.edges)
  const routes = routeGraph(graph).routes
  assert.equal(new Set(routes.map((route) => route.path)).size, graph.edges.length)
  assert.deepEqual(graph.edges, edges)
  assert.deepEqual(graph.edges.map((edge) => edge.ordinal), [0, 1, 2, 3])
})

test('当前图/历史图同 ID 不同几何、旧异步结果及取消均有版本栅栏', () => {
  const session = new GeometrySession(), graph = get('G07-before').graph, moved = get('G07-after').graph
  const old = session.issue('current-project', graph)
  const history = session.issue('history-run-1', moved)
  assert.equal(session.accept(old, graph), false)
  assert.equal(session.accept(history, graph), false)
  assert.equal(session.accept(history, moved), true)
  session.cancel()
  assert.equal(session.accept(history, moved), false)
  assert.equal(session.accept(null, moved), false)
  assert.equal(session.accept(undefined, moved), false)
  const current = session.issue('current-project', moved)
  assert.equal(session.accept(current, moved), true)
  assert.equal(session.accept({ ...current }, moved), false)
})

test('通道/时间/全图工作预算降级仍使用当前正确锚点；时钟可注入而不 flaky', () => {
  const graph = get('G07-after').graph
  const tests = [routeGraph(graph, { maxCells: 1 }), routeGraph(graph, { maxTotalExpanded: 1 })]
  let ticks = 0
  tests.push(routeGraph(graph, { maxMs: 1, maxEdgeMs: 1 }, () => ticks++ * 100))
  assert.deepEqual(tests.map((result) => result.routes[0].reason), ['channel_budget', 'work_budget', 'time_budget'])
  for (const result of tests) {
    assert.equal(result.stats.degraded, 1)
    assert.equal(audit(graph, result.routes).endpointErrors, 0)
    assert.ok(pathSegments(result.routes[0].path).length > 0)
  }
})

test('空图可处理；矩形输入外观与非几何 1000 条历史严格隔离', () => {
  assert.deepEqual(routeGraph({ version: 1, nodes: [], edges: [] }).routes, [])
  const graph = get('G10-200').graph
  const history = Array.from({ length: 1000 }, (_, index) => ({ run_id: `synthetic-${index}` }))
  assert.equal(history.length, 1000)
  assert.throws(() => routeGraph({ ...graph, history }), /FIELDS/)
  assert.equal(rect(graph.nodes[0]).left, graph.nodes[0].x - 12)
})
