/** Python Schema + 绑定语义双重校验；副作用确认只消费一次，绝不自动重试。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FetchHostBridge } from './host-bridge'
import { batchBinding, batchObservation, batchPreview, batchReadiness } from './handoff-batch-fixtures'

afterEach(() => vi.unstubAllGlobals())
const host = () => new FetchHostBridge({ baseUrl: 'http://127.0.0.1:8765', token: 'x'.repeat(48) })
const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
const items = batchPreview.matches.map((match) => ({ port_id: match.port_id, candidate_handle: match.candidate_handle!, overwrite: false }))
const received = { ...batchObservation, complete: true, rows: batchObservation.rows.map((row) => ({ ...row, collected: true, size: 1000 })) }

describe('章级收件 HTTP 合同', () => {
  it('observe/preview 只传签发句柄，confirm单次复制绑定完整目标', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(batchObservation)).mockResolvedValueOnce(response(batchPreview)).mockResolvedValueOnce(response({ ...received,
      batch_id: batchPreview.batch_id, results: items.map((item) => ({ port_id: item.port_id, status: 'collected', message: null })) }))
    vi.stubGlobal('fetch', fetch)
    const bridge = host()
    expect(await bridge.observeHandoffBatch(batchBinding)).toEqual(batchObservation)
    await bridge.previewHandoffBatch({ ...batchBinding, selection_handles: [] })
    await bridge.confirmHandoffBatch({ contract_version: '0.3.0', batch_id: batchPreview.batch_id, items })
    await expect(bridge.confirmHandoffBatch({ contract_version: '0.3.0', batch_id: batchPreview.batch_id, items })).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(fetch.mock.calls[1]![0]).toBe('http://127.0.0.1:8765/api/studio/handoff-batch/preview')
  })
  it.each([
    { ...batchObservation, project_session_id: '10000000-0000-4000-8000-000000000099' },
    { ...batchObservation, complete: true },
    { ...batchObservation, rows: [batchObservation.rows[0], batchObservation.rows[0]] },
    { ...batchObservation, unexpected: true },
  ])('拒绝错误工程、虚假齐全、重复端口及未知字段', async (value) => {
    vi.stubGlobal('fetch', vi.fn(async () => response(value)))
    await expect(host().observeHandoffBatch(batchBinding)).rejects.toThrow()
  })
  it('预览不能认领不存在的候选或把同一文件自动匹配两个端口', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ ...batchPreview, matches: batchPreview.matches.map((match) => ({ ...match, candidate_handle: batchPreview.matches[0]!.candidate_handle })) })))
    await expect(host().previewHandoffBatch({ ...batchBinding, selection_handles: [] })).rejects.toThrow('重复或未知')
  })
  it('取消后的未使用预览无副作用；覆盖及唯一来源校验发生在POST前', async () => {
    const fetch = vi.fn(async () => response({ ...batchPreview, ...received }))
    vi.stubGlobal('fetch', fetch)
    const bridge = host()
    await bridge.previewHandoffBatch({ ...batchBinding, selection_handles: [] })
    await expect(bridge.confirmHandoffBatch({ contract_version: '0.3.0', batch_id: batchPreview.batch_id, items })).rejects.toThrow('覆盖')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('网络结果未知消耗confirm绑定，不能重复复制', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(batchPreview)).mockRejectedValueOnce(new Error('network lost'))
    vi.stubGlobal('fetch', fetch)
    const bridge = host()
    await bridge.previewHandoffBatch({ ...batchBinding, selection_handles: [] })
    await expect(bridge.confirmHandoffBatch({ contract_version: '0.3.0', batch_id: batchPreview.batch_id, items })).rejects.toThrow()
    await expect(bridge.confirmHandoffBatch({ contract_version: '0.3.0', batch_id: batchPreview.batch_id, items })).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(2)
  })
  it('发布后incoming已移动仍齐全，但Submit资格必须来自完整Python检查', async () => {
    const published = { ...received, rows: received.rows.map((row) => ({ ...row, collected: false, target_exists: true })), published: true,
      validation_error: null, readiness: batchReadiness }
    const fetch = vi.fn().mockResolvedValueOnce(response(published)).mockResolvedValueOnce(response({ ...published, readiness: null }))
    vi.stubGlobal('fetch', fetch)
    expect(await host().checkHandoffBatch({ ...batchBinding, overwrite_ports: [] })).toEqual(published)
    await expect(host().checkHandoffBatch({ ...batchBinding, overwrite_ports: [] })).rejects.toThrow('回执不完整')
  })
})
