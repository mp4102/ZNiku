/** 正式0.3.5资源、服务、SQLite与Runtime；仅原生选择器使用纯合成fixture。 */
import { readFile, stat } from 'node:fs/promises'
import { test, expect, type Page } from '@playwright/test'
import axe from 'axe-core'
import { ProductionJournal, SyntheticFixtureHost, maskedScreenshot } from './production-support'
import type { StatusEnvelope, RunDetailEnvelope } from '../src/studio/contracts'
import type { SourceAdmittedCreateRequest, SourceAdmittedFullEnvelope, SourceAdmittedReplaceRequest } from '../src/studio/source-admitted-contracts'

const prefix = '/api/studio/templates/source-admitted-overlap'
let service: SyntheticFixtureHost
let journal: ProductionJournal

test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus) await journal?.capture(info)
  journal?.dispose()
  await service?.stop()
})

async function start(page: Page, repairScenario = false) {
  service = new SyntheticFixtureHost()
  await service.start('tools/studio_overlap_fixture.py', repairScenario ? ['--repair-scenario'] : [])
  journal = new ProductionJournal(page, service)
}

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

async function settings(page: Page) {
  await page.goto(service.origin)
  await page.getByRole('button', { name: /新建视频工程/ }).click()
  await page.getByRole('button', { name: '选择视频素材', exact: true }).click()
  await expect(page.locator('.creator-setup-sources')).toContainText('av27-source.mkv')
  await page.getByLabel('工程名称', { exact: true }).fill('Synthetic Source Admitted')
  await page.getByRole('button', { name: '选择工程保存位置', exact: true }).click()
  await expect(page.locator('.creator-project-path')).toContainText('wizard-output-layout.zniku')
  await page.getByRole('button', { name: '下一步：处理方案' }).click()
  // 新默认不要求操作者理解三个历史版本，也不经隐藏选择动作得到新合同。
  await expect(page.getByText('ZNIKU 标准视频流程 · 0.3.5', { exact: true })).toBeVisible()
  await expect(page.getByLabel('工作流方案')).toHaveValue('source-admitted')
  await expect(page.getByLabel('工作流方案')).not.toBeVisible()
  await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
  await page.getByLabel('片名', { exact: true }).fill('Synthetic Source Admitted')
  await page.getByLabel('年份', { exact: true }).fill('2026')
  await page.getByLabel('平均章数').fill('3')
  await expect(page.getByLabel('每段最长时长（分叶）')).toHaveValue('5')
  await page.getByLabel('Program encoder').selectOption('cpu')
}

for (const mr of ['off', 'on'] as const) {
  test(`默认单一源准入 MR ${mr}：真实分析、新exact图、保存重开及分叶到外部增强`, async ({ page }, info) => {
    await start(page)
    const errors: string[] = [], creates: SourceAdmittedCreateRequest[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url() === `${service.origin}${prefix}/create`) creates.push(request.postDataJSON() as SourceAdmittedCreateRequest)
    })
    const original = await readFile(service.fixture.original_source!)
    await settings(page)
    await expect(page.getByLabel('启用外部马赛克修复')).not.toBeChecked()
    if (mr === 'on') {
      await page.getByLabel('启用外部马赛克修复').check()
      await page.getByLabel('确认外部修复保留帧顺序').check()
    }
    await accessibility(page)
    await page.getByRole('button', { name: '下一步：分析' }).click()
    const previewResponse = page.waitForResponse((response) => response.url() === `${service.origin}${prefix}/full-preview` && response.request().method() === 'POST')
    await page.getByRole('button', { name: /开始分析素材/ }).click()
    const confirmation = page.getByRole('region', { name: '确认重叠补帧工作流' })
    await expect(confirmation).toBeVisible({ timeout: 90_000 })
    const preview = await (await previewResponse).json() as SourceAdmittedFullEnvelope
    expect(preview).toMatchObject({ contract_version: '0.3.5', profile_id: 'zniku.source-admitted-overlap', plan: { source: { frame_count: 120, frame_rate: '30000/1001' }, chapter_count: 3, leaf_count: 3 } })
    expect(preview.warnings.length).toBeGreaterThan(0)
    await expect(page.getByRole('region', { name: '素材检查提示', exact: true }).getByRole('listitem')).toHaveText([...preview.warnings])
    await expect(page.getByRole('button', { name: '确认并创建工作流' })).toBeEnabled()
    expect(preview.processing.mr?.mode).toBe(mr === 'on' ? 'external' : 'off')
    expect(creates).toHaveLength(1)
    expect(creates[0]!.request.mr).toEqual({ mode: 'off' })
    const analyzed = await status(page)
    expect(analyzed.snapshot!.project.graph.nodes).toHaveLength(2)
    expect(analyzed.snapshot!.project.graph.nodes.every((node) => node.definition_version === '0.3.5')).toBe(true)
    expect(analyzed.run_summaries).toHaveLength(1)
    expect(analyzed.run_summaries[0]).toMatchObject({ state: 'completed', requires_operator_action: false })
    const analysis = await detail(page, analyzed.run_summaries[0]!.run_id)
    expect(analysis.run.node_runs.every((node) => node.state === 'completed')).toBe(true)
    expect(analysis.run.node_runs.some((node) => node.external_handoff !== null)).toBe(false)
    await maskedScreenshot(page, info.outputPath(`source-admitted-${mr}-preview.png`))
    await page.getByRole('button', { name: '确认并创建工作流' }).click()
    await expect(confirmation).toBeHidden()
    const graph = (await status(page)).snapshot!.project.graph
    expect(graph.nodes.find((node) => node.node_id === 'overlap.split')!.definition_version).toBe('0.3.5')
    expect(graph.nodes.filter((node) => node.type_id === 'zniku.source_aligned.external.mp4')).toHaveLength(mr === 'on' ? 1 : 0)
    await page.reload()
    await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
    expect((await status(page)).snapshot!.project.graph).toEqual(graph)
    expect(await detail(page, analysis.run.run_id)).toEqual(analysis)
    await page.getByRole('button', { name: '开始处理', exact: true }).click()
    let runId = ''
    await expect.poll(async () => {
      const waiting = (await status(page)).run_summaries.find((summary) => summary.requires_operator_action)
      runId = waiting?.run_id ?? ''
      return waiting?.state_counts.waiting_external ?? 0
    }, { timeout: 90_000 }).toBe(mr === 'on' ? 1 : 3)
    if (mr === 'on') {
      const waiting = await detail(page, runId)
      const manual = waiting.run.node_runs.find((node) => node.node_id === 'source-aligned.mr')!
      expect(manual.state).toBe('waiting_external')
      expect(waiting.run.node_runs.find((node) => node.node_id === 'overlap.split')!.state).toBe('pending')
      const target = manual.external_handoff!.output_targets[0]!.path
      const candidate = await readFile(service.fixture.source_aligned_mr!)
      await page.getByRole('button', { name: /处理外部文件/ }).first().click()
      const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
      await helper.getByRole('button', { name: '选择处理好的文件', exact: true }).click()
      await expect(page.getByRole('dialog', { name: '确认导入外部处理文件', exact: true })).toBeHidden()
      expect(await stat(target).then(() => true, () => false)).toBe(false)
      await helper.getByRole('button', { name: '选择处理好的文件', exact: true }).click()
      const importDialog = page.getByRole('dialog', { name: '确认导入外部处理文件', exact: true })
      await expect(importDialog).toBeVisible()
      await importDialog.getByRole('button', { name: '确认复制到此任务', exact: true }).click()
      await expect(importDialog).toBeHidden({ timeout: 45_000 })
      expect(await readFile(target)).toEqual(candidate)
      expect(await readFile(service.fixture.source_aligned_mr!)).toEqual(candidate)
      expect((await detail(page, runId)).run.node_runs.find((node) => node.node_id === 'source-aligned.mr')!.state).toBe('waiting_external')
      await helper.getByRole('button', { name: '检查输出', exact: true }).click()
      await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled({ timeout: 45_000 })
      await helper.getByRole('button', { name: '提交并继续', exact: true }).click()
      await expect.poll(async () => (await detail(page, runId)).run.node_runs.filter((node) => node.state === 'waiting_external').length, { timeout: 90_000 }).toBe(3)
    }
    const processed = await detail(page, runId)
    expect(processed.run.node_runs.filter((node) => node.state === 'failed')).toEqual([])
    expect(processed.run.node_runs.find((node) => node.node_id === 'overlap.split')!.state).toBe('completed')
    expect(processed.run.node_runs.filter((node) => node.state === 'waiting_external').every((node) => node.node_id.startsWith('overlap.enhance.'))).toBe(true)
    const waitingEnhancements = processed.run.node_runs.filter((node) => node.state === 'waiting_external')
    expect(waitingEnhancements).toHaveLength(3)
    expect(waitingEnhancements.every((node) => node.external_handoff?.output_targets.length === 1)).toBe(true)
    expect(processed.run.graph_snapshot.nodes.filter((node) => node.type_id.startsWith('zniku.source-admitted.enhancement-batch.'))).toHaveLength(3)
    await page.getByRole('button', { name: /处理外部文件/ }).first().click()
    await page.getByRole('button', { name: 'A 章 · 批量增强 (1 份) 等待外部处理', exact: true }).click()
    const chapterHelper = page.getByRole('region', { name: '外部处理助手', exact: true })
    await expect(chapterHelper.getByRole('button', { name: '选择本章多个文件' })).toBeVisible()
    await expect(chapterHelper.getByRole('button', { name: '提交本章并继续' })).toBeDisabled()
    await expect(chapterHelper.getByRole('button', { name: '选择处理好的文件', exact: true })).toHaveCount(0)
    const chapterBoxes = await Promise.all(waitingEnhancements.map((node) => page.locator(`.react-flow__node[data-id="${node.node_id}"]`).boundingBox()))
    expect(chapterBoxes.every((box) => box !== null)).toBe(true)
    for (let left = 0; left < chapterBoxes.length; left++) for (let right = left + 1; right < chapterBoxes.length; right++) {
      const a = chapterBoxes[left]!, b = chapterBoxes[right]!
      expect(a!.x + a!.width <= b!.x || b!.x + b!.width <= a!.x || a!.y + a!.height <= b!.y || b!.y + b!.height <= a!.y).toBe(true)
    }
    expect(processed.run.graph_snapshot).toEqual(graph)
    expect((await status(page)).snapshot!.project.graph).toEqual(graph)
    expect(await readFile(service.fixture.original_source!)).toEqual(original)
    expect(errors).toEqual([])
  })
}

test('真实坏源拒绝后：选择及取消不改工程，显式确认候选后按新N/FPS/音轨重新分析', async ({ page }, info) => {
  await start(page, true)
  const errors: string[] = [], replacements: SourceAdmittedReplaceRequest[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url() === `${service.origin}${prefix}/replace-source`) replacements.push(request.postDataJSON() as SourceAdmittedReplaceRequest)
  })
  const original = await readFile(service.fixture.original_source!), candidate = await readFile(service.fixture.repair_candidate!)
  await settings(page)
  await page.getByRole('button', { name: '下一步：分析' }).click()
  await page.getByRole('button', { name: /开始分析素材/ }).click()
  await expect(page.getByText('素材分析没有完成', { exact: true })).toBeVisible({ timeout: 90_000 })
  const repair = page.getByRole('region', { name: '外部修复候选', exact: true })
  await expect(repair).toBeVisible()
  await expect(page.getByText('需要完成一个外部处理步骤', { exact: true })).toHaveCount(0)
  const before = await status(page), oldRunId = before.run_summaries[0]!.run_id
  const failed = await detail(page, oldRunId)
  expect(failed.run.node_runs.filter((node) => node.state === 'failed')).toHaveLength(1)
  expect(failed.run.node_runs.find((node) => node.state === 'failed')!.error!.message).toContain('E_AV27_SOURCE_GEOMETRY')
  expect(failed.artifacts).toEqual([])
  const choose = repair.getByRole('button', { name: '选择修复后的视频', exact: true })
  await choose.click() // 原生取消不创建候选或修改 Source。
  await expect(repair.getByRole('group', { name: '确认新的处理参考' })).toBeHidden()
  await choose.click()
  const confirm = repair.getByRole('group', { name: '确认新的处理参考' })
  await expect(confirm).toContainText('source-repaired-60-frames.mkv')
  await expect(confirm).toContainText('不会复制、覆盖或删除原片')
  expect((await status(page)).snapshot).toEqual(before.snapshot)
  expect(replacements).toEqual([])
  await confirm.getByRole('button', { name: '取消选择', exact: true }).click()
  await expect(confirm).toBeHidden()
  expect(await detail(page, oldRunId)).toEqual(failed)
  await choose.click()
  await expect(confirm).toBeVisible()
  await accessibility(page)
  await maskedScreenshot(page, info.outputPath('source-admitted-repair-confirm.png'))
  const previewResponse = page.waitForResponse((response) => response.url() === `${service.origin}${prefix}/full-preview` && response.request().method() === 'POST')
  await confirm.getByRole('button', { name: '确认选用并重新分析', exact: true }).click()
  const previewRegion = page.getByRole('region', { name: '确认重叠补帧工作流' })
  await expect(previewRegion).toBeVisible({ timeout: 90_000 })
  const preview = await (await previewResponse).json() as SourceAdmittedFullEnvelope
  expect(preview.plan.source).toMatchObject({ frame_count: 60, frame_rate: '30/1' })
  expect(preview.plan.chapters.map((chapter) => chapter.frame_count)).toEqual([20, 20, 20])
  expect(replacements).toEqual([expect.objectContaining({ contract_version: '0.3.5', reference_change_confirmed: true, source_path: service.fixture.repair_candidate })])
  const after = await status(page)
  expect(after.run_summaries).toHaveLength(2)
  const newRun = after.run_summaries.find((summary) => summary.run_id !== oldRunId)!
  expect(newRun.state).toBe('completed')
  expect(after.snapshot!.project.graph.nodes.find((node) => node.node_id === 'source.program')!.parameters.source_path).toBe(service.fixture.repair_candidate)
  const accepted = await detail(page, newRun.run_id)
  const video = accepted.artifacts.find((artifact) => artifact.producer_port_id === 'video')!
  expect(video.artifact_id).toBe(preview.plan.source.artifact_id)
  expect(video.path).toBe(service.fixture.repair_candidate)
  expect(video.media_info['zniku.avenhance.v27']).toMatchObject({ frame_count: 60, frame_rate: '30/1', audio_tracks: [expect.objectContaining({ codec: 'flac', sample_rate: 44100 })] })
  expect((await detail(page, oldRunId)).run.graph_snapshot).toEqual(failed.run.graph_snapshot)
  expect((await detail(page, oldRunId)).run.node_runs.find((node) => node.state === 'failed')).toEqual(failed.run.node_runs.find((node) => node.state === 'failed'))
  expect(await readFile(service.fixture.original_source!)).toEqual(original)
  expect(await readFile(service.fixture.repair_candidate!)).toEqual(candidate)
  expect(errors).toEqual([])
})
