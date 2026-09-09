/** B 批合法合成 Graph 的生产资源门禁；不接触用户工程、用户媒体或现有服务。 */
import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import type { RunDetailEnvelope } from '../src/studio/contracts'
import { diagnosticValue, maskedScreenshot, ProductionJournal, SyntheticFixtureHost } from './production-support'
import { arrange, assertGeometry, finishFrameAudit, measureCanvas, measuredInteraction, openSynthetic, p95, readStatus,
  savedGraph, semantics, settle, startFrameAudit, waitGeometry } from './batch-b-support'

const service = new SyntheticFixtureHost()
let journal: ProductionJournal, errors: string[]
test.beforeAll(async () => { await service.start('tools/studio_batch_b_fixture.py') })
test.beforeEach(async ({ page }) => {
  journal = new ProductionJournal(page, service); errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
})
test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus && journal.page.url().startsWith(service.origin)) await journal.capture(info)
  journal.dispose(); expect(errors, '不能忽略未知页面异常或 HTTP 失败').toEqual([])
})
test.afterAll(async () => { await service.stop() })
async function openCase(page: Page, name: string) {
  const fixture = service.fixture.batch_b_projects![name]!
  await openSynthetic(page, service, fixture.path, fixture.nodes)
  return fixture
}
async function report(info: TestInfo, name: string, value: unknown) {
  // 原始测量只用于当场断言；公开报告保留全部计数/极值，避免 200 节点坐标挤掉性能结论。
  const compact = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(compact)
    if (!item || typeof item !== 'object') return item
    const record = item as Record<string, unknown>
    if (Array.isArray(record.cards) && Array.isArray(record.routes) && Array.isArray(record.audits)) {
      const measurement = item as Awaited<ReturnType<typeof assertGeometry>>
      const reasons = Object.fromEntries([...new Set(measurement.routes.map((r) => r.reason || 'routed'))]
        .map((reason) => [reason, measurement.routes.filter((r) => (r.reason || 'routed') === reason).length]))
      return { nodes: measurement.cards.length, ports: measurement.ports.length, edges: measurement.routes.length,
        viewport: measurement.viewport, mode: measurement.mode, zoom: measurement.zoom, counters: measurement.counters, reasons,
        max_endpoint_error_css_px: Math.max(0, ...measurement.audits.map((a) => a.endpointError)),
        routed_card_intrusions: measurement.audits.filter((a, i) => measurement.routes[i]!.status === 'routed').reduce((sum, a) => sum + a.collisions.length, 0),
        full_svg_segments_audited: measurement.audits.reduce((sum, a) => sum + a.segments, 0),
        collinear: measurement.collinear,
        page_overflow: measurement.pageOverflow, nodes_intersecting_canvas: measurement.nodesInCanvas,
        nodes_fully_inside_canvas: measurement.fullyInsideCanvas,
        port_identity_errors: 0, port_row_vertical_errors: 0 }
    }
    return Object.fromEntries(Object.entries(record).map(([key, entry]) => [key, compact(entry)]))
  }
  await writeFile(info.outputPath(`${name}.json`), JSON.stringify(diagnosticValue(compact(value), service.roots()), null, 2), 'utf8')
}
async function screenshot(page: Page, info: TestInfo, name: string) {
  await settle(page); await maskedScreenshot(page, info.outputPath(`${name}-masked.png`))
}

test('B 基础 G02 / G05：真实端口外缘与最终 SVG 完整避障', async ({ page }, info) => {
  const observations: Record<string, unknown> = {}
  for (const name of ['G02', 'G05-1', 'G05-2', 'G05-6', 'G05-8', 'G05-16']) {
    const fixture = await openCase(page, name)
    observations[name] = await assertGeometry(page, fixture.nodes, fixture.edges)
    const before = await savedGraph(page, service)
    await arrange(page, true)
    await expect.poll(async () => (await savedGraph(page, service)).nodes.map((n) => n.ui_position))
      .not.toEqual(before.nodes.map((n) => n.ui_position))
    expect(semantics(await savedGraph(page, service))).toEqual(semantics(before))
    await page.getByRole('button', { name: '适应画布', exact: true }).click()
    await waitGeometry(page)
    observations[`${name}-arranged`] = await assertGeometry(page, fixture.nodes, fixture.edges)
    if (name === 'G05-16') {
      await screenshot(page, info, name)
      await page.getByRole('button', { name: '视图', exact: true }).click()
      await page.getByRole('button', { name: '高级节点图', exact: true }).click()
      await waitGeometry(page)
      observations[`${name}-advanced`] = await assertGeometry(page, fixture.nodes, fixture.edges)
      await screenshot(page, info, `${name}-advanced`)
    }
    if (name !== 'G05-16') await screenshot(page, info, name)
  }
  await report(info, 'basic-geometry', observations)
})

test('B G01 / G03 / G04 / G06 / G08：任意合法拓扑、可读摘要及 ordinal 不变', async ({ page }, info) => {
  const observations: Record<string, unknown> = {}
  for (const name of ['G01', 'G03', 'G04-multi', 'G04-zero', 'G06', 'G08']) {
    const fixture = await openCase(page, name), before = await savedGraph(page, service)
    await arrange(page, true)
    await expect.poll(async () => (await savedGraph(page, service)).nodes.map((n) => n.ui_position))
      .not.toEqual(before.nodes.map((n) => n.ui_position))
    const after = await savedGraph(page, service)
    expect(semantics(after)).toEqual(semantics(before)); expect(after.edges).toEqual(before.edges)
    await page.getByRole('button', { name: '适应画布', exact: true }).click()
    const measured = await assertGeometry(page, fixture.nodes, fixture.edges)
    if (name === 'G06') for (const card of measured.cards) {
      expect(card.accessibleTitle).toEqual(card.title)
      expect(card.summaries.join(' ')).not.toMatch(/\[object Object\]|"description"|\[0,\s*1/)
      expect(card.summaries.length).toBeLessThanOrEqual(3)
    }
    if (name === 'G08') expect(new Set(measured.routes.map((r) => r.path)).size).toBe(4)
    observations[name] = measured
    await screenshot(page, info, name)
  }
  await report(info, 'topology-matrix', observations)
})

test('B G07：无关障碍拖入通道后重算，拖动中端点正确且不消失', async ({ page }, info) => {
  const fixture = await openCase(page, 'G07')
  await page.getByRole('button', { name: '适应画布', exact: true }).click()
  const before = await assertGeometry(page, fixture.nodes, fixture.edges)
  const source = before.cards.find((n) => n.id === 'source')!, obstacle = before.cards.find((n) => n.id === 'obstacle')!
  const x = (obstacle.left + obstacle.right) / 2, y = obstacle.top + 18 * before.zoom
  await page.mouse.move(x, y); await page.mouse.down()
  try {
    for (let index = 1; index <= 6; index++) {
      await page.mouse.move(x, y + (source.top - obstacle.top) * index / 6)
      await settle(page)
      const current = await assertGeometry(page, fixture.nodes, fixture.edges, false)
      expect(current.cards.every((n) => n.visible)).toBe(true)
    }
  } finally { await page.mouse.up() }
  await waitGeometry(page)
  const after = await assertGeometry(page, fixture.nodes, fixture.edges)
  expect(after.routes[0]!.path).not.toEqual(before.routes[0]!.path)
  expect(Number(after.counters.solves)).toBeGreaterThan(Number(before.counters.solves))
  await report(info, 'non-neighbor-obstacle', { before, after })
  await screenshot(page, info, 'G07-obstacle-moved')
})

test('B G09：端口遮挡明确降级而不隐藏边，不悄悄移动合法 Graph', async ({ page }, info) => {
  const observations: Record<string, unknown> = {}
  for (const name of ['G09-overlap', 'G09-surround', 'G09-budget']) {
    const fixture = await openCase(page, name), before = await savedGraph(page, service)
    const current = await assertGeometry(page, fixture.nodes, fixture.edges, false)
    const reason = name === 'G09-budget' ? 'channel_budget' : 'blocked_port'
    expect(current.routes.some((route) => route.status === 'degraded' && route.reason === reason)).toBe(true)
    await expect(page.locator('.canvas-route-notice')).toBeVisible()
    expect((await savedGraph(page, service))).toEqual(before)
    observations[name] = current; await screenshot(page, info, name)
  }
  await report(info, 'visible-fallback', observations)
})

test('B G06 错误摘要：真实合成 Runtime 失败后高度改变不丢失端点', async ({ page }, info) => {
  const fixture = await openCase(page, 'G06-error')
  const before = await assertGeometry(page, fixture.nodes, fixture.edges)
  const graph = await savedGraph(page, service)
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  // 失败节点可重试，父 Run 仍为 running/requires_operator_action；不能擅自改成终态语义。
  await expect.poll(async () => (await readStatus(page, service)).run_summaries[0]?.state_counts.failed, { timeout: 45_000 }).toBe(1)
  await expect(page.locator('.react-flow__node[data-id="source-1"] .node-card-problem')).toBeVisible()
  await waitGeometry(page)
  const after = await assertGeometry(page, fixture.nodes, fixture.edges)
  expect((await savedGraph(page, service))).toEqual(graph)
  const height = (m: typeof before) => { const card = m.cards.find((c) => c.id === 'source-1')!; return (card.bottom - card.top) / m.zoom }
  expect(height(after)).toBeGreaterThan(height(before))
  await screenshot(page, info, 'G06-runtime-failure-height')
  await report(info, 'runtime-failure-height', { before, after, graph_unchanged: true })
})

test('B 整理取消 / 单步撤销 / SQLite 重开 / 同 ID 历史视图不改变完成结果', async ({ page }, info) => {
  await openSynthetic(page, service, service.fixture.media_project, 2)
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  await expect.poll(async () => (await readStatus(page, service)).run_summaries[0]?.state, { timeout: 45_000 }).toBe('completed')
  const statusBefore = await readStatus(page, service), before = statusBefore.snapshot!.project.graph
  const runId = statusBefore.run_summaries[0]!.run_id
  const detail = async (): Promise<RunDetailEnvelope> => (await page.request.get(`${service.origin}/api/studio/runs/${runId}`)).json() as Promise<RunDetailEnvelope>
  const completed = await detail()
  expect(completed.artifacts.length).toBeGreaterThan(0)
  // 创建 Run 会明确进入其只读快照；布局编辑要先由用户返回当前编辑图。
  await page.locator('.canvas-context').getByRole('button', { name: '返回当前编辑', exact: true }).click()
  await expect(page.locator('.context-mode')).toHaveText('当前编辑')
  const saves: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url() === `${service.origin}/api/studio/command`)
      saves.push((request.postDataJSON() as { operation: string }).operation)
  })
  await arrange(page, false)
  expect(await savedGraph(page, service)).toEqual(before)
  expect(saves.filter((s) => s === 'save_project')).toEqual([])
  await arrange(page, true)
  await expect.poll(async () => (await savedGraph(page, service)).nodes.map((n) => n.ui_position))
    .not.toEqual(before.nodes.map((n) => n.ui_position))
  const arranged = await savedGraph(page, service)
  expect(semantics(arranged)).toEqual(semantics(before))
  expect(saves.filter((s) => s === 'save_project')).toHaveLength(1)
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect.poll(async () => (await readStatus(page, service)).snapshot!.project.graph).toEqual(before)
  await page.getByRole('button', { name: '重做', exact: true }).click()
  await expect.poll(async () => (await readStatus(page, service)).snapshot!.project.graph).toEqual(arranged)
  await page.getByRole('button', { name: '查看本次处理流程', exact: true }).click()
  await expect(page.locator('.context-mode')).toHaveText('本次处理的流程（只读）')
  await waitGeometry(page); const historyGeometry = await assertGeometry(page, 2, 1)
  await expect(page.getByRole('button', { name: '整理布局', exact: true })).toBeDisabled()
  await screenshot(page, info, 'history-same-node-ids')
  await page.locator('.canvas-context').getByRole('button', { name: '返回当前编辑', exact: true }).click()
  await waitGeometry(page); await assertGeometry(page, 2, 1)
  expect(await detail()).toEqual(completed)
  expect((await readStatus(page, service)).latest_results).toEqual(statusBefore.latest_results)
  await openSynthetic(page, service, service.fixture.media_project, 2)
  expect(await savedGraph(page, service)).toEqual(arranged)
  expect(await detail()).toEqual(completed)
  expect((await readStatus(page, service)).latest_results).toEqual(statusBefore.latest_results)
  await report(info, 'layout-semantics', { nodes: 2, edges: 1, artifacts: completed.artifacts.length,
    total_authoring_saves: saves.filter((s) => s === 'save_project').length, initial_layout_save_count: 1, cancel_unchanged: true,
    undo_redo_single_step: true, persisted: true, completed_run_unchanged: true, historyGeometry })
})

test('B G10：50 / 200 交互预算，路由缓存与 1000 条历史按需有界读取', async ({ page }, info) => {
  const observations: Record<string, unknown> = {}
  for (const count of [50, 200]) {
    const detailReads: string[] = [], pages: number[] = []
    const listener = (request: import('@playwright/test').Request) => {
      const path = new URL(request.url()).pathname
      if (/^\/api\/studio\/runs\/[^/]+$/.test(path)) detailReads.push(path)
      if (path === '/api/studio/runs') pages.push(Number(new URL(request.url()).searchParams.get('limit') ?? '50'))
    }
    page.on('request', listener)
    const fixture = await openCase(page, `G10-${count}`)
    await arrange(page, true)
    await page.getByRole('button', { name: '适应画布', exact: true }).click()
    const arranged = await assertGeometry(page, count, fixture.edges, false)
    await screenshot(page, info, `G10-${count}-overview`)
    // 从完整图明确选取两个真实节点并缩放到选区；计时中交替改变选中身份，不测 no-op。
    const node = page.locator('.react-flow__node[data-id="n-000"]')
    const other = page.locator('.react-flow__node[data-id="n-001"]')
    await page.getByRole('textbox', { name: '查找画布节点' }).fill('n-000')
    await page.locator('[aria-label="画布搜索结果"]').getByRole('button').first().click()
    await other.locator('.node-card-title').click({ modifiers: ['Control'] })
    await expect(page.locator('.react-flow__node.selected')).toHaveCount(2)
    await page.getByRole('button', { name: '缩放到选区', exact: true }).click()
    await waitGeometry(page)
    await node.locator('.node-card-title').click()
    await expect(page.locator('.inspector-heading h2')).toContainText('n-000')
    await page.getByRole('tab', { name: '诊断', exact: true }).click()
    await waitGeometry(page)
    const cached = await measureCanvas(page)
    const selection: number[] = [], parameters: number[] = [], drag: number[] = []
    for (let index = 0; index < 20; index++) {
      const target = index % 2 ? node : other, identifier = index % 2 ? 'n-000' : 'n-001'
      selection.push(await measuredInteraction(page, 'click', () => target.locator('.node-card-title').click()))
      await expect(page.locator('.react-flow__node.selected')).toHaveCount(1)
      await expect(target).toHaveClass(/selected/)
      await expect(page.locator('.inspector-heading h2')).toContainText(identifier)
    }
    for (let index = 0; index < 20; index++) {
      const control = page.getByText('高级 → 原始参数', { exact: true })
      expect(await control.evaluate((element) => element.closest('details')!.open)).toBe(false)
      parameters.push(await measuredInteraction(page, 'click', () => page.getByText('高级 → 原始参数', { exact: true }).click()))
      expect(await control.evaluate((element) => element.closest('details')!.open)).toBe(true)
      await expect(page.locator('.inspector-workspace textarea')).toBeVisible()
      // 关闭在计时之外；20 个样本全部是实际处理参数编辑区从关闭变为打开。
      await control.click()
      await expect(page.locator('.inspector-workspace textarea')).not.toBeVisible()
      await settle(page)
    }
    const noChange = await measureCanvas(page)
    expect(noChange.counters.solves).toEqual(cached.counters.solves)
    // 指针每次移动后两帧测量，而不是把 Playwright 通讯/等待选择器混入交互延迟。
    const b = (await node.boundingBox())!, x = b.x + b.width / 2, y = b.y + 18
    const position = () => node.evaluate((element) => {
      const matrix = new DOMMatrixReadOnly(getComputedStyle(element).transform)
      return { x: matrix.e, y: matrix.f }
    })
    let previousPosition = await position()
    const dragPositions: Array<{ x: number; y: number }> = []
    await startFrameAudit(page)
    await page.mouse.move(x, y); await page.mouse.down()
    try {
      // 起始阈值阶段不冒充真实位移样本；先确认拖动态及第一段位移，再测持续拖动的 20 次变化。
      await page.mouse.move(x + 18, y + 10); await settle(page)
      await page.mouse.move(x + 22, y + 12)
      await expect(node).toHaveClass(/dragging/)
      await expect.poll(position).not.toEqual(previousPosition)
      previousPosition = await position()
      for (let index = 0; index < 20; index++) {
        drag.push(await measuredInteraction(page, 'pointermove', () => page.mouse.move(x + (index % 2 ? 30 : 26), y + (index % 2 ? 18 : 14))))
        const currentPosition = await position()
        expect(currentPosition).not.toEqual(previousPosition)
        dragPositions.push(currentPosition); previousPosition = currentPosition
      }
    } finally { await page.mouse.up() }
    await waitGeometry(page)
    const frameAudit = await finishFrameAudit(page)
    const afterDrag = await assertGeometry(page, count, fixture.edges, false)
    const beforeCard = noChange.cards.find((card) => card.id === 'n-000')!, afterCard = afterDrag.cards.find((card) => card.id === 'n-000')!
    expect({ left: afterCard.left, top: afterCard.top }).not.toEqual({ left: beforeCard.left, top: beforeCard.top })
    if (count === 50) for (const [kind, samples] of Object.entries({ selection, parameters, drag }))
      expect(p95(samples), `50 节点 ${kind} 两帧交互 p95`).toBeLessThanOrEqual(150)
    // 200 节点不是 50 节点的响应时间承诺，但事件必须持续完成，不能堆积无界路由队列。
    expect(Math.max(...selection, ...parameters, ...drag)).toBeLessThan(2000)
    expect(frameAudit.maxSolvesPerFrame).toBeLessThanOrEqual(1)
    expect(frameAudit.solves).toBeGreaterThan(0)
    expect(frameAudit.solves).toBeLessThanOrEqual(frameAudit.frames + 1)
    await settle(page); await settle(page)
    expect((await measureCanvas(page)).counters.solves).toEqual(afterDrag.counters.solves)
    if (count === 200) {
      const current = await readStatus(page, service)
      expect(current.run_summaries.length).toBeGreaterThan(0); expect(current.run_summaries.length).toBeLessThanOrEqual(50)
      expect(current.next_run_cursor).not.toBeNull()
      await page.getByRole('button', { name: '处理记录', exact: true }).click()
      await expect(page.locator('.task-drawer-history > li')).toHaveCount(current.run_summaries.length)
      const beforeMore = await measureCanvas(page)
      await page.getByRole('button', { name: '加载更早记录', exact: true }).click()
      await expect.poll(async () => page.locator('.task-drawer-history > li').count()).toBeGreaterThan(current.run_summaries.length)
      expect(await page.locator('.task-drawer-history > li').count()).toBeLessThanOrEqual(100)
      expect(detailReads.length).toBeLessThanOrEqual(2)
      expect(pages.every((limit) => limit > 0 && limit <= 50)).toBe(true)
      expect((await measureCanvas(page)).counters.solves).toEqual(beforeMore.counters.solves)
      await screenshot(page, info, 'G10-bounded-history')
    }
    observations[count] = { arranged, afterDrag, samples: { selection, parameters, drag }, frameAudit, dragPositions,
      p95: { selection: p95(selection), parameters: p95(parameters), drag: p95(drag) },
      detail_reads: detailReads.length, requested_page_limits: pages, selection_changes: 20, parameter_opens: 20,
      drag_measurement: '20 ongoing pointer moves after an explicitly verified drag start',
      selection_and_parameter_extra_solves: 0 }
    page.off('request', listener)
  }
  await report(info, 'performance-and-history', observations)
})

test('B DPI 模拟与 viewport-pressure：显式适应、动态折叠、键盘路径追踪', async ({ browser }, info) => {
  const observations: Record<string, unknown> = {}
  for (const dpr of [1, 1.25, 1.5]) {
    // Chromium deviceScaleFactor 只模拟渲染像素比，不能宣称替代实际 Windows DPI 验收。
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: dpr, reducedMotion: 'reduce' })
    const page = await context.newPage(), local = new ProductionJournal(page, service)
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
    try {
      const fixture = await openCase(page, 'G06')
      await arrange(page, true)
      await expect(page.locator('.react-flow__minimap')).not.toBeVisible()
      await page.getByRole('button', { name: '展开任务区', exact: true }).click()
      await page.getByRole('button', { name: '适应画布', exact: true }).click()
      await expect.poll(async () => (await measureCanvas(page)).fullyInsideCanvas).toBe(fixture.nodes)
      const before = await assertGeometry(page, fixture.nodes, fixture.edges)
      expect(before.pageOverflow).toBe(false); expect(before.fullyInsideCanvas).toBe(fixture.nodes)
      await screenshot(page, info, `dpi-${dpr}-pressure-fitted`)
      const graphBeforeMinimap = await savedGraph(page, service)
      const viewportBeforeMinimap = await page.locator('.react-flow__viewport').getAttribute('style')
      await page.getByRole('button', { name: '显示小地图', exact: true }).click()
      await expect(page.locator('.react-flow__minimap')).toBeVisible()
      await page.getByRole('button', { name: '隐藏小地图', exact: true }).click()
      await expect(page.locator('.react-flow__minimap')).not.toBeVisible()
      expect(await page.locator('.react-flow__viewport').getAttribute('style')).toEqual(viewportBeforeMinimap)
      expect(await savedGraph(page, service)).toEqual(graphBeforeMinimap)
      // 先明确收起任务区并定位节点，避免把不可读缩略图冒充参数可操作性。
      await page.getByRole('button', { name: '收起任务区', exact: true }).click()
      await page.getByRole('textbox', { name: '查找画布节点' }).fill('长名称分支 source')
      await page.locator('[aria-label="画布搜索结果"]').getByRole('button').first().click()
      await page.getByRole('tab', { name: '设置', exact: true }).click()
      await page.getByText('显示设置', { exact: true }).click()
      const graph = await savedGraph(page, service)
      await page.getByLabel('折叠节点摘要', { exact: true }).check()
      await waitGeometry(page)
      const collapsed = await assertGeometry(page, fixture.nodes, fixture.edges)
      expect(semantics(await savedGraph(page, service))).toEqual(semantics(graph))
      const sourceCard = (m: typeof before) => m.cards.find((card) => card.id === 'source')!
      // 缩放不同，比较画布单位中的高度，而不是屏幕 CSS 高度。
      expect((sourceCard(collapsed).bottom - sourceCard(collapsed).top) / collapsed.zoom)
        .toBeLessThan((sourceCard(before).bottom - sourceCard(before).top) / before.zoom)
      const label = page.locator('.node-port-label[data-node-id="source"][data-port-direction="output"]').first()
      await label.focus(); await settle(page)
      const focused = await measureCanvas(page)
      expect(focused.routes.filter((route) => route.source === 'source').every((route) => route.highlighted)).toBe(true)
      await screenshot(page, info, `dpi-${dpr}-keyboard-port-focus`)
      await page.getByLabel('折叠节点摘要', { exact: true }).uncheck()
      await waitGeometry(page)
      // 下一 DPI context 重开同一 SQLite，必须等正式展示保存完成，不能关页丢掉 debounce 草稿。
      await expect.poll(async () => (await readStatus(page, service)).studio_state?.node_views.find((node) => node.node_id === 'source')?.collapsed).toBe(false)
      await expect(page.locator('.project-shell-identity')).toContainText('已保存')
      observations[String(dpr)] = { before, collapsed, keyboard_highlighted: focused.routes.filter((r) => r.highlighted).length,
        dpi_scope: 'Chromium deviceScaleFactor simulation; not native Windows DPI' }
    } catch (error) { await local.capture(info); throw error } finally { local.dispose(); await context.close() }
  }
  await report(info, 'dpi-and-viewport-pressure', observations)
})
