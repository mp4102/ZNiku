/** 工程内部中转维护的 Python 读模型；候选 ID 不能由路径或文件名在浏览器中推导。 */
export type ScratchCategory = 'archive' | 'registered_recreatable' | 'internal_scratch' | 'unknown'

export interface ScratchPreviewRequest {
  readonly contract_version: '0.3.0'
  readonly project_session_id: string
  readonly expected_storage_revision: number
}
export interface ScratchSummary {
  readonly category: ScratchCategory
  readonly file_count: number
  readonly byte_count: number
}
export interface ScratchFileEntry {
  readonly path: string
  readonly category: ScratchCategory
  readonly byte_count: number
  readonly node_id: string | null
  readonly node_run_id: string | null
  readonly attempt: number | null
  readonly task_label: string
  readonly chapter_label: string | null
  readonly round_label: string
  readonly role: string
  readonly reason: string
  readonly candidate_id: string | null
}
export interface ScratchPreviewEnvelope extends ScratchPreviewRequest {
  readonly ticket_id: string
  readonly expires_in_seconds: number
  readonly summary: ReadonlyArray<ScratchSummary>
  readonly entries: ReadonlyArray<ScratchFileEntry>
  readonly warnings: ReadonlyArray<string>
  readonly truncated: boolean
}
export interface ScratchConfirmRequest extends ScratchPreviewRequest {
  readonly ticket_id: string
  readonly candidate_ids: ReadonlyArray<string>
  readonly confirm_irreversible: true
}
export interface ScratchConfirmEnvelope extends ScratchPreviewRequest {
  readonly ticket_id: string
  readonly entries: ReadonlyArray<{
    readonly candidate_id: string
    readonly path: string
    readonly byte_count: number
    readonly status: 'deleted' | 'skipped' | 'failed'
    readonly message: string
  }>
  readonly deleted_bytes: number
  readonly deletion_count: number
  readonly complete: boolean
  readonly warnings: ReadonlyArray<string>
}

export function assertScratchBinding(expected: ScratchPreviewRequest, result: ScratchPreviewRequest): void {
  if (expected.contract_version !== result.contract_version || expected.project_session_id !== result.project_session_id ||
    expected.expected_storage_revision !== result.expected_storage_revision) throw new Error('工程数据维护响应与当前工程会话或存储版本不一致。')
}
