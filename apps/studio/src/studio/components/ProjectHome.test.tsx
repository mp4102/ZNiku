import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ProjectHome, type ProjectHomeProps } from './ProjectHome'

afterEach(cleanup)

function props(overrides: Partial<ProjectHomeProps> = {}): ProjectHomeProps {
  return {
    open: true,
    loading: false,
    serviceUnavailable: false,
    serviceMessage: null,
    hostBridgeAvailable: true,
    busy: false,
    hasOpenProject: false,
    recentProjects: [],
    onClose: vi.fn(),
    onCreateGuided: vi.fn(),
    onCreateBlank: vi.fn(async () => undefined),
    onOpenExisting: vi.fn(async () => undefined),
    onOpenRecent: vi.fn(async () => undefined),
    onRetryService: vi.fn(),
    ...overrides,
  }
}

describe('ProjectHome', () => {
  it('只以创作者任务展示建项入口，不展示 Project ID 或绝对路径输入', () => {
    render(<ProjectHome {...props()} />)
    expect(screen.getByRole('button', { name: /新建视频工程/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /打开已有工程/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /空白工作流/ })).toBeVisible()
    expect(screen.queryByLabelText('Project ID')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('工程路径')).not.toBeInTheDocument()
  })

  it('服务离线时说明未修改媒体，并提供显式恢复动作', async () => {
    const user = userEvent.setup()
    const onRetryService = vi.fn()
    render(<ProjectHome {...props({ serviceUnavailable: true, serviceMessage: 'offline', onRetryService })} />)
    expect(screen.getByRole('alert')).toHaveTextContent('offline')
    await user.click(screen.getByRole('button', { name: '重新连接' }))
    expect(onRetryService).toHaveBeenCalledTimes(1)
  })

  it('工程服务在线但桌面选择器离线时仍提供恢复动作', async () => {
    const user = userEvent.setup()
    const onRetryService = vi.fn()
    render(<ProjectHome {...props({ hostBridgeAvailable: false, onRetryService })} />)
    expect(screen.getByText(/桌面文件选择器暂时不可用/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: '重试桌面连接' }))
    expect(onRetryService).toHaveBeenCalledTimes(1)
  })

  it('空白工程只收集名称，选择保存位置由上层 HostBridge 完成', async () => {
    const user = userEvent.setup()
    const onCreateBlank = vi.fn(async () => undefined)
    render(<ProjectHome {...props({ onCreateBlank })} />)
    await user.click(screen.getByRole('button', { name: /空白工作流/ }))
    await user.clear(screen.getByLabelText('空白工程名称'))
    await user.type(screen.getByLabelText('空白工程名称'), '访谈修复')
    await user.click(screen.getByRole('button', { name: '选择保存位置' }))
    expect(onCreateBlank).toHaveBeenCalledWith('访谈修复')
  })

  it('最近工程显示人类名称并以 path 作为内部打开绑定', async () => {
    const user = userEvent.setup()
    const onOpenRecent = vi.fn(async () => undefined)
    render(<ProjectHome {...props({
      recentProjects: [{ path: 'D:\\Work\\movie.zniku', name: '电影修复', opened_at: '2026-09-04T02:00:00Z' }],
      onOpenRecent,
    })} />)
    expect(screen.getByText('电影修复')).toBeVisible()
    expect(screen.getByText('movie.zniku')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /电影修复/ }))
    expect(onOpenRecent).toHaveBeenCalledWith('D:\\Work\\movie.zniku')
  })

  it('已有工程时把焦点限制在首页，Escape 关闭后恢复触发点焦点', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const closed = props({ open: false, hasOpenProject: true, onClose })
    const { rerender } = render(
      <><button type="button">打开首页</button><ProjectHome {...closed} /></>,
    )
    const trigger = screen.getByRole('button', { name: '打开首页' })
    trigger.focus()

    rerender(<><button type="button">打开首页</button><ProjectHome {...closed} open /></>)
    const close = screen.getByRole('button', { name: '关闭工程首页' })
    expect(close).toHaveFocus()

    const last = screen.getByRole('button', { name: /空白工作流/ })
    last.focus()
    await user.tab()
    expect(close).toHaveFocus()
    await user.tab({ shift: true })
    expect(last).toHaveFocus()

    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
    rerender(<><button type="button">打开首页</button><ProjectHome {...closed} /></>)
    expect(trigger).toHaveFocus()
  })

  it('尚无工程时 Escape 不会绕过必须完成的建项入口', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<ProjectHome {...props({ onClose })} />)
    expect(screen.getByRole('button', { name: /新建视频工程/ })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('初始加载结束后把焦点从 dialog 容器移到首个可用创作者动作', () => {
    const initial = props({ loading: true })
    const { rerender } = render(<ProjectHome {...initial} />)
    expect(screen.getByRole('dialog', { name: 'ZNIKU Studio 工程首页' })).toHaveFocus()
    rerender(<ProjectHome {...initial} loading={false} />)
    expect(screen.getByRole('button', { name: /新建视频工程/ })).toHaveFocus()
  })
})
