/** 页面内检查结果只绑定当前交接与观察到的文件；它不是验收凭据，提交仍由 Python 完整重验。 */

import type { ExternalHandoffReadiness, NodeRunWire } from './contracts'

export function readinessMatchesHandoff(value: ExternalHandoffReadiness, nodeRun: NodeRunWire): boolean {
  const handoff = nodeRun.external_handoff
  if (!handoff || nodeRun.state !== 'waiting_external' || value.run_id !== nodeRun.run_id ||
      value.node_run_id !== nodeRun.node_run_id || value.handoff_id !== handoff.handoff_id ||
      value.targets.length !== handoff.output_targets.length) return false
  const keys = new Set<string>()
  return value.targets.every((target) => {
    const key = JSON.stringify([target.port_id, target.ordinal])
    if (keys.has(key)) return false
    keys.add(key)
    return handoff.output_targets.some((expected) => expected.port_id === target.port_id &&
      expected.ordinal === target.ordinal && expected.path === target.path)
  })
}

/** size/mtime 只是撤销过期按钮资格的低成本线索，绝不替代 probe 或 validator。 */
export function sameObservedOutputs(left: ExternalHandoffReadiness, right: ExternalHandoffReadiness): boolean {
  if (left.run_id !== right.run_id || left.node_run_id !== right.node_run_id ||
      left.handoff_id !== right.handoff_id || left.targets.length !== right.targets.length) return false
  return left.targets.every((target) => right.targets.some((other) =>
    other.port_id === target.port_id && other.ordinal === target.ordinal && other.path === target.path &&
    other.size === target.size && other.mtime_ns === target.mtime_ns &&
    (other.state === 'present' || other.state === 'probe_passed'),
  ))
}

export function isFullCheck(value: ExternalHandoffReadiness, nodeRun: NodeRunWire): boolean {
  return readinessMatchesHandoff(value, nodeRun) && value.probe_requested && value.ready_for_submit &&
    value.targets.length > 0 && value.targets.every((target) => target.state === 'probe_passed')
}
