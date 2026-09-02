/**
 * 定义 ZNIKU Studio 使用的 0.2.1 Project Service wire 类型。
 *
 * Python 生成的 ``project-service.schema.json`` 是唯一运行时 Schema。这里的 TypeScript interface
 * 只为 React 提供静态约束；每个 endpoint 都使用自己的 Python Schema definition 失败关闭，不能把
 * status、Run detail、日志或 handoff readiness 混成第二套宽松响应。
 */

import Ajv2020, { type ErrorObject, type ValidateFunction } from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import projectServiceSchema from '../service/project-service.schema.json'

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }
export type JsonObject = { [key: string]: JsonValue }

export type Cardinality = 'one' | 'ordered_many'
export type ExecutionMode = 'automatic' | 'manual_external'

export interface PortSpecWire {
  readonly port_id: string
  readonly data_type: string
  readonly cardinality: Cardinality
  readonly required: boolean
}

export interface ExecutorOutputPathSpecWire {
  readonly port_id: string
  readonly relative_path: string
}

export type ExecutorSpecWire =
  | {
      readonly kind: 'python'
      readonly adapter: string
      readonly output_paths: ReadonlyArray<ExecutorOutputPathSpecWire>
    }
  | {
      readonly kind: 'command'
      readonly executable: string
      readonly argv: ReadonlyArray<string>
      readonly output_paths: ReadonlyArray<ExecutorOutputPathSpecWire>
    }
  | {
      readonly kind: 'manual_external'
      readonly instructions: string | null
      readonly output_paths: ReadonlyArray<ExecutorOutputPathSpecWire>
    }

export interface NodeDefinitionWire {
  readonly type_id: string
  readonly version: string
  readonly input_ports: ReadonlyArray<PortSpecWire>
  readonly output_ports: ReadonlyArray<PortSpecWire>
  readonly parameter_schema: JsonObject
  readonly execution_mode: ExecutionMode
  readonly executor: ExecutorSpecWire
  readonly validator: { readonly adapter: string } | null
}

export interface NodeInstanceWire {
  readonly node_id: string
  readonly type_id: string
  readonly definition_version: string
  readonly parameters: JsonObject
  readonly ui_position: { readonly x: number; readonly y: number } | null
}

export interface EdgeWire {
  readonly source_node_id: string
  readonly source_port_id: string
  readonly target_node_id: string
  readonly target_port_id: string
  readonly ordinal: number | null
}

export interface GraphWire {
  readonly nodes: ReadonlyArray<NodeInstanceWire>
  readonly edges: ReadonlyArray<EdgeWire>
}

export interface ProjectWire {
  readonly project_id: string
  readonly name: string
  readonly graph: GraphWire
}

export interface ProjectSnapshotWire {
  readonly project: ProjectWire
  readonly definitions: ReadonlyArray<NodeDefinitionWire>
}

export type AvEnhanceV27SourceMode = 'program' | 'pre_chaptered'

export interface AvEnhanceV27SourceSpecWire {
  readonly source_path: string
  readonly source_ordinal: number
  readonly chapter_label?: string
}

export type AvEnhanceV27MrSpecWire =
  | { readonly mode: 'off' }
  | {
      readonly mode: 'external'
      readonly model_name: string
      readonly model_version: string
    }

export interface AvEnhanceV27PrepareRequestWire {
  readonly profile_version: '2.7.0'
  readonly project_path: string
  readonly project_id: string
  readonly project_name: string
  readonly source_mode: AvEnhanceV27SourceMode
  readonly sources: ReadonlyArray<AvEnhanceV27SourceSpecWire>
  readonly mr: AvEnhanceV27MrSpecWire
}

export type AvEnhanceV27ChapterSelectorWire =
  | { readonly mode: 'single' }
  | { readonly mode: 'exact_frames'; readonly frames: ReadonlyArray<number> }
  | { readonly mode: 'exact_times'; readonly times: ReadonlyArray<string> }

export interface AvEnhanceV27ExpandRequestWire {
  readonly profile_version: '2.7.0'
  readonly preparation_run_id: string
  readonly chapter_selector?: AvEnhanceV27ChapterSelectorWire
  readonly leaf_duration_minutes: number
  readonly enhancement: {
    readonly model_name: string
    readonly model_version?: string
    readonly actual_scale_factor?: number
  }
  readonly frame_interpolation: {
    readonly model_name: string
    readonly model_version?: string
  }
  readonly program_encode: { readonly encoder: 'gpu' | 'cpu' }
  readonly publication: {
    readonly output_root: string
    readonly title: string
    readonly year: string
    readonly overwrite: boolean
  }
}

export type AvEnhanceV27TemplatePreviewRequestWire =
  | {
      readonly action: 'prepare'
      readonly request: AvEnhanceV27PrepareRequestWire
    }
  | {
      readonly action: 'expand'
      readonly request: AvEnhanceV27ExpandRequestWire
    }

export interface AvEnhanceV27ProfileDiagnosticWire {
  readonly code: string
  readonly message: string
  readonly node_id: string | null
  readonly field_path: string | null
}

export interface AvEnhanceV27LeafPlanWire {
  readonly leaf_id: string
  readonly leaf_ordinal: number
  readonly port_id: string
  readonly start_frame: number
  readonly end_frame: number
  readonly start_time_seconds: string
  readonly end_time_seconds: string
  readonly start_timecode: string
  readonly end_timecode: string
}

export interface AvEnhanceV27ChapterPlanWire {
  readonly chapter_id: string
  readonly chapter_ordinal: number
  readonly label: string
  readonly source_ordinal: number
  readonly start_frame: number
  readonly end_frame: number
  readonly start_time_seconds: string
  readonly end_time_seconds: string
  readonly start_timecode: string
  readonly end_timecode: string
  readonly leaves: ReadonlyArray<AvEnhanceV27LeafPlanWire>
}

export interface AvEnhanceV27TemplatePreviewEnvelope {
  readonly contract_version: '0.2.1'
  readonly profile_version: '2.7.0'
  readonly phase: 'preparation' | 'expanded'
  readonly project: ProjectWire
  readonly definitions: ReadonlyArray<NodeDefinitionWire>
  readonly profile: {
    readonly profile_version: '2.7.0'
    readonly phase: 'preparation' | 'expanded' | null
    readonly status:
      | 'preparation-compatible'
      | 'expanded-compatible'
      | 'replan_required'
      | 'incompatible'
    readonly compatible: boolean
    readonly diagnostics: ReadonlyArray<AvEnhanceV27ProfileDiagnosticWire>
  }
  readonly plan: {
    readonly source_count: number
    readonly chapter_count: number
    readonly leaf_count: number
    readonly mr_mode: 'off' | 'external'
    readonly preparation_run_id: string | null
    readonly effective_video_artifact_ids: ReadonlyArray<string>
    readonly chapters: ReadonlyArray<AvEnhanceV27ChapterPlanWire>
    readonly manual_stages: ReadonlyArray<{
      readonly stage: 'mosaic_restoration' | 'enhancement' | 'frame_interpolation'
      readonly node_count: number
      readonly output_container: '.mkv' | '.mov'
    }>
    readonly output_target_path: string | null
  }
}

export type FailureReason =
  | 'execution_error'
  | 'validation_failed'
  | 'interrupted'
  | 'cancelled'
  | 'external_submission_invalid'

export interface FailureWire {
  readonly reason: FailureReason
  readonly message: string
}

export interface ArtifactWire {
  readonly artifact_id: string
  readonly kind: string
  readonly path: string
  readonly producer_node_run_id: string
  readonly producer_port_id: string
  readonly ordinal: number | null
  readonly frame_range: { readonly start_frame: number; readonly end_frame: number } | null
  readonly media_info: JsonObject
  readonly size: number | null
  readonly mtime_ns: number | null
}

export interface ExternalOutputTargetWire {
  readonly port_id: string
  readonly path: string
  readonly ordinal: number | null
}

export interface ExternalHandoffWire {
  readonly handoff_id: string
  readonly node_run_id: string
  readonly input_artifact_ids: ReadonlyArray<string>
  readonly output_targets: ReadonlyArray<ExternalOutputTargetWire>
  readonly instructions: string | null
  readonly created_at: string
}

export type NodeRunState = 'pending' | 'running' | 'waiting_external' | 'completed' | 'failed'

export interface NodeRunWire {
  readonly node_run_id: string
  readonly run_id: string
  readonly node_id: string
  readonly definition_version: string
  readonly attempt: number
  readonly state: NodeRunState
  readonly input_artifact_ids: ReadonlyArray<string>
  readonly output_artifact_ids: ReadonlyArray<string>
  readonly created_at: string
  readonly work_dir: string
  readonly started_at: string | null
  readonly ended_at: string | null
  readonly progress: number | null
  readonly exit_code: number | null
  readonly log_path: string | null
  readonly error: FailureWire | null
  readonly reused_from_result_id: string | null
  readonly external_handoff: ExternalHandoffWire | null
}

export type RunState = 'pending' | 'running' | 'completed' | 'failed'

export interface RunWire {
  readonly run_id: string
  readonly project_id: string
  readonly graph_snapshot: GraphWire
  readonly definitions_snapshot: ReadonlyArray<NodeDefinitionWire>
  readonly selected_targets: ReadonlyArray<string>
  readonly state: RunState
  readonly node_runs: ReadonlyArray<NodeRunWire>
  readonly created_at: string
  readonly started_at: string | null
  readonly ended_at: string | null
  readonly error: FailureWire | null
}

export interface LatestResultWire {
  readonly node_id: string
  readonly result_id: string
  readonly stale: boolean
  readonly stale_reason:
    | 'graph_changed'
    | 'upstream_changed'
    | 'output_missing'
    | 'quick_probe_failed'
    | 'rerun_requested'
    | null
  readonly updated_at: string
}

export interface RunNodeStateCountsWire {
  readonly pending: number
  readonly running: number
  readonly waiting_external: number
  readonly completed: number
  readonly failed: number
}

export interface RunSummaryWire {
  readonly run_id: string
  readonly project_id: string
  readonly target_mode: 'all' | 'selected'
  readonly selected_targets: ReadonlyArray<string>
  readonly state: RunState
  readonly node_count: number
  readonly state_counts: RunNodeStateCountsWire
  readonly actionable: boolean
  readonly requires_operator_action: boolean
  readonly created_at: string
  readonly started_at: string | null
  readonly ended_at: string | null
  readonly latest_activity_at: string
  readonly error: FailureWire | null
}

export interface NodeProgressProjectionWire {
  readonly node_run_id: string
  readonly fraction: number
  readonly current: number | null
  readonly total: number | null
  readonly unit: 'frames' | 'bytes' | 'microseconds' | 'items' | null
  readonly observed_at: string
}

export interface StudioServiceError {
  readonly code: string
  readonly message: string
  readonly related_run_ids: ReadonlyArray<string>
}

export type ActiveStudioOperation =
  | 'run_all'
  | 'run_to'
  | 'rerun_from_here'
  | 'submit_external'
  | 'abandon_run'

export interface StatusEnvelope {
  readonly contract_version: '0.2.1'
  readonly project_path: string | null
  readonly snapshot: ProjectSnapshotWire | null
  readonly run_summaries: ReadonlyArray<RunSummaryWire>
  readonly next_run_cursor: string | null
  readonly active_run_id: string | null
  readonly active_operation: ActiveStudioOperation | null
  readonly latest_results: ReadonlyArray<LatestResultWire>
  readonly error: StudioServiceError | null
}

export interface RunSummaryPageEnvelope {
  readonly contract_version: '0.2.1'
  readonly run_summaries: ReadonlyArray<RunSummaryWire>
  readonly next_run_cursor: string | null
}

export interface RunDetailEnvelope {
  readonly contract_version: '0.2.1'
  readonly run: RunWire
  readonly artifacts: ReadonlyArray<ArtifactWire>
  readonly progress_samples: ReadonlyArray<NodeProgressProjectionWire>
}

export interface NodeLogWire {
  readonly node_run_id: string
  readonly stdout: string
  readonly stderr: string
  readonly stdout_available: boolean
  readonly stderr_available: boolean
  readonly stdout_truncated: boolean
  readonly stderr_truncated: boolean
}

export interface NodeLogEnvelope {
  readonly contract_version: '0.2.1'
  readonly run_id: string
  readonly log: NodeLogWire
}

export type ExternalReadinessState =
  | 'missing'
  | 'empty'
  | 'present'
  | 'probe_passed'
  | 'probe_failed'

export interface ExternalOutputReadinessWire {
  readonly port_id: string
  readonly ordinal: number | null
  readonly path: string
  readonly state: ExternalReadinessState
  readonly size: number | null
  readonly mtime_ns: number | null
  readonly message: string | null
}

export interface ExternalHandoffReadiness {
  readonly contract_version: '0.2.1'
  readonly run_id: string
  readonly node_run_id: string
  readonly handoff_id: string
  readonly checked_at: string
  readonly probe_requested: boolean
  readonly ready_for_submit: boolean
  readonly targets: ReadonlyArray<ExternalOutputReadinessWire>
}

export type StudioOperation =
  | 'create_project'
  | 'open_project'
  | 'save_project'
  | 'create_av_enhance_v27'
  | 'expand_av_enhance_v27'
  | 'abandon_run'
  | ActiveStudioOperation

export type StudioCommand =
  | {
      readonly operation: 'create_project'
      readonly path: string
      readonly project_id: string
      readonly name: string
    }
  | { readonly operation: 'open_project'; readonly path: string }
  | { readonly operation: 'save_project'; readonly project: ProjectWire }
  | {
      readonly operation: 'create_av_enhance_v27'
      readonly request: AvEnhanceV27PrepareRequestWire
    }
  | {
      readonly operation: 'expand_av_enhance_v27'
      readonly request: AvEnhanceV27ExpandRequestWire
    }
  | { readonly operation: 'run_all' }
  | { readonly operation: 'run_to'; readonly node_id: string }
  | { readonly operation: 'rerun_from_here'; readonly run_id: string; readonly node_id: string }
  | {
      readonly operation: 'submit_external'
      readonly run_id: string
      readonly node_run_id: string
      readonly handoff_id: string
    }
  | { readonly operation: 'abandon_run'; readonly run_id: string }

type SchemaDocument = {
  readonly $schema?: string
  readonly $defs?: Readonly<Record<string, unknown>>
  readonly title?: string
  readonly [key: string]: unknown
}

const schemaDocument = projectServiceSchema as unknown as SchemaDocument
const ajv = new Ajv2020({ allErrors: true, strict: false, validateFormats: true })
addFormats(ajv)

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

const validateStatus = compileDefinition('StatusEnvelope')
const validateRunSummaryPage = compileDefinition('RunSummaryPageEnvelope')
const validateRunDetail = compileDefinition('RunDetailEnvelope')
const validateNodeLog = compileDefinition('NodeLogEnvelope')
const validateExternalReadiness = compileDefinition('ExternalHandoffReadiness')
const validateTemplatePreviewRequest = compileDefinition('TemplatePreviewRequest')
const validateTemplatePreviewEnvelope = compileDefinition('TemplatePreviewEnvelope')
const validateCommand = compileDefinition('ProjectServiceCommand')

export class StudioContractError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'StudioContractError'
  }
}

function validationMessage(errors: ErrorObject[] | null | undefined): string {
  return (errors ?? [])
    .slice(0, 4)
    .map((error) => `${error.instancePath || '/'} ${error.message ?? error.keyword}`)
    .join('; ')
}

function parseWith<T>(value: unknown, validator: ValidateFunction, label: string): T {
  if (!validator(value)) {
    throw new StudioContractError(
      `${label} 不符合 Python 0.2.1 Schema：${validationMessage(validator.errors)}`,
    )
  }
  return value as T
}

export function parseStatusEnvelope(value: unknown): StatusEnvelope {
  return parseWith(value, validateStatus, 'Studio status payload')
}

export function parseRunSummaryPageEnvelope(value: unknown): RunSummaryPageEnvelope {
  return parseWith(value, validateRunSummaryPage, 'Studio Run summary page')
}

export function parseRunDetailEnvelope(value: unknown): RunDetailEnvelope {
  const detail = parseWith<RunDetailEnvelope>(value, validateRunDetail, 'Studio Run detail')
  validateProgressSamples(detail)
  return detail
}

function progressContractError(message: string): never {
  throw new StudioContractError(`Studio Run detail progress_samples 不符合 0.2.1 合同：${message}`)
}

function validateProgressSamples(detail: RunDetailEnvelope): void {
  /**
   * JSON Schema 能冻结字段、枚举和各字段范围，但无法表达跨字段比值和跨数组 identity。
   * 这里继续以 fail-closed 方式验证 Python ``model_validator`` 及 Run envelope 关系，避免 mock、
   * 缓存代理或错误 Service 把旧 attempt／manual 节点的 sample 叠加到当前画布。
   */
  const nodeRuns = new Map(detail.run.node_runs.map((item) => [item.node_run_id, item]))
  const latestByNode = new Map<string, { readonly attempt: number; readonly nodeRunId: string }>()
  const ambiguousLatest = new Set<string>()
  for (const item of detail.run.node_runs) {
    if (item.run_id !== detail.run.run_id) {
      progressContractError(
        `node_run_id ${item.node_run_id} 的 run_id 不属于当前 Run ${detail.run.run_id}`,
      )
    }
    const current = latestByNode.get(item.node_id)
    if (!current || item.attempt > current.attempt) {
      latestByNode.set(item.node_id, { attempt: item.attempt, nodeRunId: item.node_run_id })
      ambiguousLatest.delete(item.node_id)
    } else if (item.attempt === current.attempt && item.node_run_id !== current.nodeRunId) {
      ambiguousLatest.add(item.node_id)
    }
  }

  const graphNodes = new Map(detail.run.graph_snapshot.nodes.map((item) => [item.node_id, item]))
  const definitions = new Map(
    detail.run.definitions_snapshot.map((item) => [`${item.type_id}@${item.version}`, item]),
  )
  const seen = new Set<string>()

  for (const sample of detail.progress_samples) {
    if (seen.has(sample.node_run_id)) {
      progressContractError(`node_run_id ${sample.node_run_id} 重复`)
    }
    seen.add(sample.node_run_id)

    const measurement = [sample.current, sample.total, sample.unit]
    const present = measurement.filter((item) => item !== null).length
    if (present !== 0 && present !== measurement.length) {
      progressContractError('current/total/unit 必须全部出现或全部为 null')
    }
    if (!Number.isFinite(sample.fraction) || sample.fraction < 0 || sample.fraction > 1) {
      progressContractError('fraction 必须是 0.0..1.0 的有限数')
    }
    if (sample.current !== null && sample.total !== null) {
      if (
        !Number.isInteger(sample.current) ||
        !Number.isInteger(sample.total) ||
        sample.current < 0 ||
        sample.total <= 0 ||
        sample.current > sample.total
      ) {
        progressContractError('current/total 范围无效')
      }
      if (Math.abs(sample.fraction - sample.current / sample.total) > 1e-9) {
        progressContractError('fraction 与 current/total 不一致')
      }
    }

    const nodeRun = nodeRuns.get(sample.node_run_id)
    if (!nodeRun) progressContractError(`node_run_id ${sample.node_run_id} 不属于当前 Run`)
    if (
      ambiguousLatest.has(nodeRun.node_id) ||
      latestByNode.get(nodeRun.node_id)?.nodeRunId !== nodeRun.node_run_id
    ) {
      progressContractError(`node_run_id ${sample.node_run_id} 不是节点的唯一最新 attempt`)
    }
    if (nodeRun.state !== 'running') {
      progressContractError(`node_run_id ${sample.node_run_id} 不是 running attempt`)
    }
    if (nodeRun.progress !== null && sample.fraction < nodeRun.progress) {
      progressContractError(`node_run_id ${sample.node_run_id} 的投影低于持久进度`)
    }

    const graphNode = graphNodes.get(nodeRun.node_id)
    if (!graphNode || graphNode.definition_version !== nodeRun.definition_version) {
      progressContractError(`node_run_id ${sample.node_run_id} 无法绑定 Run snapshot node`)
    }
    const definition = definitions.get(`${graphNode.type_id}@${graphNode.definition_version}`)
    if (
      !definition ||
      definition.execution_mode !== 'automatic' ||
      definition.executor.kind !== 'python'
    ) {
      progressContractError(`node_run_id ${sample.node_run_id} 不是 automatic Python executor`)
    }
  }
}

export function parseNodeLogEnvelope(value: unknown): NodeLogEnvelope {
  return parseWith(value, validateNodeLog, 'Studio Node log')
}

export function parseExternalHandoffReadiness(value: unknown): ExternalHandoffReadiness {
  return parseWith(value, validateExternalReadiness, 'Studio handoff readiness')
}

export function parseAvEnhanceV27TemplatePreviewRequest(
  value: unknown,
): AvEnhanceV27TemplatePreviewRequestWire {
  return parseWith(
    value,
    validateTemplatePreviewRequest,
    'AVEnhanceFlow v2.7 template preview request',
  )
}

export function parseAvEnhanceV27TemplatePreviewEnvelope(
  value: unknown,
): AvEnhanceV27TemplatePreviewEnvelope {
  const preview = parseWith<AvEnhanceV27TemplatePreviewEnvelope>(
    value,
    validateTemplatePreviewEnvelope,
    'AVEnhanceFlow v2.7 template preview response',
  )
  const compatibleStatus =
    preview.profile.status === 'preparation-compatible' ||
    preview.profile.status === 'expanded-compatible'
  if (
    preview.profile.profile_version !== preview.profile_version ||
    preview.profile.phase !== preview.phase ||
    preview.profile.compatible !== compatibleStatus ||
    (compatibleStatus && preview.profile.diagnostics.length > 0) ||
    (!compatibleStatus && preview.profile.diagnostics.length === 0) ||
    (preview.profile.status === 'preparation-compatible' &&
      preview.profile.phase !== 'preparation') ||
    ((preview.profile.status === 'expanded-compatible' ||
      preview.profile.status === 'replan_required') &&
      preview.profile.phase !== 'expanded')
  ) {
    // 这里只镜像 Python DTO 无法投影为 JSON Schema 的跨字段 model_validator；
    // 不检查 Graph shape，也不在浏览器执行 template profile preflight。
    throw new StudioContractError(
      'AVEnhanceFlow v2.7 template preview response 的 phase/status/compatible/diagnostics 不一致',
    )
  }
  return preview
}

export function parseStudioCommand(value: unknown): StudioCommand {
  return parseWith(value, validateCommand, 'Studio command')
}

// 只为迁移现有调用者保留名称；它仍严格解析新的 0.2.1 StatusEnvelope。
export type StudioEnvelope = StatusEnvelope
export const parseStudioEnvelope = parseStatusEnvelope
