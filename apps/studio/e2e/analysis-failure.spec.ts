/** 合成不均匀 PTS 经正式 Source/Runtime 失败；production UI 不能把失败误报为 MR 等待。 */
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { test, expect, type Page } from '@playwright/test'
import axe from 'axe-core'
import type { StatusEnvelope, RunDetailEnvelope } from '../src/studio/contracts'
import { ProductionJournal, SyntheticFixtureHost } from './production-support'

let service: SyntheticFixtureHost
let journal: ProductionJournal
test.beforeEach(async ({ page }) => {
  service = new SyntheticFixtureHost()
  await service.start('tools/studio_analysis_failure_fixture.py')
  journal = new ProductionJournal(page, service)
})
test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus) await journal?.capture(info)
  journal?.dispose()
  await service?.stop()
})
async function status(page: Page): Promise<StatusEnvelope> {
  const response = await page.request.get(`${service.origin}/api/studio/status`)
  expect(response.status()).toBe(200)
  return await response.json() as StatusEnvelope
}
async function detail(page: Page, runId: string): Promise<RunDetailEnvelope> {
  const response = await page.request.get(`${service.origin}/api/studio/runs/${runId}`)
  expect(response.status()).toBe(200)
  return await response.json() as RunDetailEnvelope
}
async function accessibility(page: Page) {
  await page.evaluate(axe.source)
  const violations = await page.evaluate(async () => (await (window as unknown as { axe: typeof axe }).axe.run()).violations
    .filter((item) => ['serious', 'critical'].includes(item.impact ?? '')).map((item) => ({ id: item.id, count: item.nodes.length })))
  expect(violations).toEqual([])
}

test('原片分析失败不是外部等待：原文、重开定位与同 Run 新 attempt 重试', async ({ page }) => {
  const mutations: Array<{ operation: string; run_id?: string; node_id?: string }> = []
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url() === `${service.origin}/api/studio/command`)
      mutations.push(request.postDataJSON() as { operation: string; run_id?: string; node_id?: string })
  })
  const source = join(dirname(service.fixture.wizard_project), 'av27-source.mkv')
  const original = await readFile(source)
  await page.goto(service.origin)
  await page.getByRole('button', { name: /新建视频工程/ }).click()
  await page.getByRole('button', { name: '选择视频素材', exact: true }).click()
  await expect(page.locator('.creator-setup-sources')).toContainText('av27-source.mkv')
  await page.getByLabel('工程名称', { exact: true }).fill('Synthetic Analysis Failure')
  await page.getByRole('button', { name: '选择工程保存位置', exact: true }).click()
  await expect(page.locator('.creator-project-path')).toContainText('wizard-output-layout.zniku')
  await page.getByRole('button', { name: '下一步：处理方案' }).click()
  await page.getByText('工作流版本与旧工程兼容', { exact: true }).click()
  await page.getByLabel('工作流方案').selectOption('source-aligned')
  await expect(page.getByLabel('工作流方案')).toHaveValue('source-aligned')
  await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
  await page.getByLabel('片名', { exact: true }).fill('Synthetic Analysis Failure')
  await page.getByLabel('年份', { exact: true }).fill('2026')
  await page.getByLabel('Program encoder').selectOption('cpu')
  await page.getByRole('button', { name: '下一步：分析' }).click()
  await page.getByRole('button', { name: /开始分析素材/ }).click()
  await expect(page.getByText('素材分析没有完成', { exact: true })).toBeVisible({ timeout: 90_000 })
  await expect(page.getByRole('region', { name: '分析失败原因', exact: true })).toContainText('原片帧率或时间轴未通过检查')
  await expect(page.getByText('需要完成一个外部处理步骤', { exact: true })).toHaveCount(0)
  await expect(page.getByText('请返回工作区完成马赛克修复；文件出现不会自动提交。', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重新分析', exact: true })).toHaveCount(0)
  await page.getByText('高级详情 · 原始分析错误', { exact: true }).click()
  await expect(page.getByRole('region', { name: '分析失败原因', exact: true })).toContainText('E_AV27_SOURCE_FPS_AMBIGUOUS')
  await accessibility(page)

  const before = await status(page)
  expect(before.run_summaries).toHaveLength(1)
  const summary = before.run_summaries[0]!
  expect(summary.state).toBe('running')
  expect(summary.requires_operator_action).toBe(true)
  expect(summary.state_counts).toMatchObject({ failed: 1, pending: 1, waiting_external: 0 })
  expect(before.snapshot!.project.graph.nodes.map((node) => node.node_id)).toEqual(['source.program', 'admission'])
  const failedRun = await detail(page, summary.run_id)
  const failedSource = failedRun.run.node_runs.find((node) => node.node_id === 'source.program')!
  expect(failedSource).toMatchObject({ attempt: 1, state: 'failed', external_handoff: null,
    error: { reason: 'validation_failed' } })
  expect(failedSource.error?.message).toContain('E_AV27_SOURCE_FPS_AMBIGUOUS')
  expect(failedSource.error?.message).toContain('Source 全片 cadence 置信度不足')
  expect(failedSource.error?.message).toContain('未修改素材，未执行时间轴校正或增删帧')
  expect(failedSource.output_artifact_ids).toEqual([])

  // 重开只读取既有未完成分析。不能把含失败节点的记录猜成 completed，也不能暗建第二个 Run。
  await page.reload()
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await page.getByRole('button', { name: '工程', exact: true }).click()
  await page.getByRole('button', { name: '继续处理向导', exact: true }).click()
  await expect(page.getByLabel('片名', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('年份', { exact: true })).toHaveValue('')
  await page.getByRole('button', { name: '查看已有分析记录', exact: true }).click()
  await page.getByRole('button', { name: /查看未完成分析/ }).click()
  await expect(page.getByText('素材分析没有完成', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '查看分析问题', exact: true }).click()
  await expect(page.getByText('原片帧率或时间轴未通过检查', { exact: true }).first()).toBeVisible()
  expect(await detail(page, summary.run_id)).toEqual(failedRun)
  expect((await status(page)).run_summaries).toHaveLength(1)
  // 原片不变时从头重试仍应失败；必须保留 attempt 1，不能提交任何 external handoff。
  await page.getByRole('button', { name: '从此步骤重新处理', exact: true }).click()
  const retry = page.getByRole('dialog', { name: '确认重新处理的影响', exact: true })
  await expect(retry).toBeVisible()
  await accessibility(page)
  await retry.getByRole('button', { name: '确认从头重新处理', exact: true }).click()
  await expect(retry).toBeHidden()
  await expect.poll(async () => {
    const current = await detail(page, summary.run_id)
    return current.run.node_runs.find((node) => node.node_id === 'source.program' && node.attempt === 2)?.state
  }, { timeout: 90_000 }).toBe('failed')
  const after = await detail(page, summary.run_id)
  expect(after.run.graph_snapshot).toEqual(failedRun.run.graph_snapshot)
  expect(after.run.node_runs.find((node) => node.node_run_id === failedSource.node_run_id)).toEqual(failedSource)
  expect(after.run.node_runs.filter((node) => node.external_handoff !== null)).toEqual([])
  expect(after.run.node_runs.flatMap((node) => node.output_artifact_ids)).toEqual([])
  expect((await status(page)).run_summaries).toHaveLength(1)
  expect(mutations.filter((item) => item.operation === 'run_all')).toHaveLength(1)
  expect(mutations.filter((item) => item.operation === 'rerun_from_here')).toEqual([
    expect.objectContaining({ operation: 'rerun_from_here', run_id: summary.run_id, node_id: 'source.program' }),
  ])
  expect(mutations.filter((item) => /submit|import/.test(item.operation))).toEqual([])
  expect(await readFile(source)).toEqual(original)
  expect(errors).toEqual([])
})
