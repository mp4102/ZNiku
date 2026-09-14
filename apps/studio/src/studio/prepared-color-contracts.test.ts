/** 新版本必须独立解析与绑定；没有色彩猜测或旧版本 fallback。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FetchStudioGateway } from './gateway'
import { parsePreparedSourceViewEnvelope, parsePreparedSourceChooseRequest } from './prepared-source-contracts'
import { COLOR_PREPARED_VERSION, parseColorPreparedSourceViewEnvelope, parseColorPreparedSourceChooseRequest,
  parseColorPreparedSourceOperationEnvelope, isColorPreparationOperationNode } from './prepared-color-contracts'
import { colorView, colorOperation, colorPreview } from './prepared-color.test-fixtures'
import { preparedView, preparedSessionId, diagnosisRunId, admissionRunId } from './prepared-source.test-fixtures'
import { studioEnvelope } from './test-fixtures'

afterEach(() => vi.unstubAllGlobals())
const binding = { contract_version: COLOR_PREPARED_VERSION, project_session_id: preparedSessionId, run_id: diagnosisRunId }
const choose = { ...binding, expected_storage_revision: 1, route: 'direct', target_frame_rate: '30/1', external_format: 'mkv', interpretation_policy: 'declared_only' } as const
describe('0.3.4-color.1 工作解释 wire', () => {
  it('保留新的原始发现和后端结论，旧新响应双向拒绝交叉解析', () => {
    expect(parseColorPreparedSourceViewEnvelope(colorView())).toEqual(colorView())
    expect(() => parsePreparedSourceViewEnvelope(colorView())).toThrow()
    expect(() => parseColorPreparedSourceViewEnvelope(preparedView())).toThrow()
    expect(() => parsePreparedSourceChooseRequest({ ...choose, contract_version: '0.3.4' })).toThrow()
  })
  it.each(['force', 'bt709', true, null])('解释策略不接受 Schema 外值 %s', (policy) => {
    expect(() => parseColorPreparedSourceChooseRequest({ ...choose, interpretation_policy: policy })).toThrow()
  })
  it('显式策略只作为闭合参数；不能向响应塞入浏览器准入authority', () => {
    expect(parseColorPreparedSourceChooseRequest({ ...choose, interpretation_policy: 'operator_confirmed_bt709_limited_left' }).interpretation_policy).toBe('operator_confirmed_bt709_limited_left')
    expect(() => parseColorPreparedSourceViewEnvelope({ ...colorView(), force_admitted: true })).toThrow()
    expect(() => parseColorPreparedSourceViewEnvelope(colorView({ state: 'ready' }))).toThrow('准入状态')
    expect(() => parseColorPreparedSourceOperationEnvelope({ ...colorOperation(), working_signal_basis: 'fake' })).toThrow()
  })
  it('exact操作匹配不把旧版本升级，也不凭相似节点名挂载', () => {
    const node = { type_id: 'zniku.source_preparation.diagnostics', definition_version: COLOR_PREPARED_VERSION }
    expect(isColorPreparationOperationNode(node)).toBe(true)
    expect(isColorPreparationOperationNode({ ...node, definition_version: '0.3.4' })).toBe(false)
    expect(isColorPreparationOperationNode({ ...node, type_id: `${node.type_id}.fake` })).toBe(false)
  })
  it.each(['project_session_id', 'run_id', 'node_run_id'] as const)('新操作路由拒绝迟到的 %s', async (field) => {
    const request = { ...binding, node_run_id: admissionRunId }
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(colorOperation())))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    await expect(gateway.inspectColorPreparedSourceOperation(request)).resolves.toEqual(colorOperation())
    expect(fetcher.mock.lastCall?.[0]).toContain('/prepared-color/operation-view')
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify(colorOperation({ [field]: '00000000-0000-4000-8000-000000000099' }))))
    await expect(gateway.inspectColorPreparedSourceOperation(request)).rejects.toThrow('不属于')
  })
  it('choose显式发送策略，只写Graph不发送Run，CAS错拒绝', async () => {
    const status = { ...studioEnvelope(), project_session_id: preparedSessionId, storage_revision: 2 }
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(status)))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    await gateway.chooseColorPreparedSource(choose)
    expect(fetcher).toHaveBeenCalledOnce()
    expect(fetcher.mock.lastCall?.[0]).toContain('/prepared-color/choose')
    expect(JSON.parse(fetcher.mock.lastCall?.[1]?.body as string)).toEqual(choose)
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ...status, storage_revision: 1 })))
    await expect(gateway.chooseColorPreparedSource(choose)).rejects.toThrow('存储版本')
  })
  it('view/session绑定、两个cancel与processing仅访问独立新版路由', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(colorView())))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    await expect(gateway.inspectColorPreparedSource(binding)).resolves.toEqual(colorView())
    expect(fetcher.mock.lastCall?.[0]).toContain('/prepared-color/view')
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify(colorView({ run_id: admissionRunId }))))
    await expect(gateway.inspectColorPreparedSource(binding)).rejects.toThrow('不属于')
    for (const operation of [false, true]) {
      fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ...studioEnvelope(), project_session_id: preparedSessionId })))
      if (operation) await gateway.cancelColorPreparedSourceOperation({ ...binding, node_run_id: admissionRunId })
      else await gateway.cancelColorPreparedSource(binding)
      expect(fetcher.mock.lastCall?.[0]).toContain(operation ? '/prepared-color/operation-cancel' : '/prepared-color/cancel')
    }
    const processing = { contract_version: COLOR_PREPARED_VERSION, processing: colorPreview().processing }
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ...processing, status: 'pending_real_acceptance' })))
    await gateway.previewColorPreparedSourceProcessing(processing)
    expect(fetcher.mock.lastCall?.[0]).toContain('/prepared-color/processing-preview')
  })
  it('新版失败保留policy字段位置，不回退旧严格合同', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ contract_version: COLOR_PREPARED_VERSION,
      error: { code: 'E_SYNTHETIC_COLOR_REQUIRED', message: '需要明确工作解释', field_path: ['interpretation_policy'], related_run_ids: [diagnosisRunId] } }), { status: 422 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(new FetchStudioGateway('http://127.0.0.1:1').chooseColorPreparedSource(choose)).rejects.toMatchObject({ fieldPath: ['interpretation_policy'] })
    expect(fetcher).toHaveBeenCalledOnce()
  })
})
