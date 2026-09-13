/** 正式生产资源与 Runtime；只读规划、MR 真实导入/Submit 和旧等待兼容均使用独立合成根。 */
import { readFile, stat } from 'node:fs/promises'
import { test, expect, type Page } from '@playwright/test'
import axe from 'axe-core'
import { ProductionJournal, SyntheticFixtureHost, maskedScreenshot } from './production-support'
import type { StatusEnvelope, RunDetailEnvelope } from '../src/studio/contracts'
import type { SourceAlignedFullRequest } from '../src/studio/source-aligned-contracts'

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
async function detail(page: Page, runId: string): Promise<RunDetailEnvelope> {
  return await (await page.request.get(`${service.origin}/api/studio/runs/${runId}`)).json() as RunDetailEnvelope
}
async function accessibility(page: Page) {
  await page.evaluate(axe.source)
  const violations = await page.evaluate(async () => (await (window as unknown as { axe: typeof axe }).axe.run()).violations
    .filter((item) => ['serious', 'critical'].includes(item.impact ?? '')).map((item) => ({ id: item.id, count: item.nodes.length })))
  expect(violations).toEqual([])
}
async function reachSettings(page: Page, profile = 'source-aligned') {
  await page.goto(service.origin)
  await page.getByRole('button', { name: /新建视频工程/ }).click()
  await page.getByRole('button', { name: '选择视频素材', exact: true }).click()
  await expect(page.locator('.creator-setup-sources')).toContainText('av27-source.mkv')
  await page.getByLabel('工程名称', { exact: true }).fill('Synthetic Source Aligned')
  await page.getByRole('button', { name: '选择工程保存位置', exact: true }).click()
  await expect(page.locator('.creator-project-path')).toContainText('wizard-output-layout.zniku')
  await page.getByRole('button', { name: '下一步：处理方案' }).click()
  await expect(page.getByLabel('工作流方案')).toHaveValue('source-aligned')
  if (profile !== 'source-aligned') await page.getByLabel('工作流方案').selectOption(profile)
  await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
  await page.getByLabel('片名', { exact: true }).fill('Synthetic Source Aligned')
  await page.getByLabel('年份', { exact: true }).fill('2026')
  await page.getByLabel('Program encoder').selectOption('cpu')
}
async function assertStoredLocationReadOnly(page: Page) {
  await page.getByText('高级选项（可选）', { exact: true }).click()
  const storage = page.getByRole('region', { name: '工作数据位置', exact: true })
  await expect(storage).toContainText('沿用已保存的工作数据位置，请在“工程 → 工程数据”查看')
  await expect(storage).not.toContainText('使用工程旁默认位置')
  await expect(storage.getByRole('button')).toHaveCount(0)
}

for (const mr of ['off', 'on'] as const) {
  test(`原片规划 MR ${mr}：返回不写入、原片分析不等待、确认与真实外部门禁`, async ({ page }, info) => {
    const errors: string[] = [], prepares: { mr: { mode: string } }[] = [], previews: SourceAlignedFullRequest[] = [], mutations: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('request', (request) => {
      if (request.method() !== 'POST') return
      if (request.url() === `${service.origin}/api/studio/templates/source-aligned-overlap/full-preview`) previews.push(request.postDataJSON() as SourceAlignedFullRequest)
      if (request.url() === `${service.origin}/api/studio/command`) {
        const body = request.postDataJSON() as { operation: string; request?: { mr: { mode: string } } }
        mutations.push(body.operation)
        if (body.operation === 'create_av_enhance_v27') prepares.push(body.request!)
      }
    })
    await reachSettings(page)
    await expect(page.getByLabel('启用外部马赛克修复')).not.toBeChecked()
    await page.getByLabel('平均章数').fill('3')
    if (mr === 'on') {
      await page.getByLabel('启用外部马赛克修复').check()
      await expect(page.getByLabel('MR output container')).toHaveValue('mp4')
      await page.getByLabel('确认外部修复保留帧顺序').check()
    }
    await accessibility(page)
    await maskedScreenshot(page, info.outputPath(`source-aligned-${mr}-settings.png`))
    expect((await status(page)).snapshot).toBeNull()
    await page.getByRole('button', { name: '下一步：分析' }).click()
    await page.getByRole('button', { name: /开始分析素材/ }).click()
    const confirmation = page.getByRole('region', { name: '确认重叠补帧工作流' })
    await expect(confirmation).toBeVisible({ timeout: 90_000 })
    expect(prepares).toHaveLength(1)
    expect(prepares[0]!.mr).toEqual({ mode: 'off' })
    expect(previews[0]!.processing.mr?.mode).toBe(mr === 'on' ? 'external' : 'off')
    const analyzed = await status(page)
    expect(analyzed.snapshot!.project.graph.nodes).toHaveLength(2)
    expect(analyzed.run_summaries).toHaveLength(1)
    expect(analyzed.run_summaries[0]!.state).toBe('completed')
    expect(analyzed.run_summaries[0]!.requires_operator_action).toBe(false)
    const analysisDetail = await detail(page, analyzed.run_summaries[0]!.run_id)
    // 返回本身不写入。身份已创建只能查看；新 MR 意图可以变更并重新预览，不能回写分析 snapshot。
    await page.getByRole('button', { name: '查看处理方案' }).click()
    await expect(page.getByLabel('工作流方案')).toBeEnabled()
    await page.getByRole('button', { name: '上一步' }).click()
    await expect(page.getByLabel('工程名称', { exact: true })).toBeDisabled()
    await expect(page.getByRole('button', { name: '选择视频素材', exact: true })).toBeDisabled()
    await assertStoredLocationReadOnly(page)
    await page.getByRole('button', { name: '下一步：处理方案' }).click()
    await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
    if (mr === 'on') await page.getByLabel('MR model name').fill('Synthetic external tool')
    else {
      await page.getByLabel('启用外部马赛克修复').check()
      await page.getByLabel('启用外部马赛克修复').uncheck()
    }
    await page.getByRole('button', { name: '下一步：分析' }).click()
    await page.getByRole('button', { name: '生成工作流预览', exact: true }).click()
    await expect(confirmation).toBeVisible()
    expect(previews).toHaveLength(2)
    expect((await status(page)).snapshot).toEqual(analyzed.snapshot)
    expect(await detail(page, analyzed.run_summaries[0]!.run_id)).toEqual(analysisDetail)
    expect(mutations.filter((operation) => operation === 'run_all')).toHaveLength(1)
    if (mr === 'on') expect(previews[1]!.processing.mr).toMatchObject({ mode: 'external', model_name: 'Synthetic external tool' })
    await accessibility(page)
    await page.getByRole('button', { name: '确认并创建工作流' }).click()
    await expect(confirmation).toBeHidden()
    const expanded = await status(page), graph = expanded.snapshot!.project.graph
    expect(graph.nodes.find((node) => node.node_id === 'overlap.split')!.definition_version).toBe('0.3.3')
    expect(graph.nodes.filter((node) => node.type_id === 'zniku.source_aligned.external.mp4')).toHaveLength(mr === 'on' ? 1 : 0)
    await page.getByRole('button', { name: '适应画布', exact: true }).click()
    await expect.poll(async () => page.locator('.react-flow__node:visible').count()).toBeGreaterThan(1)
    const layout = await page.locator('.react-flow__node').evaluateAll((elements) => {
      const visible = elements.flatMap((element) => {
        const bounds = element.getBoundingClientRect(), css = getComputedStyle(element)
        return css.visibility !== 'hidden' && css.display !== 'none' && bounds.width > 0 && bounds.height > 0
          ? [{ id: element.getAttribute('data-id'), left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom }] : []
      })
      const overlaps: string[] = []
      for (const [index, left] of visible.entries()) for (const right of visible.slice(index + 1)) {
        if (Math.min(left.right, right.right) - Math.max(left.left, right.left) > 1 && Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top) > 1) overlaps.push(`${left.id}/${right.id}`)
      }
      return { visibleCount: visible.length, overlaps }
    })
    expect(layout.visibleCount).toBeGreaterThan(1)
    expect(layout.overlaps).toEqual([])
    await maskedScreenshot(page, info.outputPath(`source-aligned-${mr}-graph.png`))
    expect(await detail(page, analyzed.run_summaries[0]!.run_id)).toEqual(analysisDetail)
    await page.getByRole('button', { name: '开始处理', exact: true }).click()
    let runId = ''
    await expect.poll(async () => {
      const current = await status(page)
      const waiting = current.run_summaries.find((summary) => summary.requires_operator_action)
      runId = waiting?.run_id ?? ''
      return waiting?.state_counts.waiting_external ?? 0
    }, { timeout: 90_000 }).toBe(mr === 'on' ? 1 : 3)
    const first = await detail(page, runId)
    expect(first.run.node_runs.filter((node) => node.state === 'failed')).toEqual([])
    if (mr === 'on') {
      const waitingMR = first.run.node_runs.find((node) => node.node_id === 'source-aligned.mr')!
      expect(waitingMR.state).toBe('waiting_external')
      expect(first.run.node_runs.find((node) => node.node_id === 'overlap.split')?.state).toBe('pending')
      const target = waitingMR.external_handoff!.output_targets[0]!.path
      expect(target).toMatch(/\.mp4$/)
      const candidateBytes = await readFile(service.fixture.source_aligned_mr!)
      await page.getByRole('button', { name: /处理外部文件/ }).first().click()
      const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
      await expect(helper).toBeVisible()
      await expect(helper).toContainText('120')
      await helper.getByRole('button', { name: '选择处理好的文件', exact: true }).click()
      await expect(page.getByRole('dialog', { name: '确认导入外部处理文件', exact: true })).toBeHidden()
      expect(await stat(target).then(() => true, () => false)).toBe(false)
      await helper.getByRole('button', { name: '选择处理好的文件', exact: true }).click()
      const importDialog = page.getByRole('dialog', { name: '确认导入外部处理文件', exact: true })
      await expect(importDialog).toBeVisible()
      await expect(importDialog).toContainText('source-aligned-mr.mp4')
      await importDialog.getByRole('button', { name: '确认复制到此任务', exact: true }).click()
      await expect(importDialog).toBeHidden({ timeout: 45_000 })
      expect(await readFile(target)).toEqual(candidateBytes)
      expect(await readFile(service.fixture.source_aligned_mr!)).toEqual(candidateBytes)
      expect((await detail(page, runId)).run.node_runs.find((node) => node.node_id === 'source-aligned.mr')!.state).toBe('waiting_external')
      await helper.getByRole('button', { name: '检查输出', exact: true }).click()
      await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled({ timeout: 45_000 })
      expect((await detail(page, runId)).run.node_runs.find((node) => node.node_id === 'overlap.split')?.state).toBe('pending')
      await accessibility(page)
      await maskedScreenshot(page, info.outputPath('source-aligned-mr-submit.png'))
      await helper.getByRole('button', { name: '提交并继续', exact: true }).click()
      await expect.poll(async () => (await detail(page, runId)).run.node_runs.filter((node) => node.state === 'waiting_external').length, { timeout: 90_000 }).toBe(3)
      const after = await detail(page, runId)
      expect(after.run.node_runs.find((node) => node.node_id === 'source-aligned.mr')!.state).toBe('completed')
      expect(after.run.node_runs.find((node) => node.node_id === 'overlap.split')!.state).toBe('completed')
      expect(after.run.node_runs.filter((node) => node.state === 'failed')).toEqual([])
      expect(after.run.graph_snapshot).toEqual(graph)
    }
    expect((await status(page)).snapshot!.project.graph).toEqual(graph)
    expect(errors).toEqual([])
  })
}

test('旧 MR-on 等待工程重开：真实配置回填，直接进入同一外部助手，不改旧 Graph', async ({ page }) => {
  await reachSettings(page, 'av27')
  await page.getByLabel('启用外部马赛克修复').check()
  await page.getByLabel('MR model version').fill('legacy-synthetic')
  await page.getByRole('button', { name: '下一步：分析' }).click()
  await page.getByRole('button', { name: /开始分析素材/ }).click()
  await expect.poll(async () => (await status(page)).run_summaries.some((summary) => summary.requires_operator_action), { timeout: 90_000 }).toBe(true)
  const before = await status(page)
  await page.reload()
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await page.getByRole('button', { name: '工程', exact: true }).click()
  await page.getByRole('button', { name: '继续处理向导', exact: true }).click()
  await expect(page.getByLabel('启用外部马赛克修复')).toBeChecked()
  await expect(page.getByLabel('启用外部马赛克修复')).toBeDisabled()
  await expect(page.getByLabel('MR model version')).toHaveValue('legacy-synthetic')
  await expect(page.getByLabel('工作流方案')).toHaveValue('av27')
  await page.getByRole('button', { name: '查看选择素材' }).click()
  await assertStoredLocationReadOnly(page)
  await page.getByRole('button', { name: '查看处理与成片设置' }).click()
  await page.getByRole('button', { name: /打开外部任务/ }).click()
  const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
  await expect(helper).toBeVisible()
  await expect(helper.getByRole('button', { name: '选择处理好的文件', exact: true })).toBeEnabled()
  expect((await status(page)).snapshot).toEqual(before.snapshot)
  expect((await status(page)).run_summaries.map((summary) => summary.run_id)).toEqual(before.run_summaries.map((summary) => summary.run_id))
  await accessibility(page)
})
