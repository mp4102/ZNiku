/** 章级交接异步状态机不串工程、不自动Submit、不把取消当作复制中止。 */
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { HostBridge } from './host-bridge'
import type { HandoffBatchCheckEnvelope, HandoffBatchConfirmEnvelope, HandoffBatchConfirmRequest, HandoffBatchPreviewEnvelope } from './handoff-batch-contracts'
import { batchBinding, batchNodeRun, batchObservation, batchPreview, batchReadiness } from './handoff-batch-fixtures'
import { useHandoffBatch } from './use-handoff-batch'

afterEach(cleanup)
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
const items = batchPreview.matches.map((match) => ({ port_id: match.port_id, candidate_handle: match.candidate_handle!, overwrite: false }))
function setup() {
  let observation = batchObservation
  const pick = vi.fn(async () => [{ selection_handle: 'synthetic-selection', path: 'D:/synthetic/source.mov' }])
  const preview = vi.fn(async (): Promise<HandoffBatchPreviewEnvelope> => ({ ...batchPreview, ...observation }))
  const confirm = vi.fn(async (request: HandoffBatchConfirmRequest): Promise<HandoffBatchConfirmEnvelope> => {
    const rows = observation.rows.map((row) => request.items.some((item) => item.port_id === row.port_id) ? { ...row, collected: true, size: 1000 } : row)
    observation = { ...observation, rows, complete: rows.every((row) => row.collected) }
    return { ...observation, batch_id: batchPreview.batch_id, results: request.items.map((item) => ({ port_id: item.port_id, status: 'collected', message: null })) }
  })
  const check = vi.fn(async (): Promise<HandoffBatchCheckEnvelope> => ({ ...observation, published: true, validation_error: null, readiness: batchReadiness }))
  const bridge: HostBridge = { configured: true, inspectCapabilities: vi.fn(), launch: vi.fn(), pick,
    observeHandoffBatch: vi.fn(async () => observation), previewHandoffBatch: preview, confirmHandoffBatch: confirm, checkHandoffBatch: check }
  const options = { hostBridge: bridge, projectSessionId: batchBinding.project_session_id, nodeRun: batchNodeRun,
    scope: 'first', operationRef: { current: null as symbol | null }, canStart: () => true, isCurrent: () => true,
    onBusyChange: vi.fn(), onChanged: vi.fn(), onChecked: vi.fn() }
  return { options, pick, preview, confirm, check }
}

describe('章级收件交互状态机', () => {
  it('多文件选择→部分收件→补齐→完整检查，任何阶段均不包含自动Submit', async () => {
    const fixture = setup(), { result } = renderHook(() => useHandoffBatch(fixture.options))
    await waitFor(() => expect(result.current.observation).not.toBeNull())
    await act(() => result.current.choose('open_files'))
    expect(fixture.pick).toHaveBeenCalledWith('open_files', expect.anything())
    expect(fixture.confirm).not.toHaveBeenCalled()
    await act(() => result.current.confirm(items.slice(0, 1)))
    expect(result.current.observation?.complete).toBe(false)
    await act(() => result.current.check([]))
    expect(fixture.check).not.toHaveBeenCalled()
    await act(() => result.current.choose('select_directory'))
    await act(() => result.current.confirm(items.slice(1)))
    expect(result.current.observation?.complete).toBe(true)
    expect(fixture.options.onChecked).not.toHaveBeenCalled()
    await act(() => result.current.check([]))
    expect(fixture.options.onChecked).toHaveBeenCalledExactlyOnceWith(batchNodeRun, batchReadiness)
    expect(result.current.message).toContain('等待你显式提交')
  })
  it('picker取消与预览取消都不复制，不撤销既有检查', async () => {
    const fixture = setup(), { result } = renderHook(() => useHandoffBatch(fixture.options))
    fixture.pick.mockResolvedValueOnce([])
    await act(() => result.current.choose('open_files'))
    expect(fixture.preview).not.toHaveBeenCalled()
    await act(() => result.current.choose('inbox'))
    act(() => result.current.cancel())
    expect(fixture.confirm).not.toHaveBeenCalled()
    expect(fixture.options.onChanged).not.toHaveBeenCalled()
    expect(fixture.options.operationRef.current).toBeNull()
  })
  it('切换工程后迟到预览不会显示或允许确认', async () => {
    const fixture = setup(), late = deferred<HandoffBatchPreviewEnvelope>()
    fixture.preview.mockReturnValueOnce(late.promise)
    const { result, rerender } = renderHook((options) => useHandoffBatch(options), { initialProps: fixture.options })
    let choosing!: Promise<void>
    await act(async () => { choosing = result.current.choose('inbox'); await Promise.resolve() })
    rerender({ ...fixture.options, scope: 'other-project' })
    await act(async () => { late.resolve(batchPreview); await choosing })
    expect(result.current.preview).toBeNull()
    await act(() => result.current.confirm(items))
    expect(fixture.confirm).not.toHaveBeenCalled()
  })
  it('复制已经开始时取消无效，切换只隐藏旧回执且锁保持到返回', async () => {
    const fixture = setup(), late = deferred<HandoffBatchConfirmEnvelope>()
    fixture.confirm.mockReturnValueOnce(late.promise)
    const { result, rerender } = renderHook((options) => useHandoffBatch(options), { initialProps: fixture.options })
    await act(() => result.current.choose('inbox'))
    let copying!: Promise<void>
    await act(async () => { copying = result.current.confirm(items); await Promise.resolve() })
    act(() => result.current.cancel())
    expect(fixture.options.operationRef.current).not.toBeNull()
    rerender({ ...fixture.options, scope: 'other-node' })
    expect(result.current.preview).toBeNull()
    await act(async () => { late.resolve({ ...batchObservation, batch_id: batchPreview.batch_id, results: [] }); await copying })
    expect(fixture.options.operationRef.current).toBeNull()
    expect(fixture.options.onChecked).not.toHaveBeenCalled()
  })
  it('预览错目标失败关闭；完整检查失败只保留等待提示', async () => {
    const fixture = setup(), { result } = renderHook(() => useHandoffBatch(fixture.options))
    fixture.preview.mockResolvedValueOnce({ ...batchPreview, rows: batchPreview.rows.map((row) => ({ ...row, target_path: 'D:/wrong/output.mov' })) })
    await act(() => result.current.choose('inbox'))
    expect(result.current.error).toContain('没有复制')
    await act(() => result.current.choose('inbox'))
    await act(() => result.current.confirm(items))
    fixture.check.mockResolvedValueOnce({ ...batchObservation, rows: batchObservation.rows.map((row) => ({ ...row, collected: true, size: 1000 })), complete: true,
      published: false, validation_error: { code: 'E_SYNTHETIC_FRAME_COUNT', message: 'Wrong N' }, readiness: null })
    await act(() => result.current.check([]))
    expect(result.current.error).toContain('整章检查未通过')
    expect(result.current.observation?.complete).toBe(true)
    expect(fixture.options.onChecked).not.toHaveBeenCalled()
  })
  it('检查迟到且当前节点失效时不授予Submit资格', async () => {
    const fixture = setup(), late = deferred<HandoffBatchCheckEnvelope>()
    const { result, rerender } = renderHook((options) => useHandoffBatch(options), { initialProps: fixture.options })
    await act(() => result.current.choose('inbox'))
    await act(() => result.current.confirm(items))
    fixture.check.mockReturnValueOnce(late.promise)
    let checking!: Promise<void>
    await act(async () => { checking = result.current.check([]); await Promise.resolve() })
    rerender({ ...fixture.options, isCurrent: () => false })
    await act(async () => { late.resolve({ ...batchObservation, published: true, validation_error: null, readiness: batchReadiness }); await checking })
    expect(fixture.options.onChecked).not.toHaveBeenCalled()
  })
  it('检查期间A→B→A仍永久丢弃旧回执，不能因身份重新相同而恢复提交资格', async () => {
    const fixture = setup(), late = deferred<HandoffBatchCheckEnvelope>()
    const { result, rerender } = renderHook((options) => useHandoffBatch(options), { initialProps: fixture.options })
    await act(() => result.current.choose('inbox'))
    await act(() => result.current.confirm(items))
    const observation = result.current.observation!
    fixture.check.mockReturnValueOnce(late.promise)
    let checking!: Promise<void>
    await act(async () => { checking = result.current.check([]); await Promise.resolve() })
    rerender({ ...fixture.options, scope: 'B' })
    rerender(fixture.options)
    await act(async () => { late.resolve({ ...observation, published: true, validation_error: null, readiness: batchReadiness }); await checking })
    expect(fixture.options.onChecked).not.toHaveBeenCalled()
    expect(fixture.options.operationRef.current).toBeNull()
  })
})
