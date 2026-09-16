/** 单文件交回 wire 由 Python Schema 校验；选择、检查、发布票据逐步绑定且不自动重放。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FetchHostBridge } from './host-bridge'
import { intakeBinding, intakeCandidate, intakeJob, intakeObservation, intakeSelection } from './handoff-intake-fixtures'
afterEach(() => vi.unstubAllGlobals())
const selection = { ...intakeBinding, candidate_handle: intakeCandidate.candidate_handle, selection_handle: null }
const check = { contract_version: '0.3.0' as const, ticket_id: intakeSelection.ticket_id, overwrite: false }
const job = { contract_version: '0.3.0' as const, job_id: intakeJob.job_id }
const publish = { contract_version: '0.3.0' as const, ready_id: intakeJob.ready_id! }
function response(value: unknown) { return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }) }
function host() { return new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token: 'a'.repeat(43) }) }
describe('单文件交回 HostBridge', () => {
  it('五阶段都使用POST+token，选择不发送原始路径', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(intakeObservation)).mockResolvedValueOnce(response(intakeSelection))
      .mockResolvedValueOnce(response(job)).mockResolvedValueOnce(response(intakeJob))
      .mockResolvedValueOnce(response({ contract_version: '0.3.0', output_path: intakeSelection.output_path }))
    vi.stubGlobal('fetch', fetch)
    const bridge = host()
    await expect(bridge.observeHandoffIntake(intakeBinding)).resolves.toEqual(intakeObservation)
    await expect(bridge.selectHandoffIntake(selection)).resolves.toEqual(intakeSelection)
    expect(fetch).toHaveBeenCalledTimes(2)
    await expect(bridge.checkHandoffIntake(check)).resolves.toEqual(job)
    await expect(bridge.inspectHandoffIntake(job)).resolves.toEqual(intakeJob)
    await expect(bridge.publishHandoffIntake(publish)).resolves.toMatchObject({ output_path: intakeSelection.output_path })
    expect(fetch.mock.calls.map(([url]) => url)).toEqual(['observe', 'select', 'check', 'status', 'publish'].map((route) => `http://127.0.0.1:18765/api/studio/handoff-intake/${route}`))
    for (const [, options] of fetch.mock.calls) {
      expect(options.method).toBe('POST')
      expect(options.headers).toMatchObject({ 'X-ZNIKU-Host-Token': 'a'.repeat(43) })
      expect(options.body).not.toContain('source_path')
      expect(options.body).not.toContain('a'.repeat(43))
    }
    await expect(bridge.publishHandoffIntake(publish)).rejects.toThrow('已失效')
    await expect(bridge.checkHandoffIntake(check)).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(5)
  })
  it('原始路径、未知字段、错绑定和不支持实际容器失败关闭', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ ...intakeObservation, candidates: [{ ...intakeCandidate, container: 'avi' }] }))
      .mockResolvedValueOnce(response({ ...intakeSelection, node_run_id: intakeBinding.run_id }))
    vi.stubGlobal('fetch', fetch)
    const bridge = host()
    await expect(bridge.observeHandoffIntake({ ...intakeBinding, path: 'D:\\unsafe.mp4' } as typeof intakeBinding)).rejects.toThrow('Schema')
    expect(fetch).not.toHaveBeenCalled()
    await expect(bridge.observeHandoffIntake(intakeBinding)).rejects.toThrow('Schema')
    await expect(bridge.selectHandoffIntake(selection)).rejects.toThrow('不属于')
    await expect(bridge.checkHandoffIntake(check)).rejects.toThrow('已失效')
  })
  it('选择必须二选一，覆盖确认未给出不得消费票据或发出检查', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ ...intakeSelection, replace_existing: true })).mockResolvedValueOnce(response(job))
    vi.stubGlobal('fetch', fetch)
    const bridge = host()
    await expect(bridge.selectHandoffIntake({ ...selection, candidate_handle: null })).rejects.toThrow('一个文件')
    await expect(bridge.selectHandoffIntake({ ...selection, selection_handle: 'selection_1234567890_1234567890' })).rejects.toThrow('一个文件')
    expect(fetch).not.toHaveBeenCalled()
    await bridge.selectHandoffIntake(selection)
    await expect(bridge.checkHandoffIntake(check)).rejects.toThrow('明确允许')
    expect(fetch).toHaveBeenCalledTimes(1)
    await expect(bridge.checkHandoffIntake({ ...check, overwrite: true })).resolves.toEqual(job)
  })
  it('检查回执的任务与真实进度不一致拒绝ready', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(intakeSelection)).mockResolvedValueOnce(response(job))
      .mockResolvedValueOnce(response({ ...intakeJob, job_id: 'other_job_1234567890_1234567890' }))
      .mockResolvedValueOnce(response({ ...intakeJob, bytes_done: 2000 }))
      .mockResolvedValueOnce(response({ ...intakeJob, phase: 'checking' }))
    vi.stubGlobal('fetch', fetch)
    const bridge = host()
    await bridge.selectHandoffIntake(selection); await bridge.checkHandoffIntake(check)
    for (let index = 0; index < 3; index++) await expect(bridge.inspectHandoffIntake(job)).rejects.toThrow('不一致')
    await expect(bridge.publishHandoffIntake(publish)).rejects.toThrow('已失效')
  })
  it('publish目标必须等于检查的实际容器路径；失败仍不重放', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(intakeSelection)).mockResolvedValueOnce(response(job)).mockResolvedValueOnce(response(intakeJob))
      .mockResolvedValueOnce(response({ contract_version: '0.3.0', output_path: 'D:\\unexpected.mp4' }))
    vi.stubGlobal('fetch', fetch)
    const bridge = host()
    await bridge.selectHandoffIntake(selection); await bridge.checkHandoffIntake(check); await bridge.inspectHandoffIntake(job)
    await expect(bridge.publishHandoffIntake(publish)).rejects.toThrow('不一致')
    await expect(bridge.publishHandoffIntake(publish)).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(4)
  })
})
