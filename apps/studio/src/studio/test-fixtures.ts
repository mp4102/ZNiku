/** 纯合成 Studio 0.2.1 fixtures；不包含真实媒体、主机路径或外部副作用。 */

import type {
  ArtifactWire,
  ExternalHandoffReadiness,
  NodeLogEnvelope,
  NodeRunWire,
  NodeDefinitionWire,
  ProjectSnapshotWire,
  RunDetailEnvelope,
  RunSummaryWire,
  RunWire,
  StatusEnvelope,
} from './contracts'

const emptySchema = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  type: 'object',
  properties: {},
  additionalProperties: false,
} as const

export const sourceDefinition: NodeDefinitionWire = {
  type_id: 'test.source',
  version: '0.2.0',
  input_ports: [],
  output_ports: [{ port_id: 'out', data_type: 'VideoFile', cardinality: 'one', required: false }],
  parameter_schema: emptySchema,
  execution_mode: 'automatic',
  executor: { kind: 'python', adapter: 'tests.source' },
  validator: null,
}

export const transformDefinition: NodeDefinitionWire = {
  type_id: 'test.transform',
  version: '0.2.0',
  input_ports: [{ port_id: 'in', data_type: 'VideoFile', cardinality: 'one', required: true }],
  output_ports: [{ port_id: 'out', data_type: 'VideoFile', cardinality: 'one', required: false }],
  parameter_schema: {
    $schema: 'https://json-schema.org/draft/2020-12/schema',
    type: 'object',
    properties: {
      strength: { type: 'integer', minimum: 1, maximum: 9, default: 3 },
      model_name: { type: 'string', default: 'Synthetic Model' },
    },
    additionalProperties: false,
  },
  execution_mode: 'manual_external',
  executor: { kind: 'manual_external', instructions: '处理输入并写入目标路径。' },
  validator: null,
}

export const sinkDefinition: NodeDefinitionWire = {
  type_id: 'test.sink',
  version: '0.2.0',
  input_ports: [{ port_id: 'in', data_type: 'VideoFile', cardinality: 'one', required: true }],
  output_ports: [],
  parameter_schema: emptySchema,
  execution_mode: 'automatic',
  executor: { kind: 'python', adapter: 'tests.sink' },
  validator: null,
}

export const dataSourceDefinition: NodeDefinitionWire = {
  type_id: 'test.data-source',
  version: '0.2.0',
  input_ports: [],
  output_ports: [{ port_id: 'out', data_type: 'DataFile', cardinality: 'one', required: false }],
  parameter_schema: emptySchema,
  execution_mode: 'automatic',
  executor: { kind: 'command', executable: 'synthetic-tool', argv: ['--data'] },
  validator: null,
}

export const mergeDefinition: NodeDefinitionWire = {
  type_id: 'test.merge',
  version: '0.2.0',
  input_ports: [
    { port_id: 'items', data_type: 'VideoFile', cardinality: 'ordered_many', required: true },
  ],
  output_ports: [{ port_id: 'out', data_type: 'VideoFile', cardinality: 'one', required: false }],
  parameter_schema: emptySchema,
  execution_mode: 'automatic',
  executor: { kind: 'command', executable: 'synthetic-tool', argv: ['--merge'] },
  validator: null,
}

export const projectSnapshot: ProjectSnapshotWire = {
  project: {
    project_id: 'project.synthetic',
    name: 'Synthetic Studio Project',
    graph: {
      nodes: [
        {
          node_id: 'source',
          type_id: sourceDefinition.type_id,
          definition_version: sourceDefinition.version,
          parameters: {},
          ui_position: { x: 80, y: 180 },
        },
        {
          node_id: 'transform',
          type_id: transformDefinition.type_id,
          definition_version: transformDefinition.version,
          parameters: { strength: 3, model_name: 'Synthetic Model' },
          ui_position: { x: 360, y: 180 },
        },
        {
          node_id: 'sink',
          type_id: sinkDefinition.type_id,
          definition_version: sinkDefinition.version,
          parameters: {},
          ui_position: { x: 640, y: 180 },
        },
      ],
      edges: [
        {
          source_node_id: 'source',
          source_port_id: 'out',
          target_node_id: 'transform',
          target_port_id: 'in',
          ordinal: null,
        },
        {
          source_node_id: 'transform',
          source_port_id: 'out',
          target_node_id: 'sink',
          target_port_id: 'in',
          ordinal: null,
        },
      ],
    },
  },
  definitions: [
    sourceDefinition,
    transformDefinition,
    sinkDefinition,
    dataSourceDefinition,
    mergeDefinition,
  ],
}

export const handoffFixtureIds = {
  run: '00000000-0000-4000-8000-000000000010',
  sourceNodeRun: '00000000-0000-4000-8000-000000000011',
  transformNodeRun: '00000000-0000-4000-8000-000000000012',
  sinkNodeRun: '00000000-0000-4000-8000-000000000013',
  artifact: '00000000-0000-4000-8000-000000000001',
  handoff: '00000000-0000-4000-8000-000000000030',
} as const

const inputArtifact: ArtifactWire = {
  artifact_id: handoffFixtureIds.artifact,
  kind: 'VideoFile',
  path: 'C:\\synthetic\\source.mkv',
  producer_node_run_id: handoffFixtureIds.sourceNodeRun,
  producer_port_id: 'out',
  ordinal: null,
  frame_range: null,
  media_info: {},
  size: 1024,
  mtime_ns: 1,
}

function summary(
  overrides: Partial<RunSummaryWire> & Pick<RunSummaryWire, 'run_id' | 'state' | 'created_at'>,
): RunSummaryWire {
  const stateCounts = overrides.state_counts ?? {
    pending: 0,
    running: 0,
    waiting_external: 0,
    completed: 1,
    failed: 0,
  }
  const baseline: RunSummaryWire = {
    run_id: overrides.run_id,
    project_id: projectSnapshot.project.project_id,
    target_mode: 'all',
    selected_targets: [],
    state: overrides.state,
    node_count:
      stateCounts.pending +
      stateCounts.running +
      stateCounts.waiting_external +
      stateCounts.completed +
      stateCounts.failed,
    state_counts: stateCounts,
    actionable: overrides.state === 'pending' || overrides.state === 'running',
    requires_operator_action:
      overrides.state === 'failed' ||
      stateCounts.waiting_external > 0 ||
      stateCounts.failed > 0,
    created_at: overrides.created_at,
    started_at: overrides.started_at ?? overrides.created_at,
    ended_at:
      overrides.ended_at ??
      (overrides.state === 'completed' || overrides.state === 'failed'
        ? overrides.created_at
        : null),
    latest_activity_at: overrides.latest_activity_at ?? overrides.created_at,
    error: overrides.error ?? null,
  }
  return { ...baseline, ...overrides }
}

export function studioEnvelope(overrides: Partial<StatusEnvelope> = {}): StatusEnvelope {
  return {
    contract_version: '0.2.1',
    project_path: 'C:\\synthetic\\project.zniku',
    snapshot: projectSnapshot,
    run_summaries: [],
    next_run_cursor: null,
    active_run_id: null,
    active_operation: null,
    latest_results: [],
    error: null,
    ...overrides,
  }
}

export function handoffRun(): RunWire {
  const source: NodeRunWire = {
    node_run_id: handoffFixtureIds.sourceNodeRun,
    run_id: handoffFixtureIds.run,
    node_id: 'source',
    definition_version: '0.2.0',
    attempt: 1,
    state: 'completed',
    input_artifact_ids: [],
    output_artifact_ids: [inputArtifact.artifact_id],
    created_at: '2026-08-24T00:00:00Z',
    work_dir: 'C:\\synthetic\\attempt-source',
    started_at: '2026-08-24T00:00:00Z',
    ended_at: '2026-08-24T00:00:01Z',
    progress: 1,
    exit_code: 0,
    log_path: 'C:\\synthetic\\attempt-source\\logs',
    error: null,
    reused_from_result_id: null,
    external_handoff: null,
  }
  const transform: NodeRunWire = {
    node_run_id: handoffFixtureIds.transformNodeRun,
    run_id: handoffFixtureIds.run,
    node_id: 'transform',
    definition_version: '0.2.0',
    attempt: 1,
    state: 'waiting_external',
    input_artifact_ids: [inputArtifact.artifact_id],
    output_artifact_ids: [],
    created_at: '2026-08-24T00:00:01Z',
    work_dir: 'C:\\synthetic\\attempt-transform',
    started_at: '2026-08-24T00:00:01Z',
    ended_at: null,
    progress: null,
    exit_code: null,
    log_path: 'C:\\synthetic\\attempt-transform\\logs',
    error: null,
    reused_from_result_id: null,
    external_handoff: {
      handoff_id: handoffFixtureIds.handoff,
      node_run_id: handoffFixtureIds.transformNodeRun,
      input_artifact_ids: [inputArtifact.artifact_id],
      output_targets: [
        {
          port_id: 'out',
          path: 'C:\\synthetic\\attempt-transform\\output.mkv',
          ordinal: null,
        },
      ],
      instructions: '使用外部工具处理后写入目标路径。',
      created_at: '2026-08-24T00:00:01Z',
    },
  }
  const sink: NodeRunWire = {
    node_run_id: handoffFixtureIds.sinkNodeRun,
    run_id: handoffFixtureIds.run,
    node_id: 'sink',
    definition_version: '0.2.0',
    attempt: 1,
    state: 'pending',
    input_artifact_ids: [],
    output_artifact_ids: [],
    created_at: '2026-08-24T00:00:01Z',
    work_dir: 'C:\\synthetic\\attempt-sink',
    started_at: null,
    ended_at: null,
    progress: null,
    exit_code: null,
    log_path: null,
    error: null,
    reused_from_result_id: null,
    external_handoff: null,
  }
  return {
    run_id: handoffFixtureIds.run,
    project_id: projectSnapshot.project.project_id,
    graph_snapshot: projectSnapshot.project.graph,
    definitions_snapshot: projectSnapshot.definitions,
    selected_targets: [],
    state: 'running',
    node_runs: [source, transform, sink],
    created_at: '2026-08-24T00:00:00Z',
    started_at: '2026-08-24T00:00:00Z',
    ended_at: null,
    error: null,
  }
}

export function handoffSummary(): RunSummaryWire {
  return summary({
    run_id: handoffFixtureIds.run,
    state: 'running',
    created_at: '2026-08-24T00:00:00Z',
    latest_activity_at: '2026-08-24T00:00:01Z',
    state_counts: { pending: 1, running: 0, waiting_external: 1, completed: 1, failed: 0 },
  })
}

export function handoffEnvelope(): StatusEnvelope {
  return studioEnvelope({
    active_run_id: handoffFixtureIds.run,
    run_summaries: [handoffSummary()],
    latest_results: [
      {
        node_id: 'source',
        result_id: '00000000-0000-4000-8000-000000000021',
        stale: false,
        stale_reason: null,
        updated_at: '2026-08-24T00:00:01Z',
      },
    ],
  })
}

export function handoffDetailEnvelope(): RunDetailEnvelope {
  return {
    contract_version: '0.2.1',
    run: handoffRun(),
    artifacts: [inputArtifact],
    progress_samples: [],
  }
}

export function handoffReadinessEnvelope(
  state: ExternalHandoffReadiness['targets'][number]['state'] = 'present',
  probeRequested = false,
): ExternalHandoffReadiness {
  return {
    contract_version: '0.2.1',
    run_id: handoffFixtureIds.run,
    node_run_id: handoffFixtureIds.transformNodeRun,
    handoff_id: handoffFixtureIds.handoff,
    checked_at: '2026-08-24T00:00:02Z',
    probe_requested: probeRequested,
    ready_for_submit: probeRequested && state === 'probe_passed',
    targets: [
      {
        port_id: 'out',
        ordinal: null,
        path: 'C:\\synthetic\\attempt-transform\\output.mkv',
        state,
        size: state === 'missing' ? null : state === 'empty' ? 0 : 2048,
        mtime_ns: state === 'missing' ? null : 2,
        message: state === 'probe_failed' ? 'synthetic probe failed' : null,
      },
    ],
  }
}

export function handoffLogEnvelope(): NodeLogEnvelope {
  return {
    contract_version: '0.2.1',
    run_id: handoffFixtureIds.run,
    log: {
      node_run_id: handoffFixtureIds.transformNodeRun,
      stdout: '等待外部输出',
      stderr: '',
      stdout_available: true,
      stderr_available: true,
      stdout_truncated: false,
      stderr_truncated: false,
    },
  }
}

/** 固定现场问题：最新 completed 局部 Run 的 active 身份不得覆盖中间 actionable 整图 Run。 */
export const threeRunFixtureIds = {
  earlierLocalRun: '00000000-0000-4000-8000-000000000040',
  waitingFullRun: handoffFixtureIds.run,
  laterLocalRun: '00000000-0000-4000-8000-000000000060',
} as const

function completedLocalSummary(runId: string, timestamp: string): RunSummaryWire {
  return summary({
    run_id: runId,
    state: 'completed',
    created_at: timestamp,
    target_mode: 'selected',
    selected_targets: ['source'],
  })
}

function completedLocalDetail(runId: string, timestamp: string): RunDetailEnvelope {
  const nodeRunId = runId === threeRunFixtureIds.earlierLocalRun
    ? '00000000-0000-4000-8000-000000000041'
    : '00000000-0000-4000-8000-000000000061'
  const artifactId = runId === threeRunFixtureIds.earlierLocalRun
    ? '00000000-0000-4000-8000-000000000072'
    : '00000000-0000-4000-8000-000000000073'
  const artifact: ArtifactWire = {
    ...inputArtifact,
    artifact_id: artifactId,
    producer_node_run_id: nodeRunId,
    path: `C:\\synthetic\\local-${runId.slice(-2)}\\source.mkv`,
  }
  const source = handoffRun().node_runs[0]!
  return {
    contract_version: '0.2.1',
    artifacts: [artifact],
    progress_samples: [],
    run: {
      ...handoffRun(),
      run_id: runId,
      selected_targets: ['source'],
      state: 'completed',
      created_at: timestamp,
      started_at: timestamp,
      ended_at: timestamp,
      node_runs: [
        {
          ...source,
          node_run_id: nodeRunId,
          run_id: runId,
          output_artifact_ids: [artifactId],
          created_at: timestamp,
          started_at: timestamp,
          ended_at: timestamp,
          work_dir: `C:\\synthetic\\local-${runId.slice(-2)}\\source`,
          log_path: `C:\\synthetic\\local-${runId.slice(-2)}\\source\\logs`,
        },
      ],
    },
  }
}

export function threeRunEnvelope(): StatusEnvelope {
  return studioEnvelope({
    active_run_id: threeRunFixtureIds.laterLocalRun,
    active_operation: null,
    run_summaries: [
      completedLocalSummary(threeRunFixtureIds.laterLocalRun, '2026-08-24T00:02:00Z'),
      handoffSummary(),
      completedLocalSummary(threeRunFixtureIds.earlierLocalRun, '2026-08-23T23:59:30Z'),
    ],
  })
}

export function threeRunDetail(runId: string): RunDetailEnvelope {
  if (runId === threeRunFixtureIds.waitingFullRun) return handoffDetailEnvelope()
  return completedLocalDetail(
    runId,
    runId === threeRunFixtureIds.laterLocalRun
      ? '2026-08-24T00:02:00Z'
      : '2026-08-23T23:59:30Z',
  )
}

/** 构造 automatic Source 节点的受控进度响应，用于验证轮询与乱序响应。 */
export function runningProgressEnvelope(
  progress: number,
  activeOperation: StatusEnvelope['active_operation'] = 'run_all',
): StatusEnvelope {
  return studioEnvelope({
    active_run_id: handoffFixtureIds.run,
    active_operation: activeOperation,
    run_summaries: [
      summary({
        run_id: handoffFixtureIds.run,
        state: 'running',
        created_at: '2026-08-24T00:00:00Z',
        latest_activity_at: `2026-08-24T00:00:0${Math.min(9, Math.ceil(progress * 9))}Z`,
        state_counts: { pending: 2, running: 1, waiting_external: 0, completed: 0, failed: 0 },
      }),
    ],
  })
}

export function runningProgressDetail(progress: number): RunDetailEnvelope {
  const run = handoffRun()
  const source = run.node_runs[0]!
  const transform = run.node_runs[1]!
  const sink = run.node_runs[2]!
  return {
    contract_version: '0.2.1',
    artifacts: [],
    progress_samples: [],
    run: {
      ...run,
      node_runs: [
        {
          ...source,
          state: 'running',
          output_artifact_ids: [],
          ended_at: null,
          progress,
          exit_code: null,
        },
        {
          ...transform,
          state: 'pending',
          input_artifact_ids: [],
          started_at: null,
          progress: null,
          log_path: null,
          external_handoff: null,
        },
        sink,
      ],
    },
  }
}

export function failedStatusEnvelope(reason: 'cancelled' | 'interrupted'): StatusEnvelope {
  const message = reason === 'cancelled' ? '操作者取消当前 attempt' : '应用重启中断当前 attempt'
  return studioEnvelope({
    active_run_id: handoffFixtureIds.run,
    run_summaries: [
      summary({
        run_id: handoffFixtureIds.run,
        state: 'failed',
        created_at: '2026-08-24T00:00:00Z',
        ended_at: '2026-08-24T00:00:03Z',
        latest_activity_at: '2026-08-24T00:00:03Z',
        state_counts: { pending: 1, running: 0, waiting_external: 0, completed: 1, failed: 1 },
        error: { reason, message },
      }),
    ],
    latest_results: [
      {
        node_id: 'source',
        result_id: '00000000-0000-4000-8000-000000000021',
        stale: true,
        stale_reason: 'graph_changed',
        updated_at: '2026-08-24T00:00:03Z',
      },
    ],
  })
}

export function failedDetailEnvelope(reason: 'cancelled' | 'interrupted'): RunDetailEnvelope {
  const base = handoffDetailEnvelope()
  const message = reason === 'cancelled' ? '操作者取消当前 attempt' : '应用重启中断当前 attempt'
  return {
    ...base,
    run: {
      ...base.run,
      state: 'failed',
      ended_at: '2026-08-24T00:00:03Z',
      error: { reason, message },
      node_runs: base.run.node_runs.map((item) =>
        item.node_id === 'transform'
          ? {
              ...item,
              state: 'failed',
              ended_at: '2026-08-24T00:00:03Z',
              progress: 0.4,
              error: { reason, message },
              external_handoff: null,
            }
          : item,
      ),
    },
  }
}
