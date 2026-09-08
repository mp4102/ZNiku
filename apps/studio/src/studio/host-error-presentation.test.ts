/** HostBridge 人话提示是有限映射，不猜测未知错误，也不改变原始异常。 */
import { describe, expect, it } from 'vitest'
import { HostBridgeError } from './host-bridge'
import { formatHostBridgeError } from './host-error-presentation'

describe('HostBridge 已知错误展示', () => {
  it('原生选择器忙碌统一解释为已打开窗口，而不是素材要求不符', () => {
    const error = new HostBridgeError('synthetic native chooser is active', { code: 'E_HOST_BRIDGE_DIALOG_BUSY', httpStatus: 409 })
    const message = '已有文件/文件夹选择窗口打开，请先完成或取消；它可能在浏览器后面。'
    expect(formatHostBridgeError(error)).toBe(message)
    expect(formatHostBridgeError('E_HOST_BRIDGE_DIALOG_BUSY: synthetic native chooser is active')).toBe(message)
    expect(error.message).toBe('synthetic native chooser is active')
  })

  it('未知码、非错误输入及原文中偶然提到的码均不套用恢复提示', () => {
    expect(formatHostBridgeError(new HostBridgeError('E_HOST_BRIDGE_DIALOG_BUSY: quoted', { code: 'E_HOST_BRIDGE_UNKNOWN' }))).toBeNull()
    expect(formatHostBridgeError(new Error('Unexpected E_HOST_BRIDGE_DIALOG_BUSY while reading log'))).toBeNull()
    expect(formatHostBridgeError('E_HOST_BRIDGE_DIALOG_BUSY_EXTRA: unknown')).toBeNull()
    expect(formatHostBridgeError(null)).toBeNull()
  })
})
