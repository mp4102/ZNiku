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
  await dialog.getByRole('button', { name: '取消，不复制' }).click()
  await expect(panel.getByText('已收 0/2', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '选择本章文件目录' }).click()
  dialog = page.getByRole('dialog', { name: '确认本章收件匹配' })
  await dialog.getByRole('button', { name: '确认收件', exact: true }).click()
  await expect(panel.getByText('已收 2/2', { exact: true })).toBeVisible()
  expect((await detail(page)).run.node_runs.find((node) => node.node_id === 'chapter-A')?.state).toBe('waiting_external')
  await expect(panel.getByRole('button', { name: '提交本章并继续' })).toBeDisabled()
})
