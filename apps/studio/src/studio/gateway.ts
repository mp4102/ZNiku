/**
 * 提供 Studio 到 loopback Project Service 0.3.0 的分层网关。
 *
 * 网关只发送结构化 JSON，并按 endpoint 调用 Python Schema 派生的解析器。status、Run detail、日志和
 * readiness 使用不同资源通道，网络或合同失败不会清空其他通道最后一次可信数据。
 */

import {
  parseAvEnhanceV27PublicationPreviewEnvelope,
  parseAvEnhanceV27PublicationPreviewRequest,
  parseAvEnhanceV27TemplatePreviewEnvelope,
  parseAvEnhanceV27TemplatePreviewRequest,
  parseExternalHandoffReadiness,
  parseNodeLogEnvelope,
  parsePresentationCatalogEnvelope,
  parseRerunPreviewEnvelope,
  parseRunDetailEnvelope,
  parseRunSummaryPageEnvelope,
  parseStatusEnvelope,
  parseStudioCommand,
  StudioContractError,
  type ExternalHandoffReadiness,
  type AvEnhanceV27TemplatePreviewEnvelope,
  type AvEnhanceV27TemplatePreviewRequestWire,
  type AvEnhanceV27PublicationPreviewEnvelope,
  type AvEnhanceV27PublicationPreviewRequestWire,
  type NodeLogEnvelope,
  type PresentationCatalogEnvelopeWire,
  type RerunPreviewEnvelope,
  type RerunPreviewRequest,
  type RunDetailEnvelope,
  type RunSummaryPageEnvelope,
  type StatusEnvelope,
  type StudioCommand,
  type StudioServiceError,
} from './contracts'
import {
  overlapProcessingMatches, parseOverlapFailure, parseOverlapFullEnvelope, parseOverlapFullRequest,
  parseOverlapProcessingEnvelope, parseOverlapProcessingRequest,
  type OverlapFailureEnvelope, type OverlapFullEnvelope, type OverlapFullRequest, type OverlapProcessingEnvelope, type OverlapProcessingRequest,
} from './chapter-overlap-contracts'
import {
  sourceAlignedProcessingMatches, parseSourceAlignedFailure, parseSourceAlignedFullEnvelope, parseSourceAlignedFullRequest,
  parseSourceAlignedProcessingEnvelope, parseSourceAlignedProcessingRequest,
  type SourceAlignedFullEnvelope, type SourceAlignedFullRequest, type SourceAlignedProcessingEnvelope, type SourceAlignedProcessingRequest,
} from './source-aligned-contracts'
import {
  parsePreparedSourceCreateRequest, parsePreparedSourceChooseRequest, parsePreparedSourceViewRequest,
  parsePreparedSourceViewEnvelope, parsePreparedSourceFailure, parsePreparedSourceProcessingRequest,
  parsePreparedSourceProcessingEnvelope, parsePreparedSourceFullRequest, parsePreparedSourceFullEnvelope,
  preparedSourceProcessingMatches,
  parsePreparedSourceOperationRequest, parsePreparedSourceOperationEnvelope,
  type PreparedSourceOperationRequest, type PreparedSourceOperationEnvelope,
  type PreparedSourceCreateRequest, type PreparedSourceChooseRequest, type PreparedSourceViewRequest,
  type PreparedSourceViewEnvelope, type PreparedSourceProcessingRequest, type PreparedSourceProcessingEnvelope,
  type PreparedSourceFullRequest, type PreparedSourceFullEnvelope,
} from './prepared-source-contracts'

import { createWorkingSourceGateway, type WorkingSourceGateway } from './working-source-gateway'
import { parseWorkFailure } from './working-source-contracts'

export interface StudioGateway {
  readonly workingSource?: WorkingSourceGateway
  createColorPreparedSource?(request: colorPrepared.ColorPreparedSourceCreateRequest): Promise<StatusEnvelope>
  inspectColorPreparedSource?(request: colorPrepared.ColorPreparedSourceViewRequest): Promise<colorPrepared.ColorPreparedSourceViewEnvelope>
  cancelColorPreparedSource?(request: colorPrepared.ColorPreparedSourceViewRequest): Promise<StatusEnvelope>
  inspectColorPreparedSourceOperation?(request: colorPrepared.ColorPreparedSourceOperationRequest): Promise<colorPrepared.ColorPreparedSourceOperationEnvelope>
  cancelColorPreparedSourceOperation?(request: colorPrepared.ColorPreparedSourceOperationRequest): Promise<StatusEnvelope>
  chooseColorPreparedSource?(request: colorPrepared.ColorPreparedSourceChooseRequest): Promise<StatusEnvelope>
  previewColorPreparedSourceProcessing?(request: colorPrepared.ColorPreparedSourceProcessingRequest): Promise<colorPrepared.ColorPreparedSourceProcessingEnvelope>
  previewColorPreparedSource?(request: colorPrepared.ColorPreparedSourceFullRequest): Promise<colorPrepared.ColorPreparedSourceFullEnvelope>
  expandColorPreparedSource?(request: colorPrepared.ColorPreparedSourceFullRequest): Promise<StatusEnvelope>
  createPreparedSource?(request: PreparedSourceCreateRequest): Promise<StatusEnvelope>
  inspectPreparedSource?(request: PreparedSourceViewRequest): Promise<PreparedSourceViewEnvelope>
  cancelPreparedSource?(request: PreparedSourceViewRequest): Promise<StatusEnvelope>
  inspectPreparedSourceOperation?(request: PreparedSourceOperationRequest): Promise<PreparedSourceOperationEnvelope>
  cancelPreparedSourceOperation?(request: PreparedSourceOperationRequest): Promise<StatusEnvelope>
  choosePreparedSource?(request: PreparedSourceChooseRequest): Promise<StatusEnvelope>
  previewPreparedSourceProcessing?(request: PreparedSourceProcessingRequest): Promise<PreparedSourceProcessingEnvelope>
  previewPreparedSource?(request: PreparedSourceFullRequest): Promise<PreparedSourceFullEnvelope>
  expandPreparedSource?(request: PreparedSourceFullRequest): Promise<StatusEnvelope>
  previewSourceAlignedProcessing?(request: SourceAlignedProcessingRequest): Promise<SourceAlignedProcessingEnvelope>
  previewSourceAligned?(request: SourceAlignedFullRequest): Promise<SourceAlignedFullEnvelope>
  expandSourceAligned?(request: SourceAlignedFullRequest): Promise<StatusEnvelope>
  previewOverlapProcessing?(request: OverlapProcessingRequest): Promise<OverlapProcessingEnvelope>
  previewOverlap?(request: OverlapFullRequest): Promise<OverlapFullEnvelope>
  expandOverlap?(request: OverlapFullRequest): Promise<StatusEnvelope>
  /** 正式 gateway 必须提供无媒体 I/O 的输出检查；缺失的旧测试 double/服务不能绕过该检查。 */
  previewAvEnhanceV27Publication?(request: AvEnhanceV27PublicationPreviewRequestWire): Promise<AvEnhanceV27PublicationPreviewEnvelope>
  /** 可选仅用于兼容测试 double；正式 Fetch gateway 始终实现独立展示目录读取。 */
  inspectPresentations?(): Promise<PresentationCatalogEnvelopeWire>
  inspect(viewRunId?: string | null): Promise<StatusEnvelope>
  listRuns(cursor?: string | null, limit?: number): Promise<RunSummaryPageEnvelope>
  inspectRun(runId: string): Promise<RunDetailEnvelope>
  previewRerun?(request: RerunPreviewRequest): Promise<RerunPreviewEnvelope>
  inspectLog(runId: string, nodeRunId: string): Promise<NodeLogEnvelope>
  inspectReadiness(
    runId: string,
    nodeRunId: string,
    probe: boolean,
  ): Promise<ExternalHandoffReadiness>
  previewAvEnhanceV27(
    request: AvEnhanceV27TemplatePreviewRequestWire,
  ): Promise<AvEnhanceV27TemplatePreviewEnvelope>
  command(command: StudioCommand): Promise<StatusEnvelope>
}

export class StudioGatewayError extends Error {
  readonly code: string | null
  /** Python 的原始 message，仅作纯文本说明，不解析成路径或动作权限。 */
  readonly serviceMessage: string | null
  readonly relatedRunIds: ReadonlyArray<string>
  readonly httpStatus: number | null
  readonly fieldPath: ReadonlyArray<string | number>

  constructor(
    message: string,
    options: {
      readonly code?: string | null
      readonly serviceMessage?: string | null
      readonly relatedRunIds?: ReadonlyArray<string>
      readonly httpStatus?: number | null
      readonly fieldPath?: ReadonlyArray<string | number>
    } = {},
  ) {
    super(message)
    this.name = 'StudioGatewayError'
    this.code = options.code ?? null
    this.serviceMessage = options.serviceMessage ?? null
    this.relatedRunIds = options.relatedRunIds ?? []
    this.httpStatus = options.httpStatus ?? null
    this.fieldPath = options.fieldPath ?? []
  }
}

function parseErrorEnvelope(value: unknown): StudioServiceError {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new StudioGatewayError('Project Service 错误响应不是 JSON object')
  }
  const top = Object.keys(value)
  if (top.length !== 1 || top[0] !== 'error') {
    throw new StudioGatewayError('Project Service 错误响应必须只包含 error')
  }
  const error = (value as { readonly error?: unknown }).error
  if (typeof error !== 'object' || error === null || Array.isArray(error)) {
    throw new StudioGatewayError('Project Service error 必须是 object')
  }
  const keys = Object.keys(error).sort()
  if (
    keys.length !== 3 ||
    keys[0] !== 'code' ||
    keys[1] !== 'message' ||
    keys[2] !== 'related_run_ids'
  ) {
    throw new StudioGatewayError(
      'Project Service error 只能包含 code、message 与 related_run_ids',
    )
  }
  const { code, message, related_run_ids: relatedRunIds } = error as {
    readonly code?: unknown
    readonly message?: unknown
    readonly related_run_ids?: unknown
  }
  if (
    typeof code !== 'string' ||
    !code ||
    typeof message !== 'string' ||
    !message ||
    !Array.isArray(relatedRunIds) ||
    relatedRunIds.some((item) => typeof item !== 'string' || !item)
  ) {
    throw new StudioGatewayError(
      'Project Service error code/message/related_run_ids 字段类型无效',
    )
  }
  return { code, message, related_run_ids: relatedRunIds as string[] }
}

declare global {
  interface Window {
    __ZNIKU_STUDIO_API_BASE__?: string
  }
}

type Parser<T> = (value: unknown) => T
import * as colorPrepared from './prepared-color-contracts'

export class FetchStudioGateway implements StudioGateway {
  readonly workingSource = createWorkingSourceGateway((action, payload, parser) => this.request(`/api/studio/templates/prepared-color/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }, parser, 'working-source'))
  constructor(
    private readonly baseUrl =
      window.__ZNIKU_STUDIO_API_BASE__ ?? 'http://127.0.0.1:18765',
  ) {}

  private colorPreparedRequest<T>(action: string, payload: unknown, parser: Parser<T>): Promise<T> {
    return this.request(`/api/studio/templates/prepared-color/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }, parser, 'prepared-color')
  }

  async createColorPreparedSource(request: colorPrepared.ColorPreparedSourceCreateRequest): Promise<StatusEnvelope> {
    const payload = colorPrepared.parseColorPreparedSourceCreateRequest(request)
    const status = await this.colorPreparedRequest('create', payload, parseStatusEnvelope)
    if (!status.project_session_id || status.project_path !== payload.project_path ||
        status.snapshot?.project.project_id !== payload.project_id || status.snapshot.project.name !== payload.project_name) {
      throw new StudioContractError('素材检查工程创建响应与请求的工程身份不一致')
    }
    return status
  }

  async inspectColorPreparedSource(request: colorPrepared.ColorPreparedSourceViewRequest): Promise<colorPrepared.ColorPreparedSourceViewEnvelope> {
    const payload = colorPrepared.parseColorPreparedSourceViewRequest(request)
    const view = await this.colorPreparedRequest('view', payload, colorPrepared.parseColorPreparedSourceViewEnvelope)
    if (view.project_session_id !== payload.project_session_id || view.run_id !== payload.run_id) throw new StudioContractError('素材检查视图不属于请求的工程或运行记录')
    return view
  }

  async chooseColorPreparedSource(request: colorPrepared.ColorPreparedSourceChooseRequest): Promise<StatusEnvelope> {
    const payload = colorPrepared.parseColorPreparedSourceChooseRequest(request)
    const status = await this.colorPreparedRequest('choose', payload, parseStatusEnvelope)
    if (status.project_session_id !== payload.project_session_id || status.storage_revision !== payload.expected_storage_revision + 1) throw new StudioContractError('素材准备选择响应与当前工程或存储版本不一致')
    return status
  }

  async inspectColorPreparedSourceOperation(request: colorPrepared.ColorPreparedSourceOperationRequest): Promise<colorPrepared.ColorPreparedSourceOperationEnvelope> {
    const payload = colorPrepared.parseColorPreparedSourceOperationRequest(request)
    const view = await this.colorPreparedRequest('operation-view', payload, colorPrepared.parseColorPreparedSourceOperationEnvelope)
    if (view.project_session_id !== payload.project_session_id || view.run_id !== payload.run_id || view.node_run_id !== payload.node_run_id) throw new StudioContractError('素材操作视图不属于请求的工程、Run 或节点 attempt')
    return view
  }

  async cancelColorPreparedSourceOperation(request: colorPrepared.ColorPreparedSourceOperationRequest): Promise<StatusEnvelope> {
    const payload = colorPrepared.parseColorPreparedSourceOperationRequest(request)
    const status = await this.colorPreparedRequest('operation-cancel', payload, parseStatusEnvelope)
    if (status.project_session_id !== payload.project_session_id) throw new StudioContractError('素材操作停止响应不属于当前工程')
    return status
  }

  async cancelColorPreparedSource(request: colorPrepared.ColorPreparedSourceViewRequest): Promise<StatusEnvelope> {
    const payload = colorPrepared.parseColorPreparedSourceViewRequest(request)
    const status = await this.colorPreparedRequest('cancel', payload, parseStatusEnvelope)
    // 只发送停止信号，后台收敛前不能假定 Run 已终止或 storage revision 已递增。
    if (status.project_session_id !== payload.project_session_id) throw new StudioContractError('停止请求的响应不属于当前工程')
    return status
  }

  async previewColorPreparedSourceProcessing(request: colorPrepared.ColorPreparedSourceProcessingRequest): Promise<colorPrepared.ColorPreparedSourceProcessingEnvelope> {
    const payload = colorPrepared.parseColorPreparedSourceProcessingRequest(request)
    const preview = await this.colorPreparedRequest('processing-preview', payload, colorPrepared.parseColorPreparedSourceProcessingEnvelope)
    if (!preparedSourceProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('工作源处理设置回显与请求不一致')
    return preview
  }

  async previewColorPreparedSource(request: colorPrepared.ColorPreparedSourceFullRequest): Promise<colorPrepared.ColorPreparedSourceFullEnvelope> {
    const payload = colorPrepared.parseColorPreparedSourceFullRequest(request)
    const preview = await this.colorPreparedRequest('full-preview', payload, colorPrepared.parseColorPreparedSourceFullEnvelope)
    if (preview.project_session_id !== payload.project_session_id || preview.storage_revision !== payload.expected_storage_revision ||
        preview.preparation_run_id !== payload.preparation_run_id || !preparedSourceProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('工作源预览与工程、存储版本、准入记录或处理设置不一致')
    return preview
  }

  async expandColorPreparedSource(request: colorPrepared.ColorPreparedSourceFullRequest): Promise<StatusEnvelope> {
    const payload = colorPrepared.parseColorPreparedSourceFullRequest(request)
    const status = await this.colorPreparedRequest('expand', payload, parseStatusEnvelope)
    if (status.project_session_id !== payload.project_session_id || status.storage_revision !== payload.expected_storage_revision + 1) throw new StudioContractError('工作源展开响应与当前工程或存储版本不一致')
    return status
  }

  private preparedRequest<T>(action: string, payload: unknown, parser: Parser<T>): Promise<T> {
    return this.request(`/api/studio/templates/prepared-source/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }, parser, 'prepared-source')
  }

  async createPreparedSource(request: PreparedSourceCreateRequest): Promise<StatusEnvelope> {
    const payload = parsePreparedSourceCreateRequest(request)
    const status = await this.preparedRequest('create', payload, parseStatusEnvelope)
    if (!status.project_session_id || status.project_path !== payload.project_path ||
        status.snapshot?.project.project_id !== payload.project_id || status.snapshot.project.name !== payload.project_name) {
      throw new StudioContractError('素材检查工程创建响应与请求的工程身份不一致')
    }
    return status
  }

  async inspectPreparedSource(request: PreparedSourceViewRequest): Promise<PreparedSourceViewEnvelope> {
    const payload = parsePreparedSourceViewRequest(request)
    const view = await this.preparedRequest('view', payload, parsePreparedSourceViewEnvelope)
    if (view.project_session_id !== payload.project_session_id || view.run_id !== payload.run_id) throw new StudioContractError('素材检查视图不属于请求的工程或运行记录')
    return view
  }

  async choosePreparedSource(request: PreparedSourceChooseRequest): Promise<StatusEnvelope> {
    const payload = parsePreparedSourceChooseRequest(request)
    const status = await this.preparedRequest('choose', payload, parseStatusEnvelope)
    if (status.project_session_id !== payload.project_session_id || status.storage_revision !== payload.expected_storage_revision + 1) throw new StudioContractError('素材准备选择响应与当前工程或存储版本不一致')
    return status
  }

  async inspectPreparedSourceOperation(request: PreparedSourceOperationRequest): Promise<PreparedSourceOperationEnvelope> {
    const payload = parsePreparedSourceOperationRequest(request)
    const view = await this.preparedRequest('operation-view', payload, parsePreparedSourceOperationEnvelope)
    if (view.project_session_id !== payload.project_session_id || view.run_id !== payload.run_id || view.node_run_id !== payload.node_run_id) throw new StudioContractError('素材操作视图不属于请求的工程、Run 或节点 attempt')
    return view
  }

  async cancelPreparedSourceOperation(request: PreparedSourceOperationRequest): Promise<StatusEnvelope> {
    const payload = parsePreparedSourceOperationRequest(request)
    const status = await this.preparedRequest('operation-cancel', payload, parseStatusEnvelope)
    if (status.project_session_id !== payload.project_session_id) throw new StudioContractError('素材操作停止响应不属于当前工程')
    return status
  }

  async cancelPreparedSource(request: PreparedSourceViewRequest): Promise<StatusEnvelope> {
    const payload = parsePreparedSourceViewRequest(request)
    const status = await this.preparedRequest('cancel', payload, parseStatusEnvelope)
    // 只发送停止信号，后台收敛前不能假定 Run 已终止或 storage revision 已递增。
    if (status.project_session_id !== payload.project_session_id) throw new StudioContractError('停止请求的响应不属于当前工程')
    return status
  }

  async previewPreparedSourceProcessing(request: PreparedSourceProcessingRequest): Promise<PreparedSourceProcessingEnvelope> {
    const payload = parsePreparedSourceProcessingRequest(request)
    const preview = await this.preparedRequest('processing-preview', payload, parsePreparedSourceProcessingEnvelope)
    if (!preparedSourceProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('工作源处理设置回显与请求不一致')
    return preview
  }

  async previewPreparedSource(request: PreparedSourceFullRequest): Promise<PreparedSourceFullEnvelope> {
    const payload = parsePreparedSourceFullRequest(request)
    const preview = await this.preparedRequest('full-preview', payload, parsePreparedSourceFullEnvelope)
    if (preview.project_session_id !== payload.project_session_id || preview.storage_revision !== payload.expected_storage_revision ||
        preview.preparation_run_id !== payload.preparation_run_id || !preparedSourceProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('工作源预览与工程、存储版本、准入记录或处理设置不一致')
    return preview
  }

  async expandPreparedSource(request: PreparedSourceFullRequest): Promise<StatusEnvelope> {
    const payload = parsePreparedSourceFullRequest(request)
    const status = await this.preparedRequest('expand', payload, parseStatusEnvelope)
    if (status.project_session_id !== payload.project_session_id || status.storage_revision !== payload.expected_storage_revision + 1) throw new StudioContractError('工作源展开响应与当前工程或存储版本不一致')
    return status
  }

  async previewSourceAlignedProcessing(request: SourceAlignedProcessingRequest): Promise<SourceAlignedProcessingEnvelope> {
    const payload = parseSourceAlignedProcessingRequest(request)
    const preview = await this.request('/api/studio/templates/source-aligned-overlap/processing-preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }, parseSourceAlignedProcessingEnvelope, 'source-aligned')
    if (!sourceAlignedProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('原片规划设置检查回显与当前请求不一致')
    return preview
  }

  async previewSourceAligned(request: SourceAlignedFullRequest): Promise<SourceAlignedFullEnvelope> {
    const payload = parseSourceAlignedFullRequest(request)
    const preview = await this.request('/api/studio/templates/source-aligned-overlap/full-preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }, parseSourceAlignedFullEnvelope, 'source-aligned')
    if (preview.project_session_id !== request.project_session_id || preview.storage_revision !== request.expected_storage_revision ||
        preview.preparation_run_id !== request.preparation_run_id || !sourceAlignedProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('原片规划预览与当前工程、存储版本、分析记录或处理设置不一致')
    return preview
  }

  async expandSourceAligned(request: SourceAlignedFullRequest): Promise<StatusEnvelope> {
    const payload = parseSourceAlignedFullRequest(request)
    const status = await this.request('/api/studio/templates/source-aligned-overlap/expand', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }, parseStatusEnvelope, 'source-aligned')
    if (status.project_session_id !== request.project_session_id || status.storage_revision !== request.expected_storage_revision + 1) throw new StudioContractError('原片规划展开响应与当前工程或存储版本不一致')
    return status
  }

  async previewOverlapProcessing(request: OverlapProcessingRequest): Promise<OverlapProcessingEnvelope> {
    const payload = parseOverlapProcessingRequest(request)
    const preview = await this.request('/api/studio/templates/chapter-overlap-fi/processing-preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }, parseOverlapProcessingEnvelope, true)
    if (!overlapProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('设置检查回显与当前请求不一致')
    return preview
  }

  async previewOverlap(request: OverlapFullRequest): Promise<OverlapFullEnvelope> {
    const payload = parseOverlapFullRequest(request)
    const preview = await this.request('/api/studio/templates/chapter-overlap-fi/full-preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }, parseOverlapFullEnvelope, true)
    if (preview.project_session_id !== request.project_session_id || preview.storage_revision !== request.expected_storage_revision ||
        preview.preparation_run_id !== request.preparation_run_id || !overlapProcessingMatches(payload.processing, preview.processing)) throw new StudioContractError('重叠补帧预览与当前工程、存储版本、分析记录或处理设置不一致')
    return preview
  }

  async expandOverlap(request: OverlapFullRequest): Promise<StatusEnvelope> {
    const payload = parseOverlapFullRequest(request)
    const status = await this.request('/api/studio/templates/chapter-overlap-fi/expand', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }, parseStatusEnvelope, true)
    if (status.project_session_id !== request.project_session_id || status.storage_revision !== request.expected_storage_revision + 1) {
      throw new StudioContractError('重叠补帧展开响应与当前工程或存储版本不一致')
    }
    return status
  }

  async inspectPresentations(): Promise<PresentationCatalogEnvelopeWire> {
    return this.request(
      '/api/studio/presentations',
      { method: 'GET' },
      parsePresentationCatalogEnvelope,
    )
  }

  async inspect(viewRunId?: string | null): Promise<StatusEnvelope> {
    const query = new URLSearchParams()
    if (viewRunId) query.set('view_run_id', viewRunId)
    return this.request(
      `/api/studio/status${query.size ? `?${query.toString()}` : ''}`,
      { method: 'GET' },
      parseStatusEnvelope,
    )
  }

  async listRuns(cursor?: string | null, limit = 20): Promise<RunSummaryPageEnvelope> {
    const query = new URLSearchParams({ limit: String(limit) })
    if (cursor) query.set('cursor', cursor)
    return this.request(
      `/api/studio/runs?${query.toString()}`,
      { method: 'GET' },
      parseRunSummaryPageEnvelope,
    )
  }

  async inspectRun(runId: string): Promise<RunDetailEnvelope> {
    const detail = await this.request(
      `/api/studio/runs/${encodeURIComponent(runId)}`,
      { method: 'GET' },
      parseRunDetailEnvelope,
    )
    if (detail.run.run_id !== runId) {
      throw new StudioContractError('Run detail 的 run_id 与请求资源不一致')
    }
    return detail
  }

  async inspectLog(runId: string, nodeRunId: string): Promise<NodeLogEnvelope> {
    const envelope = await this.request(
      `/api/studio/runs/${encodeURIComponent(runId)}/node-runs/${encodeURIComponent(nodeRunId)}/logs`,
      { method: 'GET' },
      parseNodeLogEnvelope,
    )
    if (envelope.run_id !== runId || envelope.log.node_run_id !== nodeRunId) {
      throw new StudioContractError('Node log 的 run_id/node_run_id 与请求资源不一致')
    }
    return envelope
  }

  async previewRerun(request: RerunPreviewRequest): Promise<RerunPreviewEnvelope> {
    const payload = parseStudioCommand(request)
    const preview = await this.request('/api/studio/rerun-preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }, parseRerunPreviewEnvelope)
    if (preview.run_id !== request.run_id || preview.node_id !== request.node_id ||
        preview.project_session_id !== request.project_session_id || preview.storage_revision !== request.expected_storage_revision) {
      throw new StudioContractError('重跑影响预览与请求工程、Run 或存储版本不一致')
    }
    return preview
  }

  async inspectReadiness(
    runId: string,
    nodeRunId: string,
    probe: boolean,
  ): Promise<ExternalHandoffReadiness> {
    const readiness = await this.request(
      `/api/studio/runs/${encodeURIComponent(runId)}/node-runs/${encodeURIComponent(nodeRunId)}/handoff-readiness?probe=${probe ? 'true' : 'false'}`,
      { method: 'GET' },
      parseExternalHandoffReadiness,
    )
    if (readiness.run_id !== runId || readiness.node_run_id !== nodeRunId) {
      throw new StudioContractError(
        'handoff readiness 的 run_id/node_run_id 与请求资源不一致',
      )
    }
    return readiness
  }

  async previewAvEnhanceV27(
    request: AvEnhanceV27TemplatePreviewRequestWire,
  ): Promise<AvEnhanceV27TemplatePreviewEnvelope> {
    const payload = parseAvEnhanceV27TemplatePreviewRequest(request)
    const preview = await this.request(
      '/api/studio/templates/av-enhance-v27/preview',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
      parseAvEnhanceV27TemplatePreviewEnvelope,
    )
    const expectedPhase = payload.action === 'prepare' ? 'preparation' : 'expanded'
    if (preview.phase !== expectedPhase) {
      throw new StudioContractError(
        `AVEnhanceFlow v2.7 preview phase 与 ${payload.action} action 不一致`,
      )
    }
    return preview
  }

  async previewAvEnhanceV27Publication(request: AvEnhanceV27PublicationPreviewRequestWire): Promise<AvEnhanceV27PublicationPreviewEnvelope> {
    const payload = parseAvEnhanceV27PublicationPreviewRequest(request)
    const preview = await this.request(
      '/api/studio/templates/av-enhance-v27/publication-preview',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) },
      parseAvEnhanceV27PublicationPreviewEnvelope,
    )
    if (preview.layout !== (payload.request.layout ?? 'direct')) {
      throw new StudioContractError('输出检查的 layout 与当前请求不一致')
    }
    return preview
  }

  async command(command: StudioCommand): Promise<StatusEnvelope> {
    const payload = parseStudioCommand(command)
    return this.request(
      payload.operation === 'save_project' ? '/api/studio/graph-save' : '/api/studio/command',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
      parseStatusEnvelope,
    )
  }

  private async request<T>(path: string, init: RequestInit, parser: Parser<T>, overlap: boolean | 'source-aligned' | 'prepared-source' | 'prepared-color' | 'working-source' = false): Promise<T> {
    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, init)
    } catch (error) {
      throw new StudioGatewayError(
        `本地 ZNIKU Project Service 不可用：${error instanceof Error ? error.message : 'network error'}`,
      )
    }

    let value: unknown
    try {
      value = await response.json()
    } catch {
      throw new StudioGatewayError(`Project Service 返回非 JSON 响应（HTTP ${response.status}）`, {
        httpStatus: response.status,
      })
    }

    if (!response.ok) {
      const error = overlap === 'working-source' ? parseWorkFailure(value).error : overlap === 'prepared-color' ? colorPrepared.parseColorPreparedSourceFailure(value).error : overlap === 'prepared-source' ? parsePreparedSourceFailure(value).error : overlap === 'source-aligned' ? parseSourceAlignedFailure(value).error : overlap ? parseOverlapFailure(value).error : parseErrorEnvelope(value)
      throw new StudioGatewayError(`Project Service command 失败：${error.code}: ${error.message}`, {
        code: error.code,
        serviceMessage: error.message,
        relatedRunIds: error.related_run_ids,
        httpStatus: response.status,
        fieldPath: overlap ? (error as OverlapFailureEnvelope['error']).field_path : [],
      })
    }

    try {
      return parser(value)
    } catch (error) {
      if (error instanceof StudioContractError) throw error
      throw new StudioGatewayError('Project Service 响应解析失败', {
        httpStatus: response.status,
      })
    }
  }
}

export function createStudioGateway(): StudioGateway {
  return new FetchStudioGateway()
}
