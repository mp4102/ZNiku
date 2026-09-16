/** 新源准入只消费 Python Schema；未知字段、迟到身份和未确认候选不能触发写入。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import example from './__fixtures__/source-admitted-preview.json'
import { StudioContractError } from './contracts'
import { FetchStudioGateway } from './gateway'
import {
  parseSourceAdmittedFullEnvelope, parseSourceAdmittedReplaceRequest, sourceAdmittedCatalogAvailable,
  type SourceAdmittedCreateRequest, type SourceAdmittedFullRequest, type SourceAdmittedReplaceRequest,
} from './source-admitted-contracts'
import { projectSnapshot, studioEnvelope } from './test-fixtures'

afterEach(() => vi.unstubAllGlobals())
const preview = parseSourceAdmittedFullEnvelope({ ...example, warnings: ['合成缺失信号警告'] })
const replacement: SourceAdmittedReplaceRequest = {
  contract_version: '0.3.5', project_session_id: preview.project_session_id,
  expected_storage_revision: preview.storage_revision, source_path: 'C:\\synthetic\\candidate.mp4',
  reference_change_confirmed: true,
}
function fullRequest(): SourceAdmittedFullRequest {
  return { contract_version: '0.3.5', processing: preview.processing,
    project_session_id: preview.project_session_id, expected_storage_revision: preview.storage_revision,
    preparation_run_id: preview.preparation_run_id,
    publication: { title: 'Synthetic', year: '2026', output_root: 'C:\\synthetic\\output', overwrite: false, layout: 'direct' } }
}
const gateway = () => new FetchStudioGateway('http://127.0.0.1:1')
const response = (value: unknown) => new Response(JSON.stringify(value))

describe('0.3.5 单一准入 Schema 和响应绑定', () => {
  it.each([false, 1, 'true', null, undefined])('候选必须明确 true，不接受 %j', (value) => {
    expect(() => parseSourceAdmittedReplaceRequest({ ...replacement, reference_change_confirmed: value })).toThrow(StudioContractError)
  })
  it('未知字段和旧版本在网络前拒绝，不能调用旧路由兜底', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    for (const changed of [{ shell: 'untrusted' }, { contract_version: '0.3.3' }, { expected_storage_revision: -1 }]) {
      await expect(gateway().replaceSourceAdmitted({ ...replacement, ...changed } as SourceAdmittedReplaceRequest)).rejects.toThrow(StudioContractError)
    }
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('候选替换只接受当前 session 的 revision+1，且发送明确确认', async () => {
    const status = studioEnvelope({ project_session_id: replacement.project_session_id,
      storage_revision: replacement.expected_storage_revision + 1 })
    const fetcher = vi.fn().mockResolvedValue(response(status))
    vi.stubGlobal('fetch', fetcher)
    expect(await gateway().replaceSourceAdmitted(replacement)).toEqual(status)
    expect(fetcher).toHaveBeenCalledWith('http://127.0.0.1:1/api/studio/templates/source-admitted-overlap/replace-source',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(replacement) }))
    for (const patch of [{ project_session_id: '00000000-0000-4000-8000-000000000099' },
      { storage_revision: replacement.expected_storage_revision }, { unexpected: true }]) {
      fetcher.mockResolvedValueOnce(response({ ...status, ...patch }))
      await expect(gateway().replaceSourceAdmitted(replacement)).rejects.toThrow(StudioContractError)
    }
  })
  it.each(['project_session_id', 'storage_revision', 'preparation_run_id'])('完整预览拒绝迟到身份 %s', async (field) => {
    const changed = { ...preview, [field]: field === 'storage_revision' ? 999 : '00000000-0000-4000-8000-000000000099' }
    vi.stubGlobal('fetch', vi.fn(async () => response(changed)))
    await expect(gateway().previewSourceAdmitted(fullRequest())).rejects.toThrow('不一致')
  })
  it('完整预览保留警告；未知响应字段与设置漂移失败关闭', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(preview))
    vi.stubGlobal('fetch', fetcher)
    expect((await gateway().previewSourceAdmitted(fullRequest())).warnings).toEqual(['合成缺失信号警告'])
    fetcher.mockResolvedValueOnce(response({ ...preview, unexpected: true }))
    await expect(gateway().previewSourceAdmitted(fullRequest())).rejects.toThrow(StudioContractError)
    fetcher.mockResolvedValueOnce(response({ ...preview, processing: { ...preview.processing,
      settings: { ...preview.processing.settings, leaf_max_minutes: 9 } } }))
    await expect(gateway().previewSourceAdmitted(fullRequest())).rejects.toThrow('不一致')
  })
  it('新建响应必须匹配 project id/name，不用其他工程的成功状态代替', async () => {
    const request: SourceAdmittedCreateRequest = { contract_version: '0.3.5', request: {
      profile_version: '2.7.0', project_id: projectSnapshot.project.project_id,
      project_name: projectSnapshot.project.name, project_path: 'C:\\synthetic\\new.zniku',
      source_mode: 'program', sources: [{ source_path: 'C:\\synthetic\\source.mp4', source_ordinal: 0 }], mr: { mode: 'off' },
    } }
    const fetcher = vi.fn().mockResolvedValueOnce(response(studioEnvelope()))
      .mockResolvedValueOnce(response(studioEnvelope({ snapshot: { ...projectSnapshot,
        project: { ...projectSnapshot.project, name: 'Other Project' } } })))
    vi.stubGlobal('fetch', fetcher)
    await gateway().createSourceAdmitted(request)
    await expect(gateway().createSourceAdmitted(request)).rejects.toThrow('不一致')
  })
  it('取消只发送当前 Run，拒绝其他 session 的响应', async () => {
    const request = { contract_version: '0.3.5' as const, project_session_id: replacement.project_session_id,
      run_id: preview.preparation_run_id }
    const fetcher = vi.fn().mockResolvedValueOnce(response(studioEnvelope({ project_session_id: request.project_session_id })))
      .mockResolvedValueOnce(response(studioEnvelope()))
    vi.stubGlobal('fetch', fetcher)
    await gateway().cancelSourceAdmitted(request)
    expect(fetcher).toHaveBeenCalledWith('http://127.0.0.1:1/api/studio/templates/source-admitted-overlap/cancel-analysis',
      expect.objectContaining({ body: JSON.stringify(request) }))
    await expect(gateway().cancelSourceAdmitted(request)).rejects.toThrow('不属于当前工程')
  })
  it('仅完整新 exact 目录启用，不把旧节点组合当作 0.3.5 能力', () => {
    const ids = ['zniku.avenhance.v27.source_program', 'zniku.avenhance.v27.source_admission',
      'zniku.overlap.split.leaves.1', 'zniku.source-admitted.enhancement-batch.1', 'zniku.source-admitted.chapter-batch.merge',
      'zniku.source-admitted.chapter-batch.context', 'zniku.source-admitted.chapter-batch.fi', 'zniku.source-admitted.chapter-batch.crop',
      'zniku.source-admitted.chapter-batch.program', 'zniku.source-admitted.chapter-batch.final', 'zniku.source_aligned.external.mp4',
      'zniku.source_aligned.external.mov', 'zniku.source_aligned.external.mkv']
    const nodes = ids.map((type_id) => ({ type_id, definition_version: '0.3.5' }))
    expect(sourceAdmittedCatalogAvailable(nodes)).toBe(true)
    // 同版本旧单叶目录不能冒充新的章级能力；任一新下游角色缺失也不可混搭启用。
    const oldRoles = ['zniku.overlap.enhancement.external', 'zniku.overlap.merge_video', 'zniku.overlap.fi_context',
      'zniku.overlap.frame_interpolation.external', 'zniku.overlap.fi_crop', 'zniku.overlap.program_encode', 'zniku.overlap.final_mux']
    const common = nodes.filter((node) => !node.type_id.startsWith('zniku.source-admitted.'))
    expect(sourceAdmittedCatalogAvailable([...common, ...oldRoles.map((type_id) => ({ type_id, definition_version: '0.3.5' }))])).toBe(false)
    for (const missing of nodes) expect(sourceAdmittedCatalogAvailable(nodes.filter((node) => node !== missing))).toBe(false)
    expect(sourceAdmittedCatalogAvailable(nodes.slice(1))).toBe(false)
    expect(sourceAdmittedCatalogAvailable(nodes.map((node) => ({ ...node, definition_version: '0.3.3' })))).toBe(false)
  })
})
