/** 0.3.6 显式融合候选使用生产资源与真实分析/SQLite；只替代原生选择器，不运行外部 AI。 */
import { readFile, stat } from 'node:fs/promises'
import { test, expect, type Page } from '@playwright/test'
import axe from 'axe-core'
import type { RunDetailEnvelope, StatusEnvelope } from '../src/studio/contracts'
import type { FusedFullEnvelope, FusedFullRequest } from '../src/studio/chapter-batch-fused-contracts'
import type { SourceAdmittedCreateRequest } from '../src/studio/source-admitted-contracts'
import { ProductionJournal, SyntheticFixtureHost, maskedScreenshot } from './production-support'

const admittedPrefix = '/api/studio/templates/source-admitted-overlap'
const fusedPrefix = '/api/studio/templates/chapter-batch-fused'
let service: SyntheticFixtureHost, journal: ProductionJournal

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
  const response = await page.request.get(`${service.origin}/api/studio/status`)
  expect(response.status()).toBe(200)
  return await response.json() as StatusEnvelope
}
async function detail(page: Page, runId: string): Promise<RunDetailEnvelope> {
  const response = await page.request.get(`${service.origin}/api/studio/runs/${runId}`)
  expect(response.status()).toBe(200)
  return await response.json() as RunDetailEnvelope
}

for (const exportCropped of [false, true]) {
  test(`显式融合候选：${exportCropped ? '额外导出真实裁后章' : '默认无裁后章副本'}、真实分析与工程重开`, async ({ page }, info) => {
    const errors: string[] = [], creates: SourceAdmittedCreateRequest[] = [], expands: FusedFullRequest[] = []
    let oldExpands = 0
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('request', (request) => {
      if (request.method() !== 'POST') return
      if (request.url() === `${service.origin}${admittedPrefix}/create`) creates.push(request.postDataJSON() as SourceAdmittedCreateRequest)
      if (request.url() === `${service.origin}${admittedPrefix}/expand`) oldExpands++
      if (request.url() === `${service.origin}${fusedPrefix}/expand`) expands.push(request.postDataJSON() as FusedFullRequest)
    })
    const original = await readFile(service.fixture.original_source!)
    const projectName = `Synthetic Fused ${exportCropped ? 'Export' : 'Default'}`
    await page.goto(service.origin)
    await page.getByRole('button', { name: /新建视频工程/ }).click()
    await page.getByRole('button', { name: '选择视频素材', exact: true }).click()
    await expect(page.locator('.creator-setup-sources')).toContainText('av27-source.mkv')
    await page.getByLabel('工程名称', { exact: true }).fill(projectName)
    await page.getByRole('button', { name: '选择工程保存位置', exact: true }).click()
    await expect(page.locator('.creator-project-path')).toContainText('wizard-output-layout.zniku')
    await page.getByRole('button', { name: '下一步：处理方案' }).click()
    // 候选必须显式选择；新默认仍保持已验收的 0.3.5，不由测试绕过折叠区。
    await expect(page.getByLabel('工作流方案')).toHaveValue('source-admitted')
    await expect(page.getByLabel('工作流方案')).not.toBeVisible()
    await page.getByText('工作流版本与旧工程兼容', { exact: true }).click()
    await page.getByLabel('工作流方案').selectOption('fused')
    await expect(page.getByText('0.3.6 融合编码候选（待验收）', { exact: true }).first()).toBeVisible()
    const exportBox = page.getByRole('checkbox', { name: /同时导出裁后章节/ })
    await expect(exportBox).not.toBeChecked()
    if (exportCropped) await exportBox.check()
    await page.getByRole('button', { name: '下一步：处理与成片设置' }).click()
    await page.getByLabel('片名', { exact: true }).fill(projectName)
    await page.getByLabel('年份', { exact: true }).fill('2026')
    await page.getByLabel('平均章数').fill('3')
    await expect(page.getByLabel('每段最长时长（分叶）')).toHaveValue('5')
    await expect(page.getByLabel('启用外部马赛克修复')).not.toBeChecked()
    await page.getByLabel('Program encoder').selectOption('cpu')
    await page.getByRole('button', { name: '下一步：分析' }).click()
    const previewResponse = page.waitForResponse((response) => response.url() === `${service.origin}${fusedPrefix}/full-preview` && response.request().method() === 'POST')
    await page.getByRole('button', { name: /开始分析素材/ }).click()
    const confirmation = page.getByRole('region', { name: '确认重叠补帧工作流' })
    await expect(confirmation).toBeVisible({ timeout: 90_000 })
    const preview = await (await previewResponse).json() as FusedFullEnvelope
    expect(preview).toMatchObject({
      contract_version: '0.3.6', profile_id: 'zniku.source-admitted.chapter-batch-fused', profile_version: '0.3.6',
      export_cropped_chapters: exportCropped,
      plan: { source: { frame_count: 120, frame_rate: '30000/1001' }, chapter_count: 3, leaf_count: 3 },
    })
    await expect(confirmation).toContainText(exportCropped ? '已选择额外导出裁后章节，会增加读写与存储。' : '不导出整章裁边副本。')
    expect(creates).toHaveLength(1)
    expect(creates[0]).toMatchObject({ contract_version: '0.3.5', request: { mr: { mode: 'off' } } })
    const analyzed = await status(page)
    expect(analyzed.snapshot!.project.graph.nodes).toHaveLength(2)
    expect(analyzed.snapshot!.project.graph.nodes.every((node) => node.definition_version === '0.3.5')).toBe(true)
    expect(analyzed.run_summaries).toHaveLength(1)
    expect(analyzed.run_summaries[0]).toMatchObject({ state: 'completed', requires_operator_action: false })
    const analysis = await detail(page, analyzed.run_summaries[0]!.run_id)
    expect(analysis.run.node_runs.every((node) => node.state === 'completed' && node.external_handoff === null)).toBe(true)
    await page.evaluate(axe.source)
    const violations = await page.evaluate(async () => (await (window as unknown as { axe: typeof axe }).axe.run()).violations
      .filter((item) => ['critical', 'serious'].includes(item.impact ?? '')).map((item) => item.id))
    expect(violations).toEqual([])
    await maskedScreenshot(page, info.outputPath(`fused-${exportCropped ? 'export' : 'default'}-preview.png`))
    await page.getByRole('button', { name: '确认并创建工作流' }).click()
    await expect(confirmation).toBeHidden()
    expect(expands).toHaveLength(1)
    expect(expands[0]).toMatchObject({ contract_version: '0.3.6', export_cropped_chapters: exportCropped })
    expect(oldExpands).toBe(0)
    const expanded = await status(page), graph = expanded.snapshot!.project.graph
    const byId = new Map(graph.nodes.map((node) => [node.node_id, node]))
    expect(byId.get('overlap.program')).toMatchObject({ type_id: 'zniku.source-admitted.chapter-batch.program-fused', definition_version: '0.3.6' })
    expect(byId.get('overlap.final')).toMatchObject({ type_id: 'zniku.source-admitted.chapter-batch.final-publish-fused', definition_version: '0.3.6', parameters: { overwrite: false, create_parent: true } })
    expect(byId.get('output')!.parameters).toEqual({ mode: 'reference', overwrite: false })
    const rawEdges = graph.edges.filter((edge) => edge.target_node_id === 'overlap.program')
    expect(rawEdges.map((edge) => edge.ordinal)).toEqual([0, 1, 2])
    expect(rawEdges.every((edge) => edge.target_port_id === 'videos')).toBe(true)
    expect(rawEdges.map((edge) => byId.get(edge.source_node_id))).toEqual([
      expect.objectContaining({ type_id: 'zniku.source-admitted.chapter-batch.fi', definition_version: '0.3.5' }),
      expect.objectContaining({ type_id: 'zniku.source-admitted.chapter-batch.fi', definition_version: '0.3.5' }),
      expect.objectContaining({ type_id: 'zniku.source-admitted.chapter-batch.fi', definition_version: '0.3.5' }),
    ])
    const crops = graph.nodes.filter((node) => node.type_id === 'zniku.source-admitted.chapter-batch.crop')
    expect(crops).toHaveLength(exportCropped ? 3 : 0)
    for (const crop of crops) {
      expect(crop.definition_version).toBe('0.3.5')
      expect(graph.edges.filter((edge) => edge.source_node_id === crop.node_id)).toEqual([])
      const inputs = graph.edges.filter((edge) => edge.target_node_id === crop.node_id)
      expect(inputs).toHaveLength(1)
      expect(byId.get(inputs[0]!.source_node_id)!.type_id).toBe('zniku.source-admitted.chapter-batch.fi')
    }
    expect(graph.nodes).toHaveLength(preview.node_count)
    expect(graph.edges).toHaveLength(preview.edge_count)
    expect((await stat(service.fixture.wizard_project)).size).toBeGreaterThan(0)
    // 首页最近项目发起正式 open_project：重读 SQLite，不能只刷新页面后比较内存图。
    await page.reload()
    const reopened = page.waitForResponse((response) => response.url() === `${service.origin}/api/studio/command` &&
      response.request().method() === 'POST' && response.request().postDataJSON()?.operation === 'open_project')
    await page.getByRole('region', { name: '最近工程' }).getByRole('button', { name: new RegExp(projectName) }).click()
    expect((await reopened).status()).toBe(200)
    await expect(page.locator('.project-shell-identity')).toContainText(projectName)
    const restored = await status(page)
    expect(restored.project_session_id).not.toBe(expanded.project_session_id)
    expect(restored.snapshot).toEqual(expanded.snapshot)
    expect(restored.run_summaries).toHaveLength(1)
    expect((await detail(page, analysis.run.run_id)).run).toEqual(analysis.run)
    expect(await readFile(service.fixture.original_source!)).toEqual(original)
    expect(errors).toEqual([])
  })
}
