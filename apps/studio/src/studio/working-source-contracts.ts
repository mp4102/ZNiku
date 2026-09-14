/** 普通工作源的薄 exact 适配；复用展示形状，仍由 Python Schema 严格验证新版本。 */
import type { ValidateFunction } from 'ajv'
import { compileDefinition } from './contracts'
import { OverlapContractError } from './chapter-overlap-contracts'
import type { ColorPreparedSourceViewEnvelope, ColorPreparedSourceChoice, ColorPreparedSourceCreateRequest, ColorPreparedSourceViewRequest,
  ColorPreparedSourceOperationRequest, ColorPreparedSourceOperationEnvelope, ColorPreparedSourceProcessingRequest,
  ColorPreparedSourceProcessingEnvelope, ColorPreparedSourceFullIntent, ColorPreparedSourceFullRequest,
  ColorPreparedSourceFullEnvelope, ColorPreparedSourceFailureEnvelope } from './prepared-color-contracts'

export const WORK_SOURCE_VERSION = '0.3.4-work.1' as const
type WorkVersion<T> = Omit<T, 'contract_version'> & { readonly contract_version: typeof WORK_SOURCE_VERSION }
export type WorkRoute = ColorPreparedSourceChoice['route']
export type WorkConfirmationId = 'color_interpretation' | 'target_frame_rate' | 'retime' | 'external_reference'
export interface WorkConfirmation { readonly id: WorkConfirmationId; readonly routes: ReadonlyArray<WorkRoute>; readonly label: string; readonly description: string }
export interface WorkImpact { readonly id: 'frames' | 'timing' | 'color' | 'audio' | 'storage' | 'external_reference'; readonly routes: ReadonlyArray<WorkRoute>; readonly title: string; readonly description: string }
export interface WorkSettings {
  readonly route: WorkRoute | 'diagnose'; readonly target_frame_rate: string | null
  readonly external_format: 'mp4' | 'mov' | 'mkv'; readonly interpretation_policy: ColorPreparedSourceChoice['interpretation_policy']
  readonly confirmations: ReadonlyArray<WorkConfirmationId>; readonly audio_source: 'original' | 'reference' | 'none'
}
export interface WorkRetryTarget { readonly run_id: string; readonly node_run_id: string; readonly node_id: string }
export interface WorkChoice extends ColorPreparedSourceChoice { readonly confirmations: ReadonlyArray<WorkConfirmationId> }
export type WorkCreateRequest = WorkVersion<ColorPreparedSourceCreateRequest>
export type WorkViewRequest = WorkVersion<ColorPreparedSourceViewRequest>
export type WorkChooseRequest = WorkViewRequest & WorkChoice & { readonly expected_storage_revision: number }
export type WorkOperationRequest = WorkVersion<ColorPreparedSourceOperationRequest>
export type WorkOperationEnvelope = WorkVersion<ColorPreparedSourceOperationEnvelope>
export type WorkViewEnvelope = Omit<WorkVersion<ColorPreparedSourceViewEnvelope>, 'profile_id' | 'profile_version'> & {
  readonly profile_id: 'zniku.prepared-work-overlap'; readonly profile_version: typeof WORK_SOURCE_VERSION
  readonly decision: 'direct' | 'preparation_required' | 'unsupported' | null
  readonly inspection_scope: 'header_only' | 'frames_eof' | null
  readonly impacts: ReadonlyArray<WorkImpact>; readonly required_confirmations: ReadonlyArray<WorkConfirmation>
  readonly current_settings: WorkSettings; readonly retry_target: WorkRetryTarget | null
}
export type WorkProcessingRequest = WorkVersion<ColorPreparedSourceProcessingRequest>
export type WorkProcessingEnvelope = WorkVersion<ColorPreparedSourceProcessingEnvelope>
export type WorkFullIntent = WorkVersion<ColorPreparedSourceFullIntent>
export type WorkFullRequest = WorkVersion<ColorPreparedSourceFullRequest>
export type WorkFullEnvelope = Omit<WorkVersion<ColorPreparedSourceFullEnvelope>, 'profile_id' | 'profile_version'> & {
  readonly profile_id: 'zniku.prepared-work-overlap'; readonly profile_version: typeof WORK_SOURCE_VERSION
}
export type WorkFailureEnvelope = WorkVersion<ColorPreparedSourceFailureEnvelope>
export interface WorkingSourceWizardServices {
  readonly create: (request: WorkCreateRequest) => Promise<boolean>
  readonly inspect: (runId: string) => Promise<WorkViewEnvelope>
  readonly choose: (choice: WorkChoice) => Promise<boolean>
  readonly processing: (request: WorkProcessingRequest) => Promise<WorkProcessingEnvelope>
  readonly preview: (request: WorkFullIntent) => Promise<WorkFullEnvelope>
  readonly expand: (request: WorkFullIntent) => Promise<boolean>
}
export const workPreparationTypes = new Set(['zniku.source_preparation.source', 'zniku.source_preparation.diagnostics',
  'zniku.source_preparation.admission', 'zniku.source_preparation.video_prepare.frame_retime',
  'zniku.source_preparation.work_reference.external.mkv', 'zniku.source_preparation.work_reference.external.mp4',
  'zniku.source_preparation.work_reference.external.mov'])
export function isWorkPreparationOperationNode(node: { readonly type_id: string; readonly definition_version: string } | undefined): boolean {
  return node?.definition_version === WORK_SOURCE_VERSION && workPreparationTypes.has(node.type_id)
}
export function workingSourceCatalogAvailable(nodes: ReadonlyArray<{ readonly type_id: string; readonly definition_version: string }>): boolean {
  const published = new Set(nodes.filter((node) => node.definition_version === WORK_SOURCE_VERSION).map((node) => node.type_id))
  return [...workPreparationTypes, 'zniku.prepared.overlap.split.leaves.1', 'zniku.prepared.overlap.enhancement.external',
    'zniku.prepared.overlap.merge_video', 'zniku.prepared.overlap.fi_context', 'zniku.prepared.overlap.frame_interpolation.external',
    'zniku.prepared.overlap.fi_crop', 'zniku.prepared.overlap.program_encode', 'zniku.prepared.overlap.final_mux'].every((id) => published.has(id))
}
const validators = new Map<string, ValidateFunction>()
function parse<T>(name: string, value: unknown): T {
  let validator = validators.get(name)
  if (!validator) { validator = compileDefinition(name); validators.set(name, validator) }
  if (!validator(value)) throw new OverlapContractError(`普通素材准备不符合 Python ${WORK_SOURCE_VERSION} Schema：${validator.errors?.[0]?.instancePath ?? '/'} ${validator.errors?.[0]?.message ?? '无效字段'}`, [])
  return value as T
}
export const parseWorkCreateRequest = (value: unknown): WorkCreateRequest => parse('WorkCreateRequest', value)
export const parseWorkViewRequest = (value: unknown): WorkViewRequest => parse('WorkViewRequest', value)
export const parseWorkChooseRequest = (value: unknown): WorkChooseRequest => parse('WorkChooseRequest', value)
export const parseWorkOperationRequest = (value: unknown): WorkOperationRequest => parse('WorkOperationRequest', value)
export const parseWorkOperationEnvelope = (value: unknown): WorkOperationEnvelope => parse('WorkOperationEnvelope', value)
export const parseWorkViewEnvelope = (value: unknown): WorkViewEnvelope => {
  const view = parse<WorkViewEnvelope>('WorkViewEnvelope', value)
  if ((view.progress && view.progress.node_run_id !== view.current_node_run_id) || (view.handoff && view.handoff.run_id !== view.run_id)
      || (view.retry_target && view.retry_target.run_id !== view.run_id)
      || (view.state === 'ready' && (view.admission_status !== 'completed' || !view.reference_path))) {
    throw new OverlapContractError('普通素材准备响应的身份或完成状态不一致', [])
  }
  return view
}
export const parseWorkProcessingRequest = (value: unknown): WorkProcessingRequest => parse('WorkProcessingRequest', value)
export const parseWorkProcessingEnvelope = (value: unknown): WorkProcessingEnvelope => parse('WorkProcessingEnvelope', value)
export const parseWorkFullRequest = (value: unknown): WorkFullRequest => parse('WorkFullRequest', value)
export const parseWorkFullEnvelope = (value: unknown): WorkFullEnvelope => parse('WorkFullEnvelope', value)
export const parseWorkFailure = (value: unknown): WorkFailureEnvelope => parse('WorkFailureEnvelope', value)
