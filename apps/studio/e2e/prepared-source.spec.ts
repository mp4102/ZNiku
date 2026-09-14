/** 真实生产资源＋正式素材检查/准入/Runtime；全部媒体来自独立短合成 fixture，不运行真实 AI。 */
import { readFile, stat } from 'node:fs/promises'
import { test, expect, type Page } from '@playwright/test'
import axe from 'axe-core'
import { ProductionJournal, SyntheticFixtureHost, maskedScreenshot, selectWorkflowProfile } from './production-support'
import type { StatusEnvelope, RunDetailEnvelope } from '../src/studio/contracts'
import type { PreparedSourceChooseRequest, PreparedSourceFullRequest } from '../src/studio/prepared-source-contracts'

let service: SyntheticFixtureHost
let journal: ProductionJournal
test.beforeEach(async ({ page }) => {
  service = new SyntheticFixtureHost()
  await service.start('tools/studio_prepared_source_fixture.py')
  journal = new ProductionJournal(page, service)
})
test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus) await journal?.capture(info)
  journal?.dispose()
  await service?.stop()
})
async function status(page: Page): Promise<StatusEnvelope> { return await (await page.request.get(`${service.origin}/api/studio/status`)).json() as StatusEnvelope }
async function detail(page: Page, runId: string): Promise<RunDetailEnvelope> { return await (await page.request.get(`${service.origin}/api/studio/runs/${runId}`)).json() as RunDetailEnvelope }
async function accessibility(page: Page) {
  await page.evaluate(axe.source)
  expect(await page.evaluate(async () => (await (window as unknown as { axe: typeof axe }).axe.run()).violations
    .filter((item) => ['serious', 'critical'].includes(item.impact ?? '')).map((item) => item.id))).toEqual([])
}
async function reachCheck(page: Page, problem = false) {
  await page.goto(service.origin)
  await page.getByRole('button', { name: /新建视频工程/ }).click()
  await page.getByRole('button', { name: '选择视频素材', exact: true }).click()
  await expect(page.locator('.creator-setup-sources')).toContainText('.mkv')
  if (problem) {
    await page.getByRole('button', { name: '选择视频素材', exact: true }).click()
    await expect(page.locator('.creator-setup-sources')).toContainText('synthetic-clock-issue.mkv')
  }
  await page.getByLabel('工程名称', { exact: true }).fill('Synthetic Prepared Source')
  await page.getByRole('button', { name: '选择工程保存位置', exact: true }).click()
  await expect(page.locator('.creator-project-path')).toContainText('wizard-output-layout.zniku')
  await page.getByRole('button', { name: '下一步：处理方案' }).click()
  // 此套件锁定旧 0.3.4 行为；新建默认政策升级不能把旧回归悄悄换成新 wire。
  await selectWorkflowProfile(page, 'prepared-source')
  await expect(page.getByLabel('工作流方案')).toHaveValue('prepared-source')
  await page.getByRole('button', { name: '下一步：素材检查与准备' }).click()
  await expect(page.getByLabel('片名', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '下一步：处理与成片设置' })).toBeDisabled()
  expect((await status(page)).snapshot).toBeNull()
}
async function start(page: Page) { await page.getByRole('button', { name: /开始检查素材/ }).click() }
async function returnWizard(page: Page) {
  await page.getByRole('button', { name: '工程', exact: true }).click()
  await page.getByRole('button', { name: '继续处理向导', exact: true }).click()
}
test('正常素材：四输入先检查、自动只读准入、返回不改图、真实展开到增强等待', async ({ page }, info) => {
  const choices: PreparedSourceChooseRequest[] = [], previews: PreparedSourceFullRequest[] = [], errors: string[] = []
  const failedRequests: { path: string, error: string | null }[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error' && !message.text().includes('409')) errors.push(message.text()) })
  page.on('requestfailed', (request) => { if (failedRequests.length < 20) failedRequests.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText ?? null }) })
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/prepared-source/choose')) choices.push(request.postDataJSON() as PreparedSourceChooseRequest)
    if (request.url().endsWith('/prepared-source/full-preview')) previews.push(request.postDataJSON() as PreparedSourceFullRequest)
  })
  await reachCheck(page)
  await start(page)
  await expect(page.getByText('工作源已通过准入，可以继续设置', { exact: true })).toBeVisible({ timeout: 90_000 })
  expect(choices).toHaveLength(1)
  expect(choices[0]!.route).toBe('direct')
  const admitted = await status(page)
  expect(admitted.snapshot!.project.graph.nodes.map((node) => node.type_id)).not.toContain('zniku.source_preparation.video_prepare.t1')
  expect(admitted.snapshot!.project.graph.nodes).toHaveLength(3)
  await accessibility(page)
  await maskedScreenshot(page, info.outputPath('prepared-normal-ready.png'))
  await page.getByRole('button', { name: '上一步' }).click()
  await expect(page.getByLabel('工作流方案')).toBeDisabled()
  await page.getByRole('button', { name: '查看选择素材' }).click()
  await expect(page.getByRole('button', { name: '选择视频素材', exact: true })).toBeDisabled()
  expect((await status(page)).snapshot).toEqual(admitted.snapshot)
  await page.getByRole('button', { name: '查看素材检查与准备' }).click()
  await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
  await expect(page.getByLabel('每段最长时长（分叶）')).toHaveValue('5')
  await page.getByLabel('平均章数').fill('2')
  await page.getByLabel('片名', { exact: true }).fill('Synthetic Prepared Source')
  await page.getByLabel('年份', { exact: true }).fill('2026')
  await page.getByLabel('Program encoder').selectOption('cpu')
  await page.getByRole('button', { name: '下一步：确认工作流' }).click()
  const confirmation = page.getByRole('region', { name: '确认重叠补帧工作流', exact: true })
  await expect(confirmation).toBeVisible({ timeout: 60_000 })
  expect(previews).toHaveLength(1)
  expect(previews[0]!.contract_version).toBe('0.3.4')
  expect(previews[0]!.processing.enhancement.model_version).toBeNull()
  expect((await status(page)).snapshot).toEqual(admitted.snapshot)
  await accessibility(page)
  await page.getByRole('button', { name: '确认并创建工作流', exact: true }).click()
  await expect(confirmation).toBeHidden()
  const expanded = await status(page)
  expect(expanded.snapshot!.project.graph.nodes.some((node) => node.type_id.startsWith('zniku.prepared.overlap.split.'))).toBe(true)
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  let processingRun = ''
  await expect.poll(async () => {
    const current = await status(page)
    const waiting = current.run_summaries.find((run) => run.state_counts.waiting_external > 0)
    processingRun = waiting?.run_id ?? ''
    return waiting?.state_counts.waiting_external ?? 0
  }, { timeout: 90_000 }).toBe(2)
  expect((await detail(page, processingRun)).run.node_runs.filter((node) => node.state === 'failed')).toEqual([])
  await page.reload()
  try { await page.getByRole('button', { name: '关闭工程首页', exact: true }).click() }
  catch (failure) {
    // 保留新页首个异常，避免密集17节点状态投影先耗尽通用诊断事件预算。
    const visibleState = await page.evaluate(() => ({ readyState: document.readyState,
      rootChildren: document.getElementById('root')?.childElementCount,
      scripts: Array.from(document.scripts).map((script) => ({ type: script.type, source: script.src.split('/').at(-1) })),
      resources: performance.getEntriesByType('resource').filter((item) => item.name.includes('/assets/')).map((item) => ({ name: item.name.split('/').at(-1), duration: Math.round(item.duration) })) }))
    throw new Error(`重开页面未挂载：${JSON.stringify({ visibleState, errors, failedRequests })}；${String(failure)}`)
  }
  await returnWizard(page)
  await expect(page.getByRole('dialog', { name: '已有工作流保持不变' })).toBeVisible()
  expect((await status(page)).snapshot).toEqual(expanded.snapshot)
  expect(choices).toHaveLength(1)
  expect(errors).toEqual([])
})

test('时钟问题：问题卡不自动修复、当前助手导入检查显式Submit、工作副本准入后继续', async ({ page }, info) => {
  const choices: PreparedSourceChooseRequest[] = [], commands: string[] = [], errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/prepared-source/choose')) choices.push(request.postDataJSON() as PreparedSourceChooseRequest)
    if (request.url().endsWith('/api/studio/command')) commands.push((request.postDataJSON() as { operation: string }).operation)
  })
  await reachCheck(page, true)
  const originalBytes = await readFile(service.fixture.source_preparation_problem!)
  const repairedBytes = await readFile(service.fixture.source_preparation_repaired!)
  await start(page)
  await expect(page.getByText('素材需要准备', { exact: true })).toBeVisible({ timeout: 90_000 })
  const problems = page.getByRole('region', { name: '素材问题与建议' })
  await expect(problems).toBeVisible()
  await expect(problems).toContainText('推荐怎么做')
  await expect(page.getByRole('button', { name: '下一步：处理与成片设置' })).toBeDisabled()
  expect(choices).toEqual([])
  await expect(page.getByRole('radio', { name: /内置/ })).toBeDisabled()
  await accessibility(page)
  await maskedScreenshot(page, info.outputPath('prepared-clock-finding.png'))
  await page.getByRole('radio', { name: /外部/ }).check()
  await page.getByRole('button', { name: '建立外部保内容修复任务' }).click()
  await expect(page.getByRole('button', { name: '打开当前外部修复助手' })).toBeVisible({ timeout: 60_000 })
  expect(choices).toHaveLength(1)
  expect(choices[0]!.route).toBe('external')
  const waiting = (await status(page)).run_summaries.find((run) => run.state_counts.waiting_external > 0)!
  const attempt = (await detail(page, waiting.run_id)).run.node_runs.find((node) => node.node_id === 'source-preparation-prepare')!
  const target = attempt.external_handoff!.output_targets[0]!.path
  await page.getByRole('button', { name: '打开当前外部修复助手' }).click()
  const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
  await expect(helper).toBeVisible()
  await expect(helper).toContainText('保内容')
  expect(await stat(target).then(() => true, () => false)).toBe(false)
  await helper.getByRole('button', { name: '选择处理好的文件', exact: true }).click()
  const importDialog = page.getByRole('dialog', { name: '确认导入外部处理文件', exact: true })
  await expect(importDialog).toBeVisible({ timeout: 90_000 })
  await importDialog.getByRole('button', { name: '确认复制到此任务', exact: true }).click()
  await expect(importDialog).toBeHidden({ timeout: 90_000 })
  expect(await readFile(target)).toEqual(repairedBytes)
  expect(await readFile(service.fixture.source_preparation_problem!)).toEqual(originalBytes)
  expect((await detail(page, waiting.run_id)).run.node_runs.find((node) => node.node_id === 'source-preparation-prepare')!.state).toBe('waiting_external')
  expect(commands).not.toContain('submit_external')
  const checkRequested = page.waitForRequest((request) => request.url().includes('/handoff-readiness?probe=true'))
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await checkRequested
  await expect.poll(async () => (await status(page)).active_operation, { timeout: 10_000 }).toBe('import_external')
  // 真实同步校验占用普通操作时，停止走独立信号；文件和 waiting 交接均保持。
  const stop = helper.getByRole('button', { name: '停止当前检查或验证', exact: true })
  await expect(stop).toBeVisible()
  page.once('dialog', async (dialog) => dialog.accept())
  const cancelled = page.waitForResponse((response) => response.url().endsWith('/prepared-source/operation-cancel'))
  await stop.click()
  expect((await cancelled).status()).toBe(200)
  await expect(helper.getByRole('button', { name: '检查输出', exact: true })).toBeEnabled({ timeout: 45_000 })
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeDisabled()
  expect((await detail(page, waiting.run_id)).run.node_runs.find((node) => node.node_id === 'source-preparation-prepare')!.state).toBe('waiting_external')
  expect(await readFile(target)).toEqual(repairedBytes)
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled({ timeout: 90_000 })
  expect(commands).not.toContain('submit_external')
  await maskedScreenshot(page, info.outputPath('prepared-external-checked.png'))
  await helper.getByRole('button', { name: '提交并继续', exact: true }).click()
  await expect.poll(async () => (await detail(page, waiting.run_id)).run.node_runs.find((node) => node.node_id === 'source-preparation-admission')?.state, { timeout: 90_000 }).toBe('completed')
  expect(commands.filter((operation) => operation === 'submit_external')).toHaveLength(1)
  await returnWizard(page)
  await page.getByRole('button', { name: /查看检查与准备/ }).first().click()
  await expect(page.getByText('工作源已通过准入，可以继续设置', { exact: true })).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
  await expect(page.getByLabel('平均章数')).toHaveValue('1')
  expect(await readFile(service.fixture.source_preparation_repaired!)).toEqual(repairedBytes)
  expect(errors).toEqual([])
})

test('改名自由图：真实保存与运行后外部助手按当前attempt检查和停止，不依赖向导模板', async ({ page }) => {
  const opened = await page.request.post(`${service.origin}/api/studio/command`, { headers: { Origin: service.origin },
    data: { operation: 'open_project', path: service.fixture.external_project } })
  expect(opened.status()).toBe(200)
  const before = await status(page), project = before.snapshot!.project
  const rename = (id: string) => `custom-${id}`
  const graph = { ...project.graph, nodes: project.graph.nodes.map((node) => ({ ...node, node_id: rename(node.node_id) })),
    edges: project.graph.edges.map((edge) => ({ ...edge, source_node_id: rename(edge.source_node_id), target_node_id: rename(edge.target_node_id) })) }
  const saved = await page.request.post(`${service.origin}/api/studio/graph-save`, { headers: { Origin: service.origin }, data: {
    operation: 'save_project', project_session_id: before.project_session_id, expected_storage_revision: before.storage_revision,
    project: { ...project, graph }, studio_state: before.studio_state } })
  expect(saved.status()).toBe(200)
  await page.goto(service.origin)
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  let runId = ''
  await expect.poll(async () => { const current = await status(page), waiting = current.run_summaries.find((run) => run.state_counts.waiting_external > 0)
    runId = waiting?.run_id ?? ''; return !!waiting }, { timeout: 90_000 }).toBe(true)
  const attempt = (await detail(page, runId)).run.node_runs.find((item) => item.state === 'waiting_external')!
  expect(attempt.node_id).toBe('custom-source-preparation-prepare')
  await page.locator(`.react-flow__node[data-id="${attempt.node_id}"]`).click()
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
  await helper.getByRole('button', { name: '选择处理好的文件', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '确认导入外部处理文件', exact: true })
  await expect(dialog).toBeVisible({ timeout: 90_000 })
  await dialog.getByRole('button', { name: '确认复制到此任务', exact: true }).click()
  await expect(dialog).toBeHidden({ timeout: 90_000 })
  const request = page.waitForRequest((item) => item.url().includes('/handoff-readiness?probe=true'))
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await request
  await expect.poll(async () => (await status(page)).active_operation).toBe('import_external')
  const stopped = page.waitForResponse((response) => response.url().endsWith('/prepared-source/operation-cancel'))
  page.once('dialog', async (confirmation) => confirmation.accept())
  await helper.getByRole('button', { name: '停止当前检查或验证', exact: true }).click()
  const response = await stopped
  expect(response.status()).toBe(200)
  expect(response.request().postDataJSON()).toMatchObject({ run_id: runId, node_run_id: attempt.node_run_id, project_session_id: before.project_session_id })
  await expect(helper.getByRole('button', { name: '检查输出', exact: true })).toBeEnabled({ timeout: 45_000 })
  expect((await detail(page, runId)).run.node_runs.find((item) => item.node_run_id === attempt.node_run_id)!.state).toBe('waiting_external')
  expect((await status(page)).snapshot!.project.graph).toEqual(graph)
})
