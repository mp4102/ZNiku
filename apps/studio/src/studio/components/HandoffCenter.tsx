/** 外部处理助手只投影正式交接；检查与提交是独立意图，文件存在绝不触发执行。 */

import { HandoffContract, HandoffPrecheckFailure, ReadinessMessages, fileName } from '../HandoffContract'
import type { ArtifactWire, ExternalHandoffReadiness, NodeRunWire, RunDetailEnvelope } from '../contracts'
import { isFullCheck, readinessMatchesHandoff, sameObservedOutputs } from '../handoff-check'
import type { HostPathReference, HostSystemCapability } from '../host-bridge'

export function handoffResourceKey(runId: string, nodeRunId: string, handoffId: string): string {
  return `${runId}/${nodeRunId}/${handoffId}`
}

export function readinessLabel(value: ExternalHandoffReadiness | null): string {
  if (!value) return '正在检测目标文件'
  if (value.probe_requested && value.ready_for_submit) return '完整检查通过，等待你提交'
  if (value.targets.some((target) => target.state === 'probe_failed')) return '检查未通过'
  if (value.targets.some((target) => target.state === 'missing')) return '尚未发现全部目标文件'
  if (value.targets.some((target) => target.state === 'empty')) return '目标文件为空，请等待外部工具完成'
  return value.targets.length ? '已发现目标文件，尚未完成检查' : '未提供目标文件'
}

export function elapsedLabel(createdAt: string): string {
  const created = Date.parse(createdAt)
  if (!Number.isFinite(created)) return '等待时长未知'
  const seconds = Math.max(0, Math.floor((Date.now() - created) / 1_000))
  if (seconds < 60) return `已等待 ${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `已等待 ${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `已等待 ${hours} 小时 ${minutes % 60} 分钟`
  return `已等待 ${Math.floor(hours / 24)} 天 ${hours % 24} 小时`
}

export interface HandoffCenterProps {
  readonly advanced?: boolean
  readonly nodeLabel?: (nodeId: string) => string
  readonly waitingNodeRuns: ReadonlyArray<NodeRunWire>
  readonly detail: RunDetailEnvelope | null
  readonly artifactsById: ReadonlyMap<string, ArtifactWire>
  readonly readiness: ReadonlyMap<string, ExternalHandoffReadiness>
  readonly checkedOutputs: ReadonlyMap<string, ExternalHandoffReadiness>
  readonly lastFullPrecheckFailures: ReadonlyMap<string, ExternalHandoffReadiness>
  readonly checkingNodeRunId: string | null
  readonly submittingNodeRunId: string | null
  readonly mutationBlocked: boolean
  readonly readinessStale: boolean
  readonly canRevealHandoff: boolean
  readonly canOpenHandoffInput: boolean
  readonly onSelectNode: (nodeId: string) => void
  readonly onCopyPath: (path: string) => void
  readonly onCheckOutput: (nodeRun: NodeRunWire) => void
  readonly onSubmitOutput: (nodeRun: NodeRunWire) => void
  readonly onLaunchHandoff: (
    nodeRun: NodeRunWire,
    capability: HostSystemCapability,
    selector: Extract<HostPathReference, { readonly kind: 'handoff' }>['selector'],
  ) => void
}

export function HandoffCenter(props: HandoffCenterProps) {
  const { waitingNodeRuns, detail, artifactsById, readiness, checkedOutputs } = props
  if (!waitingNodeRuns.length) return null
  return (
    <section className="handoff-queue" aria-label="外部处理助手" id="external-processing-assistant">
      <h3>外部处理助手</h3>
      <p>在外部工具中完成处理后，先检查输出，再由你提交并继续。这里不会自动操作外部工具。</p>
      {waitingNodeRuns.filter((item) => item.state === 'waiting_external' && item.external_handoff !== null).map((nodeRun) => {
        const handoff = nodeRun.external_handoff!
        const key = handoffResourceKey(nodeRun.run_id, nodeRun.node_run_id, handoff.handoff_id)
        const candidate = readiness.get(nodeRun.node_run_id) ?? null
        const observed = candidate && readinessMatchesHandoff(candidate, nodeRun) ? candidate : null
        const checked = checkedOutputs.get(key) ?? null
        const checkStillCurrent = !!checked && isFullCheck(checked, nodeRun) && !!observed && sameObservedOutputs(checked, observed)
        const checking = props.checkingNodeRunId === nodeRun.node_run_id
        const submitting = props.submittingNodeRunId === nodeRun.node_run_id
        const anyOperation = props.checkingNodeRunId !== null || props.submittingNodeRunId !== null
        const disabledReason = anyOperation ? '正在检查或提交，请等待本次操作完成。'
          : props.mutationBlocked ? '当前连接或工程状态不允许操作，请先恢复连接并处理提示。'
          : props.readinessStale ? '文件检测暂时离线；恢复检测后才能检查或提交。'
          : !observed ? '正在读取此任务的目标文件状态，请稍候。'
          : observed.targets.length === 0 || observed.targets.some((target) => target.state === 'missing' || target.state === 'empty') ? '请先在外部工具中完成输出，保存为下方目标文件。'
          : null
        const submitReason = disabledReason ?? (!checkStillCurrent
          ? checked ? '目标文件或检查结果已变化，请重新检查输出。' : '请先点击“检查输出”，完整检查通过后才能提交。'
          : null)
        const nodeTitle = props.nodeLabel?.(nodeRun.node_id) ?? '外部处理步骤'
        const failure = props.lastFullPrecheckFailures.get(key) ?? null
        const contracts = detail?.run.run_id === nodeRun.run_id ? detail.handoff_contracts.filter((item) => item.node_run_id === nodeRun.node_run_id && item.handoff_id === handoff.handoff_id) : []
        return (
          <article aria-label={`外部处理：${nodeTitle}`} key={key}>
            <button className="handoff-queue-select" type="button" onClick={() => props.onSelectNode(nodeRun.node_id)}>
              <strong>{nodeTitle}</strong>
              <em>{elapsedLabel(handoff.created_at)}</em>
              <span>定位到此节点</span>
            </button>
            <ol className="handoff-steps">
              <li className="handoff-step">
                <h4>确认处理要求</h4>
                {handoff.instructions && <p>{handoff.instructions}</p>}
                {contracts.map((contract) => <HandoffContract key={contract.node_run_id} contract={contract} />)}
                {!contracts.length && <p>请按本步骤要求处理，并保留指定输出名称。系统会检查输出是否符合节点的正式要求。</p>}
              </li>
              <li className="handoff-step">
                <h4>打开输入，在外部工具中处理</h4>
                {handoff.input_artifact_ids.map((artifactId, index) => {
                  const artifact = artifactsById.get(artifactId)
                  return <div className="handoff-path" key={artifactId}>
                    <strong className="handoff-file-name">{artifact ? fileName(artifact.path) : `输入 ${index + 1} 暂不可用`}</strong>
                    {artifact && <div className="artifact-host-actions">
                      <button type="button" disabled={props.mutationBlocked || !props.canOpenHandoffInput} onClick={() => props.onLaunchHandoff(nodeRun, 'open_with_system_player', { role: 'input_artifact', artifact_id: artifactId })}>打开输入</button>
                      <button type="button" disabled={props.mutationBlocked || !props.canRevealHandoff} onClick={() => props.onLaunchHandoff(nodeRun, 'reveal_in_file_manager', { role: 'input_artifact', artifact_id: artifactId })}>显示输入位置</button>
                      <button type="button" onClick={() => props.onCopyPath(artifact.path)}>复制输入路径</button>
                    </div>}
                  </div>
                })}
                <button type="button" disabled={props.mutationBlocked || !props.canRevealHandoff} onClick={() => props.onLaunchHandoff(nodeRun, 'reveal_in_file_manager', { role: 'work_directory' })}>打开工作目录</button>
                {(!props.canRevealHandoff || !props.canOpenHandoffInput) && <p className="handoff-disabled-reason">本机打开能力不可用时，可复制路径后在外部工具中打开；使用 ZNIKU launcher 启动可连接本机能力。</p>}
              </li>
              <li className="handoff-step">
                <h4>保存到目标文件</h4>
                {handoff.output_targets.map((target) => {
                  const state = observed?.targets.find((item) => item.port_id === target.port_id && item.ordinal === target.ordinal)?.state
                  const status = state === 'missing' ? '尚未发现' : state === 'empty' ? '文件为空' : state === 'probe_failed' ? '检查未通过' : state ? '已发现' : '等待检测'
                  return <div className="handoff-path" key={`${target.port_id}-${target.ordinal ?? 'one'}`}>
                    <strong className="handoff-file-name">{fileName(target.path)}</strong>
                    <span className="handoff-target-status">{status}</span>
                    <button type="button" onClick={() => props.onCopyPath(target.path)}>复制目标路径</button>
                  </div>
                })}
                <p>文件出现只表示已发现，不代表外部工具已完成，也不会自动继续。</p>
              </li>
              <li className="handoff-step">
                <h4>检查后，由你提交并继续</h4>
                <p className="handoff-status" role="status">{props.readinessStale ? '检测离线，保留上次观察' : checkStillCurrent ? '完整检查通过，等待你提交' : readinessLabel(observed)}</p>
                <ReadinessMessages readiness={observed} />
                {failure && <HandoffPrecheckFailure failure={failure} resolved={checkStillCurrent} />}
                <div className="handoff-check-actions">
                  <button className="button button--ghost" type="button" disabled={disabledReason !== null} onClick={() => props.onCheckOutput(nodeRun)}>{checking ? '正在完整检查…' : '检查输出'}</button>
                  <button className="button button--primary" type="button" disabled={submitReason !== null} onClick={() => props.onSubmitOutput(nodeRun)}>{submitting ? '正在提交并继续…' : '提交并继续'}</button>
                </div>
                {submitReason && <p className="handoff-disabled-reason">{submitReason}</p>}
                <p>提交时会再次完整检查；失败不会登记输出，已完成的上游结果保留。替换文件后必须重新检查。</p>
              </li>
            </ol>
            <details className="handoff-advanced" open={props.advanced || undefined}>
              <summary>高级 → 交接身份与完整路径</summary>
              <dl><div><dt>Run ID</dt><dd>{nodeRun.run_id}</dd></div><div><dt>NodeRun ID</dt><dd>{nodeRun.node_run_id}</dd></div><div><dt>Handoff ID</dt><dd>{handoff.handoff_id}</dd></div></dl>
              <code>{nodeRun.work_dir}</code>
              {handoff.input_artifact_ids.map((id) => <p key={id}><code>{id} · {artifactsById.get(id)?.path ?? '未解析'}</code></p>)}
              {handoff.output_targets.map((target) => <p key={`${target.port_id}-${target.ordinal}`}><code>{target.port_id} · {target.ordinal ?? 'single'} · {target.path}</code></p>)}
            </details>
          </article>
        )
      })}
    </section>
  )
}
