/** 仅投影 Python 0.3.4 素材准备合同；准入、策略可用性和来源关系均由服务决定。 */
import type { ValidateFunction } from 'ajv'
import { compileDefinition, type NodeProgressProjectionWire } from './contracts'
import { OverlapContractError, type OverlapFullIntent } from './chapter-overlap-contracts'
import { sourceAlignedProcessingMatches, type SourceAlignedFullEnvelope, type SourceAlignedProcessing } from './source-aligned-contracts'

export interface PreparedSourceCreateRequest {
  readonly contract_version: '0.3.4'
  readonly project_path: string
  readonly project_id: string
  readonly project_name: string
  readonly source_path: string
  readonly data_parent?: string | null
}
export interface PreparedSourceViewRequest {
  readonly contract_version: '0.3.4'
  readonly project_session_id: string
  readonly run_id: string
}
export interface PreparedSourceOperationRequest extends PreparedSourceViewRequest { readonly node_run_id: string }
export interface PreparedSourceOperationEnvelope extends PreparedSourceOperationRequest {
  readonly active: boolean
  readonly operation: string | null
  readonly stage_progress: PreparedSourceViewEnvelope['stage_progress']
  readonly cancel_requested: boolean
}
export type PreparedSourceRoute = 'direct' | 'builtin' | 'external'
export interface PreparedSourceChoice {
  readonly run_id: string
  readonly route: PreparedSourceRoute
  readonly target_frame_rate: string | null
  readonly external_format: 'mp4' | 'mov' | 'mkv'
}
export interface PreparedSourceChooseRequest extends PreparedSourceViewRequest, PreparedSourceChoice {
  readonly expected_storage_revision: number
}
export interface PreparedSourceFinding {
  readonly code: string
  readonly reason: string
  readonly impact: string
  readonly recommendation: string
}
export interface PreparedSourceAction {
  readonly route: PreparedSourceRoute
  readonly label: string
  readonly enabled: boolean
  readonly reason: string
  readonly strategy_id: string | null
  readonly estimated_additional_bytes: number | null
}
export interface PreparedSourceFailure {
  readonly code: string
  readonly message: string
  readonly field_path: ReadonlyArray<string | number>
  readonly related_run_ids: ReadonlyArray<string>
}
export interface PreparedSourceViewEnvelope {
  readonly contract_version: '0.3.4'
  readonly profile_id: 'zniku.prepared-source-overlap'
  readonly profile_version: '0.3.4'
  readonly project_session_id: string
  readonly storage_revision: number
  readonly run_id: string
  readonly route: 'diagnose' | PreparedSourceRoute
  readonly state: 'checking' | 'needs_choice' | 'preparing' | 'waiting_external' | 'ready' | 'failed'
  readonly stage: string
  readonly current_node_run_id: string | null
  readonly progress: NodeProgressProjectionWire | null
  readonly stage_progress: { readonly stage: string; readonly current: number | null; readonly total: number | null;
    readonly unit: 'frames' | 'packets' | 'bytes' | 'tracks' | 'samples' | null;
    readonly elapsed_seconds: number; readonly rate_per_second: number | null } | null
  readonly source_name: string
  readonly original_path: string
  readonly reference_path: string | null
  readonly source_frame_count: number | null
  readonly frame_rate: string | null
  readonly frame_rate_choices: ReadonlyArray<string>
  readonly audio_track_count: number | null
  readonly findings: ReadonlyArray<PreparedSourceFinding>
  readonly available_actions: ReadonlyArray<PreparedSourceAction>
  readonly diagnosis_status: 'pending' | 'completed' | 'failed'
  readonly admission_status: 'not_started' | 'pending' | 'completed' | 'failed'
  readonly handoff: { readonly run_id: string; readonly node_run_id: string; readonly node_id: string } | null
  readonly error: PreparedSourceFailure | null
}
export interface PreparedSourceProcessingRequest { readonly contract_version: '0.3.4'; readonly processing: SourceAlignedProcessing }
export interface PreparedSourceProcessingEnvelope extends PreparedSourceProcessingRequest { readonly status: 'pending_real_acceptance' }
export interface PreparedSourceFullIntent extends PreparedSourceProcessingRequest {
  readonly preparation_run_id: string
  readonly publication: OverlapFullIntent['publication']
}
export interface PreparedSourceFullRequest extends PreparedSourceFullIntent { readonly project_session_id: string; readonly expected_storage_revision: number }
export interface PreparedSourceFullEnvelope extends Omit<SourceAlignedFullEnvelope, 'contract_version' | 'profile_id' | 'profile_version'> {
  readonly contract_version: '0.3.4'
  readonly profile_id: 'zniku.prepared-source-overlap'
  readonly profile_version: '0.3.4'
}
export interface PreparedSourceFailureEnvelope { readonly contract_version: '0.3.4'; readonly error: PreparedSourceFailure }

/** 只检查服务是否发布新 exact 目录，不以此判定任何媒体可用。 */
export function preparedSourceCatalogAvailable(nodes: ReadonlyArray<{ readonly type_id: string; readonly definition_version: string }>): boolean {
  const published = new Set(nodes.filter((node) => node.definition_version === '0.3.4').map((node) => node.type_id))
  return ['zniku.source_preparation.source', 'zniku.source_preparation.diagnostics', 'zniku.source_preparation.admission',
    'zniku.prepared.overlap.split.leaves.1', 'zniku.prepared.overlap.enhancement.external',
    'zniku.prepared.overlap.merge_video', 'zniku.prepared.overlap.fi_context',
    'zniku.prepared.overlap.frame_interpolation.external', 'zniku.prepared.overlap.fi_crop',
    'zniku.prepared.overlap.program_encode', 'zniku.prepared.overlap.final_mux'].every((id) => published.has(id))
}

export const preparedSourceProcessingMatches = sourceAlignedProcessingMatches
const operationTypeIds = new Set(['zniku.source_preparation.source', 'zniku.source_preparation.diagnostics',
  'zniku.source_preparation.video_prepare.t1', 'zniku.source_preparation.admission',
  'zniku.source_preparation.video_repair.external.mkv', 'zniku.source_preparation.video_repair.external.mp4',
  'zniku.source_preparation.video_repair.external.mov'])
/** 只选取已声明准备节点的展示入口；合法性与活动操作仍由服务检查，不依赖实例名称或图模板。 */
export function isSourcePreparationOperationNode(node: { readonly type_id: string; readonly definition_version: string } | undefined): boolean {
  return node?.definition_version === '0.3.4' && operationTypeIds.has(node.type_id)
}
const validators = new Map<string, ValidateFunction>()
function parse<T>(name: string, value: unknown): T {
  let validator = validators.get(name)
  if (!validator) { validator = compileDefinition(name); validators.set(name, validator) }
  if (!validator(value)) {
    const errors = validator.errors ?? []
    const first = errors.filter((error) => !['oneOf', 'anyOf', 'const'].includes(error.keyword))
      .sort((left, right) => right.instancePath.split('/').length - left.instancePath.split('/').length)[0] ?? errors[0]
    const fieldPath = (first?.instancePath ?? '').split('/').filter(Boolean).map((part) => /^[0-9]+$/.test(part) ? Number(part) : part.replace(/~1/g, '/').replace(/~0/g, '~'))
    throw new OverlapContractError(`素材准备响应或设置不符合 Python 0.3.4 Schema：${first?.instancePath ?? '/'} ${first?.message ?? '无效字段'}`, fieldPath)
  }
  return value as T
}
export const parsePreparedSourceCreateRequest = (value: unknown): PreparedSourceCreateRequest => parse('PreparedSourceCreateRequest', value)
export const parsePreparedSourceViewRequest = (value: unknown): PreparedSourceViewRequest => parse('PreparedSourceViewRequest', value)
export const parsePreparedSourceOperationRequest = (value: unknown): PreparedSourceOperationRequest => parse('PreparedSourceOperationRequest', value)
export const parsePreparedSourceOperationEnvelope = (value: unknown): PreparedSourceOperationEnvelope => parse('PreparedSourceOperationEnvelope', value)
export const parsePreparedSourceChooseRequest = (value: unknown): PreparedSourceChooseRequest => parse('PreparedSourceChooseRequest', value)
export const parsePreparedSourceViewEnvelope = (value: unknown): PreparedSourceViewEnvelope => {
  const view = parse<PreparedSourceViewEnvelope>('PreparedSourceViewEnvelope', value)
  // 只核对 wire 自身的身份与成功形状，不检查媒体或重新计算服务结论。
  if ((view.progress && view.progress.node_run_id !== view.current_node_run_id) ||
      (view.handoff && view.handoff.run_id !== view.run_id) ||
      (view.state === 'ready' && (view.admission_status !== 'completed' || !view.reference_path))) {
    throw new OverlapContractError('素材准备响应内部绑定或准入状态不一致', [])
  }
  return view
}
export const parsePreparedSourceProcessingRequest = (value: unknown): PreparedSourceProcessingRequest => parse('PreparedSourceProcessingRequest', value)
export const parsePreparedSourceProcessingEnvelope = (value: unknown): PreparedSourceProcessingEnvelope => parse('PreparedSourceProcessingEnvelope', value)
export const parsePreparedSourceFullRequest = (value: unknown): PreparedSourceFullRequest => parse('PreparedSourceFullRequest', value)
export const parsePreparedSourceFullEnvelope = (value: unknown): PreparedSourceFullEnvelope => parse('PreparedSourceFullEnvelope', value)
export const parsePreparedSourceFailure = (value: unknown): PreparedSourceFailureEnvelope => parse('PreparedSourceFailureEnvelope', value)
