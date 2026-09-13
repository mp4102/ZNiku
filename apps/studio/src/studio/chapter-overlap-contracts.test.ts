import { afterEach, describe, expect, it, vi } from 'vitest'
import example from './__fixtures__/overlap-preview.json'
import { parseOverlapFullEnvelope, parseOverlapFullRequest, parseOverlapProcessingRequest, OverlapContractError, type OverlapFullRequest } from './chapter-overlap-contracts'
import { FetchStudioGateway } from './gateway'
import { studioEnvelope } from './test-fixtures'

afterEach(() => vi.unstubAllGlobals())
const preview = parseOverlapFullEnvelope(example)
function request(): OverlapFullRequest { return { contract_version: '0.3.2', processing: preview.processing, project_session_id: preview.project_session_id, expected_storage_revision: preview.storage_revision, preparation_run_id: preview.preparation_run_id, publication: { title: 'Synthetic', year: '2026', output_root: 'synthetic-output', overwrite: false, layout: 'direct' } } }

describe('Python overlap wire 隔离与失败关闭', () => {
  it('接受 Python 生成预览、可空增强版本和完整请求', () => {
    expect(preview.processing.enhancement.model_version).toBeNull()
    expect(parseOverlapFullRequest(request())).toEqual(request())
    expect(preview.status).toBe('pending_real_acceptance')
    expect(preview.contexts.status).toBe('mathematical-only')
  })
  it.each([
    { contract_version: '0.3.0' }, { shell: 'echo forbidden' }, { source_frame_count: 1801 }, { expected_storage_revision: -1 },
  ])('拒绝未知字段和无效版本：%j', (patch) => expect(() => parseOverlapFullRequest({ ...request(), ...patch })).toThrow())
  it.each([0, 61, 1.5, '5'])('分叶配置使用 Python Schema 严格限制 %s', (value) => {
    expect(() => parseOverlapProcessingRequest({ contract_version: '0.3.2', processing: { ...preview.processing, settings: { ...preview.processing.settings, leaf_max_minutes: value } } })).toThrow(OverlapContractError)
  })
  it('精确时间行错误保留行号，不被 average 分支的附加字段错误遮蔽', () => {
    try {
      parseOverlapProcessingRequest({ contract_version: '0.3.2', processing: { ...preview.processing, settings: { leaf_max_minutes: 5, chapter_selector: { mode: 'exact_times', times: ['00:00:01', 'bad'] } } } })
      throw new Error('expected validation failure')
    } catch (error) {
      expect(error).toBeInstanceOf(OverlapContractError)
      expect((error as OverlapContractError).issue.fieldPath).toEqual(['processing', 'settings', 'chapter_selector', 'times', 1])
    }
  })
  it('新错误 envelope 保留字段位置，网络失败不伪称输出被修改', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ contract_version: '0.3.2', error: { code: 'E_CHAPTER_SELECTOR_ORDER', message: '切点必须递增', field_path: ['processing', 'settings', 'chapter_selector', 'frames', 1], related_run_ids: [] } }), { status: 422 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(new FetchStudioGateway('http://127.0.0.1:1').previewOverlap(request())).rejects.toMatchObject({ code: 'E_CHAPTER_SELECTOR_ORDER', fieldPath: ['processing', 'settings', 'chapter_selector', 'frames', 1] })
    expect(fetcher.mock.calls).toHaveLength(1)
  })
  it.each(['project_session_id', 'storage_revision', 'preparation_run_id'])('拒绝迟到或其他资源响应：%s', async (field) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...example, [field]: field === 'storage_revision' ? 2 : '00000000-0000-4000-8000-000000000009' }))))
    await expect(new FetchStudioGateway('http://127.0.0.1:1').previewOverlap(request())).rejects.toThrow('不一致')
  })
  it('完整 Graph 保存使用专属有界入口，wire 仍是旧 0.3.0 命令', async () => {
    const current = studioEnvelope()
    const fetcher = vi.fn(async () => new Response(JSON.stringify(current)))
    vi.stubGlobal('fetch', fetcher)
    await new FetchStudioGateway('http://127.0.0.1:1').command({ operation: 'save_project', project: current.snapshot!.project, studio_state: current.studio_state!, project_session_id: current.project_session_id!, expected_storage_revision: current.storage_revision! })
    expect(fetcher).toHaveBeenCalledWith('http://127.0.0.1:1/api/studio/graph-save', expect.objectContaining({ method: 'POST' }))
  })
})
