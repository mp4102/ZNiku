/** 0.3.6 融合候选的独立 wire；不扩展或改写旧 0.3.5 请求。 */
import type { ValidateFunction } from 'ajv'
import { compileDefinition } from './contracts'
import { OverlapContractError } from './chapter-overlap-contracts'
import { sourceAdmittedCatalogAvailable, type SourceAdmittedFullEnvelope, type SourceAdmittedFullIntent } from './source-admitted-contracts'

export interface FusedFullIntent extends Omit<SourceAdmittedFullIntent, 'contract_version'> {
  readonly contract_version: '0.3.6'
  readonly export_cropped_chapters: boolean
}
export interface FusedFullRequest extends FusedFullIntent {
  readonly project_session_id: string
  readonly expected_storage_revision: number
}
export interface FusedFullEnvelope extends Omit<SourceAdmittedFullEnvelope, 'contract_version' | 'profile_id' | 'profile_version'> {
  readonly contract_version: '0.3.6'
  readonly profile_id: 'zniku.source-admitted.chapter-batch-fused'
  readonly profile_version: '0.3.6'
  readonly export_cropped_chapters: boolean
}
export function fusedCatalogAvailable(nodes: ReadonlyArray<{ readonly type_id: string; readonly definition_version: string }>): boolean {
  return sourceAdmittedCatalogAvailable(nodes) && ['zniku.source-admitted.chapter-batch.program-fused',
    'zniku.source-admitted.chapter-batch.final-publish-fused'].every((typeId) => nodes.some((node) => node.type_id === typeId && node.definition_version === '0.3.6'))
}
const validators = new Map<string, ValidateFunction>()
function parse<T>(name: string, value: unknown): T {
  let validator = validators.get(name)
  if (!validator) { validator = compileDefinition(name); validators.set(name, validator) }
  if (!validator(value)) {
    const first = validator.errors?.find((error) => !['oneOf', 'anyOf', 'const'].includes(error.keyword)) ?? validator.errors?.[0]
    const fieldPath = (first?.instancePath ?? '').split('/').filter(Boolean).map((part) => /^[0-9]+$/.test(part) ? Number(part) : part.replace(/~1/g, '/').replace(/~0/g, '~'))
    throw new OverlapContractError(`设置不符合 Python 0.3.6 Schema：${first?.instancePath ?? '/'} ${first?.message ?? '无效字段'}`, fieldPath)
  }
  return value as T
}
export const parseFusedFullRequest = (value: unknown): FusedFullRequest => parse('FusedFullRequest', value)
export const parseFusedFullEnvelope = (value: unknown): FusedFullEnvelope => parse('FusedFullEnvelope', value)
