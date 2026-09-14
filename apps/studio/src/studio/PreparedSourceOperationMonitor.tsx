/** 外部助手与导入弹窗共用的只读准备进度；停止信号不受复制/验证互斥锁阻断。 */
import { useEffect, useRef, useState } from 'react'
import type { PreparedSourceOperationEnvelope } from './prepared-source-contracts'
import type { ColorPreparedSourceOperationEnvelope } from './prepared-color-contracts'
import { WORK_SOURCE_VERSION, type WorkOperationEnvelope } from './working-source-contracts'
import { SourcePreparationProgress } from './SourcePreparationProgress'

const units = { frames: '帧', packets: '包', bytes: '字节', tracks: '音轨', samples: '样本' } as const
export function PreparedSourceOperationMonitor({ projectSessionId, runId, nodeRunId, unavailable, operationPending, inspect, cancel }: {
  readonly projectSessionId: string
  readonly runId: string
  readonly nodeRunId: string
  readonly unavailable: boolean
  readonly operationPending: boolean
  readonly inspect: (runId: string, nodeRunId: string) => Promise<PreparedSourceOperationEnvelope | ColorPreparedSourceOperationEnvelope | WorkOperationEnvelope>
  readonly cancel: (runId: string, nodeRunId: string) => Promise<void>
}) {
  const [view, setView] = useState<PreparedSourceOperationEnvelope | ColorPreparedSourceOperationEnvelope | WorkOperationEnvelope | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stopping, setStopping] = useState(false)
  const [stopMessage, setStopMessage] = useState<string | null>(null)
  const identity = `${projectSessionId}/${runId}/${nodeRunId}`
  const identityRef = useRef(identity)
  identityRef.current = identity
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    setView(null); setError(null); setStopMessage(null); setStopping(false)
    if (unavailable) return
    const read = async () => {
      try {
        const next = await inspect(runId, nodeRunId)
        if (!disposed && next.project_session_id === projectSessionId && next.run_id === runId && next.node_run_id === nodeRunId) { setView(next); setError(null) }
      } catch (failure) {
        if (!disposed) setError(failure instanceof Error ? failure.message : '暂不能读取当前验证进度')
      } finally { if (!disposed) timer = setTimeout(() => void read(), 1000) }
    }
    void read()
    return () => { disposed = true; clearTimeout(timer) }
  }, [inspect, projectSessionId, runId, nodeRunId, unavailable])
  const current = view?.project_session_id === projectSessionId && view.run_id === runId && view.node_run_id === nodeRunId ? view : null
  const active = current?.active ?? operationPending
  if (!active) return null
  const measurement = current?.stage_progress
  const stop = async () => {
    if (stopping || unavailable) return
    setStopping(true)
    try { await cancel(runId, nodeRunId); if (identityRef.current === identity) setStopMessage('停止请求处理完毕；以服务返回的最终状态为准，未完成步骤只能从头重试。') }
    catch { if (identityRef.current === identity) setStopMessage('停止请求未完成，请检查连接后重试；不代表当前操作已停止。') }
    finally { if (identityRef.current === identity) setStopping(false) }
  }
  return <section aria-label="当前素材准备操作" className="source-preparation">
    <SourcePreparationProgress mode={current?.contract_version === WORK_SOURCE_VERSION ? 'working' : 'strict'} measurement={measurement ? { stage: measurement.stage, current: measurement.current, total: measurement.total,
      unit: measurement.unit ? units[measurement.unit] : null, elapsed_seconds: measurement.elapsed_seconds,
      speed: measurement.rate_per_second === null ? null : `${measurement.rate_per_second.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ${measurement.unit ? units[measurement.unit] : '项'}/秒`,
    } : { stage: '正在读取检查与验证进度', current: null, total: null, unit: null, elapsed_seconds: null, speed: null }} />
    {(error || unavailable) && <p role="status">进度暂不可用；不代表验证已通过。连接恢复后可重新读取。</p>}
    <button className="button button--ghost" type="button" disabled={stopping || unavailable || current?.cancel_requested === true} onClick={() => void stop()}>{stopping ? '正在发送停止请求…' : current?.cancel_requested ? '已请求停止，等待收尾…' : '停止当前检查或验证'}</button>
    {stopMessage && <p role="status">{stopMessage}</p>}
  </section>
}
