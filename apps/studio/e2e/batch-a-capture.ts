/** A 批占位布局的真实生产 DOM 证据；只使用独立合成工程和正式 Python 服务，不操作用户工作区。 */
import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import type { RunDetailEnvelope, StatusEnvelope } from '../src/studio/contracts'
import { diagnosticValue, maskedScreenshot, ProductionJournal, SyntheticFixtureHost } from './production-support'

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
  // 先卸载旧页面，防止截图前的 fixture 导航与上一页 viewport 自动保存竞争。
  await page.goto('about:blank')
  await expect.poll(async () => (await status(page)).active_operation).toBeNull()
  const response = await page.request.post(`${service.origin}/api/studio/command`, {
    headers: { Origin: service.origin }, data: { operation: 'open_project', path },
  })
  expect(response.status()).toBe(200)
  await page.goto(service.origin)
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(count)
  await expect(page.locator('.context-mode')).toHaveText('当前编辑')
}
async function settle(page: Page) {
  await page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))))
}
async function shot(page: Page, info: TestInfo, name: string) {
  await settle(page)
  await maskedScreenshot(page, info.outputPath(`${name}-masked.png`))
}
async function layout(page: Page) {
  return page.evaluate(() => {
    const selectors = {
      topbar: '.project-shell', library: '.workspace-library', canvas: '.canvas-panel',
      inspector: '.inspector-workspace', tasks: '.task-drawer',
    }
    const boxes = Object.fromEntries(Object.entries(selectors).map(([name, selector]) => {
      const element = document.querySelector(selector)
      if (!element) return [name, null]
      const box = element.getBoundingClientRect(), style = getComputedStyle(element)
      return [name, { x: box.x, y: box.y, width: box.width, height: box.height,
        visible: style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0 }]
    }))
    const context = document.querySelector('.canvas-context')?.getBoundingClientRect()
    const toolbar = document.querySelector('.canvas-edit-toolbar')?.getBoundingClientRect()
    const flow = document.querySelector('.canvas-panel > .react-flow')?.getBoundingClientRect()
    const canvas = document.querySelector('.canvas-panel')?.getBoundingClientRect()
    const graphViewport = flow ?? canvas
    const tasks = document.querySelector('.task-drawer')?.getBoundingClientRect()
    const nodeBoxes = [...document.querySelectorAll('.react-flow__node')].map((node) => ({
      box: node.getBoundingClientRect(), visible: getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none',
    }))
    return { viewport: { width: innerWidth, height: innerHeight }, boxes,
      horizontal_page_overflow: document.documentElement.scrollWidth > innerWidth,
      context_toolbar_overlap: !!(context && toolbar && context.bottom > toolbar.top + 1),
      toolbar_flow_overlap: !!(toolbar && flow && toolbar.bottom > flow.top + 1),
      canvas_tasks_overlap: !!(canvas && tasks && canvas.bottom > tasks.top + 1),
      node_count: nodeBoxes.length,
      node_dom_visible_count: nodeBoxes.filter((node) => node.visible && node.box.width > 0 && node.box.height > 0).length,
      node_in_canvas_count: graphViewport ? nodeBoxes.filter((node) => node.visible && Math.min(node.box.right, graphViewport.right) > Math.max(node.box.left, graphViewport.left) &&
        Math.min(node.box.bottom, graphViewport.bottom) > Math.max(node.box.top, graphViewport.top)).length : 0,
      old_canvas_overlays: document.querySelectorAll('.run-summary-strip,.next-action-banner,.canvas-run-overlays').length,
      primary_actions: document.querySelectorAll('.project-shell .creator-run-action > .button--primary').length }
  })
}

test('A 批生产布局：菜单、独立页签、任务与历史、三个桌面视口', async ({ page }, info) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  const observations: Record<string, unknown> = {}
  await open(page, service.fixture.small_project, 50)
  await page.getByRole('button', { name: '适应画布', exact: true }).click()
  await shot(page, info, 'creator-current-50')
  observations.creator_50 = await layout(page)
  expect((await layout(page)).old_canvas_overlays).toBe(0)
  expect((await layout(page)).primary_actions).toBe(1)
  await page.getByRole('button', { name: '工程', exact: true }).click()
  await expect(page.getByRole('button', { name: '打开工程', exact: true })).toBeVisible()
  await shot(page, info, 'project-menu')
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '视图', exact: true }).click()
  await expect(page.getByRole('button', { name: '高级节点图', exact: true })).toBeVisible()
  await shot(page, info, 'view-menu')
  await page.getByRole('button', { name: '高级节点图', exact: true }).click()
  await expect(page.getByRole('tab', { name: '诊断', exact: true })).toHaveAttribute('aria-selected', 'false')
  await shot(page, info, 'advanced-current-50')
  await page.getByRole('button', { name: '视图', exact: true }).click()
  await page.getByRole('button', { name: '返回创作者模式', exact: true }).click()
  await page.getByRole('textbox', { name: '查找画布节点' }).fill('source.mkv')
  await page.locator('[aria-label="画布搜索结果"]').getByRole('button').first().click()
  await page.getByRole('tab', { name: '设置', exact: true }).click()
  await shot(page, info, 'inspector-settings')
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  await shot(page, info, 'inspector-files')
  await page.getByRole('tab', { name: '诊断', exact: true }).click()
  await page.getByText('高级 → 原始参数', { exact: true }).click()
  await shot(page, info, 'inspector-diagnostics')
  await page.getByRole('tab', { name: '设置', exact: true }).click()
  await page.getByRole('button', { name: '展开任务区', exact: true }).click()
  await shot(page, info, 'task-drawer-current')
  const resize = page.getByRole('slider', { name: '任务区高度', exact: true })
  await resize.focus()
  await resize.press('Home')
  await expect(resize).toHaveValue('120')
  const lowerCanvas = await page.locator('.canvas-panel').boundingBox()
  await resize.press('ArrowRight')
  await expect(resize).toHaveValue('130')
  await expect.poll(async () => (await page.locator('.canvas-panel').boundingBox())!.height).toBeCloseTo(lowerCanvas!.height - 10, 0)
  await resize.press('End')
  observations.drawer_resize = { keyboard_minimum: 120, keyboard_step: 10, expanded_height: await resize.inputValue() }
  for (const viewport of [{ width: 1280, height: 720 }, { width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
    await page.setViewportSize(viewport)
    await expect(page.getByRole('button', { name: '处理记录', exact: true })).toBeVisible()
    // 等待实际 resize 投影采用新上限，不能将上一帧的高度混入本尺寸截图记录。
    await expect(resize).toHaveAttribute('max', String(Math.max(120, Math.min(420, Math.floor(viewport.height * .45)))))
    await settle(page)
    const current = await layout(page)
    expect(current.horizontal_page_overflow).toBe(false)
    expect(current.context_toolbar_overlap).toBe(false)
    expect(current.toolbar_flow_overlap).toBe(false)
    expect(current.canvas_tasks_overlap).toBe(false)
    expect(current.primary_actions).toBe(1)
    expect(current.old_canvas_overlays).toBe(0)
    expect(current.node_count).toBe(50)
    expect(current.node_dom_visible_count).toBe(50)
    observations[`viewport_${viewport.width}_pressure`] = current
    await shot(page, info, `viewport-pressure-${viewport.width}x${viewport.height}`)
    // 保留原 viewport 压力证据后，由显式用户动作适应画布；不偷偷移动节点或自动更改镜头。
    await page.getByRole('button', { name: '适应画布', exact: true }).click()
    await settle(page)
    await expect.poll(async () => (await layout(page)).node_in_canvas_count).toBeGreaterThan(0)
    observations[`viewport_${viewport.width}_fit`] = await layout(page)
    await shot(page, info, `layout-${viewport.width}x${viewport.height}`)
  }
  await page.setViewportSize({ width: 840, height: 720 })
  await page.getByRole('button', { name: '视图', exact: true }).click()
  await page.getByRole('button', { name: '收起节点库', exact: true }).click()
  await expect(page.locator('.workspace-library')).not.toBeVisible()
  for (const [label, selector] of [['跳到节点面板', '#node-palette'], ['跳到步骤设置', '#node-inspector'], ['跳到画布', '#workflow-canvas']] as const) {
    const link = page.getByRole('link', { name: label, exact: true })
    await link.focus()
    await link.press('Enter')
    await expect(page.locator(selector)).toBeVisible()
    await expect(page.locator(selector)).toBeFocused()
  }
  observations.keyboard_840 = await layout(page)
  expect((await layout(page)).horizontal_page_overflow).toBe(false)
  await shot(page, info, 'keyboard-840x720')
  await page.setViewportSize({ width: 1920, height: 1080 })
  await open(page, service.fixture.large_project, 200)
  await page.getByRole('button', { name: '适应画布', exact: true }).click()
  await shot(page, info, 'creator-current-200')
  await page.getByRole('button', { name: '处理记录', exact: true }).click()
  const history = page.getByRole('tabpanel', { name: '历史记录', exact: true })
  await expect(history.locator('.task-drawer-history > li')).toHaveCount(20)
  const current = await status(page)
  expect(current.run_summaries).toHaveLength(20)
  expect(current.next_run_cursor).toBeTruthy()
  const selectedRunId = await history.locator('.task-drawer-history > li > button').nth(1).getAttribute('value')
  expect(current.run_summaries.map((summary) => summary.run_id)).toContain(selectedRunId)
  const detailUrl = `${service.origin}/api/studio/runs/${selectedRunId}`
  const before = await (await page.request.get(detailUrl)).json()
  await history.locator('.task-drawer-history > li > button').nth(1).click()
  await expect(page.locator('.context-mode')).toHaveText('本次处理的流程（只读）')
  await expect(page.locator('.react-flow__node')).toHaveCount(0)
  await shot(page, info, 'history-snapshot')
  observations.history = { total_fixture_history: 1000, displayed_rows: await history.locator('.task-drawer-history > li').count(),
    first_status_summaries: current.run_summaries.length, has_next_cursor: !!current.next_run_cursor }
  await page.locator('.canvas-context').getByRole('button', { name: '返回当前编辑', exact: true }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(200)
  expect(await (await page.request.get(detailUrl)).json()).toEqual(before)
  expect(errors).toEqual([])
  await writeFile(info.outputPath('batch-a-layout.json'), JSON.stringify(diagnosticValue({
    purpose: 'Batch A synthetic production UI evidence; not real-media or native Windows DPI acceptance', observations,
  }, service.roots()), null, 2), 'utf8')
})

test('A 批生产问题与外部文件：真实失败状态和两个精确待办', async ({ page }, info) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await open(page, service.fixture.media_project, 2)
  const current = await status(page)
  const project = current.snapshot!.project
  const graph = { ...project.graph, nodes: project.graph.nodes.map((node) => node.node_id === 'source'
    ? { ...node, parameters: { source_path: join(dirname(service.fixture.media_project), 'missing-synthetic-source.mkv') } } : node) }
  const saved = await page.request.post(`${service.origin}/api/studio/command`, { headers: { Origin: service.origin },
    data: { operation: 'save_project', project_session_id: current.project_session_id, expected_storage_revision: current.storage_revision,
      project: { ...project, graph }, studio_state: current.studio_state } })
  expect(saved.status()).toBe(200)
  await page.reload()
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  await expect.poll(async () => (await status(page)).run_summaries[0]?.state_counts.failed, { timeout: 45_000 }).toBe(1)
  await page.getByRole('button', { name: /^查看(?:本次)?问题$/ }).click()
  await expect(page.getByRole('tabpanel', { name: /^问题/ })).toBeVisible()
  await shot(page, info, 'failed-problems')
  await open(page, service.fixture.external_project, 4)
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  await expect.poll(async () => {
    const id = (await status(page)).run_summaries[0]?.run_id
    if (!id) return 0
    const detail = await (await page.request.get(`${service.origin}/api/studio/runs/${id}`)).json() as RunDetailEnvelope
    return detail.run.node_runs.filter((node) => node.state === 'waiting_external').length
  }, { timeout: 45_000 }).toBe(2)
  await page.getByRole('button', { name: '处理外部文件（2）', exact: true }).click()
  const drawer = page.getByRole('region', { name: '被查看处理详情', exact: true })
  await drawer.getByRole('button', { name: /画质增强（2）.*等待外部处理/ }).click()
  await expect(page.getByRole('tab', { name: '文件', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('article', { name: '外部处理：画质增强（2）', exact: true })).toBeVisible()
  await shot(page, info, 'waiting-selected-B')
  await page.setViewportSize({ width: 1280, height: 720 })
  await shot(page, info, 'waiting-1280x720')
  expect((await layout(page)).horizontal_page_overflow).toBe(false)
  expect(errors).toEqual([])
})

test('A 批首次打开：主操作出现即点击外部任务，不等待画布挂载或自动提交', async ({ page }, info) => {
  const errors: string[] = [], commands: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto('about:blank')
  await expect.poll(async () => (await status(page)).active_operation).toBeNull()
  const headers = { Origin: service.origin }
  const opened = await page.request.post(`${service.origin}/api/studio/command`, { headers,
    data: { operation: 'open_project', path: service.fixture.external_project } })
  expect(opened.status()).toBe(200)
  const source = await opened.json() as StatusEnvelope
  const path = join(dirname(service.fixture.external_project), 'batch-a-initial-waiting.zniku')
  const created = await page.request.post(`${service.origin}/api/studio/command`, { headers,
    data: { operation: 'create_project', path, name: '合成初次打开外部任务' } })
  expect(created.status()).toBe(200)
  const fresh = await created.json() as StatusEnvelope
  const graph = { nodes: source.snapshot!.project.graph.nodes.filter((node) => ['source-A', 'enhance-A'].includes(node.node_id)),
    edges: source.snapshot!.project.graph.edges.filter((edge) => edge.target_node_id === 'enhance-A') }
  const saved = await page.request.post(`${service.origin}/api/studio/command`, { headers,
    data: { operation: 'save_project', project_session_id: fresh.project_session_id, expected_storage_revision: fresh.storage_revision,
      project: { ...fresh.snapshot!.project, graph }, studio_state: fresh.studio_state } })
  expect(saved.status()).toBe(200)
  const savedState = await saved.json() as StatusEnvelope
  const started = await page.request.post(`${service.origin}/api/studio/command`, { headers, data: {
    operation: 'run_all', project_session_id: savedState.project_session_id, expected_storage_revision: savedState.storage_revision,
  } })
  expect(started.status()).toBe(200)
  await expect.poll(async () => (await status(page)).run_summaries[0]?.state_counts.waiting_external, { timeout: 45_000 }).toBe(1)
  // 只监听真实点击时的挂载数量，不注入替代 UI、不把节点出现作为点击前置条件。
  await page.addInitScript(() => document.addEventListener('click', (event) => {
    if (event.target instanceof Element && event.target.closest('button')?.textContent === '处理外部文件') {
      (window as unknown as { __batchAInitialNodeCount?: number }).__batchAInitialNodeCount = document.querySelectorAll('.react-flow__node').length
    }
  }, { capture: true }))
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url() === `${service.origin}/api/studio/command`) commands.push((request.postDataJSON() as { operation: string }).operation)
  })
  await page.goto(service.origin)
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await page.getByRole('button', { name: '处理外部文件', exact: true }).click()
  const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
  const drawer = page.getByRole('region', { name: '被查看处理详情', exact: true })
  await expect.poll(async () => await helper.isVisible() || await drawer.isVisible()).toBe(true)
  const navigation = await helper.isVisible() ? 'exact-files' : 'task-drawer-awaiting-detail'
  if (navigation === 'task-drawer-awaiting-detail') await drawer.getByRole('button', { name: /画质增强.*等待外部处理/ }).click()
  await expect(helper.getByRole('article', { name: '外部处理：画质增强', exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: '文件', exact: true })).toHaveAttribute('aria-selected', 'true')
  expect((await status(page)).run_summaries[0]!.state_counts.waiting_external).toBe(1)
  expect(commands).not.toContain('submit_external')
  expect(commands).not.toContain('run_all')
  expect(errors).toEqual([])
  const nodeCountAtClick = await page.evaluate(() => (window as unknown as { __batchAInitialNodeCount?: number }).__batchAInitialNodeCount)
  await shot(page, info, 'initial-external-action')
  await writeFile(info.outputPath('batch-a-initial-action.json'), JSON.stringify({
    purpose: 'Actual first-mount navigation; no sleep or node-mount prerequisite. The observed count is not a forced unmounted scenario.',
    node_count_at_click: nodeCountAtClick, initial_navigation: navigation, automatic_submits: 0,
  }, null, 2), 'utf8')
})
