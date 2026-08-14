import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  type Connection,
  type EdgeMouseHandler,
  type NodeMouseHandler,
} from '@xyflow/react'
import type {
  AuthoringCommand,
  AuthoringIntent,
  Diagnostic,
  PortEndpoint,
} from './generated/authoring-wire.generated'
import { parseAuthoringCommand } from './formal/contracts'
import { FormalWorkflowNodeCard } from './formal/FormalWorkflowNodeCard'
import {
  ExpandedPlanView,
  OperatorPaletteProjection,
  RunMonitorView,
} from './formal/StudioAuthorityViews'
import {
  createWindowAuthoringGateway,
  GatewayUnavailableError,
  type AuthorityState,
  type AuthoringGateway,
} from './formal/gateway'
import { projectAuthorityGraph } from './formal/graph'
import type { FormalWorkflowEdge, FormalWorkflowNode } from './formal/graph-model'

const nodeTypes = { formalWorkflow: FormalWorkflowNodeCard }
const DEFAULT_DRAFT_ID = 'draft.default'

interface AppProps {
  readonly gateway?: AuthoringGateway
  readonly draftId?: string
  readonly commandIdFactory?: () => string
}

function defaultCommandId(): string {
  return `command.studio.${globalThis.crypto.randomUUID()}`
}

function endpointKey(endpoint: PortEndpoint): string {
  return `${endpoint.node_id}|${endpoint.port_id}`
}

function parseEndpoint(value: string): PortEndpoint {
  const [nodeId, portId] = value.split('|')
  return { node_id: nodeId, port_id: portId }
}

interface ParameterSchemaField {
  readonly type?: string
  readonly enum?: ReadonlyArray<unknown>
  readonly minimum?: number
  readonly maximum?: number
}

function parameterFields(schema: Readonly<Record<string, unknown>> | undefined): ReadonlyArray<[string, ParameterSchemaField]> {
  const properties = schema?.properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return []
  return Object.entries(properties).filter(
    (item): item is [string, ParameterSchemaField] =>
      typeof item[1] === 'object' && item[1] !== null && !Array.isArray(item[1]),
  )
}

function AppContent({ gateway, draftId = DEFAULT_DRAFT_ID, commandIdFactory }: AppProps) {
  const effectiveGateway = useMemo(() => gateway ?? createWindowAuthoringGateway(), [gateway])
  const nextCommandId = commandIdFactory ?? defaultCommandId
  const [authority, setAuthority] = useState<AuthorityState | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const [clientHint, setClientHint] = useState<string | null>(null)
  const [commandDiagnostics, setCommandDiagnostics] = useState<ReadonlyArray<Diagnostic>>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [sourceEndpoint, setSourceEndpoint] = useState('')
  const [targetEndpoint, setTargetEndpoint] = useState('')
  const [parameterText, setParameterText] = useState('{}')
  const [bottomOpen, setBottomOpen] = useState(true)
  const [mode, setMode] = useState<'designer' | 'plan' | 'run'>('designer')
  const revisionRef = useRef(-1)

  const projection = useMemo(
    () => (authority ? projectAuthorityGraph(authority) : { nodes: [], edges: [] }),
    [authority],
  )
  const [viewNodes, setViewNodes, onNodesChange] = useNodesState<FormalWorkflowNode>(projection.nodes)

  useEffect(() => {
    setViewNodes(projection.nodes)
  }, [projection.nodes, setViewNodes])

  const acceptAuthority = useCallback((next: AuthorityState) => {
    if (next.snapshot.spec_revision < revisionRef.current) return
    revisionRef.current = next.snapshot.spec_revision
    setAuthority(next)
    setCommandDiagnostics([])
    setUnavailable(null)
  }, [])

  const loadAuthority = useCallback(async () => {
    setLoading(true)
    setClientHint(null)
    try {
      acceptAuthority(await effectiveGateway.loadDraft(draftId))
    } catch (error) {
      const message = error instanceof Error ? error.message : '未知 authoring gateway 错误'
      setUnavailable(message)
      if (!(error instanceof GatewayUnavailableError)) console.error(error)
    } finally {
      setLoading(false)
    }
  }, [acceptAuthority, draftId, effectiveGateway])

  useEffect(() => {
    void loadAuthority()
  }, [loadAuthority])

  const submitIntent = useCallback(
    async (intent: AuthoringIntent) => {
      if (!authority || busy) return
      setBusy(true)
      setClientHint(null)
      const command = parseAuthoringCommand({
        authoring_contract_version: '0.1.0',
        command_id: nextCommandId(),
        draft_id: authority.snapshot.draft_id,
        base_revision: authority.snapshot.spec_revision,
        intent,
      }) as AuthoringCommand
      try {
        const reply = await effectiveGateway.applyCommand(command)
        if ('snapshot' in reply) {
          acceptAuthority(reply)
        } else {
          setCommandDiagnostics(reply.diagnostics)
          if (
            reply.result_kind === 'command_rejected' &&
            reply.current_revision !== undefined &&
            reply.current_revision !== null &&
            reply.current_revision > authority.snapshot.spec_revision
          ) {
            setClientHint('Draft revision 已变化，请刷新 authority 后重试。')
          }
        }
      } catch (error) {
        setUnavailable(error instanceof Error ? error.message : 'Authoring command 失败')
      } finally {
        setBusy(false)
      }
    },
    [acceptAuthority, authority, busy, effectiveGateway, nextCommandId],
  )

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (!connection.sourceHandle || !connection.targetHandle) return
      void submitIntent({
        intent_kind: 'connect_ports',
        source: { node_id: connection.source, port_id: connection.sourceHandle },
        target: { node_id: connection.target, port_id: connection.targetHandle },
      })
    },
    [submitIntent],
  )

  const handleNodeClick: NodeMouseHandler<FormalWorkflowNode> = useCallback((_event, node) => {
    setSelectedNodeId(node.id)
    setSelectedEdgeId(null)
  }, [])

  const handleEdgeClick: EdgeMouseHandler<FormalWorkflowEdge> = useCallback((_event, edge) => {
    setSelectedEdgeId(edge.id)
    setSelectedNodeId(null)
  }, [])

  const selectedNode = projection.nodes.find((node) => node.id === selectedNodeId) ?? null
  const selectedEdge = projection.edges.find((edge) => edge.id === selectedEdgeId) ?? null

  useEffect(() => {
    if (selectedNode?.data.engine) {
      setParameterText(JSON.stringify(selectedNode.data.parameters ?? {}, null, 2))
    }
  }, [selectedNode])

  const outputEndpoints = projection.nodes.flatMap((node) =>
    node.data.outputs.map((port) => ({
      key: endpointKey({ node_id: node.id, port_id: port.portId }),
      label: `${node.data.label}.${port.portId}`,
    })),
  )
  const inputEndpoints = projection.nodes.flatMap((node) =>
    node.data.inputs.map((port) => ({
      key: endpointKey({ node_id: node.id, port_id: port.portId }),
      label: `${node.data.label}.${port.portId}`,
    })),
  )

  useEffect(() => {
    if (!sourceEndpoint && outputEndpoints[0]) setSourceEndpoint(outputEndpoints[0].key)
    if (!targetEndpoint && inputEndpoints[0]) setTargetEndpoint(inputEndpoints[0].key)
  }, [inputEndpoints, outputEndpoints, sourceEndpoint, targetEndpoint])

  const diagnostics = [
    ...(authority?.snapshot.validation.result.diagnostics ?? []),
    ...commandDiagnostics,
  ]

  const locateDiagnostic = (diagnostic: Diagnostic) => {
    const reference = diagnostic.entity_ref
    if ('node_id' in reference) {
      setSelectedNodeId(reference.node_id)
      setSelectedEdgeId(null)
    } else if (reference.kind === 'edge') {
      setSelectedEdgeId(reference.edge_id)
      setSelectedNodeId(null)
    }
  }

  const replaceParameters = () => {
    if (!selectedNode || !selectedNode.data.engine) return
    try {
      const parsed: unknown = JSON.parse(parameterText)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setClientHint('参数必须是 JSON object。')
        return
      }
      void submitIntent({
        intent_kind: 'replace_parameters',
        node_id: selectedNode.id,
        parameters: parsed as Record<string, unknown>,
      })
    } catch {
      setClientHint('参数不是合法 JSON；该输入尚未提交到 Python authority。')
    }
  }

  const updateParameterDraft = (key: string, value: unknown) => {
    try {
      const current = JSON.parse(parameterText) as Record<string, unknown>
      setParameterText(JSON.stringify({ ...current, [key]: value }, null, 2))
    } catch {
      setClientHint('先修复专家 JSON，再使用 Schema 表单。')
    }
  }

  return (
    <main className={`app-shell ${bottomOpen ? 'has-bottom-drawer' : ''}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">ZN</div>
          <div>
            <span className="brand-name">ZNIKU</span>
            <span className="brand-subtitle">Studio</span>
          </div>
        </div>

        <div className="workflow-identity">
          <span className="eyebrow">WORKFLOW AUTHORITY</span>
          <strong>{authority?.snapshot.spec.workflow_id ?? '等待 Python Authoring Service'}</strong>
          <span className="identity-meta">
            {authority
              ? `${authority.snapshot.draft_id} · revision ${authority.snapshot.spec_revision}`
              : draftId}
          </span>
        </div>

        <nav className="mode-tabs" aria-label="工作区视图">
          <button type="button" className={mode === 'designer' ? 'is-active' : ''} onClick={() => setMode('designer')}>Designer</button>
          <button type="button" className={mode === 'plan' ? 'is-active' : ''} onClick={() => setMode('plan')}>Expanded Plan</button>
          <button type="button" className={mode === 'run' ? 'is-active' : ''} onClick={() => setMode('run')}>Run Monitor</button>
        </nav>

        <div className="top-actions">
          <span className={`authority-badge ${unavailable ? 'is-unavailable' : ''}`}>
            {unavailable ? 'AUTHORITY UNAVAILABLE' : 'FORMAL CONTRACT · NO MEDIA I/O'}
          </span>
          <button className="button button--ghost" type="button" onClick={() => void loadAuthority()} disabled={loading || busy}>
            刷新 authority
          </button>
        </div>
      </header>

      <aside className="palette-panel">
        <div className="panel-heading">
          <span className="eyebrow">PYTHON PROJECTION</span>
          <h2>正式节点</h2>
          <span className="registry-state"><i /> {projection.nodes.length} projected</span>
        </div>
        <div className="palette-list formal-node-list">
          {projection.nodes.map((node) => (
            <button
              className={`palette-item palette-item--${node.data.category}`}
              type="button"
              key={node.id}
              onClick={() => setSelectedNodeId(node.id)}
            >
              <span className="palette-icon">{node.data.category === 'engine' ? '◆' : node.data.category === 'source' ? '◉' : '✓'}</span>
              <span><strong>{node.data.label}</strong><small>{node.id}</small></span>
              <em>{node.data.scope}</em>
            </button>
          ))}
        </div>
        <OperatorPaletteProjection />
        <div className="palette-note">
          <span>Phase 5 formal boundary</span>
          <p>Engine 与 Operator typed ports 均来自 Python projection；Canvas 不维护第二套合同。</p>
        </div>
      </aside>

      <section className="canvas-panel" aria-label={mode === 'designer' ? 'Designer 画布' : mode === 'plan' ? 'Expanded Plan 画布' : 'Run Monitor 画布'}>
        <div className="canvas-context">
          <div>
            <span className="context-mode">{mode === 'designer' ? 'Designer' : mode === 'plan' ? 'Expanded Plan' : 'Run Monitor'}</span>
            <strong>{mode === 'designer' ? '正式 Draft · typed ports · Compiler diagnostics' : mode === 'plan' ? '冻结 Plan · chapter expansion · exact Engine binding' : 'fresh snapshot · Evidence-derived state · read only'}</strong>
          </div>
          <div className="canvas-legend">
            <span><i className="legend-dot source" /> Source</span>
            <span><i className="legend-dot engine" /> Engine</span>
            <span><i className="legend-dot operator" /> Operator</span>
            <span><i className="legend-dot final" /> Final</span>
          </div>
        </div>

        {mode === 'plan' ? (
          <ExpandedPlanView />
        ) : mode === 'run' ? (
          <RunMonitorView />
        ) : unavailable && !authority ? (
          <div className="authority-empty" role="alert">
            <strong>Python Authoring authority 不可用</strong>
            <p>{unavailable}</p>
            <p>正式模式不会回退 mock Registry、浏览器内 Compiler、Freeze 或 Run。</p>
          </div>
        ) : (
          <ReactFlow
            nodes={viewNodes}
            edges={projection.edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onConnect={handleConnect}
            onNodeClick={handleNodeClick}
            onEdgeClick={handleEdgeClick}
            nodesDraggable={!busy && mode === 'designer'}
            nodesConnectable={!busy && mode === 'designer'}
            edgesReconnectable={false}
            deleteKeyCode={null}
            fitView
            minZoom={0.3}
            maxZoom={1.8}
            colorMode="dark"
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.1} color="#263344" />
            <Controls position="bottom-left" showInteractive={false} />
            <MiniMap
              position="bottom-right"
              pannable
              zoomable
              nodeColor={(item) => {
                const category = (item.data as FormalWorkflowNode['data']).category
                return { source: '#53a5c9', engine: '#d89b45', operator: '#9c7bd8', final: '#5fb98a' }[category]
              }}
            />
          </ReactFlow>
        )}
      </section>

      <aside className="inspector-panel">
        <div className="panel-heading inspector-heading">
          <span className="eyebrow">INSPECTOR</span>
          <h2>{selectedNode?.data.label ?? (selectedEdge ? 'Data edge' : '未选择实体')}</h2>
          {(selectedNode || selectedEdge) && <code>{selectedNode?.id ?? selectedEdge?.id}</code>}
        </div>

        <div className="inspector-content">
          {selectedNode ? (
            <>
              <section>
                <h3>节点摘要</h3>
                <p>{selectedNode.data.description}</p>
                <dl className="property-list">
                  <div><dt>Category</dt><dd>{selectedNode.data.category}</dd></div>
                  <div><dt>Scope</dt><dd>{selectedNode.data.scope}</dd></div>
                  {selectedNode.data.engine && (
                    <><div><dt>Engine</dt><dd>{selectedNode.data.engine.engine_id}</dd></div><div><dt>Version</dt><dd>{selectedNode.data.engine.engine_version}</dd></div></>
                  )}
                </dl>
              </section>
              <section>
                <h3>Typed ports</h3>
                {[['inputs', selectedNode.data.inputs], ['outputs', selectedNode.data.outputs]].map(([label, ports]) => (
                  <div className="port-group" key={label as string}>
                    <span className="port-group-label">{label as string}</span>
                    {(ports as typeof selectedNode.data.inputs).map((port) => (
                      <div className="port-summary" key={port.portId}>
                        <span>{port.portId}</span>
                        <code>{port.artifactType}/{port.mediaKind ?? 'none'} · {port.scope} · {port.cardinality}</code>
                      </div>
                    ))}
                  </div>
                ))}
              </section>
              {selectedNode.data.engine && (
                <section>
                  <h3>Manifest Schema 参数</h3>
                  <div className="schema-form">
                    {parameterFields(selectedNode.data.parameterSchema).map(([name, field]) => {
                      const current = selectedNode.data.parameters?.[name]
                      if (field.enum) {
                        return <label key={name}>{name}<select value={String(current ?? field.enum[0] ?? '')} onChange={(event) => updateParameterDraft(name, field.type === 'integer' || field.type === 'number' ? Number(event.target.value) : event.target.value)}>{field.enum.map((value) => <option key={String(value)} value={String(value)}>{String(value)}</option>)}</select></label>
                      }
                      if (field.type === 'boolean') {
                        return <label key={name}>{name}<select value={String(current ?? false)} onChange={(event) => updateParameterDraft(name, event.target.value === 'true')}><option value="true">true</option><option value="false">false</option></select></label>
                      }
                      return <label key={name}>{name}<input type={field.type === 'integer' || field.type === 'number' ? 'number' : 'text'} min={field.minimum} max={field.maximum} value={String(current ?? '')} onChange={(event) => updateParameterDraft(name, field.type === 'integer' || field.type === 'number' ? Number(event.target.value) : event.target.value)} /></label>
                    })}
                  </div>
                  <h3>完整参数对象</h3>
                  <textarea aria-label="Engine 参数 JSON" value={parameterText} onChange={(event) => setParameterText(event.target.value)} rows={7} />
                  <button className="button button--primary inspector-action" type="button" onClick={replaceParameters} disabled={busy}>替换参数并验证</button>
                </section>
              )}
            </>
          ) : selectedEdge ? (
            <section>
              <h3>正式 Edge</h3>
              <p>{selectedEdge.source}.{selectedEdge.sourceHandle} → {selectedEdge.target}.{selectedEdge.targetHandle}</p>
              <button className="button button--primary" type="button" disabled={busy} onClick={() => void submitIntent({ intent_kind: 'disconnect_ports', edge_id: selectedEdge.id })}>断开正式 Edge</button>
            </section>
          ) : (
            <div className="empty-inspector">选择 node、edge 或 diagnostic 查看权威引用。</div>
          )}

          {authority && (
            <section className="connection-editor">
              <h3>连接端口</h3>
              <label>Output<select aria-label="Source endpoint" value={sourceEndpoint} onChange={(event) => setSourceEndpoint(event.target.value)}>{outputEndpoints.map((item) => <option value={item.key} key={item.key}>{item.label}</option>)}</select></label>
              <label>Input<select aria-label="Target endpoint" value={targetEndpoint} onChange={(event) => setTargetEndpoint(event.target.value)}>{inputEndpoints.map((item) => <option value={item.key} key={item.key}>{item.label}</option>)}</select></label>
              <button className="button button--primary" type="button" disabled={busy || !sourceEndpoint || !targetEndpoint} onClick={() => void submitIntent({ intent_kind: 'connect_ports', source: parseEndpoint(sourceEndpoint), target: parseEndpoint(targetEndpoint) })}>提交连接</button>
              <div className="authority-edge-list">
                {projection.edges.map((edge) => (
                  <button
                    type="button"
                    key={edge.id}
                    disabled={busy}
                    onClick={() => void submitIntent({ intent_kind: 'disconnect_ports', edge_id: edge.id })}
                  >
                    断开 {edge.id}
                  </button>
                ))}
              </div>
            </section>
          )}
          {clientHint && <p className="client-hint" role="status">{clientHint}</p>}
        </div>
      </aside>

      <section className={`bottom-drawer ${bottomOpen ? 'is-open' : ''}`}>
        <button className="drawer-toggle" type="button" onClick={() => setBottomOpen((open) => !open)}>
          <span>Python Diagnostics</span><strong>{diagnostics.length}</strong><i>{bottomOpen ? '收起' : '展开'}</i>
        </button>
        {bottomOpen && (
          <div className="diagnostic-list">
            {diagnostics.length === 0 ? <div className="diagnostic-empty">{authority ? 'authoring_valid · 无阻塞诊断' : '等待 Python authority · 尚无正式诊断'}</div> : diagnostics.map((diagnostic) => (
              <article className={`diagnostic diagnostic--${diagnostic.severity}`} key={diagnostic.diagnostic_id}>
                <span className="diagnostic-icon">{diagnostic.severity === 'error' ? '×' : diagnostic.severity === 'warning' ? '!' : 'i'}</span>
                <div><span className="diagnostic-code">{diagnostic.stable_code}</span><strong>{diagnostic.phase}</strong><p>{diagnostic.message}</p></div>
                <button type="button" onClick={() => locateDiagnostic(diagnostic)}>定位</button>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}

export function App(props: AppProps) {
  return <ReactFlowProvider><AppContent {...props} /></ReactFlowProvider>
}
