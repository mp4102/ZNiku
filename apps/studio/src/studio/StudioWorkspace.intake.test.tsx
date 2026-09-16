/** 新交回在真实 Workspace 中按发布实际容器核验，绝不以旧建议路径或前端票据替代正式提交。 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import type { StudioGateway } from './gateway'
import type { HostBridge } from './host-bridge'
import { handoffDetailEnvelope, handoffEnvelope, handoffReadinessEnvelope, handoffLogEnvelope } from './test-fixtures'
import { intakeJob, intakeSelection, intakeObservation } from './handoff-intake-fixtures'
afterEach(() => { cleanup(); window.localStorage.clear() })
function setup(wrongPublishedProbe = false) {
  const state = handoffEnvelope(), base = handoffDetailEnvelope()
  const waiting = base.run.node_runs.find((item) => item.state === 'waiting_external')!
  const detail = { ...base, handoff_contracts: [{ node_run_id: waiting.node_run_id, handoff_id: waiting.external_handoff!.handoff_id,
    input_artifact_id: waiting.external_handoff!.input_artifact_ids[0]!, title: '合成交回', fields: [], intake_supported: true }] }
  const order: string[] = []
  const command = vi.fn(async () => { order.push('submit'); return state })
  const readiness = vi.fn(async (_run: string, _node: string, probe: boolean) => { order.push('probe'); const value = handoffReadinessEnvelope('probe_passed', probe)
    return { ...value, targets: value.targets.map((target) => ({ ...target, path: wrongPublishedProbe ? 'D:\\wrong.mov' : intakeSelection.output_path })) } })
  const gateway: StudioGateway = { command, inspect: vi.fn(async () => state), inspectRun: vi.fn(async () => detail),
    listRuns: vi.fn(async () => ({ contract_version: '0.3.0' as const, run_summaries: [], next_run_cursor: null })),
    inspectReadiness: readiness, inspectLog: vi.fn(async () => handoffLogEnvelope()), previewAvEnhanceV27: vi.fn() }
  const publish = vi.fn(async () => { order.push('publish'); return { contract_version: '0.3.0' as const, output_path: intakeSelection.output_path } })
  const bridge: HostBridge = { configured: true, inspectCapabilities: vi.fn(async () => ({ contract_version: '0.3.0' as const, capabilities:
    (['open_file', 'open_files', 'select_directory', 'save_file', 'reveal_in_file_manager', 'open_with_system_player'] as const)
      .map((capability) => ({ capability, available: true, unavailable_reason: null })) })), pick: vi.fn(), launch: vi.fn(),
    observeHandoffIntake: vi.fn(async (binding) => ({ ...intakeObservation, ...binding })),
    selectHandoffIntake: vi.fn(async (request) => ({ ...intakeSelection, ...request })),
    checkHandoffIntake: vi.fn(async () => ({ contract_version: '0.3.0' as const, job_id: intakeJob.job_id })),
    inspectHandoffIntake: vi.fn(async () => intakeJob), publishHandoffIntake: publish }
  return { gateway, bridge, command, order, readiness, publish, waiting }
}
async function assistant() {
  await screen.findByRole('tab', { name: '文件' })
  const close = screen.queryByRole('button', { name: '关闭工程首页' })
  if (close) await act(async () => { fireEvent.click(close) })
  fireEvent.click(screen.getByRole('tab', { name: '文件' }))
  const task = await screen.findByRole('button', { name: /^查看外部任务：/ })
  await act(async () => { fireEvent.click(task) })
  const section = await screen.findByRole('region', { name: '外部处理助手' })
  const area = within(section)
  await waitFor(() => expect(area.getByRole('button', { name: '检查并导入' })).toBeEnabled())
  return area
}
describe('Workspace 单文件先检查后导入', () => {
  it('实际MOV交回可替代MP4建议名：显式一次提交按 publish→probe→Submit 串联', async () => {
    const test = setup()
    render(<App gateway={test.gateway} hostBridge={test.bridge} />)
    const area = await assistant()
    expect(test.readiness).not.toHaveBeenCalled()
    expect(test.publish).not.toHaveBeenCalled()
    fireEvent.click(area.getByRole('button', { name: '检查并导入' }))
    await waitFor(() => expect(area.getByRole('button', { name: '提交并继续' })).toBeEnabled())
    expect(test.command).not.toHaveBeenCalled()
    fireEvent.click(area.getByRole('button', { name: '提交并继续' }))
    await waitFor(() => expect(test.command).toHaveBeenCalledOnce())
    expect(test.order).toEqual(['publish', 'probe', 'submit'])
    expect(test.command).toHaveBeenCalledWith({ operation: 'submit_external', run_id: test.waiting.run_id,
      node_run_id: test.waiting.node_run_id, handoff_id: test.waiting.external_handoff!.handoff_id })
  })
  it('发布后probe指向别的文件则阻止正式Submit', async () => {
    const test = setup(true)
    render(<App gateway={test.gateway} hostBridge={test.bridge} />)
    const area = await assistant()
    fireEvent.click(area.getByRole('button', { name: '检查并导入' }))
    await waitFor(() => expect(area.getByRole('button', { name: '提交并继续' })).toBeEnabled())
    fireEvent.click(area.getByRole('button', { name: '提交并继续' }))
    await area.findByRole('alert')
    expect(test.order).toEqual(['publish', 'probe'])
    expect(test.command).not.toHaveBeenCalled()
    expect(area.getByRole('button', { name: '提交并继续' })).toBeDisabled()
  })
  it('刷新后的已发布大文件可重新检查并显式提交，不再次复制或publish', async () => {
    const test = setup()
    render(<App gateway={test.gateway} hostBridge={test.bridge} />)
    const area = await assistant()
    fireEvent.click(area.getByText('刷新页面后找不到已导入的文件？'))
    fireEvent.click(area.getByRole('button', { name: '检查已收纳输出' }))
    await waitFor(() => expect(area.getByRole('button', { name: '提交并继续' })).toBeEnabled())
    expect(test.command).not.toHaveBeenCalled()
    expect(test.publish).not.toHaveBeenCalled()
    expect(test.bridge.checkHandoffIntake).not.toHaveBeenCalled()
    fireEvent.click(area.getByRole('button', { name: '提交并继续' }))
    await waitFor(() => expect(test.command).toHaveBeenCalledOnce())
    expect(test.order).toEqual(['probe', 'probe', 'submit'])
    expect(test.publish).not.toHaveBeenCalled()
  })
})
