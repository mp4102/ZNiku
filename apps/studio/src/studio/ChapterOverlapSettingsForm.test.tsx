/** 只检验表单原文、分页与字段定位；测试不使用 TypeScript 计算任何媒体分章结果。 */
import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChapterOverlapSettingsForm } from './ChapterOverlapSettingsForm'
import { chapterSettingsIntent, initialChapterOverlapSettings, ChapterOverlapDraftError, type ChapterOverlapFieldIssue, type ChapterOverlapSettingsDraft } from './chapter-overlap-form'

afterEach(cleanup)

function Harness({ initial = initialChapterOverlapSettings(), issue = null, disabled = false, changed = () => {} }: {
  readonly initial?: ChapterOverlapSettingsDraft
  readonly issue?: ChapterOverlapFieldIssue | null
  readonly disabled?: boolean
  readonly changed?: (value: ChapterOverlapSettingsDraft) => void
}) {
  const [draft, setDraft] = useState(initial)
  return <ChapterOverlapSettingsForm value={draft} issue={issue} disabled={disabled} onChange={(next) => { setDraft(next); changed(next) }} />
}

describe('新方案分章与独立分叶表单', () => {
  it('默认平均一章、最长五分钟，参数常驻且不产生服务动作', () => {
    const changed = vi.fn()
    render(<Harness changed={changed} />)
    expect(screen.getByRole('combobox', { name: '章节切分方式' })).toHaveValue('average')
    expect(screen.getByLabelText('平均章数')).toHaveValue('1')
    const leaf = screen.getByLabelText('每段最长时长（分叶）')
    expect(leaf).toHaveValue('5')
    expect(leaf.closest('details')).toBeNull()
    expect(screen.getByText(/短章保留一段/)).toBeVisible()
    expect(changed).not.toHaveBeenCalled()
    expect(chapterSettingsIntent(initialChapterOverlapSettings())).toEqual({ chapter_selector: { mode: 'average', count: 1 }, leaf_max_minutes: 5 })
  })

  it('切换模式保留各自原文和独立叶时长，不静默换算或排序', async () => {
    const user = userEvent.setup()
    const changed = vi.fn()
    render(<Harness changed={changed} />)
    await user.selectOptions(screen.getByLabelText('章节切分方式'), 'exact_times')
    fireEvent.change(screen.getByLabelText('第 1 个时间切分点'), { target: { value: '00:30:00' } })
    fireEvent.change(screen.getByLabelText('每段最长时长（分叶）'), { target: { value: '10' } })
    await user.selectOptions(screen.getByLabelText('章节切分方式'), 'exact_frames')
    fireEvent.change(screen.getByLabelText('第 1 个帧切分点'), { target: { value: '899' } })
    await user.selectOptions(screen.getByLabelText('章节切分方式'), 'exact_times')
    expect(screen.getByLabelText('第 1 个时间切分点')).toHaveValue('00:30:00')
    expect(screen.getByLabelText('每段最长时长（分叶）')).toHaveValue('10')
    expect(chapterSettingsIntent(changed.mock.calls.at(-1)![0])).toEqual({ chapter_selector: { mode: 'exact_times', times: ['00:30:00'] }, leaf_max_minutes: 10 })
  })

  it('999行只挂载一页且分页不截断999项，不能添加第1000点', async () => {
    const user = userEvent.setup()
    const changed = vi.fn()
    const initial = { ...initialChapterOverlapSettings(), mode: 'exact_frames' as const, frameCuts: Array.from({ length: 999 }, (_, index) => String(index + 1)) }
    render(<Harness initial={initial} changed={changed} />)
    const editor = screen.getByRole('region', { name: '帧切分点' })
    expect(within(editor).getAllByRole('textbox')).toHaveLength(20)
    expect(screen.getByRole('button', { name: '添加切分点' })).toBeDisabled()
    expect(screen.getByText(/已添加 999 \/ 999 个切分点；全部有效时生成 1000 章/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: '末页' }))
    expect(within(editor).getAllByRole('textbox')).toHaveLength(19)
    expect(screen.getByLabelText('第 999 个帧切分点')).toHaveValue('999')
    fireEvent.change(screen.getByLabelText('第 999 个帧切分点'), { target: { value: '1200' } })
    const latest = changed.mock.calls.at(-1)![0] as ChapterOverlapSettingsDraft
    const request = chapterSettingsIntent(latest)
    expect(request.chapter_selector).toEqual({ mode: 'exact_frames', frames: [...Array.from({ length: 998 }, (_, index) => index + 1), 1200] })
    await user.click(screen.getByRole('button', { name: '首页' }))
    expect(screen.getByLabelText('第 1 个帧切分点')).toHaveValue('1')
  })

  it('逐行服务错误自动打开正确页并聚焦，没有把第999行错误挂到第19行', async () => {
    const initial = { ...initialChapterOverlapSettings(), mode: 'exact_times' as const, timeCuts: Array.from({ length: 999 }, () => '00:30:00') }
    const { rerender } = render(<Harness initial={initial} />)
    const issue = { fieldPath: ['settings', 'chapter_selector', 'times', 998], message: '第999行对齐后的帧重复，请调整。' }
    rerender(<Harness initial={initial} issue={issue} />)
    const input = await screen.findByLabelText('第 999 个时间切分点')
    await waitFor(() => expect(input).toHaveFocus())
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription(issue.message)
    expect(screen.queryByLabelText('第 19 个时间切分点')).not.toBeInTheDocument()
    expect(screen.getAllByRole('alert')).toHaveLength(1)
  })

  it('单页可由键盘添加和删除，新增空行必须保留并获得焦点', async () => {
    const user = userEvent.setup()
    const changed = vi.fn()
    render(<Harness changed={changed} initial={{ ...initialChapterOverlapSettings(), mode: 'exact_frames', frameCuts: ['899'] }} />)
    screen.getByRole('button', { name: '添加切分点' }).focus()
    await user.keyboard('{Enter}')
    expect(screen.getByLabelText('第 2 个帧切分点')).toHaveFocus()
    expect(changed.mock.calls.at(-1)![0].frameCuts).toEqual(['899', ''])
    screen.getByRole('button', { name: '删除第 1 个切分点' }).focus()
    await user.keyboard('{Enter}')
    expect(screen.getByLabelText('第 1 个帧切分点')).toHaveValue('')
    expect(screen.queryByRole('navigation', { name: '切分点分页' })).not.toBeInTheDocument()
  })

  it('删除末页最后一项退回有效页，删除全部不会偷偷生成默认切点', async () => {
    const user = userEvent.setup()
    const changed = vi.fn()
    const initial = { ...initialChapterOverlapSettings(), mode: 'exact_frames' as const, frameCuts: Array.from({ length: 21 }, (_, index) => String(index + 1)) }
    render(<Harness initial={initial} changed={changed} />)
    await user.click(screen.getByRole('button', { name: '末页' }))
    await user.click(screen.getByRole('button', { name: '删除第 21 个切分点' }))
    expect(screen.getByLabelText('第 20 个帧切分点')).toHaveFocus()
    expect(screen.queryByRole('navigation', { name: '切分点分页' })).not.toBeInTheDocument()
    expect(changed.mock.calls.at(-1)![0].frameCuts).toHaveLength(20)
  })

  it('空列表可显式添加；disabled阻止编辑和分页但不丢草稿', async () => {
    const user = userEvent.setup()
    const changed = vi.fn()
    const initial = { ...initialChapterOverlapSettings(), mode: 'exact_times' as const, timeCuts: [] }
    const { rerender } = render(<Harness initial={initial} changed={changed} />)
    expect(screen.getByText(/请添加切分点或改为平均 1 章/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: '添加切分点' }))
    expect(screen.getByLabelText('第 1 个时间切分点')).toHaveFocus()
    rerender(<Harness initial={initial} changed={changed} disabled />)
    expect(screen.getByLabelText('第 1 个时间切分点')).toBeDisabled()
    expect(screen.getByLabelText('每段最长时长（分叶）')).toBeDisabled()
    expect(screen.getByRole('button', { name: '添加切分点' })).toBeDisabled()
    expect(changed).toHaveBeenCalledTimes(1)
  })

  it('叶时长和平均章数错误定位常驻控件，不展开无关高级区', async () => {
    const initial = initialChapterOverlapSettings()
    const { rerender } = render(<Harness initial={initial} issue={{ fieldPath: ['settings', 'leaf_max_minutes'], message: '请输入1–60分钟。' }} />)
    expect(screen.getByLabelText('每段最长时长（分叶）')).toHaveFocus()
    rerender(<Harness initial={initial} issue={{ fieldPath: ['settings', 'chapter_selector', 'count'], message: '章数不能大于源帧数。' }} />)
    await waitFor(() => expect(screen.getByLabelText('平均章数')).toHaveFocus())
  })
})

describe('表单转换不成为planner', () => {
  it('保留逆序、重复和时间原文交给Python，不自动修复', () => {
    expect(chapterSettingsIntent({ ...initialChapterOverlapSettings(), mode: 'exact_frames', frameCuts: ['899', '899', '2'] }).chapter_selector)
      .toEqual({ mode: 'exact_frames', frames: [899, 899, 2] })
    expect(chapterSettingsIntent({ ...initialChapterOverlapSettings(), mode: 'exact_times', timeCuts: ['30:00:00', '00:30:00', ' 00:30:00 '] }).chapter_selector)
      .toEqual({ mode: 'exact_times', times: ['30:00:00', '00:30:00', ' 00:30:00 '] })
  })

  it.each(['', '1.5', 'true', '-1', '9007199254740993'])('非法帧整数%s不被宽松转换或丢弃', (value) => {
    expect(() => chapterSettingsIntent({ ...initialChapterOverlapSettings(), mode: 'exact_frames', frameCuts: ['899', value] }))
      .toThrow(ChapterOverlapDraftError)
    try { chapterSettingsIntent({ ...initialChapterOverlapSettings(), mode: 'exact_frames', frameCuts: ['899', value] }) }
    catch (error) { expect((error as ChapterOverlapDraftError).issue.fieldPath).toEqual(['settings', 'chapter_selector', 'frames', 1]) }
  })

  it('时间空行与1000项都拒绝，不悄悄过滤或截断', () => {
    expect(() => chapterSettingsIntent({ ...initialChapterOverlapSettings(), mode: 'exact_times', timeCuts: ['00:30:00', ''] })).toThrow('第 2 个时间切分点')
    expect(() => chapterSettingsIntent({ ...initialChapterOverlapSettings(), mode: 'exact_times', timeCuts: Array.from({ length: 1000 }, () => '00:30:00') })).toThrow('1–999')
    expect(() => chapterSettingsIntent({ ...initialChapterOverlapSettings(), mode: 'exact_frames', frameCuts: [] })).toThrow('1–999')
  })
})
