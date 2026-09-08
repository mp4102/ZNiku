/** 桌面退出是显式用户动作，不在卸载、关标签页或轮询时停止服务。 */
import { useState } from 'react'
import { HostBridgeError, type HostBridge } from '../host-bridge'
import { CanvasDialog } from './ConnectNodesDialog'

export function DesktopExit({ hostBridge, unsaved, operationBusy = false }: { readonly hostBridge: HostBridge; readonly unsaved: boolean; readonly operationBusy?: boolean }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [closed, setClosed] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const bootstrap = window.__ZNIKU_DESKTOP__
  if (!bootstrap) return null
  async function close() {
    if (busy || unsaved || operationBusy || !bootstrap || !hostBridge.inspectDesktop || !hostBridge.closeDesktop) return
    setBusy(true)
    setMessage(null)
    try {
      const session = await hostBridge.inspectDesktop()
      if (session.instance_id !== bootstrap.instanceId || session.contract_version !== bootstrap.contractVersion) throw new Error('instance mismatch')
      if (session.busy) {
        setMessage('正在处理或保存，请等待当前自动操作结束再退出。退出不会强制取消处理中步骤。')
        return
      }
      await hostBridge.closeDesktop(bootstrap.instanceId)
      setClosed(true)
    } catch (error) {
      setMessage(error instanceof HostBridgeError && error.code === 'E_DESKTOP_BUSY'
        ? '正在处理或保存，请等待当前自动操作结束再退出。'
        : '无法确认应用已经退出。请恢复本机连接后重试；不要重复强制关闭处理进程。')
    } finally { setBusy(false) }
  }
  return <div className="desktop-exit">
    <button type="button" onClick={() => { setMessage(null); setOpen(true) }}>退出应用</button>
    {open && <CanvasDialog title={closed ? '应用已退出' : '退出 ZNIKU Studio'} onClose={() => { if (!busy) setOpen(false) }}>
      {closed ? <p role="status">本机服务已停止。你可以关闭此标签页；下次双击桌面入口可重新打开。</p> : <>
        <p>退出将停止本机服务。已保存工程、已完成输出与等待外部处理的任务会保留。只关闭浏览器标签页不会停止服务。</p>
        {unsaved && <p role="alert">还有未保存或未应用的更改。请先返回工程应用或放弃设置，并等待保存完成。</p>}
        {operationBusy && <p role="alert">正在选择或导入外部文件。请先完成或取消本次操作，再退出应用。</p>}
        {message && <p role="alert">{message}</p>}
        <div className="dialog-actions"><button type="button" disabled={busy} onClick={() => setOpen(false)}>返回工作区</button><button type="button" disabled={busy || unsaved || operationBusy || !hostBridge.closeDesktop || !hostBridge.inspectDesktop} onClick={() => void close()}>{busy ? '正在安全退出…' : '确认退出应用'}</button></div>
      </>}
    </CanvasDialog>}
  </div>
}
