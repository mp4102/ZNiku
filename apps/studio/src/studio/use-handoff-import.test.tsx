/** 人工导入仅复制到确认的唯一任务；取消、换选区、迟到响应与重复点击均不得误投或自动 Submit。 */
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { HostBridge, HandoffImportPreviewEnvelope, HandoffImportPreviewRequest, HandoffImportEnvelope } from './host-bridge'
import { HostBridgeError } from './host-bridge'
import { handoffDetailEnvelope } from './test-fixtures'
import { useHandoffImport } from './use-handoff-import'

afterEach(cleanup)
const nodeRun = handoffDetailEnvelope().run.node_runs.find((item) => item.state === 'waiting_external')!
const target = nodeRun.external_handoff!.output_targets[0]!
const preview = (request: HandoffImportPreviewRequest, replace = false): HandoffImportPreviewEnvelope => ({
  ...request, import_id: 'import-a', source_name: 'finished-A.mkv', source_size: 1024,
  target_path: target.path, replace_existing: replace, expires_in_seconds: 300,
})
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
function setup(replace = false) {
  let currentPreview: HandoffImportPreviewEnvelope
  const pick = vi.fn(async () => [{ selection_handle: 'selected-a', path: 'D:\\external\\finished-A.mkv' }])
  const previewImport = vi.fn(async (request: HandoffImportPreviewRequest) => { currentPreview = preview(request, replace); return currentPreview })
  const confirmImport = vi.fn(async (): Promise<HandoffImportEnvelope> => ({ ...currentPreview, status: 'imported' }))
  const host: HostBridge = { configured: true, inspectCapabilities: vi.fn(), pick, launch: vi.fn(),
    previewHandoffImport: previewImport, confirmHandoffImport: confirmImport }
  const options = { hostBridge: host, projectSessionId: 'project-a', scope: 'project-a/run-a/node-a', operationRef: { current: null as symbol | null },
    canStart: () => true, isCurrent: () => true, onBusyChange: vi.fn(), onImportStarted: vi.fn(), onImported: vi.fn(async () => undefined) }
  return { options, pick, previewImport, confirmImport }
}

describe('外部处理文件导入 session', () => {
  it('原生窗口忙碌明确提示完成已有窗口，保留原文且不当作媒体要求失败或自动重试', async () => {
    const test = setup()
    const rawMessage = 'E_HOST_BRIDGE_DIALOG_BUSY: synthetic native chooser is active'
    test.pick.mockRejectedValueOnce(new HostBridgeError(rawMessage, { code: 'E_HOST_BRIDGE_DIALOG_BUSY', httpStatus: 409 }))
    const { result } = renderHook(() => useHandoffImport(test.options))
    await act(() => result.current.choose(nodeRun, target, '画质增强（2）'))
    expect(result.current.error).toContain('已有文件/文件夹选择窗口打开，请先完成或取消；它可能在浏览器后面。')
    expect(result.current.error).toContain('当前任务“画质增强（2）”')
    expect(result.current.error).not.toContain('检查所选文件与任务要求')
    expect(result.current.rawError).toBe(rawMessage)
    expect(result.current.busy).toBe(false)
    expect(test.options.operationRef.current).toBeNull()
    expect(test.pick).toHaveBeenCalledTimes(1)
    expect(test.previewImport).not.toHaveBeenCalled()
    expect(test.confirmImport).not.toHaveBeenCalled()
    expect(test.options.onImportStarted).not.toHaveBeenCalled()
  })

  it('picker/预览精确绑定 nodeRun/handoff/port，只有确认后复制；成功清旧检查并重新观察', async () => {
    const test = setup()
    const { result } = renderHook(() => useHandoffImport(test.options))
    await act(() => result.current.choose(nodeRun, target, '画质增强（1）'))
    expect(test.pick).toHaveBeenCalledExactlyOnceWith('open_file', { title: '选择处理好的文件 · 画质增强（1）' })
    expect(test.previewImport).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.0', selection_handle: 'selected-a',
      project_session_id: 'project-a', run_id: nodeRun.run_id, node_run_id: nodeRun.node_run_id,
      handoff_id: nodeRun.external_handoff!.handoff_id, port_id: target.port_id, ordinal: target.ordinal })
    expect(test.confirmImport).not.toHaveBeenCalled()
    expect(result.current.preview?.sourcePath).toBe('D:\\external\\finished-A.mkv')
    expect(result.current.busy).toBe(true)
    await act(() => result.current.confirm(false))
    expect(test.confirmImport).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.0', import_id: 'import-a', overwrite: false })
    expect(test.options.onImportStarted).toHaveBeenCalledExactlyOnceWith(nodeRun)
    expect(test.options.onImported).toHaveBeenCalledExactlyOnceWith(nodeRun)
    expect(result.current.message).toContain('尚未提交')
    expect(result.current.busy).toBe(false)
    expect(test.options.operationRef.current).toBeNull()
  })

  it('取消预览不复制，已有目标要求独立勾选才能确认', async () => {
    const test = setup(true)
    const { result } = renderHook(() => useHandoffImport(test.options))
    await act(() => result.current.choose(nodeRun, target, '画质增强（1）'))
    await act(() => result.current.confirm(false))
    expect(test.confirmImport).not.toHaveBeenCalled()
    act(() => result.current.cancel())
    expect(result.current.preview).toBeNull()
    expect(result.current.busy).toBe(false)
    await act(() => result.current.choose(nodeRun, target, '画质增强（1）'))
    await act(() => result.current.confirm(true))
    expect(test.confirmImport).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.0', import_id: 'import-a', overwrite: true })
  })

  it('原生选择取消不请求预览、复制或清除既有检查', async () => {
    const test = setup()
    test.pick.mockResolvedValueOnce([])
    const { result } = renderHook(() => useHandoffImport(test.options))
    await act(() => result.current.choose(nodeRun, target, '画质增强（1）'))
    expect(test.previewImport).not.toHaveBeenCalled()
    expect(test.confirmImport).not.toHaveBeenCalled()
    expect(test.options.onImportStarted).not.toHaveBeenCalled()
    expect(result.current.busy).toBe(false)
  })

  it.each(['picker', 'preview'] as const)('切换 selection/project/run 后迟到的 %s 响应不能进入另一个任务', async (phase) => {
    const test = setup()
    const selected = deferred<Awaited<ReturnType<typeof test.pick>>>()
    const pendingPreview = deferred<HandoffImportPreviewEnvelope>()
    if (phase === 'picker') test.pick.mockReturnValueOnce(selected.promise)
    else test.previewImport.mockReturnValueOnce(pendingPreview.promise)
    const { result, rerender } = renderHook((options) => useHandoffImport(options), { initialProps: test.options })
    let choosing!: Promise<void>
    await act(async () => { choosing = result.current.choose(nodeRun, target, '画质增强（1）'); await Promise.resolve() })
    rerender({ ...test.options, projectSessionId: 'project-b', scope: 'project-b/run-b/node-b' })
    await act(async () => {
      if (phase === 'picker') selected.resolve([{ selection_handle: 'selected-a', path: 'D:\\old-A.mkv' }])
      else pendingPreview.resolve(preview(test.previewImport.mock.calls[0]![0]))
      await choosing
    })
    expect(result.current.preview).toBeNull()
    expect(result.current.message).toBeNull()
    expect(result.current.busy).toBe(false)
    expect(test.confirmImport).not.toHaveBeenCalled()
    if (phase === 'picker') expect(test.previewImport).not.toHaveBeenCalled()
  })

  it('错任务预览失败关闭；不显示可确认目标、不复制', async () => {
    const test = setup()
    test.previewImport.mockImplementationOnce(async (request) => ({ ...preview(request), node_run_id: 'node-run-B' }))
    const { result } = renderHook(() => useHandoffImport(test.options))
    await act(() => result.current.choose(nodeRun, target, '画质增强（1）'))
    expect(result.current.preview).toBeNull()
    expect(result.current.error).toContain('当前任务“画质增强（1）”')
    expect(test.confirmImport).not.toHaveBeenCalled()
    expect(result.current.busy).toBe(false)
  })

  it('同一 nodeRun/handoff 在其他标签变为终态时主动释放预览，隐藏助手不会留下无法取消的锁', async () => {
    const test = setup()
    const { result, rerender } = renderHook((options) => useHandoffImport(options), { initialProps: test.options })
    await act(() => result.current.choose(nodeRun, target, '画质增强（1）'))
    expect(result.current.preview).not.toBeNull()
    // 即使外部只改变正式状态、不改变 ID 或 scope，最新资格检查也必须关闭旧 preview。
    rerender({ ...test.options, isCurrent: () => false })
    expect(result.current.preview).toBeNull()
    expect(result.current.busy).toBe(false)
    expect(test.options.operationRef.current).toBeNull()
    await act(() => result.current.confirm(false))
    expect(test.confirmImport).not.toHaveBeenCalled()
  })

  it('复制在途拒绝重复确认；切选区不能重定向回执，也不能提前释放文件操作互斥', async () => {
    const test = setup()
    const copying = deferred<HandoffImportEnvelope>()
    test.confirmImport.mockReturnValueOnce(copying.promise)
    const { result, rerender } = renderHook((options) => useHandoffImport(options), { initialProps: test.options })
    await act(() => result.current.choose(nodeRun, target, '画质增强（1）'))
    const confirmed = result.current.preview!.envelope
    let first!: Promise<void>
    await act(async () => { first = result.current.confirm(false); await Promise.resolve() })
    await act(() => result.current.confirm(false))
    act(() => result.current.cancel())
    expect(result.current.copying).toBe(true)
    rerender({ ...test.options, scope: 'project-a/run-a/node-b' })
    expect(result.current.preview).toBeNull()
    expect(result.current.busy).toBe(true)
    await act(async () => { copying.resolve({ ...confirmed, status: 'imported' }); await first })
    expect(test.confirmImport).toHaveBeenCalledTimes(1)
    expect(test.options.onImported).not.toHaveBeenCalled()
    expect(result.current.message).toBeNull()
    expect(result.current.busy).toBe(false)
  })

  it('复制失败只绑定当前任务，保留错误并要求重新选择或检查', async () => {
    const test = setup()
    test.confirmImport.mockRejectedValueOnce(new Error('E_SYNTHETIC_IMPORT_INVALID'))
    const { result } = renderHook(() => useHandoffImport(test.options))
    await act(() => result.current.choose(nodeRun, target, '画质增强（2）'))
    await act(() => result.current.confirm(false))
    expect(result.current.error).toContain('当前任务“画质增强（2）”')
    expect(result.current.error).toContain('没有自动提交')
    expect(result.current.preview).toBeNull()
    expect(test.options.onImported).not.toHaveBeenCalled()
    expect(result.current.busy).toBe(false)
  })

  it('复制中切换scope并失去正式资格时只清一次视图，不因每render的新predicate产生渲染循环', async () => {
    const test = setup()
    const copying = deferred<HandoffImportEnvelope>()
    test.confirmImport.mockReturnValueOnce(copying.promise)
    let renders = 0
    const { result, rerender } = renderHook(({ scope, allowed }) => {
      renders += 1
      return useHandoffImport({ ...test.options, scope, isCurrent: () => allowed })
    }, { initialProps: { scope: 'A', allowed: true } })
    await act(() => result.current.choose(nodeRun, target, '画质增强（1）'))
    const confirmed = result.current.preview!.envelope
    let first!: Promise<void>
    await act(async () => { first = result.current.confirm(false); await Promise.resolve() })
    const before = renders
    rerender({ scope: 'B', allowed: false })
    expect(renders - before).toBeLessThan(5)
    expect(result.current.busy).toBe(true)
    expect(result.current.preview).toBeNull()
    expect(test.options.onBusyChange).toHaveBeenCalledExactlyOnceWith(true)
    await act(async () => { copying.resolve({ ...confirmed, status: 'imported' }); await first })
    expect(result.current.busy).toBe(false)
    expect(test.options.onImported).not.toHaveBeenCalled()
  })
})
