/** 生产资源 + 真 Python/SQLite/Runtime；仅原生选择窗口使用显式的合成测试平台。 */
import { test, expect, type Locator, type Page } from '@playwright/test'
import { copyFile, readFile, readdir, stat, writeFile } from 'node:fs/promises'
import { dirname, join, sep } from 'node:path'
import axe from 'axe-core'
import type { GraphWire, RunDetailEnvelope, StatusEnvelope } from '../src/studio/contracts'
import type { StorageInspection } from '../src/studio/host-bridge'
import { maskedScreenshot, ProductionJournal, SyntheticFixtureHost, type SyntheticFixture } from './production-support'

const service = new SyntheticFixtureHost()
let origin: string
let fixture: SyntheticFixture
let journals: ProductionJournal[] = []
test.beforeEach(async ({ page }) => {
  journals = [new ProductionJournal(page, service)]
})
test.beforeAll(async () => {
  await service.start()
  origin = service.origin
  fixture = service.fixture
})
test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus) await journals.at(-1)?.capture(info)
  for (const journal of journals) journal.dispose()
})
test.afterAll(async () => { await service.stop() })

async function accessibility(page: Page, label: string) {
  // 测试引擎注入只发生在此测试进程，不进入 production bundle 或插件执行路径。
  await page.evaluate(axe.source)
  const result = await page.evaluate(async () => {
    const engine = (window as unknown as { axe: typeof axe }).axe
    return (await engine.run()).violations.filter((item) => ['critical', 'serious'].includes(item.impact ?? ''))
      .map((item) => ({ id: item.id, count: item.nodes.length, nodes: item.nodes.slice(0, 5).map((node) => ({ target: node.target, reason: node.failureSummary })) }))
  })
  expect(result, `${label}: critical/serious 必须为零`).toEqual([])
}

async function openProject(page: Page, count: number, home = false) {
  if (!home) await page.getByRole('button', { name: '工程', exact: true }).click()
  await page.getByRole('button', { name: home ? /打开已有工程/ : /^打开工程$/ }).click()
  await expect(page.locator('.project-shell-identity')).toContainText(`合成 ${count} 节点工程`)
  await expect(page.locator('.context-mode')).toHaveText('当前编辑')
  await expect(page.locator('.react-flow__node')).toHaveCount(count)
}

async function focusSource(page: Page) {
  await page.getByRole('textbox', { name: '查找画布节点' }).fill('source.mkv')
  await page.locator('[aria-label="画布搜索结果"]').getByRole('button').first().click()
  await page.getByRole('tab', { name: '设置', exact: true }).click()
  const display = page.getByText('显示设置', { exact: true })
  if (!await display.evaluate((item) => item.closest('details')?.open)) await display.click()
  await expect(page.getByLabel('节点别名')).toBeVisible()
}

async function measured(page: Page, event: 'click' | 'pointermove', action: () => Promise<void>) {
  await page.evaluate((eventName) => {
    const state = window as unknown as { __interaction?: Promise<number> }
    state.__interaction = new Promise<number>((done) => document.addEventListener(eventName, () => {
      const start = performance.now()
      requestAnimationFrame(() => requestAnimationFrame(() => done(performance.now() - start)))
    }, { once: true, capture: true }))
  }, event)
  await action()
  return page.evaluate(async () => (window as unknown as { __interaction: Promise<number> }).__interaction)
}
function p95(values: number[]): number { return [...values].sort((a, b) => a - b)[Math.ceil(values.length * .95) - 1]! }

async function settleCanvas(page: Page) {
  await page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))))
}

async function readStatus(page: Page): Promise<StatusEnvelope> {
  const response = await page.request.get(`${origin}/api/studio/status`)
  expect(response.status()).toBe(200)
  return await response.json() as StatusEnvelope
}

async function assertCanvasGeometry(page: Page, nodeCount: number, edgeCount: number) {
  // 不只检查 DOM 数量或松手后的恢复：缺失 measured 会让仍在 DOM 中的节点全部 visibility:hidden。
  const nodes = await page.locator('.react-flow__node').evaluateAll((items) => items.map((item) => {
    const style = getComputedStyle(item), bounds = item.getBoundingClientRect()
    return { id: item.getAttribute('data-id'), visible: style.visibility === 'visible' && style.display !== 'none' && Number(style.opacity) > 0,
      finite: [bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite), width: bounds.width, height: bounds.height }
  }))
  expect(nodes).toHaveLength(nodeCount)
  expect(nodes.filter((node) => !node.visible || !node.finite || node.width <= 0 || node.height <= 0)).toEqual([])
  const paths = await page.locator('.react-flow__edge-path').evaluateAll((items) => items.map((item) => ({
    d: item.getAttribute('d'), length: (item as SVGPathElement).getTotalLength(),
  })))
  expect(paths).toHaveLength(edgeCount)
  expect(paths.filter((path) => !path.d || /NaN|Infinity/.test(path.d) || !Number.isFinite(path.length) || path.length <= 0)).toEqual([])
}

async function dragSourceContinuously(page: Page, count: number) {
  const source = page.locator('.react-flow__node[data-id="source"]')
  const bounds = await source.boundingBox()
  if (!bounds) throw new Error('连续拖动前 source 必须可见')
  const pathsBefore = await page.locator('.react-flow__edge-path').evaluateAll((items) => items.map((item) => item.getAttribute('d')))
  const x = bounds.x + bounds.width / 2, y = bounds.y + 20
  await page.mouse.move(x, y)
  await page.mouse.down()
  try {
    for (let index = 1; index <= 6; index++) {
      await page.mouse.move(x + index * 9, y + index * 5)
      // 同时覆盖事件提交后与两帧稳定期；只等 ResizeObserver 完成会漏掉每次移动的一帧消失。
      await assertCanvasGeometry(page, count, count - 1)
      await settleCanvas(page)
      // 必须在 pointer 仍按下时验证，避免原缺陷在 mouseup 后恢复而产生假通过。
      await expect(source).toHaveClass(/dragging/)
      await assertCanvasGeometry(page, count, count - 1)
    }
  } finally { await page.mouse.up() }
  await settleCanvas(page)
  await assertCanvasGeometry(page, count, count - 1)
  const pathsAfter = await page.locator('.react-flow__edge-path').evaluateAll((items) => items.map((item) => item.getAttribute('d')))
  expect(pathsAfter).not.toEqual(pathsBefore)
}

async function waitSavedGraph(page: Page, expected: GraphWire) {
  await expect.poll(async () => (await readStatus(page)).snapshot?.project.graph).toEqual(expected)
  await expect(page.locator('.project-shell-identity')).toContainText('已保存')
}

async function assertDragPersistence(page: Page) {
  await settleCanvas(page)
  await expect(page.locator('.project-shell-identity')).toContainText('已保存')
  const before = (await readStatus(page)).snapshot!.project.graph
  const saves: GraphWire[] = []
  const observeSave = (request: import('@playwright/test').Request) => {
    if (request.method() !== 'POST' || request.url() !== `${origin}/api/studio/graph-save`) return
    const command = request.postDataJSON() as { operation: string; project?: { graph: GraphWire } }
    if (command.operation === 'save_project' && command.project) saves.push(command.project.graph)
  }
  page.on('request', observeSave)
  await dragSourceContinuously(page, 50)
  await expect.poll(async () => (await readStatus(page)).snapshot!.project.graph.nodes[0]!.ui_position).not.toEqual(before.nodes[0]!.ui_position)
  await expect(page.locator('.project-shell-identity')).toContainText('已保存')
  page.off('request', observeSave)
  const moved = (await readStatus(page)).snapshot!.project.graph
  expect(saves).toEqual([moved])
  // 只允许正式 ui_position 变化，measured/dragging/selected/尺寸缓存不能进入 Python 领域合同。
  expect({ ...moved, nodes: moved.nodes.map((node, index) => ({ ...node, ui_position: before.nodes[index]!.ui_position })) }).toEqual(before)
  expect(moved.nodes.slice(1)).toEqual(before.nodes.slice(1))
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await waitSavedGraph(page, before)
  await assertCanvasGeometry(page, 50, 49)
  await page.getByRole('button', { name: '重做', exact: true }).click()
  await waitSavedGraph(page, moved)
  await assertCanvasGeometry(page, 50, 49)

  // 页面重开从正式 SQLite 恢复相同 Graph，不能仅靠同一 React session 的临时布局。
  await page.reload()
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await expect(page.locator('.context-mode')).toHaveText('当前编辑')
  await expect(page.locator('.react-flow__node')).toHaveCount(50)
  expect((await readStatus(page)).snapshot!.project.graph).toEqual(moved)
  const domPosition = await page.locator('.react-flow__node[data-id="source"]').evaluate((item) => {
    const transform = new DOMMatrixReadOnly(getComputedStyle(item).transform)
    return { x: transform.e, y: transform.f }
  })
  expect(domPosition.x).toBeCloseTo(moved.nodes[0]!.ui_position!.x, 3)
  expect(domPosition.y).toBeCloseTo(moved.nodes[0]!.ui_position!.y, 3)
  await focusSource(page)
  await page.getByLabel('折叠节点摘要', { exact: true }).check()
  await page.getByRole('button', { name: '缩小画布', exact: true }).click()
  await settleCanvas(page)
  await dragSourceContinuously(page, 50)
  await page.getByLabel('折叠节点摘要', { exact: true }).uncheck()
  await settleCanvas(page)
  await assertCanvasGeometry(page, 50, 49)
  await expect(page.locator('.project-shell-identity')).toContainText('已保存')
}

test('桌面 production：原生代理合同、50/200节点、1000历史、静帧与无障碍', async ({ page }, info) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto(origin)
  await expect(page.getByRole('button', { name: /打开已有工程/ })).toBeEnabled()
  await accessibility(page, '工程首页')
  // 选择器取消不能新建工程或目录；底层取消由正式 HostBridge 消费一次性票据。
  await page.getByRole('button', { name: /空白工作流/ }).click()
  await page.getByRole('button', { name: /选择保存位置/ }).click()
  await expect(page.getByRole('button', { name: /打开已有工程/ })).toBeVisible()
  await openProject(page, 50, true)
  await focusSource(page)
  await accessibility(page, '50 节点与参数表单')
  await assertDragPersistence(page)
  const source = page.locator('.react-flow__node[data-id="source"]')
  const select: number[] = [], expand: number[] = [], drag: number[] = []
  for (let index = 0; index < 20; index++) {
    await source.click({ modifiers: ['Control'] })
    await expect(page.getByLabel('节点别名')).toHaveCount(0)
    select.push(await measured(page, 'click', () => source.click()))
    await page.getByRole('tab', { name: '诊断', exact: true }).click()
    expand.push(await measured(page, 'click', () => page.getByText('高级 → 原始参数', { exact: true }).click()))
    const bounds = await source.boundingBox()
    if (!bounds) throw new Error('合成 source 节点没有可见边界')
    await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 20)
    await page.mouse.down()
    drag.push(await measured(page, 'pointermove', () => page.mouse.move(bounds.x + bounds.width / 2 + 5, bounds.y + 25)))
    await page.mouse.up()
  }
  const metrics = { select_p95_ms: p95(select), parameter_expand_p95_ms: p95(expand), drag_p95_ms: p95(drag), samples_each: 20 }
  console.log('PHASE5_INTERACTION', JSON.stringify(metrics))
  await info.attach('interaction.json', { body: JSON.stringify(metrics), contentType: 'application/json' })
  expect(metrics.select_p95_ms).toBeLessThanOrEqual(150)
  expect(metrics.parameter_expand_p95_ms).toBeLessThanOrEqual(150)
  expect(metrics.drag_p95_ms).toBeLessThanOrEqual(150)
  await expect(page.locator('.project-shell-identity')).toContainText('已保存')
  await openProject(page, 200)
  await page.getByRole('button', { name: '处理记录', exact: true }).click()
  const history = page.getByRole('tabpanel', { name: '历史记录', exact: true })
  await expect(history.locator('.task-drawer-history > li')).toHaveCount(20)
  const status = await (await page.request.get(`${origin}/api/studio/status`)).json() as { run_summaries: { run_id: string }[]; next_run_cursor: string }
  expect(status.run_summaries).toHaveLength(20)
  expect(status.next_run_cursor).toBeTruthy()
  const historicalRun = await history.locator('.task-drawer-history > li > button').nth(1).getAttribute('value')
  if (!historicalRun) throw new Error('合成历史记录必须具有正式 Run ID')
  expect(status.run_summaries.map((summary) => summary.run_id)).toContain(historicalRun)
  const detailUrl = `${origin}/api/studio/runs/${historicalRun}`
  const historicalBefore = await (await page.request.get(detailUrl)).json()
  await history.locator('.task-drawer-history > li > button').nth(1).click()
  await expect(page.locator('.context-mode')).toHaveText('本次处理的流程（只读）')
  await expect(page.locator('.react-flow__node')).toHaveCount(0)
  await page.locator('.canvas-context').getByRole('button', { name: '返回当前编辑', exact: true }).click()
  await page.getByRole('button', { name: '关闭任务区', exact: true }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(200)
  await focusSource(page)
  await page.getByLabel('节点别名').fill('大型图可编辑')
  await page.getByLabel('节点别名').press('Enter')
  await expect(page.locator('.project-shell-identity')).toContainText('已保存')
  expect(await (await page.request.get(detailUrl)).json()).toEqual(historicalBefore)
  await openProject(page, 2)
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  await expect(page.getByRole('button', { name: '查看本次输出', exact: true })).toBeVisible({ timeout: 45_000 })
  await page.getByRole('button', { name: '查看本次输出', exact: true }).click()
  await page.getByRole('region', { name: '本次输出列表', exact: true }).getByRole('button', { name: '查看文件详情', exact: true }).last().click()
  await page.getByText('画面预览与前后比较', { exact: true }).click()
  await page.getByRole('button', { name: '加载画面', exact: true }).click()
  await expect(page.locator('.preview-frames img')).toHaveCount(2)
  const previewWidth = await page.locator('.preview-frames img').first().evaluate((item: HTMLImageElement) => item.naturalWidth)
  expect(previewWidth).toBeGreaterThan(0)
  expect(previewWidth).toBeLessThanOrEqual(640)
  await accessibility(page, '完成输出与 A/B')
  for (const size of [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }, { width: 960, height: 540 }]) {
    await page.setViewportSize(size)
    await expect(page.getByRole('button', { name: /^查看(?:本次)?输出$/ })).toBeVisible()
    await page.screenshot({ path: info.outputPath(`viewport-${size.width}.png`), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await accessibility(page, `视口 ${size.width}`)
  }
  // 键盘打开退出确认并取消，服务与已完成结果不受影响。
  await page.getByRole('button', { name: '工程', exact: true }).click()
  await page.getByRole('button', { name: '退出应用', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog', { name: '退出 ZNIKU Studio' })).toBeVisible()
  await accessibility(page, '退出确认')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: '退出应用', exact: true })).toBeFocused()
  expect(errors).toEqual([])
})

test('高 DPI 与 200% 等效布局仍可键盘访问', async ({ browser }, info) => {
  // 1920×1080 物理像素 / DPR 2 = 960×540 CSS 像素；等效布局测试不冒充修改 Windows 系统设置。
  const context = await browser.newContext({ viewport: { width: 960, height: 540 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
  const page = await context.newPage()
  journals.push(new ProductionJournal(page, service))
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  // 前例失败后 Playwright 会重建 worker/fixture；本例明确打开自己的合成工程，不借用前例状态。
  const opened = await page.request.post(`${origin}/api/studio/command`, {
    headers: { Origin: origin }, data: { operation: 'open_project', path: fixture.media_project },
  })
  expect(opened.status()).toBe(200)
  await page.goto(origin)
  await expect(page.getByRole('button', { name: '关闭工程首页', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).press('Enter')
  await page.getByRole('button', { name: '适应画布', exact: true }).click()
  await accessibility(page, 'DPR 2 / 200% 等效视口')
  expect(await page.evaluate(() => window.devicePixelRatio)).toBe(2)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: info.outputPath('dpi-2.png'), fullPage: true })
  expect(errors).toEqual([])
  await context.close()
})

type WizardStatus = {
  project_path: string; project_session_id: string
  snapshot: { project: { project_id: string; graph: { nodes: { type_id: string; parameters: Record<string, unknown> }[] } } }
  run_summaries: { run_id: string; state: string }[]
}

async function readWizardStatus(page: Page): Promise<WizardStatus> {
  return (await (await page.request.get(`${origin}/api/studio/status`)).json()) as WizardStatus
}

async function exists(path: string): Promise<boolean> {
  try { await stat(path); return true } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false
    throw error
  }
}

async function wizardSettings(page: Page): Promise<Locator> {
  await page.goto(origin)
  await page.getByRole('button', { name: /新建视频工程/ }).click()
  const wizard = page.getByRole('dialog', { name: 'AVEnhanceFlow v2.7.0 创作者向导' })
  await wizard.getByRole('button', { name: /选择视频素材/ }).click()
  await expect(wizard.getByText('av27-source.mkv', { exact: true })).toBeVisible()
  await wizard.getByLabel('工程名称', { exact: true }).fill('合成输出布局工程')
  await wizard.getByRole('button', { name: '选择工程保存位置', exact: true }).click()
  await wizard.getByRole('button', { name: '下一步：处理方案', exact: true }).click()
  await wizard.getByRole('button', { name: '下一步：成片设置', exact: true }).click()
  if (!await wizard.getByLabel('片名', { exact: true }).isVisible()) await openOutputOptions(wizard)
  await wizard.getByLabel('片名', { exact: true }).fill('Synthetic Wizard Output')
  await wizard.getByLabel('年份', { exact: true }).fill('2026')
  // 正式 CPU profile 不依赖测试机器的 NVIDIA 驱动；测试只分析/展开，不运行外部增强或成片编码。
  await wizard.getByLabel('Program encoder', { exact: true }).selectOption('cpu')
  const options = wizard.locator('summary').filter({ hasText: '其他选项（可选）' })
  if (await options.locator('..').evaluate((item) => (item as HTMLDetailsElement).open)) await options.click()
  return wizard
}

async function openOutputOptions(wizard: Locator): Promise<Locator> {
  const summary = wizard.locator('summary').filter({ hasText: '其他选项（可选）' })
  if (!await summary.locator('..').evaluate((item) => (item as HTMLDetailsElement).open)) await summary.click()
  return summary
}

test('生产向导：首步四项设置，高级统一折叠、取消与恢复均不写工程', async ({ page }, info) => {
  // 明确挂载已有自由图，避免单独运行时空工程未挂载 ReactFlow 而漏掉其全局快捷键冲突。
  const opened = await page.request.post(`${origin}/api/studio/command`, {
    headers: { Origin: origin }, data: { operation: 'open_project', path: fixture.media_project },
  })
  expect(opened.status()).toBe(200)
  const errors: string[] = [], studioPosts: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().startsWith(`${origin}/api/studio/`)) {
      studioPosts.push(new URL(request.url()).pathname)
    }
  })
  if (!fixture.data_parent) throw new Error('合成存储门禁必须提供隔离专用磁盘目录')
  await page.goto(origin)
  const productionEntry = await page.locator('script[type="module"][src]').getAttribute('src')
  const productionEntryPath = info.outputPath('storage-ui-production-entry.txt')
  await writeFile(productionEntryPath, productionEntry?.split('/').at(-1) ?? 'missing', 'utf8')
  await info.attach('storage-ui-production-entry', { path: productionEntryPath, contentType: 'text/plain' })
  await page.getByRole('button', { name: /新建视频工程/ }).click()
  const wizard = page.getByRole('dialog', { name: 'AVEnhanceFlow v2.7.0 创作者向导' })
  const advanced = wizard.locator('summary').filter({ hasText: '高级选项（可选）' })
  const dataPicker = wizard.getByRole('button', { name: '选择工作数据父目录', exact: true })
  const storage = wizard.getByRole('region', { name: '工作数据位置', exact: true })
  const next = wizard.getByRole('button', { name: '下一步：处理方案', exact: true })
  await expect(dataPicker).not.toBeVisible()
  await expect(advanced.locator('..')).not.toHaveAttribute('open')
  await expect(storage).not.toBeVisible()
  await expect(wizard.locator('details')).toHaveCount(1)
  const primaryFields = [
    wizard.getByLabel('素材组织方式', { exact: true }),
    wizard.getByRole('button', { name: /选择视频素材/ }),
    wizard.getByLabel('工程名称', { exact: true }),
    wizard.getByRole('button', { name: '选择工程保存位置', exact: true }),
  ]
  for (const width of [1920, 960]) {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 540 })
    // summary 只是披露开关；首步正常表单只能有这四项，不能藏在首屏外再增加必填操作。
    await expect(wizard.locator('.creator-wizard-body input:visible, .creator-wizard-body select:visible, .creator-wizard-body button:visible')).toHaveCount(4)
    for (const field of primaryFields) {
      await expect(field).toBeVisible()
      const bounds = await field.boundingBox()
      expect(bounds!.width).toBeGreaterThanOrEqual(200)
      expect(bounds!.height).toBeGreaterThanOrEqual(32)
      expect(bounds!.height).toBeLessThanOrEqual(100)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await expect(next).toBeInViewport()
    await accessibility(page, `首步四项设置 ${width}`)
    await maskedScreenshot(page, info.outputPath(`wizard-four-fields-${width}.png`))
    if (width === 960) {
      await primaryFields[3]!.scrollIntoViewIfNeeded()
      await expect(primaryFields[3]!).toBeInViewport()
      await maskedScreenshot(page, info.outputPath('wizard-four-fields-project-960.png'))
    }
  }
  await page.setViewportSize({ width: 1920, height: 1080 })
  // 由真实 Chromium 执行 summary 默认键盘行为；不能手动设置 open 来冒充可访问性。
  await advanced.focus()
  await advanced.press('Enter')
  await expect(dataPicker).toBeVisible()
  await advanced.press('Tab')
  await expect(dataPicker).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(advanced).toBeFocused()
  await advanced.press('Enter')
  await expect(dataPicker).not.toBeVisible()
  await advanced.press('Tab')
  await expect(next).toBeFocused()
  await advanced.focus()
  await expect(advanced).toBeFocused()
  await advanced.press('Space')
  await expect(dataPicker).toBeVisible()
  await advanced.press('Space')
  await expect(dataPicker).not.toBeVisible()
  await advanced.press('Tab')
  await expect(next).toBeFocused()
  await wizard.getByRole('button', { name: /选择视频素材/ }).click()
  await expect(wizard.getByText('av27-source.mkv', { exact: true })).toBeVisible()
  await wizard.getByLabel('工程名称', { exact: true }).fill('合成单位置工程')
  await wizard.getByRole('button', { name: '选择工程保存位置', exact: true }).click()
  const before = await readStatus(page)
  const rootListing = await readdir(dirname(fixture.wizard_project))
  const sourceBytes = await readFile(join(dirname(fixture.wizard_project), 'av27-source.mkv'))
  expect(await exists(fixture.wizard_project)).toBe(false)
  expect(await readdir(fixture.data_parent)).toEqual([])
  // 不碰可选磁盘 picker 也可继续；默认位置不能成为另一道必答题。
  await wizard.getByRole('button', { name: '下一步：处理方案', exact: true }).click()
  await expect(wizard.getByRole('button', { name: '下一步：成片设置', exact: true })).toBeVisible()
  await wizard.getByRole('button', { name: '上一步', exact: true }).click()
  await maskedScreenshot(page, info.outputPath('wizard-storage-default-1920.png'))

  // 固定测试平台按真实按钮请求依次返回取消、已选路径、取消，不截获生产 HostBridge 的合同。
  await advanced.click()
  await expect(storage).toContainText('工程旁')
  await expect(wizard.getByRole('region', { name: '手动路径（开发用）', exact: true })).toBeVisible()
  for (const width of [1920, 960]) {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 540 })
    await dataPicker.scrollIntoViewIfNeeded()
    const pickerBounds = await dataPicker.boundingBox()
    expect(pickerBounds!.width).toBeGreaterThanOrEqual(112)
    expect(pickerBounds!.height).toBeLessThanOrEqual(60)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await accessibility(page, `统一高级选项展开 ${width}`)
    await maskedScreenshot(page, info.outputPath(`wizard-advanced-expanded-${width}.png`))
    await wizard.getByLabel('Source 1 path', { exact: true }).scrollIntoViewIfNeeded()
    await expect(wizard.getByLabel('Source 1 path', { exact: true })).toBeInViewport()
    await maskedScreenshot(page, info.outputPath(`wizard-advanced-manual-paths-${width}.png`))
  }
  await dataPicker.click()
  await expect(storage).toContainText('工程旁')
  await expect(wizard.getByRole('button', { name: '恢复工程旁默认', exact: true })).not.toBeVisible()
  await dataPicker.click()
  await expect(storage).toContainText(fixture.data_parent)
  await advanced.click()
  await expect(dataPicker).not.toBeVisible()
  await expect(storage).not.toBeVisible()
  await expect(advanced).toContainText('已自定义工作数据位置')
  await expect(advanced).not.toContainText(fixture.data_parent)
  for (const width of [1920, 960]) {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 540 })
    await advanced.scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await accessibility(page, `工程高级存储折叠摘要 ${width}`)
    await maskedScreenshot(page, info.outputPath(`wizard-storage-custom-collapsed-${width}.png`))
  }
  await advanced.click()
  await expect(storage).toContainText(fixture.data_parent)
  await dataPicker.click()
  await expect(storage).toContainText(fixture.data_parent)
  await wizard.getByRole('button', { name: '恢复工程旁默认', exact: true }).click()
  await expect(storage).toContainText('工程旁')
  await expect(storage).not.toContainText(fixture.data_parent)
  await advanced.click()
  await expect(storage).not.toBeVisible()
  await expect(advanced).not.toContainText('已自定义工作数据位置')
  await expect(dataPicker).not.toBeVisible()
  await wizard.getByRole('button', { name: '下一步：处理方案', exact: true }).click()
  await expect(wizard.getByRole('button', { name: '下一步：成片设置', exact: true })).toBeVisible()
  expect(await readStatus(page)).toEqual(before)
  expect(await readdir(dirname(fixture.wizard_project))).toEqual(rootListing)
  expect(await exists(fixture.wizard_project)).toBe(false)
  expect(await readdir(fixture.data_parent)).toEqual([])
  expect(await readFile(join(dirname(fixture.wizard_project), 'av27-source.mkv'))).toEqual(sourceBytes)
  expect(studioPosts).toEqual([])
  expect(errors).toEqual([])
})

test('生产向导：四组常用设置与工程旁默认输出，改选取消及路径拒绝均无写入', async ({ page }, info) => {
  const opened = await page.request.post(`${origin}/api/studio/command`, {
    headers: { Origin: origin }, data: { operation: 'open_project', path: fixture.media_project },
  })
  expect(opened.status()).toBe(200)
  const errors: string[] = []
  const expectedConsoleErrors: string[] = []
  const commands: string[] = []
  const checks: { request: { layout: string; project_path?: string; output_root?: string } }[] = []
  const checkUrl = `${origin}/api/studio/templates/av-enhance-v27/publication-preview`
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    if (message.location().url === checkUrl && /^Failed to load resource: the server responded with a status of 422(?: |$)/.test(message.text())) {
      // 只允许下面逐字段验证的正式路径拒绝，不放行任意 console error。
      expectedConsoleErrors.push(message.text())
    } else errors.push(message.text())
  })
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    if ([`${origin}/api/studio/command`, `${origin}/api/studio/graph-save`].includes(request.url())) commands.push((request.postDataJSON() as { operation: string }).operation)
    if (request.url() === checkUrl) checks.push(request.postDataJSON() as typeof checks[number])
  })
  const automaticChecks: Promise<unknown>[] = []
  page.on('response', (response) => {
    if (response.url() === checkUrl && response.status() === 200 &&
        response.request().postDataJSON().request.project_path === fixture.wizard_project &&
        response.request().postDataJSON().request.title === 'Synthetic Wizard Output' &&
        !response.request().postDataJSON().processing) automaticChecks.push(response.json())
  })
  const wizard = await wizardSettings(page)
  const before = await readWizardStatus(page)
  const rootBefore = await readdir(dirname(fixture.wizard_project))
  const defaultDirectory = join(dirname(fixture.wizard_project), 'Synthetic Wizard Output (2026)')
  await expect.poll(() => automaticChecks.length).toBeGreaterThan(0)
  expect(await automaticChecks[0]).toMatchObject({ layout: 'title_subdirectory',
    resolved_output_root: dirname(fixture.wizard_project), output_directory: defaultDirectory, will_create_directory: true })
  const options = wizard.locator('summary').filter({ hasText: '其他选项（可选）' })
  const other = options.locator('..')
  const outputPicker = wizard.getByRole('button', { name: '更改成片父目录', exact: true })
  const outputPath = wizard.getByLabel('Publication output root', { exact: true })
  await expect(other).not.toHaveAttribute('open')
  await expect(outputPicker).not.toBeVisible()
  await expect(wizard.locator('.creator-wizard-body details')).toHaveCount(1)
  const groups = ['章节与分段', '画质增强', '章节补帧', '成片编码'].map((name) => wizard.getByRole('region', { name, exact: true }))
  const fields = ['Enhancement model name', 'FI model name', 'Chapter selector mode', 'Leaf duration minutes', 'Program encoder']
    .map((name) => wizard.getByLabel(name, { exact: true }))
  for (const width of [1920, 960]) {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 540 })
    for (const group of groups) { await expect(group).toBeVisible(); expect((await group.boundingBox())!.width).toBeGreaterThanOrEqual(260) }
    for (const field of fields) { await expect(field).toBeVisible(); expect((await field.boundingBox())!.width).toBeGreaterThanOrEqual(120) }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await wizard.getByRole('heading', { name: '成片设置', exact: true }).scrollIntoViewIfNeeded()
    await maskedScreenshot(page, info.outputPath(`wizard-step3-primary-${width}.png`))
    await fields.at(-1)!.scrollIntoViewIfNeeded()
    await expect(wizard.getByRole('button', { name: '下一步：分析', exact: true })).toBeInViewport()
    await accessibility(page, `第3步四组常用设置 ${width}`)
    await maskedScreenshot(page, info.outputPath(`wizard-step3-encoding-${width}.png`))
  }
  await page.setViewportSize({ width: 1920, height: 1080 })
  await wizard.getByLabel('Chapter selector mode', { exact: true }).selectOption('exact_frames')
  await expect(wizard.getByLabel('Exact chapter frames', { exact: true })).toBeVisible()
  await wizard.getByLabel('Exact chapter frames', { exact: true }).fill('899')
  await wizard.getByLabel('Exact chapter frames', { exact: true }).fill('899,899')
  const duplicateFrames = page.waitForResponse((response) => response.url() === checkUrl && response.status() === 422 && response.request().postDataJSON().processing !== undefined)
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  expect((await (await duplicateFrames).json()).error.code).toBe('E_AV27_SETTINGS_CHAPTER_SELECTOR')
  await expect(wizard.getByLabel('Exact chapter frames', { exact: true })).toBeFocused()
  await expect(wizard.getByRole('region', { name: '设置', exact: true })).toBeVisible()
  expect(commands).toEqual([])
  await wizard.getByLabel('Exact chapter frames', { exact: true }).fill('899')
  await wizard.getByLabel('Chapter selector mode', { exact: true }).selectOption('exact_times')
  await expect(wizard.getByLabel('Exact chapter times', { exact: true })).toBeVisible()
  await expect(wizard.getByLabel('Exact chapter frames', { exact: true })).not.toBeVisible()
  await wizard.getByLabel('Exact chapter times', { exact: true }).fill('abc')
  const invalidTime = page.waitForResponse((response) => response.url() === checkUrl && response.status() === 422 && response.request().postDataJSON().processing !== undefined)
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  expect((await (await invalidTime).json()).error.code).toBe('E_AV27_SETTINGS_CHAPTER_SELECTOR')
  await expect(wizard.getByLabel('Exact chapter times', { exact: true })).toBeFocused()
  await expect(wizard.getByRole('region', { name: '设置', exact: true })).toBeVisible()
  expect(commands).toEqual([])
  await wizard.getByLabel('Exact chapter times', { exact: true }).fill('30')
  await wizard.getByLabel('Chapter selector mode', { exact: true }).selectOption('single')
  await expect(wizard.getByLabel('Exact chapter times', { exact: true })).not.toBeVisible()
  // 用真正键盘展开默认关闭的其他选项；后台画布不得抢走 summary 的 Space。
  await options.focus()
  await expect(options).toBeFocused()
  await options.press('Space')
  await expect(outputPicker).toBeVisible()
  await expect(wizard.getByLabel('按片名创建子文件夹', { exact: true })).toBeChecked()
  await options.press('Enter')
  await expect(outputPicker).not.toBeVisible()
  await options.press('Tab')
  await expect(wizard.getByRole('button', { name: '上一步', exact: true })).toBeFocused()
  const repeatedDefault = page.waitForResponse((response) => response.url() === checkUrl && response.status() === 200 &&
    response.request().postDataJSON().processing !== undefined &&
    response.request().postDataJSON().request.project_path === fixture.wizard_project &&
    response.request().postDataJSON().request.title === 'Synthetic Wizard Output')
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  expect(await (await repeatedDefault).json()).toMatchObject({ output_directory: defaultDirectory })
  await expect(wizard.getByRole('button', { name: /开始分析素材/ })).toBeEnabled()
  await wizard.getByRole('button', { name: '上一步', exact: true }).click()
  await openOutputOptions(wizard)
  await outputPicker.click()
  await expect(outputPath).toHaveValue(fixture.output_root)
  await outputPicker.click()
  await expect(outputPath).toHaveValue(fixture.output_root)
  await wizard.getByRole('button', { name: '恢复工程目录', exact: true }).click()
  const restoredDefault = page.waitForResponse((response) => response.url() === checkUrl && response.status() === 200 &&
    response.request().postDataJSON().processing !== undefined &&
    response.request().postDataJSON().request.project_path === fixture.wizard_project &&
    response.request().postDataJSON().request.title === 'Synthetic Wizard Output')
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  expect(await (await restoredDefault).json()).toMatchObject({ output_directory: defaultDirectory })
  await wizard.getByRole('button', { name: '上一步', exact: true }).click()
  await openOutputOptions(wizard)
  await wizard.getByLabel('按片名创建子文件夹', { exact: true }).uncheck()
  expect(await readdir(fixture.output_root)).toEqual([])
  expect(await exists(fixture.wizard_project)).toBe(false)
  expect(await exists(defaultDirectory)).toBe(false)
  expect(await readdir(dirname(fixture.wizard_project))).toEqual(rootBefore)
  await expect(wizard.getByLabel('按片名创建子文件夹', { exact: true })).not.toBeChecked()

  // 固定合成 fixture 是普通文件而不是目录；正式 Python 检查拒绝，不用 HTTP mock 冒充。
  const automaticRefusal = page.waitForResponse((response) => response.url() === checkUrl && response.status() === 422 && response.request().postDataJSON().processing === undefined)
  await wizard.getByLabel('Publication output root', { exact: true }).fill(fixture.output_collision)
  await options.focus()
  expect((await (await automaticRefusal).json()).error.code).toBe('E_AV27_NAMING_ROOT')
  await expect(wizard.getByLabel('成片目录预览', { exact: true })).toContainText('目录暂未通过检查')
  await expect(options).toBeFocused()
  expect(commands).toEqual([])
  await options.click()
  await expect(outputPath).not.toBeVisible()
  const refusedCheck = page.waitForResponse((response) => response.url() === checkUrl && response.status() === 422 && response.request().postDataJSON().processing !== undefined)
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  const refusal = await (await refusedCheck).json() as { error: { code: string; message: string } }
  expect(refusal.error.code).toBe('E_AV27_NAMING_ROOT')
  await expect(wizard.getByRole('region', { name: '设置', exact: true })).toBeVisible()
  await expect(wizard.getByRole('alert')).toBeVisible()
  await expect(outputPath).toBeFocused()
  await expect(other).toHaveAttribute('open')
  await expect(wizard.getByRole('button', { name: /开始分析素材/ })).toHaveCount(0)
  expect(await readWizardStatus(page)).toEqual(before)
  expect(commands).toEqual([])
  for (const size of [{ width: 1920, height: 1080 }, { width: 960, height: 540 }]) {
    await page.setViewportSize(size)
    await wizard.getByRole('heading', { name: '成片设置', exact: true }).scrollIntoViewIfNeeded()
    // axe 不会报告“每个汉字都被挤成一行”；补布局几何门禁，覆盖长路径与新增选项。
    const pickerBounds = await wizard.getByRole('button', { name: '更改成片父目录', exact: true }).boundingBox()
    const layoutBounds = await wizard.getByLabel('按片名创建子文件夹', { exact: true }).locator('..').boundingBox()
    const checkboxBounds = await wizard.getByLabel('按片名创建子文件夹', { exact: true }).boundingBox()
    expect(pickerBounds?.width).toBeGreaterThanOrEqual(112)
    expect(pickerBounds?.height).toBeLessThanOrEqual(60)
    expect(layoutBounds?.width).toBeGreaterThanOrEqual(260)
    expect(layoutBounds?.height).toBeLessThanOrEqual(100)
    expect(checkboxBounds?.width).toBeLessThanOrEqual(24)
    expect(checkboxBounds?.height).toBeLessThanOrEqual(24)
    expect(checkboxBounds!.x - layoutBounds!.x).toBeLessThanOrEqual(4)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await accessibility(page, `输出位置在分析前拒绝 ${size.width}`)
    if (size.width === 960) await wizard.getByLabel('按片名创建子文件夹', { exact: true }).scrollIntoViewIfNeeded()
    await maskedScreenshot(page, info.outputPath(`wizard-output-precheck-${size.width}.png`))
    await outputPath.scrollIntoViewIfNeeded()
    await expect(outputPath).toBeInViewport()
    await maskedScreenshot(page, info.outputPath(`wizard-step3-other-options-${size.width}.png`))
  }
  await page.setViewportSize({ width: 1920, height: 1080 })

  await wizard.getByRole('button', { name: '更改成片父目录', exact: true }).click()
  await wizard.getByLabel('按片名创建子文件夹', { exact: true }).check()
  const groupedCheck = page.waitForResponse((response) => response.url() === checkUrl && response.status() === 200 && response.request().postDataJSON().processing !== undefined)
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  expect(await (await groupedCheck).json()).toMatchObject({
    layout: 'title_subdirectory', resolved_output_root: fixture.output_root,
    output_directory: join(fixture.output_root, 'Synthetic Wizard Output (2026)'), will_create_directory: true,
  })
  await expect(wizard.getByRole('button', { name: /开始分析素材/ })).toBeEnabled()
  expect(await readdir(fixture.output_root)).toEqual([])
  await wizard.getByRole('button', { name: '上一步', exact: true }).click()
  await openOutputOptions(wizard)
  await wizard.getByLabel('按片名创建子文件夹', { exact: true }).uncheck()
  const directCheck = page.waitForResponse((response) => response.url() === checkUrl && response.status() === 200 && response.request().postDataJSON().processing !== undefined)
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  expect(await (await directCheck).json()).toMatchObject({
    layout: 'direct', resolved_output_root: fixture.output_root,
    output_directory: fixture.output_root, will_create_directory: false,
  })
  await wizard.getByRole('button', { name: '关闭模板向导', exact: true }).click()
  await expect(wizard).not.toBeVisible()
  expect(await readdir(fixture.output_root)).toEqual([])
  expect(await exists(fixture.wizard_project)).toBe(false)
  expect(await readWizardStatus(page)).toEqual(before)
  // 自动只读预览可以合并输入或多次重验，不锁定typing请求次数；每次只能有一种路径来源。
  expect(checks.some((check) => check.request.project_path === fixture.wizard_project && check.request.layout === 'title_subdirectory')).toBe(true)
  expect(checks.some((check) => check.request.output_root === fixture.output_collision && check.request.layout === 'direct')).toBe(true)
  expect(checks.some((check) => check.request.output_root === fixture.output_root && check.request.layout === 'title_subdirectory')).toBe(true)
  expect(checks.some((check) => check.request.output_root === fixture.output_root && check.request.layout === 'direct')).toBe(true)
  expect(checks.every((check) => (check.request.project_path !== undefined) !== (check.request.output_root !== undefined))).toBe(true)
  expect(commands).toEqual([])
  expect(expectedConsoleErrors.length).toBeGreaterThan(0)
  expect(errors).toEqual([])
})

test('生产向导：显式直存与片名子目录预览不落盘，布局变化复用原分析后确认', async ({ page }, info) => {
  const errors: string[] = []
  const commands: string[] = []
  const previews: { request: { preparation_run_id: string; publication: { layout: string } } }[] = []
  const previewUrl = `${origin}/api/studio/templates/av-enhance-v27/preview`
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    if ([`${origin}/api/studio/command`, `${origin}/api/studio/graph-save`].includes(request.url())) commands.push((request.postDataJSON() as { operation: string }).operation)
    if (request.url() === previewUrl) {
      const payload = request.postDataJSON() as { action: string; request: { preparation_run_id: string; publication: { layout: string } } }
      if (payload.action === 'expand') previews.push(payload)
    }
  })
  const wizard = await wizardSettings(page)
  expect(await readdir(fixture.output_root)).toEqual([])
  await openOutputOptions(wizard)
  await wizard.getByRole('button', { name: '更改成片父目录', exact: true }).click()
  await wizard.getByLabel('按片名创建子文件夹', { exact: true }).uncheck()
  await expect(wizard.getByLabel('按片名创建子文件夹', { exact: true })).not.toBeChecked()
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  const directPreview = page.waitForResponse((response) => response.url() === previewUrl && response.request().postDataJSON().action === 'expand')
  await wizard.getByRole('button', { name: /开始分析素材/ }).click()
  const direct = await (await directPreview).json() as { plan: { output_target_path: string; output_directory_to_create: string | null } }
  expect(dirname(direct.plan.output_target_path)).toBe(fixture.output_root)
  expect(direct.plan.output_directory_to_create).toBeNull()
  await expect(wizard.getByRole('button', { name: '确认并创建工作流', exact: true })).toBeEnabled({ timeout: 45_000 })
  await expect(wizard.getByText(direct.plan.output_target_path, { exact: true })).toBeVisible()
  expect(await readdir(fixture.output_root)).toEqual([])
  const before = await readWizardStatus(page)
  expect(before.run_summaries).toHaveLength(1)
  expect(before.run_summaries[0]!.state).toBe('completed')
  expect(before.snapshot.project.graph.nodes).toHaveLength(2)
  const runId = before.run_summaries[0]!.run_id
  const detailUrl = `${origin}/api/studio/runs/${runId}`
  const detailBefore = await (await page.request.get(detailUrl)).json() as {
    run: { run_id: string; state: string; node_runs: { state: string; attempt: number }[] }
    artifacts: unknown[]
  }
  expect(detailBefore.run.node_runs).toHaveLength(2)
  expect(detailBefore.run.node_runs.every((node) => node.state === 'completed' && node.attempt === 1)).toBe(true)
  expect(detailBefore.artifacts.length).toBeGreaterThan(0)

  await accessibility(page, '显式直存成片确认')
  await wizard.locator('.creator-target').scrollIntoViewIfNeeded()
  await maskedScreenshot(page, info.outputPath('wizard-direct-output.png'))
  await wizard.getByRole('button', { name: '返回设置', exact: true }).click()
  await openOutputOptions(wizard)
  await wizard.getByLabel('按片名创建子文件夹', { exact: true }).check()
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  const groupedPreview = page.waitForResponse((response) => response.url() === previewUrl && response.request().postDataJSON().action === 'expand')
  await wizard.getByRole('button', { name: '生成工作流预览', exact: true }).click()
  const grouped = await (await groupedPreview).json() as { plan: { output_target_path: string; output_directory_to_create: string | null } }
  const titleDirectory = join(fixture.output_root, 'Synthetic Wizard Output (2026)')
  expect(dirname(grouped.plan.output_target_path)).toBe(titleDirectory)
  expect(grouped.plan.output_directory_to_create).toBe(titleDirectory)
  await expect(wizard.getByRole('button', { name: '确认并创建工作流', exact: true })).toBeEnabled({ timeout: 45_000 })
  await expect(wizard.getByText(grouped.plan.output_target_path, { exact: true })).toBeVisible()
  await expect(wizard.getByText(/将在开始处理后的输出步骤创建文件夹/)).toBeVisible()
  expect(await exists(titleDirectory)).toBe(false)
  expect(await readWizardStatus(page)).toEqual(before)
  expect(await (await page.request.get(detailUrl)).json()).toEqual(detailBefore)
  await accessibility(page, '可选片名子目录与显式创建说明')
  await wizard.locator('.creator-target').scrollIntoViewIfNeeded()
  await maskedScreenshot(page, info.outputPath('wizard-title-directory.png'))

  await wizard.getByRole('button', { name: '返回设置', exact: true }).click()
  await openOutputOptions(wizard)
  await wizard.getByLabel('按片名创建子文件夹', { exact: true }).uncheck()
  await wizard.getByRole('button', { name: '下一步：分析', exact: true }).click()
  await wizard.getByRole('button', { name: '生成工作流预览', exact: true }).click()
  await expect(wizard.getByRole('button', { name: '确认并创建工作流', exact: true })).toBeEnabled({ timeout: 45_000 })
  expect(await readWizardStatus(page)).toEqual(before)
  await wizard.getByRole('button', { name: '确认并创建工作流', exact: true }).click()
  await expect(wizard).not.toBeVisible()
  const after = await readWizardStatus(page)
  expect(after.project_path).toBe(before.project_path)
  expect(after.project_session_id).toBe(before.project_session_id)
  expect(after.snapshot.project.project_id).toBe(before.snapshot.project.project_id)
  expect(after.snapshot.project.graph.nodes.length).toBeGreaterThan(2)
  expect(after.run_summaries).toEqual(before.run_summaries)
  // 展开只修改当前 Graph；原 preparation snapshot、attempt 与 Artifact 逐字段不变。
  expect(await (await page.request.get(detailUrl)).json()).toEqual(detailBefore)
  expect(commands.filter((operation) => operation === 'create_av_enhance_v27')).toHaveLength(1)
  expect(commands.filter((operation) => operation === 'run_all')).toHaveLength(1)
  expect(commands.filter((operation) => operation === 'expand_av_enhance_v27')).toHaveLength(1)
  expect(previews.map((preview) => preview.request.preparation_run_id)).toEqual([runId, runId, runId])
  expect(previews.map((preview) => preview.request.publication.layout)).toEqual(['direct', 'title_subdirectory', 'direct'])
  expect(await readdir(fixture.output_root)).toEqual([])
  expect(after.snapshot.project.graph.nodes.some((node) => node.parameters.target_path === direct.plan.output_target_path)).toBe(true)
  // 分析记录只有 2 个节点；展开后的当前图必须在重开时直接可编辑，而不是被旧 snapshot 遮住。
  await page.reload()
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await expect(page.locator('.context-mode')).toHaveText('当前编辑')
  await expect(page.locator('.react-flow__node')).toHaveCount(after.snapshot.project.graph.nodes.length)
  await page.getByRole('button', { name: '查看本次处理流程', exact: true }).click()
  await expect(page.locator('.context-mode')).toHaveText('本次处理的流程（只读）')
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  await page.locator('.canvas-context').getByRole('button', { name: '返回当前编辑', exact: true }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(after.snapshot.project.graph.nodes.length)
  expect((await readWizardStatus(page)).snapshot.project.graph).toEqual(after.snapshot.project.graph)
  expect(await (await page.request.get(detailUrl)).json()).toEqual(detailBefore)
  // 未选择高级磁盘时，正式 Python 创建相邻 .data 并长期保留；不是仅凭 UI 文案推测路径。
  await page.getByRole('button', { name: '工程', exact: true }).click()
  const storageResponse = page.waitForResponse((response) => response.url() === `${origin}/api/studio/storage/inspect`)
  await page.getByRole('button', { name: '工程数据', exact: true }).click()
  const inspected = await (await storageResponse).json() as StorageInspection
  expect(inspected.storage).toMatchObject({
    mode: 'adjacent', retention: 'keep',
    data_root: fixture.wizard_project.replace(/\.zniku$/, '.data'),
    attempts_root: join(fixture.wizard_project.replace(/\.zniku$/, '.data'), 'attempts'),
  })
  await page.getByRole('button', { name: '关闭工程数据与归档检查', exact: true }).click()
  expect(errors).toEqual([])
})

test('生产外部任务：同名节点不串目标，选择确认导入不提交，错帧不覆盖原目标', async ({ page }, info) => {
  const errors: string[] = [], expectedConsoleErrors: string[] = [], commands: string[] = []
  const previewBindings: { node_run_id: string; target_path?: string }[] = []
  const previewUrl = `${origin}/api/studio/handoff-import/preview`
  const confirmUrl = `${origin}/api/studio/handoff-import/confirm`
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    if (message.location().url === confirmUrl && /^Failed to load resource: the server responded with a status of 422(?: |$)/.test(message.text())) {
      expectedConsoleErrors.push(message.text())
    } else errors.push(message.text())
  })
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    if ([`${origin}/api/studio/command`, `${origin}/api/studio/graph-save`].includes(request.url())) commands.push((request.postDataJSON() as { operation: string }).operation)
    if (request.url() === previewUrl) previewBindings.push(request.postDataJSON() as { node_run_id: string })
  })
  // 初始化仍走正式 Project Service，只给本测试 TemporaryDirectory 中的合成工程；不碰真实操作者工程。
  const opened = await page.request.post(`${origin}/api/studio/command`, {
    headers: { Origin: origin }, data: { operation: 'open_project', path: fixture.external_project },
  })
  expect(opened.status()).toBe(200)
  await page.goto(origin)
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await expect(page.locator('.project-shell-identity')).toContainText('合成外部任务区分工程')
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  await expect.poll(async () => (await readStatus(page)).run_summaries.length).toBe(1)
  const runId = (await readStatus(page)).run_summaries[0]!.run_id
  const detailUrl = `${origin}/api/studio/runs/${runId}`
  const detail = async (): Promise<RunDetailEnvelope> => await (await page.request.get(detailUrl)).json() as RunDetailEnvelope
  await expect.poll(async () => (await detail()).run.node_runs.filter((node) => node.state === 'waiting_external').length, { timeout: 45_000 }).toBe(2)
  const before = await detail()
  const nodeA = before.run.node_runs.find((node) => node.node_id === 'enhance-A')!
  const nodeB = before.run.node_runs.find((node) => node.node_id === 'enhance-B')!
  const targetA = nodeA.external_handoff!.output_targets[0]!.path
  const targetB = nodeB.external_handoff!.output_targets[0]!.path
  const fixtureRoot = dirname(fixture.external_project)
  expect(targetA.startsWith(fixtureRoot)).toBe(true)
  expect(targetB.startsWith(fixtureRoot)).toBe(true)
  expect(targetA).not.toBe(targetB)
  const inputA = await readFile(join(fixtureRoot, 'input-A-12.mkv'))
  const inputB = await readFile(join(fixtureRoot, 'input-B-15.mkv'))
  const goodA = await readFile(join(fixtureRoot, 'external-A-12.mkv'))
  const goodB = await readFile(join(fixtureRoot, 'external-B-15.mkv'))
  const replacementB = await readFile(join(fixtureRoot, 'external-B-replacement-15.mkv'))
  const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
  const queue = page.getByRole('region', { name: '待外部处理任务', exact: true })
  const dialog = page.getByRole('dialog', { name: '确认导入外部处理文件', exact: true })
  const choose = () => helper.getByRole('button', { name: '选择处理好的文件', exact: true }).click()
  const confirm = () => dialog.getByRole('button', { name: '确认复制到此任务', exact: true })
  const assertUnchanged = async () => {
    expect(await detail()).toEqual(before)
    expect(await exists(targetA)).toBe(false)
    expect(commands.filter((operation) => operation === 'submit_external')).toEqual([])
  }
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  await expect(queue).toBeVisible()
  await expect(queue.getByRole('button', { name: '查看外部任务：画质增强（1）', exact: true })).toBeVisible()
  await expect(queue.getByRole('button', { name: '查看外部任务：画质增强（2）', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '复制目标路径', exact: true })).toHaveCount(0)
  await page.locator('.react-flow__node[data-id="enhance-A"]').click()
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  await expect(helper.getByRole('article')).toHaveCount(1)
  await expect(helper.getByRole('article')).toHaveAttribute('aria-label', '外部处理：画质增强（1）')
  await expect(helper.getByText('input-A-12.mkv', { exact: true })).toBeVisible()
  await expect(helper.getByText(targetA, { exact: true }).first()).toBeVisible()
  await expect(helper.getByText('input-B-15.mkv', { exact: true })).toHaveCount(0)
  await page.locator('.react-flow__node[data-id="enhance-B"]').click()
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  await expect(helper.getByRole('article')).toHaveCount(1)
  await expect(helper.getByRole('article')).toHaveAttribute('aria-label', '外部处理：画质增强（2）')
  await expect(helper.getByText('input-B-15.mkv', { exact: true })).toBeVisible()
  await expect(helper.getByText(targetB, { exact: true }).first()).toBeVisible()
  await expect(helper.getByText('input-A-12.mkv', { exact: true })).toHaveCount(0)
  await accessibility(page, '同名外部节点选中详情')
  await helper.getByRole('heading', { name: '外部处理助手', exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('external-selected-task.png'), fullPage: true })

  // 取消原生选择，不创建预览或目标。
  await choose()
  await settleCanvas(page)
  await expect(dialog).not.toBeVisible()
  expect(previewBindings).toEqual([])
  expect(await exists(targetB)).toBe(false)
  await assertUnchanged()
  // 取消确认也不复制；选择到的文件与本任务完整目标在正式只读预览中核对。
  await choose()
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('external-B-15.mkv', { exact: true })).toBeVisible()
  await expect(dialog.getByText(targetB, { exact: true })).toBeVisible()
  await accessibility(page, '外部产物导入确认')
  await page.screenshot({ path: info.outputPath('external-import-confirm.png'), fullPage: true })
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  expect(await exists(targetB)).toBe(false)
  await assertUnchanged()
  await choose()
  await expect(dialog).toBeVisible()
  const imported = page.waitForResponse((response) => response.url() === confirmUrl && response.status() === 200)
  await confirm().click()
  expect(await (await imported).json()).toMatchObject({ status: 'imported', node_run_id: nodeB.node_run_id, target_path: targetB })
  await expect(dialog).not.toBeVisible()
  expect(await readFile(targetB)).toEqual(goodB)
  await assertUnchanged()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeDisabled()
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled()
  await assertUnchanged()

  // 已有目标需独立允许覆盖；取消保留已导入的文件，不能因选了候选就替换。
  await choose()
  await expect(dialog).toBeVisible()
  await expect(dialog.getByLabel('允许替换此任务已有目标文件', { exact: true })).not.toBeChecked()
  await expect(confirm()).toBeDisabled()
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  expect(await readFile(targetB)).toEqual(goodB)
  await assertUnchanged()
  // 将 12 帧产物误投 15 帧任务，即使明确覆盖也必须由正式 validator 拒绝，旧文件不丢失。
  await choose()
  await expect(dialog.getByText('external-A-12.mkv', { exact: true })).toBeVisible()
  await dialog.getByLabel('允许替换此任务已有目标文件', { exact: true }).check()
  const refused = page.waitForResponse((response) => response.url() === confirmUrl && response.status() === 422)
  await confirm().click()
  const refusal = await (await refused).json() as { error: { code: string; message: string } }
  expect(`${refusal.error.code}: ${refusal.error.message}`).toContain('E_MEDIA_TRANSFORM_FRAME_RELATION')
  await expect(dialog).not.toBeVisible()
  await expect(helper.getByRole('alert')).toBeVisible()
  expect(await readFile(targetB)).toEqual(goodB)
  await assertUnchanged()
  await choose()
  await expect(dialog.getByText('external-B-replacement-15.mkv', { exact: true })).toBeVisible()
  await dialog.getByLabel('允许替换此任务已有目标文件', { exact: true }).check()
  await confirm().click()
  await expect(dialog).not.toBeVisible()
  expect(await readFile(targetB)).toEqual(replacementB)
  await assertUnchanged()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeDisabled()
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled()
  await helper.getByRole('button', { name: '提交并继续', exact: true }).click()
  await expect.poll(async () => (await detail()).run.node_runs.find((node) => node.node_id === 'enhance-B')!.state).toBe('completed')
  expect((await detail()).run.node_runs.find((node) => node.node_id === 'enhance-A')!.state).toBe('waiting_external')
  expect(await exists(targetA)).toBe(false)

  await page.locator('.react-flow__node[data-id="enhance-A"]').click()
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  await expect(helper.getByRole('article')).toHaveAttribute('aria-label', '外部处理：画质增强（1）')
  await choose()
  await expect(dialog.getByText(targetA, { exact: true })).toBeVisible()
  await confirm().click()
  await expect(dialog).not.toBeVisible()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeDisabled()
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled()
  // 区分“页面未发送 Submit”和“Python worker 未完成”；票据/header 不进入诊断。
  const submitResponse = page.waitForResponse((response) => response.url() === `${origin}/api/studio/command` &&
    response.request().method() === 'POST' && (response.request().postDataJSON() as { operation?: string }).operation === 'submit_external', { timeout: 15_000 })
  await helper.getByRole('button', { name: '提交并继续', exact: true }).click()
  const submitted = await submitResponse
  expect(submitted.status()).toBe(200)
  expect(submitted.request().postDataJSON()).toMatchObject({ run_id: runId, node_run_id: nodeA.node_run_id, handoff_id: nodeA.external_handoff!.handoff_id })
  await expect.poll(async () => (await detail()).run.state).toBe('completed')
  expect((await detail()).artifacts).toHaveLength(before.artifacts.length + 2)
  expect(await readFile(join(fixtureRoot, 'input-A-12.mkv'))).toEqual(inputA)
  expect(await readFile(join(fixtureRoot, 'input-B-15.mkv'))).toEqual(inputB)
  expect(await readFile(join(fixtureRoot, 'external-A-12.mkv'))).toEqual(goodA)
  expect(await readFile(join(fixtureRoot, 'external-B-15.mkv'))).toEqual(goodB)
  expect(await readFile(join(fixtureRoot, 'external-B-replacement-15.mkv'))).toEqual(replacementB)
  expect(previewBindings.map((item) => item.node_run_id)).toEqual([
    nodeB.node_run_id, nodeB.node_run_id, nodeB.node_run_id, nodeB.node_run_id, nodeB.node_run_id, nodeA.node_run_id,
  ])
  expect(commands.filter((operation) => operation === 'submit_external')).toHaveLength(2)
  expect(expectedConsoleErrors).toHaveLength(1)
  expect(errors).toEqual([])
})

test('生产新标签：读取旧 bootstrap 后迟挂载不会回写并覆盖新偏好', async ({ page }) => {
  const errors: string[] = []
  const writes: unknown[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('request', (request) => {
    if (request.url() === `${origin}/api/desktop/preferences` && request.method() === 'POST') {
      writes.push(request.postDataJSON())
    }
  })
  let releaseScript!: () => void
  let markScriptBlocked!: () => void
  const scriptGate = new Promise<void>((done) => { releaseScript = done })
  const scriptBlocked = new Promise<void>((done) => { markScriptBlocked = done })
  // 精确重现两标签时序：HTML bootstrap 已读取，React production module 尚未执行。
  // 只延迟正式资源的网络交付，不改 production 内容或注入替代应用。
  await page.route(/\/assets\/[^/]+\.js$/, async (route) => {
    markScriptBlocked()
    await scriptGate
    await route.continue()
  })
  try {
    await page.goto(origin, { waitUntil: 'commit' })
    await scriptBlocked
    await page.waitForFunction(() => Boolean((window as unknown as { __ZNIKU_DESKTOP__?: unknown }).__ZNIKU_DESKTOP__))
    const bootstrap = await page.evaluate(() => {
      const state = window as unknown as {
        __ZNIKU_HOST_BRIDGE__: { token: string }
        __ZNIKU_DESKTOP__: {
          instanceId: string
          preferences: { density: 'creator' | 'advanced'; recent_projects: unknown[] }
        }
      }
      return { token: state.__ZNIKU_HOST_BRIDGE__.token, ...state.__ZNIKU_DESKTOP__ }
    })
    const headers = { Origin: origin, 'X-ZNIKU-Host-Token': bootstrap.token }
    const changed = {
      contract_version: '0.3.0', instance_id: bootstrap.instanceId,
      density: bootstrap.preferences.density === 'creator' ? 'advanced' : 'creator',
      recent_projects: bootstrap.preferences.recent_projects,
    }
    // 独立客户端代表另一个已挂载标签刚完成的显式选择；token 只留在此测试内存中。
    const saved = await page.request.post(`${origin}/api/desktop/preferences`, { headers, data: changed })
    expect(saved.status()).toBe(200)
    expect(await saved.json()).toEqual(changed)
    releaseScript()
    await page.waitForLoadState('domcontentloaded')
    await expect(page.getByRole('button', { name: /新建视频工程/ })).toBeVisible()
    // 越过首次 React commit/passive effects，不依赖任意 sleep 等待碰巧的网络时序。
    await page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))))
    expect(writes).toEqual([])
    const persisted = await page.request.get(`${origin}/api/desktop/preferences`, { headers })
    expect(persisted.status()).toBe(200)
    expect(await persisted.json()).toEqual(changed)
    expect(errors).toEqual([])
  } finally {
    releaseScript()
  }
})

test('生产工程数据：工程旁目录、任意来件名显式收纳和归档依赖检查', async ({ page }, info) => {
  const errors: string[] = []
  const commands: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('request', (request) => {
    if ([`${origin}/api/studio/command`, `${origin}/api/studio/graph-save`].includes(request.url()) && request.method() === 'POST') {
      commands.push((request.postDataJSON() as { operation: string }).operation)
    }
  })
  // 独立工程只复用 fixture 已生成的合成输入；所有写入严格位于测试 TemporaryDirectory。
  const opened = await page.request.post(`${origin}/api/studio/command`, {
    headers: { Origin: origin }, data: { operation: 'open_project', path: fixture.external_project },
  })
  expect(opened.status()).toBe(200)
  const template = (await opened.json() as StatusEnvelope).snapshot!
  const graph = { nodes: template.project.graph.nodes.filter((node) => ['source-A', 'enhance-A'].includes(node.node_id)),
    edges: template.project.graph.edges.filter((edge) => edge.target_node_id === 'enhance-A') }
  const projectPath = join(dirname(fixture.external_project), 'inbox-acceptance.zniku')
  const created = await page.request.post(`${origin}/api/studio/command`, {
    headers: { Origin: origin }, data: { operation: 'create_project', path: projectPath, name: '合成工程数据与收件验收' },
  })
  expect(created.status()).toBe(200)
  const fresh = await created.json() as StatusEnvelope
  const saved = await page.request.post(`${origin}/api/studio/command`, {
    headers: { Origin: origin }, data: { operation: 'save_project', project_session_id: fresh.project_session_id,
      expected_storage_revision: fresh.storage_revision, project: { ...fresh.snapshot!.project, graph }, studio_state: fresh.studio_state },
  })
  expect(saved.status()).toBe(200)
  await page.goto(origin)
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await expect(page.locator('.project-shell-identity')).toContainText('合成工程数据与收件验收')
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  await expect.poll(async () => (await readStatus(page)).run_summaries.length).toBe(1)
  const runId = (await readStatus(page)).run_summaries[0]!.run_id
  const detail = async (): Promise<RunDetailEnvelope> => await (await page.request.get(`${origin}/api/studio/runs/${runId}`)).json() as RunDetailEnvelope
  await expect.poll(async () => (await detail()).run.node_runs.filter((node) => node.state === 'waiting_external').length, { timeout: 45_000 }).toBe(1)
  const before = await detail()
  const node = before.run.node_runs.find((item) => item.node_id === 'enhance-A')!
  const target = node.external_handoff!.output_targets[0]!.path
  const dataRoot = projectPath.replace(/\.zniku$/, '.data')
  expect(node.work_dir.startsWith(join(dataRoot, 'attempts') + sep)).toBe(true)
  expect(target.startsWith(node.work_dir + sep)).toBe(true)
  await page.locator('.react-flow__node[data-id="enhance-A"]').click()
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  const inbox = page.getByRole('region', { name: '当前任务收件箱', exact: true })
  await expect(inbox).toBeVisible()
  await expect(inbox.getByText(/收件箱中尚无可用的/)).toBeVisible()
  const inboxPath = await inbox.locator('code.handoff-target-path').innerText()
  expect(inboxPath.startsWith(join(node.work_dir, 'incoming') + sep)).toBe(true)
  expect((await stat(inboxPath)).isDirectory()).toBe(true)
  const original = join(dirname(fixture.external_project), 'external-A-12.mkv')
  const arrival = join(inboxPath, 'Topaz_export_arbitrary_name.mkv')
  const otherArrival = join(inboxPath, 'another_candidate.mkv')
  await copyFile(original, arrival)
  await copyFile(join(dirname(fixture.external_project), 'external-B-15.mkv'), otherArrival)
  await inbox.getByRole('button', { name: '刷新收件箱', exact: true }).click()
  await expect(inbox.getByText(/发现多个候选/)).toBeVisible()
  expect(await detail()).toEqual(before)
  expect(await exists(target)).toBe(false)
  expect(commands.filter((operation) => operation === 'submit_external')).toEqual([])
  await inbox.getByRole('button', { name: '检查并收纳：Topaz_export_arbitrary_name.mkv', exact: true }).click()
  const confirmDialog = page.getByRole('dialog', { name: '确认收纳外部处理文件', exact: true })
  await expect(confirmDialog.getByText(target, { exact: true })).toBeVisible()
  await expect(confirmDialog.getByText(/来件原名称将不再保留/)).toBeVisible()
  await accessibility(page, '收件显式移动确认')
  await page.screenshot({ path: info.outputPath('inbox-collect-confirm.png'), fullPage: true })
  expect(await exists(arrival)).toBe(true)
  const collectedResponse = page.waitForResponse((response) => response.url() === `${origin}/api/studio/handoff-inbox/confirm`)
  await confirmDialog.getByRole('button', { name: '确认检查并收纳', exact: true }).click()
  const collected = await collectedResponse
  expect(collected.status()).toBe(200)
  expect(await collected.json()).toMatchObject({ status: 'collected', node_run_id: node.node_run_id, target_path: target })
  await expect(confirmDialog).not.toBeVisible()
  expect(await exists(arrival)).toBe(false)
  expect(await exists(otherArrival)).toBe(true)
  expect(await readFile(target)).toEqual(await readFile(original))
  expect((await stat(target)).nlink).toBe(1)
  expect(await detail()).toEqual(before)
  expect(commands.filter((operation) => operation === 'submit_external')).toEqual([])
  const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled()
  // 原完成门槛不变，额外确认点击确实提交了当前 attempt，失败包可区分 UI 与 worker。
  const submitResponse = page.waitForResponse((response) => response.url() === `${origin}/api/studio/command` &&
    response.request().method() === 'POST' && (response.request().postDataJSON() as { operation?: string }).operation === 'submit_external', { timeout: 15_000 })
  await helper.getByRole('button', { name: '提交并继续', exact: true }).click()
  const submitted = await submitResponse
  expect(submitted.status()).toBe(200)
  expect(submitted.request().postDataJSON()).toMatchObject({ run_id: runId, node_run_id: node.node_run_id, handoff_id: node.external_handoff!.handoff_id })
  await expect.poll(async () => (await detail()).run.state).toBe('completed')
  await page.getByRole('button', { name: '工程', exact: true }).click()
  await expect(page.getByRole('button', { name: '工程数据', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '工程数据', exact: true }).click()
  const panel = page.getByRole('dialog', { name: '工程数据与归档检查', exact: true })
  await expect(panel.getByText(dataRoot, { exact: true })).toBeVisible()
  await expect(panel.getByText('工程文件旁', { exact: true })).toBeVisible()
  await expect(panel.getByText('工程资产长期保留。', { exact: true })).toBeVisible()
  await expect(panel.getByText('已登记文件未发现缺失。', { exact: true })).toBeVisible()
  await expect(panel.getByText(join(dirname(fixture.external_project), 'input-A-12.mkv'), { exact: true })).toBeVisible()
  await expect(panel.getByText(/不等于完整离线归档/)).toBeVisible()
  await accessibility(page, '工程旁数据与归档依赖检查')
  await page.screenshot({ path: info.outputPath('project-data-inspection.png'), fullPage: true })
  await panel.getByRole('button', { name: '完成', exact: true }).click()
  expect(await exists(otherArrival)).toBe(true)
  expect(await readFile(target)).toEqual(await readFile(original))
  expect(commands.filter((operation) => operation === 'submit_external')).toHaveLength(1)
  expect(errors).toEqual([])
})
