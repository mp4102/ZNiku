/** 仅投影 Python 0.3.2 wire；不在浏览器计算分章、分叶或裁边。 */
import type { ValidateFunction } from 'ajv'
import { compileDefinition, StudioContractError, type AvEnhanceV27ExpandRequestWire } from './contracts'
import type { ChapterOverlapSettingsIntent, ChapterOverlapFieldIssue } from './chapter-overlap-form'

export interface OverlapProcessing {
  readonly settings: ChapterOverlapSettingsIntent
  readonly enhancement: { readonly model_name: string; readonly model_version?: string | null; readonly actual_scale_factor?: number }
  readonly fi_profile: {
    readonly software_version: 'v1.0'
    readonly model_name: 'Aion'
    readonly status: 'pending_real_acceptance'
    readonly phase: 'even-input-2m-minus-1'
    readonly left_context_frames: number
    readonly right_context_frames: number
    readonly minimum_input_frames: number
  }
  readonly program_encode: AvEnhanceV27ExpandRequestWire['program_encode']
}
export interface OverlapProcessingRequest { readonly contract_version: '0.3.2'; readonly processing: OverlapProcessing }
export interface OverlapProcessingEnvelope extends OverlapProcessingRequest { readonly status: 'pending_real_acceptance' }
export interface OverlapFullIntent extends OverlapProcessingRequest {
  readonly preparation_run_id: string
  readonly publication: AvEnhanceV27ExpandRequestWire['publication']
}
export interface OverlapFullRequest extends OverlapFullIntent {
  readonly project_session_id: string
  readonly expected_storage_revision: number
}
export interface OverlapFrameSpan {
  readonly start_frame: number; readonly end_frame: number; readonly frame_count: number
  readonly start_seconds: string; readonly end_seconds: string; readonly duration_seconds: string
  readonly start_timecode: string; readonly end_timecode: string; readonly duration_timecode: string
}
export interface OverlapLeaf extends OverlapFrameSpan {
  readonly leaf_id: string; readonly global_ordinal: number; readonly chapter_ordinal: number; readonly ordinal: number
}
export interface OverlapChapter extends OverlapFrameSpan {
  readonly chapter_id: string; readonly ordinal: number; readonly label: string; readonly leaves: ReadonlyArray<OverlapLeaf>
}
export interface OverlapPlan {
  readonly profile_id: 'zniku.chapter-overlap-fi'; readonly profile_version: '0.3.2'
  readonly source: { readonly artifact_id: string; readonly frame_count: number; readonly frame_rate: string }
  readonly settings: ChapterOverlapSettingsIntent
  readonly chapter_count: number; readonly leaf_count: number; readonly leaf_frame_cap: number
  readonly cut_points: ReadonlyArray<{
    readonly ordinal: number; readonly requested_time: string | null; readonly requested_frame: number | null
    readonly actual_frame: number; readonly actual_seconds: string; readonly actual_timecode: string
  }>
  readonly chapters: ReadonlyArray<OverlapChapter>
}
export interface OverlapContext {
  readonly chapter_id: string; readonly chapter_ordinal: number
  readonly formal_start_frame: number; readonly formal_end_frame: number
  readonly context_start_frame: number; readonly context_end_frame: number
  readonly input_frame_count: number; readonly raw_fi_frame_count: number
  readonly crop_start_frame: number; readonly crop_end_frame: number; readonly cropped_frame_count: number
  readonly global_start_half_frame: number; readonly global_end_half_frame: number
  readonly sources: ReadonlyArray<{
    readonly chapter_id: string; readonly chapter_ordinal: number
    readonly source_start_frame: number; readonly source_end_frame: number
    readonly chapter_local_start_frame: number; readonly chapter_local_end_frame: number
    readonly context_local_start_frame: number; readonly context_local_end_frame: number; readonly frame_count: number
  }>
}
export interface OverlapContexts {
  readonly status: 'mathematical-only'; readonly assumption: 'even-input-2m-minus-1'
  readonly chapter_plan: OverlapPlan
  readonly settings: { readonly left_context_frames: number; readonly right_context_frames: number; readonly minimum_input_frames: number }
  readonly chapters: ReadonlyArray<OverlapContext>
  readonly unpadded_frame_count: number; readonly final_tail_clone_frames: 1; readonly encoded_frame_count: number
}
export interface OverlapFullEnvelope extends OverlapProcessingEnvelope {
  readonly profile_id: 'zniku.chapter-overlap-fi'; readonly profile_version: '0.3.2'
  readonly project_session_id: string; readonly storage_revision: number; readonly preparation_run_id: string
  readonly execution_available: true; readonly plan: OverlapPlan; readonly contexts: OverlapContexts
  readonly publication: { readonly output_target_path: string; readonly output_directory_to_create: string | null }
  readonly node_count: number; readonly edge_count: number
}
export interface OverlapFailureEnvelope {
  readonly contract_version: '0.3.2'
  readonly error: { readonly code: string; readonly message: string; readonly field_path: ReadonlyArray<string | number>; readonly related_run_ids: ReadonlyArray<string> }
}
/** 比较服务器回显的设置与已发送意图；只补 Python 声明的默认值，不计算媒体边界。 */
export function overlapProcessingMatches(left: OverlapProcessing, right: OverlapProcessing): boolean {
  const normalized = (value: OverlapProcessing) => {
    const selector = value.settings.chapter_selector
    return {
      settings: { leaf_max_minutes: value.settings.leaf_max_minutes, chapter_selector: selector.mode === 'average'
        ? { mode: selector.mode, count: selector.count } : selector.mode === 'exact_times' ? { mode: selector.mode, times: selector.times } : { mode: selector.mode, frames: selector.frames } },
      enhancement: { model_name: value.enhancement.model_name, model_version: value.enhancement.model_version ?? null, actual_scale_factor: value.enhancement.actual_scale_factor ?? 1 },
      fi_profile: { software_version: value.fi_profile.software_version, model_name: value.fi_profile.model_name, status: value.fi_profile.status, phase: value.fi_profile.phase,
        left_context_frames: value.fi_profile.left_context_frames, right_context_frames: value.fi_profile.right_context_frames, minimum_input_frames: value.fi_profile.minimum_input_frames },
      encoder: value.program_encode.encoder,
    }
  }
  return JSON.stringify(normalized(left)) === JSON.stringify(normalized(right))
}
export class OverlapContractError extends StudioContractError {
  readonly issue: ChapterOverlapFieldIssue
  constructor(message: string, fieldPath: ReadonlyArray<string | number>) { super(message); this.issue = { message, fieldPath } }
}
// 延迟编译让旧入口只使用旧 Schema；新 endpoint 首次调用必须存在精确定义，缺失时失败关闭。
const validators = new Map<string, ValidateFunction>()
function parse<T>(name: string, value: unknown): T {
  let validator = validators.get(name)
  if (!validator) { validator = compileDefinition(name); validators.set(name, validator) }
  if (!validator(value)) {
    const errors = validator.errors ?? []
    const first = errors.filter((error) => !['oneOf', 'anyOf', 'const'].includes(error.keyword))
      .sort((left, right) => right.instancePath.split('/').length - left.instancePath.split('/').length)[0] ?? errors[0]
    const fieldPath = (first?.instancePath ?? '').split('/').filter(Boolean).map((part) => /^[0-9]+$/.test(part) ? Number(part) : part.replace(/~1/g, '/').replace(/~0/g, '~'))
    throw new OverlapContractError(`设置不符合 Python 0.3.2 Schema：${first?.instancePath ?? '/'} ${first?.message ?? '无效字段'}`, fieldPath)
  }
  return value as T
}
export const parseOverlapProcessingRequest = (value: unknown): OverlapProcessingRequest => parse('ChapterOverlapProcessingRequest', value)
export const parseOverlapProcessingEnvelope = (value: unknown): OverlapProcessingEnvelope => parse('ChapterOverlapProcessingEnvelope', value)
export const parseOverlapFullRequest = (value: unknown): OverlapFullRequest => parse('ChapterOverlapFullRequest', value)
export const parseOverlapFullEnvelope = (value: unknown): OverlapFullEnvelope => parse('ChapterOverlapFullEnvelope', value)
export const parseOverlapFailure = (value: unknown): OverlapFailureEnvelope => parse('ChapterOverlapFailureEnvelope', value)
