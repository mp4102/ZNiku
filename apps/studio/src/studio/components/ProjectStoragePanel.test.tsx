/** 合成 HostBridge 测试证明工程数据不会因打开、选择、取消或过期响应被悄悄迁移。 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HostBridgeError, type HostBridge, type StorageInspection, type StorageMigrationPreview } from '../host-bridge'
import { ProjectStoragePanel } from './ProjectStoragePanel'

afterEach(cleanup)

function inspection(attempts = 2): StorageInspection {
  return {
    contract_version: '0.3.0',
    storage: { contract_version: '0.3.0', mode: 'legacy', data_root: 'C:\\synthetic', attempts_root: 'C:\\synthetic\\attempts', retention: 'keep', media_basename: null },
    configured: false, attempt_count: attempts, registered_file_count: 2, registered_bytes: 1024 ** 3,
    managed_file_count: 1, managed_bytes: 512, missing: [],
    external_dependencies: [{ path: 'D:\\synthetic-source.dat', artifact_ids: ['source-id'], state: 'present' }],
    coverage: 'registered_artifacts', warnings: ['此检查只覆盖已登记文件，不是完整离线归档。'],
  }
}

function moved(attempts = 2): StorageInspection {
  return { ...inspection(attempts), configured: true, storage: { ...inspection().storage, mode: 'custom', data_root: 'E:\\archive\\synthetic.data', attempts_root: 'E:\\archive\\synthetic.data\\attempts' } }
}

function preview(): StorageMigrationPreview {
  return {
    contract_version: '0.3.0', ticket_id: 'synthetic-ticket', project_session_id: 'session-1', expected_storage_revision: 4,
    source: inspection().storage, target: moved().storage, attempt_count: 2, file_count: 7, byte_count: 1024 ** 3,
    external_dependencies: inspection().external_dependencies, originals_retained: true,
  }
}

function bridge(attempts = 2) {
  return {
    configured: true,
    inspectCapabilities: vi.fn(async () => ({ contract_version: '0.3.0' as const, capabilities: [] })),
    pick: vi.fn<HostBridge['pick']>(async () => [{ selection_handle: 'selected-directory', path: 'E:\\archive' }]),
    launch: vi.fn<HostBridge['launch']>(async () => undefined),
    inspectStorage: vi.fn(async (_session: string) => inspection(attempts)),
    configureStorage: vi.fn<NonNullable<HostBridge['configureStorage']>>(async () => moved(attempts)),
    previewStorageMigration: vi.fn<NonNullable<HostBridge['previewStorageMigration']>>(async () => preview()),
    confirmStorageMigration: vi.fn<NonNullable<HostBridge['confirmStorageMigration']>>(async () => moved(attempts)),
  } satisfies HostBridge
}

async function show(value = bridge(), callbacks = { onChanged: vi.fn(), onClose: vi.fn(), onBusyChange: vi.fn() }) {
  const result = render(<ProjectStoragePanel bridge={value} projectSessionId="session-1" storageRevision={4} {...callbacks} />)
  await screen.findByText('旧版工作目录：保留原位置，尚未迁移')
  return { ...result, value, callbacks }
}

describe('工程数据位置与显式迁移', () => {
  it('打开只检查，显示长期保留与外部依赖，不将工作资产宣称为完整归档', async () => {
    const { value } = await show()
    expect(value.inspectStorage).toHaveBeenCalledWith('session-1')
    expect(screen.getByText('工程资产长期保留。')).toBeInTheDocument()
    expect(screen.getByText('D:\\synthetic-source.dat')).toBeInTheDocument()
    expect(screen.getByText('此检查只覆盖已登记文件，不是完整离线归档。')).toBeInTheDocument()
    expect(value.pick).not.toHaveBeenCalled()
    expect(value.configureStorage).not.toHaveBeenCalled()
    expect(value.previewStorageMigration).not.toHaveBeenCalled()
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it('原生选择取消不改变配置或创建迁移', async () => {
    const value = bridge(); value.pick.mockResolvedValue(null)
    await show(value)
    fireEvent.click(screen.getByRole('button', { name: '选择数据父目录' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '选择数据父目录' })).toBeEnabled())
    expect(value.pick).toHaveBeenCalledWith('select_directory', { title: '选择工程数据父文件夹' })
    expect(value.previewStorageMigration).not.toHaveBeenCalled()
    expect(value.configureStorage).not.toHaveBeenCalled()
  })

  it('没有attempt时只在明确保存后写配置，不触发迁移', async () => {
    const { value, callbacks } = await show(bridge(0))
    fireEvent.click(screen.getByRole('button', { name: '选择数据父目录' }))
    await screen.findByText('待设置位置：E:\\archive')
    expect(value.configureStorage).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '保存数据位置' }))
    await screen.findByText('新的工程数据位置已保存；节点图和参数没有改动。')
    expect(value.configureStorage).toHaveBeenCalledWith({ project_session_id: 'session-1', expected_storage_revision: 4, selection_handle: 'selected-directory' })
    expect(value.previewStorageMigration).not.toHaveBeenCalled()
    expect(callbacks.onChanged).toHaveBeenCalledTimes(1)
  })

  it('恢复工程旁默认只传null选择绑定，不在浏览器拼路径', async () => {
    const { value } = await show(bridge(0))
    fireEvent.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    expect(value.configureStorage).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '保存数据位置' }))
    await waitFor(() => expect(value.configureStorage).toHaveBeenCalledWith({ project_session_id: 'session-1', expected_storage_revision: 4, selection_handle: null }))
  })

  it('已有attempt先只读预览，明确确认后才复制迁移，保留原件清晰可见', async () => {
    const { value, callbacks } = await show()
    fireEvent.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    fireEvent.click(screen.getByRole('button', { name: '预览迁移到新位置' }))
    const review = await screen.findByRole('region', { name: '确认数据迁移' })
    expect(review).toHaveTextContent('7 个文件')
    expect(review).toHaveTextContent('全部原件保留')
    expect(value.configureStorage).not.toHaveBeenCalled()
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
    fireEvent.click(within(review).getByRole('button', { name: '确认复制迁移并保留原件' }))
    await waitFor(() => expect(callbacks.onChanged).toHaveBeenCalledTimes(1))
    expect(value.confirmStorageMigration).toHaveBeenCalledWith({ project_session_id: 'session-1', expected_storage_revision: 4, ticket_id: 'synthetic-ticket' })
    expect(screen.getByText(/工程数据迁移完成/)).toBeInTheDocument()
  })

  it('取消迁移预览不确认、不删除', async () => {
    const { value } = await show()
    fireEvent.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    fireEvent.click(screen.getByRole('button', { name: '预览迁移到新位置' }))
    await screen.findByRole('region', { name: '确认数据迁移' })
    fireEvent.click(screen.getByRole('button', { name: '取消本次迁移' }))
    expect(screen.queryByRole('region', { name: '确认数据迁移' })).not.toBeInTheDocument()
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it('迁移期间禁止关闭和重复确认，提示复制比对不是完成', async () => {
    const value = bridge()
    let finish!: (result: StorageInspection) => void
    value.confirmStorageMigration.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const { callbacks } = await show(value)
    fireEvent.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    fireEvent.click(screen.getByRole('button', { name: '预览迁移到新位置' }))
    const confirm = await screen.findByRole('button', { name: '确认复制迁移并保留原件' })
    fireEvent.click(confirm); fireEvent.click(confirm)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: '关闭工程数据与归档检查' }))
    expect(callbacks.onClose).not.toHaveBeenCalled()
    expect(value.confirmStorageMigration).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/正在复制和逐文件比对/)).toBeInTheDocument()
    expect(callbacks.onChanged).not.toHaveBeenCalled()
    await act(async () => finish(moved()))
    expect(callbacks.onChanged).toHaveBeenCalledTimes(1)
    expect(callbacks.onBusyChange).toHaveBeenLastCalledWith(false)
  })

  it('工程revision变化后丢弃旧迁移预览，不能继续确认', async () => {
    const { value, rerender, callbacks } = await show()
    fireEvent.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    fireEvent.click(screen.getByRole('button', { name: '预览迁移到新位置' }))
    await screen.findByRole('region', { name: '确认数据迁移' })
    rerender(<ProjectStoragePanel bridge={value} projectSessionId="session-1" storageRevision={5} {...callbacks} />)
    await screen.findByText('旧版工作目录：保留原位置，尚未迁移')
    expect(screen.queryByRole('button', { name: '确认复制迁移并保留原件' })).not.toBeInTheDocument()
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it('迟到的旧会话读取不会覆盖新工程信息', async () => {
    const value = bridge()
    let late!: (result: StorageInspection) => void
    value.inspectStorage.mockImplementationOnce(() => new Promise((resolve) => { late = resolve }))
    const callbacks = { onChanged: vi.fn(), onClose: vi.fn() }
    const result = render(<ProjectStoragePanel bridge={value} projectSessionId="old-session" storageRevision={4} {...callbacks} />)
    result.rerender(<ProjectStoragePanel bridge={value} projectSessionId="new-session" storageRevision={4} {...callbacks} />)
    await screen.findByText('旧版工作目录：保留原位置，尚未迁移')
    await act(async () => late(moved()))
    expect(screen.queryByText('自定义磁盘位置')).not.toBeInTheDocument()
  })

  it('缺失文件清单阻止迁移，错误来源保留技术详情', async () => {
    const value = bridge()
    value.inspectStorage.mockResolvedValue({ ...inspection(), missing: [{ path: 'C:\\missing.dat', artifact_ids: ['missing-id'], state: 'missing' }] })
    await show(value)
    fireEvent.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    expect(screen.getByText('C:\\missing.dat')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '预览迁移到新位置' })).toBeDisabled()
    expect(value.previewStorageMigration).not.toHaveBeenCalled()
  })

  it('活动Run拒绝迁移时显示可执行说明，不自动重试', async () => {
    const value = bridge()
    value.previewStorageMigration.mockRejectedValue(new HostBridgeError('E_PROJECT_STORAGE_ACTIVE: synthetic active run', { code: 'E_PROJECT_STORAGE_ACTIVE' }))
    await show(value)
    fireEvent.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    fireEvent.click(screen.getByRole('button', { name: '预览迁移到新位置' }))
    expect(await screen.findByText('请先完成或放弃所有运行和外部等待任务，再迁移工程数据。')).toBeInTheDocument()
    expect(screen.getByText(/synthetic active run/)).toBeInTheDocument()
    expect(value.previewStorageMigration).toHaveBeenCalledTimes(1)
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it('再次刷新只检查登记资产，没有迁移或目录选择副作用', async () => {
    const { value } = await show()
    fireEvent.click(screen.getByRole('button', { name: '刷新归档检查' }))
    await waitFor(() => expect(value.inspectStorage).toHaveBeenCalledTimes(2))
    expect(value.pick).not.toHaveBeenCalled()
    expect(value.configureStorage).not.toHaveBeenCalled()
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })
})
