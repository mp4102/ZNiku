/** 顶栏重组不改变操作资格，也不建立第二个参数、保存或运行状态所有者。 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DesktopExit } from './DesktopExit'
import { ProjectShell, type ProjectShellProps } from './ProjectShell'
import type { HostBridge } from '../host-bridge'
import { version } from '../../../package.json'

afterEach(() => { cleanup(); delete window.__ZNIKU_DESKTOP__ })

function props(overrides: Partial<ProjectShellProps> = {}): ProjectShellProps {
  return {
    projectName: '合成剪辑工程', projectId: 'synthetic-project', nodeCount: 3,
    dirty: false, profile: null, projectPath: 'D:\\synthetic\\example.zniku',
    projectIdDraft: 'synthetic-project', projectNameDraft: '合成剪辑工程',
    serviceBusy: false, statusStale: false, canSave: true,
    hostBridgeAvailable: true, canResumeGuided: true,
    canUndo: true, canRedo: true, runCenter: <button type="button">本次主操作</button>,
    onUndo: vi.fn(), onRedo: vi.fn(), onToggleAdvanced: vi.fn(), onOpenHistory: vi.fn(),
    onToggleLibrary: vi.fn(), onOpenDiagnostics: vi.fn(), onReloadProject: vi.fn(), onOpenStorage: vi.fn(),
    onProjectPathChange: vi.fn(), onOpenTemplates: vi.fn(), onHome: vi.fn(),
    onOpenWithPicker: vi.fn(), onOpenProject: vi.fn(), onCreateProject: vi.fn(), onSaveProject: vi.fn(),
    ...overrides,
  }
}

describe('ProjectShell 紧凑菜单', () => {
  it('版本只在展开工程菜单时展示，读取当前构建版本并明确不是服务健康证明', async () => {
    render(<ProjectShell {...props()} />)
    expect(screen.getByLabelText('前端构建版本')).not.toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: '工程' }))
    const versionLabel = screen.getByLabelText('前端构建版本')
    expect(versionLabel).toBeVisible()
    expect(versionLabel).toHaveTextContent(`ZNIKU Studio · v${version} 验收候选`)
    expect(versionLabel).toHaveTextContent('不代表工程格式或服务连接状态')
  })

  it('菜单方向键略过折叠开发入口中的按钮，进入可用的 summary', async () => {
    render(<ProjectShell {...props({ serviceBusy: true, projectSwitchBlocked: false, hostBridgeAvailable: false })} />)
    const trigger = screen.getByRole('button', { name: '工程' })
    await userEvent.click(trigger)
    await userEvent.keyboard('{ArrowDown}')
    // 普通动作因 busy 禁用；不能跳进尚未展开的按路径按钮。
    expect(screen.getByText('开发入口', { selector: 'summary' })).toHaveFocus()
  })

  it('默认只显示顶层导航、可信保存状态和一个原主操作，参数草稿另行提示', () => {
    render(<ProjectShell {...props({ parameterDirty: true })} />)
    expect(screen.getByRole('img', { name: 'ZNIKU Studio' })).toBeVisible()
    expect(screen.getByRole('status', { name: '工程保存状态' })).toHaveTextContent('3 个节点 · 已保存')
    expect(screen.getByText('参数未应用')).toBeVisible()
    for (const name of ['工程', '视图', '撤销', '重做', '处理记录', '本次主操作']) expect(screen.getByRole('button', { name })).toBeVisible()
    for (const name of ['打开工程', '保存', '工程数据', '继续处理向导', '高级节点图']) expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '本次主操作' })).toHaveLength(1)
    expect(screen.getByLabelText('工程路径')).not.toBeVisible()
  })

  it.each([
    [{ dirty: true }, '有未保存更改'],
    [{ saving: true, dirty: true }, '正在保存…'],
    [{ saveError: 'CAS 冲突', saving: true }, '保存未完成'],
    [{ draftBlocked: true }, '已保存，但暂不可运行'],
    [{ statusStale: true }, '连接待恢复'],
  ] satisfies [Partial<ProjectShellProps>, string][])('保存投影保留可信原状态 %j', (state, text) => {
    render(<ProjectShell {...props(state)} />)
    expect(screen.getByRole('status', { name: '工程保存状态' })).toHaveTextContent(text)
  })

  it('错误不藏进菜单，重试与冲突恢复仍调用原保存操作', async () => {
    const user = userEvent.setup()
    const original = props({ saveError: '保存冲突：磁盘工程已更新' })
    render(<ProjectShell {...original} />)
    expect(screen.getByRole('alert')).toHaveTextContent('保存冲突：磁盘工程已更新')
    await user.click(screen.getByRole('button', { name: '重试保存' }))
    await user.click(screen.getByRole('button', { name: '重新载入磁盘版本' }))
    expect(original.onSaveProject).toHaveBeenCalledTimes(1)
    expect(original.onReloadProject).toHaveBeenCalledTimes(1)
  })

  it('新建、近期与首页只导航，不暗中执行创建工程或丢弃设置', async () => {
    const user = userEvent.setup()
    const original = props({ parameterDirty: true })
    render(<ProjectShell {...original} />)
    for (const name of ['新建工程…', '最近工程', '工程首页']) {
      await user.click(screen.getByRole('button', { name: '工程' }))
      await user.click(screen.getByRole('button', { name }))
      expect(screen.getByRole('button', { name: '工程' })).toHaveAttribute('aria-expanded', 'false')
    }
    expect(original.onHome).toHaveBeenCalledTimes(3)
    expect(original.onCreateProject).not.toHaveBeenCalled()
    expect(original.onSaveProject).not.toHaveBeenCalled()
    expect(screen.getByText('参数未应用')).toBeVisible()
  })

  it.each([
    ['打开工程', 'onOpenWithPicker'], ['保存', 'onSaveProject'], ['工程数据', 'onOpenStorage'], ['继续处理向导', 'onOpenTemplates'],
  ] as const)('工程菜单 %s 只触发一次原回调并恢复触发按钮焦点', async (name, callback) => {
    const user = userEvent.setup()
    const original = props()
    render(<ProjectShell {...original} />)
    const trigger = screen.getByRole('button', { name: '工程' })
    await user.click(trigger)
    await user.click(screen.getByRole('button', { name }))
    expect(original[callback]).toHaveBeenCalledTimes(1)
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it.each([
    [{ serviceBusy: true }, ['打开工程', '保存', '工程数据', '继续处理向导', '工程首页', '新建工程…', '最近工程', '打开', '新建']],
    [{ statusStale: true }, ['打开工程', '工程数据', '继续处理向导', '打开', '新建']],
    [{ projectSwitchBlocked: true }, ['打开工程', '打开', '新建']],
    [{ hostBridgeAvailable: false }, ['打开工程']],
    [{ canSave: false }, ['保存']],
    [{ projectId: null }, ['工程数据', '继续处理向导']],
    [{ canResumeGuided: false }, ['处理向导']],
    [{ projectPath: '  ' }, ['打开', '新建']],
    [{ projectNameDraft: ' ' }, ['新建']],
  ] satisfies [Partial<ProjectShellProps>, string[]][])('菜单搬迁保留原禁用资格 %j', async (state, names) => {
    const user = userEvent.setup()
    render(<ProjectShell {...props(state)} />)
    await user.click(screen.getByRole('button', { name: '工程' }))
    await user.click(screen.getByText('开发入口'))
    for (const name of names) expect(screen.getByRole('button', { name })).toBeDisabled()
  })

  it('服务忙碌不扩大高级视图与历史导航资格，Undo/Redo 仍被原条件禁用', async () => {
    const user = userEvent.setup()
    const original = props({ serviceBusy: true })
    render(<ProjectShell {...original} />)
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重做' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '处理记录' }))
    expect(original.onOpenHistory).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: '视图' }))
    await user.click(screen.getByRole('button', { name: '高级节点图' }))
    expect(original.onToggleAdvanced).toHaveBeenCalledTimes(1)
    expect(original.onOpenDiagnostics).not.toHaveBeenCalled()
  })

  it('密度、节点库与诊断明确分开，只调用各自的原动作', async () => {
    const user = userEvent.setup()
    const original = props({ advanced: true, libraryOpen: false })
    render(<ProjectShell {...original} />)
    for (const [name, callback] of [['返回创作者模式', 'onToggleAdvanced'], ['展开节点库', 'onToggleLibrary'], ['高级诊断', 'onOpenDiagnostics']] as const) {
      await user.click(screen.getByRole('button', { name: '视图' }))
      await user.click(screen.getByRole('button', { name }))
      expect(original[callback]).toHaveBeenCalledTimes(1)
    }
    expect(original.onSaveProject).not.toHaveBeenCalled()
  })

  it('开发入口需要两次明确展开，保持原输入与动作标签可访问', async () => {
    const user = userEvent.setup()
    const original = props()
    render(<ProjectShell {...original} />)
    await user.click(screen.getByRole('button', { name: '工程' }))
    expect(screen.getByLabelText('工程路径')).not.toBeVisible()
    await user.click(screen.getByText('开发入口'))
    await user.type(screen.getByLabelText('工程路径'), 'x')
    expect(original.onProjectPathChange).toHaveBeenCalled()
    expect(screen.getByLabelText('开发入口工程名称')).toHaveAttribute('readonly')
    expect(screen.getByLabelText('开发入口 Project ID')).toHaveAttribute('readonly')
    await user.click(screen.getByRole('button', { name: '打开' }))
    expect(original.onOpenProject).toHaveBeenCalledTimes(1)
  })

  it('Escape 只关闭当前菜单并返回焦点，不把输入按键传给画布', async () => {
    const user = userEvent.setup()
    const globalKey = vi.fn()
    render(<div onKeyDown={globalKey}><ProjectShell {...props()} /></div>)
    const trigger = screen.getByRole('button', { name: '工程' })
    await user.click(trigger)
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('button', { name: '新建工程…' })).toHaveFocus()
    await user.click(screen.getByText('开发入口'))
    await user.click(screen.getByLabelText('工程路径'))
    globalKey.mockClear()
    await user.keyboard('{Escape}')
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(globalKey).not.toHaveBeenCalled()
  })

  it('外部空白关闭并恢复焦点，外部输入和顶栏操作继续得到原点击', async () => {
    const user = userEvent.setup()
    render(<><ProjectShell {...props()} /><div data-testid="outside">画布空白</div><input aria-label="外部参数" /></>)
    const trigger = screen.getByRole('button', { name: '工程' })
    await user.click(trigger)
    await user.click(screen.getByRole('button', { name: '保存' }).parentElement!)
    fireEvent.pointerDown(screen.getByTestId('outside'))
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    await user.click(trigger)
    await user.click(screen.getByLabelText('外部参数'))
    expect(screen.getByLabelText('外部参数')).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    await user.click(trigger)
    await user.click(screen.getByRole('button', { name: '撤销' }))
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('两个菜单互斥，普通 Tab 导航不被截获，也不把高级密度当作诊断开关', async () => {
    const user = userEvent.setup()
    render(<ProjectShell {...props()} />)
    await user.click(screen.getByRole('button', { name: '工程' }))
    await user.click(screen.getByRole('button', { name: '视图' }))
    expect(screen.getByRole('button', { name: '工程' })).toHaveAttribute('aria-expanded', 'false')
    await user.tab()
    expect(screen.getByRole('button', { name: '高级节点图' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: '视图' })).toHaveFocus()
  })

  it('退出沿用原确认组件：取消不碰服务，确认才关闭；菜单不吞掉确认框', async () => {
    const user = userEvent.setup()
    window.__ZNIKU_DESKTOP__ = { contractVersion: '0.3.0', instanceId: 'synthetic-instance' }
    const bridge: HostBridge = { configured: true, inspectCapabilities: vi.fn(), pick: vi.fn(), launch: vi.fn(),
      inspectDesktop: vi.fn(async () => ({ contract_version: '0.3.0' as const, instance_id: 'synthetic-instance', busy: false, closing: false })),
      closeDesktop: vi.fn(async () => undefined) }
    render(<><ProjectShell {...props({ desktopControls: <DesktopExit hostBridge={bridge} unsaved={false} /> })} /><div data-testid="outside">外部</div></>)
    await user.click(screen.getByRole('button', { name: '工程' }))
    await user.click(screen.getByRole('button', { name: '退出应用' }))
    expect(screen.getByRole('dialog', { name: '退出 ZNIKU Studio' })).toBeVisible()
    fireEvent.pointerDown(screen.getByTestId('outside'))
    expect(screen.getByRole('dialog', { name: '退出 ZNIKU Studio' })).toBeVisible()
    await user.keyboard('{Escape}')
    expect(bridge.inspectDesktop).not.toHaveBeenCalled()
    expect(bridge.closeDesktop).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '退出应用' })).toHaveFocus()
    await user.click(screen.getByRole('button', { name: '退出应用' }))
    await user.click(screen.getByRole('button', { name: '确认退出应用' }))
    await waitFor(() => expect(bridge.closeDesktop).toHaveBeenCalledExactlyOnceWith('synthetic-instance'))
    expect(screen.getByRole('dialog', { name: '应用已退出' })).toBeVisible()
  })
})
