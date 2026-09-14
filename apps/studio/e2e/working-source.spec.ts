/** 普通work.1真实production三路线；仅合成短媒体，不执行外部AI或用户任务。 */
import { readFile } from 'node:fs/promises'
import { test, expect, type Page } from '@playwright/test'
import axe from 'axe-core'
import { ProductionJournal, SyntheticFixtureHost, maskedScreenshot } from './production-support'
import type { StatusEnvelope, RunDetailEnvelope } from '../src/studio/contracts'
import type { WorkChooseRequest } from '../src/studio/working-source-contracts'
let service: SyntheticFixtureHost, journal: ProductionJournal
test.beforeEach(async ({ page }) => { service = new SyntheticFixtureHost(); await service.start('tools/studio_work_fixture.py'); journal = new ProductionJournal(page, service) })
test.afterEach(async ({}, info) => { if (info.status !== info.expectedStatus) await journal?.capture(info); journal?.dispose(); await service?.stop() })
async function status(page: Page): Promise<StatusEnvelope> { return (await page.request.get(`${service.origin}/api/studio/status`)).json() as Promise<StatusEnvelope> }
async function detail(page: Page, runId: string): Promise<RunDetailEnvelope> { return (await page.request.get(`${service.origin}/api/studio/runs/${runId}`)).json() as Promise<RunDetailEnvelope> }
async function start(page: Page, problem = false) {
  await page.goto(service.origin)
  await page.getByRole('button', { name: /新建视频工程/ }).click()
  await page.getByRole('button', { name: '选择视频素材', exact: true }).click()
  await expect(page.locator('.creator-setup-sources')).toContainText('.mkv')
  if (problem) { await page.getByRole('button', { name: '选择视频素材', exact: true }).click(); await expect(page.locator('.creator-setup-sources')).toContainText('synthetic-work-retime.mkv') }
  await page.getByLabel('工程名称', { exact: true }).fill('Synthetic Working Source')
  await page.getByRole('button', { name: '选择工程保存位置', exact: true }).click()
  await expect(page.locator('.creator-project-path')).toContainText('wizard-output-layout.zniku')
  await page.getByRole('button', { name: '下一步：处理方案' }).click()
  await expect(page.getByLabel('工作流方案')).toHaveValue('working-source')
  await expect(page.getByLabel('工作流方案')).toBeHidden()
  await page.getByRole('button', { name: '下一步：素材检查与准备' }).click()
  await expect(page.getByRole('button', { name: '下一步：处理与成片设置' })).toBeDisabled()
  await page.getByRole('button', { name: /开始检查素材/ }).click()
}
async function accessibility(page: Page) {
  await page.evaluate(axe.source)
  expect(await page.evaluate(async () => (await (window as unknown as { axe: typeof axe }).axe.run()).violations.filter((item) => ['serious', 'critical'].includes(item.impact ?? '')).map((item) => item.id))).toEqual([])
}
function observe(page: Page) {
  const choices: WorkChooseRequest[] = [], commands: string[] = [], errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => { if (request.method() !== 'POST') return
    if (request.url().endsWith('/prepared-color/choose')) choices.push(request.postDataJSON() as WorkChooseRequest)
    if (request.url().endsWith('/api/studio/command')) commands.push((request.postDataJSON() as { operation: string }).operation)
  })
  return { choices, commands, errors }
}
test('普通direct：只确认缺色彩、不复制，展开到增强等待并重开保留exact', async ({ page }, info) => {
  const { choices, errors } = observe(page)
  await start(page)
  await expect(page.getByText('可直接处理', { exact: true })).toBeVisible({ timeout: 90_000 })
  await expect(page.getByRole('button', { name: '确认并使用原件' })).toBeDisabled()
  expect(choices).toHaveLength(0)
  await accessibility(page)
  await page.getByRole('radio', { name: /我确认本工程采用 SDR BT.709/ }).check()
  await page.getByRole('button', { name: '确认并使用原件' }).click()
  await expect(page.getByText('工作素材已准备好，可以继续', { exact: true })).toBeVisible({ timeout: 90_000 })
  expect(choices).toHaveLength(1)
  expect(choices[0]).toMatchObject({ contract_version: '0.3.4-work.1', route: 'direct', confirmations: ['color_interpretation'] })
  const ready = await status(page)
  expect(ready.snapshot!.project.graph.nodes).toHaveLength(3)
  expect(ready.snapshot!.project.graph.nodes.every((node) => node.definition_version === '0.3.4-work.1')).toBe(true)
  await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
  await page.getByLabel('平均章数').fill('2')
  await page.getByLabel('片名', { exact: true }).fill('Synthetic Working Source')
  await page.getByLabel('年份', { exact: true }).fill('2026')
  await page.getByLabel('Program encoder').selectOption('cpu')
  await page.getByRole('button', { name: '下一步：确认工作流' }).click()
  await expect(page.getByRole('region', { name: '确认重叠补帧工作流', exact: true })).toBeVisible({ timeout: 60_000 })
  await maskedScreenshot(page, info.outputPath('work-direct-confirm.png'))
  await accessibility(page)
  await page.getByRole('button', { name: '确认并创建工作流', exact: true }).click()
  await expect(page.getByRole('region', { name: '确认重叠补帧工作流', exact: true })).toBeHidden()
  const expanded = await status(page)
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  await expect.poll(async () => (await status(page)).run_summaries.find((run) => run.state_counts.waiting_external > 0)?.state_counts.waiting_external ?? 0, { timeout: 90_000 }).toBe(2)
  await page.reload()
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await page.getByRole('button', { name: '工程', exact: true }).click()
  await page.getByRole('button', { name: '继续处理向导', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '已有工作流保持不变' })).toBeVisible()
  expect((await status(page)).snapshot).toEqual(expanded.snapshot)
  expect(errors).toEqual([])
})
test('普通builtin：确认真实时间影响后生成保帧重定时副本，并展开到增强等待', async ({ page }, info) => {
  const { choices, errors } = observe(page)
  await start(page, true)
  const original = await readFile(service.fixture.source_preparation_problem!)
  await expect(page.getByText('需要准备工作副本', { exact: true })).toBeVisible({ timeout: 90_000 })
  await expect(page.getByRole('button', { name: '开始生成工作副本' })).toBeDisabled()
  expect(choices).toHaveLength(0)
  await expect(page.getByRole('region', { name: '本次操作的影响与确认' })).toContainText('时长')
  await page.getByRole('radio', { name: /我确认本工程采用 SDR BT.709/ }).check()
  await page.getByRole('checkbox', { name: /确认重新定时/ }).check()
  await accessibility(page)
  await maskedScreenshot(page, info.outputPath('work-retime-confirm.png'))
  await page.getByRole('button', { name: '开始生成工作副本' }).click()
  await expect(page.getByText('工作素材已准备好，可以继续', { exact: true })).toBeVisible({ timeout: 90_000 })
  expect(choices[0]).toMatchObject({ route: 'builtin', confirmations: expect.arrayContaining(['color_interpretation', 'retime']) })
  const current = await status(page)
  expect(current.snapshot!.project.graph.nodes.some((node) => node.type_id === 'zniku.source_preparation.video_prepare.frame_retime')).toBe(true)
  expect(current.snapshot!.project.graph.nodes.some((node) => node.type_id === 'zniku.source_preparation.video_prepare.t1')).toBe(false)
  expect(await readFile(service.fixture.source_preparation_problem!)).toEqual(original)
  await expect(page.getByRole('region', { name: '素材检查与准备' })).toContainText('120 帧')
  await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
  await page.getByLabel('片名', { exact: true }).fill('Synthetic Retimed Reference')
  await page.getByLabel('年份', { exact: true }).fill('2026')
  await page.getByLabel('Program encoder').selectOption('cpu')
  await page.getByRole('button', { name: '下一步：确认工作流' }).click()
  await expect(page.getByRole('region', { name: '重叠流程素材摘要', exact: true })).toContainText('120 帧', { timeout: 60_000 })
  await page.getByRole('button', { name: '确认并创建工作流', exact: true }).click()
  await expect(page.getByRole('region', { name: '确认重叠补帧工作流', exact: true })).toBeHidden()
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  let processingRun = ''
  await expect.poll(async () => {
    const next = (await status(page)).run_summaries.find((run) => run.state_counts.waiting_external > 0)
    processingRun = next?.run_id ?? ''
    return next?.state_counts.waiting_external ?? 0
  }, { timeout: 90_000 }).toBe(1)
  const processing = (await detail(page, processingRun)).run
  expect(processing.node_runs.filter((node) => node.state === 'failed')).toEqual([])
  const waitingNode = processing.node_runs.find((node) => node.state === 'waiting_external')!
  expect(processing.graph_snapshot.nodes.find((node) => node.node_id === waitingNode.node_id)).toMatchObject({ type_id: 'zniku.prepared.overlap.enhancement.external', definition_version: '0.3.4-work.1' })
  expect(errors).toEqual([])
})
test('普通external：不同N新参考显式提交，候选解释重试复用已完成步骤', async ({ page }) => {
  const { choices, commands, errors } = observe(page)
  await start(page, true)
  await expect(page.getByText('需要准备工作副本', { exact: true })).toBeVisible({ timeout: 90_000 })
  // 对原件的解释不能预授权尚未选择的新文件；后端出现候选后才再次确认。
  await page.getByRole('radio', { name: /我确认本工程采用 SDR BT.709/ }).check()
  await page.getByRole('radio', { name: /外部/ }).check()
  await expect(page.getByRole('region', { name: '本工程工作色彩解释' })).toHaveCount(0)
  await page.getByRole('checkbox', { name: /确认采用新的工作参考/ }).check()
  await page.getByRole('button', { name: '建立外部工作源任务' }).click()
  await expect(page.getByRole('button', { name: '打开当前外部工作源助手' })).toBeVisible({ timeout: 90_000 })
  expect(choices[0]).toMatchObject({ route: 'external', interpretation_policy: 'declared_only', confirmations: ['external_reference'] })
  const waiting = (await status(page)).run_summaries.find((run) => run.state_counts.waiting_external > 0)!
  await page.getByRole('button', { name: '打开当前外部工作源助手' }).click()
  const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
  await helper.getByRole('button', { name: '选择处理好的文件', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '确认导入外部处理文件', exact: true })
  await expect(dialog).toBeVisible({ timeout: 90_000 })
  await dialog.getByRole('button', { name: '确认复制到此任务', exact: true }).click()
  await expect(dialog).toBeHidden({ timeout: 90_000 })
  expect(commands).not.toContain('submit_external')
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled({ timeout: 90_000 })
  expect(commands).not.toContain('submit_external')
  await helper.getByRole('button', { name: '提交并继续', exact: true }).click()
  await expect.poll(async () => (await detail(page, waiting.run_id)).run.node_runs.some((node) => node.state === 'failed'), { timeout: 90_000 }).toBe(true)
  const failed = await detail(page, waiting.run_id)
  await page.getByRole('button', { name: '工程', exact: true }).click()
  await page.getByRole('button', { name: '继续处理向导', exact: true }).click()
  await page.getByRole('button', { name: /查看检查与准备/ }).first().click()
  await expect(page.getByText(/以下解释针对本次提交的新参考文件/)).toBeVisible()
  await page.getByRole('radio', { name: /我确认本工程采用 SDR BT.709/ }).check()
  await page.getByRole('button', { name: '结束失败批次并重新检查', exact: true }).click()
  await expect(page.getByText('工作素材已准备好，可以继续', { exact: true })).toBeVisible({ timeout: 90_000 })
  const current = await status(page), latest = current.run_summaries.find((run) => run.run_id !== waiting.run_id && run.state === 'completed' && run.node_count === 4)!
  const reused = await detail(page, latest.run_id)
  await expect(page.getByRole('region', { name: '素材检查与准备' })).toContainText('90 帧')
  for (const old of failed.run.node_runs.filter((node) => node.state === 'completed')) {
    const fresh = reused.run.node_runs.find((node) => node.node_id === old.node_id)!
    expect(fresh.output_artifact_ids).toEqual(old.output_artifact_ids)
    expect(fresh.external_handoff).toBeNull()
  }
  expect(commands.filter((item) => item === 'submit_external')).toHaveLength(1)
  expect(commands.filter((item) => item === 'rerun_from_here')).toHaveLength(1)
  expect(choices).toHaveLength(2)
  await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
  await page.getByLabel('片名', { exact: true }).fill('Synthetic New Reference')
  await page.getByLabel('年份', { exact: true }).fill('2026')
  await page.getByLabel('Program encoder').selectOption('cpu')
  await page.getByRole('button', { name: '下一步：确认工作流' }).click()
  await expect(page.getByRole('region', { name: '重叠流程素材摘要', exact: true })).toContainText('90 帧', { timeout: 60_000 })
  await page.getByRole('button', { name: '确认并创建工作流', exact: true }).click()
  await expect(page.getByRole('region', { name: '确认重叠补帧工作流', exact: true })).toBeHidden()
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  let processingRun = ''
  await expect.poll(async () => {
    const next = (await status(page)).run_summaries.find((run) => run.run_id !== waiting.run_id && run.run_id !== latest.run_id && run.state_counts.waiting_external > 0)
    processingRun = next?.run_id ?? ''
    return next?.state_counts.waiting_external ?? 0
  }, { timeout: 90_000 }).toBe(1)
  expect((await detail(page, processingRun)).run.node_runs.filter((node) => node.state === 'failed')).toEqual([])
  expect(errors).toEqual([])
})
