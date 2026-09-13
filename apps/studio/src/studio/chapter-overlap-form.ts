/** 新方案的表单草稿只保存用户原文；不计算媒体帧边界、章长、分叶或上下文。 */
export type ChapterOverlapSelectorMode = 'average' | 'exact_times' | 'exact_frames'

export interface ChapterOverlapSettingsDraft {
  readonly mode: ChapterOverlapSelectorMode
  readonly averageCount: string
  readonly frameCuts: ReadonlyArray<string>
  readonly timeCuts: ReadonlyArray<string>
  readonly leafMaxMinutes: string
}

export interface ChapterOverlapFieldIssue {
  readonly fieldPath: ReadonlyArray<string | number>
  readonly message: string
}

/** 与 Python ChapterSettings 对应的用户意图；最终仍由生成的 Schema 和服务校验。 */
export interface ChapterOverlapSettingsIntent {
  readonly chapter_selector:
    | { readonly mode: 'average'; readonly count: number }
    | { readonly mode: 'exact_times'; readonly times: ReadonlyArray<string> }
    | { readonly mode: 'exact_frames'; readonly frames: ReadonlyArray<number> }
  readonly leaf_max_minutes: number
}

export const CHAPTER_CUT_LIMIT = 999
export const CHAPTER_ROWS_PER_PAGE = 20

export function initialChapterOverlapSettings(): ChapterOverlapSettingsDraft {
  return { mode: 'average', averageCount: '1', frameCuts: [''], timeCuts: [''], leafMaxMinutes: '5' }
}

export class ChapterOverlapDraftError extends Error {
  constructor(readonly issue: ChapterOverlapFieldIssue) {
    super(issue.message)
    this.name = 'ChapterOverlapDraftError'
  }
}

function integerInput(value: string, name: string, fieldPath: ChapterOverlapFieldIssue['fieldPath']): number {
  if (!/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(Number(value))) {
    throw new ChapterOverlapDraftError({ fieldPath, message: `${name}必须填写严格正整数，且不能超过浏览器可精确表示的整数范围。` })
  }
  return Number(value)
}

export function chapterSettingsIntent(draft: ChapterOverlapSettingsDraft): ChapterOverlapSettingsIntent {
  const path = ['settings', 'chapter_selector'] as const
  const leaf_max_minutes = integerInput(draft.leafMaxMinutes, '每段最长时长', ['settings', 'leaf_max_minutes'])
  if (draft.mode === 'average') {
    return { chapter_selector: { mode: 'average', count: integerInput(draft.averageCount, '平均章数', [...path, 'count']) }, leaf_max_minutes }
  }
  const field = draft.mode === 'exact_frames' ? 'frames' : 'times'
  const rows = draft.mode === 'exact_frames' ? draft.frameCuts : draft.timeCuts
  if (rows.length === 0 || rows.length > CHAPTER_CUT_LIMIT) {
    throw new ChapterOverlapDraftError({ fieldPath: [...path, field], message: '请添加 1–999 个切分点；整片一章请选择平均分章 1。' })
  }
  if (draft.mode === 'exact_frames') {
    return { chapter_selector: { mode: 'exact_frames', frames: rows.map((value, index) => integerInput(value, `第 ${index + 1} 个帧切分点`, [...path, 'frames', index])) }, leaf_max_minutes }
  }
  rows.forEach((value, index) => {
    // 空行不能丢弃；其余时间格式及顺序原样交给 Python，不在浏览器换算秒或帧。
    if (value === '') throw new ChapterOverlapDraftError({ fieldPath: [...path, 'times', index], message: `请填写第 ${index + 1} 个时间切分点。` })
  })
  return { chapter_selector: { mode: 'exact_times', times: [...rows] }, leaf_max_minutes }
}

export function chapterIssueRow(issue: ChapterOverlapFieldIssue | null | undefined, mode: ChapterOverlapSelectorMode): number | null {
  if (!issue || mode === 'average') return null
  const key = mode === 'exact_frames' ? 'frames' : 'times'
  const position = issue.fieldPath.indexOf(key)
  const value = position < 0 ? undefined : issue.fieldPath[position + 1]
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null
}
