/**
 * 消费 launcher 注入的 HostBridge 0.3.0 闭集能力。
 *
 * 原生系统动作先签发五秒一次性票据再立即消费；静帧只读，外部文件导入采用独立的预览与确认。
 * 本模块不重试副作用，也不把 token 写入 URL、Storage、错误或日志。所有响应都按 Python Schema 失败关闭。
 */

import Ajv2020, { type ErrorObject, type ValidateFunction } from 'ajv/dist/2020.js'
import projectServiceSchema from '../service/project-service.schema.json'
import type { RecentProject } from './recent-projects'

export type HostCapability =
  | 'open_file'
  | 'open_files'
  | 'select_directory'
  | 'save_file'
  | 'reveal_in_file_manager'
  | 'open_with_system_player'

export type HostDialogCapability = Extract<
  HostCapability,
  'open_file' | 'open_files' | 'select_directory' | 'save_file'
>
export type HostSystemCapability = Extract<
  HostCapability,
  'reveal_in_file_manager' | 'open_with_system_player'
>

export interface HostCapabilityState {
  readonly capability: HostCapability
  readonly available: boolean
  readonly unavailable_reason: string | null
}

export interface HostCapabilitiesEnvelope {
  readonly contract_version: '0.3.0'
  readonly capabilities: ReadonlyArray<HostCapabilityState>
}

export interface HostSelection {
  readonly selection_handle: string
  readonly path: string
}

export type HostPathReference =
  | { readonly kind: 'picker_selection'; readonly selection_handle: string }
  | { readonly kind: 'artifact'; readonly run_id: string; readonly artifact_id: string }
  | {
      readonly kind: 'handoff'
      readonly run_id: string
      readonly node_run_id: string
      readonly handoff_id: string
      readonly selector:
        | { readonly role: 'work_directory' }
        | { readonly role: 'input_artifact'; readonly artifact_id: string }
        | { readonly role: 'output_target'; readonly port_id: string; readonly ordinal?: number | null }
        | { readonly role: 'incoming_directory'; readonly port_id: string; readonly ordinal?: number | null }
    }

export interface HostDialogArguments {
  readonly title?: string
  readonly extensions?: ReadonlyArray<string>
  readonly suggested_name?: string
}

interface HostUserActionEnvelope {
  readonly contract_version: '0.3.0'
  readonly user_action_id: string
  readonly capability: HostCapability
  readonly expires_in_seconds: 5
}

interface HostInvokeEnvelope {
  readonly contract_version: '0.3.0'
  readonly status: 'selected' | 'cancelled' | 'launched'
  readonly selections: ReadonlyArray<HostSelection>
}

export interface HostBridge {
  readonly configured: boolean
  inspectCapabilities(): Promise<HostCapabilitiesEnvelope>
  pick(
    capability: HostDialogCapability,
    argumentsValue?: HostDialogArguments,
  ): Promise<ReadonlyArray<HostSelection> | null>
  launch(capability: HostSystemCapability, reference: HostPathReference): Promise<void>
  /** 只读静帧请求；可选仅兼容旧测试 double，不签发系统动作票据。 */
  preview?(request: MediaPreviewRequest): Promise<MediaPreviewEnvelope>
  /** 选择只产生预览；确认只复制/验证，不替代 Runtime 的显式 Submit。 */
  previewHandoffImport?(request: HandoffImportPreviewRequest): Promise<HandoffImportPreviewEnvelope>
  confirmHandoffImport?(request: HandoffImportConfirmRequest): Promise<HandoffImportEnvelope>
  observeHandoffInbox?(request: HandoffImportBinding): Promise<HandoffInboxObserveEnvelope>
  previewHandoffInbox?(request: HandoffInboxPreviewRequest): Promise<HandoffInboxPreviewEnvelope>
  confirmHandoffInbox?(request: HandoffInboxConfirmRequest): Promise<HandoffInboxConfirmEnvelope>
  inspectStorage?(projectSessionId: string): Promise<StorageInspection>
  configureStorage?(request: StorageLocationRequest): Promise<StorageInspection>
  previewStorageMigration?(request: StorageLocationRequest): Promise<StorageMigrationPreview>
  confirmStorageMigration?(request: StorageMigrationConfirmRequest): Promise<StorageInspection>
  inspectDesktop?(): Promise<DesktopSessionEnvelope>
  closeDesktop?(instanceId: string): Promise<void>
  saveDesktopPreferences?(instanceId: string, preferences: DesktopPreferences): Promise<void>
}

export interface DesktopPreferences {
  readonly density: 'creator' | 'advanced'
  readonly recent_projects: ReadonlyArray<RecentProject>
}

export interface DesktopSessionEnvelope {
  readonly contract_version: '0.3.0'
  readonly instance_id: string
  readonly busy: boolean
  readonly closing: boolean
}

export interface MediaPreviewRequest {
  readonly contract_version: '0.3.0'
  readonly project_session_id: string | null
  readonly reference: HostPathReference
}

export interface MediaPreviewEnvelope extends MediaPreviewRequest {
  readonly image_data_url: string
  readonly width: number
  readonly height: number
  readonly cache_hit: boolean
}

export interface HandoffImportPreviewRequest {
  readonly contract_version: '0.3.0'
  readonly selection_handle: string
  readonly project_session_id: string
  readonly run_id: string
  readonly node_run_id: string
  readonly handoff_id: string
  readonly port_id: string
  readonly ordinal: number | null
}

export interface HandoffImportPreviewEnvelope extends HandoffImportPreviewRequest {
  readonly import_id: string
  readonly source_name: string
  readonly source_size: number
  readonly target_path: string
  readonly replace_existing: boolean
  readonly expires_in_seconds: 300
}

export interface HandoffImportConfirmRequest {
  readonly contract_version: '0.3.0'
  readonly import_id: string
  readonly overwrite: boolean
}

export interface HandoffImportEnvelope extends Omit<HandoffImportPreviewRequest, 'selection_handle'> {
  readonly import_id: string
  readonly source_name: string
  readonly source_size: number
  readonly target_path: string
  readonly status: 'imported'
}

export type HandoffImportBinding = Omit<HandoffImportPreviewRequest, 'selection_handle'>
export interface HandoffInboxCandidate {
  readonly candidate_handle: string
  readonly name: string
  readonly size: number
  readonly mtime_ns: number
}
export interface HandoffInboxObserveEnvelope extends HandoffImportBinding {
  readonly inbox_path: string
  readonly allowed_suffix: string
  readonly candidates: ReadonlyArray<HandoffInboxCandidate>
  readonly rejected_count: number
  readonly expires_in_seconds: 300
}
export interface HandoffInboxPreviewRequest extends HandoffImportBinding {
  readonly candidate_handle: string
}
export interface HandoffInboxPreviewEnvelope extends HandoffImportBinding {
  readonly inbox_id: string
  readonly source_name: string
  readonly source_size: number
  readonly target_path: string
  readonly replace_existing: boolean
  readonly action: 'move'
  readonly expires_in_seconds: 300
}
export interface HandoffInboxConfirmRequest {
  readonly contract_version: '0.3.0'
  readonly inbox_id: string
  readonly overwrite: boolean
}
export interface HandoffInboxConfirmEnvelope extends HandoffImportBinding {
  readonly inbox_id: string
  readonly source_name: string
  readonly source_size: number
  readonly target_path: string
  readonly status: 'collected'
}
export interface ProjectStorage {
  readonly contract_version: '0.3.0'
  readonly mode: 'adjacent' | 'custom' | 'legacy'
  readonly data_root: string
  readonly attempts_root: string
  readonly retention: 'keep'
  readonly media_basename: string | null
}
export interface StorageDependency {
  readonly path: string
  readonly artifact_ids: ReadonlyArray<string>
  readonly state: 'present' | 'missing' | 'unreadable'
}
export interface StorageInspection {
  readonly contract_version: '0.3.0'
  readonly storage: ProjectStorage
  readonly configured: boolean
  readonly attempt_count: number
  readonly registered_file_count: number
  readonly registered_bytes: number
  readonly managed_file_count: number
  readonly managed_bytes: number
  readonly missing: ReadonlyArray<StorageDependency>
  readonly external_dependencies: ReadonlyArray<StorageDependency>
  readonly coverage: 'registered_artifacts'
  readonly warnings: ReadonlyArray<string>
}
export interface StorageLocationRequest {
  readonly contract_version?: '0.3.0'
  readonly project_session_id: string
  readonly expected_storage_revision: number
  readonly selection_handle: string | null
}
export interface StorageMigrationPreview {
  readonly contract_version: '0.3.0'
  readonly ticket_id: string
  readonly project_session_id: string
  readonly expected_storage_revision: number
  readonly source: ProjectStorage
  readonly target: ProjectStorage
  readonly attempt_count: number
  readonly file_count: number
  readonly byte_count: number
  readonly external_dependencies: ReadonlyArray<StorageDependency>
  readonly originals_retained: true
}
export interface StorageMigrationConfirmRequest {
  readonly contract_version?: '0.3.0'
  readonly project_session_id: string
  readonly expected_storage_revision: number
  readonly ticket_id: string
}

interface HostBridgeBootstrap {
  readonly baseUrl: string
  readonly token: string
}

declare global {
  interface Window {
    __ZNIKU_HOST_BRIDGE__?: HostBridgeBootstrap
    __ZNIKU_DESKTOP__?: { readonly contractVersion: '0.3.0'; readonly instanceId: string; readonly preferences?: DesktopPreferences }
  }
}

type SchemaDocument = {
  readonly $schema?: string
  readonly $defs?: Readonly<Record<string, unknown>>
}

const schemaDocument = projectServiceSchema as unknown as SchemaDocument
const ajv = new Ajv2020({ allErrors: true, strict: false, validateFormats: true })
const definitionValidators = new Map<string, ValidateFunction>()

function compileDefinition(name: string): ValidateFunction {
  const cached = definitionValidators.get(name)
  if (cached) return cached
  if (!schemaDocument.$defs || !(name in schemaDocument.$defs)) {
    throw new Error(`Python Project Service Schema 缺少 $defs/${name}`)
  }
  const validator = ajv.compile({
    $schema: schemaDocument.$schema ?? 'https://json-schema.org/draft/2020-12/schema',
    $defs: schemaDocument.$defs,
    $ref: `#/$defs/${name}`,
  })
  definitionValidators.set(name, validator)
  return validator
}

const validateCapabilities = compileDefinition('HostCapabilitiesEnvelope')
const validateUserAction = compileDefinition('HostUserActionEnvelope')
const validateInvoke = compileDefinition('HostInvokeEnvelope')
const validateReference = compileDefinition('HostPathReference')
// 延迟编译：预览不是启动、建项或运行的前置条件。
let validatePreviewRequest: ValidateFunction | undefined
let validatePreviewEnvelope: ValidateFunction | undefined

function sameReference(left: HostPathReference, right: HostPathReference): boolean {
  // 只比较 wire 身份字段，不比较序列化属性顺序，也不生成领域 digest。
  const ordered = (value: unknown): string => JSON.stringify(value, (_key, item: unknown) => {
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      return Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b)))
    }
    return item
  })
  return ordered(left) === ordered(right)
}
const capabilityOrder: ReadonlyArray<HostCapability> = [
  'open_file',
  'open_files',
  'select_directory',
  'save_file',
  'reveal_in_file_manager',
  'open_with_system_player',
]

function validationMessage(errors: ErrorObject[] | null | undefined): string {
  return (errors ?? []).slice(0, 4).map(
    (error) => `${error.instancePath || '/'} ${error.message ?? error.keyword}`,
  ).join('; ')
}

function parseWith<T>(value: unknown, validator: ValidateFunction, label: string): T {
  if (!validator(value)) {
    throw new HostBridgeError(`${label} 不符合 Python 0.3.0 Schema：${validationMessage(validator.errors)}`)
  }
  return value as T
}

function parseCapabilities(value: unknown): HostCapabilitiesEnvelope {
  const parsed = parseWith<HostCapabilitiesEnvelope>(
    value,
    validateCapabilities,
    'Host capability response',
  )
  if (
    parsed.capabilities.length !== capabilityOrder.length ||
    parsed.capabilities.some((item, index) =>
      item.capability !== capabilityOrder[index] ||
      item.available === (item.unavailable_reason !== null),
    )
  ) {
    throw new HostBridgeError('Host capability response 不符合完整固定闭集或可用性语义')
  }
  return parsed
}

function isAbsoluteHostPath(path: string): boolean {
  return path.trim() === path && !path.includes('\u0000') && (
    /^[A-Za-z]:[\\/]/.test(path) || /^\\\\[^\\/]+[\\/][^\\/]+/.test(path)
  )
}

function parseInvoke(value: unknown): HostInvokeEnvelope {
  const parsed = parseWith<HostInvokeEnvelope>(value, validateInvoke, 'Host invoke response')
  if ((parsed.status === 'selected') !== (parsed.selections.length > 0)) {
    throw new HostBridgeError('Host invoke response 的 selected 与 selections 不一致')
  }
  if (parsed.selections.some((selection) => !isAbsoluteHostPath(selection.path))) {
    throw new HostBridgeError('Host invoke response 含有无效的非绝对路径')
  }
  return parsed
}

export class HostBridgeError extends Error {
  readonly code: string | null
  readonly httpStatus: number | null

  constructor(message: string, options: { readonly code?: string; readonly httpStatus?: number } = {}) {
    super(message)
    this.name = 'HostBridgeError'
    this.code = options.code ?? null
    this.httpStatus = options.httpStatus ?? null
  }
}

function parseError(value: unknown): { readonly code: string; readonly message: string } {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new HostBridgeError('HostBridge 返回了无法识别的错误响应')
  }
  const keys = Object.keys(value)
  const error = (value as { readonly error?: unknown }).error
  if (keys.length !== 1 || keys[0] !== 'error' || typeof error !== 'object' || error === null || Array.isArray(error)) {
    throw new HostBridgeError('HostBridge 错误响应必须只包含 error')
  }
  if (Object.keys(error).sort().join('\u0000') !== 'code\u0000message') {
    throw new HostBridgeError('HostBridge error 必须只包含 code 与 message')
  }
  const { code, message } = error as { readonly code?: unknown; readonly message?: unknown }
  if (typeof code !== 'string' || !code || typeof message !== 'string' || !message) {
    throw new HostBridgeError('HostBridge error 字段类型无效')
  }
  return { code, message }
}

export class FetchHostBridge implements HostBridge {
  readonly configured: boolean
  private readonly baseUrl: string | null
  private readonly token: string | null
  private readonly bootstrapError: string | null
  private preferenceWrites: Promise<void> = Promise.resolve()
  private readonly importPreviews = new Map<string, HandoffImportPreviewEnvelope>()
  private readonly inboxPreviews = new Map<string, HandoffInboxPreviewEnvelope>()
  private readonly storagePreviews = new Map<string, StorageMigrationPreview>()

  constructor(bootstrap: HostBridgeBootstrap | undefined = window.__ZNIKU_HOST_BRIDGE__) {
    let baseUrl: string | null = null
    let token: string | null = null
    let bootstrapError: string | null = null
    if (bootstrap !== undefined) {
      try {
        const parsed = new URL(bootstrap.baseUrl)
        const port = Number(parsed.port)
        const canonical =
          parsed.protocol === 'http:' &&
          parsed.hostname === '127.0.0.1' &&
          Number.isInteger(port) &&
          port >= 1024 &&
          port <= 65535 &&
          parsed.username === '' &&
          parsed.password === '' &&
          parsed.pathname === '/' &&
          parsed.search === '' &&
          parsed.hash === '' &&
          bootstrap.baseUrl === parsed.origin
        if (!canonical) throw new Error('invalid loopback origin')
        if (!/^[A-Za-z0-9_-]{43,256}$/.test(bootstrap.token)) throw new Error('invalid token')
        baseUrl = parsed.origin
        token = bootstrap.token
      } catch {
        bootstrapError = 'ZNIKU launcher 提供了无效的本机连接信息。'
      }
    }
    this.configured = baseUrl !== null && token !== null
    this.baseUrl = baseUrl
    this.token = token
    this.bootstrapError = bootstrapError
  }

  async inspectCapabilities(): Promise<HostCapabilitiesEnvelope> {
    return this.request('/api/host-bridge/capabilities', { method: 'GET' }, parseCapabilities)
  }

  async pick(
    capability: HostDialogCapability,
    argumentsValue: HostDialogArguments = {},
  ): Promise<ReadonlyArray<HostSelection> | null> {
    const result = await this.invoke(capability, argumentsValue)
    if (result.status === 'cancelled') return null
    if (result.status !== 'selected' || result.selections.length === 0) {
      throw new HostBridgeError('HostBridge picker 没有返回 selected 结果')
    }
    if (capability !== 'open_files' && result.selections.length !== 1) {
      throw new HostBridgeError('HostBridge 单选 capability 返回了多个路径')
    }
    return result.selections
  }

  async launch(capability: HostSystemCapability, reference: HostPathReference): Promise<void> {
    parseWith(reference, validateReference, 'Host path reference')
    const result = await this.invoke(capability, { reference })
    if (result.status !== 'launched' || result.selections.length !== 0) {
      throw new HostBridgeError('HostBridge 系统动作没有返回 launched 结果')
    }
  }

  async preview(request: MediaPreviewRequest): Promise<MediaPreviewEnvelope> {
    validatePreviewRequest ??= compileDefinition('MediaPreviewRequest')
    validatePreviewEnvelope ??= compileDefinition('MediaPreviewEnvelope')
    parseWith(request, validatePreviewRequest, 'Media preview request')
    const result = await this.request('/api/host-bridge/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    }, (value) => parseWith<MediaPreviewEnvelope>(value, validatePreviewEnvelope!, 'Media preview response'))
    if (result.project_session_id !== request.project_session_id || !sameReference(result.reference, request.reference)) {
      throw new HostBridgeError('静帧响应不属于当前工程与媒体引用。')
    }
    return result
  }

  async inspectDesktop(): Promise<DesktopSessionEnvelope> {
    const validator = compileDefinition('DesktopSessionEnvelope')
    return this.request('/api/desktop/session', { method: 'GET' },
      (value) => parseWith<DesktopSessionEnvelope>(value, validator, 'Desktop session'))
  }

  async previewHandoffImport(request: HandoffImportPreviewRequest): Promise<HandoffImportPreviewEnvelope> {
    parseWith(request, compileDefinition('HandoffImportPreviewRequest'), 'Handoff import preview request')
    const result = await this.request('/api/studio/handoff-import/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
    }, (value) => parseWith<HandoffImportPreviewEnvelope>(value, compileDefinition('HandoffImportPreviewEnvelope'), 'Handoff import preview response'))
    if (Object.entries(request).some(([key, value]) => result[key as keyof HandoffImportPreviewRequest] !== value)) {
      throw new HostBridgeError('文件导入预览不属于当前工程、任务与所选文件。')
    }
    this.importPreviews.set(result.import_id, result)
    if (this.importPreviews.size > 64) this.importPreviews.delete(this.importPreviews.keys().next().value!)
    return result
  }

  async confirmHandoffImport(request: HandoffImportConfirmRequest): Promise<HandoffImportEnvelope> {
    parseWith(request, compileDefinition('HandoffImportConfirmRequest'), 'Handoff import confirm request')
    const preview = this.importPreviews.get(request.import_id)
    if (!preview) throw new HostBridgeError('文件导入预览已失效，请重新选择文件。')
    // 确认有文件副作用：发出前消费本地绑定，网络结果不明也不自动重试。
    this.importPreviews.delete(request.import_id)
    const result = await this.request('/api/studio/handoff-import/confirm', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
    }, (value) => parseWith<HandoffImportEnvelope>(value, compileDefinition('HandoffImportConfirmEnvelope'), 'Handoff import response'))
    const keys = ['contract_version', 'import_id', 'project_session_id', 'run_id', 'node_run_id',
      'handoff_id', 'port_id', 'ordinal', 'source_name', 'source_size', 'target_path'] as const
    if (keys.some((key) => result[key] !== preview[key])) {
      throw new HostBridgeError('文件导入响应不属于已确认的任务与文件，请刷新检查实际结果。')
    }
    return result
  }

  private dataPost<T>(route: string, request: object, inputSchema: string, outputSchema: string): Promise<T> {
    parseWith(request, compileDefinition(inputSchema), inputSchema)
    return this.request(`/api/studio/${route}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
    }, (value) => parseWith<T>(value, compileDefinition(outputSchema), outputSchema))
  }

  async observeHandoffInbox(request: HandoffImportBinding): Promise<HandoffInboxObserveEnvelope> {
    const result = await this.dataPost<HandoffInboxObserveEnvelope>('handoff-inbox/observe', request,
      'HandoffInboxObserveRequest', 'HandoffInboxObserveEnvelope')
    this.assertInboxBinding(request, result)
    return result
  }

  private assertInboxBinding(request: HandoffImportBinding, result: HandoffImportBinding): void {
    const keys = ['contract_version', 'project_session_id', 'run_id', 'node_run_id', 'handoff_id', 'port_id', 'ordinal'] as const
    if (keys.some((key) => request[key] !== result[key])) throw new HostBridgeError('收件响应不属于当前工程与外部任务。')
  }

  async previewHandoffInbox(request: HandoffInboxPreviewRequest): Promise<HandoffInboxPreviewEnvelope> {
    const result = await this.dataPost<HandoffInboxPreviewEnvelope>('handoff-inbox/preview', request,
      'HandoffInboxPreviewRequest', 'HandoffInboxPreviewEnvelope')
    this.assertInboxBinding(request, result)
    this.inboxPreviews.set(result.inbox_id, result)
    if (this.inboxPreviews.size > 32) this.inboxPreviews.delete(this.inboxPreviews.keys().next().value!)
    return result
  }

  async confirmHandoffInbox(request: HandoffInboxConfirmRequest): Promise<HandoffInboxConfirmEnvelope> {
    const preview = this.inboxPreviews.get(request.inbox_id)
    if (!preview) throw new HostBridgeError('收件预览已失效，请重新选择候选。')
    this.inboxPreviews.delete(request.inbox_id)
    const result = await this.dataPost<HandoffInboxConfirmEnvelope>('handoff-inbox/confirm', request,
      'HandoffInboxConfirmRequest', 'HandoffInboxConfirmEnvelope')
    this.assertInboxBinding(preview, result)
    const keys = ['inbox_id', 'source_name', 'source_size', 'target_path'] as const
    if (keys.some((key) => preview[key] !== result[key])) throw new HostBridgeError('收纳结果与预览不一致，请检查实际文件，不要重复确认。')
    return result
  }

  inspectStorage(projectSessionId: string): Promise<StorageInspection> {
    return this.dataPost('storage/inspect', { contract_version: '0.3.0', project_session_id: projectSessionId },
      'StorageInspectRequest', 'StorageInspection')
  }

  configureStorage(request: StorageLocationRequest): Promise<StorageInspection> {
    return this.dataPost('storage/configure', request, 'StorageLocationRequest', 'StorageInspection')
  }

  async previewStorageMigration(request: StorageLocationRequest): Promise<StorageMigrationPreview> {
    const result = await this.dataPost<StorageMigrationPreview>('storage/preview', request,
      'StorageLocationRequest', 'StorageMigrationPreview')
    if (result.project_session_id !== request.project_session_id || result.expected_storage_revision !== request.expected_storage_revision) {
      throw new HostBridgeError('迁移预览不属于当前工程，请重新检查。')
    }
    this.storagePreviews.set(result.ticket_id, result)
    if (this.storagePreviews.size > 32) this.storagePreviews.delete(this.storagePreviews.keys().next().value!)
    return result
  }

  async confirmStorageMigration(request: StorageMigrationConfirmRequest): Promise<StorageInspection> {
    const preview = this.storagePreviews.get(request.ticket_id)
    if (!preview || preview.project_session_id !== request.project_session_id || preview.expected_storage_revision !== request.expected_storage_revision) {
      throw new HostBridgeError('迁移预览已失效，请重新选择位置。')
    }
    this.storagePreviews.delete(request.ticket_id)
    const result = await this.dataPost<StorageInspection>('storage/confirm', request,
      'StorageMigrationConfirmRequest', 'StorageInspection')
    if (result.storage.data_root !== preview.target.data_root || result.storage.attempts_root !== preview.target.attempts_root) {
      throw new HostBridgeError('迁移响应与已确认位置不一致，请刷新检查实际工程。')
    }
    return result
  }

  async closeDesktop(instanceId: string): Promise<void> {
    await this.preferenceWrites.catch(() => undefined)
    const request = { contract_version: '0.3.0', instance_id: instanceId, confirm: true }
    parseWith(request, compileDefinition('DesktopCloseRequest'), 'Desktop close request')
    const validator = compileDefinition('DesktopCloseEnvelope')
    const result = await this.request('/api/desktop/close', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
    }, (value) => parseWith<{ readonly instance_id: string }>(value, validator, 'Desktop close response'))
    if (result.instance_id !== instanceId) throw new HostBridgeError('退出响应不属于当前桌面实例。')
  }

  saveDesktopPreferences(instanceId: string, preferences: DesktopPreferences): Promise<void> {
    const request = { contract_version: '0.3.0', instance_id: instanceId, ...preferences }
    parseWith(request, compileDefinition('DesktopPreferencesRequest'), 'Desktop preferences request')
    // 同一 session 单路有序保存；不让慢响应把新密度/最近工程回写成旧值，也不重试副作用。
    const body = JSON.stringify(request)
    const pending = this.preferenceWrites.catch(() => undefined).then(async () => {
      const result = await this.request('/api/desktop/preferences', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body, signal: AbortSignal.timeout(10_000),
      }, (value) => parseWith<{ readonly instance_id: string }>(value, compileDefinition('DesktopPreferencesEnvelope'), 'Desktop preferences response'))
      if (result.instance_id !== instanceId) throw new HostBridgeError('偏好响应不属于当前桌面实例。')
    })
    this.preferenceWrites = pending
    return pending
  }

  private async invoke(capability: HostCapability, argumentsValue: object): Promise<HostInvokeEnvelope> {
    const action = await this.request(
      '/api/host-bridge/user-actions',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ capability }),
      },
      (value) => parseWith<HostUserActionEnvelope>(value, validateUserAction, 'Host user action'),
    )
    if (action.capability !== capability || action.expires_in_seconds !== 5) {
      throw new HostBridgeError('HostBridge user action 与请求 capability 不一致')
    }
    // 不自动重试：票据在成功、取消和失败时都会消费，重放可能重复弹窗或系统动作。
    return this.request(
      '/api/host-bridge/invoke',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_action_id: action.user_action_id,
          capability,
          arguments: argumentsValue,
        }),
      },
      parseInvoke,
    )
  }

  private async request<T>(path: string, init: RequestInit, parse: (value: unknown) => T): Promise<T> {
    if (!this.configured || !this.baseUrl || !this.token) {
      throw new HostBridgeError(
        this.bootstrapError ?? '桌面文件能力未连接；请使用 ZNIKU launcher 启动 Studio。',
        {
        code: this.bootstrapError ? 'E_HOST_BRIDGE_BOOTSTRAP' : 'E_HOST_BRIDGE_UNAVAILABLE',
      })
    }
    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        cache: 'no-store',
        credentials: 'omit',
        referrerPolicy: 'no-referrer',
        headers: {
          ...init.headers,
          'X-ZNIKU-Host-Token': this.token,
        },
      })
    } catch (error) {
      throw new HostBridgeError(
        `本机文件能力不可用：${error instanceof Error ? error.message : 'network error'}`,
      )
    }
    let value: unknown
    try {
      value = await response.json()
    } catch {
      throw new HostBridgeError(`HostBridge 返回非 JSON 响应（HTTP ${response.status}）`, {
        httpStatus: response.status,
      })
    }
    if (!response.ok) {
      const error = parseError(value)
      throw new HostBridgeError(`${error.code}: ${error.message}`, {
        code: error.code,
        httpStatus: response.status,
      })
    }
    return parse(value)
  }
}

export function createHostBridge(): HostBridge {
  return new FetchHostBridge()
}

export function readDesktopPreferences(): DesktopPreferences | null {
  const value = window.__ZNIKU_DESKTOP__
  if (!value?.preferences) return null
  try {
    const parsed = parseWith<DesktopPreferences>({ contract_version: value.contractVersion,
      instance_id: value.instanceId, ...value.preferences }, compileDefinition('DesktopPreferencesEnvelope'), 'Desktop preference bootstrap')
    return { density: parsed.density, recent_projects: parsed.recent_projects }
  } catch { return null } // 纯 UI 偏好损坏不得阻断正式工程或修改 Graph。
}
