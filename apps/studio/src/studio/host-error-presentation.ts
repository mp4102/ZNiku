/** 只翻译 HostBridge 已知错误；未知错误由调用方保留原文与现有安全降级，不推导新的恢复动作。 */
import { HostBridgeError } from './host-bridge'

export function formatHostBridgeError(error: unknown): string | null {
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : null
  // 结构化错误码优先；旧 UI 已存为字符串时只接受开头的精确错误码，不匹配原文里的偶然提及。
  const code = error instanceof HostBridgeError && error.code !== null
    ? error.code
    : message?.match(/^(E_[A-Z_]+)(?=:|$)/)?.[1]
  if (code === 'E_HOST_BRIDGE_DIALOG_BUSY') {
    return '已有文件/文件夹选择窗口打开，请先完成或取消；它可能在浏览器后面。'
  }
  return null
}
