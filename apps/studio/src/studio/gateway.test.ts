import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  StudioContractError,
  type AvEnhanceV27TemplatePreviewEnvelope,
  type PresentationCatalogEnvelopeWire,
  type AvEnhanceV27TemplatePreviewRequestWire,
  type AvEnhanceV27PublicationPreviewRequestWire,
  type StudioCommand,
  type RerunPreviewRequest,
} from './contracts'
import { FetchStudioGateway, StudioGatewayError } from './gateway'
import {
  handoffDetailEnvelope,
  handoffEnvelope,
  handoffFixtureIds,
  handoffLogEnvelope,
  handoffReadinessEnvelope,
  handoffSummary,
  projectSnapshot,
  projectSessionId,
  defaultStudioState,
  studioEnvelope,
  rerunPreviewEnvelope,
} from './test-fixtures'

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.__ZNIKU_STUDIO_API_BASE__
})

function response(value: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => value } as unknown as Response
}

describe('FetchStudioGateway 0.3.0', () => {
  it('输出位置检查走独立只读 POST，响应必须绑定整理方式且非法请求不发送', async () => {
    const request: AvEnhanceV27PublicationPreviewRequestWire = { contract_version: '0.3.0', request: {
      output_root: 'D:\\Library', title: 'Movie', year: '2026', layout: 'direct', overwrite: false,
    } }
    const result = { contract_version: '0.3.0', layout: 'direct', resolved_output_root: 'D:\\Library', output_directory: 'D:\\Library', will_create_directory: false }
    const fetchMock = vi.fn().mockResolvedValue(response(result))
    vi.stubGlobal('fetch', fetchMock)
    const gateway = new FetchStudioGateway('http://loopback.test')
    expect(await gateway.previewAvEnhanceV27Publication(request)).toEqual(result)
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith('http://loopback.test/api/studio/templates/av-enhance-v27/publication-preview', expect.objectContaining({ method: 'POST', body: JSON.stringify(request) }))
    fetchMock.mockResolvedValueOnce(response({ ...result, layout: 'title_subdirectory' }))
    await expect(gateway.previewAvEnhanceV27Publication(request)).rejects.toThrow(StudioContractError)
    const callsBefore = fetchMock.mock.calls.length
    await expect(gateway.previewAvEnhanceV27Publication({ ...request, request: { ...request.request, layout: 'guess' } } as unknown as AvEnhanceV27PublicationPreviewRequestWire)).rejects.toThrow(StudioContractError)
    expect(fetchMock).toHaveBeenCalledTimes(callsBefore)
  })
  it('只读重跑预览严格绑定请求，不接受未知字段和其他工程或 Run 回执', async () => {
    const request: RerunPreviewRequest = { operation: 'rerun_from_here', run_id: handoffFixtureIds.run,
      node_id: 'transform', project_session_id: projectSessionId, expected_storage_revision: 0 }
    const preview = rerunPreviewEnvelope(request)
    const fetchMock = vi.fn().mockResolvedValue(response(preview))
    vi.stubGlobal('fetch', fetchMock)
    const gateway = new FetchStudioGateway('http://loopback.test')
    expect(await gateway.previewRerun(request)).toEqual(preview)
    expect(fetchMock).toHaveBeenLastCalledWith('http://loopback.test/api/studio/rerun-preview', expect.objectContaining({ method: 'POST', body: JSON.stringify(request) }))
    for (const changed of [
      { ...preview, storage_revision: 1 }, { ...preview, node_id: 'other', rerun_node_ids: ['other'] },
      { ...preview, project_session_id: handoffFixtureIds.run },
      { ...preview, run_id: projectSessionId }, { ...preview, unexpected: true },
      { ...preview, reusable_node_ids: ['transform'] },
    ]) {
      fetchMock.mockResolvedValueOnce(response(changed))
      await expect(gateway.previewRerun(request)).rejects.toThrow(StudioContractError)
    }
    await expect(gateway.previewRerun({ ...request, contract_version: '0.3.0' } as unknown as RerunPreviewRequest)).rejects.toThrow(StudioContractError)
  })
  it('只向固定 endpoint 发送严格 v2.7 preview request 并解析 Python response', async () => {
    const payload: AvEnhanceV27TemplatePreviewRequestWire = {
      action: 'prepare',
      request: {
        profile_version: '2.7.0',
        project_path: 'C:\\synthetic\\av27.zniku',
        project_id: 'project.av27',
        project_name: 'Synthetic AV27',
        source_mode: 'program',
        sources: [
          { source_path: 'C:\\synthetic\\source.mkv', source_ordinal: 0 },
        ],
        mr: { mode: 'off' },
      },
    }
    const envelope: AvEnhanceV27TemplatePreviewEnvelope = {
      contract_version: '0.3.0',
      profile_version: '2.7.0',
      phase: 'preparation',
      project: projectSnapshot.project,
      definitions: projectSnapshot.definitions,
      profile: {
        profile_version: '2.7.0',
        phase: 'preparation',
        status: 'preparation-compatible',
        compatible: true,
        diagnostics: [],
      },
      plan: {
        source_count: 1,
        chapter_count: 0,
        leaf_count: 0,
        mr_mode: 'off',
        preparation_run_id: null,
        effective_video_artifact_ids: [],
        chapters: [],
        manual_stages: [],
        output_target_path: null,
        output_directory_to_create: null,
      },
      creator: {
        analyzed: false,
        sources: [],
        estimated_step_count: projectSnapshot.project.graph.nodes.length,
        estimated_steps: `预计 ${projectSnapshot.project.graph.nodes.length} 个处理步骤`,
      },
    }
    const fetchMock = vi.fn().mockResolvedValue(response(envelope))
    vi.stubGlobal('fetch', fetchMock)

    const result = await new FetchStudioGateway('http://loopback.test').previewAvEnhanceV27(
      payload,
    )

    expect(result.phase).toBe('preparation')
    expect(fetchMock).toHaveBeenCalledWith(
      'http://loopback.test/api/studio/templates/av-enhance-v27/preview',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    )

    const wrongPhase: AvEnhanceV27TemplatePreviewEnvelope = {
      ...envelope,
      phase: 'expanded',
      profile: {
        ...envelope.profile,
        phase: 'expanded',
        status: 'expanded-compatible',
      },
      plan: {
        ...envelope.plan,
        preparation_run_id: '00000000-0000-4000-8000-000000000027',
      },
      creator: {
        ...envelope.creator,
        analyzed: true,
        sources: [{
          source_ordinal: 0, chapter_label: null, display_name: 'source.mkv',
          size_bytes: 1024, size_label: '1 KiB', container: 'Matroska', video_codec: 'HEVC',
          pixel_format: 'yuv420p10le', resolution: '1920 × 1080',
          frame_rate: '30000/1001 fps（29.970）', duration: '1 分 0 秒', frame_count: '1,801 帧',
          audio_tracks: [],
        }],
      },
    }
    fetchMock.mockResolvedValueOnce(response(wrongPhase))
    await expect(
      new FetchStudioGateway('http://loopback.test').previewAvEnhanceV27(payload),
    ).rejects.toThrow(/phase 与 prepare action 不一致/)

    const injected = { ...payload, definitions: [] } as unknown as AvEnhanceV27TemplatePreviewRequestWire
    await expect(
      new FetchStudioGateway('http://loopback.test').previewAvEnhanceV27(injected),
    ).rejects.toThrow(StudioContractError)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

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

    await expect(new FetchStudioGateway().command({
      operation: 'run_all', project_session_id: projectSessionId, expected_storage_revision: 0,
    })).rejects.toEqual(
      expect.objectContaining<Partial<StudioGatewayError>>({
        name: 'StudioGatewayError',
        code: 'E_PROJECT_SERVICE_RUN_CONFLICT',
        serviceMessage: '已有非终态 Run',
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

  it('保存携带完整 CAS 与 StudioState；冲突保持原码且不自动重试', async () => {
    const command: StudioCommand = {
      operation: 'save_project', project: projectSnapshot.project,
      studio_state: defaultStudioState, project_session_id: projectSessionId, expected_storage_revision: 7,
    }
    const fetchMock = vi.fn().mockResolvedValueOnce(response(studioEnvelope({ storage_revision: 8 })))
      .mockResolvedValueOnce(response({ error: {
        code: 'E_STORAGE_REVISION_CONFLICT', message: '存储版本已变化', related_run_ids: [],
      } }, false, 409))
    vi.stubGlobal('fetch', fetchMock)
    const gateway = new FetchStudioGateway()
    expect((await gateway.command(command)).storage_revision).toBe(8)
    expect(fetchMock).toHaveBeenNthCalledWith(1, expect.any(String), expect.objectContaining({ body: JSON.stringify(command) }))
    await expect(gateway.command(command)).rejects.toMatchObject({ code: 'E_STORAGE_REVISION_CONFLICT', httpStatus: 409 })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const old = { operation: 'save_project', project: projectSnapshot.project } as unknown as StudioCommand
    await expect(gateway.command(old)).rejects.toThrow(StudioContractError)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('从节点重跑同时绑定历史 Run 与当前工程 CAS，旧形状不发送', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(handoffEnvelope()))
    vi.stubGlobal('fetch', fetchMock)
    const gateway = new FetchStudioGateway()
    const command: StudioCommand = {
      operation: 'rerun_from_here', run_id: handoffFixtureIds.run, node_id: 'transform',
      project_session_id: projectSessionId, expected_storage_revision: 7,
    }
    await gateway.command(command)
    expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      method: 'POST', body: JSON.stringify(command),
    }))
    const old = { operation: 'rerun_from_here', run_id: handoffFixtureIds.run, node_id: 'transform' } as unknown as StudioCommand
    await expect(gateway.command(old)).rejects.toThrow(StudioContractError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('按资源 endpoint 定向读取 summary/detail/log/readiness 并编码 query', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(handoffEnvelope()))
      .mockResolvedValueOnce(response({
        contract_version: '0.3.0',
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

  it('从独立只读 endpoint 获取严格 Presentation catalog', async () => {
    const envelope: PresentationCatalogEnvelopeWire = {
      contract_version: '0.3.0',
      catalog: {
        contract_version: '0.3.0',
        locale: 'zh-CN',
        categories: [{ category_id: 'media', title: '媒体', description: null, order: 1 }],
        nodes: [{
          type_id: 'test.source', definition_version: '1.0.0', title: '媒体输入',
          description: '选择待处理媒体。', category_id: 'media', icon_token: 'source',
          palette_level: 'primary', keywords: [], parameter_groups: [], parameters: [],
          ports: [], card_summary_paths: [],
        }],
      },
      diagnostics: [],
    }
    const fetchMock = vi.fn().mockResolvedValue(response(envelope))
    vi.stubGlobal('fetch', fetchMock)

    const result = await new FetchStudioGateway('http://loopback.test').inspectPresentations()

    expect(result.catalog.nodes[0]?.title).toBe('媒体输入')
    expect(fetchMock).toHaveBeenCalledWith(
      'http://loopback.test/api/studio/presentations',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('即使 payload 通过 Schema，也拒绝与请求不一致的资源 identity', async () => {
    const detail = handoffDetailEnvelope()
    const otherRunId = '00000000-0000-4000-8000-ffffffffffff'
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response({
          ...detail,
          run: {
            ...detail.run,
            run_id: otherRunId,
            node_runs: detail.run.node_runs.map((nodeRun) => ({
              ...nodeRun,
              run_id: otherRunId,
            })),
          },
        }),
      ),
    )

    await expect(new FetchStudioGateway().inspectRun(handoffFixtureIds.run)).rejects.toThrow(
      /run_id 与请求资源不一致/,
    )
  })
})
