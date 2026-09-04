/**
 * 消费 launcher 注入的 HostBridge 0.3.0 闭集能力。
 *
 * 每次显式调用都先签发五秒一次性票据再立即消费；本模块不重试系统动作，也不把 token 写入 URL、
 * Storage、错误或日志。所有响应都按 Python 生成的 JSON Schema 失败关闭。
 */

import Ajv2020, { type ErrorObject, type ValidateFunction } from 'ajv/dist/2020.js'
import projectServiceSchema from '../service/project-service.schema.json'

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
}

interface HostBridgeBootstrap {
  readonly baseUrl: string
  readonly token: string
}

declare global {
  interface Window {
    __ZNIKU_HOST_BRIDGE__?: HostBridgeBootstrap
  }
}

type SchemaDocument = {
  readonly $schema?: string
  readonly $defs?: Readonly<Record<string, unknown>>
}

const schemaDocument = projectServiceSchema as unknown as SchemaDocument
const ajv = new Ajv2020({ allErrors: true, strict: false, validateFormats: true })

function compileDefinition(name: string): ValidateFunction {
  if (!schemaDocument.$defs || !(name in schemaDocument.$defs)) {
    throw new Error(`Python Project Service Schema 缺少 $defs/${name}`)
  }
  return ajv.compile({
    $schema: schemaDocument.$schema ?? 'https://json-schema.org/draft/2020-12/schema',
    $defs: schemaDocument.$defs,
    $ref: `#/$defs/${name}`,
  })
}

const validateCapabilities = compileDefinition('HostCapabilitiesEnvelope')
const validateUserAction = compileDefinition('HostUserActionEnvelope')
const validateInvoke = compileDefinition('HostInvokeEnvelope')
const validateReference = compileDefinition('HostPathReference')
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
