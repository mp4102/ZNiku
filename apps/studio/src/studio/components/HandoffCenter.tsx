/** 展示人工外部节点队列；存在/探测状态只读投影，提交仍由 Workspace 发起正式命令。 */

import { HandoffContract, HandoffPrecheckFailure, ReadinessMessages } from '../HandoffContract'
import type {
  ArtifactWire,
  ExternalHandoffReadiness,
  JsonObject,
  NodeRunWire,
  RunDetailEnvelope,
} from '../contracts'

export function handoffResourceKey(runId: string, nodeRunId: string, handoffId: string): string {
  return `${runId}/${nodeRunId}/${handoffId}`
}
export function readinessLabel(value: ExternalHandoffReadiness | null): string {
  if (!value) return 'checking'
  if (value.ready_for_submit) return 'probe passed'
  const states = [...new Set(value.targets.map((target) => target.state))]
  return states.join(', ') || 'no targets'
}

export function elapsedLabel(createdAt: string): string {
  const created = Date.parse(createdAt)
  if (!Number.isFinite(created)) return '等待时长未知'
  const seconds = Math.max(0, Math.floor((Date.now() - created) / 1_000))
  if (seconds < 60) return `已等待 ${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `已等待 ${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `已等待 ${hours}h ${minutes % 60}m`
  return `已等待 ${Math.floor(hours / 24)}d ${hours % 24}h`
}

function parameterTextValue(parameters: JsonObject, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = parameters[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return null
}

export interface HandoffCenterProps {
  readonly waitingNodeRuns: ReadonlyArray<NodeRunWire>
  readonly detail: RunDetailEnvelope | null
  readonly artifactsById: ReadonlyMap<string, ArtifactWire>
  readonly readiness: ReadonlyMap<string, ExternalHandoffReadiness>
  readonly lastFullPrecheckFailures: ReadonlyMap<string, ExternalHandoffReadiness>
  readonly mutationBlocked: boolean
  readonly readinessStale: boolean
  readonly onSelectNode: (nodeId: string) => void
  readonly onCopyPath: (path: string) => void
  readonly onValidateAndSubmit: (nodeRun: NodeRunWire) => void
}

export function HandoffCenter({
  waitingNodeRuns,
  detail,
  artifactsById,
  readiness,
  lastFullPrecheckFailures,
  mutationBlocked,
  readinessStale,
  onSelectNode,
  onCopyPath,
  onValidateAndSubmit,
}: HandoffCenterProps) {
  if (waitingNodeRuns.length === 0) return null
  const run = detail?.run ?? null
  return (
    <section className="handoff-queue" aria-label="External Handoff 队列">
      <h3>External Handoff Queue</h3>
      {waitingNodeRuns.map((nodeRun) => {
        const node = run?.graph_snapshot.nodes.find((item) => item.node_id === nodeRun.node_id)
        const modelName = node ? parameterTextValue(node.parameters, 'actual_model_name', 'model_name') : null
        const modelVersion = node ? parameterTextValue(node.parameters, 'actual_model_version', 'model_version') : null
        const handoff = nodeRun.external_handoff!
        const observedReadiness = readiness.get(nodeRun.node_run_id) ?? null
        const inputPaths = handoff.input_artifact_ids.map((artifactId) => artifactsById.get(artifactId)?.path ?? `未解析 Artifact：${artifactId}`)
        const canValidate = !mutationBlocked && !readinessStale && observedReadiness !== null && observedReadiness.targets.every((target) => target.state === 'present' || target.state === 'probe_passed')
        return (
          <article aria-label={`Handoff ${nodeRun.node_id}`} key={nodeRun.node_run_id}>
            <button className="handoff-queue-select" type="button" onClick={() => onSelectNode(nodeRun.node_id)}>
              <strong>{nodeRun.node_id}</strong>
              <span>{modelName ?? 'model not declared'}{modelVersion ? ` · ${modelVersion}` : ''}</span>
              <em>{readinessLabel(observedReadiness)} · {elapsedLabel(nodeRun.started_at ?? nodeRun.created_at)}</em>
            </button>
            {handoff.instructions && <p>{handoff.instructions}</p>}
            {detail?.handoff_contracts.filter((contract) => contract.node_run_id === nodeRun.node_run_id).map((contract) => <HandoffContract key={contract.node_run_id} contract={contract} />)}
            <ReadinessMessages readiness={observedReadiness} />
            {run && <HandoffPrecheckFailure failure={lastFullPrecheckFailures.get(handoffResourceKey(run.run_id, nodeRun.node_run_id, handoff.handoff_id)) ?? null} />}
            <span className="handoff-queue-label">Inputs</span>
            {inputPaths.map((path, index) => (
              <div className="handoff-path" key={`${handoff.input_artifact_ids[index]}-${index}`}>
                <code>{path}</code><button type="button" onClick={() => onCopyPath(path)}>Copy input path</button>
              </div>
            ))}
            <span className="handoff-queue-label">Targets</span>
            {handoff.output_targets.map((target) => (
              <div className="handoff-path" key={`${target.port_id}-${target.ordinal ?? 'one'}`}>
                <code>{target.port_id}{target.ordinal === null ? '' : ` #${target.ordinal}`} · {target.path}</code>
                <button type="button" onClick={() => onCopyPath(target.path)}>Copy target path</button>
              </div>
            ))}
            <button className="button button--primary handoff-queue-submit" type="button" disabled={!canValidate} onClick={() => onValidateAndSubmit(nodeRun)}>Validate and submit</button>
          </article>
        )
      })}
    </section>
  )
}
