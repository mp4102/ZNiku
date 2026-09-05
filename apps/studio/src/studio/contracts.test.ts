import { describe, expect, it } from 'vitest'
import {
  parseAvEnhanceV27TemplatePreviewEnvelope,
  parseAvEnhanceV27TemplatePreviewRequest,
  parseExternalHandoffReadiness,
  parseNodeLogEnvelope,
  parsePresentationCatalogEnvelope,
  parseRunDetailEnvelope,
  parseRunSummaryPageEnvelope,
  parseStatusEnvelope,
  parseStudioCommand,
  StudioContractError,
  type RunDetailEnvelope,
} from './contracts'
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
  runningProgressDetail,
  studioEnvelope,
} from './test-fixtures'

describe('Studio Project Service 0.3.0 contract', () => {
  it('分别解析轻量 status、Run detail、日志、readiness 与历史页', () => {
    const status = parseStatusEnvelope(handoffEnvelope())
    const detail = parseRunDetailEnvelope(handoffDetailEnvelope())
    const log = parseNodeLogEnvelope(handoffLogEnvelope())
    const readiness = parseExternalHandoffReadiness(handoffReadinessEnvelope('present'))
    const page = parseRunSummaryPageEnvelope({
      contract_version: '0.3.0',
      run_summaries: [handoffSummary()],
      next_run_cursor: 'cursor.synthetic',
    })

    expect(status.contract_version).toBe('0.3.0')
    expect(Object.keys(status).sort()).toEqual([
      'active_operation',
      'active_run_id',
      'authoring_diagnostics',
      'contract_version',
      'error',
      'latest_results',
      'next_run_cursor',
      'project_path',
      'project_session_id',
      'run_summaries',
      'snapshot',
      'storage_revision',
      'studio_state',
      'studio_warnings',
    ])
    expect(detail.run.node_runs[1]?.state).toBe('waiting_external')
    expect(detail.artifacts[0]?.path).toContain('source.mkv')
    expect(log.log.stdout_available).toBe(true)
    expect(readiness.targets[0]?.state).toBe('present')
    expect(page.next_run_cursor).toBe('cursor.synthetic')
  })

  it('工程、会话、存储计数和 StudioState 必须同有同无', () => {
    expect(parseStatusEnvelope(studioEnvelope({ snapshot: null })).snapshot).toBeNull()
    const fields = ['snapshot', 'project_path', 'project_session_id', 'storage_revision', 'studio_state'] as const
    for (const field of fields) {
      expect(() => parseStatusEnvelope({ ...studioEnvelope(), [field]: null })).toThrow(/authoring binding/)
    }
    for (const field of ['authoring_diagnostics', 'studio_warnings'] as const) {
      expect(() => parseStatusEnvelope({
        ...studioEnvelope({ snapshot: null }),
        [field]: field === 'authoring_diagnostics'
          ? [{ code: 'E_REQUIRED_INPUT_MISSING', path: 'graph.nodes.0', message: '请连接输入', validator_keyword: null }]
          : [{ code: 'W_STUDIO_STATE_RESET', message: '展示设置已恢复' }],
      })).toThrow(/authoring binding/)
    }
    expect(() => parseStatusEnvelope(studioEnvelope({ active_operation: 'run_all' })))
      .toThrow(/active_operation 必须绑定/)
    for (const storage_revision of [-1, 0.5, Number.MAX_SAFE_INTEGER + 1]) {
      expect(() => parseStatusEnvelope(studioEnvelope({ storage_revision }))).toThrow(StudioContractError)
    }
  })

  it('StudioState 只引用当前 Graph，拒绝重复、悬空引用和非法显示字段', () => {
    const state = {
      ...defaultStudioState,
      viewport: { x: 50, y: -100, zoom: 1.2 },
      groups: [{ group_id: 'chapter-a', title: '章节 A', color_token: 'blue' as const, collapsed: false }],
      node_views: [{ node_id: 'source', display_name: '素材 A', group_id: 'chapter-a', collapsed: false }],
    }
    expect(parseStatusEnvelope(studioEnvelope({ studio_state: state })).studio_state).toEqual(state)
    for (const patch of [
      { groups: [state.groups[0], state.groups[0]] },
      { node_views: [state.node_views[0], state.node_views[0]] },
      { node_views: [{ ...state.node_views[0], node_id: 'missing' }] },
      { node_views: [{ ...state.node_views[0], group_id: 'missing' }] },
      { node_views: [{ ...state.node_views[0], display_name: ' 名称' }] },
      { node_views: [{ ...state.node_views[0], display_name: '名称\u0000' }] },
      { groups: [{ ...state.groups[0], title: '章节 ' }] },
      { groups: [{ ...state.groups[0], title: '\u0000章节' }] },
    ]) {
      const studio_state = { ...state, ...patch }
      expect(() => parseStatusEnvelope(studioEnvelope({ studio_state }))).toThrow(/StudioState/)
      expect(() => parseStudioCommand({
        operation: 'save_project', project: projectSnapshot.project, studio_state,
        project_session_id: projectSessionId, expected_storage_revision: 1,
      })).toThrow(/StudioState/)
    }
    for (const patch of [
      { contract_version: '0.4.0' }, { unexpected: true },
      { viewport: { x: Infinity, y: 0, zoom: 1 } },
      { viewport: { x: 0, y: 0, zoom: 0 } },
      { groups: [{ ...state.groups[0], color_token: 'javascript' }] },
    ]) {
      expect(() => parseStatusEnvelope({ ...studioEnvelope(), studio_state: { ...state, ...patch } }))
        .toThrow(StudioContractError)
    }
  })

  it('保存 authoring diagnostics 不伪造可运行状态，执行命令必须携带当前 CAS', () => {
    const status = studioEnvelope({ authoring_diagnostics: [{
      code: 'E_REQUIRED_INPUT_MISSING', path: 'graph.nodes.1', message: '请连接视频输入', validator_keyword: null,
    }] })
    expect(parseStatusEnvelope(status).authoring_diagnostics).toEqual(status.authoring_diagnostics)
    for (const operation of ['run_all', 'run_to', 'rerun_from_here'] as const) {
      const command = {
        operation, ...(operation !== 'run_all' ? { node_id: 'sink' } : {}),
        ...(operation === 'rerun_from_here' ? { run_id: handoffFixtureIds.run } : {}),
        project_session_id: projectSessionId, expected_storage_revision: 2,
      }
      expect(parseStudioCommand(command)).toEqual(command)
      const { expected_storage_revision: _revision, ...missing } = command
      expect(() => parseStudioCommand(missing)).toThrow(StudioContractError)
      expect(() => parseStudioCommand({ ...command, project_session_id: 'old-page' })).toThrow(StudioContractError)
    }
  })

  it('按 Python Schema 解析 v2.7 template preview，并拒绝客户端 Graph 注入', () => {
    const request = {
      action: 'prepare' as const,
      request: {
        profile_version: '2.7.0' as const,
        project_path: 'C:\\synthetic\\av27.zniku',
        project_id: 'project.av27',
        project_name: 'Synthetic AV27',
        source_mode: 'program' as const,
        sources: [
          { source_path: 'C:\\synthetic\\source.mkv', source_ordinal: 0 },
        ],
        mr: { mode: 'off' as const },
      },
    }
    expect(parseAvEnhanceV27TemplatePreviewRequest(request)).toEqual(request)
    expect(parseStudioCommand({ operation: 'create_av_enhance_v27', request: request.request }))
      .toEqual({ operation: 'create_av_enhance_v27', request: request.request })
    expect(() =>
      parseAvEnhanceV27TemplatePreviewRequest({ ...request, graph: projectSnapshot.project.graph }),
    ).toThrow(StudioContractError)
    expect(() =>
      parseAvEnhanceV27TemplatePreviewRequest({
        ...request,
        request: { ...request.request, definitions: projectSnapshot.definitions },
      }),
    ).toThrow(StudioContractError)
    expect(() =>
      parseStudioCommand({
        operation: 'create_av_enhance_v27',
        request: { ...request.request, graph: projectSnapshot.project.graph },
      }),
    ).toThrow(StudioContractError)

    const envelope = {
      contract_version: '0.3.0' as const,
      profile_version: '2.7.0' as const,
      phase: 'preparation' as const,
      project: projectSnapshot.project,
      definitions: projectSnapshot.definitions,
      profile: {
        profile_version: '2.7.0' as const,
        phase: 'preparation',
        status: 'preparation-compatible',
        compatible: true,
        diagnostics: [],
      },
      plan: {
        source_count: 1,
        chapter_count: 0,
        leaf_count: 0,
        mr_mode: 'off' as const,
        preparation_run_id: null,
        effective_video_artifact_ids: [],
        chapters: [],
        manual_stages: [],
        output_target_path: null,
      },
      creator: {
        analyzed: false,
        sources: [],
        estimated_step_count: projectSnapshot.project.graph.nodes.length,
        estimated_steps: `预计 ${projectSnapshot.project.graph.nodes.length} 个处理步骤`,
      },
    }
    expect(parseAvEnhanceV27TemplatePreviewEnvelope(envelope).phase).toBe('preparation')
    expect(() =>
      parseAvEnhanceV27TemplatePreviewEnvelope({ ...envelope, profile_version: '2.8.0' }),
    ).toThrow(StudioContractError)
    expect(() =>
      parseAvEnhanceV27TemplatePreviewEnvelope({ ...envelope, unexpected: true }),
    ).toThrow(StudioContractError)
    expect(() =>
      parseAvEnhanceV27TemplatePreviewEnvelope({
        ...envelope,
        profile: { ...envelope.profile, phase: 'expanded', status: 'expanded-compatible' },
      }),
    ).toThrow(/phase\/status\/compatible\/diagnostics 不一致/)
    expect(() =>
      parseAvEnhanceV27TemplatePreviewEnvelope({
        ...envelope,
        profile: { ...envelope.profile, compatible: false },
      }),
    ).toThrow(/phase\/status\/compatible\/diagnostics 不一致/)
    expect(() => parseAvEnhanceV27TemplatePreviewEnvelope({
      ...envelope,
      creator: { ...envelope.creator, analyzed: true },
    })).toThrow(/creator 跨字段语义不一致/)
    expect(() => parseAvEnhanceV27TemplatePreviewEnvelope({
      ...envelope,
      creator: { ...envelope.creator, estimated_steps: '预计很多步骤' },
    })).toThrow(/creator 跨字段语义不一致/)
    expect(() => parseAvEnhanceV27TemplatePreviewEnvelope({
      ...envelope,
      creator: { ...envelope.creator, estimated_step_count: 999, estimated_steps: '预计 999 个处理步骤' },
    })).toThrow(/creator 跨字段语义不一致/)

    const sourceSummary = {
      source_ordinal: 0,
      chapter_label: null,
      display_name: 'source.mkv',
      size_bytes: 1024,
      size_label: '1 KiB',
      container: 'Matroska',
      video_codec: 'HEVC',
      pixel_format: 'yuv420p10le',
      resolution: '1920 × 1080',
      frame_rate: '30000/1001 fps（29.970）',
      duration: '1 分 0 秒',
      frame_count: '1,801 帧',
      audio_tracks: [],
    }
    const expandedEnvelope = {
      ...envelope,
      phase: 'expanded' as const,
      profile: {
        ...envelope.profile,
        phase: 'expanded' as const,
        status: 'expanded-compatible' as const,
      },
      plan: {
        ...envelope.plan,
        preparation_run_id: '00000000-0000-4000-8000-000000000027',
      },
      creator: {
        ...envelope.creator,
        analyzed: true,
        sources: [sourceSummary],
      },
    }
    expect(parseAvEnhanceV27TemplatePreviewEnvelope(expandedEnvelope).creator.sources)
      .toHaveLength(1)
    expect(() => parseAvEnhanceV27TemplatePreviewEnvelope({
      ...expandedEnvelope,
      creator: {
        ...expandedEnvelope.creator,
        sources: [{ ...sourceSummary, source_ordinal: 1 }],
      },
    })).toThrow(/creator 跨字段语义不一致/)
    expect(() => parseAvEnhanceV27TemplatePreviewEnvelope({
      ...expandedEnvelope,
      creator: {
        ...expandedEnvelope.creator,
        sources: [{ ...sourceSummary, display_name: 'D:\\secret\\source.mkv' }],
      },
    })).toThrow(StudioContractError)
    expect(() => parseAvEnhanceV27TemplatePreviewEnvelope({
      ...expandedEnvelope,
      creator: {
        ...expandedEnvelope.creator,
        sources: [{ ...sourceSummary, display_name: '.' }],
      },
    })).toThrow(/creator 跨字段语义不一致/)
  })

  it.each(['missing', 'empty', 'present', 'probe_passed', 'probe_failed'] as const)(
    '接受 readiness 状态 %s，且仍要求 exact 0.3.0 envelope',
    (state) => {
      const parsed = parseExternalHandoffReadiness(
        handoffReadinessEnvelope(state, state === 'probe_passed' || state === 'probe_failed'),
      )
      expect(parsed.targets[0]?.state).toBe(state)
    },
  )

  it('未知字段、非法版本及未知 Runtime 状态默认失败关闭', () => {
    expect(() => parseStatusEnvelope({})).toThrow(StudioContractError)
    expect(() => parseStatusEnvelope({ ...studioEnvelope(), unexpected: true })).toThrow(
      StudioContractError,
    )
    expect(() => parseStatusEnvelope({ ...studioEnvelope(), contract_version: '0.2.1' })).toThrow(
      /Python 0\.3\.0 Schema/,
    )
    expect(() => parseStatusEnvelope({ ...studioEnvelope(), active_operation: 'save_project' }))
      .toThrow(StudioContractError)

    const invalidTimestamp = structuredClone(handoffEnvelope()) as unknown as {
      run_summaries: Array<{ created_at: string }>
    }
    invalidTimestamp.run_summaries[0]!.created_at = 'not-a-utc-timestamp'
    expect(() => parseStatusEnvelope(invalidTimestamp)).toThrow(StudioContractError)

    const invalidState = structuredClone(handoffDetailEnvelope()) as unknown as {
      run: { node_runs: Array<{ state: string }> }
    }
    invalidState.run.node_runs[0]!.state = 'resuming'
    expect(() => parseRunDetailEnvelope(invalidState)).toThrow(StudioContractError)

    const missingDefaultedField = structuredClone(handoffDetailEnvelope()) as unknown as {
      run: { node_runs: Array<{ progress?: number | null }> }
    }
    delete missingDefaultedField.run.node_runs[0]!.progress
    expect(() => parseRunDetailEnvelope(missingDefaultedField)).toThrow(StudioContractError)

    expect(() =>
      parseExternalHandoffReadiness({
        ...handoffReadinessEnvelope(),
        targets: [{ ...handoffReadinessEnvelope().targets[0], state: 'unknown' }],
      }),
    ).toThrow(StudioContractError)
  })

  it('严格解析独立 Presentation catalog，并拒绝未知字段与非法 icon token', () => {
    const envelope = {
      contract_version: '0.3.0' as const,
      catalog: {
        contract_version: '0.3.0' as const,
        locale: 'zh-CN' as const,
        categories: [{ category_id: 'media', title: '媒体', description: null, order: 1 }],
        nodes: [{
          type_id: 'test.source',
          definition_version: '1.0.0',
          title: '媒体输入',
          description: '选择待处理媒体。',
          category_id: 'media',
          icon_token: 'source' as const,
          palette_level: 'primary' as const,
          keywords: ['输入'],
          parameter_groups: [],
          parameters: [],
          ports: [{ direction: 'output' as const, port_id: 'media', label: '媒体', description: null }],
          card_summary_paths: [],
        }],
      },
      diagnostics: [],
    }
    expect(parsePresentationCatalogEnvelope(envelope).catalog.nodes[0]?.title).toBe('媒体输入')
    expect(() => parsePresentationCatalogEnvelope({ ...envelope, unexpected: true })).toThrow(StudioContractError)
    expect(() => parsePresentationCatalogEnvelope({
      ...envelope,
      catalog: { ...envelope.catalog, nodes: [{ ...envelope.catalog.nodes[0]!, icon_token: 'script' }] },
    })).toThrow(StudioContractError)
  })

  it('严格校验 progress sample 测量组合、Run binding 与 automatic latest attempt', () => {
    const base = runningProgressDetail(0.2)
    const valid = {
      ...base,
      progress_samples: [
        {
          node_run_id: handoffFixtureIds.sourceNodeRun,
          fraction: 0.8000000005,
          current: 80,
          total: 100,
          unit: 'frames' as const,
          observed_at: '2026-08-24T00:00:02Z',
        },
      ],
    }
    expect(parseRunDetailEnvelope(valid).progress_samples[0]?.current).toBe(80)

    const outsideParent = {
      ...valid,
      run: {
        ...valid.run,
        node_runs: valid.run.node_runs.map((item) =>
          item.node_run_id === handoffFixtureIds.sourceNodeRun
            ? { ...item, run_id: '00000000-0000-4000-8000-00000000f00d' }
            : item,
        ),
      },
    }
    expect(() => parseRunDetailEnvelope(outsideParent)).toThrow(StudioContractError)

    const commandProjection = {
      ...valid,
      run: {
        ...valid.run,
        definitions_snapshot: valid.run.definitions_snapshot.map((definition) =>
          definition.type_id === 'test.source'
            ? {
                ...definition,
                executor: {
                  kind: 'command' as const,
                  executable: 'synthetic-tool',
                  argv: ['--progress-like-output'],
                  output_paths: [],
                },
              }
            : definition,
        ),
      },
    }
    expect(() => parseRunDetailEnvelope(commandProjection)).toThrow(StudioContractError)

    const invalidSamples = [
      { ...valid.progress_samples[0]!, current: 1, total: null, unit: null },
      { ...valid.progress_samples[0]!, fraction: 0.8, current: 101, total: 100 },
      { ...valid.progress_samples[0]!, fraction: 0.7 },
      { ...valid.progress_samples[0]!, fraction: 0.1 },
      { ...valid.progress_samples[0]!, unit: 'seconds' },
      { ...valid.progress_samples[0]!, observed_at: 'not-a-timestamp' },
      { ...valid.progress_samples[0]!, unexpected: true },
      {
        ...valid.progress_samples[0]!,
        node_run_id: '00000000-0000-4000-8000-ffffffffffff',
      },
    ]
    for (const sample of invalidSamples) {
      expect(() => parseRunDetailEnvelope({ ...base, progress_samples: [sample] })).toThrow(
        StudioContractError,
      )
    }
    expect(() =>
      parseRunDetailEnvelope({
        ...base,
        progress_samples: [valid.progress_samples[0], valid.progress_samples[0]],
      }),
    ).toThrow(/重复/)

    const terminal = handoffDetailEnvelope()
    expect(() =>
      parseRunDetailEnvelope({ ...terminal, progress_samples: [valid.progress_samples[0]] }),
    ).toThrow(/不是 running attempt/)

    const manual = structuredClone(base) as unknown as {
      run: RunDetailEnvelope['run'] & { node_runs: Array<Record<string, unknown>> }
      progress_samples: unknown[]
    }
    const manualNodeRun = manual.run.node_runs.find((item) => item.node_id === 'transform')!
    Object.assign(manualNodeRun, {
      state: 'running',
      started_at: '2026-08-24T00:00:02Z',
      progress: null,
      external_handoff: null,
    })
    manual.progress_samples = [
      {
        ...valid.progress_samples[0],
        node_run_id: handoffFixtureIds.transformNodeRun,
        fraction: 0.5,
        current: null,
        total: null,
        unit: null,
      },
    ]
    expect(() => parseRunDetailEnvelope(manual)).toThrow(/不是 automatic Python executor/)

    const oldAttempt = structuredClone(base) as unknown as {
      run: RunDetailEnvelope['run'] & { node_runs: Array<Record<string, unknown>> }
      progress_samples: unknown[]
    }
    oldAttempt.run.node_runs.push({
      ...oldAttempt.run.node_runs[0]!,
      node_run_id: '00000000-0000-4000-8000-000000000099',
      attempt: 2,
      progress: null,
    })
    oldAttempt.progress_samples = [{ ...valid.progress_samples[0], fraction: 0.5, current: null, total: null, unit: null }]
    expect(() => parseRunDetailEnvelope(oldAttempt)).toThrow(/不是节点的唯一最新 attempt/)
  })

  it('handoff display projection 拒绝错 Run/attempt/input、重复项与未知字段', () => {
    const base = handoffDetailEnvelope()
    const projection = {
      node_run_id: handoffFixtureIds.transformNodeRun,
      handoff_id: handoffFixtureIds.handoff,
      input_artifact_id: base.artifacts[0]!.artifact_id,
      title: 'Synthetic 输出合同',
      fields: [{ label: '输入 exact N', value: '100' }],
    }
    const valid = { ...base, handoff_contracts: [projection] }
    expect(parseRunDetailEnvelope(valid).handoff_contracts[0]?.fields[0]?.value).toBe('100')
    for (const patch of [
      { node_run_id: handoffFixtureIds.sourceNodeRun },
      { handoff_id: 'wrong' },
      { input_artifact_id: 'unknown' },
      { unexpected: true },
      { fields: [{ label: 'N', value: 100 }] },
    ]) {
      expect(() => parseRunDetailEnvelope({ ...base, handoff_contracts: [{ ...projection, ...patch }] })).toThrow(StudioContractError)
    }
    expect(() => parseRunDetailEnvelope({ ...base, handoff_contracts: [projection, projection] })).toThrow(StudioContractError)
  })

  it('active_operation 与 command 均覆盖 abandon_run 和精确 Submit identity', () => {
    expect(parseStatusEnvelope({ ...handoffEnvelope(), active_operation: 'abandon_run' }))
      .toMatchObject({ active_operation: 'abandon_run' })
    expect(parseStudioCommand({
      operation: 'submit_external',
      run_id: handoffFixtureIds.run,
      node_run_id: handoffFixtureIds.transformNodeRun,
      handoff_id: handoffFixtureIds.handoff,
    })).toEqual({
      operation: 'submit_external',
      run_id: handoffFixtureIds.run,
      node_run_id: handoffFixtureIds.transformNodeRun,
      handoff_id: handoffFixtureIds.handoff,
    })
    expect(parseStudioCommand({ operation: 'abandon_run', run_id: handoffFixtureIds.run }))
      .toEqual({ operation: 'abandon_run', run_id: handoffFixtureIds.run })
    expect(() => parseStudioCommand({
      operation: 'submit_external',
      node_run_id: handoffFixtureIds.transformNodeRun,
    })).toThrow(StudioContractError)
    expect(() => parseStudioCommand({ operation: 'run_all', unexpected: true }))
      .toThrow(StudioContractError)
    expect(() => parseStudioCommand({ operation: 'unknown' })).toThrow(StudioContractError)
  })
})
