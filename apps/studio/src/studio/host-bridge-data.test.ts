/** 工程数据与收件 wire 只接受 Python Schema；票据消费后网络不明也不重放文件副作用。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FetchHostBridge } from './host-bridge'
import type { HandoffImportBinding, ProjectStorage, StorageInspection, StorageMigrationPreview } from './host-bridge'

afterEach(() => vi.unstubAllGlobals())
const session = '00000000-0000-4000-8000-000000000001'
const other = '00000000-0000-4000-8000-000000000002'
const binding: HandoffImportBinding = {
  contract_version: '0.3.0', project_session_id: session,
  run_id: other, node_run_id: '00000000-0000-4000-8000-000000000003',
  handoff_id: '00000000-0000-4000-8000-000000000004', port_id: 'video', ordinal: null,
}
const inboxObserved = { ...binding, inbox_path: 'D:\\synthetic\\incoming\\video', allowed_suffix: '.mov',
  candidates: [{ candidate_handle: 'candidate_1234567890_1234567890', name: 'arbitrary.mov', size: 1024, mtime_ns: 1000 }],
  rejected_count: 0, expires_in_seconds: 300 }
const inboxRequest = { ...binding, candidate_handle: inboxObserved.candidates[0]!.candidate_handle }
const inboxPreview = { ...binding, inbox_id: 'inbox_1234567890_1234567890', source_name: 'arbitrary.mov', source_size: 1024,
  target_path: 'D:\\synthetic\\outputs\\SYN-001.A.enhancement.mov', replace_existing: true, action: 'move', expires_in_seconds: 300 }
const inboxResult = { ...binding, inbox_id: inboxPreview.inbox_id, source_name: inboxPreview.source_name,
  source_size: inboxPreview.source_size, target_path: inboxPreview.target_path, status: 'collected' }
const storage: ProjectStorage = { contract_version: '0.3.0', mode: 'custom', data_root: 'D:\\archive\\project.data',
  attempts_root: 'D:\\archive\\project.data\\attempts', retention: 'keep', media_basename: 'SYN-001 (2020)' }
const inspection: StorageInspection = { contract_version: '0.3.0', storage, configured: true, attempt_count: 2,
  registered_file_count: 2, registered_bytes: 2048, managed_file_count: 2, managed_bytes: 2048,
  missing: [], external_dependencies: [], coverage: 'registered_artifacts', warnings: [] }
const storageRequest = { contract_version: '0.3.0' as const, project_session_id: session, expected_storage_revision: 3, selection_handle: null }
const storagePreview: StorageMigrationPreview = { contract_version: '0.3.0', ticket_id: '00000000-0000-4000-8000-000000000005',
  project_session_id: session, expected_storage_revision: 3,
  source: { ...storage, mode: 'legacy', data_root: 'C:\\synthetic', attempts_root: 'C:\\synthetic\\attempts' },
  target: storage, attempt_count: 2, file_count: 2, byte_count: 2048, external_dependencies: [], originals_retained: true }
function response(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
}
function bridge() {
  return new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token: 'a'.repeat(43) })
}

describe('工程数据 HostBridge wire', () => {
  it('收件观察和预览只传精确身份及opaque句柄，token只在header', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(inboxObserved)).mockResolvedValueOnce(response(inboxPreview))
    vi.stubGlobal('fetch', fetch)
    const host = bridge()
    await expect(host.observeHandoffInbox(binding)).resolves.toEqual(inboxObserved)
    await expect(host.previewHandoffInbox(inboxRequest)).resolves.toEqual(inboxPreview)
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      'http://127.0.0.1:18765/api/studio/handoff-inbox/observe',
      'http://127.0.0.1:18765/api/studio/handoff-inbox/preview',
    ])
    for (const [, raw] of fetch.mock.calls) {
      const init = raw as RequestInit
      expect(init.method).toBe('POST')
      expect(init.headers).toMatchObject({ 'X-ZNIKU-Host-Token': 'a'.repeat(43) })
      expect(init.body).not.toContain('a'.repeat(43))
      expect(init.body).not.toContain('path')
      expect(init.body).not.toContain('source_name')
    }
  })

  it('拒绝收件原始路径、未知候选字段、非法类型和错任务响应', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response({ ...inboxObserved, candidates: [{ ...inboxObserved.candidates[0], path: 'D:\\arbitrary.mov' }] }))
      .mockResolvedValueOnce(response({ ...inboxObserved, candidates: [{ ...inboxObserved.candidates[0], size: '1024' }] }))
      .mockResolvedValueOnce(response({ ...inboxObserved, project_session_id: other }))
      .mockResolvedValueOnce(response({ ...inboxPreview, handoff_id: other }))
    vi.stubGlobal('fetch', fetch)
    const host = bridge()
    await expect(host.observeHandoffInbox({ ...binding, inbox_path: 'D:\\wrong' } as typeof binding)).rejects.toThrow('Schema')
    expect(fetch).not.toHaveBeenCalled()
    await expect(host.observeHandoffInbox(binding)).rejects.toThrow('Schema')
    await expect(host.observeHandoffInbox(binding)).rejects.toThrow('Schema')
    await expect(host.observeHandoffInbox(binding)).rejects.toThrow('不属于')
    await expect(host.previewHandoffInbox(inboxRequest)).rejects.toThrow('不属于')
  })

  it('收纳确认一次性消费，回执严格保持任务、来源和目标', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(inboxPreview)).mockResolvedValueOnce(response(inboxResult))
    vi.stubGlobal('fetch', fetch)
    const host = bridge()
    await host.previewHandoffInbox(inboxRequest)
    const confirm = { contract_version: '0.3.0' as const, inbox_id: inboxPreview.inbox_id, overwrite: true }
    await expect(host.confirmHandoffInbox(confirm)).resolves.toEqual(inboxResult)
    await expect(host.confirmHandoffInbox(confirm)).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(JSON.parse((fetch.mock.calls[1]![1] as RequestInit).body as string)).toEqual(confirm)
  })

  it('网络结果不明和错误收纳回执都不自动重放', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(inboxPreview)).mockRejectedValueOnce(new Error('synthetic offline'))
      .mockResolvedValueOnce(response(inboxPreview)).mockResolvedValueOnce(response({ ...inboxResult, target_path: 'D:\\wrong.mov' }))
    vi.stubGlobal('fetch', fetch)
    const host = bridge()
    const confirm = { contract_version: '0.3.0' as const, inbox_id: inboxPreview.inbox_id, overwrite: false }
    await host.previewHandoffInbox(inboxRequest)
    await expect(host.confirmHandoffInbox(confirm)).rejects.toThrow('synthetic offline')
    await expect(host.confirmHandoffInbox(confirm)).rejects.toThrow('已失效')
    await host.previewHandoffInbox(inboxRequest)
    await expect(host.confirmHandoffInbox(confirm)).rejects.toThrow('不一致')
    await expect(host.confirmHandoffInbox(confirm)).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(4)
  })

  it('存储检查/配置严格Schema，不接受浏览器直接指定目标目录', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(inspection)).mockResolvedValueOnce(response({ ...inspection, extra: true }))
    vi.stubGlobal('fetch', fetch)
    const host = bridge()
    await expect(host.inspectStorage(session)).resolves.toEqual(inspection)
    expect(() => host.configureStorage({ ...storageRequest, data_root: 'D:\\bad' } as typeof storageRequest)).toThrow('Schema')
    expect(fetch).toHaveBeenCalledTimes(1)
    await expect(host.inspectStorage(session)).rejects.toThrow('Schema')
  })

  it('迁移预览必须绑定工程会话与storage_revision，不能拿错项目票据确认', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ ...storagePreview, project_session_id: other }))
      .mockResolvedValueOnce(response({ ...storagePreview, expected_storage_revision: 4 }))
      .mockResolvedValueOnce(response(storagePreview))
    vi.stubGlobal('fetch', fetch)
    const host = bridge()
    await expect(host.previewStorageMigration(storageRequest)).rejects.toThrow('不属于')
    await expect(host.previewStorageMigration(storageRequest)).rejects.toThrow('不属于')
    await host.previewStorageMigration(storageRequest)
    await expect(host.confirmStorageMigration({ contract_version: '0.3.0', project_session_id: other,
      expected_storage_revision: 3, ticket_id: storagePreview.ticket_id })).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('迁移只确认一次，验证目标一致且不把检查结果夸大为完整归档', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(storagePreview)).mockResolvedValueOnce(response(inspection))
      .mockResolvedValueOnce(response(storagePreview)).mockResolvedValueOnce(response({ ...inspection, storage: { ...storage, data_root: 'D:\\wrong.data' } }))
    vi.stubGlobal('fetch', fetch)
    const host = bridge()
    const confirm = { contract_version: '0.3.0' as const, project_session_id: session, expected_storage_revision: 3, ticket_id: storagePreview.ticket_id }
    await host.previewStorageMigration(storageRequest)
    await expect(host.confirmStorageMigration(confirm)).resolves.toMatchObject({ coverage: 'registered_artifacts' })
    await expect(host.confirmStorageMigration(confirm)).rejects.toThrow('已失效')
    await host.previewStorageMigration(storageRequest)
    await expect(host.confirmStorageMigration(confirm)).rejects.toThrow('不一致')
    expect(fetch).toHaveBeenCalledTimes(4)
  })

  it('迁移确认网络失败也消费票据，不自动再次复制', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(storagePreview)).mockRejectedValueOnce(new Error('synthetic lost acknowledgement'))
    vi.stubGlobal('fetch', fetch)
    const host = bridge()
    const confirm = { contract_version: '0.3.0' as const, project_session_id: session, expected_storage_revision: 3, ticket_id: storagePreview.ticket_id }
    await host.previewStorageMigration(storageRequest)
    await expect(host.confirmStorageMigration(confirm)).rejects.toThrow('synthetic lost acknowledgement')
    await expect(host.confirmStorageMigration(confirm)).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(2)
  })
})
