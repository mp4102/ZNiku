/**
 * 展示选中节点/连接的表单与运行投影。ParameterDraft 由 Workspace session 持有，
 * 本组件只发出表单、raw JSON、应用或放弃意图。
 */

import type { ReactNode } from 'react'
import { ArtifactMediaSummary, fileName } from '../HandoffContract'
import { SchemaParameterForm } from '../SchemaParameterForm'
import type { ParameterPickerRequest } from '../SchemaParameterForm'
import type {
  ArtifactWire,
  EdgeWire,
  ExternalHandoffReadiness,
  JsonObject,
  LatestResultWire,
  NodeDefinitionWire,
  NodeLogWire,
  NodePresentationWire,
  NodeRunWire,
  RunDetailEnvelope,
} from '../contracts'
import { edgeId } from '../graph'
import { asParameterSchema, type ParameterDraftValidation } from '../parameter-draft'
import { elapsedLabel } from './HandoffCenter'
import { creatorElapsedLabel, failurePresentation, nodeStateLabel } from '../run-presentation'
import type { WorkflowProgressData } from '../../model'
import { OrderedInputList } from './OrderedInputList'

export interface NodeInspectorProps {
  readonly advanced?: boolean
  readonly authoringPanel?: ReactNode
  readonly orderedInputs?: ReadonlyArray<{ readonly label: string; readonly edges: ReadonlyArray<EdgeWire> }>
  readonly nodeLabel?: (nodeId: string) => string
  readonly sourcePortLabel?: (nodeId: string, portId: string) => string
  readonly selectedNode: { readonly node_id: string; readonly type_id: string; readonly definition_version: string } | null
  readonly selectedDefinition: NodeDefinitionWire | null
  readonly selectedPresentation: NodePresentationWire | null
  readonly selectedEdge: EdgeWire | null
  readonly parameterDraft: JsonObject
  readonly parameterText: string
  readonly parameterDirty: boolean
  readonly parameterRawError: string | null
  readonly parameterValidation: ParameterDraftValidation | null
  readonly graphEditable: boolean
  readonly busy: boolean
  readonly selectedNodeRun: NodeRunWire | null
  readonly selectedLatestResult?: LatestResultWire | null
  readonly advancedDetailsOpen?: boolean
  readonly onToggleDiagnostics?: (open: boolean) => void
  readonly selectedProgress: WorkflowProgressData | null
  readonly selectedLog: NodeLogWire | null
  readonly logStale: boolean
  readonly readinessStale: boolean
  readonly mutationBlocked: boolean
  readonly selectedOutputs: ReadonlyArray<ArtifactWire>
  readonly handoffInputs: ReadonlyArray<string>
  readonly readiness: ExternalHandoffReadiness | null
  readonly detail: RunDetailEnvelope | null
  readonly lastFullPrecheckFailure: ExternalHandoffReadiness | null
  readonly handoffCenter: ReactNode
  readonly actionableRun: boolean
  readonly actionableRunIsRunning: boolean
  readonly clientHint: string | null
  readonly boundaryError: string | null
  readonly onParameterDraftChange: (draft: JsonObject) => void
  readonly onPickParameterPath?: (request: ParameterPickerRequest) => Promise<ReadonlyArray<string> | null>
  readonly onParameterPickerError?: (error: unknown) => void
  readonly onParameterTextChange: (text: string) => void
  readonly onApplyParameters: () => void
  readonly onDiscardParameters: () => void
  readonly onCopyPath: (path: string) => void
  readonly canRevealArtifact: boolean
  readonly canOpenArtifact: boolean
  readonly onRevealArtifact: (artifactId: string) => void
  readonly onOpenArtifact: (artifactId: string) => void
  readonly onReorderEdge: (edgeId: string, ordinal: number) => void
  readonly onDeleteEdge: () => void
  readonly onAbandonRun: () => void
}
export function NodeInspector(props: NodeInspectorProps) {
  const {
    selectedNode, selectedDefinition, selectedPresentation, selectedEdge,
    parameterDraft, parameterText, parameterDirty, parameterRawError, parameterValidation,
    graphEditable, busy, mutationBlocked,
    handoffCenter, actionableRun, actionableRunIsRunning,
    clientHint, boundaryError,
  } = props
  // 运行任务优先于编辑表单；只调整同一投影的位置，不另建参数或交接状态。
  const handoffFirst = selectedNode !== null && props.selectedNodeRun?.state === 'waiting_external'
  return (
    <aside className="inspector-panel">
      <div className="panel-heading inspector-heading">
        <span className="eyebrow">INSPECTOR</span>
        <h2>{selectedNode ? props.nodeLabel?.(selectedNode.node_id) ?? selectedPresentation?.title ?? selectedNode.node_id : selectedEdge ? '连接设置' : '未选择实体'}</h2>
        {props.advanced && (selectedNode || selectedEdge) && <code>{selectedNode ? `${selectedNode.type_id}@${selectedNode.definition_version}` : edgeId(selectedEdge!)}</code>}
      </div>
      {selectedNode && <NodeRuntime {...props} />}
      {handoffFirst && handoffCenter}
      {props.authoringPanel}
      {props.orderedInputs?.map((input) => <section className="inspector-ordered-inputs" key={input.label}><h3>{input.label}</h3><OrderedInputList edges={input.edges} nodeLabel={props.nodeLabel ?? ((id) => id)} portLabel={props.sourcePortLabel ?? ((_nodeId, portId) => portId)} disabled={busy || !graphEditable} onReorder={props.onReorderEdge} /></section>)}
      {selectedNode && selectedDefinition && parameterValidation ? (
        <div className="inspector-content">
          {selectedPresentation && <p className="node-presentation-description">{selectedPresentation.description}</p>}
          <section>
            <h3>设置</h3>
            <SchemaParameterForm
              schema={asParameterSchema(selectedDefinition.parameter_schema)}
              draft={parameterDraft}
              validation={parameterValidation}
              presentation={selectedPresentation}
              readOnly={!graphEditable || busy}
              onPickPath={props.onPickParameterPath}
              onPickError={props.onParameterPickerError}
              onChange={props.onParameterDraftChange}
            />
            <div className="parameter-draft-actions">
              <button className="button button--primary inspector-action" type="button" disabled={busy || !graphEditable || !parameterDirty || !!parameterRawError || !parameterValidation.valid} onClick={props.onApplyParameters}>应用设置</button>
              <button className="button button--ghost inspector-action" type="button" disabled={!parameterDirty} onClick={props.onDiscardParameters}>放弃未应用更改</button>
              <span className={parameterValidation.valid && !parameterRawError ? 'is-valid' : 'is-invalid'}>
                {parameterRawError ?? (parameterValidation.valid ? '设置有效' : `${parameterValidation.errors.length} 个问题待修复`)}
              </span>
            </div>
            <details className="parameter-raw-json">
              <summary>高级 → 原始参数</summary>
              <textarea aria-label="节点参数 JSON" value={parameterText} onChange={(event) => props.onParameterTextChange(event.target.value)} rows={10} readOnly={!graphEditable || busy} />
              {parameterRawError && <p role="alert">{parameterRawError}</p>}
            </details>
            <details><summary>高级 → parameter_schema</summary><pre>{JSON.stringify(selectedDefinition.parameter_schema, null, 2)}</pre></details>
          </section>
          <details className="node-binding-details">
            <summary>高级 → 节点合同</summary>
            <section><h3>Node binding</h3><dl className="property-list"><div><dt>type_id</dt><dd>{selectedNode.type_id}</dd></div><div><dt>version</dt><dd>{selectedNode.definition_version}</dd></div><div><dt>executor</dt><dd>{selectedDefinition.executor.kind}</dd></div></dl></section>
            <section><h3>Typed ports</h3>{(['input_ports', 'output_ports'] as const).map((direction) => <div className="port-group" key={direction}><span className="port-group-label">{direction}</span>{selectedDefinition[direction].length ? selectedDefinition[direction].map((port) => { const portPresentation = selectedPresentation?.ports.find((item) => item.direction === (direction === 'input_ports' ? 'input' : 'output') && item.port_id === port.port_id); return <div className="port-summary" key={port.port_id}><span>{portPresentation?.label ?? port.port_id}</span><code>{port.data_type} · {port.cardinality}{port.required ? ' · required' : ''}</code></div> }) : <div className="port-empty">none</div>}</div>)}</section>
          </details>
        </div>
      ) : selectedEdge ? (
        <div className="inspector-content"><section><h3>连接</h3><p>{props.nodeLabel?.(selectedEdge.source_node_id) ?? selectedEdge.source_node_id} → {props.nodeLabel?.(selectedEdge.target_node_id) ?? selectedEdge.target_node_id}</p>{props.advanced && selectedEdge.ordinal !== null && <label className="ordinal-editor">Ordinal<input aria-label="Edge ordinal" type="number" min={0} value={selectedEdge.ordinal} disabled={!graphEditable} onChange={(event) => props.onReorderEdge(edgeId(selectedEdge), Number(event.target.value))} /></label>}<button className="button button--danger inspector-action" type="button" disabled={busy || !graphEditable} onClick={props.onDeleteEdge}>删除所选连接</button></section></div>
      ) : <div className="empty-inspector">选择一个步骤或连接，查看设置、处理状态和输出。</div>}
      {!handoffFirst && handoffCenter}
      {actionableRun && <details className="inspector-run-actions"><summary>高级 → 放弃本次处理</summary><p>放弃本次运行不会删除已有媒体。正在运行时不可放弃，请先等待当前步骤停止。</p><button className="button button--danger abandon-run" type="button" disabled={mutationBlocked || actionableRunIsRunning} onClick={props.onAbandonRun}>Abandon Run</button>{mutationBlocked && <p>请先恢复连接并解决当前阻塞提示。</p>}</details>}
      {clientHint && <p className="client-hint" role="status">{clientHint}</p>}
      {boundaryError && <section className="client-hint client-hint--error"><p role="alert">这次操作未能完成。当前工作与已有文件仍然保留，请处理运行中心的提示后重试。</p><details><summary>高级 → 操作原始详情</summary><pre>{boundaryError}</pre></details></section>}
    </aside>
  )
}

/** 运行结果完全来自服务；手工节点不展示任何传入百分比或外部工具 ETA。 */
function NodeRuntime(props: NodeInspectorProps) {
  const { selectedNodeRun: nodeRun, selectedProgress: progress, selectedLog: log } = props
  const stale = props.selectedLatestResult?.stale
  if (!nodeRun && !stale) return null
  const manual = props.selectedDefinition?.executor.kind === 'manual_external' || !!nodeRun?.external_handoff || nodeRun?.state === 'waiting_external'
  const error = nodeRun?.error
  const problem = error ? failurePresentation(error.reason) : null
  return <section className="runtime-user-status" aria-label="步骤处理状态">
    <h3>处理状态</h3>
    {nodeRun && <div className={`runtime-status runtime-status--${nodeRun.state}`}>{nodeStateLabel(nodeRun.state)}</div>}
    {stale && <p className="runtime-preserved">当前工程的此步骤需要重新处理：设置、输入或输出有效性已变化。历史文件保留，但不能直接作为当前结果使用。</p>}
    {nodeRun?.reused_from_result_id && <p className="runtime-preserved">已复用上次有效结果，本次没有重复处理。</p>}
    {problem && <div className="runtime-problem"><h4>{problem.title}</h4><p>{problem.cause}</p><p>{problem.preserved}</p><p>{problem.recovery}</p></div>}
    {manual && nodeRun?.state === 'waiting_external' && <p>{elapsedLabel(nodeRun.external_handoff?.created_at ?? nodeRun.started_at ?? nodeRun.created_at)}。请在下方<a href="#external-processing-assistant">外部处理助手</a>完成检查与提交。</p>}
    {!manual && nodeRun && progress && <div className="runtime-progress-detail" aria-label="步骤实测进度">
      {progress.mode === 'determinate' && progress.fraction !== null && <span>{Math.round(progress.fraction * 100)}%</span>}
      {progress.mode === 'indeterminate' && <span>正在处理，暂时没有可计算的百分比。</span>}
      {progress.measurement && progress.measurement.current !== null && progress.measurement.total !== null && <span>{progress.measurement.current} / {progress.measurement.total} {progress.measurement.unit === 'frames' ? '帧' : progress.measurement.unit}</span>}
      {progress.elapsed && <span>{props.advanced ? progress.elapsed : creatorElapsedLabel(progress.elapsed)}</span>}
    </div>}
    {props.selectedOutputs.length > 0 && <section className="inspector-artifacts"><h4>可用输出</h4>{props.selectedOutputs.map((artifact) => <div className="output-path" key={artifact.artifact_id}>
      <strong>{fileName(artifact.path)}</strong>
      <div className="artifact-host-actions"><button disabled={!props.canRevealArtifact} onClick={() => props.onRevealArtifact(artifact.artifact_id)} type="button">在文件夹中显示</button><button disabled={!props.canOpenArtifact} onClick={() => props.onOpenArtifact(artifact.artifact_id)} type="button">播放</button><button onClick={() => props.onCopyPath(artifact.path)} type="button">复制输出路径</button></div>
      {(!props.canRevealArtifact || !props.canOpenArtifact) && <p>本机打开能力未连接，可复制输出路径；使用 ZNIKU launcher 启动可恢复本机能力。</p>}
      <ArtifactMediaSummary artifact={artifact} />
    </div>)}</section>}
    <details className="inspector-runtime-details" open={props.advancedDetailsOpen ?? props.advanced ?? false} onToggle={(event) => props.onToggleDiagnostics?.(event.currentTarget.open)}>
      <summary>高级 → 运行身份与日志</summary>
      {(props.advancedDetailsOpen ?? props.advanced ?? false) && <>
        {nodeRun && <dl className="property-list"><div><dt>Run ID</dt><dd>{nodeRun.run_id}</dd></div><div><dt>NodeRun ID</dt><dd>{nodeRun.node_run_id}</dd></div><div><dt>attempt</dt><dd>{nodeRun.attempt}</dd></div><div><dt>state</dt><dd>{nodeRun.state}</dd></div><div><dt>reused_from_result_id</dt><dd>{nodeRun.reused_from_result_id ?? 'none'}</dd></div></dl>}
        {props.selectedLatestResult?.stale && <pre>{props.selectedLatestResult.stale_reason}</pre>}
        {error && <pre>{error.reason}{'\n'}{error.message}</pre>}
        {props.selectedOutputs.map((artifact) => <p key={artifact.artifact_id}><code>{artifact.artifact_id} · {artifact.producer_port_id} · {artifact.path}</code></p>)}
        {nodeRun && <div className="node-logs">{nodeRun.log_path && <code>{nodeRun.log_path}</code>}{props.logStale && <p>日志通道离线，以下保留最后可信内容。</p>}<h4>stdout{log?.stdout_truncated ? '（尾部截断）' : ''}</h4><pre>{log?.stdout_available ? log.stdout || '（空）' : '（不可用）'}</pre><h4>stderr{log?.stderr_truncated ? '（尾部截断）' : ''}</h4><pre>{log?.stderr_available ? log.stderr || '（空）' : '（不可用）'}</pre></div>}
      </>}
    </details>
  </section>
}
