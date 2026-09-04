import { describe, expect, it } from 'vitest'
import {
  parseAvEnhanceV27TemplatePreviewEnvelope,
  parseAvEnhanceV27TemplatePreviewRequest,
  parseExternalHandoffReadiness,
  parseNodeLogEnvelope,
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
  runningProgressDetail,
  studioEnvelope,
} from './test-fixtures'

describe('Studio Project Service 0.2.1 contract', () => {
  it('分别解析轻量 status、Run detail、日志、readiness 与历史页', () => {
    const status = parseStatusEnvelope(handoffEnvelope())
    const detail = parseRunDetailEnvelope(handoffDetailEnvelope())
    const log = parseNodeLogEnvelope(handoffLogEnvelope())
    const readiness = parseExternalHandoffReadiness(handoffReadinessEnvelope('present'))
    const page = parseRunSummaryPageEnvelope({
      contract_version: '0.2.1',
      run_summaries: [handoffSummary()],
      next_run_cursor: 'cursor.synthetic',
    })

    expect(status.contract_version).toBe('0.2.1')
    expect(Object.keys(status).sort()).toEqual([
      'active_operation',
      'active_run_id',
      'contract_version',
      'error',
      'latest_results',
      'next_run_cursor',
      'project_path',
      'run_summaries',
      'snapshot',
    ])
    expect(detail.run.node_runs[1]?.state).toBe('waiting_external')
    expect(detail.artifacts[0]?.path).toContain('source.mkv')
    expect(log.log.stdout_available).toBe(true)
    expect(readiness.targets[0]?.state).toBe('present')
    expect(page.next_run_cursor).toBe('cursor.synthetic')
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
      contract_version: '0.2.1' as const,
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
  })

  it.each(['missing', 'empty', 'present', 'probe_passed', 'probe_failed'] as const)(
    '接受 readiness 状态 %s，且仍要求 exact 0.2.1 envelope',
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
    expect(() => parseStatusEnvelope({ ...studioEnvelope(), contract_version: '0.2.0' })).toThrow(
      /Python 0\.2\.1 Schema/,
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
