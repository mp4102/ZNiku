/** 显式工作色彩解释的独立 exact wire；不扩宽旧 0.3.4 parser，不自行判断媒体。 */
import type { ValidateFunction } from 'ajv'
import { compileDefinition } from './contracts'
import { OverlapContractError } from './chapter-overlap-contracts'
import type {
  PreparedSourceChoice, PreparedSourceCreateRequest, PreparedSourceViewRequest, PreparedSourceChooseRequest,
  PreparedSourceViewEnvelope, PreparedSourceOperationRequest, PreparedSourceOperationEnvelope,
  PreparedSourceProcessingRequest, PreparedSourceProcessingEnvelope, PreparedSourceFullIntent,
  PreparedSourceFullRequest, PreparedSourceFullEnvelope, PreparedSourceFailureEnvelope,
} from './prepared-source-contracts'

export const COLOR_PREPARED_VERSION = '0.3.4-color.1' as const
export type ColorInterpretationPolicy = 'declared_only' | 'operator_confirmed_bt709_limited_left'
type ColorVersion<T> = Omit<T, 'contract_version'> & { readonly contract_version: typeof COLOR_PREPARED_VERSION }
export type ColorPreparedSourceCreateRequest = ColorVersion<PreparedSourceCreateRequest>
export type ColorPreparedSourceViewRequest = ColorVersion<PreparedSourceViewRequest>
export type ColorPreparedSourceOperationRequest = ColorVersion<PreparedSourceOperationRequest>
export type ColorPreparedSourceOperationEnvelope = ColorVersion<PreparedSourceOperationEnvelope>
export interface ColorPreparedSourceChoice extends PreparedSourceChoice { readonly interpretation_policy: ColorInterpretationPolicy }
export type ColorPreparedSourceChooseRequest = ColorVersion<PreparedSourceChooseRequest> & ColorPreparedSourceChoice
export type ColorPreparedSourceViewEnvelope = Omit<ColorVersion<PreparedSourceViewEnvelope>, 'profile_id' | 'profile_version'> & {
  readonly profile_id: 'zniku.prepared-color-overlap'
  readonly profile_version: typeof COLOR_PREPARED_VERSION
  readonly interpretation_policy: ColorInterpretationPolicy
  readonly color_interpretation_available: boolean
  readonly color_interpretation_required: boolean
  readonly color_interpretation_reason: string
  readonly working_signal_basis: string | null
}
export type ColorPreparedSourceProcessingRequest = ColorVersion<PreparedSourceProcessingRequest>
export type ColorPreparedSourceProcessingEnvelope = ColorVersion<PreparedSourceProcessingEnvelope>
export type ColorPreparedSourceFullIntent = ColorVersion<PreparedSourceFullIntent>
export type ColorPreparedSourceFullRequest = ColorVersion<PreparedSourceFullRequest>
export type ColorPreparedSourceFullEnvelope = Omit<ColorVersion<PreparedSourceFullEnvelope>, 'profile_id' | 'profile_version'> & {
  readonly profile_id: 'zniku.prepared-color-overlap'
  readonly profile_version: typeof COLOR_PREPARED_VERSION
}
export type ColorPreparedSourceFailureEnvelope = ColorVersion<PreparedSourceFailureEnvelope>

export interface ColorPreparedSourceWizardServices {
  readonly create: (request: ColorPreparedSourceCreateRequest) => Promise<boolean>
  readonly inspect: (runId: string) => Promise<ColorPreparedSourceViewEnvelope>
  readonly choose: (choice: ColorPreparedSourceChoice) => Promise<boolean>
  readonly processing: (request: ColorPreparedSourceProcessingRequest) => Promise<ColorPreparedSourceProcessingEnvelope>
  readonly preview: (request: ColorPreparedSourceFullIntent) => Promise<ColorPreparedSourceFullEnvelope>
  readonly expand: (request: ColorPreparedSourceFullIntent) => Promise<boolean>
}
/** 只验证目录声明的能力；不能据此认定任何输入色彩有效。 */
export function preparedColorCatalogAvailable(nodes: ReadonlyArray<{ readonly type_id: string; readonly definition_version: string }>): boolean {
  const published = new Set(nodes.filter((node) => node.definition_version === COLOR_PREPARED_VERSION).map((node) => node.type_id))
  return ['zniku.source_preparation.source', 'zniku.source_preparation.diagnostics', 'zniku.source_preparation.admission',
    'zniku.prepared.overlap.split.leaves.1', 'zniku.prepared.overlap.enhancement.external', 'zniku.prepared.overlap.merge_video',
    'zniku.prepared.overlap.fi_context', 'zniku.prepared.overlap.frame_interpolation.external', 'zniku.prepared.overlap.fi_crop',
    'zniku.prepared.overlap.program_encode', 'zniku.prepared.overlap.final_mux'].every((id) => published.has(id))
}
const operationTypes = new Set(['zniku.source_preparation.source', 'zniku.source_preparation.diagnostics',
  'zniku.source_preparation.video_prepare.t1', 'zniku.source_preparation.admission',
  'zniku.source_preparation.video_repair.external.mkv', 'zniku.source_preparation.video_repair.external.mp4',
  'zniku.source_preparation.video_repair.external.mov'])
export function isColorPreparationOperationNode(node: { readonly type_id: string; readonly definition_version: string } | undefined): boolean {
  return node?.definition_version === COLOR_PREPARED_VERSION && operationTypes.has(node.type_id)
}
const validators = new Map<string, ValidateFunction>()
function parse<T>(name: string, value: unknown): T {
  let validator = validators.get(name)
  if (!validator) { validator = compileDefinition(name); validators.set(name, validator) }
  if (!validator(value)) {
    const error = (validator.errors ?? []).filter((item) => !['oneOf', 'anyOf', 'const'].includes(item.keyword))
      .sort((a, b) => b.instancePath.split('/').length - a.instancePath.split('/').length)[0] ?? validator.errors?.[0]
    const path = (error?.instancePath ?? '').split('/').filter(Boolean).map((part) => /^[0-9]+$/.test(part) ? Number(part) : part.replace(/~1/g, '/').replace(/~0/g, '~'))
    throw new OverlapContractError(`工作色彩解释响应不符合 Python ${COLOR_PREPARED_VERSION} Schema：${error?.instancePath ?? '/'} ${error?.message ?? '无效字段'}`, path)
  }
  return value as T
}
export const parseColorPreparedSourceCreateRequest = (value: unknown): ColorPreparedSourceCreateRequest => parse('ColorPreparedSourceCreateRequest', value)
export const parseColorPreparedSourceViewRequest = (value: unknown): ColorPreparedSourceViewRequest => parse('ColorPreparedSourceViewRequest', value)
export const parseColorPreparedSourceOperationRequest = (value: unknown): ColorPreparedSourceOperationRequest => parse('ColorPreparedSourceOperationRequest', value)
export const parseColorPreparedSourceOperationEnvelope = (value: unknown): ColorPreparedSourceOperationEnvelope => parse('ColorPreparedSourceOperationEnvelope', value)
export const parseColorPreparedSourceChooseRequest = (value: unknown): ColorPreparedSourceChooseRequest => parse('ColorPreparedSourceChooseRequest', value)
export const parseColorPreparedSourceViewEnvelope = (value: unknown): ColorPreparedSourceViewEnvelope => {
  const view = parse<ColorPreparedSourceViewEnvelope>('ColorPreparedSourceViewEnvelope', value)
  if ((view.progress && view.progress.node_run_id !== view.current_node_run_id) || (view.handoff && view.handoff.run_id !== view.run_id) ||
      (view.state === 'ready' && (view.admission_status !== 'completed' || !view.reference_path))) {
    throw new OverlapContractError('工作色彩准备响应的身份或准入状态不一致', [])
  }
  return view
}
export const parseColorPreparedSourceProcessingRequest = (value: unknown): ColorPreparedSourceProcessingRequest => parse('ColorPreparedSourceProcessingRequest', value)
export const parseColorPreparedSourceProcessingEnvelope = (value: unknown): ColorPreparedSourceProcessingEnvelope => parse('ColorPreparedSourceProcessingEnvelope', value)
export const parseColorPreparedSourceFullRequest = (value: unknown): ColorPreparedSourceFullRequest => parse('ColorPreparedSourceFullRequest', value)
export const parseColorPreparedSourceFullEnvelope = (value: unknown): ColorPreparedSourceFullEnvelope => parse('ColorPreparedSourceFullEnvelope', value)
export const parseColorPreparedSourceFailure = (value: unknown): ColorPreparedSourceFailureEnvelope => parse('ColorPreparedSourceFailureEnvelope', value)
