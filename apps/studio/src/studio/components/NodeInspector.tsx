/**
 * 用稳定受控页签展示选中对象；ParameterDraft、页签与执行资格由 Workspace session 持有。
 * 文件页保持挂载，避免切页销毁收件控制器；诊断按需展示，不因高级模式读取日志或发出命令。
 */

import { useId, type KeyboardEvent, type ReactNode } from 'react'
import './inspector-workspace.css'
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

export type InspectorTab = 'settings' | 'files' | 'diagnostics'

export interface NodeInspectorProps {
  readonly tab: InspectorTab
  readonly onTabChange: (tab: InspectorTab) => void
  readonly selectedNodeCount?: number
  readonly nodeActions?: ReactNode
  readonly serviceDiagnostics?: ReactNode
  readonly advanced?: boolean
  readonly authoringPanel?: ReactNode
  readonly mediaPreview?: ReactNode
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
  const id = useId()
  const { selectedNode, selectedPresentation, selectedEdge, clientHint, boundaryError, tab } = props
  const selectedCount = props.selectedNodeCount ?? (selectedNode ? 1 : 0)
  const singleNode = selectedCount === 1 && selectedNode !== null
  const hasSettings = singleNode && props.selectedDefinition !== null && props.parameterValidation !== null
  const tabs: ReadonlyArray<readonly [InspectorTab, string]> = [
    ['settings', '设置'], ['files', '文件'], ['diagnostics', '诊断'],
  ]
  const moveTabFocus = (event: KeyboardEvent<HTMLButtonElement>) => {
    const buttons = [...(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? [])]
    const index = buttons.indexOf(event.currentTarget)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : event.key === 'ArrowRight' ? (index + 1) % buttons.length
        : event.key === 'ArrowLeft' ? (index - 1 + buttons.length) % buttons.length : null
    if (next === null) return
    event.preventDefault()
    // 手动激活页签：方向键只移动焦点，Enter/Space/点击才发出显式导航意图。
    buttons[next]?.focus()
  }
  return (
    <aside className="inspector-panel inspector-workspace" aria-label="步骤设置与输出" id="node-inspector" tabIndex={-1}>
      <div className="panel-heading inspector-heading">
        <h2>{selectedCount > 1 ? `已选择 ${selectedCount} 个步骤` : selectedNode ? props.nodeLabel?.(selectedNode.node_id) ?? selectedPresentation?.title ?? selectedNode.type_id : selectedEdge ? '连接设置' : '步骤详情'}</h2>
        {!props.graphEditable && <p className="inspector-readonly">正在查看处理记录，只读。返回当前编辑后可修改设置。</p>}
        {props.parameterDirty && <p className="inspector-draft-notice">参数尚未应用；切换页签会保留更改。</p>}
      </div>
      <div className="inspector-tabs" role="tablist" aria-label="步骤面板页签">
        {tabs.map(([value, label]) => <button key={value} type="button" role="tab"
          id={`${id}-tab-${value}`} aria-controls={`${id}-panel-${value}`} aria-selected={tab === value}
          tabIndex={tab === value ? 0 : -1} onKeyDown={moveTabFocus}
          onClick={() => { if (tab !== value) props.onTabChange(value) }}>{label}</button>)}
      </div>
      <div className="inspector-workspace-scroll">
        {singleNode && <NodeRuntimeSummary {...props} />}
        {clientHint && <p className="client-hint" role="status" aria-label="操作提示">{clientHint}</p>}
        {boundaryError && <p className="client-hint client-hint--error" role="alert">这次操作未能完成。当前工作与已有文件仍然保留，请查看任务区的问题提示后重试。</p>}
        {props.parameterRawError && tab !== 'diagnostics' && <p className="inspector-parameter-error" role="alert">{props.parameterRawError} <button type="button" onClick={() => props.onTabChange('diagnostics')}>查看原始参数</button></p>}
        <div role="tabpanel" id={`${id}-panel-settings`} aria-labelledby={`${id}-tab-settings`} hidden={tab !== 'settings'} className="inspector-tab-panel inspector-content">
          {hasSettings ? <>
            {selectedPresentation && <p className="node-presentation-description">{selectedPresentation.description}</p>}
            <section className="inspector-processing-settings"><h3>处理参数</h3>
              <SchemaParameterForm schema={asParameterSchema(props.selectedDefinition!.parameter_schema)}
                draft={props.parameterDraft} validation={props.parameterValidation!} presentation={selectedPresentation}
                readOnly={!props.graphEditable || props.busy} onPickPath={props.onPickParameterPath}
                onPickError={props.onParameterPickerError} onChange={props.onParameterDraftChange} />
            </section>
          </> : selectedEdge && selectedCount < 2 ? <EdgeSettings {...props} /> : <SelectionHint count={selectedCount} hasNode={singleNode} />}
          {singleNode && props.nodeActions && <section className="inspector-node-actions" aria-label="步骤操作">{props.nodeActions}</section>}
          {(singleNode || selectedEdge) && selectedCount < 2 && props.orderedInputs?.map((input) => <section className="inspector-ordered-inputs" key={input.label}><h3>{input.label}</h3><OrderedInputList edges={input.edges} nodeLabel={props.nodeLabel ?? ((nodeId) => nodeId)} portLabel={props.sourcePortLabel ?? ((_nodeId, portId) => portId)} disabled={props.busy || !props.graphEditable} onReorder={props.onReorderEdge} /></section>)}
          {props.authoringPanel && <details className="inspector-display-settings"><summary>显示设置</summary><p>别名、分组和折叠只影响外观，不改变处理参数或已有结果。</p>{props.authoringPanel}</details>}
        </div>
        {/* 不能按 tab 条件卸载：HandoffInbox 持有当前候选/确认状态，文件页只是可见性变化。 */}
        <div role="tabpanel" id={`${id}-panel-files`} aria-labelledby={`${id}-tab-files`} hidden={tab !== 'files'} className="inspector-tab-panel inspector-files">
          {selectedCount > 1 ? <SelectionHint count={selectedCount} /> : <>
            {(selectedNode === null || props.selectedNodeRun?.state === 'waiting_external') && props.handoffCenter}
            {singleNode && <NodeFiles {...props} />}
            {selectedNode === null && !props.handoffCenter && <p>选择一个步骤，查看它的输入、文件任务和输出。</p>}
          </>}
        </div>
        <div role="tabpanel" id={`${id}-panel-diagnostics`} aria-labelledby={`${id}-tab-diagnostics`} hidden={tab !== 'diagnostics'} className="inspector-tab-panel inspector-content inspector-diagnostics">
          {tab === 'diagnostics' && <>
            <p className="inspector-diagnostic-warning">诊断可能包含本机路径。复制分享前请检查内容；这里不会自动上传文件或日志。</p>
            {singleNode ? <NodeDiagnostics {...props} errorId={`${id}-parameter-raw-error`} /> : selectedEdge && selectedCount < 2 ? <pre>{JSON.stringify(selectedEdge, null, 2)}</pre> : <SelectionHint count={selectedCount} />}
            {boundaryError && <section><h3>操作原始详情</h3><pre>{boundaryError}</pre></section>}
            {props.serviceDiagnostics}
          </>}
        </div>
      </div>
      {hasSettings && <footer className="parameter-draft-actions inspector-apply-bar" hidden={tab !== 'settings'} aria-label="参数应用操作">
        <span className={props.parameterValidation!.valid && !props.parameterRawError ? 'is-valid' : 'is-invalid'}>
          {props.parameterRawError ? '原始参数尚有错误，请到诊断页修正。' : props.parameterValidation!.valid ? props.parameterDirty ? '更改尚未应用' : '设置有效' : `${props.parameterValidation!.errors.length} 个问题待修复`}
        </span>
        <button className="button button--primary inspector-action" type="button" disabled={props.busy || !props.graphEditable || !props.parameterDirty || !!props.parameterRawError || !props.parameterValidation!.valid} onClick={props.onApplyParameters}>应用设置</button>
        <button className="button button--ghost inspector-action" type="button" disabled={!props.parameterDirty} onClick={props.onDiscardParameters}>放弃未应用更改</button>
      </footer>}
    </aside>
  )
}

function SelectionHint({ count, hasNode = false }: { readonly count: number; readonly hasNode?: boolean }) {
  return <p className="empty-inspector">{count > 1 ? '多选时不批量覆盖处理参数。选择一个步骤查看设置和文件；共同外观可在显示设置中调整。'
    : hasNode ? '当前步骤的设置暂不可用，请查看问题提示。' : '选择一个步骤或连接，查看设置、处理状态和文件。'}</p>
}

function EdgeSettings(props: NodeInspectorProps) {
  const edge = props.selectedEdge!
  return <section><h3>连接</h3>
    <p>{props.nodeLabel?.(edge.source_node_id) ?? edge.source_node_id} → {props.nodeLabel?.(edge.target_node_id) ?? edge.target_node_id}</p>
    <p>输出：{props.sourcePortLabel?.(edge.source_node_id, edge.source_port_id) ?? edge.source_port_id} → 输入：{edge.target_port_id}</p>
    {edge.ordinal !== null && <p>合并顺序：第 {edge.ordinal + 1} 项。调整下方输入列表，不会根据画布位置自动排序。</p>}
    {props.advanced && edge.ordinal !== null && <label className="ordinal-editor">Ordinal<input aria-label="Edge ordinal" type="number" min={0} value={edge.ordinal} disabled={props.busy || !props.graphEditable} onChange={(event) => props.onReorderEdge(edgeId(edge), Number(event.target.value))} /></label>}
    <button className="button button--danger inspector-action" type="button" disabled={props.busy || !props.graphEditable} onClick={props.onDeleteEdge}>删除所选连接</button>
  </section>
}

/** 正常层保留可信摘要；手工节点不展示任何传入百分比或外部工具 ETA。 */
function NodeRuntimeSummary(props: NodeInspectorProps) {
  const { selectedNodeRun: nodeRun, selectedProgress: progress } = props
  const stale = props.selectedLatestResult?.stale
  if (!nodeRun && !stale) return null
  const manual = props.selectedDefinition?.executor.kind === 'manual_external' || !!nodeRun?.external_handoff || nodeRun?.state === 'waiting_external'
  const error = nodeRun?.error
  const problem = error ? failurePresentation(error.reason) : null
  return <section className="runtime-user-status" aria-label="步骤处理状态">
    {nodeRun && <div className={`runtime-status runtime-status--${nodeRun.state}`}>{nodeStateLabel(nodeRun.state)}</div>}
    {stale && <p className="runtime-preserved">当前工程的此步骤需要重新处理：设置、输入或输出有效性已变化。历史文件保留，但不能直接作为当前结果使用。</p>}
    {nodeRun?.reused_from_result_id && <p className="runtime-preserved">已复用上次有效结果，本次没有重复处理。</p>}
    {problem && <div className="runtime-problem"><h4>{problem.title}</h4><p>{problem.cause}</p><p>{problem.preserved}</p><p>{problem.recovery}</p></div>}
    {manual && nodeRun?.state === 'waiting_external' && <p>{elapsedLabel(nodeRun.external_handoff?.created_at ?? nodeRun.started_at ?? nodeRun.created_at)}。{props.tab === 'files' ? '请在文件页完成检查与提交。' : <button type="button" onClick={() => props.onTabChange('files')}>查看外部文件</button>}</p>}
    {!manual && nodeRun && progress && <div className="runtime-progress-detail" aria-label="步骤实测进度">
      {progress.mode === 'determinate' && progress.fraction !== null && <span>{Math.round(progress.fraction * 100)}%</span>}
      {progress.mode === 'indeterminate' && <span>正在处理，暂时没有可计算的百分比。</span>}
      {progress.measurement && progress.measurement.current !== null && progress.measurement.total !== null && <span>{progress.measurement.current} / {progress.measurement.total} {progress.measurement.unit === 'frames' ? '帧' : progress.measurement.unit}</span>}
      {progress.elapsed && <span>{props.advanced ? progress.elapsed : creatorElapsedLabel(progress.elapsed)}</span>}
    </div>}
    {props.tab !== 'files' && props.selectedOutputs.length > 0 && <button type="button" onClick={() => props.onTabChange('files')}>查看输出（{props.selectedOutputs.length}）</button>}
  </section>
}

function NodeFiles(props: NodeInspectorProps) {
  const manual = props.selectedNodeRun?.state === 'waiting_external'
  return <>
    {!manual && props.handoffInputs.length > 0 && <section><h3>输入文件</h3>{props.handoffInputs.map((path, index) => <p key={`${index}:${path}`}>{fileName(path)}</p>)}</section>}
    {props.mediaPreview}
    {props.selectedOutputs.length > 0 ? <section className="inspector-artifacts"><h3>已登记输出</h3>{props.selectedOutputs.map((artifact) => <div className="output-path" key={artifact.artifact_id}>
      <strong>{fileName(artifact.path)}</strong>
      <div className="artifact-host-actions"><button disabled={!props.canRevealArtifact} onClick={() => props.onRevealArtifact(artifact.artifact_id)} type="button">在文件夹中显示</button><button disabled={!props.canOpenArtifact} onClick={() => props.onOpenArtifact(artifact.artifact_id)} type="button">播放</button><button onClick={() => props.onCopyPath(artifact.path)} type="button">复制输出路径</button></div>
      {(!props.canRevealArtifact || !props.canOpenArtifact) && <p>本机打开能力未连接。请从 ZNIKU Studio 桌面入口启动后重试；高级操作仍可复制输出路径。</p>}
      <ArtifactMediaSummary artifact={artifact} />
    </div>)}</section> : !manual && <p>当前步骤没有已登记的输出文件。没有输出不代表处理失败，请以步骤状态为准。</p>}
  </>
}

function NodeDiagnostics(props: NodeInspectorProps & { readonly errorId: string }) {
  const { selectedNode: node, selectedDefinition: definition, selectedNodeRun: nodeRun, selectedLog: log } = props
  const error = nodeRun?.error
  return <>
    {node && definition && <>
      <details className="parameter-raw-json">
        <summary>高级 → 原始参数</summary>
        <p>这里编辑的是同一份设置草稿；返回设置页后显式应用。</p>
        <textarea aria-label="节点参数 JSON" aria-invalid={!!props.parameterRawError} aria-describedby={props.parameterRawError ? props.errorId : undefined} value={props.parameterText} onChange={(event) => props.onParameterTextChange(event.target.value)} rows={10} readOnly={!props.graphEditable || props.busy} />
        {props.parameterRawError && <p id={props.errorId} role="alert">{props.parameterRawError}</p>}
        <button type="button" onClick={() => props.onTabChange('settings')}>返回设置</button>
      </details>
      <details><summary>高级 → parameter_schema</summary><pre>{JSON.stringify(definition.parameter_schema, null, 2)}</pre></details>
      <details className="node-binding-details"><summary>高级 → 节点合同</summary>
        <dl className="property-list"><div><dt>node_id</dt><dd>{node.node_id}</dd></div><div><dt>type_id</dt><dd>{node.type_id}</dd></div><div><dt>version</dt><dd>{node.definition_version}</dd></div><div><dt>executor</dt><dd>{definition.executor.kind}</dd></div></dl>
        <h3>Typed ports</h3>{(['input_ports', 'output_ports'] as const).map((direction) => <div className="port-group" key={direction}><span className="port-group-label">{direction}</span>{definition[direction].length ? definition[direction].map((port) => <div className="port-summary" key={port.port_id}><span>{port.port_id}</span><code>{port.data_type} · {port.cardinality}{port.required ? ' · required' : ''}</code></div>) : <p>none</p>}</div>)}
      </details>
    </>}
    <section className="inspector-runtime-details" aria-label="运行身份与日志">
      <h3>运行身份与日志</h3>
        {nodeRun && <dl className="property-list"><div><dt>Run ID</dt><dd>{nodeRun.run_id}</dd></div><div><dt>NodeRun ID</dt><dd>{nodeRun.node_run_id}</dd></div><div><dt>attempt</dt><dd>{nodeRun.attempt}</dd></div><div><dt>state</dt><dd>{nodeRun.state}</dd></div><div><dt>reused_from_result_id</dt><dd>{nodeRun.reused_from_result_id ?? 'none'}</dd></div></dl>}
        {props.selectedLatestResult?.stale && <pre>{props.selectedLatestResult.stale_reason}</pre>}
        {error && <pre>{error.reason}{'\n'}{error.message}</pre>}
        {props.selectedOutputs.map((artifact) => <p key={artifact.artifact_id}><code>{artifact.artifact_id} · {artifact.producer_port_id} · {artifact.path}</code></p>)}
        {nodeRun && <div className="node-logs">{nodeRun.log_path && <code>{nodeRun.log_path}</code>}{props.logStale && <p>日志通道离线，以下保留最后可信内容。</p>}<h4>stdout{log?.stdout_truncated ? '（尾部截断）' : ''}</h4><pre>{log?.stdout_available ? log.stdout || '（空）' : '（不可用）'}</pre><h4>stderr{log?.stderr_truncated ? '（尾部截断）' : ''}</h4><pre>{log?.stderr_available ? log.stderr || '（空）' : '（不可用）'}</pre></div>}
      {!nodeRun && <p>当前没有绑定的处理记录；不会猜测其他步骤的日志。</p>}
    </section>
  </>
}
