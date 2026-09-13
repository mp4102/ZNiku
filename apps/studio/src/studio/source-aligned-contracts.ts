/** 仅投影 Python 0.3.3 原片规划合同；浏览器不计算帧边界或决定外部媒体合格。 */
import type { ValidateFunction } from 'ajv'
import { compileDefinition } from './contracts'
import { OverlapContractError, overlapProcessingMatches, type OverlapFullEnvelope, type OverlapFullIntent, type OverlapProcessing } from './chapter-overlap-contracts'

/** 新界面连接旧服务时不能以客户端方法存在冒充服务能力；只识别明确发布的 exact 节点目录。 */
export function sourceAlignedCatalogAvailable(nodes: ReadonlyArray<{ readonly type_id: string; readonly definition_version: string }>): boolean {
  const published = new Set(nodes.filter((node) => node.definition_version === '0.3.3').map((node) => node.type_id))
  return ['zniku.overlap.split.leaves.1', 'zniku.overlap.enhancement.external', 'zniku.overlap.merge_video',
    'zniku.overlap.fi_context', 'zniku.overlap.frame_interpolation.external', 'zniku.overlap.fi_crop',
    'zniku.overlap.program_encode', 'zniku.overlap.final_mux', 'zniku.source_aligned.external.mp4',
    'zniku.source_aligned.external.mov', 'zniku.source_aligned.external.mkv'].every((typeId) => published.has(typeId))
}

export type SourceAlignedMR = { readonly mode: 'off' } | {
  readonly mode: 'external'
  readonly model_name: string
  readonly model_version?: string | null
  readonly declared_container?: 'mp4' | 'mov' | 'mkv'
  readonly operator_frame_order_confirmed: true
}
export interface SourceAlignedProcessing extends OverlapProcessing { readonly mr?: SourceAlignedMR }
export interface SourceAlignedProcessingRequest { readonly contract_version: '0.3.3'; readonly processing: SourceAlignedProcessing }
export interface SourceAlignedProcessingEnvelope extends SourceAlignedProcessingRequest { readonly status: 'pending_real_acceptance' }
export interface SourceAlignedFullIntent extends SourceAlignedProcessingRequest {
  readonly preparation_run_id: string
  readonly publication: OverlapFullIntent['publication']
}
export interface SourceAlignedFullRequest extends SourceAlignedFullIntent {
  readonly project_session_id: string
  readonly expected_storage_revision: number
}
export interface SourceAlignedFullEnvelope extends Omit<OverlapFullEnvelope, 'contract_version' | 'profile_id' | 'profile_version' | 'processing'> {
  readonly contract_version: '0.3.3'
  readonly profile_id: 'zniku.source-aligned-overlap'
  readonly profile_version: '0.3.3'
  readonly processing: SourceAlignedProcessing
}
export interface SourceAlignedFailureEnvelope {
  readonly contract_version: '0.3.3'
  readonly error: { readonly code: string; readonly message: string; readonly field_path: ReadonlyArray<string | number>; readonly related_run_ids: ReadonlyArray<string> }
}
export function sourceAlignedProcessingMatches(left: SourceAlignedProcessing, right: SourceAlignedProcessing): boolean {
  const normalizeMR = (value: SourceAlignedProcessing) => value.mr?.mode === 'external'
    ? { mode: 'external', model_name: value.mr.model_name, model_version: value.mr.model_version ?? null,
      declared_container: value.mr.declared_container ?? 'mp4', operator_frame_order_confirmed: value.mr.operator_frame_order_confirmed }
    : { mode: 'off' }
  return overlapProcessingMatches(left, right) && JSON.stringify(normalizeMR(left)) === JSON.stringify(normalizeMR(right))
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
    throw new OverlapContractError(`设置不符合 Python 0.3.3 Schema：${first?.instancePath ?? '/'} ${first?.message ?? '无效字段'}`, fieldPath)
  }
  return value as T
}
export const parseSourceAlignedProcessingRequest = (value: unknown): SourceAlignedProcessingRequest => parse('SourceAlignedProcessingRequest', value)
export const parseSourceAlignedProcessingEnvelope = (value: unknown): SourceAlignedProcessingEnvelope => parse('SourceAlignedProcessingEnvelope', value)
export const parseSourceAlignedFullRequest = (value: unknown): SourceAlignedFullRequest => parse('SourceAlignedFullRequest', value)
export const parseSourceAlignedFullEnvelope = (value: unknown): SourceAlignedFullEnvelope => parse('SourceAlignedFullEnvelope', value)
export const parseSourceAlignedFailure = (value: unknown): SourceAlignedFailureEnvelope => parse('SourceAlignedFailureEnvelope', value)
