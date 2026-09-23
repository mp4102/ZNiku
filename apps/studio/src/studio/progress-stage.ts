/** 只翻译同一节点的正式进度；总量达到 100% 不代替 Runtime 完成确认。 */
import type { WorkflowProgressData } from '../model'
import type { NodeRunWire } from './contracts'

export function progressStageLabel(progress: WorkflowProgressData | null, nodeRun: NodeRunWire | null): string | null {
  if (!progress || nodeRun?.state !== 'running') return null
  return progress.fraction === 1 ? `${progress.stage ?? '媒体处理已到总量'}，等待完成确认` : progress.stage ?? null
}
