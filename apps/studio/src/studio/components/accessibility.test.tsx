/** 自动检查结构/名称/错误关联与键盘焦点；像素对比度、缩放和实际布局另由 production 浏览器门禁覆盖。 */

import { useState } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CanvasDialog } from './ConnectNodesDialog'
import { ProjectHome } from './ProjectHome'
import { NodePalette } from './NodePalette'
import { SchemaParameterForm } from '../SchemaParameterForm'
import { validateParameterDraft, type ParameterSchema } from '../parameter-draft'
import { groupStudioDefinitions } from '../catalog'
import { projectSnapshot } from '../test-fixtures'
import { dialogFocusTargets } from './focus-management'

afterEach(cleanup)

async function expectAccessible(container: HTMLElement) {
  const report = await axe.run(container, {
    // jsdom 没有实际像素/布局；只禁用这一条，并在 Playwright production 扫描中启用它。
    rules: { 'color-contrast': { enabled: false } },
  })
  expect(report.violations.filter((item) => item.impact === 'critical' || item.impact === 'serious')
    .map((item) => ({ id: item.id, targets: item.nodes.map((node) => node.target) }))).toEqual([])
}

const schema: ParameterSchema = {
  type: 'object', additionalProperties: false,
  properties: {
    path: { type: 'string', minLength: 1, description: '选择要处理的视频。' },
    strength: { type: 'integer', minimum: 1, maximum: 9 },
    enabled: { type: 'boolean' },
    mode: { type: 'string', enum: ['a', 'b'] },
  },
  required: ['path', 'strength'],
}

describe('创作者自动无障碍门禁', () => {
  it('首页及取消建项保持有名称的键盘路径，扫描无严重问题', async () => {
    const { container } = render(<ProjectHome open loading={false} serviceUnavailable={false}
      serviceMessage={null} hostBridgeAvailable busy={false} hasOpenProject recentProjects={[]}
      onClose={vi.fn()} onCreateGuided={vi.fn()} onCreateBlank={vi.fn()} onOpenExisting={vi.fn()}
      onOpenRecent={vi.fn()} onRetryService={vi.fn()} />)
    await expectAccessible(container)
    await userEvent.click(screen.getByRole('button', { name: /空白工作流/ }))
    expect(screen.getByRole('textbox', { name: '空白工程名称' })).toHaveFocus()
    await expectAccessible(container)
    await userEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.getByRole('button', { name: /空白工作流/ })).toHaveFocus()
  })

  it('参数错误与对应控件严格关联，没有缺失 id 或只显示红色的错误', async () => {
    const draft = { path: '', strength: 20, enabled: false, mode: 'a' }
    const { container, rerender } = render(<SchemaParameterForm schema={schema} draft={draft}
      validation={validateParameterDraft(schema, draft)} presentation={null} onChange={vi.fn()} />)
    const field = screen.getByRole('spinbutton', { name: 'strength（必填）' })
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(field).toHaveAttribute('aria-required', 'true')
    expect(field).toHaveAccessibleDescription(/9/)
    for (const element of container.querySelectorAll('[aria-describedby]')) {
      for (const id of element.getAttribute('aria-describedby')!.split(' ')) expect(document.getElementById(id)).not.toBeNull()
    }
    await expectAccessible(container)
    const valid = { ...draft, path: 'synthetic.mkv', strength: 3 }
    rerender(<SchemaParameterForm schema={schema} draft={valid} validation={validateParameterDraft(schema, valid)} presentation={null} onChange={vi.fn()} />)
    expect(field).toHaveAttribute('aria-invalid', 'false')
  })

  it('节点面板可键盘选用且默认不显示精确版本术语', async () => {
    const { container } = render(<NodePalette definitionCount={projectSnapshot.definitions.length}
      projectId="synthetic" projectName="合成工程" query="" groups={groupStudioDefinitions(projectSnapshot.definitions, '')}
      canEditGraph busy={false} selectedNodeCount={0} selectedEdgeCount={0}
      onProjectNameChange={vi.fn()} onQueryChange={vi.fn()} onAddDefinition={vi.fn()}
      onCopySelection={vi.fn()} onDeleteSelection={vi.fn()} />)
    expect(screen.queryByText(/exact versions/)).not.toBeInTheDocument()
    await expectAccessible(container)
  })

  it('模态焦点遍历包含 textarea/summary，排除折叠及禁用项，并恢复原按钮', async () => {
    function Example() {
      const [open, setOpen] = useState(false)
      return <><button onClick={() => setOpen(true)}>打开测试窗口</button>{open && <CanvasDialog title="键盘测试" onClose={() => setOpen(false)}>
        <details><summary>高级说明</summary><input aria-label="折叠输入" /></details>
        <button disabled>不可用操作</button><div hidden><button>隐藏操作</button></div>
        <textarea aria-label="备注" />
      </CanvasDialog>}</>
    }
    const { container } = render(<Example />)
    const trigger = screen.getByRole('button', { name: '打开测试窗口' })
    await userEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: '键盘测试' })
    expect(dialogFocusTargets(dialog).map((element) => element.getAttribute('aria-label') ?? element.textContent)).toEqual(['关闭键盘测试', '高级说明', '备注'])
    const close = screen.getByRole('button', { name: '关闭键盘测试' })
    const textarea = screen.getByRole('textbox', { name: '备注' })
    expect(close).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(textarea).toHaveFocus()
    await userEvent.tab()
    expect(close).toHaveFocus()
    await expectAccessible(container)
    await userEvent.keyboard('{Escape}')
    expect(trigger).toHaveFocus()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
