/** 通用 OutputFile 的字节进度展示，不按业务节点名猜执行阶段，也不推算 ETA。 */
import type { WorkflowProgressData } from '../model'
import type { JsonObject, NodeDefinitionWire, NodeRunWire } from './contracts'

export function copyProgressOperation(definition: NodeDefinitionWire, parameters?: JsonObject): 'copy' | undefined {
  return definition.execution_mode === 'automatic' && definition.executor.kind === 'python' &&
    definition.executor.adapter === 'zniku.media.adapters:output_file' && parameters?.mode === 'copy' ? 'copy' : undefined
}

export interface CopyProgressPresentation {
  readonly label: string
  readonly volume: string
  readonly averageRate: string | null
  readonly current: number
  readonly total: number
}

function capacity(bytes: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'] as const
  const index = Math.min(units.length - 1, Math.max(0, Math.floor(Math.log2(Math.max(1, bytes)) / 10)))
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 2)} ${units[index]}`
}

export function copyProgressPresentation(progress: WorkflowProgressData, nodeRun: NodeRunWire | null): CopyProgressPresentation | null {
  const sample = progress.measurement
  if (progress.operation !== 'copy' || nodeRun?.state !== 'running' || progress.mode !== 'determinate' ||
    !sample || sample.node_run_id !== nodeRun.node_run_id || sample.unit !== 'bytes' ||
    sample.current === null || sample.total === null || !Number.isFinite(sample.current) || !Number.isFinite(sample.total) ||
    sample.total <= 0 || sample.current < 0 || sample.current > sample.total) return null
  const started = nodeRun.started_at ? Date.parse(nodeRun.started_at) : NaN
  const observed = Date.parse(sample.observed_at)
  const seconds = (observed - started) / 1000
  // 两个服务端时间戳才能给出步骤平均速率；不用浏览器时钟补齐样本，也不冒称瞬时网络吞吐。
  const averageRate = Number.isFinite(seconds) && seconds > 0 && sample.current > 0
    ? `步骤平均 ${capacity(sample.current / seconds)}/s` : null
  return { label: sample.current === sample.total ? '字节已复制，等待完成确认' : '正在复制文件',
    volume: `已复制 ${capacity(sample.current)} / ${capacity(sample.total)}`, averageRate,
    current: sample.current, total: sample.total }
}
