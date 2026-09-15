/** 生产资源 + 正式服务 + 120 帧合成媒体；只测试到外部等待，不伪造 Aion 产物。 */
import { test, expect, type Page } from '@playwright/test'
import axe from 'axe-core'
import { ProductionJournal, SyntheticFixtureHost, maskedScreenshot } from './production-support'
import type { StatusEnvelope, RunDetailEnvelope } from '../src/studio/contracts'

let service: SyntheticFixtureHost
let journal: ProductionJournal
test.beforeEach(async ({ page }) => {
  service = new SyntheticFixtureHost()
  await service.start('tools/studio_overlap_fixture.py')
  journal = new ProductionJournal(page, service)
})
test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus) await journal?.capture(info)
  journal?.dispose()
  await service?.stop()
})
async function status(page: Page): Promise<StatusEnvelope> {
  return await (await page.request.get(`${service.origin}/api/studio/status`)).json() as StatusEnvelope
}
async function accessibility(page: Page) {
  await page.evaluate(axe.source)
  const violations = await page.evaluate(async () => (await (window as unknown as { axe: typeof axe }).axe.run()).violations
    .filter((item) => ['serious', 'critical'].includes(item.impact ?? '')).map((item) => ({ id: item.id, count: item.nodes.length })))
  expect(violations).toEqual([])
}

for (const mode of ['average', 'exact_times', 'exact_frames'] as const) {
  test(`新候选 ${mode}：设置/修复/分析/入图/保存重开/真实 Runtime 外部等待`, async ({ page }, info) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto(service.origin)
    await page.getByRole('button', { name: /新建视频工程/ }).click()
    await page.getByRole('button', { name: '选择视频素材', exact: true }).click()
    await expect(page.locator('.creator-setup-sources')).toContainText('av27-source.mkv')
    await page.getByLabel('工程名称', { exact: true }).fill(`Overlap ${mode}`)
    await page.getByRole('button', { name: '选择工程保存位置', exact: true }).click()
    await expect(page.locator('.creator-project-path')).toContainText('wizard-output-layout.zniku')
    await page.getByRole('button', { name: '下一步：处理方案' }).click()
    await page.getByText('工作流版本与旧工程兼容', { exact: true }).click()
    await expect(page.getByLabel('工作流方案')).toHaveValue('source-admitted')
    await page.getByLabel('工作流方案').selectOption('overlap')
    await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
    await page.getByLabel('片名', { exact: true }).fill('Synthetic Overlap')
    await page.getByLabel('年份', { exact: true }).fill('2026')
    await expect(page.getByLabel('每段最长时长（分叶）')).toHaveValue('5')
    await page.getByLabel('Program encoder').selectOption('cpu')
    await page.getByLabel('章节切分方式').selectOption(mode)
    if (mode === 'average') await page.getByLabel('平均章数').fill('3')
    else {
      const kind = mode === 'exact_times' ? '时间' : '帧'
      await page.getByLabel(`第 1 个${kind}切分点`).fill(mode === 'exact_times' ? '00:00:01' : '40')
      await page.getByRole('button', { name: '添加切分点' }).click()
      await page.getByLabel(`第 2 个${kind}切分点`).fill(mode === 'exact_times' ? '00:00:00' : '10')
      await page.getByRole('button', { name: '下一步：分析' }).click()
      await expect(page.getByLabel(`第 2 个${kind}切分点`)).toBeFocused()
      expect((await status(page)).snapshot).toBeNull()
      await page.getByLabel(`第 2 个${kind}切分点`).fill(mode === 'exact_times' ? '00:00:02' : '80')
    }
    await accessibility(page)
    await page.getByRole('button', { name: '下一步：分析' }).click()
    await page.getByRole('button', { name: /开始分析素材/ }).click()
    const confirmation = page.getByRole('region', { name: '确认重叠补帧工作流' })
    await expect(confirmation).toBeVisible({ timeout: 90_000 })
    await expect(confirmation).toContainText('3 章 · 3 个处理段')
    await expect(confirmation).toContainText('待真实验收')
    await expect(confirmation).toContainText('240 帧')
    await accessibility(page)
    await maskedScreenshot(page, info.outputPath(`overlap-${mode}-preview.png`))
    await page.getByRole('button', { name: '确认并创建工作流' }).click()
    await expect(confirmation).toBeHidden()
    const expanded = await status(page)
    const graph = expanded.snapshot!.project.graph
    const split = graph.nodes.find((node) => node.type_id === 'zniku.overlap.split.leaves.3')!
    expect(split).toBeTruthy()
    expect(graph.nodes.filter((node) => node.type_id === 'zniku.overlap.fi_context')).toHaveLength(3)
    expect(graph.nodes.filter((node) => node.type_id === 'zniku.overlap.fi_crop')).toHaveLength(3)
    expect((split.parameters.plan as { settings: { chapter_selector: { mode: string }; leaf_max_minutes: number } }).settings).toMatchObject({ chapter_selector: { mode }, leaf_max_minutes: 5 })
    // 重新载入同一个已经持久化的普通工程；不借用第二张工作流或再次 expand。
    await page.reload()
    await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
    await expect(page.locator('.react-flow__node')).toHaveCount(graph.nodes.length)
    expect((await status(page)).snapshot!.project.graph).toEqual(graph)
    // 新节点不能全部退回原始 JSON；修改/恢复仍保存同一 Graph，绑定不被 UI 隐式重建。
    await page.getByRole('textbox', { name: '查找画布节点' }).fill('外部上下文补帧')
    await page.locator('[aria-label="画布搜索结果"]').getByRole('button').first().click()
    await page.getByRole('tab', { name: '设置', exact: true }).click()
    await expect(page.getByText('部分字段使用原始参数', { exact: true })).toHaveCount(0)
    const contextFrames = page.getByLabel('left_context_frames', { exact: true })
    await expect(contextFrames).toHaveValue('32')
    await contextFrames.fill('31')
    await page.getByRole('button', { name: '应用设置', exact: true }).click()
    await expect.poll(async () => {
      const current = (await status(page)).snapshot!.project.graph.nodes.find((node) => node.node_id === 'overlap.fi.A')!
      return (current.parameters.fi_profile as { left_context_frames: number }).left_context_frames
    }).toBe(31)
    await contextFrames.fill('32')
    await page.getByRole('button', { name: '应用设置', exact: true }).click()
    await expect.poll(async () => (await status(page)).snapshot!.project.graph).toEqual(graph)
    await page.getByRole('button', { name: '开始处理', exact: true }).click()
    let running!: StatusEnvelope
    await expect.poll(async () => {
      running = await status(page)
      return running.run_summaries.some((run) => run.state_counts.waiting_external === 3)
    }, { timeout: 90_000 }).toBe(true)
    const runId = running.run_summaries.find((run) => run.requires_operator_action)!.run_id
    const detail = await (await page.request.get(`${service.origin}/api/studio/runs/${runId}`)).json() as RunDetailEnvelope
    expect(detail.run.node_runs.filter((node) => node.state === 'failed')).toEqual([])
    expect(detail.run.node_runs.filter((node) => node.state === 'waiting_external')).toHaveLength(3)
    expect(detail.run.node_runs.filter((node) => node.node_id.startsWith('overlap.context.')).every((node) => node.state === 'pending')).toBe(true)
    expect(detail.handoff_contracts.length).toBe(3)
    await expect(page.getByRole('button', { name: /处理外部文件/ }).first()).toBeVisible()
    expect(errors).toEqual([])
  })
}
