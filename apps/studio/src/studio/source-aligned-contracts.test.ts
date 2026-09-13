import { afterEach, describe, expect, it, vi } from 'vitest'
import example from './__fixtures__/source-aligned-preview.json'
import { FetchStudioGateway } from './gateway'
import { StudioContractError } from './contracts'
import { sourceAlignedCatalogAvailable, parseSourceAlignedFullEnvelope, parseSourceAlignedFullRequest, parseSourceAlignedProcessingRequest, type SourceAlignedFullRequest, type SourceAlignedProcessingRequest } from './source-aligned-contracts'
import { studioEnvelope } from './test-fixtures'

afterEach(() => vi.unstubAllGlobals())
const preview = parseSourceAlignedFullEnvelope(example)
function request(): SourceAlignedFullRequest {
  return { contract_version: '0.3.3', processing: preview.processing, project_session_id: preview.project_session_id,
    expected_storage_revision: preview.storage_revision, preparation_run_id: preview.preparation_run_id,
    publication: { title: 'Synthetic', year: '2026', output_root: 'synthetic-output', overwrite: false, layout: 'direct' } }
}
const externalMR = { mode: 'external', model_name: 'Synthetic Tool', model_version: null, declared_container: 'mp4', operator_frame_order_confirmed: true } as const

describe('Python source-aligned wire 与旧合同严格隔离', () => {
  it('接受 Python 生成的原片规划预览；数学 plan 保持 0.3.2，外层 0.3.3', () => {
    expect(parseSourceAlignedFullRequest(request())).toEqual(request())
    expect(preview.profile_id).toBe('zniku.source-aligned-overlap')
    expect(preview.plan.profile_version).toBe('0.3.2')
    expect(preview.processing.mr).toEqual({ mode: 'off' })
  })
  it.each(['mp4', 'mov', 'mkv'] as const)('明确 %s 容器通过，不把文件名当合同', (declaredContainer) => {
    const value = { contract_version: '0.3.3', processing: { ...preview.processing, mr: { ...externalMR, declared_container: declaredContainer } } }
    expect(parseSourceAlignedProcessingRequest(value)).toEqual(value)
  })
  it.each([{ contract_version: '0.3.2' }, { source_frame_count: 120 }, { shell: 'untrusted' }, { expected_storage_revision: -1 }])('未知或旧字段失败关闭 %j', (patch) => {
    expect(() => parseSourceAlignedFullRequest({ ...request(), ...patch })).toThrow(StudioContractError)
  })
  it.each([{ operator_frame_order_confirmed: false }, { operator_frame_order_confirmed: undefined }, { declared_container: 'avi' }, { command: 'anything' }, { model_name: '' }])('MR 声明失败关闭 %j', (patch) => {
    expect(() => parseSourceAlignedProcessingRequest({ contract_version: '0.3.3', processing: { ...preview.processing, mr: { ...externalMR, ...patch } } })).toThrow(StudioContractError)
  })
  it('仅真实 exact 新目录启用，旧版本或缺少任一容器都不可冒充新能力', () => {
    const ids = ['zniku.overlap.split.leaves.1', 'zniku.overlap.enhancement.external', 'zniku.overlap.merge_video', 'zniku.overlap.fi_context', 'zniku.overlap.frame_interpolation.external', 'zniku.overlap.fi_crop', 'zniku.overlap.program_encode', 'zniku.overlap.final_mux', 'zniku.source_aligned.external.mp4', 'zniku.source_aligned.external.mov', 'zniku.source_aligned.external.mkv']
    expect(sourceAlignedCatalogAvailable(ids.map((type_id) => ({ type_id, definition_version: '0.3.3' })))).toBe(true)
    expect(sourceAlignedCatalogAvailable(ids.map((type_id) => ({ type_id, definition_version: '0.3.2' })))).toBe(false)
    expect(sourceAlignedCatalogAvailable(ids.slice(0, -1).map((type_id) => ({ type_id, definition_version: '0.3.3' })))).toBe(false)
  })
  it('三个独立端点均使用新合同；不发旧 command，展开仍校验存储 CAS', async () => {
    const processing: SourceAlignedProcessingRequest = { contract_version: '0.3.3', processing: preview.processing }
    const status = { ...studioEnvelope(), project_session_id: preview.project_session_id, storage_revision: preview.storage_revision + 1 }
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ ...processing, status: 'pending_real_acceptance' })))
      .mockResolvedValueOnce(new Response(JSON.stringify(preview))).mockResolvedValueOnce(new Response(JSON.stringify(status)))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    await gateway.previewSourceAlignedProcessing(processing)
    await gateway.previewSourceAligned(request())
    await gateway.expandSourceAligned(request())
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['processing-preview', 'full-preview', 'expand'].map((suffix) => `http://127.0.0.1:1/api/studio/templates/source-aligned-overlap/${suffix}`))
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ...status, storage_revision: preview.storage_revision })))
    await expect(gateway.expandSourceAligned(request())).rejects.toThrow('不一致')
  })
  it.each(['project_session_id', 'storage_revision', 'preparation_run_id'])('其他资源或晚到预览失败关闭 %s', async (field) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...example, [field]: field === 'storage_revision' ? 999 : '00000000-0000-4000-8000-000000000009' }))))
    await expect(new FetchStudioGateway('http://127.0.0.1:1').previewSourceAligned(request())).rejects.toThrow('不一致')
  })
  it('拒绝 MR 回显漂移；Schema 非法输入在网络前拒绝', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ...preview, processing: { ...preview.processing, mr: externalMR } })))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    await expect(gateway.previewSourceAligned(request())).rejects.toThrow('不一致')
    await expect(gateway.previewSourceAligned({ ...request(), contract_version: '0.3.2' } as unknown as SourceAlignedFullRequest)).rejects.toThrow(StudioContractError)
    expect(fetcher).toHaveBeenCalledOnce()
  })
  it('保留 Python MR field_path 和旧准备图拒绝原因，不自动重试旧接口', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ contract_version: '0.3.3', error: { code: 'E_SOURCE_ALIGNED_LEGACY_PREPARATION', message: '请继续原外部任务', field_path: ['processing', 'mr'], related_run_ids: [] } }), { status: 422 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(new FetchStudioGateway('http://127.0.0.1:1').previewSourceAligned(request())).rejects.toMatchObject({ code: 'E_SOURCE_ALIGNED_LEGACY_PREPARATION', fieldPath: ['processing', 'mr'] })
    expect(fetcher).toHaveBeenCalledOnce()
  })
})
