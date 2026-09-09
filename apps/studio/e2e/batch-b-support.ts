/** B 批真实 DOM 测量与只读合同比较；输出只含合成几何，不记录媒体路径或授权票据。 */
import { expect, type Page } from '@playwright/test'
import type { GraphWire, StatusEnvelope } from '../src/studio/contracts'
import { auditRoute, collinearOverlap, type AuditBox, type AuditRoute } from './batch-b-audit'
import type { SyntheticFixtureHost } from './production-support'

export async function readStatus(page: Page, host: SyntheticFixtureHost): Promise<StatusEnvelope> {
  const response = await page.request.get(`${host.origin}/api/studio/status`)
  expect(response.status()).toBe(200)
  return response.json() as Promise<StatusEnvelope>
}
export async function openSynthetic(page: Page, host: SyntheticFixtureHost, path: string, count: number) {
  await page.goto('about:blank')
  await expect.poll(async () => (await readStatus(page, host)).active_operation).toBeNull()
  const response = await page.request.post(`${host.origin}/api/studio/command`, {
    headers: { Origin: host.origin }, data: { operation: 'open_project', path },
  })
  expect(response.status()).toBe(200)
  await page.goto(host.origin)
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  // 生产偏好会跨工程保留；验收统一默认密度，高级另由对应测试明确切换并标记。
  await page.getByRole('button', { name: '视图', exact: true }).click()
  const creator = page.getByRole('button', { name: '返回创作者模式', exact: true })
  if (await creator.isVisible()) await creator.click()
  else await page.keyboard.press('Escape')
  await expect(page.locator('.react-flow__node')).toHaveCount(count)
  await expect(page.locator('.context-mode')).toHaveText('当前编辑')
  expect((await readStatus(page, host)).authoring_diagnostics).toEqual([])
  await waitGeometry(page)
}
export async function settle(page: Page) {
  await page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))))
}
export async function waitGeometry(page: Page) {
  const snapshot = () => page.evaluate(() => ({
    pending: document.querySelector('.canvas-panel')?.getAttribute('data-route-pending'),
    pendingEdges: document.querySelectorAll('.routed-workflow-edge[data-route-status="pending"]').length,
    geometry: [...document.querySelectorAll('.react-flow__node')].map((node) => node.getAttribute('style')),
    viewport: document.querySelector('.react-flow__viewport')?.getAttribute('style'),
    routes: [...document.querySelectorAll('.react-flow__edge-path')].map((path) => path.getAttribute('d')),
  }))
  // 松手/fit 可能在下一帧才使旧 ready 变为 pending；比较两帧后的同一几何而非先读 ready 再盲等。
  await expect.poll(async () => {
    const before = await snapshot(); await settle(page); const after = await snapshot()
    return after.pending === 'false' && after.pendingEdges === 0 && JSON.stringify(before) === JSON.stringify(after)
  }).toBe(true)
}
export function semantics(graph: GraphWire) {
  return { ...graph, nodes: graph.nodes.map(({ ui_position: _position, ...node }) => node) }
}
export async function savedGraph(page: Page, host: SyntheticFixtureHost): Promise<GraphWire> {
  await expect(page.locator('.project-shell-identity')).toContainText('已保存')
  return (await readStatus(page, host)).snapshot!.project.graph
}
export async function arrange(page: Page, apply: boolean) {
  await waitGeometry(page)
  await page.getByRole('button', { name: '整理布局', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '整理布局', exact: true })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: apply ? '应用布局' : '取消整理', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await waitGeometry(page)
}

export async function measureCanvas(page: Page) {
  return page.evaluate(() => {
    const rect = (element: Element) => {
      const b = element.getBoundingClientRect()
      return { left: b.left, right: b.right, top: b.top, bottom: b.bottom }
    }
    const cards = [...document.querySelectorAll<HTMLElement>('.react-flow__node')].map((node) => ({
      id: node.dataset.id!, ...rect(node), visible: getComputedStyle(node).visibility === 'visible',
      title: node.querySelector('.node-card-title')?.textContent ?? '',
      accessibleTitle: node.querySelector('.node-card-title')?.getAttribute('title') ?? '',
      summaries: [...node.querySelectorAll('[aria-label="关键设置"] [role="listitem"]')].map((span) => span.textContent),
    }))
    const ports = [...document.querySelectorAll<HTMLElement>('.typed-handle')].map((handle) => {
      const label = handle.parentElement?.querySelector('.node-port-label'), b = handle.getBoundingClientRect()
      const row = handle.parentElement!.getBoundingClientRect()
      return { node: handle.dataset.nodeId!, port: handle.dataset.portId!, direction: handle.dataset.portDirection!,
        x: handle.dataset.portDirection === 'output' ? b.right : b.left, y: (b.top + b.bottom) / 2,
        rowTop: row.top, rowBottom: row.bottom, label: label?.getAttribute('aria-label') ?? '',
        labelTabIndex: label?.getAttribute('tabindex') }
    })
    const routes = [...document.querySelectorAll<SVGGElement>('.routed-workflow-edge')].map((group) => {
      const path = group.querySelector<SVGPathElement>('.react-flow__edge-path')!
      const matrix = path.getScreenCTM()!
      const source = group.dataset.source!, target = group.dataset.target!
      const sourcePort = group.dataset.sourcePort!, targetPort = group.dataset.targetPort!
      const start = ports.find((port) => port.node === source && port.port === sourcePort && port.direction === 'output')
      const end = ports.find((port) => port.node === target && port.port === targetPort && port.direction === 'input')
      if (!start || !end) throw new Error('生产 SVG 缺少真实端口身份')
      return { id: group.dataset.edgeId!, source, target, sourcePort, targetPort,
        path: path.getAttribute('d')!, matrix: [matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f] as [number, number, number, number, number, number],
        start: { x: start.x, y: start.y }, end: { x: end.x, y: end.y },
        status: group.dataset.routeStatus!, reason: group.dataset.routeReason!,
        highlighted: group.classList.contains('is-highlighted') }
    })
    const transform = new DOMMatrixReadOnly(getComputedStyle(document.querySelector('.react-flow__viewport')!).transform)
    const flow = document.querySelector('.canvas-panel > .react-flow')!, flowBounds = rect(flow)
    return { cards, ports, routes, zoom: transform.a,
      mode: document.querySelector('.workflow-node .node-version') ? 'advanced' : 'creator',
      counters: Object.fromEntries([...document.querySelector('.canvas-panel')!.attributes].filter((a) => a.name.startsWith('data-route-')).map((a) => [a.name.slice(11), a.value])),
      viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
      pageOverflow: document.documentElement.scrollWidth > innerWidth,
      fullyInsideCanvas: cards.filter((c) => c.visible && c.left >= flowBounds.left - 1 && c.right <= flowBounds.right + 1 &&
        c.top >= flowBounds.top - 1 && c.bottom <= flowBounds.bottom + 1).length,
      nodesInCanvas: cards.filter((c) => c.visible && Math.min(c.right, flowBounds.right) > Math.max(c.left, flowBounds.left) &&
        Math.min(c.bottom, flowBounds.bottom) > Math.max(c.top, flowBounds.top)).length }
  })
}
export async function assertGeometry(page: Page, nodes: number, edges: number, requireRouted = true) {
  if (requireRouted) await waitGeometry(page)
  const measurement = await measureCanvas(page)
  expect(measurement.cards).toHaveLength(nodes); expect(measurement.routes).toHaveLength(edges)
  expect(measurement.cards.every((card) => card.visible && card.right > card.left && card.bottom > card.top)).toBe(true)
  for (const port of measurement.ports) {
    const card = measurement.cards.find((item) => item.id === port.node)!
    expect(port.y).toBeGreaterThan(card.top); expect(port.y).toBeLessThan(card.bottom)
    expect(port.y).toBeGreaterThanOrEqual(port.rowTop - .5); expect(port.y).toBeLessThanOrEqual(port.rowBottom + .5)
    expect(port.label).not.toBe(''); expect(port.labelTabIndex).toBe('0')
  }
  const audits = measurement.routes.map((route) => auditRoute(route as AuditRoute, measurement.cards as AuditBox[], 12 * measurement.zoom))
  for (let i = 0; i < audits.length; i++) {
    const audit = audits[i]!, route = measurement.routes[i]!
    expect(audit.endpointError, `${route.id} 必须停在真实 Handle 外缘`).toBeLessThan(1)
    expect(audit.segments).toBeGreaterThan(0)
    if (requireRouted) expect(route.status, `${route.id}: ${route.reason}`).toBe('routed')
    if (route.status === 'routed') expect(audit.collisions, `${route.id} 最终 SVG 侵入扩张卡片`).toEqual([])
    else expect(route.reason).not.toBe('')
  }
  return { ...measurement, audits, collinear: collinearOverlap(measurement.routes, 48 * measurement.zoom) }
}

export async function measuredInteraction(page: Page, event: 'click' | 'pointermove', action: () => Promise<void>) {
  await page.evaluate((name) => {
    const local = window as unknown as { __batchBInteraction?: Promise<number> }
    local.__batchBInteraction = new Promise<number>((done) => document.addEventListener(name, () => {
      const start = performance.now()
      requestAnimationFrame(() => requestAnimationFrame(() => done(performance.now() - start)))
    }, { once: true, capture: true }))
  }, event)
  await action()
  return page.evaluate(async () => (window as unknown as { __batchBInteraction: Promise<number> }).__batchBInteraction)
}
export function p95(values: readonly number[]) { return [...values].sort((a, b) => a - b)[Math.ceil(values.length * .95) - 1]! }

interface FrameAudit { readonly frames: number; readonly solves: number; readonly maxSolvesPerFrame: number }
interface FrameAuditWindow { __batchBFinishFrames?: () => FrameAudit }
export async function startFrameAudit(page: Page) {
  await page.evaluate(() => {
    const canvas = document.querySelector('.canvas-panel')!
    let frame = 0, raf = 0, previous = Number(canvas.getAttribute('data-route-solves'))
    const counts = new Map<number, number>()
    const tick = () => { frame++; raf = requestAnimationFrame(tick) }
    raf = requestAnimationFrame(tick)
    const observer = new MutationObserver(() => {
      const current = Number(canvas.getAttribute('data-route-solves'))
      if (current > previous) counts.set(frame, (counts.get(frame) ?? 0) + current - previous)
      previous = current
    })
    observer.observe(canvas, { attributes: true, attributeFilter: ['data-route-solves'] })
    ;(window as unknown as FrameAuditWindow).__batchBFinishFrames = () => {
      cancelAnimationFrame(raf); observer.disconnect()
      return { frames: frame, solves: [...counts.values()].reduce((sum, value) => sum + value, 0), maxSolvesPerFrame: Math.max(0, ...counts.values()) }
    }
  })
}
export async function finishFrameAudit(page: Page): Promise<FrameAudit> {
  return page.evaluate(() => (window as unknown as FrameAuditWindow).__batchBFinishFrames!())
}
