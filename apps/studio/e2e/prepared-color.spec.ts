/** 真实生产资源＋正式素材检查/准入/Runtime；全部媒体来自独立短合成 fixture，不运行真实 AI。 */
import { readFile, stat } from 'node:fs/promises'
import { test, expect, type Page } from '@playwright/test'
import axe from 'axe-core'
import { ProductionJournal, SyntheticFixtureHost, maskedScreenshot, selectWorkflowProfile } from './production-support'
import type { StatusEnvelope, RunDetailEnvelope } from '../src/studio/contracts'
import type { ColorPreparedSourceChooseRequest, ColorPreparedSourceFullRequest } from '../src/studio/prepared-color-contracts'

let service: SyntheticFixtureHost
let journal: ProductionJournal
test.beforeEach(async ({ page }) => {
  service = new SyntheticFixtureHost()
  await service.start('tools/studio_prepared_color_fixture.py')
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
  await selectWorkflowProfile(page, 'prepared-color')
  await expect(page.getByLabel('工作流方案')).toHaveValue('prepared-color')
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
test('缺声明素材：默认阻断与显式工作解释、重开不迁移、完整展开到增强等待', async ({ page }, info) => {
  const choices: ColorPreparedSourceChooseRequest[] = [], previews: ColorPreparedSourceFullRequest[] = [], errors: string[] = []
  const failedRequests: { path: string, error: string | null }[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error' && !message.text().includes('409')) errors.push(message.text()) })
  page.on('requestfailed', (request) => { if (failedRequests.length < 20) failedRequests.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText ?? null }) })
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/prepared-color/choose')) choices.push(request.postDataJSON() as ColorPreparedSourceChooseRequest)
    if (request.url().endsWith('/prepared-color/full-preview')) previews.push(request.postDataJSON() as ColorPreparedSourceFullRequest)
  })
  await reachCheck(page)
  await start(page)
  await expect(page.getByRole('region', { name: '本工程工作色彩解释' })).toBeVisible({ timeout: 90_000 })
  await expect(page.getByRole('radio', { name: /要求素材已明确声明/ })).toBeChecked()
  await expect(page.getByRole('button', { name: '使用原件并检查准入' })).toBeDisabled()
  expect(choices).toHaveLength(0)
  const beforeInterpretation = await status(page)
  expect(beforeInterpretation.snapshot!.project.graph.nodes).toHaveLength(2)
  await accessibility(page)
  await page.getByRole('radio', { name: /我确认本工程采用 SDR BT.709/ }).check()
  expect(choices).toHaveLength(0)
  expect((await status(page)).snapshot).toEqual(beforeInterpretation.snapshot)
  await page.getByRole('button', { name: '使用原件并检查准入' }).click()
  await expect(page.getByText('工作源已通过准入，可以继续设置', { exact: true })).toBeVisible({ timeout: 90_000 })
  expect(choices).toHaveLength(1)
  expect(choices[0]!.route).toBe('direct')
  expect(choices[0]!.interpretation_policy).toBe('operator_confirmed_bt709_limited_left')
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
  expect(previews[0]!.contract_version).toBe('0.3.4-color.1')
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

test('修复与准入分离：默认先保内容修复，准入阻断后明确解释且复用已完成副本', async ({ page }, info) => {
  const choices: ColorPreparedSourceChooseRequest[] = [], commands: string[] = [], errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    if (request.url().endsWith('/prepared-color/choose')) choices.push(request.postDataJSON() as ColorPreparedSourceChooseRequest)
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
  expect(choices[0]!.interpretation_policy).toBe('declared_only')
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
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled({ timeout: 90_000 })
  expect(commands).not.toContain('submit_external')
  await maskedScreenshot(page, info.outputPath('prepared-external-checked.png'))
  await helper.getByRole('button', { name: '提交并继续', exact: true }).click()
  await expect.poll(async () => (await detail(page, waiting.run_id)).run.node_runs.find((node) => node.node_id === 'source-preparation-admission')?.state, { timeout: 90_000 }).toBe('failed')
  expect(commands.filter((operation) => operation === 'submit_external')).toHaveLength(1)
  await returnWizard(page)
  await page.getByRole('button', { name: /查看检查与准备/ }).first().click()
  await expect(page.getByText('素材准备尚未完成', { exact: true })).toBeVisible({ timeout: 30_000 })
  const failedDetail = await detail(page, waiting.run_id)
  const preparedResult = failedDetail.run.node_runs.find((node) => node.node_id === 'source-preparation-prepare')!
  expect(preparedResult.state).toBe('completed')
  await expect(page.getByRole('radio', { name: /要求素材已明确声明/ })).toBeChecked()
  await page.getByRole('radio', { name: /我确认本工程采用 SDR BT.709/ }).check()
  await page.getByRole('button', { name: '更新工作解释并重新检查准入' }).click()
  await expect(page.getByText('工作源已通过准入，可以继续设置', { exact: true })).toBeVisible({ timeout: 90_000 })
  expect(choices).toHaveLength(2)
  expect(choices[1]!.interpretation_policy).toBe('operator_confirmed_bt709_limited_left')
  const finalStatus = await status(page)
  const latest = finalStatus.run_summaries.find((run) => run.run_id !== waiting.run_id && run.state === 'completed' && run.node_count === failedDetail.run.graph_snapshot.nodes.length)!
  const reusedDetail = await detail(page, latest.run_id)
  const reused = reusedDetail.run.node_runs.find((node) => node.node_id === 'source-preparation-prepare')!
  expect(reused.state).toBe('completed')
  expect(reused.output_artifact_ids).toEqual(preparedResult.output_artifact_ids)
  for (const completed of failedDetail.run.node_runs.filter((node) => node.state === 'completed')) {
    const fresh = reusedDetail.run.node_runs.find((node) => node.node_id === completed.node_id)!
    expect(fresh.state).toBe('completed')
    expect(fresh.output_artifact_ids).toEqual(completed.output_artifact_ids)
    expect(fresh.external_handoff).toBeNull()
  }
  expect(commands.filter((operation) => operation === 'rerun_from_here')).toHaveLength(1)
  expect(commands.filter((operation) => operation === 'submit_external')).toHaveLength(1)
  await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
  await expect(page.getByLabel('平均章数')).toHaveValue('1')
  expect(await readFile(service.fixture.source_preparation_repaired!)).toEqual(repairedBytes)
  expect(errors).toEqual([])
})
