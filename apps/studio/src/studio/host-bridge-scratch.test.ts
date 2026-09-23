/** 内部中转确认仅发送一次性句柄，响应失配及网络结果不明均不自动重试。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FetchHostBridge } from './host-bridge'
import type { ScratchPreviewEnvelope, ScratchConfirmRequest, ScratchConfirmEnvelope } from './storage-scratch-contracts'

afterEach(() => vi.unstubAllGlobals())
const session = '00000000-0000-4000-8000-000000000001'
const ticket = '00000000-0000-4000-8000-000000000002'
const candidate = '00000000-0000-4000-8000-000000000003'
const binding = { contract_version: '0.3.0' as const, project_session_id: session, expected_storage_revision: 7 }
const preview: ScratchPreviewEnvelope = { ...binding, ticket_id: ticket, expires_in_seconds: 300,
  summary: [{ category: 'internal_scratch', file_count: 1, byte_count: 1024 }], warnings: [], truncated: false,
  entries: [{ path: 'D:\\synthetic\\context\\round-001\\scratch.mov', category: 'internal_scratch', byte_count: 1024,
    node_id: 'context', node_run_id: '00000000-0000-4000-8000-000000000004', attempt: 1,
    task_label: 'FI 上下文', chapter_label: 'A', round_label: 'round-001', role: 'scratch', reason: '合成服务标记', candidate_id: candidate }] }
const request: ScratchConfirmRequest = { ...binding, ticket_id: ticket, candidate_ids: [candidate], confirm_irreversible: true }
const result: ScratchConfirmEnvelope = { ...binding, ticket_id: ticket, entries: [{ candidate_id: candidate,
  path: preview.entries[0]!.path, byte_count: 1024, status: 'deleted', message: '已删除' }], deleted_bytes: 1024, deletion_count: 1, complete: true, warnings: [] }
const host = () => new FetchHostBridge({ baseUrl: 'http://127.0.0.1:8765', token: 's'.repeat(48) })
const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200 })

describe('scratch 严格 HostBridge', () => {
  it('预览绑定版本，确认只传ticket与候选，不传路径且只用一次', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(preview)).mockResolvedValueOnce(response(result)); vi.stubGlobal('fetch', fetcher)
    const bridge = host()
    await expect(bridge.previewStorageScratch(binding)).resolves.toEqual(preview)
    await expect(bridge.confirmStorageScratch(request)).resolves.toEqual(result)
    expect(fetcher).toHaveBeenLastCalledWith('http://127.0.0.1:8765/api/studio/storage/scratch-confirm', expect.objectContaining({ body: JSON.stringify(request) }))
    await expect(bridge.confirmStorageScratch(request)).rejects.toThrow()
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('空选择、重复、未知候选以及未明确确认不发送副作用', async () => {
    const fetcher = vi.fn().mockResolvedValue(response(preview)); vi.stubGlobal('fetch', fetcher)
    const bridge = host(); await bridge.previewStorageScratch(binding)
    for (const patch of [{ candidate_ids: [] }, { candidate_ids: [candidate, candidate] }, { candidate_ids: [ticket] },
      { confirm_irreversible: false }, { path: 'D:\\untrusted' }, { expected_storage_revision: 8 }]) {
      await expect(bridge.confirmStorageScratch({ ...request, ...patch } as ScratchConfirmRequest)).rejects.toThrow()
    }
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('预览错误会话或版本不会留下可确认票据', async () => {
    for (const patch of [{ project_session_id: ticket }, { expected_storage_revision: 8 }, { unexpected: true }]) {
      const fetcher = vi.fn().mockResolvedValue(response({ ...preview, ...patch })); vi.stubGlobal('fetch', fetcher)
      const bridge = host()
      await expect(bridge.previewStorageScratch(binding)).rejects.toThrow()
      await expect(bridge.confirmStorageScratch(request)).rejects.toThrow()
      expect(fetcher).toHaveBeenCalledTimes(1)
    }
  })
  it('结果只计实际删除；回执错路径或虚报大小失败关闭', async () => {
    for (const changed of [{ ...result, deleted_bytes: 1 }, { ...result, project_session_id: ticket },
      { ...result, entries: [{ ...result.entries[0]!, path: 'D:\\another.mov' }] }]) {
      const fetcher = vi.fn().mockResolvedValueOnce(response(preview)).mockResolvedValueOnce(response(changed)); vi.stubGlobal('fetch', fetcher)
      const bridge = host(); await bridge.previewStorageScratch(binding)
      await expect(bridge.confirmStorageScratch(request)).rejects.toThrow()
      await expect(bridge.confirmStorageScratch(request)).rejects.toThrow()
      expect(fetcher).toHaveBeenCalledTimes(2)
    }
  })
  it('网络不明消耗客户端票据，不自动再次删除', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(preview)).mockRejectedValueOnce(new Error('connection lost')); vi.stubGlobal('fetch', fetcher)
    const bridge = host(); await bridge.previewStorageScratch(binding)
    await expect(bridge.confirmStorageScratch(request)).rejects.toThrow()
    await expect(bridge.confirmStorageScratch(request)).rejects.toThrow()
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
