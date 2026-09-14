/** ordinary exact 不借用旧 wire 成功；端点复用仍按请求版本验证身份及失败。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FetchStudioGateway } from './gateway'
import { parseColorPreparedSourceViewEnvelope } from './prepared-color-contracts'
import { parsePreparedSourceViewEnvelope } from './prepared-source-contracts'
import { WORK_SOURCE_VERSION, parseWorkViewEnvelope, parseWorkChooseRequest, isWorkPreparationOperationNode } from './working-source-contracts'
import { workView, workPreview } from './working-source.test-fixtures'
import { colorView, colorOperation } from './prepared-color.test-fixtures'
import { preparedSessionId, diagnosisRunId, admissionRunId } from './prepared-source.test-fixtures'
import { studioEnvelope } from './test-fixtures'
afterEach(() => vi.unstubAllGlobals())
const binding = { contract_version: WORK_SOURCE_VERSION, project_session_id: preparedSessionId, run_id: diagnosisRunId }
const choose = { ...binding, expected_storage_revision: 1, route: 'direct', target_frame_rate: '30/1', external_format: 'mkv', interpretation_policy: 'declared_only', confirmations: [] } as const
describe('普通工作源薄 Schema 与网关', () => {
  it('三个版本互不混读，保留后端三结果、变化与当前设置', () => {
    expect(parseWorkViewEnvelope(workView())).toEqual(workView())
    expect(() => parseWorkViewEnvelope(colorView())).toThrow()
    expect(() => parseColorPreparedSourceViewEnvelope(workView())).toThrow()
    expect(() => parsePreparedSourceViewEnvelope(workView())).toThrow()
  })
  it.each([{ force: true }, { confirmations: ['allow_anything'] }, { route: 'transcode_anything' }, { interpretation_policy: 'guess_bt709' }, { contract_version: '0.3.4' }])('参数闭合拒绝%j', (patch) => {
    expect(() => parseWorkChooseRequest({ ...choose, ...patch })).toThrow()
  })
  it('ready以及handoff/progress/retry必须真实身份一致，DTO不接受浏览器伪权威', () => {
    expect(() => parseWorkViewEnvelope(workView({ state: 'ready' }))).toThrow('完成状态')
    expect(() => parseWorkViewEnvelope(workView({ retry_target: { run_id: admissionRunId, node_id: 'actual-node', node_run_id: admissionRunId } }))).toThrow('身份')
    expect(() => parseWorkViewEnvelope({ ...workView(), admitted_by_ui: true })).toThrow()
  })
  it('operation只认新exact完整type；旧T1、旧external不能假冒普通节点', () => {
    const node = { type_id: 'zniku.source_preparation.video_prepare.frame_retime', definition_version: WORK_SOURCE_VERSION }
    expect(isWorkPreparationOperationNode(node)).toBe(true)
    expect(isWorkPreparationOperationNode({ ...node, type_id: 'zniku.source_preparation.video_prepare.t1' })).toBe(false)
    expect(isWorkPreparationOperationNode({ ...node, type_id: 'zniku.source_preparation.work_reference.external.mov' })).toBe(true)
    expect(isWorkPreparationOperationNode({ ...node, definition_version: '0.3.4-color.1' })).toBe(false)
  })
  it('choose传明确确认但不附加Run命令；CAS冲突拒绝', async () => {
    const status = { ...studioEnvelope(), project_session_id: preparedSessionId, storage_revision: 2 }
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(status)))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1').workingSource
    await gateway.choose(choose)
    expect(fetcher).toHaveBeenCalledOnce()
    expect(fetcher.mock.lastCall?.[0]).toContain('/prepared-color/choose')
    expect(JSON.parse(fetcher.mock.lastCall?.[1]?.body as string)).toEqual(choose)
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ...status, storage_revision: 1 })))
    await expect(gateway.choose(choose)).rejects.toThrow('存储版本')
  })
  it.each(['project_session_id', 'run_id', 'node_run_id'] as const)('operation丢弃迟到的%s，停止走独立新wire', async (field) => {
    const value = { ...colorOperation(), contract_version: WORK_SOURCE_VERSION }, request = { ...binding, node_run_id: admissionRunId }
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(value)))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1').workingSource
    await expect(gateway.inspectOperation(request)).resolves.toEqual(value)
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ...value, [field]: '00000000-0000-4000-8000-000000000099' })))
    await expect(gateway.inspectOperation(request)).rejects.toThrow('不属于')
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ...studioEnvelope(), project_session_id: preparedSessionId })))
    await gateway.cancelOperation(request)
    expect(fetcher.mock.lastCall?.[0]).toContain('/prepared-color/operation-cancel')
    expect(JSON.parse(fetcher.mock.lastCall?.[1]?.body as string).contract_version).toBe(WORK_SOURCE_VERSION)
  })
  it('processing/full-preview绑定真实请求，旧版本响应没有fallback', async () => {
    const preview = workPreview(), request = { contract_version: WORK_SOURCE_VERSION, processing: preview.processing }
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ ...request, status: 'pending_real_acceptance' })))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1').workingSource
    await gateway.processing(request)
    const full = { ...request, project_session_id: preview.project_session_id, expected_storage_revision: preview.storage_revision, preparation_run_id: preview.preparation_run_id,
      publication: { output_root: 'D:\\Synthetic', title: 'Synthetic', year: '2026', overwrite: false, layout: 'title_subdirectory' as const } }
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify(preview)))
    await expect(gateway.preview(full)).resolves.toEqual(preview)
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ...preview, preparation_run_id: diagnosisRunId })))
    await expect(gateway.preview(full)).rejects.toThrow('不一致')
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify(colorView())))
    await expect(gateway.inspect(binding)).rejects.toThrow()
  })
  it('失败仅按请求work版本解析，保留具体确认字段位置', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ contract_version: WORK_SOURCE_VERSION,
      error: { code: 'E_WORK_CONFIRM', message: '须明确确认', field_path: ['confirmations'], related_run_ids: [diagnosisRunId] } }), { status: 422 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(new FetchStudioGateway('http://127.0.0.1:1').workingSource.choose(choose)).rejects.toMatchObject({ fieldPath: ['confirmations'] })
    expect(fetcher).toHaveBeenCalledOnce()
  })
})
