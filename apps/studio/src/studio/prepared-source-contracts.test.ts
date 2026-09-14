import { afterEach, describe, expect, it, vi } from 'vitest'
import { FetchStudioGateway } from './gateway'
import { StudioContractError } from './contracts'
import { parsePreparedSourceChooseRequest, parsePreparedSourceCreateRequest, parsePreparedSourceViewEnvelope,
  parsePreparedSourceFullRequest, parsePreparedSourceProcessingRequest, parsePreparedSourceOperationRequest, parsePreparedSourceOperationEnvelope, isSourcePreparationOperationNode } from './prepared-source-contracts'
import { admissionRunId, diagnosisRunId, preparedPreview, preparedSessionId, preparedView, preparedOperation } from './prepared-source.test-fixtures'
import { studioEnvelope } from './test-fixtures'

afterEach(() => vi.unstubAllGlobals())
const create = { contract_version: '0.3.4', project_path: 'D:\\Synthetic\\new.zniku', project_id: 'project.synthetic', project_name: 'Synthetic', source_path: 'D:\\Synthetic\\source.mkv' } as const
const choose = { contract_version: '0.3.4', project_session_id: preparedSessionId, expected_storage_revision: 1,
  run_id: diagnosisRunId, route: 'direct', target_frame_rate: null, external_format: 'mkv' } as const
function fullRequest() { const preview = preparedPreview(); return { contract_version: '0.3.4' as const, processing: preview.processing,
  project_session_id: preparedSessionId, expected_storage_revision: preview.storage_revision, preparation_run_id: admissionRunId,
  publication: { title: 'Synthetic', year: '2026', output_root: 'synthetic-output', overwrite: false, layout: 'direct' as const } } }

describe('准备路由严格使用 Python 0.3.4 wire', () => {
  it('自由图操作 DTO 必须绑定 attempt，不携带路线或准入 authority', () => {
    const request = { contract_version: '0.3.4', project_session_id: preparedSessionId, run_id: diagnosisRunId, node_run_id: admissionRunId }
    expect(parsePreparedSourceOperationRequest(request)).toEqual(request)
    expect(() => parsePreparedSourceOperationRequest({ ...request, node_run_id: undefined })).toThrow(StudioContractError)
    expect(parsePreparedSourceOperationEnvelope(preparedOperation())).toEqual(preparedOperation())
    expect(() => parsePreparedSourceOperationEnvelope({ ...preparedOperation(), route: 'direct', admission_status: 'completed' })).toThrow(StudioContractError)
  })
  it('改名及混图仅按实际exact类型挂载，不凭模板前缀或相似类型猜测', () => {
    expect(isSourcePreparationOperationNode({ type_id: 'zniku.source_preparation.video_repair.external.mov', definition_version: '0.3.4' })).toBe(true)
    expect(isSourcePreparationOperationNode({ type_id: 'zniku.source_preparation.diagnostics', definition_version: '0.3.4' })).toBe(true)
    expect(isSourcePreparationOperationNode({ type_id: 'zniku.source_preparation.video_repair.external.mov', definition_version: '0.3.3' })).toBe(false)
    expect(isSourcePreparationOperationNode({ type_id: 'zniku.source_preparation.fake', definition_version: '0.3.4' })).toBe(false)
    expect(isSourcePreparationOperationNode(undefined)).toBe(false)
  })
  it.each(['project_session_id', 'run_id', 'node_run_id'] as const)('operation-view 严格核对 %s，cancel不要求存储版本变化', async (field) => {
    const request = { contract_version: '0.3.4' as const, project_session_id: preparedSessionId, run_id: diagnosisRunId, node_run_id: admissionRunId }
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(preparedOperation())))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    expect(await gateway.inspectPreparedSourceOperation(request)).toEqual(preparedOperation())
    expect(fetcher.mock.calls[0]?.[0]).toBe('http://127.0.0.1:1/api/studio/templates/prepared-source/operation-view')
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify(preparedOperation({ [field]: '00000000-0000-4000-8000-000000000099' }))))
    await expect(gateway.inspectPreparedSourceOperation(request)).rejects.toThrow('不属于')
    const status = { ...studioEnvelope(), project_session_id: preparedSessionId }
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify(status)))
    expect(await gateway.cancelPreparedSourceOperation(request)).toEqual(status)
    expect(fetcher.mock.lastCall?.[0]).toBe('http://127.0.0.1:1/api/studio/templates/prepared-source/operation-cancel')
  })
  it('四个基本输入即可创建检查请求，不要求提前填写片名和年份', () => {
    expect(parsePreparedSourceCreateRequest(create)).toEqual(create)
    expect(parsePreparedSourceChooseRequest(choose)).toEqual(choose)
    expect(parsePreparedSourceFullRequest(fullRequest())).toEqual(fullRequest())
  })
  it.each([{ contract_version: '0.3.3' }, { shell: 'anything' }, { source_frame_count: 120 }, { data_parent_directory: 'elsewhere' }])('旧版本或未知创建字段失败关闭 %j', (patch) => {
    expect(() => parsePreparedSourceCreateRequest({ ...create, ...patch })).toThrow(StudioContractError)
  })
  it.each([{ route: 'force' }, { expected_storage_revision: -1 }, { target_frame_rate: '29.97' }, { external_format: 'avi' }])('准备选择不接受自造动作或近似率 %j', (patch) => {
    expect(() => parsePreparedSourceChooseRequest({ ...choose, ...patch })).toThrow(StudioContractError)
  })
  it('视图保留发现，但报告完成不等于工作源准入完成', () => {
    expect(parsePreparedSourceViewEnvelope(preparedView()).state).toBe('needs_choice')
    expect(() => parsePreparedSourceViewEnvelope(preparedView({ state: 'ready' }))).toThrow('准入状态不一致')
  })
  it('拒绝跨 attempt 进度或其他 Run 的交接', () => {
    expect(() => parsePreparedSourceViewEnvelope(preparedView({ current_node_run_id: diagnosisRunId,
      progress: { node_run_id: admissionRunId, current: 1, total: 2, fraction: .5, unit: 'frames', observed_at: '2026-09-14T01:00:00Z' } }))).toThrow('绑定')
    expect(() => parsePreparedSourceViewEnvelope(preparedView({ handoff: { run_id: admissionRunId, node_run_id: admissionRunId, node_id: 'repair' } }))).toThrow('绑定')
  })
  it('新处理配置沿用三种分章和可选 MR，不接受 TS 额外媒体判断', () => {
    expect(parsePreparedSourceProcessingRequest({ contract_version: '0.3.4', processing: preparedPreview().processing }).processing.mr).toEqual({ mode: 'off' })
    expect(() => parsePreparedSourceProcessingRequest({ contract_version: '0.3.4', processing: { ...preparedPreview().processing, admitted: true } })).toThrow(StudioContractError)
  })
  it('view 无需固定 CAS，但严格绑定 session/run 且仅发新只读业务路由', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(preparedView({ storage_revision: 9 }))))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    expect((await gateway.inspectPreparedSource({ contract_version: '0.3.4', project_session_id: preparedSessionId, run_id: diagnosisRunId })).storage_revision).toBe(9)
    expect(fetcher.mock.calls[0]?.[0]).toBe('http://127.0.0.1:1/api/studio/templates/prepared-source/view')
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify(preparedView({ run_id: admissionRunId }))))
    await expect(gateway.inspectPreparedSource({ contract_version: '0.3.4', project_session_id: preparedSessionId, run_id: diagnosisRunId })).rejects.toThrow('不属于')
  })
  it('choose 只改图返回 Status，不自行启动 Run，错误 CAS 拒绝', async () => {
    const status = { ...studioEnvelope(), project_session_id: preparedSessionId, storage_revision: 2 }
    const fetcher = vi.fn(async () => new Response(JSON.stringify(status)))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    await gateway.choosePreparedSource(choose)
    expect(fetcher).toHaveBeenCalledOnce()
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ...status, storage_revision: 1 })))
    await expect(gateway.choosePreparedSource(choose)).rejects.toThrow('存储版本不一致')
  })
  it('停止仅走独立signal路由，不要求CAS递增或伪造取消终态', async () => {
    const status = { ...studioEnvelope(), project_session_id: preparedSessionId, storage_revision: 1 }
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(status)))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    expect(await gateway.cancelPreparedSource({ contract_version: '0.3.4', project_session_id: preparedSessionId, run_id: diagnosisRunId })).toEqual(status)
    expect(fetcher.mock.calls[0]?.[0]).toBe('http://127.0.0.1:1/api/studio/templates/prepared-source/cancel')
    expect(fetcher).toHaveBeenCalledOnce()
  })
  it('失败保留中文原因和字段导航，不 fallback 到旧 profile', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ contract_version: '0.3.4', error: {
      code: 'E_PREPARED_SOURCE_STRATEGY_DISABLED', message: '该策略尚未启用', field_path: ['route'], related_run_ids: [diagnosisRunId],
    } }), { status: 422 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(new FetchStudioGateway('http://127.0.0.1:1').choosePreparedSource({ ...choose, route: 'builtin' })).rejects.toMatchObject({ code: 'E_PREPARED_SOURCE_STRATEGY_DISABLED', fieldPath: ['route'] })
    expect(fetcher).toHaveBeenCalledOnce()
  })
})
