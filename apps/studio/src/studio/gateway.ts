/**
 * 提供 Studio 到 loopback Project Service 0.3.0 的分层网关。
 *
 * 网关只发送结构化 JSON，并按 endpoint 调用 Python Schema 派生的解析器。status、Run detail、日志和
 * readiness 使用不同资源通道，网络或合同失败不会清空其他通道最后一次可信数据。
 */

import {
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

export interface StudioGateway {
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
  readonly relatedRunIds: ReadonlyArray<string>
  readonly httpStatus: number | null

  constructor(
    message: string,
    options: {
      readonly code?: string | null
      readonly relatedRunIds?: ReadonlyArray<string>
      readonly httpStatus?: number | null
    } = {},
  ) {
    super(message)
    this.name = 'StudioGatewayError'
    this.code = options.code ?? null
    this.relatedRunIds = options.relatedRunIds ?? []
    this.httpStatus = options.httpStatus ?? null
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

export class FetchStudioGateway implements StudioGateway {
  constructor(
    private readonly baseUrl =
      window.__ZNIKU_STUDIO_API_BASE__ ?? 'http://127.0.0.1:18765',
  ) {}

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

  async command(command: StudioCommand): Promise<StatusEnvelope> {
    const payload = parseStudioCommand(command)
    return this.request(
      '/api/studio/command',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      },
      parseStatusEnvelope,
    )
  }

  private async request<T>(path: string, init: RequestInit, parser: Parser<T>): Promise<T> {
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
      const error = parseErrorEnvelope(value)
      throw new StudioGatewayError(`Project Service command 失败：${error.code}: ${error.message}`, {
        code: error.code,
        relatedRunIds: error.related_run_ids,
        httpStatus: response.status,
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
