/** 有界显示新分章意图；分页不裁掉未显示的行，不自行对时间/帧排序、去重或规划。 */
import { useEffect, useId, useRef, useState } from 'react'
import {
  CHAPTER_CUT_LIMIT, CHAPTER_ROWS_PER_PAGE, chapterIssueRow,
  type ChapterOverlapFieldIssue, type ChapterOverlapSettingsDraft,
} from './chapter-overlap-form'
import './chapter-overlap-form.css'

interface Props {
  readonly value: ChapterOverlapSettingsDraft
  readonly disabled?: boolean
  readonly issue?: ChapterOverlapFieldIssue | null
  readonly onChange: (value: ChapterOverlapSettingsDraft) => void
}

export function ChapterOverlapSettingsForm({ value, disabled = false, issue = null, onChange }: Props) {
  const id = useId()
  const [page, setPage] = useState(0)
  const [focusIndex, setFocusIndex] = useState<number | null>(null)
  const inputRefs = useRef(new Map<number, HTMLInputElement>())
  const countRef = useRef<HTMLInputElement | null>(null)
  const leafRef = useRef<HTMLInputElement | null>(null)
  const modeRef = useRef<HTMLSelectElement | null>(null)
  const rows = value.mode === 'exact_times' ? value.timeCuts : value.frameCuts
  const rowIssue = chapterIssueRow(issue, value.mode)
  const pages = Math.max(1, Math.ceil(rows.length / CHAPTER_ROWS_PER_PAGE))
  const currentPage = Math.min(page, pages - 1)
  const first = currentPage * CHAPTER_ROWS_PER_PAGE
  const isTime = value.mode === 'exact_times'
  const modeIssue = issue?.fieldPath.includes('chapter_selector') && rowIssue === null
  const countIssue = issue?.fieldPath.includes('count') ?? false
  const leafIssue = issue?.fieldPath.includes('leaf_max_minutes') ?? false
  const errorId = `${id}-issue`

  useEffect(() => {
    if (!issue) return
    if (rowIssue !== null && rowIssue < rows.length) {
      setPage(Math.floor(rowIssue / CHAPTER_ROWS_PER_PAGE))
      setFocusIndex(rowIssue)
    } else if (leafIssue) leafRef.current?.focus()
    else if (countIssue) countRef.current?.focus()
    else if (modeIssue) modeRef.current?.focus()
  }, [issue, rowIssue, rows.length, leafIssue, countIssue, modeIssue])

  useEffect(() => {
    if (focusIndex === null) return
    const input = inputRefs.current.get(focusIndex)
    if (input) { input.focus(); setFocusIndex(null) }
  }, [focusIndex, first, value.mode, rows.length])

  const replaceRows = (next: ReadonlyArray<string>) => onChange(isTime ? { ...value, timeCuts: next } : { ...value, frameCuts: next })
  const addRow = () => {
    if (disabled || rows.length >= CHAPTER_CUT_LIMIT) return
    replaceRows([...rows, ''])
    setPage(Math.floor(rows.length / CHAPTER_ROWS_PER_PAGE))
    setFocusIndex(rows.length)
  }
  const removeRow = (index: number) => {
    if (disabled) return
    const next = rows.filter((_, row) => row !== index)
    replaceRows(next)
    setPage(Math.min(currentPage, Math.max(0, Math.ceil(next.length / CHAPTER_ROWS_PER_PAGE) - 1)))
    if (next.length) setFocusIndex(Math.min(index, next.length - 1))
    else modeRef.current?.focus()
  }

  return <div className="overlap-chapter-settings">
    <div className="template-form-grid">
      <label>章节切分<select aria-label="章节切分方式" ref={modeRef} disabled={disabled}
        aria-invalid={modeIssue || undefined} aria-describedby={modeIssue ? errorId : undefined}
        value={value.mode} onChange={(event) => {
          const mode = event.target.value
          if (mode !== 'average' && mode !== 'exact_times' && mode !== 'exact_frames') return
          setPage(0); setFocusIndex(null); onChange({ ...value, mode })
        }}>
        <option value="average">平均分章</option><option value="exact_times">精确时间分章</option><option value="exact_frames">精确帧分章</option>
      </select></label>
      {value.mode === 'average' && <label>平均章数<input aria-label="平均章数" ref={countRef} inputMode="numeric" disabled={disabled}
        aria-invalid={countIssue || undefined} aria-describedby={countIssue ? errorId : `${id}-average-help`}
        value={value.averageCount} onChange={(event) => onChange({ ...value, averageCount: event.target.value })} />
        <small id={`${id}-average-help`}>默认 1 为整篇一章；最多 1000 章，具体边界由 Python 按准确帧数平均分配。</small></label>}
    </div>
    {value.mode !== 'average' && <section className="overlap-cut-editor" aria-label={isTime ? '时间切分点' : '帧切分点'}>
      <p id={`${id}-cut-help`}>{isTime
        ? '相对素材开头的 HH:MM:SS，例如 00:30:00；小时可超过 23。帧对齐与时长由 Python 计算。'
        : '填写下一章的零基首帧，例如 899 表示从第 899 帧开始下一章，不是每隔 899 帧切分。'}</p>
      <p className="overlap-cut-count" role="status">已添加 {rows.length} / {CHAPTER_CUT_LIMIT} 个切分点{rows.length ? `；全部有效时生成 ${rows.length + 1} 章` : '；请添加切分点或改为平均 1 章'}。</p>
      <ol className="overlap-cut-rows" start={first + 1}>
        {rows.slice(first, first + CHAPTER_ROWS_PER_PAGE).map((entry, offset) => {
          const index = first + offset
          const invalid = rowIssue === index
          return <li key={`${value.mode}-${index}`}>
            <label><span>{index + 1}</span><input aria-label={`第 ${index + 1} 个${isTime ? '时间' : '帧'}切分点`}
              ref={(input) => { if (input) inputRefs.current.set(index, input); else inputRefs.current.delete(index) }}
              aria-invalid={invalid || undefined} aria-describedby={invalid ? errorId : `${id}-cut-help`}
              disabled={disabled} inputMode={isTime ? 'text' : 'numeric'} placeholder={isTime ? '00:30:00' : '899'}
              value={entry} onChange={(event) => replaceRows(rows.map((previous, row) => row === index ? event.target.value : previous))} /></label>
            <button type="button" className="button button--ghost" aria-label={`删除第 ${index + 1} 个切分点`} disabled={disabled} onClick={() => removeRow(index)}>删除</button>
          </li>
        })}
      </ol>
      <div className="overlap-cut-actions"><button className="button button--ghost" type="button" disabled={disabled || rows.length >= CHAPTER_CUT_LIMIT} onClick={addRow}>添加切分点</button>
        {pages > 1 && <nav className="overlap-pagination" aria-label="切分点分页">
          <button type="button" disabled={disabled || currentPage === 0} onClick={() => setPage(0)}>首页</button>
          <button type="button" disabled={disabled || currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button>
          <span aria-live="polite">第 {currentPage + 1} / {pages} 页 · 每页最多 {CHAPTER_ROWS_PER_PAGE} 项</span>
          <button type="button" disabled={disabled || currentPage === pages - 1} onClick={() => setPage(currentPage + 1)}>下一页</button>
          <button type="button" disabled={disabled || currentPage === pages - 1} onClick={() => setPage(pages - 1)}>末页</button>
        </nav>}
      </div>
      <small>按顺序逐项填写；不会自动排序、去重或丢弃空行。分页只改变显示，不删减输入。</small>
    </section>}
    <label className="overlap-leaf-setting">每段最长时长（分叶）<input aria-label="每段最长时长（分叶）" ref={leafRef}
      disabled={disabled} inputMode="numeric" aria-invalid={leafIssue || undefined} aria-describedby={leafIssue ? errorId : `${id}-leaf-help`}
      value={value.leafMaxMinutes} onChange={(event) => onChange({ ...value, leafMaxMinutes: event.target.value })} />
      <small id={`${id}-leaf-help`}>默认 5 分钟，可设 1–60 分钟。短章保留一段；长章在本章内均分，避免很短的尾段。实际分段结果以 Python 预览为准。</small></label>
    {issue && <p className="template-local-error" role="alert" id={errorId}>{issue.message}</p>}
  </div>
}
