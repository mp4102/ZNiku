/** 批次 C：正式生产页面、真实 SQLite/Runtime 和隔离合成文件的行为回归。 */
import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { copyFile, readFile, rename, stat, writeFile } from 'node:fs/promises'
import { basename, dirname, join, sep } from 'node:path'
import type { RunDetailEnvelope, StatusEnvelope } from '../src/studio/contracts'
import type { StorageInspection, StorageMigrationPreview } from '../src/studio/host-bridge'
import { diagnosticValue, maskedScreenshot, ProductionJournal, SyntheticFixtureHost } from './production-support'

const host = new SyntheticFixtureHost()
let journal: ProductionJournal
test.beforeAll(async () => { await host.start('tools/studio_batch_c_fixture.py') })
test.afterAll(async () => { await host.stop() })
test.beforeEach(async ({ page }) => { journal = new ProductionJournal(page, host) })
test.afterEach(async ({ page }, info) => {
  if (info.status !== info.expectedStatus && page.url().startsWith(host.origin)) await journal.capture(info)
  journal.dispose()
})

const exists = async (path: string) => stat(path).then(() => true, (error: NodeJS.ErrnoException) => {
  if (error.code === 'ENOENT') return false
  throw error
})
const status = async (page: Page): Promise<StatusEnvelope> => (await page.request.get(`${host.origin}/api/studio/status`)).json() as Promise<StatusEnvelope>
const detail = async (page: Page, id: string): Promise<RunDetailEnvelope> => (await page.request.get(`${host.origin}/api/studio/runs/${id}`)).json() as Promise<RunDetailEnvelope>
async function open(page: Page, path: string) {
  await page.goto('about:blank')
  await expect.poll(async () => (await status(page)).active_operation).toBeNull()
  const response = await page.request.post(`${host.origin}/api/studio/command`, { headers: { Origin: host.origin }, data: { operation: 'open_project', path } })
  expect(response.status()).toBe(200)
  await page.goto(host.origin)
  await page.getByRole('button', { name: '关闭工程首页', exact: true }).click()
  await expect(page.locator('.canvas-context')).toContainText('当前编辑')
}
async function run(page: Page) {
  await page.getByRole('button', { name: '开始处理', exact: true }).click()
  await expect.poll(async () => (await status(page)).run_summaries.length).toBe(1)
  return (await status(page)).run_summaries[0]!.run_id
}
async function report(page: Page, info: TestInfo, name: string, value: unknown) {
  await maskedScreenshot(page, info.outputPath(`${name}.png`))
  const productionEntry = await page.locator('script[type="module"][src]').getAttribute('src')
  await writeFile(info.outputPath(`${name}.json`), JSON.stringify(diagnosticValue({ production_entry: productionEntry?.split('/').at(-1), evidence: value }, host.roots()), null, 2), 'utf8')
}
/** 预期负向 HTTP 必须精确匹配端点和状态；所有其他 console/page error 均保留并失败。 */
function evidence(page: Page, expected: ReadonlyArray<{ path: string; status: number }> = []) {
  const errors: string[] = [], accepted: string[] = [], commands: Record<string, unknown>[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const match = expected.find((item) => message.location().url === `${host.origin}${item.path}` &&
      new RegExp(`^Failed to load resource: the server responded with a status of ${item.status}(?: |$)`).test(message.text()))
    if (match) accepted.push(`${match.path}:${match.status}`)
    else errors.push(message.text())
  })
  page.on('request', (request) => {
    if (request.url() === `${host.origin}/api/studio/command` && request.method() === 'POST') commands.push(request.postDataJSON() as Record<string, unknown>)
  })
  return { errors, accepted, commands }
}

test('C1 收件确认换代拒绝、旧产物保留、重复票据失效和精确提交', async ({ page }, info) => {
  const confirmPath = '/api/studio/handoff-inbox/confirm'
  const proof = evidence(page, [{ path: confirmPath, status: 409 }])
  await open(page, host.fixture.external_project)
  const runId = await run(page)
  await expect.poll(async () => (await detail(page, runId)).run.node_runs.filter((node) => node.state === 'waiting_external').length, { timeout: 45_000 }).toBe(2)
  const before = await detail(page, runId)
  const upstreamBytes = new Map(await Promise.all(before.artifacts.map(async (artifact) => [artifact.path, await readFile(artifact.path)] as const)))
  const node = before.run.node_runs.find((item) => item.node_id === 'enhance-B')!
  const other = before.run.node_runs.find((item) => item.node_id === 'enhance-A')!
  const target = node.external_handoff!.output_targets[0]!.path
  const otherTarget = other.external_handoff!.output_targets[0]!.path
  const assertUpstreamRetained = async () => {
    for (const [path, bytes] of upstreamBytes) expect(await readFile(path)).toEqual(bytes)
    expect(await exists(otherTarget)).toBe(false)
  }
  const original = join(dirname(host.fixture.external_project), 'external-B-15.mkv')
  const replacement = join(dirname(host.fixture.external_project), 'external-B-replacement-15.mkv')
  const sourceBytes = await readFile(original), replacementBytes = await readFile(replacement)
  await page.locator('.react-flow__node[data-id="enhance-B"]').click()
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  const inbox = page.getByRole('region', { name: '当前任务收件箱', exact: true })
  const helper = page.getByRole('region', { name: '外部处理助手', exact: true })
  await expect(inbox.locator('code.handoff-target-path')).toBeVisible()
  const inboxPath = await inbox.locator('code.handoff-target-path').innerText()
  expect(inboxPath.startsWith(node.work_dir + sep)).toBe(true)
  const candidate = join(inboxPath, 'changed-by-external-tool.mkv')
  const rejected = join(inboxPath, 'unfinished.tmp')
  await copyFile(original, target)
  await copyFile(original, candidate)
  await writeFile(rejected, 'synthetic incomplete candidate', 'utf8')
  await inbox.getByRole('button', { name: '刷新收件箱', exact: true }).click()
  await expect(inbox.getByText(/另有 1 个条目/)).toBeVisible()
  await inbox.getByRole('button', { name: '检查并收纳：changed-by-external-tool.mkv', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '确认收纳外部处理文件', exact: true })
  await expect(dialog).toBeVisible()
  const confirm = dialog.getByRole('button', { name: '确认检查并收纳', exact: true })
  await expect(confirm).toBeDisabled()
  await dialog.getByLabel('允许替换此任务已有产物', { exact: true }).check()
  // 模拟外部工具在操作者已预览后写入新一代文件，不能靠旧文件名/大小决定归属。
  await copyFile(replacement, candidate)
  const deniedResponse = page.waitForResponse((response) => response.url() === `${host.origin}${confirmPath}`)
  await confirm.click()
  const denied = await deniedResponse
  expect(denied.status()).toBe(409)
  expect(await denied.json()).toMatchObject({ error: { code: 'E_HANDOFF_INBOX_CHANGED' } })
  await expect(dialog).not.toBeVisible()
  await expect(inbox.getByRole('alert')).toContainText('文件仍在写入或已经被替换')
  expect(await readFile(target)).toEqual(sourceBytes)
  expect(await readFile(candidate)).toEqual(replacementBytes)
  expect(await detail(page, runId)).toEqual(before)
  await assertUpstreamRetained()
  // 已消耗的确认票据不能重复使用；请求只在内存复制，不输出任何 token 或票据。
  const replay = await page.evaluate(async ({ path, body }) => {
    const state = window as unknown as { __ZNIKU_HOST_BRIDGE__: { token: string } }
    const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-ZNIKU-Host-Token': state.__ZNIKU_HOST_BRIDGE__.token }, body: JSON.stringify(body) })
    return { status: response.status, body: await response.json() as unknown }
  }, { path: confirmPath, body: denied.request().postDataJSON() as unknown })
  expect(replay).toMatchObject({ status: 409, body: { error: { code: 'E_HANDOFF_INBOX_EXPIRED' } } })
  expect(await readFile(target)).toEqual(sourceBytes)
  await assertUpstreamRetained()
  await inbox.getByRole('button', { name: '刷新收件箱', exact: true }).click()
  await inbox.getByRole('button', { name: '检查并收纳：changed-by-external-tool.mkv', exact: true }).click()
  await dialog.getByLabel('允许替换此任务已有产物', { exact: true }).check()
  const collected = page.waitForResponse((response) => response.url() === `${host.origin}${confirmPath}`)
  await confirm.click()
  expect((await collected).status()).toBe(200)
  await expect(dialog).not.toBeVisible()
  expect(await readFile(target)).toEqual(replacementBytes)
  expect(await exists(candidate)).toBe(false)
  expect(await exists(rejected)).toBe(true)
  expect(await detail(page, runId)).toEqual(before)
  await assertUpstreamRetained()
  expect(proof.commands.filter((item) => item.operation === 'submit_external')).toEqual([])
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeDisabled()
  await helper.getByRole('button', { name: '检查输出', exact: true }).click()
  await expect(helper.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled()
  expect(await detail(page, runId)).toEqual(before)
  await report(page, info, 'C1-changed-candidate', { observed_rejection: 'E_HANDOFF_INBOX_CHANGED', replay_rejection: 'E_HANDOFF_INBOX_EXPIRED', old_target_preserved: true, before_submit_artifacts: before.artifacts.length, automatic_submit: false })
  await helper.getByRole('button', { name: '提交并继续', exact: true }).click()
  await expect.poll(async () => (await detail(page, runId)).run.node_runs.find((item) => item.node_run_id === node.node_run_id)!.state).toBe('completed')
  const after = await detail(page, runId)
  expect(after.run.node_runs.find((item) => item.node_run_id === other.node_run_id)).toEqual(other)
  expect(after.artifacts).toHaveLength(before.artifacts.length + 1)
  expect(proof.commands.filter((item) => item.operation === 'submit_external')).toEqual([
    expect.objectContaining({ run_id: runId, node_run_id: node.node_run_id, handoff_id: node.external_handoff!.handoff_id }),
  ])
  expect(await readFile(original)).toEqual(sourceBytes)
  expect(await readFile(replacement)).toEqual(replacementBytes)
  await assertUpstreamRetained()
  expect(proof.accepted).toEqual([`${confirmPath}:409`, `${confirmPath}:409`])
  expect(proof.errors).toEqual([])
})

test('C1 真实发布失败从头重试，不重复已完成上游，也不改写旧尝试', async ({ page }, info) => {
  const proof = evidence(page)
  await open(page, host.fixture.media_project)
  const sourcePath = join(dirname(host.fixture.media_project), 'source.mkv')
  const sourceBytes = await readFile(sourcePath), blocker = await readFile(host.fixture.output_collision)
  const runId = await run(page)
  await expect.poll(async () => (await detail(page, runId)).run.node_runs.some((node) => node.node_id === 'publish' && node.state === 'failed'), { timeout: 45_000 }).toBe(true)
  const before = await detail(page, runId)
  const failed = before.run.node_runs.find((node) => node.node_id === 'publish')!
  const upstreamBytes = new Map(await Promise.all(before.artifacts.map(async (artifact) => [artifact.path, await readFile(artifact.path)] as const)))
  expect(failed.error?.message).toContain('E_MEDIA_OUTPUT_EXISTS')
  expect(before.run.node_runs.filter((node) => node.state === 'completed')).toHaveLength(2)
  expect(await readFile(host.fixture.output_collision)).toEqual(blocker)
  await page.locator('.react-flow__node[data-id="publish"]').click()
  await page.getByRole('tab', { name: '设置', exact: true }).click()
  await page.getByRole('button', { name: '从此步骤重新处理', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '确认重新处理的影响', exact: true })
  await expect(dialog.getByRole('region', { name: '将重新处理的步骤', exact: true }).locator('li')).toHaveCount(1)
  await expect(dialog.getByRole('region', { name: '可以复用的步骤', exact: true }).locator('li')).toHaveCount(2)
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  expect(await detail(page, runId)).toEqual(before)
  for (const [path, bytes] of upstreamBytes) expect(await readFile(path)).toEqual(bytes)
  expect(proof.commands.filter((item) => item.operation === 'rerun_from_here')).toEqual([])
  // 仅把本 fixture 创建的冲突文件换名保留，正式 Graph 和 overwrite 参数保持原值。
  const retained = `${host.fixture.output_collision}.retained`
  await rename(host.fixture.output_collision, retained)
  await page.getByRole('button', { name: '从此步骤重新处理', exact: true }).click()
  await expect(dialog.getByRole('button', { name: '确认从头重新处理', exact: true })).toBeEnabled()
  await report(page, info, 'C1-retry-preview', { failed_attempt: failed.attempt, reusable_nodes: 2, rerun_nodes: 1, cancellation_preserved_run: true })
  await dialog.getByRole('button', { name: '确认从头重新处理', exact: true }).click()
  await expect.poll(async () => (await detail(page, runId)).run.state, { timeout: 45_000 }).toBe('completed')
  const after = await detail(page, runId)
  expect(after.run.node_runs.filter((node) => node.node_id !== 'publish')).toEqual(before.run.node_runs.filter((node) => node.node_id !== 'publish'))
  expect(after.run.node_runs.find((node) => node.node_run_id === failed.node_run_id)).toEqual(failed)
  const attempts = after.run.node_runs.filter((node) => node.node_id === 'publish')
  expect(attempts).toHaveLength(2)
  expect(attempts.find((node) => node.node_run_id !== failed.node_run_id)).toMatchObject({ attempt: failed.attempt + 1, state: 'completed' })
  expect(await readFile(retained)).toEqual(blocker)
  expect(await readFile(sourcePath)).toEqual(sourceBytes)
  for (const [path, bytes] of upstreamBytes) expect(await readFile(path)).toEqual(bytes)
  expect((await status(page)).run_summaries).toHaveLength(1)
  expect(after.artifacts).toHaveLength(before.artifacts.length + 1)
  expect(proof.commands.filter((item) => item.operation === 'rerun_from_here')).toEqual([
    expect.objectContaining({ run_id: runId, node_id: 'publish' }),
  ])
  expect(proof.errors).toEqual([])
  await report(page, info, 'C1-retry-completed', { run_state: after.run.state, upstream_attempts_unchanged: 2, publish_attempts: attempts.length, original_collision_retained: true })
})

test('C1 工程数据取消不迁移，明确复制后保留原件并重新打开同一历史', async ({ page }, info) => {
  const proof = evidence(page)
  await open(page, host.fixture.small_project)
  const runId = await run(page)
  await expect.poll(async () => (await detail(page, runId)).run.state, { timeout: 45_000 }).toBe('completed')
  const before = await detail(page, runId), beforeStatus = await status(page)
  const files = new Map(await Promise.all(before.artifacts.map(async (item) => [item.path, await readFile(item.path)] as const)))
  const roots = before.run.node_runs.map((node) => node.work_dir)
  await page.getByRole('button', { name: '工程', exact: true }).click()
  const inspectResponse = page.waitForResponse((response) => response.url() === `${host.origin}/api/studio/storage/inspect`)
  await page.getByRole('button', { name: '工程数据', exact: true }).click()
  const inspected = await (await inspectResponse).json() as StorageInspection
  const panel = page.getByRole('dialog', { name: '工程数据与归档检查', exact: true })
  await expect(panel.getByText('旧版工作目录：保留原位置，尚未迁移', { exact: true })).toBeVisible()
  await panel.getByRole('button', { name: '选择数据父目录', exact: true }).click()
  await expect(panel.getByRole('button', { name: '预览迁移到新位置', exact: true })).toBeDisabled()
  expect((await status(page)).storage_revision).toBe(beforeStatus.storage_revision)
  await panel.getByRole('button', { name: '选择数据父目录', exact: true }).click()
  const previewResponse = page.waitForResponse((response) => response.url() === `${host.origin}/api/studio/storage/preview`)
  await panel.getByRole('button', { name: '预览迁移到新位置', exact: true }).click()
  const preview = await (await previewResponse).json() as StorageMigrationPreview
  await expect(panel.getByRole('region', { name: '确认数据迁移', exact: true })).toBeVisible()
  expect(preview.target.data_root).toBe(join(host.fixture.output_root, basename(host.fixture.small_project, '.zniku') + '.data'))
  expect(await exists(preview.target.data_root)).toBe(false)
  await panel.getByRole('button', { name: '取消本次迁移', exact: true }).click()
  expect(await exists(preview.target.data_root)).toBe(false)
  expect(await detail(page, runId)).toEqual(before)
  expect((await status(page)).storage_revision).toBe(beforeStatus.storage_revision)
  await panel.getByRole('button', { name: '预览迁移到新位置', exact: true }).click()
  await expect(panel.getByRole('button', { name: '确认复制迁移并保留原件', exact: true })).toBeEnabled()
  await report(page, info, 'C1-storage-preview', { legacy_mode: inspected.storage.mode, files: preview.file_count, attempts: preview.attempt_count, byte_count: preview.byte_count, cancellation_no_copy: true })
  const confirmed = page.waitForResponse((response) => response.url() === `${host.origin}/api/studio/storage/confirm`)
  await panel.getByRole('button', { name: '确认复制迁移并保留原件', exact: true }).click()
  const response = await confirmed
  expect(response.status()).toBe(200)
  const moved = await response.json() as StorageInspection
  await expect(panel.getByText('自定义磁盘位置', { exact: true })).toBeVisible()
  expect(moved.storage.data_root).toBe(preview.target.data_root)
  expect(moved.missing).toEqual([])
  expect(moved.external_dependencies).toEqual(inspected.external_dependencies)
  for (const [path, bytes] of files) expect(await readFile(path)).toEqual(bytes)
  for (const path of roots) expect((await stat(path)).isDirectory()).toBe(true)
  const migrated = await detail(page, runId)
  expect(migrated.run.graph_snapshot).toEqual(before.run.graph_snapshot)
  expect(migrated.run.node_runs.map((node) => [node.node_run_id, node.attempt, node.state])).toEqual(before.run.node_runs.map((node) => [node.node_run_id, node.attempt, node.state]))
  for (const node of migrated.run.node_runs) {
    expect(node.work_dir.startsWith(moved.storage.attempts_root + sep)).toBe(true)
    expect(basename(node.work_dir)).toBe(basename(before.run.node_runs.find((old) => old.node_run_id === node.node_run_id)!.work_dir))
  }
  for (const artifact of migrated.artifacts) expect(await readFile(artifact.path)).toEqual(files.get(before.artifacts.find((old) => old.artifact_id === artifact.artifact_id)!.path))
  await panel.getByRole('button', { name: '完成', exact: true }).click()
  await open(page, host.fixture.small_project)
  expect((await status(page)).snapshot!.project.graph).toEqual(beforeStatus.snapshot!.project.graph)
  expect(await detail(page, runId)).toEqual(migrated)
  expect(proof.errors).toEqual([])
  await report(page, info, 'C1-storage-reopened', { storage_mode: moved.storage.mode, registered_files: moved.registered_file_count, originals_retained: files.size, unchanged_run_id: true, graph_unchanged: true, reopened_history_equal: true })
})

test('C2 未应用草稿阻止历史覆盖，真实 CAS 冲突只在明确放弃后载入', async ({ page }, info) => {
  const proof = evidence(page, [{ path: '/api/studio/command', status: 409 }])
  await open(page, host.fixture.geometry_project)
  const runId = await run(page)
  await expect.poll(async () => (await detail(page, runId)).run.node_runs.some((node) => node.node_id === 'publish' && node.state === 'failed'), { timeout: 45_000 }).toBe(true)
  const history = await detail(page, runId)
  await page.locator('.canvas-context').getByRole('button', { name: '返回当前编辑', exact: true }).click()
  await page.locator('.react-flow__node[data-id="publish"]').click()
  await page.getByRole('tab', { name: '设置', exact: true }).click()
  const overwrite = page.getByRole('checkbox', { name: /^允许覆盖/ })
  await overwrite.check()
  const beforeDraft = await status(page)
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  await page.getByRole('tab', { name: '诊断', exact: true }).click()
  await page.getByRole('tab', { name: '设置', exact: true }).click()
  await expect(overwrite).toBeChecked()
  await expect(page.getByText('参数尚未应用；切换页签会保留更改。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '处理记录', exact: true }).click()
  await page.locator(`.task-drawer-history button[value="${runId}"]`).click()
  await expect(page.locator('.canvas-context')).toContainText('当前编辑')
  await expect(overwrite).toBeChecked()
  expect((await status(page)).snapshot!.project.graph).toEqual(beforeDraft.snapshot!.project.graph)
  expect(await detail(page, runId)).toEqual(history)
  await page.getByRole('button', { name: '放弃未应用更改', exact: true }).click()
  await expect(overwrite).not.toBeChecked()
  await page.getByRole('button', { name: '收起任务区', exact: true }).click()
  await page.getByText('显示设置', { exact: true }).click()
  const alias = page.getByRole('textbox', { name: '节点别名', exact: true })
  let releaseSave!: () => void, intercepted!: () => void
  const gate = new Promise<void>((done) => { releaseSave = done })
  const held = new Promise<void>((done) => { intercepted = done })
  let firstSave = true
  await page.route(`${host.origin}/api/studio/command`, async (route) => {
    if (firstSave && (route.request().postDataJSON() as { operation?: string }).operation === 'save_project') {
      firstSave = false; intercepted(); await gate
    }
    await route.continue()
  })
  try {
    await alias.fill('只在本页保留的别名')
    await alias.press('Enter')
    await held
    const remote = await status(page)
    const remoteProject = { ...remote.snapshot!.project, name: '另一客户端明确保存的新名称' }
    const saved = await page.request.post(`${host.origin}/api/studio/command`, { headers: { Origin: host.origin }, data: {
      operation: 'save_project', project_session_id: remote.project_session_id, expected_storage_revision: remote.storage_revision,
      project: remoteProject, studio_state: remote.studio_state,
    } })
    expect(saved.status()).toBe(200)
    const conflictResponse = page.waitForResponse((response) => response.url() === `${host.origin}/api/studio/command` && response.status() === 409)
    releaseSave()
    expect(await (await conflictResponse).json()).toMatchObject({ error: { code: 'E_PROJECT_STORAGE_CONFLICT' } })
    await expect(page.getByRole('button', { name: '重新载入磁盘版本', exact: true })).toBeVisible()
    await expect(alias).toHaveValue('只在本页保留的别名')
    expect((await status(page)).snapshot!.project.name).toBe(remoteProject.name)
    expect((await status(page)).studio_state!.node_views.some((item) => item.display_name === '只在本页保留的别名')).toBe(false)
    page.once('dialog', (dialog) => void dialog.dismiss())
    await page.getByRole('button', { name: '重新载入磁盘版本', exact: true }).click()
    await expect(alias).toHaveValue('只在本页保留的别名')
    await report(page, info, 'C2-cas-conflict', { rejection: 'E_PROJECT_STORAGE_CONFLICT', local_alias_preserved: true, remote_not_overwritten: true, draft_not_applied: true, history_unchanged: true })
    page.once('dialog', (dialog) => void dialog.accept())
    await page.getByRole('button', { name: '重新载入磁盘版本', exact: true }).click()
    await expect(page.locator('.project-shell-identity')).toContainText(remoteProject.name)
    await expect(page.getByRole('button', { name: '重新载入磁盘版本', exact: true })).toHaveCount(0)
    expect(await detail(page, runId)).toEqual(history)
    expect(proof.commands.filter((item) => ['run_all', 'rerun_from_here', 'submit_external'].includes(String(item.operation)))).toHaveLength(1)
    expect(proof.accepted).toEqual(['/api/studio/command:409'])
    expect(proof.errors).toEqual([])
  } finally { releaseSave(); await page.unroute(`${host.origin}/api/studio/command`) }
})

test('C2 菜单焦点不删除节点，迟到旧轮询不覆盖新编辑或抢焦点', async ({ page }, info) => {
  const proof = evidence(page)
  await open(page, host.fixture.small_project)
  await page.locator('.react-flow__node[data-id="step-1"]').click()
  const before = await status(page)
  const projectMenu = page.getByRole('button', { name: '工程', exact: true })
  await projectMenu.focus()
  await page.keyboard.press('Delete')
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  expect((await status(page)).snapshot!.project.graph).toEqual(before.snapshot!.project.graph)
  expect(proof.commands).toEqual([])
  await page.getByRole('tab', { name: '设置', exact: true }).click()
  await page.getByText('显示设置', { exact: true }).click()
  const alias = page.getByRole('textbox', { name: '节点别名', exact: true })
  let releasePoll!: () => void, pollHeld!: () => void, pollDelivered!: () => void
  const gate = new Promise<void>((done) => { releasePoll = done })
  const held = new Promise<void>((done) => { pollHeld = done })
  const delivered = new Promise<void>((done) => { pollDelivered = done })
  let first = true
  const statusRoute = `${host.origin}/api/studio/status*`
  await page.route(statusRoute, async (route) => {
    if (!first) { await route.continue(); return }
    first = false
    // 延迟真实 Python 状态的网络交付，不构造或改写旧合同。
    const response = await route.fetch()
    pollHeld()
    await gate
    await route.fulfill({ response })
    pollDelivered()
  })
  try {
    await expect.poll(() => first).toBe(false)
    await held
    await alias.fill('明确保存的新别名')
    await alias.press('Enter')
    await expect.poll(async () => (await status(page)).studio_state!.node_views.find((item) => item.node_id === 'step-1')?.display_name).toBe('明确保存的新别名')
    await expect(page.locator('.project-shell-identity')).toContainText('已保存')
    await alias.focus()
    await expect(alias).toBeFocused()
    releasePoll()
    await delivered
    await page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))))
    await expect(alias).toHaveValue('明确保存的新别名')
    await expect(alias).toBeFocused()
    expect((await status(page)).snapshot!.project.graph).toEqual(before.snapshot!.project.graph)
    await report(page, info, 'C2-late-poll-and-menu-keyboard', { menu_delete_mutations: 0, delayed_real_response: true, latest_alias_preserved: true, input_focus_preserved: true })
  } finally { releasePoll(); await page.unroute(statusRoute) }
  // 回到画布后同一键盘命令仍然可用，保护菜单不能成为全局禁用编辑。
  await page.locator('#workflow-canvas').focus()
  await page.keyboard.press('Delete')
  await expect(page.locator('.react-flow__node')).toHaveCount(1)
  await expect.poll(async () => (await status(page)).snapshot!.project.graph.nodes.length).toBe(1)
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  await expect.poll(async () => (await status(page)).snapshot!.project.graph).toEqual(before.snapshot!.project.graph)
  await page.setViewportSize({ width: 800, height: 720 })
  await page.getByRole('button', { name: '展开任务区', exact: true }).click()
  await page.getByRole('button', { name: '关闭任务区', exact: true }).click()
  await expect(page.getByRole('button', { name: '展开任务区', exact: true })).toBeFocused()
  await expect(page.getByRole('button', { name: '展开任务区', exact: true })).toBeInViewport()
  await report(page, info, 'C2-narrow-task-focus', { css_viewport: { width: 800, height: 720 }, drawer_closed_focus_visible: true, canvas_delete_and_undo: true, native_windows_dpi_proof: false })
  expect(proof.commands.filter((item) => ['run_all', 'rerun_from_here', 'submit_external'].includes(String(item.operation)))).toEqual([])
  expect(proof.errors).toEqual([])
})
