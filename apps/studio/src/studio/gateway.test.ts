import { afterEach, describe, expect, it, vi } from 'vitest'
import { StudioContractError, type StudioCommand } from './contracts'
import { FetchStudioGateway, StudioGatewayError } from './gateway'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.__ZNIKU_STUDIO_API_BASE__
})

function response(value: unknown, ok: boolean, status: number): Response {
  return {
    ok,
    status,
    json: async () => value,
  } as unknown as Response
}

describe('FetchStudioGateway', () => {
  it('先按闭合 error envelope 解释合法非 2xx，而不误报完整 Schema drift', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response(
        { error: { code: 'E_PROJECT_CONFLICT', message: '工程已被修改' } },
        false,
        409,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(new FetchStudioGateway().command({ operation: 'run_all' })).rejects.toThrow(
      'E_PROJECT_CONFLICT: 工程已被修改',
    )
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:18765/api/studio/command',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('对带未知字段的非 2xx error envelope 失败关闭', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          { error: { code: 'E_PROJECT_CONFLICT', message: '工程已被修改', details: {} } },
          false,
          422,
        ),
      ),
    )

    await expect(new FetchStudioGateway().inspect()).rejects.toEqual(
      expect.objectContaining<Partial<StudioGatewayError>>({
        name: 'StudioGatewayError',
        message: 'Project Service error 只能包含 code 与 message',
      }),
    )
  })

  it('发送前按 Python command Schema 拒绝未知字段', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const invalid = { operation: 'run_all', unexpected: true } as unknown as StudioCommand

    await expect(new FetchStudioGateway().command(invalid)).rejects.toThrow(StudioContractError)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
