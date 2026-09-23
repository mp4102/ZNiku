/** 维护 UI 只用合成路径与延迟回执验证显式确认、部分失败及会话切换；不操作文件系统。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ProjectScratchMaintenance } from './ProjectScratchMaintenance'
import type { HostBridge } from '../host-bridge'
import type { ScratchConfirmEnvelope, ScratchPreviewEnvelope } from '../storage-scratch-contracts'

afterEach(() => { cleanup(); vi.useRealTimers() })
const binding = { contract_version: '0.3.0' as const, project_session_id: 'synthetic-session', expected_storage_revision: 3 }
const preview: ScratchPreviewEnvelope = { ...binding, ticket_id: 'synthetic-ticket', expires_in_seconds: 300, truncated: false, warnings: [],
  summary: [{ category: 'internal_scratch', file_count: 1, byte_count: 1024 ** 3 }, { category: 'archive', file_count: 1, byte_count: 2 * 1024 ** 3 }],
  entries: [
    { path: 'D:/synthetic/context/part.mov', category: 'internal_scratch', byte_count: 1024 ** 3, node_id: 'context', node_run_id: 'context-attempt',
      attempt: 1, task_label: 'FI 上下文准备', chapter_label: 'A', round_label: 'round-001', role: '内部范围中转', reason: '未登记且没有正式引用', candidate_id: 'scratch-1' },
    { path: 'D:/synthetic/fi/raw.mov', category: 'archive', byte_count: 2 * 1024 ** 3, node_id: 'fi', node_run_id: 'fi-attempt', attempt: 1,
      task_label: '外部补帧', chapter_label: 'A', round_label: 'round-001', role: '人工外部成果', reason: '已登记成果必须保留', candidate_id: null },
  ] }
const result: ScratchConfirmEnvelope = { ...binding, ticket_id: preview.ticket_id, entries: [{ candidate_id: 'scratch-1', path: preview.entries[0]!.path,
  byte_count: 1024 ** 3, status: 'deleted', message: '已删除受控中转' }], deleted_bytes: 1024 ** 3, deletion_count: 1, complete: true, warnings: [] }
function props() {
  const bridge = { previewStorageScratch: vi.fn().mockResolvedValue(preview), confirmStorageScratch: vi.fn().mockResolvedValue(result) } as unknown as HostBridge
  return { bridge, projectSessionId: binding.project_session_id, storageRevision: 3, disabled: false,
    perform: vi.fn(async (_label: string, action: (generation: number) => Promise<void>) => action(1)) }
}
async function scan() {
  fireEvent.click(screen.getByRole('button', { name: '扫描占用并预览内部中转' }))
  await screen.findByText('已选择 0 项，待清理逻辑大小 0.00 MiB。默认不选择任何文件。')
}
describe('内部中转维护', () => {
  it('挂载不扫描、默认不选；只有选候选并确认不可恢复才提交句柄', async () => {
    const value = props()
    render(<ProjectScratchMaintenance {...value} />)
    expect(value.bridge.previewStorageScratch).not.toHaveBeenCalled()
    await scan()
    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByRole('button', { name: '确认删除所选中转' })).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择此内部中转' }))
    expect(screen.getByRole('button', { name: '确认删除所选中转' })).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认只删除/ }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除所选中转' }))
    await screen.findByText(/成功删除 1 项/)
    expect(value.bridge.confirmStorageScratch).toHaveBeenCalledExactlyOnceWith({ ...binding, ticket_id: preview.ticket_id,
      candidate_ids: ['scratch-1'], confirm_irreversible: true })
    expect(screen.queryByRole('button', { name: '确认删除所选中转' })).not.toBeInTheDocument()
  })
  it('取消不删除，重新选择会取消不可恢复确认', async () => {
    const value = props(); render(<ProjectScratchMaintenance {...value} />); await scan()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择此内部中转' }))
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认只删除/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: '选择此内部中转' }))
    fireEvent.click(screen.getByRole('checkbox', { name: '选择此内部中转' }))
    expect(screen.getByRole('checkbox', { name: /我确认只删除/ })).not.toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '取消本次清理' }))
    expect(value.bridge.confirmStorageScratch).not.toHaveBeenCalled()
  })
  it('网络断开不自动重放删除，不保留已消费票据的确认按钮', async () => {
    const value = props(); vi.mocked(value.bridge.confirmStorageScratch!).mockRejectedValue(new Error('Failed to fetch'))
    render(<ProjectScratchMaintenance {...value} />); await scan()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择此内部中转' }))
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认只删除/ }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除所选中转' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('清理结果待确认')
    expect(value.bridge.confirmStorageScratch).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: '确认删除所选中转' })).not.toBeInTheDocument()
  })
  it('部分失败只报告实际成功量，不声称 NAS 已物理释放', async () => {
    const value = props(); vi.mocked(value.bridge.confirmStorageScratch!).mockResolvedValue({ ...result, complete: false, deletion_count: 0,
      deleted_bytes: 0, entries: [{ ...result.entries[0]!, status: 'skipped', message: '确认前文件变化，保留' }] })
    render(<ProjectScratchMaintenance {...value} />); await scan()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择此内部中转' }))
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认只删除/ }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除所选中转' }))
    expect(await screen.findByText(/本次清理有跳过或失败项/)).toHaveTextContent('成功删除 0 项')
    expect(screen.getByText(/已跳过 ·/)).toHaveTextContent('确认前文件变化，保留')
  })
  it('迟到扫描不进入另一个工程，禁用状态不触发维护', async () => {
    const value = props(); let resolve!: (value: ScratchPreviewEnvelope) => void
    vi.mocked(value.bridge.previewStorageScratch!).mockReturnValue(new Promise((done) => { resolve = done }))
    const { rerender } = render(<ProjectScratchMaintenance {...value} />)
    fireEvent.click(screen.getByRole('button', { name: '扫描占用并预览内部中转' }))
    rerender(<ProjectScratchMaintenance {...value} projectSessionId="different-session" disabled />)
    await act(async () => { resolve(preview) })
    expect(screen.queryByText('未登记且没有正式引用')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '扫描占用并预览内部中转' })).toBeDisabled()
  })
  it('票据到期清除选择，不自动再扫描或删除', async () => {
    const value = props(); vi.mocked(value.bridge.previewStorageScratch!).mockResolvedValue({ ...preview, expires_in_seconds: 0.02 })
    render(<ProjectScratchMaintenance {...value} />); await scan()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('维护预览已过期'))
    expect(value.bridge.previewStorageScratch).toHaveBeenCalledTimes(1)
    expect(value.bridge.confirmStorageScratch).not.toHaveBeenCalled()
  })
})
