/** 准入失败后的显式重试只绑定实际 Run 中的精确节点，不按模板实例名猜测。 */
import type { ProjectSnapshotWire, RunDetailEnvelope } from './contracts'
import { COLOR_PREPARED_VERSION } from './prepared-color-contracts'
import { WORK_SOURCE_VERSION, isWorkPreparationOperationNode, type WorkRetryTarget } from './working-source-contracts'

export interface ColorAdmissionRetryIntent { readonly run_id: string }
export type PreparationRetryIntent = ColorAdmissionRetryIntent | (WorkRetryTarget & { readonly contract_version: typeof WORK_SOURCE_VERSION })

/** 新普通路线仅接后端实际失败 attempt；保存解释后仍使用既有 rerun_from_here。 */
export function failedPreparationNode(detail: RunDetailEnvelope, intent: PreparationRetryIntent, projectId: string): string {
  if (!('contract_version' in intent)) return failedColorAdmissionNode(detail, intent, projectId)
  const run = detail.run
  if (intent.contract_version !== WORK_SOURCE_VERSION || run.run_id !== intent.run_id || run.project_id !== projectId) throw new Error('准备重试身份已变化；请重新查看当前工程。')
  const node = run.graph_snapshot.nodes.find((item) => item.node_id === intent.node_id)
  const latest = run.node_runs.filter((item) => item.node_id === intent.node_id).sort((a, b) => b.attempt - a.attempt)[0]
  if (!isWorkPreparationOperationNode(node) || !latest || latest.node_run_id !== intent.node_run_id || latest.definition_version !== WORK_SOURCE_VERSION || latest.state !== 'failed') throw new Error('准备重试目标已不再是当前失败步骤；没有重复启动。')
  return intent.node_id
}

/** 只恢复同一 Graph 的格式选择，不从文件扩展名、路径或浏览器偏好推断。 */
export function recordedPreparationFormat(snapshot: ProjectSnapshotWire | null, version: string): 'mkv' | 'mp4' | 'mov' | null {
  const formats = ['mkv', 'mp4', 'mov'] as const
  const selected = snapshot?.project.graph.nodes.flatMap((node) => node.definition_version === version
    ? formats.filter((format) => node.type_id === `zniku.source_preparation.video_repair.external.${format}`) : []) ?? []
  return selected.length === 1 ? selected[0]! : null
}

export function failedColorAdmissionNode(detail: RunDetailEnvelope, intent: ColorAdmissionRetryIntent, projectId: string): string {
  const run = detail.run
  if (run.run_id !== intent.run_id || run.project_id !== projectId) throw new Error('准入运行身份已变化；没有启动重试，请重新查看当前工程。')
  const nodes = run.graph_snapshot.nodes.filter((node) => node.type_id === 'zniku.source_preparation.admission' && node.definition_version === COLOR_PREPARED_VERSION)
  if (nodes.length !== 1) throw new Error('无法唯一绑定本次失败的工作源准入节点；请在节点图中查看问题。')
  const node = nodes[0]!
  const latest = run.node_runs.filter((attempt) => attempt.node_id === node.node_id).sort((left, right) => right.attempt - left.attempt)[0]
  if (!latest || latest.state !== 'failed' || latest.definition_version !== COLOR_PREPARED_VERSION) throw new Error('准入节点已不处于失败状态；没有重复启动，请重新查看当前运行。')
  return node.node_id
}
