import Ajv2020, { type ErrorObject } from 'ajv/dist/2020.js'
import authoringSchema from '../generated/authoring-wire.schema.json'
import coreNodeContractsJson from '../generated/core-node-contracts.json'
import coreNodeSchema from '../generated/core-node-contracts.schema.json'
import engineManifestSchema from '../generated/engine-manifest.schema.json'
import projectionManifestJson from '../generated/projection-manifest.json'
import projectionManifestSchema from '../generated/projection-manifest.schema.json'
import studioAuthorityJson from '../generated/studio-authority.json'
import studioAuthoritySchema from '../generated/studio-authority.schema.json'
import type {
  AuthoringCommand,
  AuthoringCommandRejected,
  EngineBinding,
  WireParseFailure,
  WorkflowDraftSnapshot,
} from '../generated/authoring-wire.generated'
import type { CoreNodeContractSet } from '../generated/core-node-contracts.generated'
import type { EngineManifest } from '../generated/engine-manifest.generated'
import type { ProjectionManifest } from '../generated/projection-manifest.generated'
import type { StudioAuthorityProjection } from '../generated/studio-authority.generated'

export type AuthoringResponse = WorkflowDraftSnapshot | AuthoringCommandRejected | WireParseFailure

export class ContractBoundaryError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ContractBoundaryError'
  }
}

const ajv = new Ajv2020({ allErrors: true, strict: true })
const validateAuthoringDocument = ajv.compile(authoringSchema)
const validateEngineManifest = ajv.compile(engineManifestSchema)
const validateCoreNodeContracts = ajv.compile(coreNodeSchema)
const validateProjectionManifest = ajv.compile(projectionManifestSchema)
const validateStudioAuthority = ajv.compile(studioAuthoritySchema)

function validationMessage(errors: ErrorObject[] | null | undefined): string {
  if (!errors || errors.length === 0) return 'unknown schema violation'
  return errors
    .slice(0, 3)
    .map((error) => `${error.instancePath || '/'} ${error.message ?? error.keyword}`)
    .join('; ')
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function parseAuthoringResponse(value: unknown): AuthoringResponse {
  if (!validateAuthoringDocument(value)) {
    throw new ContractBoundaryError(
      `authoring payload 不符合 Python wire Schema：${validationMessage(validateAuthoringDocument.errors)}`,
    )
  }
  if (!record(value) || typeof value.result_kind !== 'string') {
    throw new ContractBoundaryError('authoring response 缺少 result_kind discriminator')
  }
  if (
    value.result_kind !== 'draft_snapshot' &&
    value.result_kind !== 'command_rejected' &&
    value.result_kind !== 'wire_parse_failure'
  ) {
    throw new ContractBoundaryError(`未知 authoring result_kind：${value.result_kind}`)
  }
  return value as unknown as AuthoringResponse
}

export function parseAuthoringCommand(value: unknown): AuthoringCommand {
  if (!validateAuthoringDocument(value)) {
    throw new ContractBoundaryError(
      `authoring command 不符合 Python wire Schema：${validationMessage(validateAuthoringDocument.errors)}`,
    )
  }
  if (!record(value) || typeof value.command_id !== 'string' || 'result_kind' in value) {
    throw new ContractBoundaryError('payload 不是 AuthoringCommand variant')
  }
  return value as unknown as AuthoringCommand
}

export function parseEngineManifest(value: unknown): EngineManifest {
  if (!validateEngineManifest(value)) {
    throw new ContractBoundaryError(
      `EngineManifest 不符合 Python Schema：${validationMessage(validateEngineManifest.errors)}`,
    )
  }
  return value as unknown as EngineManifest
}

function parseCoreNodeContracts(value: unknown): CoreNodeContractSet {
  if (!validateCoreNodeContracts(value)) {
    throw new ContractBoundaryError(
      `core node projection 不符合 Python Schema：${validationMessage(validateCoreNodeContracts.errors)}`,
    )
  }
  return value as unknown as CoreNodeContractSet
}

function parseProjectionManifest(value: unknown): ProjectionManifest {
  if (!validateProjectionManifest(value)) {
    throw new ContractBoundaryError(
      `projection manifest 不符合 Python Schema：${validationMessage(validateProjectionManifest.errors)}`,
    )
  }
  return value as unknown as ProjectionManifest
}

export function parseStudioAuthority(value: unknown): StudioAuthorityProjection {
  if (!validateStudioAuthority(value)) {
    throw new ContractBoundaryError(
      `Studio authority 不符合 Python Schema：${validationMessage(validateStudioAuthority.errors)}`,
    )
  }
  return value as unknown as StudioAuthorityProjection
}

export const coreNodeContracts = parseCoreNodeContracts(coreNodeContractsJson)
export const projectionManifest = parseProjectionManifest(projectionManifestJson)
export const studioAuthority = parseStudioAuthority(studioAuthorityJson)

function canonicalize(value: unknown): string {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return JSON.stringify(value)
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new ContractBoundaryError('canonical JSON 不允许非有限数字')
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalize(item)).join(',')}]`
  }
  if (record(value)) {
    const members = Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`)
    return `{${members.join(',')}}`
  }
  throw new ContractBoundaryError(`值不在 canonical JSON domain：${typeof value}`)
}

export async function sha256Digest(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalize(value))
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  const hex = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `sha256:${hex}`
}

export async function verifyProjectionAuthority(): Promise<void> {
  const actual = await sha256Digest(coreNodeContracts)
  if (actual !== projectionManifest.core_node_contract_digest) {
    throw new ContractBoundaryError('core-node-contracts.json 与 projection manifest digest 不一致')
  }
}

export async function verifySnapshot(snapshot: WorkflowDraftSnapshot): Promise<void> {
  if (
    snapshot.draft_id !== snapshot.validation.draft_id ||
    snapshot.spec_revision !== snapshot.validation.subject_revision
  ) {
    throw new ContractBoundaryError('Draft snapshot 与 validation envelope identity 不一致')
  }
  const result = snapshot.validation.result
  if (result.core_node_contract_digest !== projectionManifest.core_node_contract_digest) {
    throw new ContractBoundaryError('Compiler 与 Studio 使用了不同 core node contract digest')
  }
  const specDigest = await sha256Digest(snapshot.spec)
  if (specDigest !== result.spec_digest) {
    throw new ContractBoundaryError('Draft snapshot spec digest 与 Compiler result 不一致')
  }
  await verifyProjectionAuthority()
}

export async function verifyManifestBinding(
  binding: EngineBinding,
  manifest: EngineManifest,
): Promise<void> {
  if (
    binding.engine_id !== manifest.engine_id ||
    binding.engine_version !== manifest.engine_version
  ) {
    throw new ContractBoundaryError('EngineManifest identity 与 WorkflowSpec EngineBinding 不一致')
  }
  const digest = await sha256Digest(manifest)
  if (digest !== binding.manifest_digest) {
    throw new ContractBoundaryError('EngineManifest canonical digest 与 WorkflowSpec binding 不一致')
  }
}
