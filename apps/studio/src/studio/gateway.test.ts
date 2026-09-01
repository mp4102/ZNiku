import { afterEach, describe, expect, it, vi } from 'vitest'
import { StudioContractError, type StudioCommand } from './contracts'
import { FetchStudioGateway, StudioGatewayError } from './gateway'
import {
  handoffDetailEnvelope,
  handoffEnvelope,
  handoffFixtureIds,
  handoffLogEnvelope,
  handoffReadinessEnvelope,
  handoffSummary,
} from './test-fixtures'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.__ZNIKU_STUDIO_API_BASE__
})

function response(value: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => value } as unknown as Response
}

describe('FetchStudioGateway 0.2.1', () => {
  it('按闭合 error envelope 保留 duplicate Run conflict identity', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response(
        {
          error: {
            code: 'E_PROJECT_SERVICE_RUN_CONFLICT',
            message: '已有非终态 Run',
            related_run_ids: [handoffFixtureIds.run],
          },
        },
        false,
        409,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(new FetchStudioGateway().command({ operation: 'run_all' })).rejects.toEqual(
      expect.objectContaining<Partial<StudioGatewayError>>({
        name: 'StudioGatewayError',
        code: 'E_PROJECT_SERVICE_RUN_CONFLICT',
        relatedRunIds: [handoffFixtureIds.run],
        httpStatus: 409,
      }),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:18765/api/studio/command',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('对缺字段或带未知字段的非 2xx error envelope 失败关闭', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          {
            error: {
              code: 'E_PROJECT_CONFLICT',
              message: '工程已被修改',
              related_run_ids: [],
              details: {},
            },
          },
          false,
          422,
        ),
      ),
    )

    await expect(new FetchStudioGateway().inspect()).rejects.toEqual(
      expect.objectContaining<Partial<StudioGatewayError>>({
        name: 'StudioGatewayError',
        message: 'Project Service error 只能包含 code、message 与 related_run_ids',
      }),
    )
  })

  it('发送前按 Python command Schema 拒绝未知字段和旧 Submit 形状', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const gateway = new FetchStudioGateway()
    const unknown = { operation: 'run_all', unexpected: true } as unknown as StudioCommand
    const oldSubmit = {
      operation: 'submit_external',
      node_run_id: handoffFixtureIds.transformNodeRun,
    } as unknown as StudioCommand

    await expect(gateway.command(unknown)).rejects.toThrow(StudioContractError)
    await expect(gateway.command(oldSubmit)).rejects.toThrow(StudioContractError)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('按资源 endpoint 定向读取 summary/detail/log/readiness 并编码 query', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(handoffEnvelope()))
      .mockResolvedValueOnce(response({
        contract_version: '0.2.1',
        run_summaries: [handoffSummary()],
        next_run_cursor: null,
      }))
      .mockResolvedValueOnce(response(handoffDetailEnvelope()))
      .mockResolvedValueOnce(response(handoffLogEnvelope()))
      .mockResolvedValueOnce(response(handoffReadinessEnvelope('present', false)))
    vi.stubGlobal('fetch', fetchMock)
    const gateway = new FetchStudioGateway('http://loopback.test')

    await gateway.inspect(handoffFixtureIds.run)
    await gateway.listRuns('cursor / synthetic', 7)
    await gateway.inspectRun(handoffFixtureIds.run)
    await gateway.inspectLog(handoffFixtureIds.run, handoffFixtureIds.transformNodeRun)
    await gateway.inspectReadiness(
      handoffFixtureIds.run,
      handoffFixtureIds.transformNodeRun,
      false,
    )

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `http://loopback.test/api/studio/status?view_run_id=${handoffFixtureIds.run}`,
      'http://loopback.test/api/studio/runs?limit=7&cursor=cursor+%2F+synthetic',
      `http://loopback.test/api/studio/runs/${handoffFixtureIds.run}`,
      `http://loopback.test/api/studio/runs/${handoffFixtureIds.run}/node-runs/${handoffFixtureIds.transformNodeRun}/logs`,
      `http://loopback.test/api/studio/runs/${handoffFixtureIds.run}/node-runs/${handoffFixtureIds.transformNodeRun}/handoff-readiness?probe=false`,
    ])
  })

  it('即使 payload 通过 Schema，也拒绝与请求不一致的资源 identity', async () => {
    const detail = handoffDetailEnvelope()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response({ ...detail, run: { ...detail.run, run_id: '00000000-0000-4000-8000-ffffffffffff' } }),
      ),
    )

    await expect(new FetchStudioGateway().inspectRun(handoffFixtureIds.run)).rejects.toThrow(
      /run_id 与请求资源不一致/,
    )
  })
})
