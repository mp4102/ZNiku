import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import example from './__fixtures__/overlap-preview.json'
import { parseOverlapFullEnvelope } from './chapter-overlap-contracts'
import { ChapterOverlapPreview } from './ChapterOverlapPreview'

afterEach(cleanup)
it('显示 Python 精确边界和 raw/cropped 数量，不把数学成功说成模型验收', () => {
  const preview = parseOverlapFullEnvelope(example)
  render(<ChapterOverlapPreview preview={preview} />)
  expect(screen.getByText(/ZNIKU 重叠 FI 候选/)).toBeVisible()
  expect(screen.getByText(/全片最后补 1 帧/)).toHaveTextContent('3602 帧')
  expect(screen.getAllByText(/外部原始结果要求/)).toHaveLength(3)
  expect(screen.getByText(/没有估算倒计时/)).toBeVisible()
})
it('1000 章视觉容量仅一页 20 章，页切换不删减服务器投影', () => {
  const base = parseOverlapFullEnvelope(example)
  // 此处只压测渲染；媒体数学 fixture 仍来自 Python，不伪装这些重复展示行可执行。
  const chapters = Array.from({ length: 1000 }, (_, index) => ({ ...base.plan.chapters[0]!, chapter_id: `display-${index}`, label: `展示 ${index + 1}` }))
  const value = { ...base, plan: { ...base.plan, chapters, chapter_count: 1000 } }
  render(<ChapterOverlapPreview preview={value} />)
  expect(screen.getAllByText(/章 · .*个处理段/)).toHaveLength(21)
  expect(screen.queryByText(/展示 1000 章/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '最后一页' }))
  expect(screen.getByText(/展示 1000 章/)).toBeVisible()
  expect(chapters).toHaveLength(1000)
})
