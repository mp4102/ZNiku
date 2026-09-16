/** 英文物理目录由 Python 声明；中文说明不猜路径、不自动整理旧工程。 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { HostBridge, ProjectStorage, StorageInspection, StorageMigrationPreview } from '../host-bridge'
import { ProjectStoragePanel } from './ProjectStoragePanel'

afterEach(cleanup)

function storage(layout: 'english' | 'readable' | 'uuid' = 'english'): ProjectStorage {
  return {
    contract_version: layout === 'english' ? '0.3.5' : layout === 'readable' ? '0.3.2' : '0.3.0',
    mode: 'adjacent', data_root: 'D:\\synthetic.data',
    attempts_root: layout === 'english' ? 'D:\\synthetic.data' : 'D:\\synthetic.data\\attempts',
    retention: 'keep', media_basename: 'synthetic', layout,
  }
}

function inspection(layout: 'english' | 'readable' | 'uuid' = 'english'): StorageInspection {
  return {
    contract_version: '0.3.0', storage: storage(layout), configured: true,
    attempt_count: 1, registered_file_count: 1, registered_bytes: 12,
    managed_file_count: 1, managed_bytes: 12, missing: [], external_dependencies: [],
    coverage: 'registered_artifacts', warnings: [],
  }
}

function bridge(layout: 'english' | 'readable' | 'uuid' = 'english') {
  const preview: StorageMigrationPreview = {
    contract_version: '0.3.0', project_session_id: 'session', expected_storage_revision: 1,
    ticket_id: 'ticket', source: storage(layout),
    target: { ...storage(), data_root: 'E:\\synthetic.data', attempts_root: 'E:\\synthetic.data' },
    operation: 'organize', attempt_count: 1, file_count: 1, byte_count: 12,
    external_dependencies: [], originals_retained: true,
    path_mappings: [{ source: 'D:\\synthetic.data\\attempts\\old', target: 'E:\\synthetic.data\\enhancement\\A\\round-001' }],
  }
  return {
    configured: true,
    inspectCapabilities: vi.fn(async () => ({ contract_version: '0.3.0' as const, capabilities: [] })),
    pick: vi.fn<HostBridge['pick']>(async () => [{ selection_handle: 'directory', path: 'E:\\' }]),
    launch: vi.fn<HostBridge['launch']>(async () => undefined),
    inspectStorage: vi.fn(async () => inspection(layout)),
    previewStorageOrganization: vi.fn(async () => preview),
    confirmStorageMigration: vi.fn(async () => inspection()),
  } satisfies HostBridge
}

function show(value: ReturnType<typeof bridge>) {
  return render(<ProjectStoragePanel bridge={value} projectSessionId="session" storageRevision={1} onChanged={vi.fn()} onClose={vi.fn()} />)
}

describe('英文工程数据布局', () => {
  it('明确说明处理轮次和交回用途，不将英文布局误标为UUID旧布局', async () => {
    const value = bridge()
    show(value)
    expect(await screen.findByText('英文目录：任务／章节／处理轮次')).toBeInTheDocument()
    expect(screen.getByText(/round-001 表示该任务第 1 次处理，跨运行连续计数/)).toBeInTheDocument()
    expect(screen.getByText(/incoming 是外部结果交回区/)).toHaveTextContent('文件所在位置不代表验收通过')
    expect(screen.queryByText('UUID 旧布局：保留原有绑定，不会自动整理')).not.toBeInTheDocument()
    expect(screen.queryByText('工作产物目录', { exact: true })).not.toBeInTheDocument()
    expect(screen.getAllByText('D:\\synthetic.data', { exact: true })).toHaveLength(1)
    expect(value.pick).not.toHaveBeenCalled()
    expect(value.previewStorageOrganization).not.toHaveBeenCalled()
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it.each([
    ['uuid', 'UUID 旧布局：保留原有绑定，不会自动整理'],
    ['readable', '可读目录：章节／工序、任务名与执行批次'],
  ] as const)('旧%s布局按服务端声明展示，不凭英文示例自动转换', async (layout, label) => {
    const value = bridge(layout)
    show(value)
    expect(await screen.findByText(label)).toBeInTheDocument()
    expect(screen.queryByText('英文目录：任务／章节／处理轮次')).not.toBeInTheDocument()
    expect(value.previewStorageOrganization).not.toHaveBeenCalled()
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
  })

  it('明确预览英文目标并确认才整理，不在浏览器创建或推算目录', async () => {
    const value = bridge('readable')
    show(value)
    await screen.findByText('可读目录：章节／工序、任务名与执行批次')
    fireEvent.click(screen.getByRole('button', { name: '选择新父目录并预览整理' }))
    const review = await screen.findByRole('region', { name: '确认数据迁移' })
    expect(review).toHaveTextContent('目标使用英文目录：任务／章节／处理轮次')
    expect(review).toHaveTextContent('E:\\synthetic.data\\enhancement\\A\\round-001')
    expect(review).toHaveTextContent('全部原件保留')
    expect(value.previewStorageOrganization).toHaveBeenCalledWith({ project_session_id: 'session', expected_storage_revision: 1, selection_handle: 'directory' })
    expect(value.confirmStorageMigration).not.toHaveBeenCalled()
    fireEvent.click(within(review).getByRole('button', { name: '确认复制整理并保留原件' }))
    await waitFor(() => expect(value.confirmStorageMigration).toHaveBeenCalledWith({ project_session_id: 'session', expected_storage_revision: 1, ticket_id: 'ticket' }))
    expect(await screen.findByText('英文目录：任务／章节／处理轮次')).toBeInTheDocument()
  })
})
