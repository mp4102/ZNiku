/** 输出仅含合成 fixture 与几何统计；不探测本机路径、媒体、用户或 Run 数据。 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { performance } from 'node:perf_hooks'
import { gzipSync } from 'node:zlib'
import { fixtures } from './fixtures.mjs'
import { audit, baseline, layout } from './geometry.mjs'
import { DEFAULT_BUDGET, GeometrySession, routeGraph } from './router.mjs'

if (process.argv.length !== 2) throw new Error('E_PREFLIGHT_ARGUMENTS: 此脚本不接受本机路径或其他参数')
const round = (value) => Number(value.toFixed(3))
const quantile = (values, fraction) => [...values].sort((a, b) => a - b)[Math.max(0, Math.ceil(values.length * fraction) - 1)]
const timings = (samples) => ({ minMs: round(Math.min(...samples)), medianMs: round(quantile(samples, 0.5)),
  p95Ms: round(quantile(samples, 0.95)), maxMs: round(Math.max(...samples)) })

function benchmark(compute, signature) {
  for (let warmup = 0; warmup < 3; warmup += 1) compute()
  const samples = []
  let first, result, maxEdgeMs = 0
  for (let repeat = 0; repeat < 20; repeat += 1) {
    const started = performance.now()
    result = compute()
    samples.push(performance.now() - started)
    maxEdgeMs = Math.max(maxEdgeMs, result.stats?.maxEdgeMs ?? 0)
    const current = JSON.stringify(signature(result))
    if (first === undefined) first = current
    assert.equal(current, first, '同一输入路线或降级原因不确定')
  }
  return { result, time: timings(samples), maxEdgeMs: round(maxEdgeMs), deterministicRepeats: 20 }
}

const rows = fixtures().map((fixture) => {
  const original = JSON.stringify(fixture.graph)
  const old = benchmark(() => {
    const graph = fixture.arrange ? layout(fixture.graph) : fixture.graph
    return { graph, routes: baseline(graph) }
  }, (result) => result.routes)
  const alternative = benchmark(() => {
    const graph = fixture.arrange ? layout(fixture.graph, true) : fixture.graph
    return { graph, ...routeGraph(graph, fixture.options) }
  }, (result) => result.routes)
  const session = new GeometrySession()
  session.route(alternative.result.graph, fixture.options)
  const cache = benchmark(() => session.route(alternative.result.graph, fixture.options), (result) => result.routes)
  const reasons = Object.fromEntries([...new Set(alternative.result.routes.map((route) => route.reason).filter(Boolean))]
    .map((reason) => [reason, alternative.result.routes.filter((route) => route.reason === reason).length]))
  assert.equal(JSON.stringify(fixture.graph), original)
  return { id: fixture.id, nodes: fixture.graph.nodes.length, edges: fixture.graph.edges.length,
    legacy: { ...old.time, ...audit(old.result.graph, old.result.routes, 'legacy') },
    candidate: { ...alternative.time, ...audit(alternative.result.graph, alternative.result.routes),
      cells: alternative.result.stats.cells, expanded: alternative.result.stats.expanded,
      maxEdgeExpanded: alternative.result.stats.maxEdgeExpanded, maxEdgeMs: alternative.maxEdgeMs,
      degraded: alternative.result.stats.degraded, reasons, deterministicRepeats: alternative.deterministicRepeats },
    warmCache: { ...cache.time, hits: cache.result.stats.cacheHits },
  }
})
const code = Buffer.concat(['geometry.mjs', 'router.mjs'].map((file) => readFileSync(new URL(file, import.meta.url))))
console.log(JSON.stringify({ experimentVersion: 1, runtime: `Node ${process.versions.node}`, date: '2026-09-09',
  geometryOnly: true, warmups: 3, repetitions: 20, budget: DEFAULT_BUDGET,
  bytes: { experimentGeometryAndRouterRaw: code.length, experimentGeometryAndRouterGzip: gzipSync(code).length,
    addedProductionDependencies: 0, currentProductionBundleDelta: 0, productionImplementationBytes: null },
  historyBoundary: { syntheticHistoryCount: 1000, passedToRouter: false, servicePaginationTested: false }, rows }, null, 2))
