/** 新交回流程只读选择、显式检查和显式提交；跨任务、过期和后台失败不能冒充成功。 */
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { HostBridge } from './host-bridge'
import type { HandoffIntakeObserveEnvelope, HandoffIntakeSelectEnvelope, HandoffIntakeJobEnvelope } from './handoff-intake-contracts'
import { intakeBinding, intakeCandidate, intakeJob, intakeNodeRun, intakeObservation, intakeSelection } from './handoff-intake-fixtures'
import { useHandoffIntake } from './use-handoff-intake'
import type { ActivityListener } from './operation-presentation'

afterEach(cleanup)
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
function setup(candidates = intakeObservation.candidates) {
  const observe = vi.fn(async (): Promise<HandoffIntakeObserveEnvelope> => ({ ...intakeObservation, candidates }))
  const select = vi.fn(async (): Promise<HandoffIntakeSelectEnvelope> => intakeSelection)
  const check = vi.fn(async () => ({ contract_version: '0.3.0' as const, job_id: intakeJob.job_id }))
  const inspect = vi.fn(async (): Promise<HandoffIntakeJobEnvelope> => intakeJob)
  const pick = vi.fn(async () => [{ selection_handle: 'selection_1234567890_1234567890', path: 'D:/synthetic/elsewhere.mov' }])
  const bridge: HostBridge = { configured: true, inspectCapabilities: vi.fn(), launch: vi.fn(), pick,
    observeHandoffIntake: observe, selectHandoffIntake: select, checkHandoffIntake: check, inspectHandoffIntake: inspect,
    publishHandoffIntake: vi.fn() }
  const options = { hostBridge: bridge, projectSessionId: intakeBinding.project_session_id, nodeRun: intakeNodeRun, scope: 'first',
    onActivity: vi.fn<ActivityListener>(), taskLabel: 'A 章外部补帧',
    operationRef: { current: null as symbol | null }, canStart: () => true, isCurrent: () => true,
    onBusyChange: vi.fn(), onChanged: vi.fn(), onCheckPublished: vi.fn(async () => intakeSelection.output_path), onSubmit: vi.fn(async () => {}) }
  return { options, observe, select, check, inspect, pick }
}
describe('单文件交回状态', () => {
  it('唯一来件预选不检查不复制，通过后也只等待显式提交', async () => {
    const fixture = setup(), { result } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.selected).toEqual(intakeSelection))
    expect(fixture.select).toHaveBeenCalledWith({ ...intakeBinding, candidate_handle: intakeCandidate.candidate_handle, selection_handle: null })
    expect(fixture.check).not.toHaveBeenCalled()
    expect(fixture.options.onSubmit).not.toHaveBeenCalled()
    await act(() => result.current.check(false))
    expect(result.current.job?.ready_id).toBe(intakeJob.ready_id)
    expect(fixture.options.onSubmit).not.toHaveBeenCalled()
    await act(() => result.current.submit())
    expect(fixture.options.onSubmit).toHaveBeenCalledWith(intakeNodeRun, intakeJob.ready_id, expect.any(Symbol), null, expect.any(Function))
    expect(fixture.options.operationRef.current).toBeNull()
    expect(fixture.options.onActivity.mock.lastCall?.[0]).toMatchObject({ phase: 'needs_user', fraction: null })
    await act(() => result.current.submit())
    expect(fixture.options.onSubmit).toHaveBeenCalledOnce()
  })
  it('多个来件不猜测；原位置选择不传路径或限制扩展名', async () => {
    const fixture = setup([intakeCandidate, { ...intakeCandidate, candidate_handle: 'other', name: 'other.mkv' }])
    const { result } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.observation).not.toBeNull())
    expect(result.current.selected).toBeNull()
    expect(fixture.select).not.toHaveBeenCalled()
    await act(() => result.current.choose())
    expect(fixture.pick).toHaveBeenCalledWith('open_file', { title: '选择处理好的文件' })
    expect(fixture.select).toHaveBeenCalledWith({ ...intakeBinding, selection_handle: 'selection_1234567890_1234567890', candidate_handle: null })
    expect(fixture.check).not.toHaveBeenCalled()
  })
  it('刷新撤销旧检查资格；新候选仍需用户再次检查', async () => {
    const fixture = setup(), { result } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.selected).not.toBeNull())
    await act(() => result.current.check(false))
    await act(() => result.current.refresh())
    expect(result.current.job).toBeNull()
    await act(() => result.current.submit())
    expect(fixture.options.onSubmit).not.toHaveBeenCalled()
  })
  it('已有目标需明确允许覆盖，失败后选择票据不能再用', async () => {
    const fixture = setup()
    fixture.select.mockResolvedValue({ ...intakeSelection, replace_existing: true })
    fixture.inspect.mockResolvedValue({ ...intakeJob, phase: 'failed', ready_id: null, message: 'Wrong frame count' })
    const { result } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.selected).not.toBeNull())
    await act(() => result.current.check(false))
    expect(fixture.check).not.toHaveBeenCalled()
    await act(() => result.current.check(true))
    expect(result.current.error).toContain('没有提交')
    expect(result.current.selected).toBeNull()
    expect(fixture.options.onSubmit).not.toHaveBeenCalled()
  })
  it('选择响应绑定错误或文件换代失败关闭', async () => {
    const fixture = setup()
    fixture.select.mockResolvedValue({ ...intakeSelection, source_size: intakeSelection.source_size + 1 })
    const { result } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.selected).toBeNull()
    await act(() => result.current.check(false))
    expect(fixture.check).not.toHaveBeenCalled()
  })
  it('A→B→A期间迟到选择不进入新任务', async () => {
    const fixture = setup(), late = deferred<HandoffIntakeSelectEnvelope>()
    fixture.select.mockReturnValueOnce(late.promise)
    const { result, rerender } = renderHook((options) => useHandoffIntake(options), { initialProps: fixture.options })
    await waitFor(() => expect(fixture.select).toHaveBeenCalledOnce())
    rerender({ ...fixture.options, scope: 'B', nodeRun: null as unknown as typeof intakeNodeRun })
    rerender({ ...fixture.options, nodeRun: null as unknown as typeof intakeNodeRun })
    await act(async () => { late.resolve(intakeSelection); await late.promise })
    expect(result.current.selected).toBeNull()
    expect(fixture.options.operationRef.current).toBeNull()
    expect(fixture.options.onActivity.mock.lastCall?.[0]).toMatchObject({ label: 'A 章外部补帧', phase: 'needs_user', fraction: null })
  })
  it('检查已启动时切换保留后台锁直至终态，丢弃旧ready', async () => {
    const fixture = setup(), late = deferred<HandoffIntakeJobEnvelope>()
    const { result, rerender } = renderHook((options) => useHandoffIntake(options), { initialProps: fixture.options })
    await waitFor(() => expect(result.current.selected).not.toBeNull())
    fixture.inspect.mockReturnValueOnce(late.promise)
    let checking!: Promise<void>
    await act(async () => { checking = result.current.check(false); await Promise.resolve() })
    rerender({ ...fixture.options, scope: 'B', taskLabel: 'B 章外部补帧' })
    expect(fixture.options.operationRef.current).not.toBeNull()
    await act(async () => { late.resolve(intakeJob); await checking })
    expect(result.current.job).toBeNull()
    expect(fixture.options.operationRef.current).toBeNull()
    expect(fixture.options.onSubmit).not.toHaveBeenCalled()
    expect(fixture.options.onActivity.mock.lastCall?.[0]).toMatchObject({ label: 'A 章外部补帧', nodeRun: intakeNodeRun, phase: 'uncertain', fraction: null })
    expect(fixture.options.onActivity.mock.lastCall?.[0].message).toContain('原任务')
  })
  it('轮询错误job不授予提交资格；不自动重试副作用', async () => {
    const fixture = setup()
    fixture.inspect.mockResolvedValue({ ...intakeJob, job_id: 'wrong-job' })
    const { result } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.selected).not.toBeNull())
    await act(() => result.current.check(false))
    expect(fixture.check).toHaveBeenCalledOnce()
    expect(result.current.job).toBeNull()
    expect(result.current.error).not.toBeNull()
  })
  it('取消选择器不检查、不复制且保留已有选择', async () => {
    const fixture = setup(), { result } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.selected).not.toBeNull())
    fixture.pick.mockResolvedValueOnce([])
    await act(() => result.current.choose())
    expect(result.current.selected).toEqual(intakeSelection)
    expect(fixture.select).toHaveBeenCalledOnce()
    expect(fixture.check).not.toHaveBeenCalled()
    expect(fixture.options.onActivity.mock.lastCall?.[0].phase).toBe('needs_user')
  })
  it('检查无百分比，复制只投影后台真实字节，终态停止轮询', async () => {
    const fixture = setup()
    fixture.inspect.mockResolvedValueOnce({ ...intakeJob, phase: 'checking', bytes_done: 0, ready_id: null })
      .mockResolvedValueOnce({ ...intakeJob, phase: 'copying', bytes_done: 256, ready_id: null })
      .mockResolvedValueOnce({ ...intakeJob, phase: 'copying', ready_id: null })
    const { result } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.selected).not.toBeNull())
    let checking!: Promise<void>
    await act(async () => { checking = result.current.check(false); await Promise.resolve() })
    expect(fixture.options.onActivity.mock.lastCall?.[0]).toMatchObject({ phase: 'running', fraction: null, label: 'A 章外部补帧' })
    const requestedAt = fixture.options.onActivity.mock.lastCall?.[0].requestedAt
    expect(requestedAt).toEqual(expect.any(Number))
    await waitFor(() => expect(result.current.phase).toBe('copying'))
    expect(result.current.job?.bytes_done).toBe(256)
    expect(fixture.options.onActivity.mock.lastCall?.[0]).toMatchObject({ phase: 'running', fraction: .25 })
    expect(fixture.options.onActivity.mock.lastCall?.[0].requestedAt).toBe(requestedAt)
    expect(fixture.options.onSubmit).not.toHaveBeenCalled()
    await waitFor(() => expect(fixture.options.onActivity.mock.lastCall?.[0].phase).toBe('settling'))
    expect(fixture.options.onActivity.mock.lastCall?.[0]).toMatchObject({ fraction: 1 })
    expect(fixture.options.onActivity.mock.lastCall?.[0].requestedAt).toBe(requestedAt)
    expect(fixture.options.onActivity.mock.lastCall?.[0].message).toContain('等待收纳完成确认')
    expect(result.current.job?.ready_id).toBeNull()
    await act(async () => { await checking })
    expect(result.current.job?.ready_id).toBe(intakeJob.ready_id)
    expect(fixture.inspect).toHaveBeenCalledTimes(4)
    expect(fixture.options.onActivity.mock.lastCall?.[0]).toMatchObject({ phase: 'needs_user', fraction: null })
  })
  it('卸载清理检查轮询定时器，不让后台回执触发提交', async () => {
    const fixture = setup()
    fixture.inspect.mockResolvedValue({ ...intakeJob, phase: 'checking', bytes_done: 0, ready_id: null })
    const { result, unmount } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.selected).not.toBeNull())
    let checking!: Promise<void>
    await act(async () => { checking = result.current.check(false); await Promise.resolve() })
    await waitFor(() => expect(fixture.inspect).toHaveBeenCalledOnce())
    unmount()
    await checking
    expect(fixture.options.operationRef.current).toBeNull()
    expect(fixture.inspect).toHaveBeenCalledOnce()
    expect(fixture.options.onSubmit).not.toHaveBeenCalled()
    expect(fixture.options.onActivity.mock.lastCall?.[0].phase).toBe('uncertain')
  })
  it('提交在途 A→B→A 永久撤销交互资格，发布之后也不能继续旧 Submit', async () => {
    const fixture = setup(), late = deferred<void>()
    let guard: (() => boolean) | undefined
    const onSubmit: Parameters<typeof useHandoffIntake>[0]['onSubmit'] = async (_node, _ready, _token, _path, isCurrent) => { guard = isCurrent; await late.promise }
    const options = { ...fixture.options, onSubmit }
    const { result, rerender } = renderHook((value) => useHandoffIntake(value), { initialProps: options })
    await waitFor(() => expect(result.current.selected).not.toBeNull())
    await act(() => result.current.check(false))
    let submitting!: Promise<void>
    await act(async () => { submitting = result.current.submit(); await Promise.resolve() })
    expect(guard?.()).toBe(true)
    rerender({ ...options, scope: 'B' })
    rerender(options)
    expect(guard?.()).toBe(false)
    await act(async () => { late.resolve(); await submitting })
    expect(result.current.job).toBeNull()
    expect(fixture.options.operationRef.current).toBeNull()
    expect(fixture.options.onActivity.mock.lastCall?.[0]).toMatchObject({ label: 'A 章外部补帧', phase: 'uncertain', fraction: null })
  })
  it('检查请求未获得服务回执时只显示请求等待，不借用先前进度', async () => {
    const fixture = setup(), late = deferred<{ contract_version: '0.3.0'; job_id: string }>()
    fixture.check.mockReturnValueOnce(late.promise)
    const { result } = renderHook(() => useHandoffIntake(fixture.options))
    await waitFor(() => expect(result.current.selected).not.toBeNull())
    let checking!: Promise<void>
    await act(async () => { checking = result.current.check(false); await Promise.resolve() })
    expect(fixture.inspect).not.toHaveBeenCalled()
    expect(fixture.options.onActivity.mock.lastCall?.[0]).toMatchObject({ phase: 'requesting', fraction: null })
    expect(fixture.options.onActivity.mock.lastCall?.[0].message).toContain('等待服务响应')
    await act(async () => { late.resolve({ contract_version: '0.3.0', job_id: intakeJob.job_id }); await checking })
    expect(fixture.options.onActivity.mock.lastCall?.[0].phase).toBe('needs_user')
    expect(fixture.check).toHaveBeenCalledOnce()
    expect(fixture.inspect).toHaveBeenCalledOnce()
    expect(fixture.options.onSubmit).not.toHaveBeenCalled()
  })
})
