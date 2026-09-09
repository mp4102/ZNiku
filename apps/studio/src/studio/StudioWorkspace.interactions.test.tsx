/** Batch C 用真实 Workspace 验证键盘意图与草稿边界；gateway 只返回合成内存数据。 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { StudioGatewayError, type StudioGateway } from './gateway'
import type { StudioCommand } from './contracts'
import { studioEnvelope } from './test-fixtures'

afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks() })

function syntheticGateway(saveFailure?: Error) {
  let current = studioEnvelope()
  const command = vi.fn(async (value: StudioCommand) => {
    if (value.operation !== 'save_project') throw new Error('本用例不允许执行或工程切换')
    if (saveFailure) throw saveFailure
    current = { ...current, snapshot: { ...current.snapshot!, project: value.project },
      studio_state: value.studio_state, storage_revision: value.expected_storage_revision + 1 }
    return current
  })
  const gateway: StudioGateway = {
    command, inspect: vi.fn(async () => current),
    listRuns: vi.fn(async () => ({ contract_version: '0.3.0' as const, run_summaries: [], next_run_cursor: null })),
    inspectRun: vi.fn(), inspectReadiness: vi.fn(), inspectLog: vi.fn(), previewAvEnhanceV27: vi.fn(),
  }
  return { gateway, command }
}

describe('Workspace 键盘操作只作用于当前焦点的意图', () => {
  it('菜单与工具按钮的 Delete/Backspace/复制不会改画布选区；回到画布才允许删除', async () => {
    const { gateway, command } = syntheticGateway()
    render(<App gateway={gateway} />)
    await userEvent.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    const node = await screen.findByLabelText('test.transform 节点')
    fireEvent.click(node)
    const selected = () => expect(screen.getByLabelText('test.transform 节点')).toHaveClass('is-selected')
    selected()
    await userEvent.click(screen.getByRole('button', { name: '工程' }))
    screen.getByRole('button', { name: '最近工程' }).focus()
    await userEvent.keyboard('{Delete}{Backspace}{Control>}d{/Control}')
    selected()
    expect(command).not.toHaveBeenCalled()
    await userEvent.keyboard('{Escape}')
    const canvasTool = screen.getByRole('button', { name: '连接节点' })
    canvasTool.focus()
    await userEvent.keyboard('{Delete}{Backspace}{Control>}d{/Control}')
    selected()
    expect(command).not.toHaveBeenCalled()

    screen.getByRole('region', { name: 'Studio Designer 画布' }).focus()
    await userEvent.keyboard('{Delete}')
    await waitFor(() => expect(screen.queryByLabelText('test.transform 节点')).not.toBeInTheDocument())
    await waitFor(() => expect(command).toHaveBeenCalledTimes(1))
    expect(command.mock.calls[0]![0]).toMatchObject({ operation: 'save_project',
      project: { graph: { nodes: [{ node_id: 'source' }, { node_id: 'sink' }], edges: [] } } })

    // 全局 Undo/保存的既有含义不因选择快捷键限域而改变。
    await userEvent.click(screen.getByRole('button', { name: '工程' }))
    screen.getByRole('button', { name: '最近工程' }).focus()
    await userEvent.keyboard('{Control>}z{/Control}')
    expect(await screen.findByLabelText('test.transform 节点')).toBeInTheDocument()
    await userEvent.keyboard('{Control>}s{/Control}')
    await waitFor(() => expect(command).toHaveBeenCalledTimes(2))
    expect(command.mock.calls[1]![0]).toMatchObject({ operation: 'save_project', project: studioEnvelope().snapshot!.project })
  })

  it('输入框编辑不被图快捷键拦截，未应用参数及图保持原样', async () => {
    const { gateway, command } = syntheticGateway()
    render(<App gateway={gateway} />)
    await userEvent.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    fireEvent.click(await screen.findByLabelText('test.transform 节点'))
    const field = screen.getByRole('textbox', { name: 'model_name' })
    await userEvent.clear(field)
    await userEvent.type(field, '待保留草稿')
    await userEvent.keyboard('{Backspace}')
    expect(field).toHaveValue('待保留草')
    expect(screen.getByText('参数未应用')).toBeVisible()
    expect(screen.getByLabelText('test.transform 节点')).toHaveClass('is-selected')
    expect(command).not.toHaveBeenCalled()
  })

  it('保存冲突后取消重新载入会保留本地图和离开保护，不调用打开或自动覆盖', async () => {
    const { gateway, command } = syntheticGateway(new StudioGatewayError('合成保存冲突，请重新载入。', { code: 'E_PROJECT_STORAGE_CONFLICT' }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<App gateway={gateway} />)
    await userEvent.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    fireEvent.click(await screen.findByLabelText('test.transform 节点'))
    await userEvent.click(screen.getByText('显示设置', { selector: 'summary' }))
    const alias = screen.getByLabelText('节点别名')
    await userEvent.type(alias, '待保留名称')
    fireEvent.blur(alias)
    const reload = await screen.findByRole('button', { name: '重新载入磁盘版本' })
    await userEvent.click(reload)
    expect(confirm).toHaveBeenCalledOnce()
    expect(screen.getByLabelText('节点别名')).toHaveValue('待保留名称')
    expect(screen.getByRole('heading', { name: '待保留名称' })).toBeVisible()
    const leaving = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(leaving)
    expect(leaving.defaultPrevented).toBe(true)
    expect(command).toHaveBeenCalledTimes(1)
    expect(command.mock.calls[0]![0].operation).toBe('save_project')
  })
})
