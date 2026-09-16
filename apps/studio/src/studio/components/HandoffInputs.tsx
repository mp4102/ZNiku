/** 输入按通用 Artifact 类型分层展示；报告只消费服务端只读投影，不读取文件或重算媒体合同。 */
import type { ArtifactInputReportWire, ArtifactWire, NodeRunWire } from '../contracts'
import type { HostPathReference, HostSystemCapability } from '../host-bridge'
import { ArtifactMediaSummary, ReadOnlyJson, fileName } from '../HandoffContract'
import { handoffInputLabel } from '../handoff-presentation'

interface HandoffInputsProps {
  readonly nodeRun: NodeRunWire
  readonly artifactsById: ReadonlyMap<string, ArtifactWire>
  readonly reports: ReadonlyArray<ArtifactInputReportWire>
  readonly mutationBlocked: boolean
  readonly canRevealHandoff: boolean
  readonly canOpenHandoffInput: boolean
  readonly onCopyPath: (path: string) => void
  readonly onLaunchHandoff: (nodeRun: NodeRunWire, capability: HostSystemCapability,
    selector: Extract<HostPathReference, { readonly kind: 'handoff' }>['selector']) => void
}

export function HandoffInputs(props: HandoffInputsProps) {
  const inputs = (props.nodeRun.external_handoff?.input_artifact_ids ?? []).map((id, index) => ({
    id, index, artifact: props.artifactsById.get(id),
  }))
  const primary = inputs.filter(({ artifact }) => artifact?.kind !== 'DataFile')
  const reports = inputs.filter(({ artifact }) => artifact?.kind === 'DataFile')
  function actions(artifact: ArtifactWire, report: boolean) {
    const playable = ['VideoFile', 'MediaFile', 'AudioFile'].includes(artifact.kind)
    const selector = { role: 'input_artifact' as const, artifact_id: artifact.artifact_id }
    return <div className="artifact-host-actions">
      {playable && <button type="button" disabled={props.mutationBlocked || !props.canOpenHandoffInput}
        onClick={() => props.onLaunchHandoff(props.nodeRun, 'open_with_system_player', selector)}>打开输入</button>}
      <button type="button" disabled={props.mutationBlocked || !props.canRevealHandoff}
        onClick={() => props.onLaunchHandoff(props.nodeRun, 'reveal_in_file_manager', selector)}>{report ? '显示报告位置' : '显示输入位置'}</button>
      <button type="button" onClick={() => props.onCopyPath(artifact.path)}>{report ? '复制报告路径' : '复制输入路径'}</button>
    </div>
  }
  return <>
    {primary.map(({ id, index, artifact }) => <section className="handoff-path" key={id}
      aria-label={artifact ? `${handoffInputLabel(artifact)}：${fileName(artifact.path)}` : `输入 ${index + 1} 暂不可用`}>
      <small>{handoffInputLabel(artifact)}</small>
      <strong className="handoff-file-name">{artifact ? fileName(artifact.path) : `输入 ${index + 1} 暂不可用`}</strong>
      {artifact && <>{actions(artifact, false)}
        {['VideoFile', 'MediaFile', 'AudioFile'].includes(artifact.kind) && <ArtifactMediaSummary artifact={artifact} />}
      </>}
    </section>)}
    {reports.map(({ id, artifact }) => {
      const report = props.reports.find((item) => item.artifact_id === id)
      return <details className="artifact-media-summary" key={id}>
        <summary>{report?.title ?? '分析 / 参考报告'}</summary>
        <p>供你查看素材信息与分析结论，不需要送入外部视频工具。</p>
        <strong className="handoff-file-name">{fileName(artifact!.path)}</strong>
        {report?.fields.length ? <dl>{report.fields.map((field, index) => <div key={index}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl> : null}
        {report?.message && <p>{report.message}</p>}
        {!report && <p>当前报告摘要不可用；原文件仍然保留。</p>}
        {report?.document && <details><summary>高级 → 完整报告 JSON（只读）</summary>
          <ReadOnlyJson value={report.document} copyLabel="复制报告 JSON" />
        </details>}
        {actions(artifact!, true)}
      </details>
    })}
  </>
}
