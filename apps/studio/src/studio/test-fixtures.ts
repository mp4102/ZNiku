/** 纯合成 Studio fixtures；不包含真实媒体、主机路径或外部副作用。 */

import type {
  ArtifactWire,
  NodeDefinitionWire,
  ProjectSnapshotWire,
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
