/** Phase 4 创作者集成验收：使用正式 wire 形状的合成投影，不把浏览器测试替身作为领域权威。 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { ExternalHandoffReadiness, RerunPreviewRequest, RunDetailEnvelope, StatusEnvelope, StudioCommand } from './studio/contracts'
import type { StudioGateway } from './studio/gateway'
import type { HostBridge } from './studio/host-bridge'
import { failedDetailEnvelope, failedStatusEnvelope, handoffDetailEnvelope, handoffEnvelope, handoffFixtureIds,
  handoffLogEnvelope, handoffReadinessEnvelope, rerunPreviewEnvelope, studioEnvelope, threeRunDetail,
  threeRunEnvelope, threeRunFixtureIds } from './studio/test-fixtures'

afterEach(() => { cleanup(); window.localStorage.clear(); vi.useRealTimers() })

function friendly(envelope: StatusEnvelope): StatusEnvelope {
  return { ...envelope, studio_state: { contract_version: '0.3.0', viewport: null, groups: [],
    node_views: ['source', 'transform', 'sink'].map((id, index) => ({ node_id: id,
      display_name: ['导入视频', '外部画质增强', '保存成品'][index]!, group_id: null, collapsed: false })) } }
}
function setup(initial = handoffEnvelope(), initialDetail = handoffDetailEnvelope()) {
  let state = friendly(initial)
  let detail = initialDetail
  let inspectFailure = false
  const commands: StudioCommand[] = []
  const previews: RerunPreviewRequest[] = []
  const logs = vi.fn(async () => handoffLogEnvelope())
  const readiness = vi.fn(async (_run: string, _node: string, probe: boolean) =>
    handoffReadinessEnvelope(probe ? 'probe_passed' : 'present', probe))
  const gateway: StudioGateway = {
    inspect: async () => { if (inspectFailure) throw new Error('synthetic offline'); return state },
    listRuns: async () => ({ contract_version: '0.3.0', run_summaries: [], next_run_cursor: null }),
    inspectRun: async (id) => id === detail.run.run_id ? detail : threeRunDetail(id),
    inspectLog: logs,
    inspectReadiness: readiness,
    previewRerun: async (request) => { previews.push(request); return rerunPreviewEnvelope(request) },
    previewAvEnhanceV27: async () => { throw new Error('not used') },
    command: async (command) => { commands.push(command); return state },
  }
  return { gateway, commands, previews, logs, readiness,
    update: (value: StatusEnvelope, run = detail) => { state = friendly(value); detail = run },
    offline: () => { inspectFailure = true } }
}
async function assistant() {
  // 未选节点只显示任务导航；先明确选择本次交接，后续动作才有唯一目标。
  await screen.findByRole('tab', { name: '文件' })
  const home = screen.queryByRole('button', { name: '关闭工程首页' })
  if (home) await act(async () => { fireEvent.click(home) })
  fireEvent.click(screen.getByRole('tab', { name: '文件' }))
  if (!screen.queryByRole('region', { name: '外部处理助手' })) {
    const task = screen.queryByRole('button', { name: '查看外部任务：外部画质增强' })
      ?? await screen.findByRole('button', { name: '查看外部任务：外部画质增强' })
    await act(async () => { fireEvent.click(task) })
  }
  const section = screen.queryByRole('region', { name: '外部处理助手' })
    ?? await screen.findByRole('region', { name: '外部处理助手' })
  await waitFor(() => expect(within(section).getByRole('button', { name: '检查输出' })).toBeEnabled())
  return within(section)
}
async function tick(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
function history() {
  const open = screen.queryByRole('button', { name: '展开任务区' })
  if (open) fireEvent.click(open)
  const drawer = screen.getByRole('region', { name: '任务抽屉' })
  fireEvent.click(within(drawer).getByRole('tab', { name: '历史记录' }))
  return within(drawer).getByRole('tabpanel', { name: '历史记录' })
}
async function retryStep() {
  await screen.findByRole('button', { name: '查看问题' })
  const home = screen.queryByRole('button', { name: '关闭工程首页' })
  if (home) fireEvent.click(home)
  fireEvent.click(screen.getByRole('button', { name: '查看问题' }))
  const problems = screen.getByRole('region', { name: '问题与恢复' })
  fireEvent.click(within(problems).getByRole('button', { name: '定位步骤与设置' }))
  fireEvent.click(screen.getByRole('tab', { name: '设置' }))
  const retry = screen.getByRole('button', { name: '从此步骤重新处理' })
  await waitFor(() => expect(retry).toBeEnabled())
  fireEvent.click(retry)
}

describe('Phase 4 默认创作者操作链', () => {
  it('默认只显示上下文主操作，不读取日志；高级信息可查看但不会变更任务', async () => {
    const test = setup()
    render(<App gateway={test.gateway} />)
    await assistant()
    expect(screen.getByRole('button', { name: '处理外部文件' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '开始新的完整处理' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: '查看 Run' })).not.toBeInTheDocument()
    expect(history().textContent).not.toContain(handoffFixtureIds.run)
    expect(test.logs).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '视图' }))
    fireEvent.click(screen.getByRole('button', { name: '高级节点图' }))
    expect(test.logs).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: '诊断' }))
    await waitFor(() => expect(test.logs).toHaveBeenCalledTimes(1))
    expect(test.commands).toEqual([])
  })

  it('完整检查与显式提交分离；提交再次检查并绑定原任务', async () => {
    const test = setup()
    render(<App gateway={test.gateway} />)
    const area = await assistant()
    expect(area.getByRole('button', { name: '提交并继续' })).toBeDisabled()
    fireEvent.click(area.getByRole('button', { name: '检查输出' }))
    await waitFor(() => expect(area.getByRole('button', { name: '提交并继续' })).toBeEnabled())
    expect(test.commands).toEqual([])
    fireEvent.click(area.getByRole('button', { name: '提交并继续' }))
    await waitFor(() => expect(test.commands).toHaveLength(1))
    expect(test.commands[0]).toEqual({ operation: 'submit_external', run_id: handoffFixtureIds.run,
      node_run_id: handoffFixtureIds.transformNodeRun, handoff_id: handoffFixtureIds.handoff })
    expect(test.readiness.mock.calls.filter((call) => call[2])).toHaveLength(2)
  })

  it('两次检查之间文件被替换时拒绝提交，不登记或推进任务', async () => {
    const test = setup()
    let checks = 0
    test.readiness.mockImplementation(async (_run, _node, probe) => {
      const value = handoffReadinessEnvelope(probe ? 'probe_passed' : 'present', probe)
      if (probe && ++checks > 1) return { ...value, targets: value.targets.map((target) => ({ ...target, mtime_ns: 999 })) }
      return value
    })
    render(<App gateway={test.gateway} />)
    const area = await assistant()
    fireEvent.click(area.getByRole('button', { name: '检查输出' }))
    await waitFor(() => expect(area.getByRole('button', { name: '提交并继续' })).toBeEnabled())
    fireEvent.click(area.getByRole('button', { name: '提交并继续' }))
    await screen.findByText('输出已变化或未通过最新检查；没有提交。请重新检查输出，再确认提交。')
    expect(test.commands).toEqual([])
    expect(area.getByRole('button', { name: '提交并继续' })).toBeDisabled()
  })

  it('检查后的只读文件轮询不把画布提示退回未检查，也不自动提交', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const test = setup()
    render(<App gateway={test.gateway} />)
    const area = await assistant()
    fireEvent.click(area.getByRole('button', { name: '检查输出' }))
    await waitFor(() => expect(area.getByRole('button', { name: '提交并继续' })).toBeEnabled())
    await tick(5000)
    // 同一检查摘要归文件页，不再要求画布覆盖层重复一份。
    expect(area.getByText(/完整检查通过，等待你提交/)).toBeVisible()
    expect(test.commands).toEqual([])
    expect(test.readiness.mock.calls.filter((call) => call[2])).toHaveLength(1)
  })

  it('重复点击检查只产生一次完整检查，检查迟到不能提交到已切换的任务', async () => {
    const test = setup(threeRunEnvelope())
    let resolve!: (value: ExternalHandoffReadiness) => void
    const pending = new Promise<ExternalHandoffReadiness>((done) => { resolve = done })
    test.readiness.mockImplementation(async (_run, _node, probe) => probe ? pending : handoffReadinessEnvelope())
    render(<App gateway={test.gateway} />)
    // 当前需人工处理记录优先于最近完成的局部记录。
    const area = await assistant()
    const check = area.getByRole('button', { name: '检查输出' })
    fireEvent.click(check); fireEvent.click(check)
    await waitFor(() => expect(test.readiness.mock.calls.filter((call) => call[2])).toHaveLength(1))
    const record = within(history()).getAllByRole('button').find((item) => (item as HTMLButtonElement).value === threeRunFixtureIds.laterLocalRun)
    expect(record).toBeDefined()
    fireEvent.click(record!)
    await act(async () => { resolve(handoffReadinessEnvelope('probe_passed', true)); await pending })
    await waitFor(() => expect(screen.queryByRole('region', { name: '外部处理助手' })).not.toBeInTheDocument())
    expect(test.commands).toEqual([])
  })

  it('外部助手打开目录/输入必须使用正式 HostBridge 交接引用，不传任意路径', async () => {
    const test = setup()
    const launch = vi.fn(async () => undefined)
    const host: HostBridge = { configured: true, pick: async () => null, launch,
      inspectCapabilities: async () => ({ contract_version: '0.3.0', capabilities:
        (['open_file', 'open_files', 'select_directory', 'save_file', 'reveal_in_file_manager', 'open_with_system_player'] as const)
          .map((capability) => ({ capability, available: true, unavailable_reason: null })) }) }
    render(<App gateway={test.gateway} hostBridge={host} />)
    const area = await assistant()
    fireEvent.click(area.getByRole('button', { name: '打开工作目录' }))
    fireEvent.click(area.getByRole('button', { name: '打开输入' }))
    await waitFor(() => expect(launch).toHaveBeenCalledTimes(2))
    const identity = { kind: 'handoff', run_id: handoffFixtureIds.run,
      node_run_id: handoffFixtureIds.transformNodeRun, handoff_id: handoffFixtureIds.handoff }
    expect(launch).toHaveBeenNthCalledWith(1, 'reveal_in_file_manager', { ...identity, selector: { role: 'work_directory' } })
    expect(launch).toHaveBeenNthCalledWith(2, 'open_with_system_player', { ...identity,
      selector: { role: 'input_artifact', artifact_id: handoffFixtureIds.artifact } })
    expect(test.commands).toEqual([])
  })

  it('重跑先展示正式影响；取消没有命令，确认才发起精确重跑', async () => {
    const user = userEvent.setup()
    const test = setup(failedStatusEnvelope('interrupted'), failedDetailEnvelope('interrupted'))
    render(<App gateway={test.gateway} />)
    await retryStep()
    let dialog = within(await screen.findByRole('dialog', { name: '确认重新处理的影响' }))
    await waitFor(() => expect(dialog.getByRole('button', { name: '确认从头重新处理' })).toBeEnabled())
    expect(dialog.getByRole('region', { name: '可以复用的步骤' })).toHaveTextContent('导入视频')
    expect(test.commands).toEqual([])
    await user.click(dialog.getByRole('button', { name: '取消' }))
    expect(test.commands).toEqual([])
    await user.click(screen.getByRole('button', { name: '从此步骤重新处理' }))
    dialog = within(await screen.findByRole('dialog', { name: '确认重新处理的影响' }))
    await waitFor(() => expect(dialog.getByRole('button', { name: '确认从头重新处理' })).toBeEnabled())
    await user.click(dialog.getByRole('button', { name: '确认从头重新处理' }))
    await waitFor(() => expect(test.commands).toHaveLength(1))
    expect(test.commands[0]).toEqual(test.previews[1])
  })

  it('预览后工程修订改变，旧影响清单不能创建新的尝试', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const test = setup(failedStatusEnvelope('interrupted'), failedDetailEnvelope('interrupted'))
    render(<App gateway={test.gateway} />)
    await retryStep()
    const dialog = within(await screen.findByRole('dialog', { name: '确认重新处理的影响' }))
    await waitFor(() => expect(dialog.getByRole('button', { name: '确认从头重新处理' })).toBeEnabled())
    test.update({ ...failedStatusEnvelope('interrupted'), storage_revision: 2 })
    await tick(5000)
    fireEvent.click(dialog.getByRole('button', { name: '确认从头重新处理' }))
    await screen.findByText('工程或查看的任务已变化；请关闭此窗口，重新预览重跑影响。')
    expect(test.commands).toEqual([])
  })

  it('零 Output 的合法已完成任务提供输出说明与完成记录，不强迫添加 Output', async () => {
    const run = threeRunDetail(threeRunFixtureIds.laterLocalRun)
    const detail: RunDetailEnvelope = { ...run, artifacts: [], run: { ...run.run,
      node_runs: run.run.node_runs.map((item) => ({ ...item, output_artifact_ids: [] })) } }
    const test = setup(studioEnvelope({ run_summaries: threeRunEnvelope().run_summaries.slice(0, 1),
      active_run_id: detail.run.run_id }), detail)
    render(<App gateway={test.gateway} />)
    expect(await screen.findByRole('button', { name: '查看输出' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '关闭工程首页' }))
    fireEvent.click(screen.getByRole('button', { name: '查看输出' }))
    expect(screen.getByRole('region', { name: '任务抽屉' })).toBeVisible()
    expect(screen.getByText('本次处理没有已登记的输出文件；零输出工作流也是合法的。')).toBeVisible()
    expect(test.commands).toEqual([])
  })
})
