/**
 * 展示节点别名、折叠与分组设置，只向 Workspace 发出同一 StudioState 的编辑意图。
 * 新分组名称是未提交的组件输入，不保存 Graph、参数或另一份工程状态。
 */

import { useState } from 'react'
import type { StudioStateWire } from '../contracts'

export interface AuthoringViewPanelProps {
  readonly studioState: StudioStateWire
  readonly selectedNode: { readonly node_id: string; readonly type_id: string } | null
  readonly selectedNodeTitle?: string
  readonly selectedNodeIds: ReadonlySet<string>
  readonly disabled: boolean
  readonly onEditStudioState: (label: string, updater: (state: StudioStateWire) => StudioStateWire) => void
  readonly onUpdateNodeView: (nodeId: string, patch: Partial<StudioStateWire['node_views'][number]>) => void
}

const colors = [
  ['neutral', '灰色'], ['blue', '蓝色'], ['green', '绿色'],
  ['amber', '金色'], ['purple', '紫色'], ['rose', '玫红'],
] as const

export function AuthoringViewPanel({
  studioState, selectedNode, selectedNodeTitle, selectedNodeIds, disabled,
  onEditStudioState, onUpdateNodeView,
}: AuthoringViewPanelProps) {
  const [newGroupTitle, setNewGroupTitle] = useState('')
  const selectedView = studioState.node_views.find((view) => view.node_id === selectedNode?.node_id)

  const createGroup = () => {
    const groupId = crypto.randomUUID()
    onEditStudioState('创建展示分组', (state) => ({
      ...state,
      groups: [...state.groups, {
        group_id: groupId, title: newGroupTitle.trim(), color_token: 'blue', collapsed: false,
      }],
      node_views: [
        ...state.node_views.filter((view) => !selectedNodeIds.has(view.node_id)),
        ...[...selectedNodeIds].map((nodeId) => ({
          node_id: nodeId, display_name: null, collapsed: false,
          ...state.node_views.find((view) => view.node_id === nodeId), group_id: groupId,
        })),
      ],
    }))
    setNewGroupTitle('')
  }

  return (
    <section className="authoring-view-panel" aria-label="画布展示设置">
      {selectedNode && <fieldset disabled={disabled}>
        <legend>节点展示</legend>
        <label>节点别名<input
          aria-label="节点别名" key={`${selectedNode.node_id}/${selectedView?.display_name ?? ''}`}
          defaultValue={selectedView?.display_name ?? ''} maxLength={200}
          placeholder={selectedNodeTitle ?? selectedNode.type_id}
          onBlur={(event) => onUpdateNodeView(selectedNode.node_id, { display_name: event.target.value.trim() || null })}
          onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }}
        /></label>
        <label><input
          type="checkbox" checked={selectedView?.collapsed ?? false}
          onChange={(event) => onUpdateNodeView(selectedNode.node_id, { collapsed: event.target.checked })}
        />折叠节点摘要</label>
        <label>所属分组<select
          aria-label="所属分组" value={selectedView?.group_id ?? ''}
          onChange={(event) => onUpdateNodeView(selectedNode.node_id, { group_id: event.target.value || null })}
        >
          <option value="">不分组</option>
          {studioState.groups.map((group) => <option key={group.group_id} value={group.group_id}>{group.title}</option>)}
        </select></label>
      </fieldset>}
      <details>
        <summary>章节 / Leaf 展示分组</summary>
        <p>分组只整理画布，不决定执行范围或处理顺序。</p>
        <input
          aria-label="新分组名称" maxLength={200} value={newGroupTitle}
          onChange={(event) => setNewGroupTitle(event.target.value)} placeholder="例如：第一章 / Leaf A"
        />
        <button type="button" disabled={!newGroupTitle.trim() || disabled} onClick={createGroup}>将所选节点分组</button>
        {studioState.groups.map((group) => <div className="studio-group-editor" key={group.group_id}>
          <input
            aria-label={`分组名称 ${group.title}`} key={group.title} defaultValue={group.title} maxLength={200}
            onBlur={(event) => {
              const title = event.target.value.trim()
              if (title) onEditStudioState('重命名分组', (state) => ({
                ...state, groups: state.groups.map((item) => item.group_id === group.group_id ? { ...item, title } : item),
              }))
            }}
          />
          <select
            aria-label={`分组颜色 ${group.title}`} value={group.color_token}
            onChange={(event) => onEditStudioState('分组颜色', (state) => ({
              ...state, groups: state.groups.map((item) => item.group_id === group.group_id
                ? { ...item, color_token: event.target.value as typeof item.color_token } : item),
            }))}
          >
            {colors.map(([token, label]) => <option key={token} value={token}>{label}</option>)}
          </select>
          <button type="button" onClick={() => onEditStudioState('折叠分组', (state) => ({
            ...state, groups: state.groups.map((item) => item.group_id === group.group_id ? { ...item, collapsed: !item.collapsed } : item),
          }))}>{group.collapsed ? '展开分组' : '折叠分组'}</button>
          <button type="button" onClick={() => onEditStudioState('解除分组', (state) => ({
            ...state, groups: state.groups.filter((item) => item.group_id !== group.group_id),
            node_views: state.node_views.map((view) => view.group_id === group.group_id ? { ...view, group_id: null } : view),
          }))}>解除分组</button>
        </div>)}
      </details>
    </section>
  )
}
