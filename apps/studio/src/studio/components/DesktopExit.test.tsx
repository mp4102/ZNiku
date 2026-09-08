import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DesktopExit } from './DesktopExit'
import type { HostBridge } from '../host-bridge'

afterEach(() => { cleanup(); delete window.__ZNIKU_DESKTOP__ })
function setup(unsaved = false, busy = false) {
  window.__ZNIKU_DESKTOP__ = { contractVersion: '0.3.0', instanceId: 'instance' }
  const host: HostBridge = { configured: true, inspectCapabilities: vi.fn(), pick: vi.fn(), launch: vi.fn(),
    inspectDesktop: vi.fn(async () => ({ contract_version: '0.3.0' as const, instance_id: 'instance', busy, closing: false })),
    closeDesktop: vi.fn(async () => undefined) }
  render(<DesktopExit hostBridge={host} unsaved={unsaved} />)
  return host
}
describe('显式桌面退出', () => {
  it('取消不读或停止服务，确认后匹配实例才关闭', async () => {
    const host = setup()
    fireEvent.click(screen.getByRole('button', { name: '退出应用' }))
    fireEvent.click(screen.getByRole('button', { name: '返回工作区' }))
    expect(host.inspectDesktop).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '退出应用' }))
    fireEvent.click(screen.getByRole('button', { name: '确认退出应用' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('本机服务已停止'))
    expect(host.closeDesktop).toHaveBeenCalledExactlyOnceWith('instance')
  })
  it('未应用更改阻止退出；忙碌时仍保留正在运行的服务', async () => {
    const host = setup(false, true)
    fireEvent.click(screen.getByRole('button', { name: '退出应用' }))
    fireEvent.click(screen.getByRole('button', { name: '确认退出应用' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('正在处理或保存'))
    expect(host.closeDesktop).not.toHaveBeenCalled()
  })
  it('未保存时确认被禁用', () => {
    setup(true)
    fireEvent.click(screen.getByRole('button', { name: '退出应用' }))
    expect(screen.getByRole('button', { name: '确认退出应用' })).toBeDisabled()
  })
  it('选择或导入外部文件期间不能退出，即使服务尚未开始复制', () => {
    window.__ZNIKU_DESKTOP__ = { contractVersion: '0.3.0', instanceId: 'instance' }
    const host: HostBridge = { configured: true, inspectCapabilities: vi.fn(), pick: vi.fn(), launch: vi.fn(), inspectDesktop: vi.fn(), closeDesktop: vi.fn() }
    render(<DesktopExit hostBridge={host} unsaved={false} operationBusy />)
    fireEvent.click(screen.getByRole('button', { name: '退出应用' }))
    expect(screen.getByRole('button', { name: '确认退出应用' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('正在选择或导入外部文件')
    expect(host.inspectDesktop).not.toHaveBeenCalled()
    expect(host.closeDesktop).not.toHaveBeenCalled()
  })
})
