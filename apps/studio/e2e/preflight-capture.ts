/** 真实 production DOM + 正式合成工程的改造前证据；只观测，不注入替代 UI 或改变领域语义。 */
import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import type { StatusEnvelope, RunDetailEnvelope } from '../src/studio/contracts'
import { DIAGNOSTIC_MASK_SELECTOR, maskedScreenshot, ProductionJournal, SyntheticFixtureHost } from './production-support'

const service = new SyntheticFixtureHost()
let journal: ProductionJournal
test.beforeAll(async () => { await service.start() })
test.beforeEach(async ({ page }) => { journal = new ProductionJournal(page, service) })
test.afterEach(async ({}, info) => { if (info.status !== info.expectedStatus) await journal.capture(info); journal.dispose() })
test.afterAll(async () => { await service.stop() })

async function status(page: Page): Promise<StatusEnvelope> {
  const response = await page.request.get(`${service.origin}/api/studio/status`)
  expect(response.status()).toBe(200)
  return response.json() as Promise<StatusEnvelope>
}
async function open(page: Page, path: string, count: number) {
  // fixture 切换使用服务命令而不是 UI picker；先卸载前一页，避免旧页面的视口自动保存与设置命令竞态。
  await page.goto('about:blank')
  await expect.poll(async () => (await status(page)).active_operation).toBeNull()
  const response = await page.request.post(`${service.origin}/api/studio/command`, {
    headers: { Origin: service.origin }, data: { operation: 'open_project', path },
  })
  expect(response.status()).toBe(200)
  await page.goto(service.origin)
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(count)
  if (await page.getByRole('button', { name: '返回创作者模式', exact: true }).isVisible())
    await page.getByRole('button', { name: '返回创作者模式', exact: true }).click()
}
async function settle(page: Page) {
  await page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))))
}
async function shot(page: Page, info: TestInfo, name: string) {
  await settle(page)
  await maskedScreenshot(page, info.outputPath(`${name}-masked.png`))
}
async function measured(page: Page, event: 'click' | 'pointermove', action: () => Promise<void>) {
  await page.evaluate((eventName) => {
    const state = window as unknown as { __preflightInteraction?: Promise<number> }
    state.__preflightInteraction = new Promise<number>((done) => document.addEventListener(eventName, () => {
      const start = performance.now()
      requestAnimationFrame(() => requestAnimationFrame(() => done(performance.now() - start)))
    }, { once: true, capture: true }))
  }, event)
  await action()
  return page.evaluate(async () => (window as unknown as { __preflightInteraction: Promise<number> }).__preflightInteraction)
}
function p95(values: number[]) { return [...values].sort((a, b) => a - b)[Math.ceil(values.length * .95) - 1]! }
async function performanceSamples(page: Page, count: number) {
  await page.getByRole('textbox', { name: '查找画布节点' }).fill('source.mkv')
  await page.locator('[aria-label="画布搜索结果"]').getByRole('button').first().click()
  const source = page.locator('.react-flow__node[data-id="source"]')
  const selection: number[] = [], expand: number[] = [], drag: number[] = []
  for (let index = 0; index < 20; index++) {
    await source.click({ modifiers: ['Control'] })
    await expect(page.getByLabel('节点别名')).toHaveCount(0)
    selection.push(await measured(page, 'click', () => source.click()))
    const summary = page.getByText('高级 → 原始参数', { exact: true })
    if (await summary.evaluate((element) => element.closest('details')?.open === true)) await summary.click()
    expand.push(await measured(page, 'click', () => page.getByText('高级 → 原始参数', { exact: true }).click()))
    const box = await source.boundingBox()
    if (!box) throw new Error('合成节点必须有可量测边界')
    await page.mouse.move(box.x + box.width / 2, box.y + 20)
    await page.mouse.down()
    drag.push(await measured(page, 'pointermove', () => page.mouse.move(box.x + box.width / 2 + 5, box.y + 25)))
    await page.mouse.up()
  }
  await expect(page.locator('.workflow-identity')).toContainText('已保存')
  return { node_count: count, samples_each: 20, selection_p95_ms: p95(selection), parameter_expand_p95_ms: p95(expand), drag_p95_ms: p95(drag),
    unit: 'browser event capture to second requestAnimationFrame; no route-cache instrumentation' }
}

async function geometry(page: Page) {
  return page.evaluate(() => {
    const bodies = [...document.querySelectorAll('.react-flow__node')].map((node) => {
      const card = node.querySelector('.workflow-node') ?? node
      return { id: node.getAttribute('data-id'), box: card.getBoundingClientRect(), element: node }
    })
    const inside = (x: number, y: number, box: DOMRect) => x >= box.left && x <= box.right && y >= box.top && y <= box.bottom
    const ports = bodies.map(({ id, box, element }) => {
      const handles = [...element.querySelectorAll('.typed-handle')].map((handle) => {
        const rect = handle.getBoundingClientRect()
        return { port_id: handle.getAttribute('data-handleid'), type: handle.classList.contains('source') ? 'output' : 'input',
          center_y_relative: (rect.y + rect.height / 2 - box.y) / box.height,
          vertical_outside: rect.y + rect.height / 2 < box.top || rect.y + rect.height / 2 > box.bottom }
      })
      return { node_id: id, body_width: box.width, body_height: box.height, handles,
        outside_count: handles.filter((handle) => handle.vertical_outside).length }
    })
    const crossing = [...document.querySelectorAll<SVGPathElement>('.react-flow__edge-path')].map((path) => {
      const transform = path.getScreenCTM(), length = path.getTotalLength()
      if (!transform) return { crossing_nodes: [], samples: 0 }
      const point = (distance: number) => { const at = path.getPointAtLength(distance); return new DOMPoint(at.x, at.y).matrixTransform(transform) }
      const start = point(0), end = point(length)
      const obstacles = bodies.filter(({ box }) => !inside(start.x, start.y, box) && !inside(end.x, end.y, box))
      const ids = new Set<string | null>()
      const samples = Math.ceil(length / 4)
      for (let index = 0; index <= samples; index++) {
        const at = point(length * index / samples)
        for (const body of obstacles) if (inside(at.x, at.y, body.box)) ids.add(body.id)
      }
      return { crossing_nodes: [...ids], samples }
    })
    const overlays = [...document.querySelectorAll('.run-summary-strip, .next-action-banner')].map((element) => {
      const box = element.getBoundingClientRect()
      return { class_name: element.className, width: box.width, height: box.height,
        overlapping_node_ids: bodies.filter((body) => Math.min(box.right, body.box.right) > Math.max(box.left, body.box.left) &&
          Math.min(box.bottom, body.box.bottom) > Math.max(box.top, body.box.top)).map((body) => body.id) }
    })
    return { viewport: { width: innerWidth, height: innerHeight }, ports, edges: crossing, overlays,
      horizontal_page_overflow: document.documentElement.scrollWidth > innerWidth }
  })
}

test('改造前真实生产页面：七类视图、50/200交互与6/8/16端口实测', async ({ page }, info) => {
  const metrics = [], observations: Record<string, unknown> = {}
  await open(page, service.fixture.small_project, 50)
  observations.production_scripts = await page.locator('script[type="module"][src]').evaluateAll((scripts) => scripts.map((script) => new URL((script as HTMLScriptElement).src).pathname))
  metrics.push(await performanceSamples(page, 50))
  await page.getByRole('button', { name: '适应画布', exact: true }).click()
  await shot(page, info, 'creator-current-50')
  observations.creator_50 = await geometry(page)
  await page.getByRole('button', { name: '高级节点图', exact: true }).click()
  await shot(page, info, 'advanced-current-50')
  await page.getByRole('button', { name: '返回创作者模式', exact: true }).click()

  await open(page, service.fixture.large_project, 200)
  metrics.push(await performanceSamples(page, 200))
  const current = await status(page)
  const history = page.getByRole('combobox', { name: '处理记录', exact: true })
  observations.history_bounds = { total_fixture_history: 1000, first_status_summaries: current.run_summaries.length,
    selector_options: await history.locator('option').count(), has_next_cursor: Boolean(current.next_run_cursor), full_run_history_in_status: false }
  await page.getByRole('button', { name: '适应画布', exact: true }).click()
  await shot(page, info, 'creator-current-200')
  observations.creator_200 = await geometry(page)
  const historical = await history.locator('option').nth(1).getAttribute('value')
  if (!historical) throw new Error('合成历史 fixture 必须提供真实 Run')
  await history.selectOption(historical)
  await expect(page.locator('.context-mode')).toHaveText('本次处理的工作流')
  await shot(page, info, 'history')

  await open(page, service.fixture.geometry_project, 4)
  await page.getByRole('button', { name: '适应画布', exact: true }).click()
  await settle(page)
  observations.ports_6_8_16 = await geometry(page)
  await shot(page, info, 'ports-6-8-16')

  await open(page, service.fixture.media_project, 2)
  const template = await status(page)
  const project = template.snapshot!.project
  const graph = { ...project.graph, nodes: project.graph.nodes.map((node) => node.node_id === 'source'
    ? { ...node, parameters: { source_path: join(dirname(service.fixture.media_project), 'missing-synthetic-source.mkv') } } : node) }
  const saved = await page.request.post(`${service.origin}/api/studio/command`, { headers: { Origin: service.origin },
    data: { operation: 'save_project', project_session_id: template.project_session_id, expected_storage_revision: template.storage_revision,
      project: { ...project, graph }, studio_state: template.studio_state } })
  expect(saved.status()).toBe(200)
  await page.reload()
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  // failed 节点仍允许新 attempt，Run 可以保持 running；截图需等真实失败步骤，而非伪造 Run 终态。
  await expect.poll(async () => (await status(page)).run_summaries[0]?.state_counts.failed, { timeout: 45_000 }).toBe(1)
  await expect(page.locator('.react-flow__node[data-id="source"] .workflow-node')).toHaveClass(/status-failed/)
  await page.locator('.react-flow__node[data-id="source"]').click()
  await shot(page, info, 'failed')
  observations.failed = await geometry(page)
  const failed = await status(page)
  observations.failed_runtime = { run_state: failed.run_summaries[0]?.state,
    state_counts: failed.run_summaries[0]?.state_counts, active_operation: failed.active_operation }

  await open(page, service.fixture.external_project, 4)
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  await expect.poll(async () => {
    const id = (await status(page)).run_summaries[0]?.run_id
    if (!id) return 0
    const detail = await (await page.request.get(`${service.origin}/api/studio/runs/${id}`)).json() as RunDetailEnvelope
    return detail.run.node_runs.filter((node) => node.state === 'waiting_external').length
  }, { timeout: 45_000 }).toBe(2)
  await page.locator('.react-flow__node[data-id="enhance-B"]').click()
  await shot(page, info, 'waiting-selected-B')
  observations.waiting = await geometry(page)
  await page.setViewportSize({ width: 1280, height: 720 })
  await shot(page, info, 'narrow-1280x720')
  observations.narrow = await geometry(page)

  const report = { purpose: 'v0.3.1 implementation preflight; observed defects are not a passing product acceptance',
    screenshots_mask: `${DIAGNOSTIC_MASK_SELECTOR}; plus visible physical-path/UUID text; screenshots do not modify DOM`,
    platform: process.platform, metrics, observations }
  await writeFile(info.outputPath('preflight-observation.json'), JSON.stringify(report, null, 2), 'utf8')
  console.log('PREFLIGHT_INTERACTION', JSON.stringify(metrics))
})
