/** 验收审计器自己的反例；node --test e2e/batch-b-audit.checks.ts，不由 Playwright 收集。 */
import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { auditRoute, collinearOverlap, pathSegments, segmentIntersects, type AuditRoute } from './batch-b-audit.ts'

test('路径不认识、缺参数、非有限数与空路径全部失败关闭', () => {
  for (const path of ['', 'M0 0', 'M0 0 C1 2 3 4 5 6', 'M0 0 L1', 'M0 0 L1e999 2', 'M0 0 L3 4 Z', 'M0 0 l3 4', 'M0 0 rubbish L3 4'])
    assert.throws(() => pathSegments(path), path)
})
test('完整数学区间检测薄障碍；边界相切不是内部穿越', () => {
  const segment = { start: { x: 0, y: 5 }, end: { x: 1000, y: 5 } }
  assert.equal(segmentIntersects(segment, { id: 'thin', left: 500.123, right: 500.124, top: 4, bottom: 6 }), true)
  assert.equal(segmentIntersects(segment, { id: 'touch', left: 400, right: 600, top: 5, bottom: 6 }), false)
})
test('Q 圆角曲线的窄内部穿越不靠端点或粗采样判断', () => {
  const segment = { start: { x: 0, y: 0 }, control: { x: 50, y: 100 }, end: { x: 100, y: 0 } }
  assert.equal(segmentIntersects(segment, { id: 'curve', left: 49.999, right: 50.001, top: 49.999, bottom: 50.001 }), true)
  assert.equal(segmentIntersects(segment, { id: 'above', left: 20, right: 80, top: 51, bottom: 60 }), false)
})
test('变换后的真实端点和完整卡片扩张区均进入审计', () => {
  const route: AuditRoute = { id: 'edge', source: 'source', target: 'target', path: 'M100 50 L300 50',
    start: { x: 250, y: 120 }, end: { x: 650, y: 120 }, matrix: [2, 0, 0, 2, 50, 20], status: 'routed', reason: '' }
  const result = auditRoute(route, [{ id: 'obstacle', left: 440, right: 460, top: 121, bottom: 140 }], 2)
  assert.equal(result.endpointError, 0); assert.deepEqual(result.collisions, ['obstacle'])
})
test('源卡片第一出线豁免不掩盖随后回穿；多个 M 只审计真实可见段', () => {
  const route: AuditRoute = { id: 'edge', source: 'source', target: 'target', path: 'M100 50 L120 50 L120 80 L80 80 L80 50 M140 50 L300 50',
    start: { x: 100, y: 50 }, end: { x: 300, y: 50 }, matrix: [1, 0, 0, 1, 0, 0], status: 'routed', reason: '' }
  const result = auditRoute(route, [{ id: 'source', left: 0, right: 100, top: 0, bottom: 100 }], 12)
  assert.deepEqual(result.collisions, ['source']); assert.equal(result.segments, 5)
  assert.equal(auditRoute({ ...route, path: 'M100 50 L120 50 M140 50 L300 50' },
    [{ id: 'gap', left: 125, right: 135, top: 49, bottom: 51 }], 0).collisions.length, 0)
})
test('共线指标只豁免共同端口的短出口，不豁免第一条长线或其他节点', () => {
  const route: AuditRoute = { id: 'a', source: 's', target: 'a', path: 'M0 0 L100 0', start: { x: 0, y: 0 },
    end: { x: 100, y: 0 }, matrix: [1, 0, 0, 1, 0, 0], status: 'routed', reason: '' }
  const second = { ...route, id: 'b', target: 'b', path: 'M0 0 L20 0 L20 100', end: { x: 20, y: 100 } }
  assert.equal(collinearOverlap([route, second], 24).nonterminalCount, 0)
  assert.equal(collinearOverlap([route, { ...second, path: 'M0 0 L80 0 L80 100' }], 24).nonterminalMaximum, 56)
  assert.equal(collinearOverlap([route, { ...second, source: 'other' }], 24).nonterminalMaximum, 20)
})
