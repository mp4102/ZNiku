/**
 * 实现 ZNIKU 0.2.0 的单一正式 Studio 工作区。
 *
 * Designer 直接编辑 Project Service 返回的普通 Graph；Runtime 状态只叠加在同一画布上。浏览器不生成
 * Compiler、Freeze、ExecutionPlan 或媒体结果，所有保存、运行、日志和 external handoff 都调用 Python
 * authority。网络或合同错误不会回退到本地模拟数据。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Connection,
  type EdgeChange,
  type EdgeMouseHandler,
  type NodeChange,
  type NodeMouseHandler,
  type OnSelectionChangeParams,
} from '@xyflow/react'
import { WorkflowNodeCard } from '../components/WorkflowNodeCard'
import type { WorkflowEdge, WorkflowNode, WorkflowNodeData } from '../model'
import type {
  EdgeWire,
  GraphWire,
  JsonObject,
  NodeDefinitionWire,
  NodeInstanceWire,
  NodeRunWire,
  ProjectSnapshotWire,
  RunWire,
  StudioCommand,
  StudioEnvelope,
} from './contracts'
import {
  connectGraph,
  copySelection,
  defaultParameters,
  definitionForNode,
  deleteSelection,
  edgeId,
  inspectGraph,
  isStudioConnectionValid,
  reorderEdge,
} from './graph'
import { createStudioGateway, type StudioGateway } from './gateway'
import { groupStudioDefinitions } from './catalog'

const nodeTypes = { workflow: WorkflowNodeCard }

export interface StudioWorkspaceProps {
  readonly gateway?: StudioGateway
  readonly nodeIdFactory?: () => string
}

function defaultNodeId(): string {
  return `node.${globalThis.crypto.randomUUID()}`
}

function replaceGraph(snapshot: ProjectSnapshotWire, graph: GraphWire): ProjectSnapshotWire {
  return {
    ...snapshot,
    project: {
      ...snapshot.project,
      graph,
    },
  }
}

function latestNodeRuns(run: RunWire | null): Map<string, NodeRunWire> {
  const values = new Map<string, NodeRunWire>()
  for (const nodeRun of run?.node_runs ?? []) {
    const current = values.get(nodeRun.node_id)
    if (!current || current.attempt < nodeRun.attempt) values.set(nodeRun.node_id, nodeRun)
  }
  return values
}

function selectedRun(envelope: StudioEnvelope | null): RunWire | null {
  if (!envelope) return null
  return (
    envelope.runs.find((run) => run.run_id === envelope.active_run_id) ?? envelope.runs[0] ?? null
  )
}

function isEditingTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  )
}

function parameterObject(value: string): JsonObject | null {
  try {
    const parsed: unknown = JSON.parse(value)
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? (parsed as JsonObject)
      : null
  } catch {
    return null
  }
}

export function StudioWorkspace({ gateway, nodeIdFactory = defaultNodeId }: StudioWorkspaceProps) {
  const effectiveGateway = useMemo(() => gateway ?? createStudioGateway(), [gateway])
  const [envelope, setEnvelope] = useState<StudioEnvelope | null>(null)
  const [draft, setDraft] = useState<ProjectSnapshotWire | null>(null)
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [boundaryError, setBoundaryError] = useState<string | null>(null)
  const [clientHint, setClientHint] = useState<string | null>(null)
  const [projectPath, setProjectPath] = useState('')
  const [projectId, setProjectId] = useState('project.local')
  const [projectName, setProjectName] = useState('ZNIKU Project')
  const [query, setQuery] = useState('')
  const [selectedNodeIds, setSelectedNodeIds] = useState<ReadonlySet<string>>(new Set())
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<ReadonlySet<string>>(new Set())
  const [parameterText, setParameterText] = useState('{}')
  const [bottomOpen, setBottomOpen] = useState(true)

  const acceptEnvelope = useCallback(
    (next: StudioEnvelope, options: { readonly replaceProject: boolean }) => {
      setEnvelope(next)
      if (next.project_path !== null) setProjectPath(next.project_path)
      if (options.replaceProject) {
        setDraft(next.snapshot)
        setDirty(false)
        setSelectedNodeIds(new Set())
        setSelectedEdgeIds(new Set())
        if (next.snapshot) {
          setProjectId(next.snapshot.project.project_id)
          setProjectName(next.snapshot.project.name)
        }
      }
      setBoundaryError(null)
      setClientHint(next.error ? `${next.error.code}: ${next.error.message}` : null)
    },
    [],
  )

  const refresh = useCallback(
    async (replaceProject: boolean) => {
      try {
        acceptEnvelope(await effectiveGateway.inspect(), { replaceProject })
      } catch (error) {
        setBoundaryError(error instanceof Error ? error.message : 'Project Service inspect 失败')
      }
    },
    [acceptEnvelope, effectiveGateway],
  )

  useEffect(() => {
    let active = true
    setLoading(true)
    effectiveGateway
      .inspect()
      .then((next) => {
        if (active) acceptEnvelope(next, { replaceProject: true })
      })
      .catch((error: unknown) => {
        if (active) setBoundaryError(error instanceof Error ? error.message : 'Project Service inspect 失败')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [acceptEnvelope, effectiveGateway])

  const currentRun = selectedRun(envelope)
  const operationActive = typeof envelope?.active_operation === 'string'
  const shouldPoll = operationActive

  useEffect(() => {
    if (!shouldPoll) return
    const timer = window.setInterval(() => void refresh(false), 750)
    return () => window.clearInterval(timer)
  }, [refresh, shouldPoll])

  const invoke = useCallback(
    async (command: StudioCommand, replaceProject = true): Promise<StudioEnvelope | null> => {
      setBusy(true)
      setBoundaryError(null)
      setClientHint(null)
      try {
        const next = await effectiveGateway.command(command)
        acceptEnvelope(next, { replaceProject })
        return next
      } catch (error) {
        setBoundaryError(error instanceof Error ? error.message : 'Project Service command 失败')
        return null
      } finally {
        setBusy(false)
      }
    },
    [acceptEnvelope, effectiveGateway],
  )

  const updateGraph = useCallback((updater: (graph: GraphWire) => GraphWire) => {
    setDraft((current) => (current ? replaceGraph(current, updater(current.project.graph)) : current))
    setDirty(true)
    setClientHint(null)
  }, [])

  const activeNodeRuns = useMemo(() => latestNodeRuns(currentRun), [currentRun])
  const latestResults = useMemo(
    () => new Map((envelope?.latest_results ?? []).map((result) => [result.node_id, result])),
    [envelope?.latest_results],
  )
  const artifactsById = useMemo(
    () => new Map((envelope?.artifacts ?? []).map((artifact) => [artifact.artifact_id, artifact])),
    [envelope?.artifacts],
  )
  const definitions = draft?.definitions ?? []
  const graph = draft?.project.graph ?? { nodes: [], edges: [] }
  const diagnostics = useMemo(() => (draft ? inspectGraph(draft) : []), [draft])
  const definitionsByKey = useMemo(
    () => new Map(definitions.map((definition) => [`${definition.type_id}@${definition.version}`, definition])),
    [definitions],
  )

  const flowNodes = useMemo<WorkflowNode[]>(
    () =>
      graph.nodes.flatMap((node, index) => {
        const definition = definitionsByKey.get(`${node.type_id}@${node.definition_version}`)
        if (!definition) return []
        const data: WorkflowNodeData = {
          label: node.node_id,
          typeId: node.type_id,
          definitionVersion: node.definition_version,
          executorKind: definition.executor.kind,
          inputs: definition.input_ports,
          outputs: definition.output_ports,
          nodeRun: activeNodeRuns.get(node.node_id) ?? null,
          latestResult: latestResults.get(node.node_id) ?? null,
        }
        return [
          {
            id: node.node_id,
            type: 'workflow',
            position: node.ui_position ?? { x: 80 + (index % 4) * 250, y: 100 + Math.floor(index / 4) * 190 },
            selected: selectedNodeIds.has(node.node_id),
            data,
          },
        ]
      }),
    [activeNodeRuns, definitionsByKey, graph.nodes, latestResults, selectedNodeIds],
  )

  const flowEdges = useMemo<WorkflowEdge[]>(
    () =>
      graph.edges.map((edge) => ({
        id: edgeId(edge),
        type: 'smoothstep',
        source: edge.source_node_id,
        sourceHandle: edge.source_port_id,
        target: edge.target_node_id,
        targetHandle: edge.target_port_id,
        selected: selectedEdgeIds.has(edgeId(edge)),
        label: edge.ordinal === null ? undefined : `#${edge.ordinal}`,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        data: { ordinal: edge.ordinal },
      })),
    [graph.edges, selectedEdgeIds],
  )

  const selectedNode =
    graph.nodes.find((node) => selectedNodeIds.has(node.node_id)) ?? null
  const selectedDefinition = selectedNode ? definitionForNode(selectedNode, definitions) : null
  const selectedEdge =
    graph.edges.find((edge) => selectedEdgeIds.has(edgeId(edge))) ?? null
  const selectedNodeRun = selectedNode ? activeNodeRuns.get(selectedNode.node_id) ?? null : null
  const selectedLog = selectedNodeRun
    ? envelope?.logs.find((log) => log.node_run_id === selectedNodeRun.node_run_id) ?? null
    : null
  const selectedOutputs = selectedNodeRun
    ? selectedNodeRun.output_artifact_ids.flatMap((artifactId) => {
        const artifact = artifactsById.get(artifactId)
        return artifact ? [artifact] : []
      })
    : []
  const handoffInputs = selectedNodeRun?.external_handoff
    ? selectedNodeRun.external_handoff.input_artifact_ids.flatMap((artifactId) => {
        const artifact = artifactsById.get(artifactId)
        return artifact ? [artifact.path] : [`未解析 Artifact：${artifactId}`]
      })
    : []

  useEffect(() => {
    setParameterText(JSON.stringify(selectedNode?.parameters ?? {}, null, 2))
  }, [selectedNode])

  const handleSelection = useCallback((selection: OnSelectionChangeParams) => {
    setSelectedNodeIds(new Set(selection.nodes.map((node) => node.id)))
    setSelectedEdgeIds(new Set(selection.edges.map((edge) => edge.id)))
  }, [])

  const handleNodeClick: NodeMouseHandler<WorkflowNode> = useCallback((event, node) => {
    if (event.ctrlKey || event.metaKey) {
      setSelectedNodeIds((current) => {
        const next = new Set(current)
        if (next.has(node.id)) next.delete(node.id)
        else next.add(node.id)
        return next
      })
    } else {
      setSelectedNodeIds(new Set([node.id]))
      setSelectedEdgeIds(new Set())
    }
  }, [])

  const handleEdgeClick: EdgeMouseHandler<WorkflowEdge> = useCallback((event, edge) => {
    if (event.ctrlKey || event.metaKey) {
      setSelectedEdgeIds((current) => {
        const next = new Set(current)
        if (next.has(edge.id)) next.delete(edge.id)
        else next.add(edge.id)
        return next
      })
    } else {
      setSelectedEdgeIds(new Set([edge.id]))
      setSelectedNodeIds(new Set())
    }
  }, [])

  const handleNodesChange = useCallback(
    (changes: NodeChange<WorkflowNode>[]) => {
      const positions = new Map(
        changes.flatMap((change) =>
          change.type === 'position' && change.position ? [[change.id, change.position] as const] : [],
        ),
      )
      const removed = new Set(
        changes.flatMap((change) => (change.type === 'remove' ? [change.id] : [])),
      )
      if (positions.size > 0) {
        updateGraph((current) => ({
          ...current,
          nodes: current.nodes.map((node) =>
            positions.has(node.node_id)
              ? { ...node, ui_position: positions.get(node.node_id)! }
              : node,
          ),
        }))
      }
      if (removed.size > 0) {
        updateGraph((current) => deleteSelection(current, removed, new Set()))
      }
    },
    [updateGraph],
  )

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<WorkflowEdge>[]) => {
      const removed = new Set(
        changes.flatMap((change) => (change.type === 'remove' ? [change.id] : [])),
      )
      if (removed.size > 0) updateGraph((current) => deleteSelection(current, new Set(), removed))
    },
    [updateGraph],
  )

  const handleConnect = useCallback(
    (connection: Connection) => {
      const next = connectGraph(connection, graph, definitions)
      if (!next) {
        setClientHint('连接被拒绝：请检查精确 data_type、cardinality、占用状态与 cycle。')
        return
      }
      updateGraph(() => next)
    },
    [definitions, graph, updateGraph],
  )

  const connectionIsValid = useCallback(
    (connection: Connection | WorkflowEdge) =>
      isStudioConnectionValid(
        {
          source: connection.source,
          sourceHandle: connection.sourceHandle ?? null,
          target: connection.target,
          targetHandle: connection.targetHandle ?? null,
        },
        graph,
        definitions,
      ),
    [definitions, graph],
  )

  const addDefinition = useCallback(
    (definition: NodeDefinitionWire) => {
      const existing = new Set(graph.nodes.map((node) => node.node_id))
      let nodeId = nodeIdFactory()
      while (existing.has(nodeId)) nodeId = nodeIdFactory()
      const node: NodeInstanceWire = {
        node_id: nodeId,
        type_id: definition.type_id,
        definition_version: definition.version,
        parameters: defaultParameters(definition),
        ui_position: { x: 120 + graph.nodes.length * 42, y: 120 + graph.nodes.length * 28 },
      }
      updateGraph((current) => ({ ...current, nodes: [...current.nodes, node] }))
      setSelectedNodeIds(new Set([nodeId]))
      setSelectedEdgeIds(new Set())
    },
    [graph.nodes, nodeIdFactory, updateGraph],
  )

  const copySelected = useCallback(() => {
    if (selectedNodeIds.size === 0) return
    const copied = copySelection(graph, selectedNodeIds, nodeIdFactory)
    updateGraph(() => copied.graph)
    setSelectedNodeIds(copied.copied_node_ids)
    setSelectedEdgeIds(new Set())
  }, [graph, nodeIdFactory, selectedNodeIds, updateGraph])

  const deleteSelected = useCallback(() => {
    if (selectedNodeIds.size === 0 && selectedEdgeIds.size === 0) return
    updateGraph((current) => deleteSelection(current, selectedNodeIds, selectedEdgeIds))
    setSelectedNodeIds(new Set())
    setSelectedEdgeIds(new Set())
  }, [selectedEdgeIds, selectedNodeIds, updateGraph])

  useEffect(() => {
    const handleKeyboard = (event: KeyboardEvent) => {
      if (isEditingTarget(event.target)) return
      if ((event.key === 'Delete' || event.key === 'Backspace') && (selectedNodeIds.size || selectedEdgeIds.size)) {
        event.preventDefault()
        deleteSelected()
      } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd' && selectedNodeIds.size) {
        event.preventDefault()
        copySelected()
      }
    }
    window.addEventListener('keydown', handleKeyboard)
    return () => window.removeEventListener('keydown', handleKeyboard)
  }, [copySelected, deleteSelected, selectedEdgeIds.size, selectedNodeIds.size])

  const applyParameters = useCallback(() => {
    if (!selectedNode) return
    const parameters = parameterObject(parameterText)
    if (!parameters) {
      setClientHint('参数必须是合法 JSON object；未修改 Project Draft。')
      return
    }
    updateGraph((current) => ({
      ...current,
      nodes: current.nodes.map((node) =>
        node.node_id === selectedNode.node_id ? { ...node, parameters } : node,
      ),
    }))
  }, [parameterText, selectedNode, updateGraph])

  const createProject = () =>
    void invoke({
      operation: 'create_project',
      path: projectPath.trim(),
      project_id: projectId.trim(),
      name: projectName.trim(),
    })

  const openProject = () =>
    void invoke({ operation: 'open_project', path: projectPath.trim() })

  const saveProject = useCallback(async (): Promise<StudioEnvelope | null> => {
    if (!draft) return null
    return invoke({ operation: 'save_project', project: draft.project })
  }, [draft, invoke])

  const saveThenRun = useCallback(
    async (command: StudioCommand) => {
      if (!draft) return
      setBusy(true)
      setBoundaryError(null)
      setClientHint(null)
      try {
        const saved = await effectiveGateway.command({ operation: 'save_project', project: draft.project })
        acceptEnvelope(saved, { replaceProject: true })
        if (saved.error) return
        const next = await effectiveGateway.command(command)
        acceptEnvelope(next, { replaceProject: true })
      } catch (error) {
        setBoundaryError(error instanceof Error ? error.message : 'Runtime command 失败')
      } finally {
        setBusy(false)
      }
    },
    [acceptEnvelope, draft, effectiveGateway],
  )

  const catalogGroups = groupStudioDefinitions(definitions, query)
  const singleSelectedNodeId = selectedNodeIds.size === 1 ? selectedNode?.node_id ?? null : null
  const rerunId = currentRun?.run_id ?? null
  const rerunNodeIncluded = singleSelectedNodeId
    ? activeNodeRuns.has(singleSelectedNodeId)
    : false
  const serviceBusy = busy || operationActive
  const runBlocked = serviceBusy || !draft || diagnostics.length > 0

  return (
    <main className={`app-shell studio-workspace ${bottomOpen ? 'has-bottom-drawer' : ''}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">ZN</div>
          <div>
            <span className="brand-name">ZNIKU</span>
            <span className="brand-subtitle">Studio</span>
          </div>
        </div>

        <div className="workflow-identity">
          <span className="eyebrow">PROJECT GRAPH</span>
          <strong>{draft?.project.name ?? '打开或新建 .zniku 工程'}</strong>
          <span className="identity-meta">
            {draft ? `${draft.project.project_id} · ${graph.nodes.length} nodes · ${dirty ? '未保存' : '已保存'}` : '0.2.0 Project Service authority'}
          </span>
        </div>

        <div className="project-location">
          <input
            aria-label="工程路径"
            value={projectPath}
            onChange={(event) => setProjectPath(event.target.value)}
            placeholder="D:\\Projects\\example.zniku"
          />
          <button className="button button--ghost" type="button" disabled={serviceBusy || !projectPath.trim()} onClick={openProject}>打开</button>
          <button className="button button--ghost" type="button" disabled={serviceBusy || !projectPath.trim() || !projectId.trim() || !projectName.trim()} onClick={createProject}>新建</button>
          <button className="button button--ghost" type="button" disabled={serviceBusy || !draft || diagnostics.length > 0 || !dirty} onClick={() => void saveProject()}>保存</button>
        </div>

        <div className="top-actions">
          <span className={`authority-badge ${boundaryError ? 'is-unavailable' : ''}`}>
            {boundaryError ? 'SERVICE UNAVAILABLE' : envelope?.active_operation ? envelope.active_operation.toUpperCase() : 'PROJECT SERVICE'}
          </span>
          <button className="button button--primary" type="button" disabled={runBlocked} onClick={() => void saveThenRun({ operation: 'run_all' })}>Run all</button>
          <button className="button button--ghost" type="button" disabled={runBlocked || !singleSelectedNodeId} onClick={() => singleSelectedNodeId && void saveThenRun({ operation: 'run_to', node_id: singleSelectedNodeId })}>Run to here</button>
          <button className="button button--ghost" type="button" disabled={runBlocked || !singleSelectedNodeId || !rerunId || !rerunNodeIncluded} onClick={() => singleSelectedNodeId && rerunId && void saveThenRun({ operation: 'rerun_from_here', run_id: rerunId, node_id: singleSelectedNodeId })}>Rerun from here</button>
        </div>
      </header>

      <aside className="palette-panel">
        <div className="panel-heading">
          <span className="eyebrow">NODE DEFINITIONS</span>
          <h2>节点面板</h2>
          <span className="registry-state"><i /> {definitions.length} exact versions</span>
        </div>
        <div className="project-fields">
          <label>Project ID<input aria-label="Project ID" value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label>
          <label>Project name<input aria-label="Project name" value={projectName} onChange={(event) => setProjectName(event.target.value)} /></label>
        </div>
        <label className="search-box">
          <span>⌕</span>
          <input aria-label="搜索节点" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="type、port、mode 或 executor" />
        </label>
        <div className="palette-list" aria-label="节点定义列表">
          {catalogGroups.map((group) => (
            <section className="palette-group" aria-label={group.label} key={group.id}>
              <header>
                <span><strong>{group.label}</strong><small>{group.description}</small></span>
                <em>{group.entries.length}</em>
              </header>
              {group.entries.map(({ definition, role }) => (
                <button className={`palette-item palette-item--${definition.executor.kind}`} type="button" key={`${definition.type_id}@${definition.version}`} disabled={!draft || busy} onClick={() => addDefinition(definition)}>
                  <span className="palette-icon">{definition.executor.kind === 'manual_external' ? 'ME' : definition.executor.kind === 'command' ? 'CM' : 'PY'}</span>
                  <span><strong>{definition.type_id}</strong><small>{role} · {definition.execution_mode} · {definition.input_ports.length} in / {definition.output_ports.length} out</small></span>
                  <em>{definition.version}</em>
                </button>
              ))}
            </section>
          ))}
          {catalogGroups.length === 0 && <p className="palette-empty">没有匹配的 exact definition。</p>}
        </div>
        <div className="selection-actions">
          <button className="button button--ghost" type="button" disabled={selectedNodeIds.size === 0 || busy} onClick={copySelected}>复制所选</button>
          <button className="button button--danger" type="button" disabled={(selectedNodeIds.size === 0 && selectedEdgeIds.size === 0) || busy} onClick={deleteSelected}>删除所选</button>
        </div>
        <div className="palette-note">
          <span>自由 DAG</span>
          <p>节点与 presets 来自当前 .zniku 的 Python catalog。拖动框选可多选；端口只按精确 data_type 与 cardinality 连接。</p>
        </div>
      </aside>

      <section className="canvas-panel" aria-label="Studio Designer 画布">
        <div className="canvas-context">
          <div><span className="context-mode">Designer + Runtime</span><strong>{currentRun ? `${currentRun.run_id} · ${currentRun.state}` : '编辑与运行使用同一张 Graph'}</strong></div>
          <div className="canvas-legend"><span><i className="legend-dot source" /> automatic</span><span><i className="legend-dot engine" /> manual external</span><span><i className="legend-dot final" /> completed</span><span><i className="legend-dot operator" /> stale / failed</span></div>
        </div>
        {loading ? (
          <div className="authority-empty" role="status"><strong>正在连接 Project Service…</strong></div>
        ) : boundaryError && !draft ? (
          <div className="authority-empty" role="alert"><strong>Project Service 不可用</strong><p>{boundaryError}</p><p>Studio 不会回退浏览器内存数据。</p></div>
        ) : !draft ? (
          <div className="authority-empty"><strong>尚未打开工程</strong><p>输入本地 .zniku 路径后选择“打开”或“新建”。</p></div>
        ) : (
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onSelectionChange={handleSelection}
            onNodeClick={handleNodeClick}
            onEdgeClick={handleEdgeClick}
            onConnect={handleConnect}
            isValidConnection={connectionIsValid}
            nodesDraggable={!busy}
            nodesConnectable={!busy}
            edgesReconnectable={false}
            deleteKeyCode={null}
            selectionOnDrag
            multiSelectionKeyCode={['Control', 'Meta']}
            fitView
            minZoom={0.2}
            maxZoom={1.8}
            colorMode="dark"
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.1} color="#263344" />
            <Controls position="bottom-left" showInteractive={false} />
            <MiniMap position="bottom-right" pannable zoomable nodeColor="#d89b45" />
          </ReactFlow>
        )}
      </section>

      <aside className="inspector-panel">
        <div className="panel-heading inspector-heading">
          <span className="eyebrow">INSPECTOR</span>
          <h2>{selectedNode?.node_id ?? (selectedEdge ? 'Data edge' : '未选择实体')}</h2>
          {(selectedNode || selectedEdge) && <code>{selectedNode ? `${selectedNode.type_id}@${selectedNode.definition_version}` : edgeId(selectedEdge!)}</code>}
        </div>
        {selectedNode && selectedDefinition ? (
          <div className="inspector-content">
            <section>
              <h3>Node binding</h3>
              <dl className="property-list"><div><dt>type_id</dt><dd>{selectedNode.type_id}</dd></div><div><dt>version</dt><dd>{selectedNode.definition_version}</dd></div><div><dt>executor</dt><dd>{selectedDefinition.executor.kind}</dd></div></dl>
            </section>
            <section>
              <h3>Typed ports</h3>
              {(['input_ports', 'output_ports'] as const).map((direction) => (
                <div className="port-group" key={direction}>
                  <span className="port-group-label">{direction}</span>
                  {selectedDefinition[direction].length ? selectedDefinition[direction].map((port) => (
                    <div className="port-summary" key={port.port_id}><span>{port.port_id}</span><code>{port.data_type} · {port.cardinality}{port.required ? ' · required' : ''}</code></div>
                  )) : <div className="port-empty">none</div>}
                </div>
              ))}
            </section>
            <section>
              <h3>Parameters</h3>
              <textarea aria-label="节点参数 JSON" value={parameterText} onChange={(event) => setParameterText(event.target.value)} rows={8} />
              <button className="button button--primary inspector-action" type="button" disabled={busy} onClick={applyParameters}>应用参数到 Draft</button>
              <details><summary>parameter_schema</summary><pre>{JSON.stringify(selectedDefinition.parameter_schema, null, 2)}</pre></details>
            </section>
            {selectedNodeRun && (
              <section aria-label="Runtime details">
                <h3>Runtime · attempt {selectedNodeRun.attempt}</h3>
                <div className={`runtime-status runtime-status--${selectedNodeRun.state}`}>{selectedNodeRun.state}{selectedNodeRun.progress !== null ? ` · ${Math.round(selectedNodeRun.progress * 100)}%` : ''}</div>
                {selectedNodeRun.error && <p className="runtime-error">{selectedNodeRun.error.reason}<br />{selectedNodeRun.error.message}</p>}
                {selectedOutputs.map((artifact) => <div className="output-path" key={artifact.artifact_id}><span>{artifact.producer_port_id}</span><code>{artifact.path}</code></div>)}
                {selectedNodeRun.external_handoff && (
                  <div className="handoff-panel">
                    <strong>External handoff</strong>
                    {selectedNodeRun.external_handoff.instructions && <p>{selectedNodeRun.external_handoff.instructions}</p>}
                    <span>Inputs</span>{handoffInputs.map((path, index) => <code key={`${selectedNodeRun.external_handoff!.input_artifact_ids[index]}-${index}`}>{path}</code>)}
                    <span>Targets</span>{selectedNodeRun.external_handoff.output_targets.map((target) => <code key={`${target.port_id}-${target.ordinal ?? 'one'}`}>{target.port_id}{target.ordinal === null ? '' : ` #${target.ordinal}`} · {target.path}</code>)}
                    <button className="button button--primary inspector-action" type="button" disabled={serviceBusy || selectedNodeRun.state !== 'waiting_external'} onClick={() => void invoke({ operation: 'submit_external', node_run_id: selectedNodeRun.node_run_id }, false)}>Submit external output</button>
                  </div>
                )}
                {(selectedLog || selectedNodeRun.log_path) && (
                  <div className="node-logs">
                    {selectedNodeRun.log_path && <code>{selectedNodeRun.log_path}</code>}
                    <h4>stdout{selectedLog?.stdout_truncated ? '（尾部截断）' : ''}</h4><pre>{selectedLog?.stdout_available ? selectedLog.stdout || '（空）' : '（不可用）'}</pre>
                    <h4>stderr{selectedLog?.stderr_truncated ? '（尾部截断）' : ''}</h4><pre>{selectedLog?.stderr_available ? selectedLog.stderr || '（空）' : '（不可用）'}</pre>
                  </div>
                )}
              </section>
            )}
          </div>
        ) : selectedEdge ? (
          <div className="inspector-content">
            <section><h3>Edge</h3><p>{selectedEdge.source_node_id}.{selectedEdge.source_port_id} → {selectedEdge.target_node_id}.{selectedEdge.target_port_id}</p>
              {selectedEdge.ordinal !== null && <label className="ordinal-editor">Ordinal<input aria-label="Edge ordinal" type="number" min={0} value={selectedEdge.ordinal} onChange={(event) => updateGraph((current) => reorderEdge(current, edgeId(selectedEdge), Number(event.target.value)))} /></label>}
              <button className="button button--danger inspector-action" type="button" onClick={deleteSelected}>删除所选连接</button>
            </section>
          </div>
        ) : <div className="empty-inspector">选择节点或连接查看配置、运行状态、日志和输出。</div>}
        {clientHint && <p className="client-hint" role="status">{clientHint}</p>}
        {boundaryError && draft && <p className="client-hint client-hint--error" role="alert">{boundaryError}</p>}
      </aside>

      <section className={`bottom-drawer ${bottomOpen ? 'is-open' : ''}`}>
        <button className="drawer-toggle" type="button" onClick={() => setBottomOpen((open) => !open)}><span>Graph diagnostics</span><strong>{diagnostics.length + (envelope?.error ? 1 : 0)}</strong><i>{bottomOpen ? '收起' : '展开'}</i></button>
        {bottomOpen && <div className="diagnostic-list">
          {diagnostics.length === 0 && !envelope?.error ? <div className="diagnostic-empty">graph_valid · 可保存和运行</div> : diagnostics.map((diagnostic) => (
            <article className="diagnostic diagnostic--error" key={`${diagnostic.code}-${diagnostic.node_id ?? diagnostic.edge_id ?? 'graph'}`}><span className="diagnostic-icon">×</span><div><span className="diagnostic-code">{diagnostic.code}</span><strong>Graph Core</strong><p>{diagnostic.message}</p></div><button type="button" onClick={() => { if (diagnostic.node_id) setSelectedNodeIds(new Set([diagnostic.node_id])); if (diagnostic.edge_id) setSelectedEdgeIds(new Set([diagnostic.edge_id])) }}>定位</button></article>
          ))}
          {envelope?.error && <article className="diagnostic diagnostic--error"><span className="diagnostic-icon">×</span><div><span className="diagnostic-code">{envelope.error.code}</span><strong>Project Service</strong><p>{envelope.error.message}</p></div></article>}
        </div>}
      </section>
    </main>
  )
}
