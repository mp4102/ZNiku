/**
 * 提供 Studio 到 loopback Project Service 的可替换网关。
 *
 * 网关只发送结构化 JSON，不在浏览器实现 Project 或 Runtime 语义。网络失败、非 JSON 响应及不符合
 * 0.2.0 contract 的响应都会失败关闭，测试可注入内存网关而无需模拟全局 fetch。
 */

import {
  parseStudioCommand,
  parseStudioEnvelope,
  StudioContractError,
  type StudioCommand,
  type StudioEnvelope,
} from './contracts'

export interface StudioGateway {
  inspect(): Promise<StudioEnvelope>
  command(command: StudioCommand): Promise<StudioEnvelope>
}

export class StudioGatewayError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'StudioGatewayError'
  }
}

function parseErrorEnvelope(value: unknown): { readonly code: string; readonly message: string } {
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
  if (keys.length !== 2 || keys[0] !== 'code' || keys[1] !== 'message') {
    throw new StudioGatewayError('Project Service error 只能包含 code 与 message')
  }
  const { code, message } = error as { readonly code?: unknown; readonly message?: unknown }
  if (typeof code !== 'string' || !code || typeof message !== 'string' || !message) {
    throw new StudioGatewayError('Project Service error code/message 必须是非空字符串')
  }
  return { code, message }
}

declare global {
  interface Window {
    __ZNIKU_STUDIO_API_BASE__?: string
  }
}

export class FetchStudioGateway implements StudioGateway {
  constructor(
    private readonly baseUrl =
      window.__ZNIKU_STUDIO_API_BASE__ ?? 'http://127.0.0.1:18765',
  ) {}

  async inspect(): Promise<StudioEnvelope> {
    return this.request('/api/studio/status', { method: 'GET' })
  }

  async command(command: StudioCommand): Promise<StudioEnvelope> {
    const payload = parseStudioCommand(command)
    return this.request('/api/studio/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  }

  private async request(path: string, init: RequestInit): Promise<StudioEnvelope> {
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
      throw new StudioGatewayError(`Project Service 返回非 JSON 响应（HTTP ${response.status}）`)
    }

    if (!response.ok) {
      const error = parseErrorEnvelope(value)
      throw new StudioGatewayError(`Project Service command 失败：${error.code}: ${error.message}`)
    }

    let envelope: StudioEnvelope
    try {
      envelope = parseStudioEnvelope(value)
    } catch (error) {
      if (error instanceof StudioContractError) throw error
      throw new StudioGatewayError('Project Service 响应解析失败')
    }

    return envelope
  }
}

export function createStudioGateway(): StudioGateway {
  return new FetchStudioGateway()
}
