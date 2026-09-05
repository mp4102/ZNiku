import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { StudioStateWire } from '../contracts'
import { AuthoringViewPanel, type AuthoringViewPanelProps } from './AuthoringViewPanel'

afterEach(cleanup)

const state: StudioStateWire = {
  contract_version: '0.3.0', viewport: { x: 20, y: 30, zoom: 1 },
  groups: [{ group_id: 'chapter-a', title: '章节 A', color_token: 'blue', collapsed: false }],
  node_views: [
    { node_id: 'source', display_name: '原素材', group_id: 'chapter-a', collapsed: true },
    { node_id: 'sink', display_name: '成片', group_id: null, collapsed: false },
  ],
}

function props(overrides: Partial<AuthoringViewPanelProps> = {}): AuthoringViewPanelProps {
  return {
    studioState: state, selectedNode: { node_id: 'source', type_id: 'test.source' },
    selectedNodeTitle: '导入视频', selectedNodeIds: new Set(['source']), disabled: false,
    onEditStudioState: vi.fn(), onUpdateNodeView: vi.fn(), ...overrides,
  }
}

describe('AuthoringViewPanel', () => {
  it('新分组名称只在点击提交后成为一次展示编辑，保留已有别名和折叠', () => {
    const onEdit = vi.fn<AuthoringViewPanelProps['onEditStudioState']>()
    render(<AuthoringViewPanel {...props({ onEditStudioState: onEdit, selectedNodeIds: new Set(['source', 'new-node']) })} />)
    fireEvent.click(screen.getByText('章节 / Leaf 展示分组'))
    fireEvent.change(screen.getByLabelText('新分组名称'), { target: { value: '  第二章  ' } })
    expect(onEdit).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '将所选节点分组' }))
    expect(onEdit).toHaveBeenCalledTimes(1)
    expect(onEdit.mock.calls[0][0]).toBe('创建展示分组')
    const next = onEdit.mock.calls[0][1](state)
    const created = next.groups[1]
    expect(created).toMatchObject({ title: '第二章', color_token: 'blue', collapsed: false })
    expect(next.viewport).toEqual(state.viewport)
    expect(next.node_views).toEqual([
      state.node_views[1],
      { ...state.node_views[0], group_id: created.group_id },
      { node_id: 'new-node', display_name: null, collapsed: false, group_id: created.group_id },
    ])
    expect(screen.getByLabelText('新分组名称')).toHaveValue('')
  })

  it('节点别名在失焦时发意图，解除分组只清成员关联', () => {
    const onEdit = vi.fn<AuthoringViewPanelProps['onEditStudioState']>()
    const onUpdate = vi.fn<AuthoringViewPanelProps['onUpdateNodeView']>()
    render(<AuthoringViewPanel {...props({ onEditStudioState: onEdit, onUpdateNodeView: onUpdate })} />)
    const alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '  剪辑素材  ' } })
    expect(onUpdate).not.toHaveBeenCalled()
    fireEvent.blur(alias)
    expect(onUpdate).toHaveBeenCalledExactlyOnceWith('source', { display_name: '剪辑素材' })
    fireEvent.click(screen.getByText('章节 / Leaf 展示分组'))
    fireEvent.click(screen.getByRole('button', { name: '解除分组' }))
    const next = onEdit.mock.calls[0][1](state)
    expect(next.groups).toEqual([])
    expect(next.node_views).toEqual([{ ...state.node_views[0], group_id: null }, state.node_views[1]])
  })

  it('忙碌或未应用参数时禁止提交新分组，工程会话重挂载清空未提交名称', () => {
    const onEdit = vi.fn<AuthoringViewPanelProps['onEditStudioState']>()
    const { rerender } = render(<AuthoringViewPanel key="first" {...props({ disabled: true, onEditStudioState: onEdit })} />)
    expect(screen.getByLabelText('节点别名')).toBeDisabled()
    fireEvent.click(screen.getByText('章节 / Leaf 展示分组'))
    fireEvent.change(screen.getByLabelText('新分组名称'), { target: { value: '未提交章节' } })
    expect(screen.getByRole('button', { name: '将所选节点分组' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '将所选节点分组' }))
    expect(onEdit).not.toHaveBeenCalled()
    rerender(<AuthoringViewPanel key="second" {...props({ onEditStudioState: onEdit })} />)
    expect(screen.getByLabelText('新分组名称')).toHaveValue('')
  })
})
