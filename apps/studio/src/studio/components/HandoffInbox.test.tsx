/** 合成收件响应验证候选发现不提交、确认移动、精确任务隔离和迟到响应 fencing。 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type {
  HandoffImportBinding, HandoffInboxConfirmEnvelope, HandoffInboxObserveEnvelope,
  HandoffInboxPreviewEnvelope, HandoffInboxPreviewRequest, HostBridge,
} from '../host-bridge'
import { HostBridgeError } from '../host-bridge'
import { HandoffInbox } from './HandoffInbox'

afterEach(() => { cleanup(); vi.useRealTimers() })
const binding: HandoffImportBinding = {
  contract_version: '0.3.0', project_session_id: 'project-A', run_id: 'run-A', node_run_id: 'node-run-A',
  handoff_id: 'handoff-A', port_id: 'video', ordinal: null,
}
const candidate = (name = 'Topaz_A.mov', handle = 'candidate-A') => ({ candidate_handle: handle, name, size: 1024, mtime_ns: 1000 })
function observation(items = [candidate()], bound = binding): HandoffInboxObserveEnvelope {
  return { ...bound, inbox_path: 'D:\\synthetic\\A\\incoming\\video', allowed_suffix: '.mov',
    candidates: items, rejected_count: 0, expires_in_seconds: 300 }
}
function preview(request: HandoffInboxPreviewRequest, replace = false): HandoffInboxPreviewEnvelope {
  return { ...binding, ...request, inbox_id: 'inbox-confirm-A', source_name: request.candidate_handle === 'candidate-B' ? 'Topaz_B.mov' : 'Topaz_A.mov',
    source_size: 1024, target_path: 'D:\\synthetic\\A\\outputs\\SYN-001.A.enhancement.mov', replace_existing: replace, action: 'move', expires_in_seconds: 300 }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
function fixture(items = [candidate()], replace = false) {
  let lastPreview = preview({ ...binding, candidate_handle: 'candidate-A' }, replace)
  const observe = vi.fn(async (request: HandoffImportBinding) => observation(items, request))
  const prepare = vi.fn(async (request: HandoffInboxPreviewRequest) => { lastPreview = preview(request, replace); return lastPreview })
  const confirm = vi.fn(async (): Promise<HandoffInboxConfirmEnvelope> => ({ ...lastPreview, status: 'collected' }))
  const launch = vi.fn(async () => undefined)
  const bridge: HostBridge = { configured: true, inspectCapabilities: vi.fn(), pick: vi.fn(), launch,
    observeHandoffInbox: observe, previewHandoffInbox: prepare, confirmHandoffInbox: confirm }
  const props = { bridge, binding, disabled: false, onCollected: vi.fn(), onBusyChange: vi.fn() }
  return { props, observe, prepare, confirm, launch }
}

describe('专属收件箱', () => {
  it('零候选只显示等待，打开目录只发送精确手动 handoff 引用', async () => {
    const value = fixture([])
    render(<HandoffInbox {...value.props} />)
    expect(await screen.findByText(/收件箱中尚无可用的 .mov 文件/)).toBeVisible()
    expect(value.prepare).not.toHaveBeenCalled()
    expect(value.confirm).not.toHaveBeenCalled()
    expect(value.props.onCollected).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '打开收件文件夹' }))
    expect(value.launch).toHaveBeenCalledExactlyOnceWith('reveal_in_file_manager', {
      kind: 'handoff', run_id: binding.run_id, node_run_id: binding.node_run_id, handoff_id: binding.handoff_id,
      selector: { role: 'incoming_directory', port_id: 'video', ordinal: null },
    })
  })

  it('一个候选也不自动选定或提交，只有两次明确意图才检查收纳', async () => {
    const value = fixture()
    render(<HandoffInbox {...value.props} />)
    const choose = await screen.findByRole('button', { name: '检查并收纳：Topaz_A.mov' })
    expect(value.prepare).not.toHaveBeenCalled()
    fireEvent.click(choose)
    const dialog = await screen.findByRole('dialog', { name: '确认收纳外部处理文件' })
    expect(within(dialog).getByText(/来件原名称将不再保留/)).toBeVisible()
    expect(value.prepare).toHaveBeenCalledExactlyOnceWith({ ...binding, candidate_handle: 'candidate-A' })
    expect(value.confirm).not.toHaveBeenCalled()
    expect(value.props.onBusyChange).toHaveBeenLastCalledWith(true)
    fireEvent.click(within(dialog).getByRole('button', { name: '确认检查并收纳' }))
    expect(await screen.findByText(/系统没有自动提交/)).toBeVisible()
    expect(value.confirm).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.0', inbox_id: 'inbox-confirm-A', overwrite: false })
    expect(value.props.onCollected).toHaveBeenCalledExactlyOnceWith()
    expect(value.props.onBusyChange).toHaveBeenLastCalledWith(false)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('多个候选必须明确选择，不猜最新、最大或第一个', async () => {
    const value = fixture([candidate(), candidate('Topaz_B.mov', 'candidate-B')])
    render(<HandoffInbox {...value.props} />)
    expect(await screen.findByText(/发现多个候选/)).toBeVisible()
    expect(value.prepare).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '检查并收纳：Topaz_B.mov' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Topaz_B.mov')).toBeVisible()
    expect(value.prepare).toHaveBeenCalledExactlyOnceWith({ ...binding, candidate_handle: 'candidate-B' })
  })

  it('已有产物必须明确勾选覆盖，每次新预览重新确认', async () => {
    const value = fixture(undefined, true)
    render(<HandoffInbox {...value.props} />)
    fireEvent.click(await screen.findByRole('button', { name: '检查并收纳：Topaz_A.mov' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: '确认检查并收纳' })).toBeDisabled()
    fireEvent.click(within(dialog).getByRole('checkbox', { name: '允许替换此任务已有产物' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    expect(value.confirm).not.toHaveBeenCalled()
    await act(async () => undefined)
    fireEvent.click(screen.getByRole('button', { name: '检查并收纳：Topaz_A.mov' }))
    const again = await screen.findByRole('dialog')
    expect(within(again).getByRole('checkbox')).not.toBeChecked()
    fireEvent.click(within(again).getByRole('checkbox'))
    fireEvent.click(within(again).getByRole('button', { name: '确认检查并收纳' }))
    await act(async () => undefined)
    expect(value.confirm).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.0', inbox_id: 'inbox-confirm-A', overwrite: true })
  })

  it('选择收纳期间停止两秒轮询，取消后恢复观察并重新取得句柄', async () => {
    vi.useFakeTimers()
    const value = fixture()
    await act(async () => { render(<HandoffInbox {...value.props} />) })
    expect(value.observe).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
    expect(value.observe).toHaveBeenCalledTimes(2)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查并收纳：Topaz_A.mov' })) })
    const before = value.observe.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000) })
    expect(value.observe).toHaveBeenCalledTimes(before)
    await act(async () => { fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消' })) })
    expect(value.observe).toHaveBeenCalledTimes(before + 1)
    expect(value.confirm).not.toHaveBeenCalled()
  })

  it('任务切换丢弃迟到预览，不将 A 票据显示给 B', async () => {
    const value = fixture()
    const pending = deferred<HandoffInboxPreviewEnvelope>()
    value.prepare.mockReturnValueOnce(pending.promise)
    const { rerender } = render(<HandoffInbox {...value.props} />)
    fireEvent.click(await screen.findByRole('button', { name: '检查并收纳：Topaz_A.mov' }))
    rerender(<HandoffInbox {...value.props} binding={{ ...binding, node_run_id: 'node-run-B', handoff_id: 'handoff-B' }} />)
    await act(async () => { pending.resolve(preview({ ...binding, candidate_handle: 'candidate-A' })) })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(value.confirm).not.toHaveBeenCalled()
    expect(value.props.onBusyChange).toHaveBeenLastCalledWith(false)
  })

  it('已确认收纳不允许重复点击或 Escape 取消，卸载后回执不推进新任务', async () => {
    const value = fixture()
    const pending = deferred<HandoffInboxConfirmEnvelope>()
    value.confirm.mockReturnValueOnce(pending.promise)
    const { unmount } = render(<HandoffInbox {...value.props} />)
    fireEvent.click(await screen.findByRole('button', { name: '检查并收纳：Topaz_A.mov' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '确认检查并收纳' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '正在检查与收纳…' }))
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(value.confirm).toHaveBeenCalledTimes(1)
    unmount()
    expect(value.props.onBusyChange).toHaveBeenLastCalledWith(true)
    await act(async () => { pending.resolve({ ...preview({ ...binding, candidate_handle: 'candidate-A' }), status: 'collected' }) })
    expect(value.props.onBusyChange).toHaveBeenLastCalledWith(false)
    expect(value.props.onCollected).not.toHaveBeenCalled()
  })

  it('拒绝错任务响应，并将原始详情折叠保留', async () => {
    const value = fixture()
    value.prepare.mockImplementationOnce(async (request) => ({ ...preview(request), node_run_id: 'wrong-task' }))
    render(<HandoffInbox {...value.props} />)
    fireEvent.click(await screen.findByRole('button', { name: '检查并收纳：Topaz_A.mov' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('收件操作未完成')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('收纳预览不属于当前任务与所选来件。')).not.toBeVisible()
    expect(value.confirm).not.toHaveBeenCalled()
  })

  it('文件变化失败明确提示重新检查且不自动重试副作用', async () => {
    const value = fixture()
    value.confirm.mockRejectedValueOnce(new HostBridgeError('E_HANDOFF_INBOX_CHANGED: synthetic changed', { code: 'E_HANDOFF_INBOX_CHANGED' }))
    render(<HandoffInbox {...value.props} />)
    fireEvent.click(await screen.findByRole('button', { name: '检查并收纳：Topaz_A.mov' }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '确认检查并收纳' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('文件仍在写入或已经被替换')
    expect(value.confirm).toHaveBeenCalledTimes(1)
    expect(value.props.onCollected).not.toHaveBeenCalled()
    expect(value.props.onBusyChange).toHaveBeenLastCalledWith(false)
  })

  it('缺少能力或外部禁用时不轮询、不打开目录、不选择来件', async () => {
    const value = fixture()
    const { rerender } = render(<HandoffInbox {...value.props} disabled />)
    await act(async () => undefined)
    expect(value.observe).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '打开收件文件夹' })).toBeDisabled()
    rerender(<HandoffInbox {...value.props} bridge={{ ...value.props.bridge, observeHandoffInbox: undefined }} />)
    expect(screen.getByText(/当前宿主尚未提供收件箱能力/)).toBeVisible()
    expect(value.observe).not.toHaveBeenCalled()
    expect(value.launch).not.toHaveBeenCalled()
  })
})
