/** 独立短合成媒体、真实服务/Runtime/生产UI；原生选择器只返回测试生成文件。 */
import { test, expect, type Page } from '@playwright/test'
import axe from 'axe-core'
import type { RunDetailEnvelope, StatusEnvelope } from '../src/studio/contracts'
import { ProductionJournal, SyntheticFixtureHost, maskedScreenshot } from './production-support'

let service: SyntheticFixtureHost, journal: ProductionJournal
test.beforeEach(async ({ page }) => {
  service = new SyntheticFixtureHost()
  await service.start('tools/studio_chapter_batch_fixture.py')
  journal = new ProductionJournal(page, service)
})
test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus) await journal?.capture(info)
  journal?.dispose()
  await service?.stop()
})
async function detail(page: Page) {
  const status = await (await page.request.get(`${service.origin}/api/studio/status`)).json() as StatusEnvelope
  expect(status.run_summaries).toHaveLength(1)
  // 打开工程不自动恢复 active_run_id；本合成工程仅有这一条持久 Run。
  const response = await page.request.get(`${service.origin}/api/studio/runs/${status.run_summaries[0]!.run_id}`)
  expect(response.status()).toBe(200)
  return await response.json() as RunDetailEnvelope
}
async function helper(page: Page) {
  await page.goto(service.origin)
  const closeHome = page.getByRole('button', { name: '关闭工程首页', exact: true })
  await expect(closeHome).toBeVisible()
  await closeHome.click()
  await page.getByRole('button', { name: /处理外部文件/ }).first().click()
  const panel = page.getByRole('region', { name: '外部处理助手', exact: true })
  await expect(panel.getByRole('button', { name: '选择本章多个文件' })).toBeVisible()
  return panel
}

test('分批补件、重开保留、错误整章检查保持waiting；只一次显式整章Submit', async ({ page }, info) => {
  let submits = 0, confirms = 0
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/handoff-batch/confirm')) confirms++
    if (request.url().endsWith('/command') && request.postDataJSON()?.operation === 'submit_external') submits++
  })
  let panel = await helper(page)
  await expect(panel.getByText('已收 0/2', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '选择本章多个文件' }).click()
  await expect(page.getByRole('dialog', { name: '确认本章收件匹配' })).toBeHidden()
  expect(confirms).toBe(0)
  await panel.getByRole('button', { name: '选择本章多个文件' }).click()
  let dialog = page.getByRole('dialog', { name: '确认本章收件匹配' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '确认收件', exact: true }).click()
  await expect(panel.getByText('已收 1/2', { exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: '检查本章全部输出' })).toBeDisabled()
  expect(submits).toBe(0)
  // 通过正式打开工程命令重读SQLite，不以一次页面刷新冒充工程重开。
  await page.getByRole('button', { name: '工程', exact: true }).click()
  const reopened = page.waitForResponse((response) => response.url() === `${service.origin}/api/studio/command` &&
    response.request().method() === 'POST' && response.request().postDataJSON()?.operation === 'open_project')
  await page.getByRole('button', { name: '打开工程', exact: true }).click()
  expect((await reopened).status()).toBe(200)
  panel = await helper(page)
  await expect(panel.getByText('已收 1/2', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '选择本章多个文件' }).click()
  dialog = page.getByRole('dialog', { name: '确认本章收件匹配' })
  await dialog.getByRole('button', { name: '确认收件', exact: true }).click()
  await expect(panel.getByText('已收 2/2', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '检查本章全部输出' }).click()
  await expect(panel.getByRole('alert')).toContainText('整章检查未通过')
  expect((await detail(page)).run.node_runs.find((node) => node.node_id === 'chapter-A')?.state).toBe('waiting_external')
  await expect(panel.getByRole('button', { name: '提交本章并继续' })).toBeDisabled()
  expect(submits).toBe(0)
  await panel.getByRole('button', { name: '选择本章多个文件' }).click()
  dialog = page.getByRole('dialog', { name: '确认本章收件匹配' })
  await expect(dialog.getByRole('button', { name: '确认收件', exact: true })).toBeDisabled()
  await dialog.getByRole('checkbox', { name: /允许替换已收件/ }).check()
  await dialog.getByRole('button', { name: '确认收件', exact: true }).click()
  await expect(dialog).toBeHidden()
  await panel.getByRole('button', { name: '检查本章全部输出' }).click()
  await expect(panel.getByRole('button', { name: '提交本章并继续' })).toBeEnabled()
  expect(submits).toBe(0)
  await page.evaluate(axe.source)
  const violations = await page.evaluate(async () => (await (window as unknown as { axe: typeof axe }).axe.run()).violations.filter((item) => ['critical', 'serious'].includes(item.impact ?? '')).map((item) => item.id))
  expect(violations).toEqual([])
  await maskedScreenshot(page, info.outputPath('chapter-batch-ready.png'))
  await panel.getByRole('button', { name: '提交本章并继续' }).click()
  await expect.poll(async () => (await detail(page)).run.node_runs.find((node) => node.node_id === 'chapter-A')?.state).toBe('completed')
  const completed = (await detail(page)).run.node_runs.find((node) => node.node_id === 'chapter-A')!
  expect(completed.output_artifact_ids).toHaveLength(2)
  expect(submits).toBe(1)
})

test('目录批量匹配可取消后重选；一次收齐仍不自动提交', async ({ page }) => {
  const panel = await helper(page)
  await panel.getByRole('button', { name: '选择本章文件目录' }).click()
  let dialog = page.getByRole('dialog', { name: '确认本章收件匹配' })
  await expect(dialog.getByRole('combobox')).toHaveCount(2)
  await expect(dialog.getByText('A 章 · 第 1 段', { exact: true })).toBeVisible()
  await expect(dialog.getByText('A 章 · 第 2 段', { exact: true })).toBeVisible()
  const selections = await dialog.getByRole('combobox').evaluateAll((elements) => elements.map((element) => (element as HTMLSelectElement).selectedOptions[0]?.textContent))
  expect(selections[0]).toContain('leaf-0001.enhancement_slp.mov')
  expect(selections[1]).toContain('leaf-0002.enhancement_slp.mov')
  await expect(dialog.getByText(/已自动匹配，请确认/)).toHaveCount(2)
  await expect(dialog.getByText('查看文件位置与角色').first().locator('..')).not.toHaveAttribute('open')
  await dialog.getByRole('button', { name: '取消，不复制' }).click()
  await expect(panel.getByText('已收 0/2', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '选择本章文件目录' }).click()
  dialog = page.getByRole('dialog', { name: '确认本章收件匹配' })
  await dialog.getByRole('button', { name: '确认收件', exact: true }).click()
  await expect(panel.getByText('已收 2/2', { exact: true })).toBeVisible()
  expect((await detail(page)).run.node_runs.find((node) => node.node_id === 'chapter-A')?.state).toBe('waiting_external')
  await expect(panel.getByRole('button', { name: '提交本章并继续' })).toBeDisabled()
})

test('慢检查与提交明确等待响应，三处同任务提示且画布仍可浏览，无重复副作用', async ({ page }, info) => {
  const panel = await helper(page)
  await panel.getByRole('button', { name: '选择本章文件目录' }).click()
  await page.getByRole('dialog', { name: '确认本章收件匹配' }).getByRole('button', { name: '确认收件', exact: true }).click()
  await expect(panel.getByText('已收 2/2', { exact: true })).toBeVisible()
  let releaseCheck!: () => void, checkStarted = false, checks = 0
  const checkGate = new Promise<void>((resolve) => { releaseCheck = resolve })
  await page.route('**/api/studio/handoff-batch/check', async (route) => {
    checks++; checkStarted = true
    await checkGate
    await route.continue()
  })
  await panel.getByRole('button', { name: '检查本章全部输出' }).click()
  await expect.poll(() => checkStarted).toBe(true)
  const global = page.getByLabel('任务摘要', { exact: true })
  await expect(global).toContainText('正在请求整章检查，等待服务响应')
  await expect(global).toContainText('请求等待时间')
  await expect(global).toContainText('本机计时')
  await expect(global.getByRole('progressbar')).toHaveCount(0)
  const card = page.locator('.react-flow__node[data-id="chapter-A"]')
  await expect(card).toContainText('正在请求整章检查，等待服务响应')
  await expect(page.getByRole('region', { name: '步骤处理状态' })).toContainText('正在请求整章检查，等待服务响应')
  await expect(panel.getByRole('button', { name: '等待检查响应…', exact: true })).toBeDisabled()
  // 页面不盖全屏等待层；平移画布只影响视口，不会发出新收件或检查。
  const canvas = page.locator('.react-flow__pane').first(), box = await canvas.boundingBox()
  expect(box).not.toBeNull()
  const before = await page.locator('.react-flow__viewport').getAttribute('style')
  await page.mouse.move(box!.x + box!.width - 40, box!.y + box!.height - 40)
  await page.mouse.down(); await page.mouse.move(box!.x + box!.width - 120, box!.y + box!.height - 80); await page.mouse.up()
  await expect.poll(() => page.locator('.react-flow__viewport').getAttribute('style')).not.toBe(before)
  expect(checks).toBe(1)
  releaseCheck()
  await expect(panel.getByRole('button', { name: '提交本章并继续' })).toBeEnabled()
  await expect(global).toContainText('等待你提交并继续')
  await expect(global.getByLabel('正在处理，进度未知')).toHaveCount(0)
  let releaseSubmit!: () => void, submitStarted = false, submits = 0
  const submitGate = new Promise<void>((resolve) => { releaseSubmit = resolve })
  await page.route('**/api/studio/command', async (route) => {
    if (route.request().postDataJSON()?.operation === 'submit_external') {
      submits++; submitStarted = true
      await submitGate
    }
    await route.continue()
  })
  await panel.getByRole('button', { name: '提交本章并继续' }).click()
  await expect.poll(() => submitStarted).toBe(true)
  await expect(global).toContainText('正在请求提交并复检，等待服务响应')
  await expect(global.getByRole('progressbar')).toHaveCount(0)
  await expect(panel.getByRole('button', { name: '等待提交响应…' })).toBeDisabled()
  await maskedScreenshot(page, info.outputPath('chapter-batch-request-waiting.png'))
  expect(submits).toBe(1)
  releaseSubmit()
  await expect.poll(async () => (await detail(page)).run.node_runs.find((node) => node.node_id === 'chapter-A')?.state).toBe('completed')
  expect(submits).toBe(1)
})

test('20 次只读预览的 click 到可见请求反馈 p95 不超过 150ms，不收件或提交', async ({ page }, info) => {
  const panel = await helper(page), samples: number[] = []
  let copies = 0, checks = 0, submits = 0, previews = 0
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/handoff-batch/confirm')) copies++
    if (request.url().endsWith('/handoff-batch/check')) checks++
    if (request.url().endsWith('/command') && request.postDataJSON()?.operation === 'submit_external') submits++
  })
  let previewGate: Promise<void> = Promise.resolve(), releasePreview = () => {}
  await page.route('**/api/studio/handoff-batch/preview', async (route) => {
    previews++
    await previewGate
    await route.continue()
  })
  for (let index = 0; index < 20; index++) {
    previewGate = new Promise<void>((resolve) => { releasePreview = resolve })
    await page.evaluate(() => {
      const state = window as unknown as { __requestFeedback: Promise<number> }
      state.__requestFeedback = new Promise<number>((done, reject) => {
        document.addEventListener('click', () => {
          const started = performance.now()
          const frame = () => {
            const text = document.querySelector('[aria-label="任务摘要"] .operation-status-text')
            const style = text ? getComputedStyle(text) : null
            if (text?.textContent?.includes('正在准备选择或收件预览') && text.getClientRects().length && style?.visibility === 'visible' && style.display !== 'none') {
              // 再等一个绘制帧；测量真实可见反馈，不把click处理器返回当成完成渲染。
              requestAnimationFrame(() => done(performance.now() - started))
            } else if (performance.now() - started > 5_000) reject(new Error('请求反馈未出现'))
            else requestAnimationFrame(frame)
          }
          requestAnimationFrame(frame)
        }, { once: true, capture: true })
      })
    })
    try {
      await panel.getByRole('button', { name: '选择本章文件目录' }).click()
      samples.push(await page.evaluate(async () => (window as unknown as { __requestFeedback: Promise<number> }).__requestFeedback))
      await expect(page.getByLabel('任务摘要', { exact: true })).toContainText('请求等待时间')
    } finally { releasePreview() }
    const dialog = page.getByRole('dialog', { name: '确认本章收件匹配' })
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: '取消，不复制' }).click()
    await expect(dialog).toBeHidden()
    await expect(panel.getByRole('button', { name: '选择本章文件目录' })).toBeEnabled()
  }
  const p95 = [...samples].sort((left, right) => left - right)[Math.ceil(samples.length * .95) - 1]!
  const metrics = { preview_request_feedback_p95_ms: p95, samples_ms: samples, sample_count: samples.length,
    target_ms: 150, measurement: 'capture click → actual request text visible → next rendering frame',
    boundary: 'read-only preview feedback, not media/check/copy throughput', previews, copies, checks, submits }
  await info.attach('chapter-batch-request-feedback.json', { body: JSON.stringify(metrics), contentType: 'application/json' })
  console.log(JSON.stringify(metrics))
  expect(previews).toBe(20)
  expect([copies, checks, submits]).toEqual([0, 0, 0])
  await expect(panel.getByText('已收 0/2', { exact: true })).toBeVisible()
  expect((await detail(page)).run.node_runs.find((node) => node.node_id === 'chapter-A')?.state).toBe('waiting_external')
  expect(p95).toBeLessThanOrEqual(150)
})
