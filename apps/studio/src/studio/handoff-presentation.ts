/** 通用节点去重与交接摘要只整理服务端展示文本，不解析业务类型、计算媒体要求或改写别名。 */
import type { ArtifactWire, NodeInstanceWire, NodeRunWire, RunDetailEnvelope } from './contracts'
import { fileName } from './HandoffContract'

export function distinctNodeLabels(
  nodes: ReadonlyArray<NodeInstanceWire>,
  label: (node: NodeInstanceWire) => string,
): ReadonlyMap<string, string> {
  const names = nodes.map((node) => label(node))
  const counts = new Map<string, number>()
  names.forEach((name) => counts.set(name, (counts.get(name) ?? 0) + 1))
  const ordinals = new Map<string, number>()
  const used = new Set<string>()
  return new Map(nodes.map((node, index) => {
    const name = names[index]!
    const ordinal = (ordinals.get(name) ?? 0) + 1
    ordinals.set(name, ordinal)
    let display = counts.get(name)! > 1 ? `${name}（${ordinal}）` : name
    // 用户可能已把别名命名为“增强（1）”；仅消解显示碰撞，不改变持久别名或身份。
    let collision = 1
    while (used.has(display)) display = `${name}（${ordinal}·${collision++}）`
    used.add(display)
    return [node.node_id, display]
  }))
}

export function handoffInputNames(nodeRun: NodeRunWire, artifacts: ReadonlyMap<string, ArtifactWire>): string[] {
  return (nodeRun.external_handoff?.input_artifact_ids ?? []).map((id, index) => {
    const artifact = artifacts.get(id)
    return artifact ? fileName(artifact.path) : `输入 ${index + 1} 暂不可用`
  })
}

/** 与 HandoffContract 的标签翻译相同，只提取正式文本，不转为数值或作为检查资格。 */
export function handoffExpectedFrames(nodeRun: NodeRunWire, detail: RunDetailEnvelope | null): string[] {
  if (!nodeRun.external_handoff || detail?.run.run_id !== nodeRun.run_id) return []
  return [...new Set(detail.handoff_contracts.filter((contract) => contract.node_run_id === nodeRun.node_run_id &&
    contract.handoff_id === nodeRun.external_handoff!.handoff_id).flatMap((contract) => contract.fields
    .filter((field) => field.label === '输出 exact N' || field.label === '输出精确帧数').map((field) => field.value)))]
}

export function handoffSummary(nodeRun: NodeRunWire, artifacts: ReadonlyMap<string, ArtifactWire>, detail: RunDetailEnvelope | null): string[] {
  return [
    ...handoffInputNames(nodeRun, artifacts).map((name) => `输入：${name}`),
    ...handoffExpectedFrames(nodeRun, detail).map((frames) => `预期输出：${frames} 帧`),
  ]
}
