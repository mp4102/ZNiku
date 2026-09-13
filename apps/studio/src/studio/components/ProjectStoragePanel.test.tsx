/** 合成 HostBridge 测试证明工程数据不会因打开、选择、取消或过期响应被悄悄迁移。 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HostBridgeError, type HostBridge, type StorageIndexResult, type StorageInspection, type StorageMigrationPreview } from '../host-bridge'
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

function readablePreview(operation: 'organize' | 'restore' = 'organize'): StorageMigrationPreview {
  return { ...preview(), operation, target: { ...moved().storage, contract_version: '0.3.2', layout: 'readable' },
    path_mappings: [{ source: 'C:\\synthetic\\attempts\\opaque-id', target: 'E:\\archive\\synthetic.data\\attempts\\chapters\\0001-A\\章节补帧__N009\\R002-A001' }] }
}

const fileIndex: StorageIndexResult = { contract_version: '0.3.2', project_session_id: 'session-1', expected_storage_revision: 4,
  path: 'E:\\archive\\synthetic.data\\文件目录.html', artifact_count: 2, external_dependency_count: 1, warnings: [] }

function bridge(attempts = 2) {
  return {
    configured: true,
    inspectCapabilities: vi.fn(async () => ({ contract_version: '0.3.0' as const, capabilities: [] })),
    pick: vi.fn<HostBridge['pick']>(async () => [{ selection_handle: 'selected-directory', path: 'E:\\archive' }]),
    launch: vi.fn<HostBridge['launch']>(async () => undefined),
    inspectStorage: vi.fn(async (_session: string) => inspection(attempts)),
    configureStorage: vi.fn<NonNullable<HostBridge['configureStorage']>>(async () => moved(attempts)),
    previewStorageMigration: vi.fn<NonNullable<HostBridge['previewStorageMigration']>>(async () => preview()),
    previewStorageOrganization: vi.fn<NonNullable<HostBridge['previewStorageOrganization']>>(async () => readablePreview()),
    previewStorageRestore: vi.fn<NonNullable<HostBridge['previewStorageRestore']>>(async () => readablePreview('restore')),
    confirmStorageMigration: vi.fn<NonNullable<HostBridge['confirmStorageMigration']>>(async () => moved(attempts)),
    generateStorageIndex: vi.fn<NonNullable<HostBridge['generateStorageIndex']>>(async () => fileIndex),
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
    expect(review).toHaveFocus()
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

describe('可读目录整理、索引与归档恢复', () => {
  it('新旧布局来自Python字段，不通过数据根或文件名推测，也不自动整理', async () => {
    const { value } = await show()
    expect(screen.getByText('UUID 旧布局：保留原有绑定，不会自动整理')).toBeInTheDocument()
    expect(value.previewStorageOrganization).not.toHaveBeenCalled()
    expect(value.generateStorageIndex).not.toHaveBeenCalled()
    value.inspectStorage.mockResolvedValue({ ...moved(), storage: { ...moved().storage, layout: 'readable' } })
    fireEvent.click(screen.getByRole('button', { name: '刷新归档检查' }))
    expect(await screen.findByText('可读目录：章节／工序、任务名与执行批次')).toBeInTheDocument()
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it('选父目录只预览整理和路径对照，确认前不复制；确认后旧原件保留', async () => {
    const { value, callbacks } = await show()
    fireEvent.click(screen.getByRole('button', { name: '选择新父目录并预览整理' }))
    const review = await screen.findByRole('region', { name: '确认数据迁移' })
    expect(value.pick).toHaveBeenCalledWith('select_directory', { title: '选择可读工程数据的新父文件夹' })
    expect(value.previewStorageOrganization).toHaveBeenCalledWith({ project_session_id: 'session-1', expected_storage_revision: 4, selection_handle: 'selected-directory' })
    expect(review).toHaveTextContent('确认整理为可读目录')
    expect(review).toHaveTextContent('章节补帧__N009')
    expect(review).toHaveTextContent('全部原件保留')
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
    fireEvent.click(within(review).getByRole('button', { name: '确认复制整理并保留原件' }))
    await waitFor(() => expect(callbacks.onChanged).toHaveBeenCalledTimes(1))
    expect(screen.getByText(/可读目录整理完成/)).toBeInTheDocument()
    expect(value.configureStorage).not.toHaveBeenCalled()
    expect(value.previewStorageMigration).not.toHaveBeenCalled()
  })

  it('整理原生对话框取消不产生预览、配置或复制', async () => {
    const value = bridge(); value.pick.mockResolvedValue(null)
    await show(value)
    fireEvent.click(screen.getByRole('button', { name: '选择新父目录并预览整理' }))
    await waitFor(() => expect(value.pick).toHaveBeenCalledTimes(1))
    expect(value.previewStorageOrganization).not.toHaveBeenCalled()
    expect(value.configureStorage).not.toHaveBeenCalled()
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it('索引生成和系统定位分别需要点击，定位只传当前工程引用不传路径', async () => {
    const { value } = await show()
    fireEvent.click(screen.getByRole('button', { name: '生成文件目录' }))
    const result = await screen.findByRole('region', { name: '已生成的文件目录' })
    expect(value.generateStorageIndex).toHaveBeenCalledWith({ project_session_id: 'session-1', expected_storage_revision: 4 })
    expect(result).toHaveTextContent(fileIndex.path)
    expect(result).toHaveFocus()
    expect(result).toHaveTextContent('不是完整归档')
    expect(value.launch).not.toHaveBeenCalled()
    expect(result.querySelector('a, iframe, script')).toBeNull()
    fireEvent.click(within(result).getByRole('button', { name: '在文件管理器中定位目录索引' }))
    await waitFor(() => expect(value.launch).toHaveBeenCalledWith('reveal_in_file_manager', { kind: 'storage_index', project_session_id: 'session-1' }))
  })

  it('重新定位选择.data本体，即使旧位置缺失仍可预览；确认不声称复制', async () => {
    const value = bridge()
    value.inspectStorage.mockResolvedValue({ ...inspection(), missing: [{ path: 'C:\\synthetic\\missing.mov', artifact_ids: [], state: 'missing' }] })
    value.previewStorageRestore.mockResolvedValue({ ...readablePreview('restore'), warnings: ['源目录缺失：仅核对已登记文件的尺寸/修改时间及日志存在；这不是内容证明。'] })
    value.pick.mockResolvedValue([{ selection_handle: 'existing-data-folder', path: 'E:\\archive\\synthetic.data' }])
    const { callbacks } = await show(value)
    fireEvent.click(screen.getByText('数据已经搬到另一位置？'))
    const button = screen.getByRole('button', { name: '选择已有 .data 目录并预览重新定位' })
    expect(button).toBeEnabled()
    fireEvent.click(button)
    const review = await screen.findByRole('region', { name: '确认数据迁移' })
    expect(value.pick).toHaveBeenCalledWith('select_directory', { title: '选择已经搬迁的工程 .data 文件夹' })
    expect(value.previewStorageRestore).toHaveBeenCalledWith({ project_session_id: 'session-1', expected_storage_revision: 4, selection_handle: 'existing-data-folder' })
    expect(review).toHaveTextContent('不复制、不删除')
    expect(review).toHaveTextContent('源目录缺失：仅核对已登记文件的尺寸/修改时间及日志存在；这不是内容证明。')
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
    fireEvent.click(within(review).getByRole('button', { name: '确认重新定位，不复制文件' }))
    await waitFor(() => expect(callbacks.onChanged).toHaveBeenCalledTimes(1))
    expect(screen.getByText(/工程数据已重新定位/)).toBeInTheDocument()
  })

  it('重新定位遇到错误或取消保持当前位置，不自动再试', async () => {
    const value = bridge()
    value.previewStorageRestore.mockRejectedValue(new HostBridgeError('E_PROJECT_STORAGE_RESTORE_MISMATCH: synthetic mismatch', { code: 'E_PROJECT_STORAGE_RESTORE_MISMATCH' }))
    await show(value)
    fireEvent.click(screen.getByText('数据已经搬到另一位置？'))
    fireEvent.click(screen.getByRole('button', { name: '选择已有 .data 目录并预览重新定位' }))
    expect(await screen.findByText(/所选目录不能与当前工程的已登记记录对应/)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '确认数据迁移' })).not.toBeInTheDocument()
    expect(value.previewStorageRestore).toHaveBeenCalledTimes(1)
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it('旧工程缺少归属绑定时指引先恢复原位置再复制整理，不让用户盲目重复选目录', async () => {
    const value = bridge()
    value.previewStorageRestore.mockRejectedValue(new HostBridgeError('E_PROJECT_STORAGE_RESTORE_UNPROVEN: synthetic legacy ownership missing', { code: 'E_PROJECT_STORAGE_RESTORE_UNPROVEN' }))
    await show(value)
    fireEvent.click(screen.getByText('数据已经搬到另一位置？'))
    fireEvent.click(screen.getByRole('button', { name: '选择已有 .data 目录并预览重新定位' }))
    expect(await screen.findByText(/未建立绑定的旧工程请先恢复原位置，再执行“复制整理”/)).toBeInTheDocument()
    expect(screen.getByText(/synthetic legacy ownership missing/)).toBeInTheDocument()
    expect(value.previewStorageRestore).toHaveBeenCalledTimes(1)
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it('原生选择进行中重复点击不再弹窗，旧会话的迟到选择不触发整理预览', async () => {
    const value = bridge()
    let finish!: (result: Awaited<ReturnType<HostBridge['pick']>>) => void
    value.pick.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const { rerender, callbacks } = await show(value)
    const button = screen.getByRole('button', { name: '选择新父目录并预览整理' })
    fireEvent.click(button); fireEvent.click(button)
    expect(value.pick).toHaveBeenCalledTimes(1)
    rerender(<ProjectStoragePanel bridge={value} projectSessionId="session-2" storageRevision={4} {...callbacks} />)
    await act(async () => finish([{ selection_handle: 'late-handle', path: 'E:\\old-project' }]))
    expect(value.previewStorageOrganization).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: '确认数据迁移' })).not.toBeInTheDocument()
  })

  it('会话切换后仍等待旧原生选择结束，迟到取消后新工程按钮恢复可用', async () => {
    const value = bridge()
    let finish!: (result: Awaited<ReturnType<HostBridge['pick']>>) => void
    value.pick.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const { rerender, callbacks } = await show(value)
    fireEvent.click(screen.getByRole('button', { name: '选择新父目录并预览整理' }))
    rerender(<ProjectStoragePanel bridge={value} projectSessionId="session-2" storageRevision={4} {...callbacks} />)
    await screen.findByText('旧版工作目录：保留原位置，尚未迁移')
    expect(screen.getByRole('button', { name: '选择新父目录并预览整理' })).toBeDisabled()
    await act(async () => finish(null))
    expect(screen.getByRole('button', { name: /^完成$/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: '选择新父目录并预览整理' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '选择新父目录并预览整理' }))
    await waitFor(() => expect(value.previewStorageOrganization).toHaveBeenCalledWith({ project_session_id: 'session-2', expected_storage_revision: 4, selection_handle: 'selected-directory' }))
  })

  it('索引的迟到响应不会进入新工程，不能定位旧工程索引', async () => {
    const value = bridge()
    let finish!: (result: StorageIndexResult) => void
    value.generateStorageIndex.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const { rerender, callbacks } = await show(value)
    fireEvent.click(screen.getByRole('button', { name: '生成文件目录' }))
    rerender(<ProjectStoragePanel bridge={value} projectSessionId="session-2" storageRevision={4} {...callbacks} />)
    await act(async () => finish(fileIndex))
    expect(screen.queryByRole('region', { name: '已生成的文件目录' })).not.toBeInTheDocument()
    expect(value.launch).not.toHaveBeenCalled()
    expect(callbacks.onChanged).not.toHaveBeenCalled()
  })

  it('目录对照只展示有界行数，大工程不会一次渲染所有执行路径', async () => {
    const value = bridge()
    value.previewStorageOrganization.mockResolvedValue({ ...readablePreview(), path_mappings: Array.from({ length: 80 }, (_, i) => ({ source: `C:\\source-${i}`, target: `E:\\readable-${i}` })) })
    await show(value)
    fireEvent.click(screen.getByRole('button', { name: '选择新父目录并预览整理' }))
    const review = await screen.findByRole('region', { name: '确认数据迁移' })
    expect(review).toHaveTextContent('查看目录对照（80 项）')
    expect(review.querySelectorAll('li')).toHaveLength(20)
    expect(review).not.toHaveTextContent('source-20')
  })
})
