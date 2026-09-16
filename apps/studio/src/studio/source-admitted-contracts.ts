/** Python 0.3.5 单一源准入的 wire 投影；不在浏览器判定媒体、帧率或修复成功。 */
import type { ValidateFunction } from 'ajv'
import { compileDefinition, type AvEnhanceV27PrepareRequestWire } from './contracts'
import { OverlapContractError } from './chapter-overlap-contracts'
import type { SourceAlignedFullEnvelope, SourceAlignedProcessing, SourceAlignedFailureEnvelope } from './source-aligned-contracts'

export interface SourceAdmittedCreateRequest {
  readonly contract_version: '0.3.5'
  readonly request: AvEnhanceV27PrepareRequestWire
  readonly data_parent_directory?: string | null
  readonly media_basename?: string | null
}
export interface SourceAdmittedReplaceRequest {
  readonly contract_version: '0.3.5'
  readonly project_session_id: string
  readonly expected_storage_revision: number
  readonly source_path: string
  readonly reference_change_confirmed: true
}
export interface SourceAdmittedCancelRequest {
  readonly contract_version: '0.3.5'
  readonly project_session_id: string
  readonly run_id: string
}
export interface SourceAdmittedProcessingRequest { readonly contract_version: '0.3.5'; readonly processing: SourceAlignedProcessing }
export interface SourceAdmittedProcessingEnvelope extends SourceAdmittedProcessingRequest { readonly status: 'pending_real_acceptance' }
export interface SourceAdmittedFullIntent extends SourceAdmittedProcessingRequest {
  readonly preparation_run_id: string
  readonly publication: import('./chapter-overlap-contracts').OverlapFullIntent['publication']
}
export interface SourceAdmittedFullRequest extends SourceAdmittedFullIntent {
  readonly project_session_id: string
  readonly expected_storage_revision: number
}
export interface SourceAdmittedFullEnvelope extends Omit<SourceAlignedFullEnvelope, 'contract_version' | 'profile_id' | 'profile_version'> {
  readonly contract_version: '0.3.5'
  readonly profile_id: 'zniku.source-admitted-overlap'
  readonly profile_version: '0.3.5'
  readonly warnings: ReadonlyArray<string>
}
export interface SourceAdmittedFailureEnvelope extends Omit<SourceAlignedFailureEnvelope, 'contract_version'> { readonly contract_version: '0.3.5' }

export function sourceAdmittedCatalogAvailable(nodes: ReadonlyArray<{ readonly type_id: string; readonly definition_version: string }>): boolean {
  const published = new Set(nodes.filter((node) => node.definition_version === '0.3.5').map((node) => node.type_id))
  return ['zniku.avenhance.v27.source_program', 'zniku.avenhance.v27.source_admission',
    'zniku.overlap.split.leaves.1', 'zniku.source-admitted.enhancement-batch.1', 'zniku.source-admitted.chapter-batch.merge',
    'zniku.source-admitted.chapter-batch.context', 'zniku.source-admitted.chapter-batch.fi', 'zniku.source-admitted.chapter-batch.crop',
    'zniku.source-admitted.chapter-batch.program', 'zniku.source-admitted.chapter-batch.final', 'zniku.source_aligned.external.mp4',
    'zniku.source_aligned.external.mov', 'zniku.source_aligned.external.mkv'].every((id) => published.has(id))
}
const validators = new Map<string, ValidateFunction>()
function parse<T>(name: string, value: unknown): T {
  let validator = validators.get(name)
  if (!validator) { validator = compileDefinition(name); validators.set(name, validator) }
  if (!validator(value)) {
    const first = validator.errors?.find((error) => !['oneOf', 'anyOf', 'const'].includes(error.keyword)) ?? validator.errors?.[0]
    const fieldPath = (first?.instancePath ?? '').split('/').filter(Boolean).map((part) => /^[0-9]+$/.test(part) ? Number(part) : part.replace(/~1/g, '/').replace(/~0/g, '~'))
    throw new OverlapContractError(`设置不符合 Python 0.3.5 Schema：${first?.instancePath ?? '/'} ${first?.message ?? '无效字段'}`, fieldPath)
  }
  return value as T
}
export const parseSourceAdmittedCreateRequest = (value: unknown): SourceAdmittedCreateRequest => parse('SourceAdmittedCreateRequest', value)
export const parseSourceAdmittedReplaceRequest = (value: unknown): SourceAdmittedReplaceRequest => parse('SourceAdmittedReplaceRequest', value)
export const parseSourceAdmittedCancelRequest = (value: unknown): SourceAdmittedCancelRequest => parse('SourceAdmittedCancelRequest', value)
export const parseSourceAdmittedProcessingRequest = (value: unknown): SourceAdmittedProcessingRequest => parse('SourceAdmittedProcessingRequest', value)
export const parseSourceAdmittedProcessingEnvelope = (value: unknown): SourceAdmittedProcessingEnvelope => parse('SourceAdmittedProcessingEnvelope', value)
export const parseSourceAdmittedFullRequest = (value: unknown): SourceAdmittedFullRequest => parse('SourceAdmittedFullRequest', value)
export const parseSourceAdmittedFullEnvelope = (value: unknown): SourceAdmittedFullEnvelope => parse('SourceAdmittedFullEnvelope', value)
export const parseSourceAdmittedFailure = (value: unknown): SourceAdmittedFailureEnvelope => parse('SourceAdmittedFailureEnvelope', value)
