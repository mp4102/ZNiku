/**
 * 展示选中节点/连接的表单与运行投影。ParameterDraft 由 Workspace session 持有，
 * 本组件只发出表单、raw JSON、应用或放弃意图。
 */

import type { ReactNode } from 'react'
import { ArtifactMediaSummary, HandoffContract, HandoffPrecheckFailure, ReadinessMessages } from '../HandoffContract'
import { SchemaParameterForm } from '../SchemaParameterForm'
import type { ParameterPickerRequest } from '../SchemaParameterForm'
import type {
  ArtifactWire,
  EdgeWire,
  ExternalHandoffReadiness,
  JsonObject,
  NodeDefinitionWire,
  NodeLogWire,
  NodePresentationWire,
  NodeRunWire,
  RunDetailEnvelope,
} from '../contracts'
import { edgeId } from '../graph'
import { asParameterSchema, type ParameterDraftValidation } from '../parameter-draft'
import { handoffResourceKey, readinessLabel } from './HandoffCenter'
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
  readonly onValidateAndSubmit: (nodeRun: NodeRunWire) => void
  readonly onReorderEdge: (edgeId: string, ordinal: number) => void
  readonly onDeleteEdge: () => void
  readonly onAbandonRun: () => void
}
export function NodeInspector(props: NodeInspectorProps) {
  const {
    selectedNode, selectedDefinition, selectedPresentation, selectedEdge,
    parameterDraft, parameterText, parameterDirty, parameterRawError, parameterValidation,
    graphEditable, busy, selectedNodeRun, selectedProgress, selectedLog, logStale,
    readinessStale, mutationBlocked, selectedOutputs, handoffInputs, readiness, detail,
    lastFullPrecheckFailure, handoffCenter, actionableRun, actionableRunIsRunning,
    clientHint, boundaryError,
  } = props
  return (
    <aside className="inspector-panel">
      <div className="panel-heading inspector-heading">
        <span className="eyebrow">INSPECTOR</span>
        <h2>{selectedNode ? props.nodeLabel?.(selectedNode.node_id) ?? selectedPresentation?.title ?? selectedNode.node_id : selectedEdge ? '连接设置' : '未选择实体'}</h2>
        {props.advanced && (selectedNode || selectedEdge) && <code>{selectedNode ? `${selectedNode.type_id}@${selectedNode.definition_version}` : edgeId(selectedEdge!)}</code>}
      </div>
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
          {selectedNodeRun && (
            <section aria-label="Runtime details">
              <h3>Runtime · attempt {selectedNodeRun.attempt}</h3>
              <div className={`runtime-status runtime-status--${selectedNodeRun.state}`}>{selectedNodeRun.state}{selectedNodeRun.progress !== null ? ` · ${Math.round(selectedNodeRun.progress * 100)}%` : ''}{selectedNodeRun.reused_from_result_id ? ' · reused' : ''}</div>
              {selectedNodeRun.error && <p className="runtime-error">{selectedNodeRun.error.reason}<br />{selectedNodeRun.error.message}</p>}
              {selectedOutputs.map((artifact) => <div className="output-path" key={artifact.artifact_id}><span>{artifact.producer_port_id}</span><code>{artifact.path}</code><div className="artifact-host-actions"><button disabled={!props.canRevealArtifact} onClick={() => props.onRevealArtifact(artifact.artifact_id)} type="button">在文件夹中显示</button><button disabled={!props.canOpenArtifact} onClick={() => props.onOpenArtifact(artifact.artifact_id)} type="button">播放</button></div></div>)}
              {selectedNodeRun.external_handoff && (
                <div className="handoff-panel">
                  <strong>External handoff</strong>
                  {selectedNodeRun.external_handoff.instructions && <p>{selectedNodeRun.external_handoff.instructions}</p>}
                  <span>Inputs</span>
                  {handoffInputs.map((path, index) => <div className="handoff-path" key={`${selectedNodeRun.external_handoff!.input_artifact_ids[index]}-${index}`}><code>{path}</code><button type="button" onClick={() => props.onCopyPath(path)}>Copy input path</button></div>)}
                  <span>Targets</span>
                  {selectedNodeRun.external_handoff.output_targets.map((target) => <div className="handoff-path" key={`${target.port_id}-${target.ordinal ?? 'one'}`}><code>{target.port_id}{target.ordinal === null ? '' : ` #${target.ordinal}`} · {target.path}</code><button type="button" onClick={() => props.onCopyPath(target.path)}>Copy target path</button></div>)}
                  <div className="readiness-state">Readiness · {readinessLabel(readiness)}</div>
                  <button className="button button--primary inspector-action" type="button" disabled={mutationBlocked || readinessStale || selectedNodeRun.state !== 'waiting_external' || !readiness || readiness.targets.some((target) => target.state !== 'present' && target.state !== 'probe_passed')} onClick={() => props.onValidateAndSubmit(selectedNodeRun)}>Validate and submit</button>
                </div>
              )}
              {(selectedLog || selectedNodeRun.log_path) && <div className="node-logs">{selectedNodeRun.log_path && <code>{selectedNodeRun.log_path}</code>}<h4>stdout{selectedLog?.stdout_truncated ? '（尾部截断）' : ''}</h4><pre>{logStale ? '（日志通道离线，保留最后可信内容）' : selectedLog?.stdout_available ? selectedLog.stdout || '（空）' : '（不可用）'}</pre><h4>stderr{selectedLog?.stderr_truncated ? '（尾部截断）' : ''}</h4><pre>{logStale ? '（日志通道离线，保留最后可信内容）' : selectedLog?.stderr_available ? selectedLog.stderr || '（空）' : '（不可用）'}</pre></div>}
            </section>
          )}
          {selectedNodeRun && detail?.handoff_contracts.filter((contract) => contract.node_run_id === selectedNodeRun.node_run_id).map((contract) => <HandoffContract key={contract.node_run_id} contract={contract} />)}
          {selectedNodeRun?.external_handoff && <ReadinessMessages readiness={readiness} />}
          {selectedNodeRun?.external_handoff && <HandoffPrecheckFailure failure={lastFullPrecheckFailure} />}
          {selectedOutputs.map((artifact) => <ArtifactMediaSummary key={artifact.artifact_id} artifact={artifact} />)}
          {selectedNodeRun && selectedProgress && (selectedProgress.mode === 'indeterminate' || selectedProgress.measurement !== null || selectedProgress.elapsed !== null) && (
            <div className="runtime-progress-detail" aria-label="Runtime progress details">
              {selectedProgress.mode === 'indeterminate' && <span>indeterminate</span>}
              {selectedProgress.measurement && <span>{selectedProgress.measurement.current} / {selectedProgress.measurement.total} {selectedProgress.measurement.unit}</span>}
              {selectedProgress.elapsed && <span>{selectedProgress.elapsed}</span>}
            </div>
          )}
        </div>
      ) : selectedEdge ? (
        <div className="inspector-content"><section><h3>连接</h3><p>{props.nodeLabel?.(selectedEdge.source_node_id) ?? selectedEdge.source_node_id} → {props.nodeLabel?.(selectedEdge.target_node_id) ?? selectedEdge.target_node_id}</p>{props.advanced && selectedEdge.ordinal !== null && <label className="ordinal-editor">Ordinal<input aria-label="Edge ordinal" type="number" min={0} value={selectedEdge.ordinal} disabled={!graphEditable} onChange={(event) => props.onReorderEdge(edgeId(selectedEdge), Number(event.target.value))} /></label>}<button className="button button--danger inspector-action" type="button" disabled={busy || !graphEditable} onClick={props.onDeleteEdge}>删除所选连接</button></section></div>
      ) : <div className="empty-inspector">选择节点或连接查看配置、运行状态、日志和输出。</div>}
      {handoffCenter}
      {actionableRun && <button className="button button--danger abandon-run" type="button" disabled={mutationBlocked || actionableRunIsRunning} onClick={props.onAbandonRun}>Abandon Run</button>}
      {clientHint && <p className="client-hint" role="status">{clientHint}</p>}
      {boundaryError && <p className="client-hint client-hint--error" role="alert">{boundaryError}</p>}
    </aside>
  )
}
