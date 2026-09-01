/** 纯合成 Studio fixtures；不包含真实媒体、主机路径或外部副作用。 */

import type {
  ArtifactWire,
  NodeRunWire,
  NodeDefinitionWire,
  ProjectSnapshotWire,
  RunWire,
  StudioEnvelope,
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
    properties: { strength: { type: 'integer', minimum: 1, maximum: 9, default: 3 } },
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
          parameters: { strength: 3 },
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

const inputArtifact: ArtifactWire = {
  artifact_id: '00000000-0000-4000-8000-000000000001',
  kind: 'VideoFile',
  path: 'C:\\synthetic\\source.mkv',
  producer_node_run_id: '00000000-0000-4000-8000-000000000011',
  producer_port_id: 'out',
  ordinal: null,
  frame_range: null,
  media_info: {},
  size: 1024,
  mtime_ns: 1,
}

export function studioEnvelope(overrides: Partial<StudioEnvelope> = {}): StudioEnvelope {
  return {
    contract_version: '0.2.0',
    project_path: 'C:\\synthetic\\project.zniku',
    snapshot: projectSnapshot,
    runs: [],
    active_run_id: null,
    active_operation: null,
    latest_results: [],
    artifacts: [],
    logs: [],
    error: null,
    ...overrides,
  }
}

export function handoffEnvelope(): StudioEnvelope {
  const runId = '00000000-0000-4000-8000-000000000010'
  const sourceRunId = '00000000-0000-4000-8000-000000000011'
  const transformRunId = '00000000-0000-4000-8000-000000000012'
  return studioEnvelope({
    active_run_id: runId,
    artifacts: [inputArtifact],
    latest_results: [
      {
        node_id: 'source',
        result_id: '00000000-0000-4000-8000-000000000021',
        stale: false,
        stale_reason: null,
        updated_at: '2026-08-24T00:00:01Z',
      },
    ],
    logs: [
      {
        node_run_id: transformRunId,
        stdout: '等待外部输出',
        stderr: '',
        stdout_available: true,
        stderr_available: true,
        stdout_truncated: false,
        stderr_truncated: false,
      },
    ],
    runs: [
      {
        run_id: runId,
        project_id: projectSnapshot.project.project_id,
        graph_snapshot: projectSnapshot.project.graph,
        definitions_snapshot: projectSnapshot.definitions,
        selected_targets: [],
        state: 'running',
        created_at: '2026-08-24T00:00:00Z',
        started_at: '2026-08-24T00:00:00Z',
        ended_at: null,
        error: null,
        node_runs: [
          {
            node_run_id: sourceRunId,
            run_id: runId,
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
          },
          {
            node_run_id: transformRunId,
            run_id: runId,
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
            progress: 0,
            exit_code: null,
            log_path: 'C:\\synthetic\\attempt-transform\\logs',
            error: null,
            reused_from_result_id: null,
            external_handoff: {
              handoff_id: '00000000-0000-4000-8000-000000000030',
              node_run_id: transformRunId,
              input_artifact_ids: [inputArtifact.artifact_id],
              output_targets: [
                { port_id: 'out', path: 'C:\\synthetic\\attempt-transform\\output.mkv', ordinal: null },
              ],
              instructions: '使用外部工具处理后写入目标路径。',
              created_at: '2026-08-24T00:00:01Z',
            },
          },
        ],
      },
    ],
  })
}

/**
 * 固定现场问题的三个 Run：较早的局部完成、仍待人工交接的整图 Run、以及最后启动的局部完成。
 *
 * ``active_run_id`` 故意指向最后一个局部 Run；Studio 重载时仍应优先展示唯一 actionable 的整图
 * Run。所有标识、时间和路径均为合成值，不依赖本机媒体或历史数据库。
 */
export const threeRunFixtureIds = {
  earlierLocalRun: '00000000-0000-4000-8000-000000000040',
  waitingFullRun: '00000000-0000-4000-8000-000000000050',
  laterLocalRun: '00000000-0000-4000-8000-000000000060',
} as const

export function threeRunEnvelope(): StudioEnvelope {
  const base = handoffEnvelope()
  const waitingBase = base.runs[0]!
  const waitingSourceBase = waitingBase.node_runs[0]!
  const waitingTransformBase = waitingBase.node_runs[1]!
  const waitingSourceRunId = '00000000-0000-4000-8000-000000000051'
  const waitingTransformRunId = '00000000-0000-4000-8000-000000000052'
  const waitingArtifactId = '00000000-0000-4000-8000-000000000071'
  const waitingArtifact: ArtifactWire = {
    ...inputArtifact,
    artifact_id: waitingArtifactId,
    path: 'C:\\synthetic\\waiting-full\\source.mkv',
    producer_node_run_id: waitingSourceRunId,
  }
  const waitingSource: NodeRunWire = {
    ...waitingSourceBase,
    node_run_id: waitingSourceRunId,
    run_id: threeRunFixtureIds.waitingFullRun,
    output_artifact_ids: [waitingArtifactId],
    work_dir: 'C:\\synthetic\\waiting-full\\source',
    log_path: 'C:\\synthetic\\waiting-full\\source\\logs',
    created_at: '2026-08-24T00:01:00Z',
    started_at: '2026-08-24T00:01:00Z',
    ended_at: '2026-08-24T00:01:01Z',
  }
  const waitingTransform: NodeRunWire = {
    ...waitingTransformBase,
    node_run_id: waitingTransformRunId,
    run_id: threeRunFixtureIds.waitingFullRun,
    input_artifact_ids: [waitingArtifactId],
    work_dir: 'C:\\synthetic\\waiting-full\\transform',
    log_path: 'C:\\synthetic\\waiting-full\\transform\\logs',
    created_at: '2026-08-24T00:01:01Z',
    started_at: '2026-08-24T00:01:01Z',
    external_handoff: {
      handoff_id: '00000000-0000-4000-8000-000000000053',
      node_run_id: waitingTransformRunId,
      input_artifact_ids: [waitingArtifactId],
      output_targets: [
        {
          port_id: 'out',
          path: 'C:\\synthetic\\waiting-full\\transform\\output.mkv',
          ordinal: null,
        },
      ],
      instructions: '使用合成外部工具处理后写入目标路径。',
      created_at: '2026-08-24T00:01:01Z',
    },
  }
  const waitingSink: NodeRunWire = {
    node_run_id: '00000000-0000-4000-8000-000000000054',
    run_id: threeRunFixtureIds.waitingFullRun,
    node_id: 'sink',
    definition_version: '0.2.0',
    attempt: 1,
    state: 'pending',
    input_artifact_ids: [],
    output_artifact_ids: [],
    created_at: '2026-08-24T00:01:01Z',
    work_dir: 'C:\\synthetic\\waiting-full\\sink',
    started_at: null,
    ended_at: null,
    progress: null,
    exit_code: null,
    log_path: null,
    error: null,
    reused_from_result_id: null,
    external_handoff: null,
  }
  const waitingRun: RunWire = {
    ...waitingBase,
    run_id: threeRunFixtureIds.waitingFullRun,
    selected_targets: [],
    state: 'running',
    node_runs: [waitingSource, waitingTransform, waitingSink],
    created_at: '2026-08-24T00:01:00Z',
    started_at: '2026-08-24T00:01:00Z',
    ended_at: null,
  }

  const completedLocalRun = (
    runId: string,
    nodeRunId: string,
    artifactId: string,
    timestamp: string,
  ): { readonly run: RunWire; readonly artifact: ArtifactWire } => {
    const artifact: ArtifactWire = {
      ...inputArtifact,
      artifact_id: artifactId,
      path: `C:\\synthetic\\local-${runId.slice(-2)}\\source.mkv`,
      producer_node_run_id: nodeRunId,
    }
    const nodeRun: NodeRunWire = {
      ...waitingSourceBase,
      node_run_id: nodeRunId,
      run_id: runId,
      output_artifact_ids: [artifactId],
      created_at: timestamp,
      started_at: timestamp,
      ended_at: timestamp,
      work_dir: `C:\\synthetic\\local-${runId.slice(-2)}\\source`,
      log_path: `C:\\synthetic\\local-${runId.slice(-2)}\\source\\logs`,
    }
    return {
      artifact,
      run: {
        ...waitingBase,
        run_id: runId,
        selected_targets: ['source'],
        state: 'completed',
        node_runs: [nodeRun],
        created_at: timestamp,
        started_at: timestamp,
        ended_at: timestamp,
      },
    }
  }

  const earlier = completedLocalRun(
    threeRunFixtureIds.earlierLocalRun,
    '00000000-0000-4000-8000-000000000041',
    '00000000-0000-4000-8000-000000000072',
    '2026-08-24T00:00:30Z',
  )
  const later = completedLocalRun(
    threeRunFixtureIds.laterLocalRun,
    '00000000-0000-4000-8000-000000000061',
    '00000000-0000-4000-8000-000000000073',
    '2026-08-24T00:02:00Z',
  )

  return {
    ...base,
    active_run_id: threeRunFixtureIds.laterLocalRun,
    active_operation: null,
    runs: [earlier.run, waitingRun, later.run],
    artifacts: [earlier.artifact, waitingArtifact, later.artifact],
    latest_results: [
      {
        node_id: 'source',
        result_id: '00000000-0000-4000-8000-000000000074',
        stale: false,
        stale_reason: null,
        updated_at: '2026-08-24T00:02:00Z',
      },
    ],
    logs: [
      {
        node_run_id: waitingTransformRunId,
        stdout: '等待合成外部输出',
        stderr: '',
        stdout_available: true,
        stderr_available: true,
        stdout_truncated: false,
        stderr_truncated: false,
      },
    ],
  }
}

/** 构造 automatic Source 节点的受控进度响应，用于验证轮询与乱序响应。 */
export function runningProgressEnvelope(
  progress: number,
  activeOperation: StudioEnvelope['active_operation'] = 'run_all',
): StudioEnvelope {
  const base = handoffEnvelope()
  const run = base.runs[0]!
  const source = run.node_runs[0]!
  const transform = run.node_runs[1]!
  const runningSource: NodeRunWire = {
    ...source,
    state: 'running',
    output_artifact_ids: [],
    ended_at: null,
    progress,
    exit_code: null,
  }
  const pendingTransform: NodeRunWire = {
    ...transform,
    state: 'pending',
    input_artifact_ids: [],
    started_at: null,
    progress: null,
    log_path: null,
    external_handoff: null,
  }
  return {
    ...base,
    active_operation: activeOperation,
    artifacts: [],
    latest_results: [],
    logs: [],
    runs: [
      {
        ...run,
        node_runs: [runningSource, pendingTransform],
      },
    ],
  }
}

export function failedStatusEnvelope(
  reason: 'cancelled' | 'interrupted',
): StudioEnvelope {
  const base = handoffEnvelope()
  const run = base.runs[0]!
  const sourceRun = run.node_runs[0]!
  const transformRun = run.node_runs[1]!
  const message = reason === 'cancelled' ? '操作者取消当前 attempt' : '应用重启中断当前 attempt'
  return {
    ...base,
    active_operation: null,
    latest_results: [
      {
        node_id: 'source',
        result_id: '00000000-0000-4000-8000-000000000021',
        stale: true,
        stale_reason: 'graph_changed',
        updated_at: '2026-08-24T00:00:03Z',
      },
    ],
    logs: [
      {
        node_run_id: transformRun.node_run_id,
        stdout: '',
        stderr: message,
        stdout_available: true,
        stderr_available: true,
        stdout_truncated: false,
        stderr_truncated: false,
      },
    ],
    runs: [
      {
        ...run,
        state: 'failed',
        ended_at: '2026-08-24T00:00:03Z',
        error: { reason, message },
        node_runs: [
          sourceRun,
          {
            ...transformRun,
            state: 'failed',
            ended_at: '2026-08-24T00:00:03Z',
            progress: 0.4,
            error: { reason, message },
            external_handoff: null,
          },
        ],
      },
    ],
  }
}
