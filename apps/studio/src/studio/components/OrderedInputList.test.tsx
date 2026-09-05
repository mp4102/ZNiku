/** 有序输入验证鼠标拖动与键盘替代路径使用同一 mutation，迟到拖动不能重排新列表。 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OrderedInputList } from './OrderedInputList'
import { edgeId, reorderEdge } from '../graph'
import type { EdgeWire } from '../contracts'

afterEach(cleanup)
const edges: EdgeWire[] = ['chapter.a', 'chapter.b', 'chapter.c'].map((source_node_id, ordinal) => ({
  source_node_id, source_port_id: 'out', target_node_id: 'merge', target_port_id: 'items', ordinal,
}))
const nodeLabel = (id: string) => `章节 ${id.at(-1)!.toUpperCase()}`
const transfer = () => ({ effectAllowed: '', setData: vi.fn() })

describe('ordered_many 创作者排序', () => {
  it('上下按钮提交一条 reorder，首尾按钮准确禁用', async () => {
    const onReorder = vi.fn()
    render(<OrderedInputList edges={edges} nodeLabel={nodeLabel} onReorder={onReorder} />)
    expect(screen.getByRole('button', { name: '上移 章节 A' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下移 章节 C' })).toBeDisabled()
    const move = screen.getByRole('button', { name: '上移 章节 B' })
    move.focus()
    await userEvent.keyboard('{Enter}')
    expect(onReorder).toHaveBeenCalledExactlyOnceWith(edgeId(edges[1]!), 0)
    expect(screen.getByRole('status')).toHaveTextContent('章节 B 已移到第 1 项')
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
  })

  it('拖动结束才重排；拖动时列表已改变则丢弃旧 intent', () => {
    const onReorder = vi.fn()
    const view = render(<OrderedInputList edges={edges} nodeLabel={nodeLabel} onReorder={onReorder} />)
    let items = screen.getAllByRole('listitem')
    fireEvent.dragStart(items[2]!, { dataTransfer: transfer() })
    fireEvent.dragOver(items[0]!)
    expect(onReorder).not.toHaveBeenCalled()
    fireEvent.drop(items[0]!)
    expect(onReorder).toHaveBeenCalledExactlyOnceWith(edgeId(edges[2]!), 0)
    onReorder.mockClear()
    fireEvent.dragStart(items[1]!, { dataTransfer: transfer() })
    view.rerender(<OrderedInputList edges={edges.slice(1)} nodeLabel={nodeLabel} onReorder={onReorder} />)
    items = screen.getAllByRole('listitem')
    fireEvent.drop(items[0]!)
    expect(onReorder).not.toHaveBeenCalled()
  })

  it('重排后保留按钮焦点，允许连续键盘移动同一条目', async () => {
    const onReorder = vi.fn()
    const view = render(<OrderedInputList edges={edges} nodeLabel={nodeLabel} onReorder={onReorder} />)
    const button = screen.getByRole('button', { name: '上移 章节 C' })
    button.focus()
    await userEvent.keyboard('{Enter}')
    const next = reorderEdge({ nodes: [], edges }, edgeId(edges[2]!), 1)
    view.rerender(<OrderedInputList edges={next.edges} nodeLabel={nodeLabel} onReorder={onReorder} />)
    expect(button).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(onReorder).toHaveBeenLastCalledWith(edgeId(next.edges[2]!), 0)
  })
})
