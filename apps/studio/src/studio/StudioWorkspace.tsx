/**
 * 编排 ZNIKU 0.3.0 单一正式 Studio 工作区的 authority 状态与组件边界。
 *
 * 打开工程默认编辑 Project 当前 Graph；只有显式查看或发起运行时才进入 Run snapshot。status、Run
 * detail、readiness 与日志分别从 Python authority 读取，并通过 generation/sequence 丢弃迟到响应。
 * 页面只投影状态，不生成第二套 Run、Artifact、进度或人工交接 authority。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  MarkerType,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type EdgeMouseHandler,
  type NodeChange,
  type NodeMouseHandler,
  type OnSelectionChangeParams,
} from '@xyflow/react'
import { AvEnhanceV27Wizard, type AvEnhanceV27WizardMode } from './AvEnhanceV27Wizard'
import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
  WorkflowProgressData,
} from '../model'
import type {
  ArtifactWire,
  AuthoringPrecondition,
  AvEnhanceV27ExpandRequestWire,
  AvEnhanceV27PrepareRequestWire,
  AvEnhanceV27PublicationPreviewRequestWire,
  AvEnhanceV27TemplatePreviewEnvelope,
  AvEnhanceV27TemplatePreviewRequestWire,
  EdgeWire,
  ExternalHandoffReadiness,
  GraphWire,
  JsonObject,
  NodeDefinitionWire,
  NodeInstanceWire,
  NodeLogEnvelope,
  NodeProgressProjectionWire,
  NodePresentationWire,
  PresentationCatalogEnvelopeWire,
  NodeRunWire,
  ProjectSnapshotWire,
  RunDetailEnvelope,
  RunSummaryWire,
  RerunPreviewEnvelope,
  RerunPreviewRequest,
  StatusEnvelope,
  StudioCommand,
  StudioStateWire,
  StudioServiceError,
} from './contracts'
import {
  addConnectedNodes,
  autoLayoutGraph,
  connectGraph,
  copySelection,
  defaultParameters,
  definitionForNode,
  deleteSelection,
  edgeId,
  inspectGraph,
  isStudioConnectionValid,
  nodeExecutionSignatureMatches,
  reorderEdge,
  selectionChangeBlocked,
} from './graph'
import { useAuthoringProject } from './use-authoring-project'
import { createStudioGateway, StudioGatewayError, type StudioGateway } from './gateway'
import {
  createHostBridge,
  readDesktopPreferences,
  type HostBridge,
  type HostCapabilitiesEnvelope,
  type HostDialogCapability,
  type HostPathReference,
  type HostSelection,
  type HostSystemCapability,
} from './host-bridge'
import { formatHostBridgeError } from './host-error-presentation'
import { isFullCheck, readinessMatchesHandoff, sameObservedOutputs } from './handoff-check'
import { distinctNodeLabels, handoffSummary } from './handoff-presentation'
import { useHandoffImport } from './use-handoff-import'
import { readRecentProjects, rememberRecentProject } from './recent-projects'
import type { ParameterPickerRequest } from './SchemaParameterForm'
import { groupStudioDefinitions } from './catalog'
import { asParameterSchema, canonicalJsonKey, getPointer, validateParameterDraft } from './parameter-draft'
import { DiagnosticsPanel } from './components/DiagnosticsPanel'
import { AuthoringViewPanel } from './components/AuthoringViewPanel'
import { GraphCanvas } from './components/GraphCanvas'
import { HandoffCenter, elapsedLabel, handoffResourceKey, readinessLabel } from './components/HandoffCenter'
import { NodeInspector } from './components/NodeInspector'
import { MediaPreview, type PreviewCandidate } from './components/MediaPreview'
import { DesktopExit } from './components/DesktopExit'
import { NodePalette } from './components/NodePalette'
import { ProjectHome } from './components/ProjectHome'
import { ProjectShell } from './components/ProjectShell'
import { RunCanvasOverlays, RunCenter, targetLabel } from './components/RunCenter'
import { RetryImpactDialog } from './components/RetryImpactDialog'
const failureBackoff = [750, 1_500, 3_000, 5_000] as const

// 仅决定能否继续展示“模板已就绪”标签，不进入 Run 绑定、执行或存储版本。
const graphPresentationComparison = (graph: GraphWire) => JSON.stringify({
  edges: graph.edges,
  nodes: graph.nodes.map(({ node_id, type_id, definition_version, parameters }) => ({ node_id, type_id, definition_version, parameters: canonicalJsonKey(parameters) })),
})

function sameWireData(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value, index) => sameWireData(value, right[index]))
  const a = left as Record<string, unknown>
  const b = right as Record<string, unknown>
  const keys = Object.keys(a)
  return keys.length === Object.keys(b).length && keys.every((key) => Object.hasOwn(b, key) && sameWireData(a[key], b[key]))
}

type ChannelName = 'status' | 'detail' | 'readiness' | 'log'
type ResourceChannelName = Exclude<ChannelName, 'status'>

interface ChannelHealth {
  readonly stale: boolean
  readonly lastSuccess: string | null
}

const initialHealth: Record<ChannelName, ChannelHealth> = {
  status: { stale: false, lastSuccess: null },
  detail: { stale: false, lastSuccess: null },
  readiness: { stale: false, lastSuccess: null },
  log: { stale: false, lastSuccess: null },
}

type ResourceHealth = Record<ResourceChannelName, ReadonlyMap<string, ChannelHealth>>

interface ReadinessProbeFlight {
  readonly generation: number
  readonly token: symbol
  readonly promise: Promise<ExternalHandoffReadiness | null>
}

interface DetailFlight {
  readonly generation: number
  readonly token: symbol
  readonly authorityRevision: string
  readonly promise: Promise<RunDetailEnvelope | null>
}

interface DetailBackoff {
  readonly failureIndex: number
  readonly retryAt: number
}

function nodeRunResourceKey(runId: string, nodeRunId: string): string {
  return `${runId}/${nodeRunId}`
}

function emptyResourceHealth(): ResourceHealth {
  return {
    detail: new Map(),
    readiness: new Map(),
    log: new Map(),
  }
}

function aggregateResourceHealth(
  values: ReadonlyMap<string, ChannelHealth>,
  resourceKeys: ReadonlyArray<string>,
): ChannelHealth {
  const observed = resourceKeys.flatMap((key) => {
    const value = values.get(key)
    return value ? [value] : []
  })
  const successes = observed
    .map((value) => value.lastSuccess)
    .filter((value): value is string => value !== null)
    .sort()
  return {
    stale: observed.some((value) => value.stale),
    lastSuccess: successes.at(-1) ?? null,
  }
}

export interface StudioWorkspaceProps {
  readonly gateway?: StudioGateway
  readonly hostBridge?: HostBridge
  readonly nodeIdFactory?: () => string
  readonly projectIdFactory?: () => string
}

function defaultNodeId(): string {
  return `node.${globalThis.crypto.randomUUID()}`
}

function pathLeaf(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? '已选择'
}

function safeVisibleServiceError(message: string, fallback: string): string {
  return /(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/])/.test(message) ? fallback : message
}

function cardSummaryValue(
  value: unknown,
  presentation: NodePresentationWire['parameters'][number] | undefined,
): string {
  const enumLabel = presentation?.enum_labels.find((entry) => JSON.stringify(entry.value) === JSON.stringify(value))
  if (enumLabel) return enumLabel.label
  if (
    presentation?.control_hint === 'file_path' ||
    presentation?.control_hint === 'save_file' ||
    presentation?.control_hint === 'directory_path'
  ) {
    return typeof value === 'string' ? pathLeaf(value) : '已选择'
  }
  if (presentation?.control_hint === 'file_paths') {
    if (!Array.isArray(value)) return '已选择文件'
    const names = value.flatMap((item) => typeof item === 'string' ? [pathLeaf(item)] : [])
    if (names.length === 0) return '尚未选择'
    return names.length <= 2 ? names.join('、') : `${names.slice(0, 2).join('、')} 等 ${names.length} 个文件`
  }
  return typeof value === 'string' ? value : JSON.stringify(value) ?? '—'
}

function replaceGraph(snapshot: ProjectSnapshotWire, graph: GraphWire): ProjectSnapshotWire {
  return {
    ...snapshot,
    project: { ...snapshot.project, graph },
  }
}

function latestNodeRuns(run: RunDetailEnvelope['run'] | null): Map<string, NodeRunWire> {
  const values = new Map<string, NodeRunWire>()
  for (const nodeRun of run?.node_runs ?? []) {
    const current = values.get(nodeRun.node_id)
    if (!current || current.attempt < nodeRun.attempt) values.set(nodeRun.node_id, nodeRun)
  }
  return values
}

function sortSummaries(values: ReadonlyArray<RunSummaryWire>): RunSummaryWire[] {
  return [...values].sort((left, right) => {
    const time = right.created_at.localeCompare(left.created_at)
    return time !== 0 ? time : right.run_id.localeCompare(left.run_id)
  })
}

function mergeSummaries(
  ...groups: ReadonlyArray<ReadonlyArray<RunSummaryWire>>
): RunSummaryWire[] {
  const byId = new Map<string, RunSummaryWire>()
  for (const group of groups) for (const summary of group) byId.set(summary.run_id, summary)
  return sortSummaries([...byId.values()])
}

function defaultRunId(summaries: ReadonlyArray<RunSummaryWire>): string | null {
  return summaries.find((summary) => summary.actionable)?.run_id ?? summaries[0]?.run_id ?? null
}

function summaryRevision(summary: RunSummaryWire | undefined): string {
  if (!summary) return ''
  return JSON.stringify([
    summary.run_id,
    summary.state,
    summary.state_counts,
    summary.latest_activity_at,
    summary.error,
  ])
}

function summaryAuthorityRevision(summary: RunSummaryWire | undefined): string {
  if (!summary) return ''
  return JSON.stringify([
    summary.run_id,
    summary.state,
    summary.state_counts,
    summary.error,
  ])
}

function statusPollDelay(status: StatusEnvelope | null): number {
  if (!status) return 5_000
  if (
    status.active_operation !== null ||
    status.run_summaries.some((summary) => summary.state_counts.running > 0)
  ) {
    return 750
  }
  return status.run_summaries.some((summary) => summary.actionable) ? 1_500 : 5_000
}

function detailPollDelay(summary: RunSummaryWire | undefined): number | null {
  if (!summary) return 5_000
  if (isTerminal(summary)) return null
  return summary.state_counts.running > 0 ? 750 : 1_500
}

function runtimeElapsedLabel(nodeRun: NodeRunWire): string | null {
  if (!nodeRun.started_at) return null
  const started = Date.parse(nodeRun.started_at)
  const ended = nodeRun.ended_at ? Date.parse(nodeRun.ended_at) : Date.now()
  if (!Number.isFinite(started) || !Number.isFinite(ended)) return null
  const seconds = Math.max(0, Math.floor((ended - started) / 1_000))
  if (seconds < 60) return `elapsed ${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `elapsed ${minutes}m ${seconds % 60}s`
  return `elapsed ${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

function nodeProgressView(
  nodeRun: NodeRunWire | null,
  definition: NodeDefinitionWire,
  projection: NodeProgressProjectionWire | null,
): WorkflowProgressData {
  if (!nodeRun) return { mode: 'none', fraction: null, measurement: null, elapsed: null }
  const elapsed = runtimeElapsedLabel(nodeRun)
  const supportsDeterminateProgress =
    definition.execution_mode === 'automatic' && definition.executor.kind === 'python'
  if (nodeRun.state === 'completed' || nodeRun.reused_from_result_id !== null) {
    return { mode: 'completed', fraction: null, measurement: null, elapsed }
  }
  if (nodeRun.state === 'waiting_external' || nodeRun.state === 'pending') {
    return { mode: 'none', fraction: null, measurement: null, elapsed }
  }
  if (nodeRun.state === 'running') {
    if (supportsDeterminateProgress && projection) {
      return {
        mode: 'determinate',
        fraction: projection.fraction,
        measurement: projection.current === null ? null : projection,
        elapsed,
      }
    }
    if (supportsDeterminateProgress && nodeRun.progress !== null) {
      return { mode: 'determinate', fraction: nodeRun.progress, measurement: null, elapsed }
    }
    return { mode: 'indeterminate', fraction: null, measurement: null, elapsed }
  }
  if (
    nodeRun.state === 'failed' &&
    supportsDeterminateProgress &&
    nodeRun.progress !== null
  ) {
    return { mode: 'determinate', fraction: nodeRun.progress, measurement: null, elapsed }
  }
  return { mode: 'none', fraction: null, measurement: null, elapsed }
}

function isTerminal(summary: RunSummaryWire | undefined): boolean {
  return summary?.state === 'completed' || summary?.state === 'failed'
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

function monotonicDetail(
  previous: RunDetailEnvelope | null,
  incoming: RunDetailEnvelope,
): { readonly detail: RunDetailEnvelope; readonly regressed: boolean } {
  if (!previous || previous.run.run_id !== incoming.run.run_id) {
    return { detail: incoming, regressed: false }
  }
  const trusted = new Map(previous.run.node_runs.map((item) => [item.node_run_id, item]))
  const trustedSamples = new Map(
    previous.progress_samples.map((item) => [item.node_run_id, item]),
  )
  const incomingSamples = new Map(
    incoming.progress_samples.map((item) => [item.node_run_id, item]),
  )
  let regressed = false
  const nodeRuns = incoming.run.node_runs.map((item) => {
    const prior = trusted.get(item.node_run_id)
    if (prior?.attempt !== item.attempt) return item
    let nextItem = item
    if (
      prior.progress !== null &&
      (item.state === 'running' || item.state === 'failed') &&
      (item.progress === null || item.progress < prior.progress)
    ) {
      regressed = true
      nextItem = { ...item, progress: prior.progress }
    }
    // completed/reused 已进入新的终态语义；此时 reporter projection 按合同必须消失，
    // 不能把正常的 100% 完成转换误判为回退并触发无限定向重查。
    if (item.state !== 'running' && item.state !== 'failed') return nextItem
    const priorSample = trustedSamples.get(item.node_run_id)
    const nextSample = incomingSamples.get(item.node_run_id)
    const priorFraction = priorSample?.fraction ?? prior.progress
    const nextFraction = nextSample?.fraction ?? nextItem.progress
    if (priorFraction === null || (nextFraction !== null && nextFraction >= priorFraction)) {
      return nextItem
    }

    regressed = true
    if (item.state === 'running' && priorSample?.fraction === priorFraction) {
      incomingSamples.set(item.node_run_id, priorSample)
      return nextItem
    }
    incomingSamples.delete(item.node_run_id)
    return { ...item, progress: priorFraction }
  })
  const progressSamples = incoming.progress_samples.flatMap((item) => {
    const trustedItem = incomingSamples.get(item.node_run_id)
    return trustedItem ? [trustedItem] : []
  })
  for (const [nodeRunId, sample] of incomingSamples) {
    if (!progressSamples.some((item) => item.node_run_id === nodeRunId)) progressSamples.push(sample)
  }
  return {
    regressed,
    detail: regressed
      ? {
          ...incoming,
          run: { ...incoming.run, node_runs: nodeRuns },
          progress_samples: progressSamples,
        }
      : incoming,
  }
}

export function StudioWorkspace({
  gateway,
  hostBridge,
  nodeIdFactory = defaultNodeId,
  projectIdFactory,
}: StudioWorkspaceProps) {
  const effectiveGateway = useMemo(() => gateway ?? createStudioGateway(), [gateway])
  const authoring = useAuthoringProject(effectiveGateway)
  const { ingest: ingestAuthoring, edit: editAuthoring, begin: beginMove, end: endMove, travel, flush: flushAuthoring, retry: retrySave, precondition, block: blockAuthoring } = authoring
  const draft = authoring.document?.snapshot ?? null
  const studioState = authoring.document?.studioState ?? null
  const dirty = authoring.dirty
  const effectiveHostBridge = useMemo(() => hostBridge ?? createHostBridge(), [hostBridge])
  const { fitView } = useReactFlow()
  const [status, setStatus] = useState<StatusEnvelope | null>(null)
  const [historySummaries, setHistorySummaries] = useState<ReadonlyArray<RunSummaryWire>>([])
  const [historyCursor, setHistoryCursor] = useState<string | null>(null)
  const [viewRunId, setViewRunId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RunDetailEnvelope | null>(null)
  const [readiness, setReadiness] = useState<ReadonlyMap<string, ExternalHandoffReadiness>>(
    new Map(),
  )
  // 与当前 readiness 分离的页面内历史说明。仅显式完整检查可以更新，不参与 Submit 授权。
  const [lastFullPrecheckFailures, setLastFullPrecheckFailures] = useState<ReadonlyMap<string, ExternalHandoffReadiness>>(new Map())
  const [checkedOutputs, setCheckedOutputs] = useState<ReadonlyMap<string, ExternalHandoffReadiness>>(new Map())
  const checkedOutputsRef = useRef<ReadonlyMap<string, ExternalHandoffReadiness>>(new Map())
  const [checkingNodeRunId, setCheckingNodeRunId] = useState<string | null>(null)
  const [submittingNodeRunId, setSubmittingNodeRunId] = useState<string | null>(null)
  const handoffActionRef = useRef<symbol | null>(null)
  const [runtimeDiagnosticsOpen, setRuntimeDiagnosticsOpen] = useState(false)
  const [retryOpen, setRetryOpen] = useState(false)
  const [retryBusy, setRetryBusy] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)
  const [retryPreview, setRetryPreview] = useState<RerunPreviewEnvelope | null>(null)
  const retryTokenRef = useRef<symbol | null>(null)
  const retryBindingRef = useRef<{ request: RerunPreviewRequest; generation: number } | null>(null)
  const [logs, setLogs] = useState<ReadonlyMap<string, NodeLogEnvelope>>(new Map())
  const [statusHealth, setStatusHealth] = useState<ChannelHealth>(initialHealth.status)
  const [resourceHealth, setResourceHealth] = useState<ResourceHealth>(emptyResourceHealth)
  const [showRunSnapshot, setShowRunSnapshot] = useState(false)
  const [advanced, setAdvanced] = useState(() => {
    const desktop = readDesktopPreferences()
    if (desktop) return desktop.density === 'advanced'
    try { return localStorage.getItem('zniku.studio.density') === 'advanced' } catch { return false }
  })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [historyBusy, setHistoryBusy] = useState(false)
  const [boundaryError, setBoundaryError] = useState<string | null>(null)
  const [clientHint, setClientHint] = useState<string | null>(null)
  const [commandFailure, setCommandFailure] = useState<StudioServiceError | null>(null)
  const [projectPath, setProjectPath] = useState('')
  const [projectId, setProjectId] = useState('')
  const [projectName, setProjectName] = useState('ZNIKU Project')
  const [query, setQuery] = useState('')
  const [selectedNodeIds, setSelectedNodeIds] = useState<ReadonlySet<string>>(new Set())
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<ReadonlySet<string>>(new Set())
  const [presentationEnvelope, setPresentationEnvelope] = useState<PresentationCatalogEnvelopeWire | null>(null)
  const [presentationError, setPresentationError] = useState<string | null>(null)
  const [parameterDraft, setParameterDraft] = useState<JsonObject>({})
  const [parameterText, setParameterText] = useState('{}')
  const [parameterRawError, setParameterRawError] = useState<string | null>(null)
  const [bottomOpen, setBottomOpen] = useState(advanced)
  const [pollEpoch, setPollEpoch] = useState(0)
  const [detailPollEpoch, setDetailPollEpoch] = useState(0)
  const [templateOpen, setTemplateOpen] = useState(false)
  const templateOutputSelectionRef = useRef<HostSelection | null>(null)
  const templateOutputPickerFlightRef = useRef(0)
  const [templateMode, setTemplateMode] = useState<AvEnhanceV27WizardMode>('create')
  const [homeOpen, setHomeOpen] = useState(true)
  const [homeActionBusy, setHomeActionBusy] = useState(false)
  const [recentProjects, setRecentProjects] = useState(() => [...(readDesktopPreferences()?.recent_projects ?? readRecentProjects())])
  const [hostCapabilities, setHostCapabilities] = useState<HostCapabilitiesEnvelope | null>(null)
  const [hostError, setHostError] = useState<string | null>(null)
  const [hostErrorDetails, setHostErrorDetails] = useState<string | null>(null)
  const [reconnectEpoch, setReconnectEpoch] = useState(0)
  const desktopPreferencesKey = JSON.stringify({ density: advanced ? 'advanced' : 'creator', recent_projects: recentProjects })
  const lastDesktopPreferencesKeyRef = useRef(desktopPreferencesKey)
  useEffect(() => {
    // 选择句柄只属于本次向导/HostBridge；关闭、重连或卸载后不能接纳迟到的选择。
    templateOutputSelectionRef.current = null
    templateOutputPickerFlightRef.current += 1
    return () => { templateOutputPickerFlightRef.current += 1 }
  }, [templateOpen, effectiveHostBridge, reconnectEpoch])
  useEffect(() => {
    const desktop = window.__ZNIKU_DESKTOP__
    if (!desktop || !effectiveHostBridge.saveDesktopPreferences) return
    // 新标签只读取 bootstrap，不在挂载时把旧快照回写，避免覆盖另一标签刚保存的偏好。
    // 这只是有界本机 UI 偏好的去重，不生成领域 digest，也不影响 Graph/Run。
    if (lastDesktopPreferencesKeyRef.current === desktopPreferencesKey) return
    lastDesktopPreferencesKeyRef.current = desktopPreferencesKey
    let current = true
    void effectiveHostBridge.saveDesktopPreferences(desktop.instanceId, {
      density: advanced ? 'advanced' : 'creator', recent_projects: recentProjects,
    }).catch(() => { if (current) setClientHint('本机显示偏好未能保存；工程与处理结果不受影响。') })
    return () => { current = false }
  }, [advanced, recentProjects, effectiveHostBridge, desktopPreferencesKey])
  const [templateProfile, setTemplateProfile] = useState<{
    readonly status: string
    readonly compatible: boolean
    readonly modified: boolean
    readonly graphContent?: string
  } | null>(null)
  const [fitViewEpoch, setFitViewEpoch] = useState(0)

  const statusRef = useRef<StatusEnvelope | null>(null)
  const detailRef = useRef<RunDetailEnvelope | null>(null)
  const viewRunIdRef = useRef<string | null>(null)
  const historySummariesRef = useRef<ReadonlyArray<RunSummaryWire>>([])
  const historyCursorRef = useRef<string | null>(null)
  const historyPagingStartedRef = useRef(false)
  const generationRef = useRef(0)
  const sequenceRef = useRef<Record<ChannelName | 'history', number>>({
    status: 0,
    detail: 0,
    readiness: 0,
    log: 0,
    history: 0,
  })
  const acceptedRef = useRef<Record<'status' | 'history', number>>({
    status: 0,
    history: 0,
  })
  const acceptedResourceSequenceRef = useRef({
    detail: new Map<string, number>(),
    readiness: new Map<string, number>(),
    log: new Map<string, number>(),
  })
  const latestIssuedSequenceRef = useRef({
    detail: new Map<string, number>(),
    readiness: new Map<string, number>(),
    log: new Map<string, number>(),
  })
  const readinessProbeFlightRef = useRef(new Map<string, ReadinessProbeFlight>())
  const detailFlightRef = useRef(new Map<string, DetailFlight>())
  const detailBackoffRef = useRef(new Map<string, DetailBackoff>())
  const detailReinspectRef = useRef(new Set<string>())
  const detailRegressionEpisodeRef = useRef(new Set<string>())
  const detailQueuedRevisionRef = useRef(new Map<string, string>())
  const consumedDetailPollEpochRef = useRef(0)
  const detailBoundaryErrorRef = useRef<string | null>(null)
  const statusBoundaryErrorRef = useRef<string | null>(null)
  const selectedLogResourceRef = useRef<string | null>(null)
  const busyRef = useRef(false)
  const homeActionBusyRef = useRef(false)
  const homeActionEpochRef = useRef(0)
  const commandErrorRef = useRef<string | null>(null)
  const detailSummaryRevisionRef = useRef('')
  const detailRequestedSummaryRevisionRef = useRef('')
  const selectionGuardRef = useRef<{
    readonly nodeIds: ReadonlySet<string>
    readonly edgeIds: ReadonlySet<string>
    readonly selectedNodeId: string | null
    readonly parameterDraftDirty: boolean
  }>({ nodeIds: new Set(), edgeIds: new Set(), selectedNodeId: null, parameterDraftDirty: false })
  const latestTemplatePreviewRef = useRef<{
    readonly requestJson: string
    readonly envelope: AvEnhanceV27TemplatePreviewEnvelope
    readonly precondition: AuthoringPrecondition | null
  } | null>(null)

  useEffect(() => () => {
    homeActionEpochRef.current += 1
    homeActionBusyRef.current = false
  }, [])
  const presentationDefinitionRevision = (draft?.definitions ?? [])
    .map((definition) => `${definition.type_id}@${definition.version}`)
    .sort()
    .join('\u0000')

  useEffect(() => {
    let active = true
    setHostCapabilities(null)
    setHostError(null)
    setHostErrorDetails(null)
    if (!effectiveHostBridge.configured) return () => { active = false }
    void effectiveHostBridge.inspectCapabilities().then((envelope) => {
      if (!active) return
      setHostCapabilities(envelope)
    }).catch(() => {
      if (!active) return
      setHostError('桌面文件选择器暂时不可用；请重新连接。')
    })
    return () => { active = false }
  }, [effectiveHostBridge, reconnectEpoch])

  useEffect(() => {
    let active = true
    if (!effectiveGateway.inspectPresentations) return () => { active = false }
    void effectiveGateway.inspectPresentations().then((envelope) => {
      if (!active) return
      setPresentationEnvelope(envelope)
      setPresentationError(
        envelope.diagnostics.length > 0
          ? `Presentation 已隔离 ${envelope.diagnostics.length} 个无效第三方条目。`
          : null,
      )
    }).catch((error: unknown) => {
      if (!active) return
      setPresentationEnvelope(null)
      setPresentationError(
        `Presentation 不可用，已使用通用 Schema 表单：${error instanceof Error ? error.message : '未知错误'}`,
      )
    })
    return () => { active = false }
  }, [effectiveGateway, presentationDefinitionRevision])

  const markStatusHealth = useCallback((stale: boolean) => {
    setStatusHealth((current) => ({
      stale,
      lastSuccess: stale ? current.lastSuccess : new Date().toISOString(),
    }))
  }, [])

  const updateCheckedOutputs = useCallback((updater: (current: ReadonlyMap<string, ExternalHandoffReadiness>) => ReadonlyMap<string, ExternalHandoffReadiness>) => {
    const next = updater(checkedOutputsRef.current)
    checkedOutputsRef.current = next
    setCheckedOutputs(next)
  }, [])

  const markResourceHealth = useCallback(
    (channel: ResourceChannelName, resourceKey: string, stale: boolean) => {
      setResourceHealth((current) => {
        const values = new Map(current[channel])
        const previous = values.get(resourceKey) ?? { stale: false, lastSuccess: null }
        values.set(resourceKey, {
          stale,
          lastSuccess: stale ? previous.lastSuccess : new Date().toISOString(),
        })
        return { ...current, [channel]: values }
      })
    },
    [],
  )

  const replaceHistory = useCallback(
    (summaries: ReadonlyArray<RunSummaryWire>, cursor: string | null, pagingStarted: boolean) => {
      historySummariesRef.current = summaries
      historyCursorRef.current = cursor
      historyPagingStartedRef.current = pagingStarted
      setHistorySummaries(summaries)
      setHistoryCursor(cursor)
    },
    [],
  )

  const acceptStatus = useCallback(
    (
      next: StatusEnvelope,
      options: {
        readonly replaceProject: boolean
        readonly clearGraphSelection?: boolean
        readonly resetHistory?: boolean
        readonly macroLabel?: string
      },
    ) => {
      const previous = statusRef.current
      const pathChanged = previous !== null && (previous.project_path !== next.project_path || previous.project_session_id !== next.project_session_id)
      const firstAuthority = previous === null
      if (!ingestAuthoring(next, options.replaceProject, options.macroLabel, selectionGuardRef.current.parameterDraftDirty)) return false
      statusRef.current = next
      setStatus(next)
      if (next.project_path !== null) setProjectPath(next.project_path)
      if (firstAuthority || options.replaceProject || pathChanged) {
        if (options.clearGraphSelection ?? pathChanged) {
          setSelectedNodeIds(new Set())
          setSelectedEdgeIds(new Set())
        }
        if (next.snapshot) {
          setProjectId(next.snapshot.project.project_id)
          setProjectName(next.snapshot.project.name)
        }
      }
      if (pathChanged) {
        latestTemplatePreviewRef.current = null
        setTemplateProfile(null)
        setLastFullPrecheckFailures(new Map())
        updateCheckedOutputs(() => new Map())
      }
      if (firstAuthority || pathChanged || options.resetHistory) {
        replaceHistory(next.run_summaries, next.next_run_cursor, false)
      } else {
        // session selector 保留所有已经见过的 summary；否则新 Run 推动首页窗口后，已见 terminal
        // 会在 cursor 已经推进甚至耗尽时从页面消失。
        const retained = mergeSummaries(historySummariesRef.current, next.run_summaries)
        historySummariesRef.current = retained
        setHistorySummaries(retained)
        if (!historyPagingStartedRef.current) {
          historyCursorRef.current = next.next_run_cursor
          setHistoryCursor(next.next_run_cursor)
        }
      }
      const priorStatusError = statusBoundaryErrorRef.current
      statusBoundaryErrorRef.current = null
      if (priorStatusError !== null) {
        setBoundaryError((current) =>
          current === priorStatusError ? detailBoundaryErrorRef.current : current,
        )
      }
      setClientHint(next.error ? `${next.error.code}: ${next.error.message}` : null)
      markStatusHealth(false)
      return true
    },
    [ingestAuthoring, markStatusHealth, replaceHistory, updateCheckedOutputs],
  )
  authoring.onSaved.current = (next) => { acceptStatus(next, { replaceProject: false }) }

  const setTrustedViewRunId = useCallback((runId: string | null, clearResources = true) => {
    viewRunIdRef.current = runId
    setViewRunId(runId)
    if (clearResources) {
      detailRef.current = null
      setDetail(null)
      setReadiness(new Map())
      updateCheckedOutputs(() => new Map())
      handoffActionRef.current = null
      setCheckingNodeRunId(null)
      setSubmittingNodeRunId(null)
      setLogs(new Map())
      setResourceHealth(emptyResourceHealth())
      selectedLogResourceRef.current = null
      detailSummaryRevisionRef.current = ''
      detailRequestedSummaryRevisionRef.current = ''
      const priorDetailError = detailBoundaryErrorRef.current
      detailBoundaryErrorRef.current = null
      if (priorDetailError !== null) {
        setBoundaryError((current) => (current === priorDetailError ? null : current))
      }
      detailFlightRef.current.clear()
      detailBackoffRef.current.clear()
      detailReinspectRef.current.clear()
      detailRegressionEpisodeRef.current.clear()
      detailQueuedRevisionRef.current.clear()
    }
  }, [updateCheckedOutputs])

  const loadDetail = useCallback(
    (runId: string, generation: number): Promise<RunDetailEnvelope | null> => {
      if (generation !== generationRef.current || runId !== viewRunIdRef.current) {
        return Promise.resolve(null)
      }
      const resourceKey = runId
      const requestedSummary = statusRef.current?.run_summaries.find(
        (item) => item.run_id === runId,
      )
      const requestedSummaryRevision = summaryRevision(requestedSummary)
      const requestedAuthorityRevision = summaryAuthorityRevision(requestedSummary)
      const existing = detailFlightRef.current.get(resourceKey)
      if (existing?.generation === generation) {
        if (existing.authorityRevision !== requestedAuthorityRevision) {
          // terminal/final status 不得被旧 running detail flight 消费；旧请求结束后排队 fresh detail。
          detailQueuedRevisionRef.current.set(resourceKey, requestedSummaryRevision)
        }
        return existing.promise
      }
      if (existing) detailFlightRef.current.delete(resourceKey)

      const token = Symbol(resourceKey)
      detailRequestedSummaryRevisionRef.current = requestedSummaryRevision
      const request = (async (): Promise<RunDetailEnvelope | null> => {
        const sequence = ++sequenceRef.current.detail
        latestIssuedSequenceRef.current.detail.set(resourceKey, sequence)
        try {
          const incoming = await effectiveGateway.inspectRun(runId)
          if (
            generation !== generationRef.current ||
            runId !== viewRunIdRef.current ||
            sequence !== latestIssuedSequenceRef.current.detail.get(resourceKey) ||
            sequence <= (acceptedResourceSequenceRef.current.detail.get(resourceKey) ?? 0)
          ) {
            return null
          }
          acceptedResourceSequenceRef.current.detail.set(resourceKey, sequence)
          const merged = monotonicDetail(detailRef.current, incoming)
          detailRef.current = merged.detail
          setDetail(merged.detail)
          detailBackoffRef.current.delete(resourceKey)
          const priorDetailError = detailBoundaryErrorRef.current
          detailBoundaryErrorRef.current = null
          if (priorDetailError !== null) {
            setBoundaryError((current) => (current === priorDetailError ? null : current))
          }
          if (merged.regressed) {
            if (!detailRegressionEpisodeRef.current.has(resourceKey)) {
              detailRegressionEpisodeRef.current.add(resourceKey)
              detailReinspectRef.current.add(resourceKey)
            }
            setClientHint('E_STUDIO_PROGRESS_REGRESSION：已保留最后可信进度并重新检查。')
          } else {
            detailRegressionEpisodeRef.current.delete(resourceKey)
          }
          // 只能确认请求发起时的 summary revision；不得让旧 running 响应冒领 terminal revision。
          detailSummaryRevisionRef.current = requestedSummaryRevision
          detailRequestedSummaryRevisionRef.current = requestedSummaryRevision
          markResourceHealth('detail', resourceKey, false)
          return merged.detail
        } catch (error) {
          if (
            generation === generationRef.current &&
            runId === viewRunIdRef.current &&
            sequence === latestIssuedSequenceRef.current.detail.get(resourceKey)
          ) {
            const previous = detailBackoffRef.current.get(resourceKey)
            const failureIndex = previous?.failureIndex ?? 0
            const delay = failureBackoff[Math.min(failureIndex, failureBackoff.length - 1)]!
            detailBackoffRef.current.set(resourceKey, {
              failureIndex: Math.min(failureIndex + 1, failureBackoff.length),
              retryAt: Date.now() + delay,
            })
            markResourceHealth('detail', resourceKey, true)
            const message = safeVisibleServiceError(
              error instanceof Error ? error.message : 'Run detail 读取失败',
              'Run detail 读取失败；本机路径未显示。',
            )
            detailBoundaryErrorRef.current = message
            setBoundaryError(message)
          }
          return null
        }
      })()
      const guarded = request.finally(() => {
        const current = detailFlightRef.current.get(resourceKey)
        if (current?.generation !== generation || current.token !== token) return
        detailFlightRef.current.delete(resourceKey)
        const queuedRevision = detailQueuedRevisionRef.current.get(resourceKey)
        const queuedRefresh =
          queuedRevision !== undefined && queuedRevision !== requestedSummaryRevision
        if (queuedRevision !== undefined) detailQueuedRevisionRef.current.delete(resourceKey)
        const immediateReinspect = detailReinspectRef.current.delete(resourceKey)
        if (
          (queuedRefresh || immediateReinspect) &&
          generation === generationRef.current &&
          runId === viewRunIdRef.current
        ) {
          setDetailPollEpoch((value) => value + 1)
        }
      })
      detailFlightRef.current.set(resourceKey, {
        generation,
        token,
        authorityRevision: requestedAuthorityRevision,
        promise: guarded,
      })
      return guarded
    },
    [effectiveGateway, markResourceHealth],
  )

  const loadReadiness = useCallback(
    async (
      runId: string,
      nodeRunId: string,
      probe: boolean,
      generation: number,
    ): Promise<ExternalHandoffReadiness | null> => {
      if (generation !== generationRef.current || runId !== viewRunIdRef.current) return null
      const resourceKey = nodeRunResourceKey(runId, nodeRunId)
      const existingFlight = readinessProbeFlightRef.current.get(resourceKey)
      if (existingFlight?.generation === generation) {
        // 显式完整 probe 是该 handoff 的短期权威；被动轮询不得抢占它，重复点击则复用同一请求。
        return probe ? existingFlight.promise : null
      }
      if (existingFlight) readinessProbeFlightRef.current.delete(resourceKey)
      if (probe) {
        setLastFullPrecheckFailures((current) => new Map([...current].filter(([, failure]) =>
          failure.run_id !== runId || failure.node_run_id !== nodeRunId,
        )))
      }

      const request = (async (): Promise<ExternalHandoffReadiness | null> => {
        const sequence = ++sequenceRef.current.readiness
        latestIssuedSequenceRef.current.readiness.set(resourceKey, sequence)
        try {
          const next = await effectiveGateway.inspectReadiness(runId, nodeRunId, probe)
          if (
            generation !== generationRef.current ||
            runId !== viewRunIdRef.current ||
            sequence !== latestIssuedSequenceRef.current.readiness.get(resourceKey) ||
            sequence <=
              (acceptedResourceSequenceRef.current.readiness.get(resourceKey) ?? 0)
          ) {
            return null
          }
          const currentNodeRun = latestNodeRuns(detailRef.current?.run ?? null).get(
            detailRef.current?.run.node_runs.find((item) => item.node_run_id === nodeRunId)?.node_id ?? '',
          )
          if (!currentNodeRun || currentNodeRun.node_run_id !== nodeRunId || !readinessMatchesHandoff(next, currentNodeRun) || next.probe_requested !== probe) {
            throw new Error('外部检查响应与当前处理步骤或目标文件不一致；请重新检查。')
          }
          acceptedResourceSequenceRef.current.readiness.set(resourceKey, sequence)
          setReadiness((current) => new Map(current).set(nodeRunId, next))
          const handoffKey = handoffResourceKey(runId, nodeRunId, next.handoff_id)
          updateCheckedOutputs((current) => {
            const previous = current.get(handoffKey)
            if (!previous || sameObservedOutputs(previous, next)) return current
            const retained = new Map(current)
            retained.delete(handoffKey)
            return retained
          })
          if (probe && next.probe_requested && !next.ready_for_submit) {
            setLastFullPrecheckFailures((current) => new Map(current).set(
              handoffResourceKey(next.run_id, next.node_run_id, next.handoff_id), next,
            ))
          }
          markResourceHealth('readiness', resourceKey, false)
          return next
        } catch (error) {
          if (
            generation === generationRef.current &&
            runId === viewRunIdRef.current &&
            sequence === latestIssuedSequenceRef.current.readiness.get(resourceKey)
          ) {
            markResourceHealth('readiness', resourceKey, true)
            setClientHint(error instanceof Error ? error.message : 'handoff readiness 读取失败')
          }
          return null
        }
      })()
      if (!probe) return request

      const token = Symbol(resourceKey)
      const guarded = request.finally(() => {
        const current = readinessProbeFlightRef.current.get(resourceKey)
        if (current?.generation === generation && current.token === token) {
          readinessProbeFlightRef.current.delete(resourceKey)
        }
      })
      readinessProbeFlightRef.current.set(resourceKey, { generation, token, promise: guarded })
      return guarded
    },
    [effectiveGateway, markResourceHealth, updateCheckedOutputs],
  )

  const loadLog = useCallback(
    async (runId: string, nodeRunId: string, generation: number): Promise<void> => {
      const sequence = ++sequenceRef.current.log
      const resourceKey = `${runId}/${nodeRunId}`
      latestIssuedSequenceRef.current.log.set(resourceKey, sequence)
      try {
        const next = await effectiveGateway.inspectLog(runId, nodeRunId)
        if (
          generation !== generationRef.current ||
          runId !== viewRunIdRef.current ||
          sequence !== latestIssuedSequenceRef.current.log.get(resourceKey) ||
          sequence <= (acceptedResourceSequenceRef.current.log.get(resourceKey) ?? 0)
        ) {
          return
        }
        acceptedResourceSequenceRef.current.log.set(resourceKey, sequence)
        setLogs((current) => new Map(current).set(resourceKey, next))
        markResourceHealth('log', resourceKey, false)
      } catch (error) {
        if (
          generation === generationRef.current &&
          runId === viewRunIdRef.current &&
          sequence === latestIssuedSequenceRef.current.log.get(resourceKey)
        ) {
          markResourceHealth('log', resourceKey, true)
          if (selectedLogResourceRef.current === resourceKey) {
            setClientHint(error instanceof Error ? error.message : '节点日志读取失败')
          }
        }
      }
    },
    [effectiveGateway, markResourceHealth],
  )

  useEffect(() => {
    const generation = ++generationRef.current
    let active = true
    setLoading(true)
    setBoundaryError(null)
    detailBoundaryErrorRef.current = null
    statusBoundaryErrorRef.current = null
    const sequence = ++sequenceRef.current.status
    effectiveGateway
      .inspect(null)
      .then(async (next) => {
        if (!active || generation !== generationRef.current || sequence <= acceptedRef.current.status) {
          return
        }
        acceptedRef.current.status = sequence
        if (!acceptStatus(next, { replaceProject: false })) return
        const selected = defaultRunId(next.run_summaries)
        setTrustedViewRunId(selected)
        // 历史记录用于状态/继续处理入口，不代替用户刚打开的完整工程图。
        setShowRunSnapshot(false)
        if (selected) {
          const selectedDetail = await loadDetail(selected, generation)
          const waiting = [...latestNodeRuns(selectedDetail?.run ?? null).values()].filter(
            (item) => item.state === 'waiting_external' && item.external_handoff !== null,
          )
          for (const nodeRun of waiting) {
            if (!active || generation !== generationRef.current) return
            await loadReadiness(selected, nodeRun.node_run_id, false, generation)
          }
        }
      })
      .catch((error: unknown) => {
        if (active && generation === generationRef.current) {
          markStatusHealth(true)
          const message = safeVisibleServiceError(
            error instanceof Error ? error.message : 'Project Service inspect 失败',
            '本机工程服务暂时不可用。',
          )
          statusBoundaryErrorRef.current = message
          setBoundaryError(message)
        }
      })
      .finally(() => {
        if (active && generation === generationRef.current) setLoading(false)
      })
    return () => {
      active = false
      if (generation === generationRef.current) generationRef.current += 1
    }
  }, [acceptStatus, effectiveGateway, loadDetail, loadReadiness, markStatusHealth, reconnectEpoch, setTrustedViewRunId])

  useEffect(() => {
    if (loading) return
    const generation = generationRef.current
    let cancelled = false
    let timer: number | null = null
    let failureIndex = 0
    let previousOperation = statusRef.current?.active_operation ?? null

    const schedule = (delay: number) => {
      if (!cancelled && generation === generationRef.current) {
        timer = window.setTimeout(() => void cycle(), delay)
      }
    }

    const cycle = async () => {
      if (cancelled || generation !== generationRef.current) return
      const sequence = ++sequenceRef.current.status
      let nextStatus: StatusEnvelope | null = null
      let recoveredMissingRunId: string | null = null
      try {
        nextStatus = await effectiveGateway.inspect(viewRunIdRef.current)
      } catch (error) {
        if (
          error instanceof StudioGatewayError &&
          error.code === 'E_PROJECT_SERVICE_RUN_NOT_FOUND' &&
          viewRunIdRef.current !== null
        ) {
          const missingRunId = viewRunIdRef.current
          try {
            const recovered = await effectiveGateway.inspect(null)
            // 精确 detail 已确认该 ID 不存在；即使 status 窗口短暂滞后，也不能把它重新选回。
            nextStatus = {
              ...recovered,
              run_summaries: recovered.run_summaries.filter(
                (summary) => summary.run_id !== missingRunId,
              ),
              active_run_id:
                recovered.active_run_id === missingRunId ? null : recovered.active_run_id,
            }
            recoveredMissingRunId = missingRunId
          } catch (retryError) {
            error = retryError
          }
        }
        if (!nextStatus && generation === generationRef.current) {
          markStatusHealth(true)
          const message = safeVisibleServiceError(
            error instanceof Error ? error.message : 'Project Service status 读取失败',
            '本机工程服务暂时不可用。',
          )
          statusBoundaryErrorRef.current = message
          setBoundaryError(message)
        }
      }

      if (
        cancelled ||
        generation !== generationRef.current ||
        (nextStatus && sequence <= acceptedRef.current.status)
      ) {
        return
      }

      if (!nextStatus) {
        schedule(failureBackoff[Math.min(failureIndex++, failureBackoff.length - 1)]!)
        return
      }

      acceptedRef.current.status = sequence
      failureIndex = 0
      if (recoveredMissingRunId) {
        // 404 recovery 与历史分页共享 project generation；推进独立 fence，禁止旧 page 撤销 fresh reset。
        const historyFence = ++sequenceRef.current.history
        acceptedRef.current.history = historyFence
        setHistoryBusy(false)
        setTrustedViewRunId(null)
      }
      if (!acceptStatus(nextStatus, {
        replaceProject: false,
        resetHistory: recoveredMissingRunId !== null,
      })) { schedule(1_500); return }
      const merged = mergeSummaries(nextStatus.run_summaries, historySummariesRef.current)
      let selected = viewRunIdRef.current
      if (!selected || !merged.some((summary) => summary.run_id === selected)) {
        selected = defaultRunId(merged)
        setTrustedViewRunId(selected)
      }
      if (recoveredMissingRunId) {
        setClientHint('先前选择的 Run 已不存在，已重新选择可用 Run。')
      }

      const selectedSummary = merged.find((summary) => summary.run_id === selected)
      const revision = summaryRevision(selectedSummary)
      const finalRefresh = previousOperation !== null && nextStatus.active_operation === null
      previousOperation = nextStatus.active_operation
      const revisionNeedsRefresh =
        detailSummaryRevisionRef.current !== revision &&
        detailRequestedSummaryRevisionRef.current !== revision
      if (
        selected &&
        (finalRefresh ||
          detailRef.current?.run.run_id !== selected ||
          revisionNeedsRefresh)
      ) {
        detailRequestedSummaryRevisionRef.current = revision
        // status 的 revision 可以在 Runtime 活跃时持续推进；detail channel 已失败时必须尊重
        // 自己的 750/1500/3000/5000 退避，不能被每次轻量 status 成功强制穿透。
        if (!detailBackoffRef.current.has(selected)) {
          setDetailPollEpoch((value) => value + 1)
        }
      }
      schedule(finalRefresh ? 0 : statusPollDelay(nextStatus))
    }

    schedule(statusPollDelay(statusRef.current))
    return () => {
      cancelled = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [
    acceptStatus,
    effectiveGateway,
    loading,
    markStatusHealth,
    pollEpoch,
    setTrustedViewRunId,
  ])

  useEffect(() => {
    if (loading || !viewRunId) return
    const generation = generationRef.current
    const runId = viewRunId
    let cancelled = false
    let timer: number | null = null
    const forced = detailPollEpoch > consumedDetailPollEpochRef.current
    consumedDetailPollEpochRef.current = detailPollEpoch

    const schedule = (delay: number) => {
      if (!cancelled && generation === generationRef.current) {
        timer = window.setTimeout(() => void cycle(), delay)
      }
    }

    const cycle = async () => {
      if (
        cancelled ||
        generation !== generationRef.current ||
        runId !== viewRunIdRef.current
      ) {
        return
      }
      const backoff = detailBackoffRef.current.get(runId)
      if (backoff && backoff.retryAt > Date.now()) {
        schedule(backoff.retryAt - Date.now())
        return
      }

      const selectedDetail = await loadDetail(runId, generation)
      if (
        cancelled ||
        generation !== generationRef.current ||
        runId !== viewRunIdRef.current
      ) {
        return
      }
      if (selectedDetail) {
        const waiting = [...latestNodeRuns(selectedDetail.run).values()].filter(
          (item) => item.state === 'waiting_external' && item.external_handoff !== null,
        )
        for (const nodeRun of waiting) {
          if (cancelled || generation !== generationRef.current) return
          await loadReadiness(runId, nodeRun.node_run_id, false, generation)
        }
      }

      const failed = detailBackoffRef.current.get(runId)
      if (!selectedDetail && failed) {
        schedule(Math.max(0, failed.retryAt - Date.now()))
        return
      }
      const summary = statusRef.current?.run_summaries.find((item) => item.run_id === runId)
      const delay = detailPollDelay(summary)
      if (delay !== null) schedule(delay)
    }

    const summary = statusRef.current?.run_summaries.find((item) => item.run_id === runId)
    const normalDelay = detailRef.current?.run.run_id === runId ? detailPollDelay(summary) : 0
    if (forced) void cycle()
    else if (normalDelay !== null) schedule(normalDelay)
    return () => {
      cancelled = true
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [detailPollEpoch, loadDetail, loadReadiness, loading, pollEpoch, viewRunId])

  const allSummaries = useMemo(
    () => mergeSummaries(status?.run_summaries ?? [], historySummaries),
    [historySummaries, status?.run_summaries],
  )
  const viewedSummary = allSummaries.find((summary) => summary.run_id === viewRunId) ?? null
  const currentDetail = detail?.run.run_id === viewRunId ? detail : null
  const currentRun = currentDetail?.run ?? null
  const operationActive = status?.active_operation !== null && status?.active_operation !== undefined
  const currentSnapshot = useMemo<ProjectSnapshotWire | null>(() => {
    if (!currentRun || !draft) return null
    return {
      project: {
        project_id: currentRun.project_id,
        name: draft.project.name,
        graph: currentRun.graph_snapshot,
      },
      definitions: currentRun.definitions_snapshot,
    }
  }, [currentRun, draft])
  const displaySnapshot = showRunSnapshot && currentSnapshot ? currentSnapshot : draft
  const graph = displaySnapshot?.project.graph ?? { nodes: [], edges: [] }
  const definitions = displaySnapshot?.definitions ?? []
  const diagnostics = useMemo(() => {
    if (!draft) return []
    if (dirty || !status || JSON.stringify(status.snapshot?.project.graph) !== JSON.stringify(draft.project.graph)) return inspectGraph(draft)
    // 保存后使用 Python 的完整诊断。尚未保存时仅提供即时提示，不作为保存准入 authority。
    return status.authoring_diagnostics.map((item) => {
      const nodeIndex = /^nodes\[(\d+)\]/.exec(item.path)?.[1]
      const edgeIndex = /^edges\[(\d+)\]/.exec(item.path)?.[1]
      const edge = edgeIndex === undefined ? undefined : draft.project.graph.edges[Number(edgeIndex)]
      return { code: item.code, message: item.message,
        node_id: nodeIndex === undefined ? null : draft.project.graph.nodes[Number(nodeIndex)]?.node_id ?? null,
        edge_id: edge ? edgeId(edge) : null,
      }
    })
  }, [dirty, draft, status])
  const runLatestAttempts = useMemo(() => latestNodeRuns(currentRun), [currentRun])
  const activeNodeRuns = useMemo(() => {
    if (!currentRun) return new Map<string, NodeRunWire>()
    if (showRunSnapshot) return runLatestAttempts
    const visible = new Map<string, NodeRunWire>()
    for (const [nodeId, nodeRun] of runLatestAttempts) {
      if (draft && nodeExecutionSignatureMatches(draft.project.graph, currentRun.graph_snapshot, nodeId)) {
        visible.set(nodeId, nodeRun)
      }
    }
    return visible
  }, [currentRun, draft, runLatestAttempts, showRunSnapshot])
  const latestResults = useMemo(
    () =>
      new Map(
        (showRunSnapshot ? [] : status?.latest_results ?? []).map((result) => [result.node_id, result]),
      ),
    [showRunSnapshot, status?.latest_results],
  )
  const artifactsById = useMemo(
    () => new Map((detail?.artifacts ?? []).map((artifact) => [artifact.artifact_id, artifact])),
    [detail?.artifacts],
  )
  const progressSamplesByNodeRun = useMemo(
    () =>
      new Map(
        (detail?.run.run_id === viewRunId ? detail.progress_samples : []).map((sample) => [
          sample.node_run_id,
          sample,
        ]),
      ),
    [detail, viewRunId],
  )
  const definitionsByKey = useMemo(
    () => new Map(definitions.map((definition) => [`${definition.type_id}@${definition.version}`, definition])),
    [definitions],
  )
  const presentationsByKey = useMemo(
    () => new Map(
      (presentationEnvelope?.catalog.nodes ?? []).map((presentation) => [
        `${presentation.type_id}@${presentation.definition_version}`,
        presentation,
      ]),
    ),
    [presentationEnvelope],
  )

  const nodeLabels = useMemo(() => {
    const label = (node: NodeInstanceWire) => studioState?.node_views.find((view) => view.node_id === node.node_id)?.display_name
      ?? presentationsByKey.get(`${node.type_id}@${node.definition_version}`)?.title ?? node.type_id
    const visible = distinctNodeLabels(graph.nodes, label)
    const historical = distinctNodeLabels(currentRun?.graph_snapshot.nodes ?? [], label)
    return new Map([...historical, ...visible])
  }, [currentRun?.graph_snapshot.nodes, graph.nodes, presentationsByKey, studioState?.node_views])

  const flowNodes = useMemo<WorkflowNode[]>(
    () =>
      graph.nodes.flatMap((node, index) => {
        const definition = definitionsByKey.get(`${node.type_id}@${node.definition_version}`)
        if (!definition) return []
        const presentation = presentationsByKey.get(`${node.type_id}@${node.definition_version}`) ?? null
        const nodeRun = activeNodeRuns.get(node.node_id) ?? null
        const data: WorkflowNodeData = {
          label: nodeLabels.get(node.node_id) ?? node.type_id,
          advanced,
          collapsed: studioState?.node_views.find((view) => view.node_id === node.node_id)?.collapsed ?? false,
          iconToken: presentation?.icon_token,
          portLabels: {
            input: Object.fromEntries((presentation?.ports ?? []).filter((port) => port.direction === 'input').map((port) => [port.port_id, port.label])),
            output: Object.fromEntries((presentation?.ports ?? []).filter((port) => port.direction === 'output').map((port) => [port.port_id, port.label])),
          },
          instanceId: node.node_id,
          summaries: [...(presentation?.card_summary_paths ?? []).flatMap((pointer) => {
            const value = getPointer(node.parameters, pointer)
            if (value === undefined) return []
            const parameterPresentation = presentation?.parameters.find(
              (item) => item.parameter_pointer === pointer,
            )
            const label = parameterPresentation?.label ?? pointer
            const text = cardSummaryValue(value, parameterPresentation)
            return [`${label}：${text}`]
          }), ...(nodeRun?.external_handoff ? handoffSummary(nodeRun, artifactsById, currentDetail) : [])],
          typeId: node.type_id,
          definitionVersion: node.definition_version,
          executorKind: definition.executor.kind,
          inputs: definition.input_ports,
          outputs: definition.output_ports,
          nodeRun,
          progress: nodeProgressView(
            nodeRun,
            definition,
            nodeRun ? progressSamplesByNodeRun.get(nodeRun.node_run_id) ?? null : null,
          ),
          latestResult: latestResults.get(node.node_id) ?? null,
        }
        return [
          {
            id: node.node_id,
            type: 'workflow' as const,
            position:
              node.ui_position ?? {
                x: 80 + (index % 4) * 250,
                y: 100 + Math.floor(index / 4) * 190,
              },
            selected: selectedNodeIds.has(node.node_id),
            data,
          },
        ]
      }),
    [
      activeNodeRuns,
      advanced,
      artifactsById,
      currentDetail,
      nodeLabels,
      studioState,
      definitionsByKey,
      graph.nodes,
      latestResults,
      progressSamplesByNodeRun,
      presentationsByKey,
      selectedNodeIds,
    ],
  )

  const flowEdges = useMemo<WorkflowEdge[]>(
    () =>
      graph.edges.map((edge) => ({
        id: edgeId(edge),
        type: 'smoothstep' as const,
        source: edge.source_node_id,
        sourceHandle: edge.source_port_id,
        target: edge.target_node_id,
        targetHandle: edge.target_port_id,
        selected: selectedEdgeIds.has(edgeId(edge)),
        label: edge.ordinal === null ? undefined : advanced ? `#${edge.ordinal}` : `${edge.ordinal + 1}`,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        data: { ordinal: edge.ordinal },
      })),
    [advanced, graph.edges, selectedEdgeIds],
  )

  const selectedNode = graph.nodes.find((node) => selectedNodeIds.has(node.node_id)) ?? null
  const selectedDefinition = selectedNode ? definitionForNode(selectedNode, definitions) : null
  const selectedPresentation = selectedNode
    ? presentationEnvelope?.catalog.nodes.find(
        (item) =>
          item.type_id === selectedNode.type_id &&
          item.definition_version === selectedNode.definition_version,
      ) ?? null
    : null
  const selectedEdge = graph.edges.find((edge) => selectedEdgeIds.has(edgeId(edge))) ?? null
  const selectedNodeRunAuthority = selectedNode
    ? activeNodeRuns.get(selectedNode.node_id) ?? null
    : null
  const selectedProgress =
    selectedNodeRunAuthority && selectedDefinition
      ? nodeProgressView(
          selectedNodeRunAuthority,
          selectedDefinition,
          progressSamplesByNodeRun.get(selectedNodeRunAuthority.node_run_id) ?? null,
        )
      : null
  const selectedNodeRun = selectedNodeRunAuthority
    ? {
        ...selectedNodeRunAuthority,
        progress:
          selectedProgress?.mode === 'determinate' ? selectedProgress.fraction : null,
      }
    : null
  const selectedLogResourceKey =
    viewRunId && selectedNodeRun?.log_path
      ? `${viewRunId}/${selectedNodeRun.node_run_id}`
      : null
  const selectedLog =
    selectedLogResourceKey && logs.get(selectedLogResourceKey)?.log.node_run_id === selectedNodeRun?.node_run_id
      ? logs.get(selectedLogResourceKey)!.log
      : null
  const selectedOutputs = selectedNodeRun
    ? selectedNodeRun.output_artifact_ids.flatMap((artifactId) => {
        const artifact = artifactsById.get(artifactId)
        return artifact ? [artifact] : []
      })
    : []
  const previewCandidates: PreviewCandidate[] = selectedNodeRun ? [
    ...selectedNodeRun.input_artifact_ids.map((id) => ({ id, side: 'input' as const })),
    ...selectedNodeRun.output_artifact_ids.map((id) => ({ id, side: 'output' as const })),
  ].flatMap(({ id, side }) => {
    const artifact = artifactsById.get(id)
    if (!artifact || !['VideoFile', 'MediaFile'].includes(artifact.kind)) return []
    return [{ id, side, label: `${side === 'input' ? '输入' : '输出'} · ${artifact.path.split(/[\\/]/).pop() ?? artifact.path}`,
      reference: { kind: 'artifact' as const, run_id: selectedNodeRun.run_id, artifact_id: id } }]
  }) : []
  if (selectedNodeRun?.external_handoff) {
    for (const target of selectedNodeRun.external_handoff.output_targets) {
      previewCandidates.push({ id: `target:${target.port_id}:${target.ordinal ?? ''}`, side: 'output',
        label: `外部目标（未提交） · ${target.path.split(/[\\/]/).pop() ?? target.path}`,
        reference: { kind: 'handoff', run_id: selectedNodeRun.run_id, node_run_id: selectedNodeRun.node_run_id,
          handoff_id: selectedNodeRun.external_handoff.handoff_id,
          selector: { role: 'output_target', port_id: target.port_id, ordinal: target.ordinal } } })
    }
  }
  const handoffInputs = selectedNodeRun?.external_handoff
    ? selectedNodeRun.external_handoff.input_artifact_ids.flatMap((artifactId) => {
        const artifact = artifactsById.get(artifactId)
        return artifact ? [artifact.path] : [`未解析 Artifact：${artifactId}`]
      })
    : []
  const waitingNodeRuns = useMemo(
    () =>
      [...runLatestAttempts.values()].filter(
        (nodeRun) => nodeRun.state === 'waiting_external' && nodeRun.external_handoff !== null,
      ),
    [runLatestAttempts],
  )
  useEffect(() => {
    if (!currentRun) return
    const waitingKeys = new Set(waitingNodeRuns.map((nodeRun) => handoffResourceKey(
      currentRun.run_id, nodeRun.node_run_id, nodeRun.external_handoff!.handoff_id,
    )))
    setLastFullPrecheckFailures((current) => {
      const retained = [...current].filter(([key, failure]) => failure.run_id !== currentRun.run_id || waitingKeys.has(key))
      return retained.length === current.size ? current : new Map(retained)
    })
    updateCheckedOutputs((current) => {
      const retained = [...current].filter(([key]) => waitingKeys.has(key))
      return retained.length === current.size ? current : new Map(retained)
    })
  }, [currentRun, updateCheckedOutputs, waitingNodeRuns])
  const health = useMemo<Record<ChannelName, ChannelHealth>>(
    () => ({
      status: statusHealth,
      detail: aggregateResourceHealth(
        resourceHealth.detail,
        viewRunId ? [viewRunId] : [],
      ),
      readiness: aggregateResourceHealth(
        resourceHealth.readiness,
        viewRunId
          ? waitingNodeRuns.map((nodeRun) => `${viewRunId}/${nodeRun.node_run_id}`)
          : [],
      ),
      log: aggregateResourceHealth(
        resourceHealth.log,
        selectedLogResourceKey ? [selectedLogResourceKey] : [],
      ),
    }),
    [resourceHealth, selectedLogResourceKey, statusHealth, viewRunId, waitingNodeRuns],
  )

  const selectedParameterSignature = JSON.stringify(selectedNode?.parameters ?? {})
  useEffect(() => {
    const next = parameterObject(selectedParameterSignature) ?? {}
    setParameterDraft(next)
    setParameterText(JSON.stringify(next, null, 2))
    setParameterRawError(null)
  }, [selectedNode?.node_id, selectedParameterSignature])
  const parameterValidation = useMemo(
    () => selectedDefinition
      ? validateParameterDraft(asParameterSchema(selectedDefinition.parameter_schema), parameterDraft)
      : null,
    [parameterDraft, selectedDefinition],
  )
  const parameterDraftDirty = !!selectedNode && (
    parameterRawError !== null || JSON.stringify(parameterDraft) !== selectedParameterSignature
  )
  selectionGuardRef.current = {
    nodeIds: selectedNodeIds,
    edgeIds: selectedEdgeIds,
    selectedNodeId: selectedNode?.node_id ?? null,
    parameterDraftDirty,
  }

  useEffect(() => {
    selectedLogResourceRef.current = selectedLogResourceKey
    if ((!advanced && !runtimeDiagnosticsOpen) || !viewRunId || !selectedNodeRun?.log_path || !selectedLogResourceKey) return
    void loadLog(viewRunId, selectedNodeRun.node_run_id, generationRef.current)
  }, [advanced, runtimeDiagnosticsOpen, loadLog, selectedLogResourceKey, selectedNodeRun?.log_path, selectedNodeRun?.node_run_id, viewRunId])

  const changeSelection = useCallback((
    nodeIds: ReadonlySet<string>,
    edgeIds: ReadonlySet<string>,
  ): boolean => {
    const sameSet = (left: ReadonlySet<string>, right: ReadonlySet<string>) =>
      left.size === right.size && [...left].every((item) => right.has(item))
    const current = selectionGuardRef.current
    if (selectionChangeBlocked(current.selectedNodeId, current.parameterDraftDirty, nodeIds, edgeIds)) {
      setClientHint('当前节点有未应用设置；请先“应用设置”或“放弃未应用更改”。')
      return false
    }
    // React Flow 会在受控 selected 投影后再次发出 selection 事件；相同集合不得重复 setState。
    if (sameSet(current.nodeIds, nodeIds) && sameSet(current.edgeIds, edgeIds)) return true
    selectionGuardRef.current = {
      ...current,
      nodeIds,
      edgeIds,
      selectedNodeId: nodeIds.size === 1 ? [...nodeIds][0]! : null,
    }
    setSelectedNodeIds(nodeIds)
    setSelectedEdgeIds(edgeIds)
    return true
  }, [])

  const selectRun = useCallback(
    (runId: string) => {
      if (!changeSelection(new Set(), new Set())) return
      // Run 选择会切换请求 generation；同步释放旧分页 owner，旧请求的 finally 不得回写新视图。
      setHistoryBusy(false)
      readinessProbeFlightRef.current.clear()
      const generation = ++generationRef.current
      setTrustedViewRunId(runId)
      setShowRunSnapshot(true)
      setSelectedNodeIds(new Set())
      setSelectedEdgeIds(new Set())
      setPollEpoch((value) => value + 1)
      void loadDetail(runId, generation)
    },
    [changeSelection, loadDetail, setTrustedViewRunId],
  )

  const updateGraph = useCallback((updater: (graph: GraphWire) => GraphWire) => {
    editAuthoring('编辑节点图', (current) => {
      const next = updater(current.snapshot.project.graph)
      return { ...current, snapshot: replaceGraph(current.snapshot, next) }
    })
    setClientHint(null)
  }, [editAuthoring])

  const handleSelection = useCallback((selection: OnSelectionChangeParams) => {
    changeSelection(
      new Set(selection.nodes.map((node) => node.id)),
      new Set(selection.edges.map((edge) => edge.id)),
    )
  }, [changeSelection])

  const handleNodeClick: NodeMouseHandler<WorkflowNode> = useCallback((event, node) => {
    if (event.ctrlKey || event.metaKey) {
      const next = new Set(selectedNodeIds)
      if (next.has(node.id)) next.delete(node.id)
      else next.add(node.id)
      changeSelection(next, selectedEdgeIds)
    } else {
      changeSelection(new Set([node.id]), new Set())
    }
  }, [changeSelection, selectedEdgeIds, selectedNodeIds])

  const handleEdgeClick: EdgeMouseHandler<WorkflowEdge> = useCallback((event, edge) => {
    if (event.ctrlKey || event.metaKey) {
      const next = new Set(selectedEdgeIds)
      if (next.has(edge.id)) next.delete(edge.id)
      else next.add(edge.id)
      changeSelection(selectedNodeIds, next)
    } else {
      changeSelection(new Set(), new Set([edge.id]))
    }
  }, [changeSelection, selectedEdgeIds, selectedNodeIds])

  const graphEditable = !showRunSnapshot || currentRun === null

  const handleNodesChange = useCallback(
    (changes: NodeChange<WorkflowNode>[]) => {
      if (!graphEditable) return
      const positions = new Map(
        changes.flatMap((change) =>
          change.type === 'position' && change.position ? [[change.id, change.position] as const] : [],
        ),
      )
      const removed = new Set(
        changes.flatMap((change) => (change.type === 'remove' ? [change.id] : [])),
      )
      if (removed.size > 0 && parameterDraftDirty) {
        setClientHint('当前节点有未应用设置；请先应用或放弃，再删除。')
        return
      }
      if (positions.size > 0) {
        if (changes.some((change) => change.type === 'position' && change.dragging === true)) beginMove()
        updateGraph((current) => ({
          ...current,
          nodes: current.nodes.map((node) =>
            positions.has(node.node_id)
              ? { ...node, ui_position: positions.get(node.node_id)! }
              : node,
          ),
        }))
        if (changes.some((change) => change.type === 'position' && change.dragging === false)) endMove()
      }
      if (removed.size > 0) updateGraph((current) => deleteSelection(current, removed, new Set()))
    },
    [beginMove, endMove, graphEditable, parameterDraftDirty, updateGraph],
  )

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<WorkflowEdge>[]) => {
      if (!graphEditable) return
      const removed = new Set(
        changes.flatMap((change) => (change.type === 'remove' ? [change.id] : [])),
      )
      if (removed.size > 0) updateGraph((current) => deleteSelection(current, new Set(), removed))
    },
    [graphEditable, updateGraph],
  )

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (!graphEditable || !draft) return
      const next = connectGraph(connection, draft.project.graph, draft.definitions)
      if (!next) {
        setClientHint('连接被拒绝：请检查精确 data_type、cardinality、占用状态与 cycle。')
        return
      }
      updateGraph(() => next)
    },
    [draft, graphEditable, updateGraph],
  )

  const connectionIsValid = useCallback(
    (connection: Connection | WorkflowEdge) =>
      graphEditable &&
      !!draft &&
      isStudioConnectionValid(
        {
          source: connection.source,
          sourceHandle: connection.sourceHandle ?? null,
          target: connection.target,
          targetHandle: connection.targetHandle ?? null,
        },
        draft.project.graph,
        draft.definitions,
      ),
    [draft, graphEditable],
  )

  const addDefinition = useCallback(
    (definition: NodeDefinitionWire) => {
      if (!draft || !graphEditable) return
      if (parameterDraftDirty) {
        setClientHint('当前节点有未应用设置；请先应用或放弃，再添加节点。')
        return
      }
      const existing = new Set(draft.project.graph.nodes.map((node) => node.node_id))
      let nodeId = nodeIdFactory()
      while (existing.has(nodeId)) nodeId = nodeIdFactory()
      const node: NodeInstanceWire = {
        node_id: nodeId,
        type_id: definition.type_id,
        definition_version: definition.version,
        parameters: defaultParameters(definition),
        ui_position: {
          x: 120 + draft.project.graph.nodes.length * 42,
          y: 120 + draft.project.graph.nodes.length * 28,
        },
      }
      updateGraph((current) => ({ ...current, nodes: [...current.nodes, node] }))
      setSelectedNodeIds(new Set([nodeId]))
      setSelectedEdgeIds(new Set())
    },
    [draft, graphEditable, nodeIdFactory, parameterDraftDirty, updateGraph],
  )

  const copySelected = useCallback(() => {
    if (!draft || !graphEditable || selectedNodeIds.size === 0) return
    if (parameterDraftDirty) {
      setClientHint('当前节点有未应用设置；请先应用或放弃，再复制节点。')
      return
    }
    const copied = copySelection(draft.project.graph, selectedNodeIds, nodeIdFactory)
    const originals = draft.project.graph.nodes.filter((node) => selectedNodeIds.has(node.node_id))
    const copies = [...copied.copied_node_ids]
    editAuthoring('复制节点', (current) => ({
      snapshot: replaceGraph(current.snapshot, copied.graph),
      studioState: { ...current.studioState, node_views: [
        ...current.studioState.node_views,
        ...originals.flatMap((node, index) => {
          const view = current.studioState.node_views.find((item) => item.node_id === node.node_id)
          return view && copies[index] ? [{ ...view, node_id: copies[index]! }] : []
        }),
      ] },
    }))
    setSelectedNodeIds(copied.copied_node_ids)
    setSelectedEdgeIds(new Set())
  }, [draft, editAuthoring, graphEditable, nodeIdFactory, parameterDraftDirty, selectedNodeIds])

  const deleteSelected = useCallback(() => {
    if (!graphEditable || (selectedNodeIds.size === 0 && selectedEdgeIds.size === 0)) return
    if (parameterDraftDirty) {
      setClientHint('当前节点有未应用设置；请先应用或放弃，再删除。')
      return
    }
    updateGraph((current) => deleteSelection(current, selectedNodeIds, selectedEdgeIds))
    setSelectedNodeIds(new Set())
    setSelectedEdgeIds(new Set())
  }, [graphEditable, parameterDraftDirty, selectedEdgeIds, selectedNodeIds, updateGraph])

  useEffect(() => {
    const handleKeyboard = (event: KeyboardEvent) => {
      if (isEditingTarget(event.target) || (event.target instanceof Element && event.target.closest('[role="dialog"]')) || !graphEditable || busy || homeOpen || templateOpen) return
      if ((event.ctrlKey || event.metaKey) && ['z', 'y', 's'].includes(event.key.toLowerCase())) {
        event.preventDefault()
        if (parameterDraftDirty) { setClientHint('请先应用或放弃未应用的节点设置。'); return }
        if (event.key.toLowerCase() === 's') void flushAuthoring().catch((error: unknown) => setClientHint(error instanceof Error ? error.message : '保存失败'))
        else travel(event.key.toLowerCase() === 'y' || event.shiftKey ? 'redo' : 'undo')
        return
      }
      if (
        (event.key === 'Delete' || event.key === 'Backspace') &&
        (selectedNodeIds.size || selectedEdgeIds.size)
      ) {
        event.preventDefault()
        deleteSelected()
      } else if (
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === 'd' &&
        selectedNodeIds.size
      ) {
        event.preventDefault()
        copySelected()
      }
    }
    window.addEventListener('keydown', handleKeyboard)
    return () => window.removeEventListener('keydown', handleKeyboard)
  }, [busy, copySelected, deleteSelected, flushAuthoring, graphEditable, homeOpen, parameterDraftDirty, selectedEdgeIds.size, selectedNodeIds.size, templateOpen, travel])

  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (dirty || parameterDraftDirty) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', protect)
    return () => window.removeEventListener('beforeunload', protect)
  }, [dirty, parameterDraftDirty])

  const applyParameters = useCallback(() => {
    if (!selectedNode || !graphEditable) return
    if (parameterRawError || !parameterValidation?.valid) {
      setClientHint('设置仍有 Schema 或 JSON 问题；未修改 Project Graph。')
      return
    }
    updateGraph((current) => ({
      ...current,
      nodes: current.nodes.map((node) =>
        node.node_id === selectedNode.node_id ? { ...node, parameters: parameterDraft } : node,
      ),
    }))
  }, [graphEditable, parameterDraft, parameterRawError, parameterValidation?.valid, selectedNode, updateGraph])

  const changeParameterDraft = useCallback((next: JsonObject) => {
    setParameterDraft(next)
    setParameterText(JSON.stringify(next, null, 2))
    setParameterRawError(null)
    setClientHint(null)
  }, [])

  const changeParameterText = useCallback((text: string) => {
    setParameterText(text)
    const parsed = parameterObject(text)
    if (!parsed) {
      setParameterRawError('原始参数必须是合法 JSON object；当前文本尚未进入结构化 draft。')
      return
    }
    setParameterDraft(parsed)
    setParameterRawError(null)
  }, [])

  const discardParameters = useCallback(() => {
    const next = parameterObject(selectedParameterSignature) ?? {}
    setParameterDraft(next)
    setParameterText(JSON.stringify(next, null, 2))
    setParameterRawError(null)
    setClientHint('已放弃当前节点未应用的设置。')
  }, [selectedParameterSignature])

  const executeCommands = useCallback(
    async (
      commands: ReadonlyArray<StudioCommand>,
      options: { readonly preferCreatedRun?: boolean; readonly discardLocal?: boolean; readonly templatePreview?: AvEnhanceV27TemplatePreviewEnvelope } = {},
    ): Promise<StatusEnvelope | null> => {
      if (busyRef.current) return null
      busyRef.current = true
      setBusy(true)
      setBoundaryError(null)
      setClientHint(null)
      commandErrorRef.current = null
      setCommandFailure(null)
      const previousPath = statusRef.current?.project_path ?? null
      const previousViewRunId = viewRunIdRef.current
      // 任意命令都会切换请求 generation，因此先取消旧历史分页在 UI 上的互斥占位。
      setHistoryBusy(false)
      readinessProbeFlightRef.current.clear()
      const generation = ++generationRef.current
      setPollEpoch((value) => value + 1)
      let next: StatusEnvelope | null = null
      try {
        for (const command of commands) {
          if (['open_project', 'create_project', 'create_av_enhance_v27'].includes(command.operation)) {
            if (selectionGuardRef.current.parameterDraftDirty) throw new Error('请先应用或放弃未应用的节点设置，再切换工程。')
            if (statusRef.current?.snapshot && !options.discardLocal) await flushAuthoring()
            if (selectionGuardRef.current.parameterDraftDirty) throw new Error('保存等待期间节点设置已变化；请先应用或放弃，再切换工程。')
          }
          next = await effectiveGateway.command(command)
          if (generation !== generationRef.current) return null
          if (options.templatePreview && (command.operation === 'create_av_enhance_v27' || command.operation === 'expand_av_enhance_v27')) {
            const preview = options.templatePreview
            const identityMatches = command.operation !== 'expand_av_enhance_v27' ||
              (next.project_session_id === command.project_session_id && next.project_path === previousPath && next.storage_revision === command.expected_storage_revision + 1)
            const contentMatches = next.snapshot && sameWireData(next.snapshot.project, preview.project) &&
              preview.definitions.every((definition) => next!.snapshot!.definitions.some((candidate) => sameWireData(candidate, definition)))
            if (!identityMatches || !contentMatches) {
              const message = '模板写入后的工程与确认预览不一致；可能有另一窗口更新。请重新载入磁盘版本。'
              blockAuthoring(message)
              setTemplateProfile(null)
              throw new Error(message)
            }
          }
          acceptStatus(next, {
            replaceProject:
              command.operation === 'open_project' ||
              command.operation === 'create_project' ||
              command.operation === 'save_project' ||
              command.operation === 'create_av_enhance_v27' ||
              command.operation === 'expand_av_enhance_v27',
            macroLabel: command.operation === 'expand_av_enhance_v27' ? '展开处理链' : undefined,
            clearGraphSelection:
              command.operation === 'open_project' ||
              command.operation === 'create_project' ||
              command.operation === 'create_av_enhance_v27' ||
              command.operation === 'expand_av_enhance_v27',
          })
        }
        if (!next) return null

        const last = commands.at(-1)
        if (
          next.project_path &&
          next.snapshot &&
          (last?.operation === 'open_project' ||
            last?.operation === 'create_project' ||
            last?.operation === 'save_project' ||
            last?.operation === 'create_av_enhance_v27' ||
            last?.operation === 'expand_av_enhance_v27')
        ) {
          const recent = { path: next.project_path, name: next.snapshot.project.name }
          setRecentProjects((previous) => rememberRecentProject({
            ...recent,
          }, undefined, previous))
        }
        if (last?.operation === 'save_project') return next
        if (last?.operation === 'open_project' || last?.operation === 'create_project') {
          latestTemplatePreviewRef.current = null
          setTemplateProfile(null)
        }
        let selected = viewRunIdRef.current
        if (
          last?.operation === 'open_project' ||
          last?.operation === 'create_project' ||
          last?.operation === 'create_av_enhance_v27' ||
          last?.operation === 'expand_av_enhance_v27'
        ) {
          const knownSummaries = mergeSummaries(
            next.run_summaries,
            previousPath === next.project_path ? historySummariesRef.current : [],
          )
          selected =
            previousPath === next.project_path &&
            previousViewRunId !== null &&
            knownSummaries.some((summary) => summary.run_id === previousViewRunId)
              ? previousViewRunId
              : defaultRunId(knownSummaries)
          setShowRunSnapshot(false)
        } else if (
          options.preferCreatedRun &&
          next.active_run_id &&
          (last?.operation === 'run_all' ||
            last?.operation === 'run_to' ||
            last?.operation === 'rerun_from_here')
        ) {
          selected = next.active_run_id
          setShowRunSnapshot(true)
        }
        setTrustedViewRunId(selected)
        if (selected) await loadDetail(selected, generation)
        return next
      } catch (error) {
        if (
          error instanceof StudioGatewayError &&
          error.code === 'E_PROJECT_SERVICE_RUN_CONFLICT'
        ) {
          const existing = error.relatedRunIds[0] ?? null
          const message = '已有未完成的工作，请先继续或放弃后再试。'
          commandErrorRef.current = message
          setClientHint(message)
          if (existing) {
            setTrustedViewRunId(existing)
            setShowRunSnapshot(true)
          }
        } else if (error instanceof StudioGatewayError && error.code) {
          commandErrorRef.current = error.message
          setCommandFailure({ code: error.code, message: error.message, related_run_ids: error.relatedRunIds })
          setClientHint('本次操作未完成；本地编辑仍保留，请查看问题清单和最新处理记录。')
        } else {
          const message = error instanceof Error ? error.message : '本机工程服务操作失败'
          commandErrorRef.current = message
          setCommandFailure({ code: 'E_STUDIO_REQUEST_FAILED', message, related_run_ids: [] })
          setBoundaryError('本次操作未完成；本地编辑已保留，请检查工程状态后重试。')
        }
        return null
      } finally {
        // selector 可以在命令等待期间切换 viewRunId 并使 poll generation 失效；命令仍是唯一 owner，
        // 因此必须由它无条件释放互斥，否则一次合法浏览历史会把 Studio 永久锁在 busy。
        busyRef.current = false
        setBusy(false)
        setPollEpoch((value) => value + 1)
      }
    },
    [acceptStatus, blockAuthoring, effectiveGateway, flushAuthoring, loadDetail, setTrustedViewRunId],
  )

  const saveProject = useCallback(async (): Promise<StatusEnvelope | null> => {
    if (!draft) return null
    if (parameterDraftDirty) { setClientHint('请先应用或放弃未应用的节点设置。'); return null }
    try {
      await retrySave()
      await flushAuthoring()
      setClientHint('工程已保存。')
      return statusRef.current
    } catch (error) { setClientHint(error instanceof Error ? error.message : '保存失败，本地编辑已保留。'); return null }
  }, [draft, flushAuthoring, parameterDraftDirty, retrySave])

  const saveThenRun = useCallback(
    async (command: { readonly operation: 'run_all' } | { readonly operation: 'run_to'; readonly node_id: string } | { readonly operation: 'rerun_from_here'; readonly run_id: string; readonly node_id: string }) => {
      if (!draft) return
      if (parameterDraftDirty) {
        setClientHint('当前节点有未应用设置；请先“应用设置”或“放弃未应用更改”再运行。')
        return
      }
      try {
        const binding = await flushAuthoring()
        if (selectionGuardRef.current.parameterDraftDirty) throw new Error('保存等待期间节点设置已变化；请先应用或放弃，再运行。')
        await executeCommands([{ ...command, ...binding }], { preferCreatedRun: true })
      } catch (error) { setClientHint(error instanceof Error ? error.message : '保存未完成，未启动运行。') }
    },
    [draft, executeCommands, flushAuthoring, parameterDraftDirty],
  )

  const previewAvEnhanceV27 = useCallback(
    async (
      request: AvEnhanceV27TemplatePreviewRequestWire,
    ): Promise<AvEnhanceV27TemplatePreviewEnvelope | null> => {
      latestTemplatePreviewRef.current = null
      setClientHint(null)
      setBoundaryError(null)
      const binding = request.action === 'expand' ? await flushAuthoring() : null
      const next = await effectiveGateway.previewAvEnhanceV27(request)
      latestTemplatePreviewRef.current = {
        requestJson: JSON.stringify(request),
        envelope: next,
        precondition: binding,
      }
      return next
    },
    [effectiveGateway, flushAuthoring],
  )

  const previewAvEnhanceV27Publication = useCallback(async (request: AvEnhanceV27PublicationPreviewRequestWire) => {
    if (!effectiveGateway.previewAvEnhanceV27Publication) {
      throw new Error('当前运行服务不支持输出位置检查，请关闭旧版应用后启动更新版本。')
    }
    // 输出检查不保存 Graph，不创建工程或 Run，不能借用媒体 preview 的 mutation 授权。
    return effectiveGateway.previewAvEnhanceV27Publication(request)
  }, [effectiveGateway])

  const applyAvEnhanceV27Mutation = useCallback(
    async (
      command:
        | { readonly operation: 'create_av_enhance_v27'; readonly request: AvEnhanceV27PrepareRequestWire }
        | { readonly operation: 'expand_av_enhance_v27'; readonly request: AvEnhanceV27ExpandRequestWire },
      expectedPhase: 'preparation' | 'expanded',
    ): Promise<boolean> => {
      if (parameterDraftDirty) {
        setClientHint('当前节点有未应用设置；请先应用或放弃，再替换模板 Graph。')
        return false
      }
      const authority = latestTemplatePreviewRef.current
      const expectedAction = expectedPhase === 'preparation' ? 'prepare' : 'expand'
      const requestJson = JSON.stringify({ action: expectedAction, request: command.request })
      if (
        !authority ||
        authority.requestJson !== requestJson ||
        authority.envelope.phase !== expectedPhase ||
        !authority.envelope.profile.compatible
      ) {
        latestTemplatePreviewRef.current = null
        setClientHint('E_STUDIO_TEMPLATE_PREVIEW_REQUIRED：请重新取得 compatible server preview。')
        return false
      }
      if (command.operation === 'expand_av_enhance_v27' &&
          (!authority.precondition || dirty || JSON.stringify(authority.precondition) !== JSON.stringify(precondition()))) {
        setClientHint('工程在预览后已变化，请重新预览处理链。')
        return false
      }
      const next = await executeCommands([command.operation === 'expand_av_enhance_v27'
        ? { ...command, ...authority.precondition! } : command], { templatePreview: authority.envelope })
      if (!next) {
        latestTemplatePreviewRef.current = null
        return false
      }
      setTemplateProfile({
        status: authority.envelope.profile.status,
        compatible: authority.envelope.profile.compatible,
        modified: false,
        graphContent: next.snapshot ? graphPresentationComparison(next.snapshot.project.graph) : undefined,
      })
      latestTemplatePreviewRef.current = null
      setShowRunSnapshot(false)
      setSelectedNodeIds(new Set())
      setSelectedEdgeIds(new Set())
      setFitViewEpoch((value) => value + 1)
      return true
    },
    [dirty, executeCommands, parameterDraftDirty, precondition],
  )

  const createAvEnhanceV27 = useCallback(
    (request: AvEnhanceV27PrepareRequestWire) =>
      applyAvEnhanceV27Mutation(
        { operation: 'create_av_enhance_v27', request },
        'preparation',
      ),
    [applyAvEnhanceV27Mutation],
  )

  const expandAvEnhanceV27 = useCallback(
    (request: AvEnhanceV27ExpandRequestWire) =>
      applyAvEnhanceV27Mutation(
        { operation: 'expand_av_enhance_v27', request },
        'expanded',
      ),
    [applyAvEnhanceV27Mutation],
  )

  const hostCapabilityAvailable = useCallback(
    (capability: HostDialogCapability | 'reveal_in_file_manager' | 'open_with_system_player') =>
      hostCapabilities?.capabilities.some(
        (item) => item.capability === capability && item.available,
      ) ?? false,
    [hostCapabilities],
  )

  const pickHostPaths = useCallback(
    async (
      capability: HostDialogCapability,
      options: { readonly title?: string; readonly extensions?: ReadonlyArray<string>; readonly suggested_name?: string } = {},
    ): Promise<ReadonlyArray<string> | null> => {
      if (!hostCapabilityAvailable(capability)) {
        throw new Error('桌面文件选择器当前不可用；请重新连接或使用开发浏览器高级入口。')
      }
      const selected = await effectiveHostBridge.pick(capability, options)
      return selected?.map((item) => item.path) ?? null
    },
    [effectiveHostBridge, hostCapabilityAvailable],
  )

  const pickParameterPath = useCallback(
    async (request: ParameterPickerRequest): Promise<ReadonlyArray<string> | null> =>
      pickHostPaths(request.kind, {
        title: request.label,
        ...(request.extensions.length > 0 ? { extensions: request.extensions } : {}),
      }),
    [pickHostPaths],
  )

  const pickTemplateOutputDirectory = useCallback(async (): Promise<string | null> => {
    if (!hostCapabilityAvailable('select_directory')) throw new Error('文件夹选择器当前不可用，请重新连接。')
    const flight = ++templateOutputPickerFlightRef.current
    const selected = await effectiveHostBridge.pick('select_directory', { title: '选择成片输出目录' })
    if (flight !== templateOutputPickerFlightRef.current || !selected?.length) return null
    if (selected.length !== 1) throw new Error('输出位置只能选择一个文件夹。')
    templateOutputSelectionRef.current = selected[0]!
    return selected[0]!.path
  }, [effectiveHostBridge, hostCapabilityAvailable])

  const revealTemplateOutputDirectory = useCallback(async (selectedPath: string): Promise<void> => {
    const selected = templateOutputSelectionRef.current
    if (!selected || selected.path !== selectedPath) throw new Error('请先使用文件夹选择器重新选择当前输出位置，再打开文件夹。')
    if (!hostCapabilityAvailable('reveal_in_file_manager')) throw new Error('当前无法打开文件管理器，请检查桌面连接后重试。')
    // path 仅核对当前控件是否仍是本次选择；系统动作只接收服务签发的 selection handle。
    await effectiveHostBridge.launch('reveal_in_file_manager', {
      kind: 'picker_selection', selection_handle: selected.selection_handle,
    })
  }, [effectiveHostBridge, hostCapabilityAvailable])

  const startPreparationRun = useCallback(async (): Promise<string | null> => {
    const binding = await flushAuthoring()
    if (selectionGuardRef.current.parameterDraftDirty) throw new Error('请先应用或放弃未应用的节点设置，再运行。')
    const next = await executeCommands([{ operation: 'run_all', ...binding }], { preferCreatedRun: true })
    // 只绑定本次 command response 明确返回的 active_run_id；不得从历史、时间或节点形状猜测。
    return next?.active_run_id ?? null
  }, [executeCommands, flushAuthoring])

  useEffect(() => {
    if (fitViewEpoch === 0) return
    const timer = window.setTimeout(() => {
      void fitView({ duration: 350, padding: 0.16 })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [fitView, fitViewEpoch])

  const handoffIsCurrent = useCallback((nodeRun: NodeRunWire, generation: number): boolean => {
    const current = latestNodeRuns(detailRef.current?.run ?? null).get(nodeRun.node_id)
    return generation === generationRef.current && viewRunIdRef.current === nodeRun.run_id &&
      current?.state === 'waiting_external' && current.node_run_id === nodeRun.node_run_id &&
      current.external_handoff?.handoff_id === nodeRun.external_handoff?.handoff_id
  }, [])

  const handoffImport = useHandoffImport({
    hostBridge: effectiveHostBridge,
    projectSessionId: status?.project_session_id ?? null,
    scope: JSON.stringify([status?.project_session_id, viewRunId, [...selectedNodeIds].sort(), showRunSnapshot,
      selectedNodeRun?.node_run_id, selectedNodeRun?.external_handoff?.handoff_id, selectedNodeRun?.state, currentRun?.state]),
    operationRef: handoffActionRef,
    canStart: () => !busyRef.current && !homeActionBusyRef.current && !operationActive && !health.status.stale && !health.detail.stale,
    isCurrent: (nodeRun) => !health.status.stale && !health.detail.stale && selectionGuardRef.current.nodeIds.size === 1 &&
      selectionGuardRef.current.nodeIds.has(nodeRun.node_id) && handoffIsCurrent(nodeRun, generationRef.current),
    onBusyChange: (active) => { busyRef.current = active; setBusy(active) },
    onImportStarted: (nodeRun) => {
      const key = handoffResourceKey(nodeRun.run_id, nodeRun.node_run_id, nodeRun.external_handoff!.handoff_id)
      updateCheckedOutputs((previous) => { const next = new Map(previous); next.delete(key); return next })
      setReadiness((previous) => { const next = new Map(previous); next.delete(nodeRun.node_run_id); return next })
    },
    onImported: async (nodeRun) => { await loadReadiness(nodeRun.run_id, nodeRun.node_run_id, false, generationRef.current) },
  })

  const checkOutput = useCallback(async (nodeRun: NodeRunWire) => {
    const generation = generationRef.current
    if (!nodeRun.external_handoff || handoffActionRef.current || busyRef.current ||
        health.status.stale || health.detail.stale || !handoffIsCurrent(nodeRun, generation)) return
    const token = Symbol('check-output')
    handoffActionRef.current = token
    setCheckingNodeRunId(nodeRun.node_run_id)
    const key = handoffResourceKey(nodeRun.run_id, nodeRun.node_run_id, nodeRun.external_handoff.handoff_id)
    updateCheckedOutputs((current) => { const next = new Map(current); next.delete(key); return next })
    try {
      const checked = await loadReadiness(nodeRun.run_id, nodeRun.node_run_id, true, generation)
      if (handoffActionRef.current !== token || !handoffIsCurrent(nodeRun, generation)) return
      if (!checked || !isFullCheck(checked, nodeRun)) {
        setClientHint('输出尚未通过完整检查。请按问题提示替换文件，再检查；本次没有提交产物。')
        return
      }
      updateCheckedOutputs((current) => new Map(current).set(key, checked))
      setClientHint('输出检查通过。确认外部工具已完成写入后，可点击“提交并继续”。')
    } finally {
      if (handoffActionRef.current === token) { handoffActionRef.current = null; setCheckingNodeRunId(null) }
    }
  }, [handoffIsCurrent, health.detail.stale, health.status.stale, loadReadiness, updateCheckedOutputs])

  const submitOutput = useCallback(async (nodeRun: NodeRunWire) => {
    const generation = generationRef.current
    const handoff = nodeRun.external_handoff
    if (!handoff || handoffActionRef.current || busyRef.current || health.status.stale ||
        health.detail.stale || health.readiness.stale || !handoffIsCurrent(nodeRun, generation)) return
    const key = handoffResourceKey(nodeRun.run_id, nodeRun.node_run_id, handoff.handoff_id)
    const previous = checkedOutputsRef.current.get(key)
    if (!previous || !isFullCheck(previous, nodeRun)) { setClientHint('请先检查输出，再显式提交。'); return }
    const token = Symbol('submit-output')
    handoffActionRef.current = token
    setSubmittingNodeRunId(nodeRun.node_run_id)
    try {
      // 用户确认与检查分开；确认时重新完整预检，替换文件必须回到检查步骤，不能暗中提交新文件。
      const fresh = await loadReadiness(nodeRun.run_id, nodeRun.node_run_id, true, generation)
      if (handoffActionRef.current !== token || !handoffIsCurrent(nodeRun, generation)) return
      if (!fresh || !isFullCheck(fresh, nodeRun) || !sameObservedOutputs(previous, fresh)) {
        updateCheckedOutputs((current) => { const next = new Map(current); next.delete(key); return next })
        setClientHint('输出已变化或未通过最新检查；没有提交。请重新检查输出，再确认提交。')
        return
      }
      await executeCommands([{ operation: 'submit_external', run_id: nodeRun.run_id,
        node_run_id: nodeRun.node_run_id, handoff_id: handoff.handoff_id }])
      updateCheckedOutputs((current) => { const next = new Map(current); next.delete(key); return next })
    } finally {
      if (handoffActionRef.current === token) { handoffActionRef.current = null; setSubmittingNodeRunId(null) }
    }
  }, [executeCommands, handoffIsCurrent, health.detail.stale, health.readiness.stale, health.status.stale, loadReadiness, updateCheckedOutputs])

  const requestRerun = useCallback(async (nodeId: string, runId: string) => {
    if (busyRef.current || selectionGuardRef.current.parameterDraftDirty) {
      setClientHint('请先完成当前操作，并应用或放弃未应用的设置。'); return
    }
    const token = Symbol('rerun-preview')
    const generation = generationRef.current
    retryTokenRef.current = token
    retryBindingRef.current = null
    setRetryOpen(true)
    setRetryPreview(null)
    setRetryError(null)
    setRetryBusy(true)
    try {
      const binding = await flushAuthoring()
      if (generation !== generationRef.current || retryTokenRef.current !== token) return
      if (selectionGuardRef.current.parameterDraftDirty) throw new Error('节点设置已变化；请先应用或放弃，再预览重跑影响。')
      if (!effectiveGateway.previewRerun) throw new Error('当前服务尚不支持重跑影响预览，请同步更新前后端。')
      const request: RerunPreviewRequest = { operation: 'rerun_from_here', run_id: runId, node_id: nodeId, ...binding }
      const preview = await effectiveGateway.previewRerun(request)
      if (generation !== generationRef.current || retryTokenRef.current !== token) return
      if (preview.run_id !== runId || preview.node_id !== nodeId || preview.project_session_id !== binding.project_session_id ||
          preview.storage_revision !== binding.expected_storage_revision || JSON.stringify(precondition()) !== JSON.stringify(binding)) {
        throw new Error('工程在预览期间已变化，请重新预览重跑影响。')
      }
      retryBindingRef.current = { request, generation }
      setRetryPreview(preview)
    } catch (error) {
      if (retryTokenRef.current === token) setRetryError(safeVisibleServiceError(error instanceof Error ? error.message : '无法读取重跑影响', '暂时无法预览重跑影响；请检查步骤设置与输入文件。'))
    } finally {
      if (retryTokenRef.current === token) setRetryBusy(false)
    }
  }, [effectiveGateway, flushAuthoring, precondition])

  const confirmRerun = async () => {
    const binding = retryBindingRef.current
    if (!binding || !retryPreview || retryBusy || busyRef.current) return
    if (health.status.stale || health.detail.stale || status?.active_operation) {
      setRetryError('当前任务状态尚不可用于重跑；请等待服务恢复后重新预览。'); return
    }
    try {
      const current = precondition()
      if (binding.generation !== generationRef.current || binding.request.run_id !== viewRunIdRef.current ||
          dirty || selectionGuardRef.current.parameterDraftDirty || current.project_session_id !== binding.request.project_session_id ||
          current.expected_storage_revision !== binding.request.expected_storage_revision) throw new Error('changed')
    } catch { setRetryError('工程或查看的任务已变化；请关闭此窗口，重新预览重跑影响。'); return }
    setRetryBusy(true)
    const result = await executeCommands([binding.request], { preferCreatedRun: true })
    setRetryBusy(false)
    if (result) { setRetryOpen(false); retryBindingRef.current = null }
    else setRetryError('未能开始重跑。已保留原有记录，请检查任务状态后重新预览。')
  }

  const copyPath = useCallback(async (path: string) => {
    try {
      await navigator.clipboard.writeText(path)
      setClientHint('路径已复制。')
    } catch {
      setClientHint('浏览器未授予剪贴板权限，请手动复制显示的路径。')
    }
  }, [])

  const loadOlderRuns = useCallback(async () => {
    const cursor = historyCursorRef.current
    if (!cursor || historyBusy) return
    setHistoryBusy(true)
    historyPagingStartedRef.current = true
    const generation = generationRef.current
    const sequence = ++sequenceRef.current.history
    try {
      const page = await effectiveGateway.listRuns(cursor, 20)
      if (generation !== generationRef.current || sequence <= acceptedRef.current.history) return
      acceptedRef.current.history = sequence
      // 已由新鲜 status 见过的同一 Run summary 优先于较旧分页投影。
      const merged = mergeSummaries(page.run_summaries, historySummariesRef.current)
      historySummariesRef.current = merged
      historyCursorRef.current = page.next_run_cursor
      setHistorySummaries(merged)
      setHistoryCursor(page.next_run_cursor)
    } catch (error) {
      if (
        generation === generationRef.current &&
        sequence === sequenceRef.current.history
      ) {
        setClientHint(error instanceof Error ? error.message : 'Run 历史读取失败')
      }
    } finally {
      if (
        generation === generationRef.current &&
        sequence === sequenceRef.current.history
      ) {
        setHistoryBusy(false)
      }
    }
  }, [effectiveGateway, historyBusy])

  const catalogGroups = groupStudioDefinitions(
    draft?.definitions ?? [],
    query,
    presentationEnvelope?.catalog ?? null,
  )
  const singleSelectedNodeId = selectedNodeIds.size === 1 ? selectedNode?.node_id ?? null : null
  const rerunId = currentRun?.run_id ?? null
  const rerunNodeIncluded = singleSelectedNodeId
    ? runLatestAttempts.has(singleSelectedNodeId)
    : false
  const serviceBusy = busy || operationActive || submittingNodeRunId !== null
  const runBlocked = serviceBusy || homeActionBusy || health.status.stale || !draft || diagnostics.length > 0 || parameterDraftDirty || !!authoring.error
  const detailMutationBlocked = serviceBusy || homeActionBusy || health.status.stale || health.detail.stale
  const firstWaiting = waitingNodeRuns[0] ?? null
  const firstWaitingObserved = firstWaiting ? readiness.get(firstWaiting.node_run_id) ?? null : null
  const firstWaitingChecked = firstWaiting?.external_handoff ? checkedOutputs.get(handoffResourceKey(
    firstWaiting.run_id, firstWaiting.node_run_id, firstWaiting.external_handoff.handoff_id)) : null
  const firstWaitingCheckCurrent = !!firstWaiting && !!firstWaitingChecked && !!firstWaitingObserved &&
    isFullCheck(firstWaitingChecked, firstWaiting) && sameObservedOutputs(firstWaitingChecked, firstWaitingObserved)
  const firstFailed = [...runLatestAttempts.values()].find(
    (nodeRun) => nodeRun.state === 'failed',
  ) ?? null
  const globalActionSummary =
    allSummaries.find((summary) => summary.requires_operator_action) ?? null
  const firstWaitingInputPaths = firstWaiting?.external_handoff?.input_artifact_ids.map(
    (artifactId) => artifactsById.get(artifactId)?.path ?? `未解析 Artifact：${artifactId}`,
  ) ?? []
  const snapshotChanged =
    !!currentRun && !!draft && currentRun.graph_snapshot !== draft.project.graph &&
    JSON.stringify(currentRun.graph_snapshot) !== JSON.stringify(draft.project.graph)
  const hasAvEnhanceV27Nodes =
    draft?.project.graph.nodes.some((node) => node.type_id.startsWith('zniku.avenhance.v27.')) ??
    false
  const visibleTemplateProfile =
    (templateProfile && draft && templateProfile.graphContent !== undefined && templateProfile.graphContent !== graphPresentationComparison(draft.project.graph)
      ? { ...templateProfile, modified: true, compatible: false } : templateProfile) ??
    (hasAvEnhanceV27Nodes
      ? { status: 'v2.7 nodes · profile check required', compatible: false, modified: false }
      : null)

  const beginHomeAction = (): number | null => {
    if (homeActionBusyRef.current || busyRef.current) return null
    homeActionBusyRef.current = true
    setHomeActionBusy(true)
    homeActionEpochRef.current += 1
    return homeActionEpochRef.current
  }

  const homeActionIsCurrent = (epoch: number): boolean =>
    homeActionBusyRef.current && homeActionEpochRef.current === epoch

  const finishHomeAction = (epoch: number) => {
    if (homeActionEpochRef.current !== epoch) return
    homeActionBusyRef.current = false
    setHomeActionBusy(false)
  }

  const invalidateHomeAction = () => {
    homeActionEpochRef.current += 1
    homeActionBusyRef.current = false
    setHomeActionBusy(false)
  }

  const locateTemplateNode = (nodeId: string) => {
    if (!draft?.project.graph.nodes.some((node) => node.node_id === nodeId)) {
      setClientHint(`Template preview diagnostic 指向 ${nodeId}；该节点尚未写入当前 Project。`)
      return
    }
    if (!changeSelection(new Set([nodeId]), new Set())) return
    setTemplateOpen(false)
    setShowRunSnapshot(false)
  }

  const openExistingProject = async (): Promise<void> => {
    const epoch = beginHomeAction()
    if (epoch === null) return
    setHostError(null)
    setHostErrorDetails(null)
    try {
      const paths = await pickHostPaths('open_file', {
        title: '打开 ZNIKU 工程',
        extensions: ['.zniku'],
      })
      if (!paths?.[0] || !homeActionIsCurrent(epoch)) return
      const next = await executeCommands([{ operation: 'open_project', path: paths[0] }])
      if (!homeActionIsCurrent(epoch)) return
      if (next) setHomeOpen(false)
      else setHostError('无法打开所选工程；请确认文件仍然存在且未被其他程序占用。')
    } catch (error) {
      if (!homeActionIsCurrent(epoch)) return
      setHostError(formatHostBridgeError(error) ?? '无法打开工程选择器；工程和媒体都没有被修改。')
      setHostErrorDetails(error instanceof Error ? error.message : String(error))
      setHomeOpen(true)
    } finally {
      finishHomeAction(epoch)
    }
  }

  const createBlankProject = async (name: string): Promise<void> => {
    const epoch = beginHomeAction()
    if (epoch === null) return
    setHostError(null)
    setHostErrorDetails(null)
    try {
      const paths = await pickHostPaths('save_file', {
        title: '保存空白 ZNIKU 工程',
        suggested_name: `${name.replace(/[<>:"/\\|?*]+/g, '-').replace(/[. ]+$/g, '') || '未命名视频工程'}.zniku`,
        extensions: ['.zniku'],
      })
      if (!paths?.[0] || !homeActionIsCurrent(epoch)) return
      const next = await executeCommands([{ operation: 'create_project', path: paths[0], name }])
      if (!homeActionIsCurrent(epoch)) return
      if (next) setHomeOpen(false)
      else setHostError('无法创建空白工程；已有文件不会被覆盖，请选择其他位置。')
    } catch (error) {
      if (homeActionIsCurrent(epoch)) {
        setHostError(formatHostBridgeError(error) ?? '无法创建空白工程；已有文件和媒体都没有被修改。')
        setHostErrorDetails(error instanceof Error ? error.message : String(error))
      }
    } finally {
      finishHomeAction(epoch)
    }
  }

  const openRecentProject = async (path: string): Promise<void> => {
    const epoch = beginHomeAction()
    if (epoch === null) return
    setHostError(null)
    setHostErrorDetails(null)
    try {
      const next = await executeCommands([{ operation: 'open_project', path }])
      if (!homeActionIsCurrent(epoch)) return
      if (next) setHomeOpen(false)
      else setHostError('这个最近工程当前无法打开；请重新选择文件，最近列表不会代替正式工程。')
    } finally {
      finishHomeAction(epoch)
    }
  }

  const launchArtifact = async (
    capability: 'reveal_in_file_manager' | 'open_with_system_player',
    artifactId: string,
  ): Promise<void> => {
    if (!currentRun) return
    try {
      await effectiveHostBridge.launch(capability, {
        kind: 'artifact',
        run_id: currentRun.run_id,
        artifact_id: artifactId,
      })
      setHostError(null)
    } catch (error) {
      const message = error instanceof Error ? error.message : '本机文件操作失败。'
      setHostError(message)
      setHostErrorDetails(null)
      setClientHint(message)
    }
  }

  const nodeLabel = (nodeId: string): string => {
    return nodeLabels.get(nodeId) ?? (advanced ? nodeId : '历史步骤')
  }
  const launchHandoff = async (nodeRun: NodeRunWire, capability: HostSystemCapability,
    selector: Extract<HostPathReference, { readonly kind: 'handoff' }>['selector']) => {
    if (!nodeRun.external_handoff || !handoffIsCurrent(nodeRun, generationRef.current)) return
    try {
      await effectiveHostBridge.launch(capability, { kind: 'handoff', run_id: nodeRun.run_id,
        node_run_id: nodeRun.node_run_id, handoff_id: nodeRun.external_handoff.handoff_id, selector })
      setHostError(null)
    } catch {
      setClientHint('无法打开本机文件位置；请重新连接桌面能力，或使用“复制路径”。')
    }
  }
  const locateCurrentNode = (nodeId: string) => {
    if (!changeSelection(new Set([nodeId]), new Set())) return
    setShowRunSnapshot(false)
    void fitView({ nodes: [{ id: nodeId }], padding: 0.3, maxZoom: 1.2, duration: 0 })
  }
  const locateRunNode = (nodeId: string) => {
    if (!changeSelection(new Set([nodeId]), new Set())) return
    setShowRunSnapshot(true)
    void fitView({ nodes: [{ id: nodeId }], padding: 0.3, maxZoom: 1.2, duration: 0 })
  }
  const openExternalAssistant = (nodeId: string) => {
    locateRunNode(nodeId)
    // 定位节点后把操作步骤滚入侧栏；仅改变视图，不改变交接或 Run 身份。
    requestAnimationFrame(() => document.getElementById('external-processing-assistant')?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }))
  }
  const blockedRunReason = health.status.stale ? '本机服务连接已中断；请重新连接。'
    : serviceBusy || homeActionBusy ? '当前操作尚未完成，请稍候。'
      : !draft ? '请先新建或打开工程。'
        : parameterDraftDirty ? '请先应用或放弃未应用的节点设置。'
          : authoring.error ? '工程尚未保存；请先处理保存问题。'
            : diagnostics.length > 0 ? '请修复标出的必填设置或连接。' : undefined
  const blockedDetailReason = detailMutationBlocked ? '当前任务状态不可用于操作；请等待或重新连接。'
    : parameterDraftDirty ? '请先应用或放弃未应用的设置。' : undefined
  const repairGraph = () => {
    setBottomOpen(true)
    const diagnostic = diagnostics.find((item) => item.node_id || item.edge_id)
    if (diagnostic?.node_id) locateCurrentNode(diagnostic.node_id)
    else if (diagnostic?.edge_id && changeSelection(new Set(), new Set([diagnostic.edge_id]))) setShowRunSnapshot(false)
    else if (parameterDraftDirty && selectedNode) locateCurrentNode(selectedNode.node_id)
  }
  const runningStep = [...runLatestAttempts.values()].find((item) => item.state === 'running')
  const outputStep = [...runLatestAttempts.values()].reverse().find((item) => item.state === 'completed' && item.output_artifact_ids.length > 0)
  const completedStep = [...runLatestAttempts.values()].reverse().find((item) => item.state === 'completed')
  const executionChanged = !!currentRun && !!draft && graphPresentationComparison(currentRun.graph_snapshot) !== graphPresentationComparison(draft.project.graph)
  const hasStaleResult = status?.latest_results.some((item) => item.stale) ?? false
  const primaryAction = health.status.stale ? {
    label: '重新连接', disabled: loading, reason: '与本机服务的连接已中断，保留最后可信状态。',
    onAction: () => setReconnectEpoch((value) => value + 1),
  } : !draft ? {
    label: '新建或打开工程', disabled: loading, reason: loading ? '正在连接本机工程服务。' : undefined,
    onAction: () => setHomeOpen(true),
  } : runningStep ? {
    label: '查看当前进度', disabled: false, onAction: () => locateRunNode(runningStep.node_id),
  } : firstWaiting ? {
    label: '继续外部处理', disabled: false, onAction: () => openExternalAssistant(firstWaiting.node_id),
  } : firstFailed ? {
    label: '查看问题并重试此步骤', disabled: detailMutationBlocked || parameterDraftDirty,
    reason: blockedDetailReason, onAction: () => { locateRunNode(firstFailed.node_id); void requestRerun(firstFailed.node_id, firstFailed.run_id) },
  } : globalActionSummary && globalActionSummary.run_id !== viewRunId ? {
    label: '查看待处理任务', disabled: false, onAction: () => selectRun(globalActionSummary.run_id),
  } : parameterDraftDirty || diagnostics.length > 0 ? {
    label: '修复设置或连接', disabled: false, reason: blockedRunReason, onAction: repairGraph,
  } : authoring.error ? {
    label: '处理保存问题', disabled: false, reason: '本地编辑仍保留，尚不能开始处理。', onAction: () => { void saveProject() },
  } : viewedSummary?.state === 'completed' && !executionChanged && !hasStaleResult ? {
    label: outputStep ? '查看输出' : '查看完成记录', disabled: false,
    reason: outputStep ? undefined : '此任务没有声明输出文件；已完成的步骤记录仍保留。',
    onAction: () => { if (outputStep ?? completedStep) locateRunNode((outputStep ?? completedStep)!.node_id); else setBottomOpen(true) },
  } : {
    label: '开始处理', disabled: runBlocked, reason: blockedRunReason, onAction: () => { void saveThenRun({ operation: 'run_all' }) },
  }
  const editStudioState = (label: string, updater: (state: StudioStateWire) => StudioStateWire) => {
    if (!graphEditable || busy || homeActionBusy || parameterDraftDirty) return
    editAuthoring(label, (current) => ({ ...current, studioState: updater(current.studioState) }))
  }
  const updateNodeView = (nodeId: string, patch: Partial<StudioStateWire['node_views'][number]>) => {
    editStudioState('编辑节点展示', (state) => {
      const existing = state.node_views.find((view) => view.node_id === nodeId)
        ?? { node_id: nodeId, display_name: null, collapsed: false, group_id: null }
      return { ...state, node_views: [...state.node_views.filter((view) => view.node_id !== nodeId), { ...existing, ...patch }] }
    })
  }
  const orderedTarget = selectedNode ?? graph.nodes.find((node) => node.node_id === selectedEdge?.target_node_id)
  const orderedDefinition = orderedTarget ? definitionForNode(orderedTarget, definitions) : null
  const orderedInputs = orderedDefinition?.input_ports.filter((port) => port.cardinality === 'ordered_many')
    .filter((port) => !selectedEdge || port.port_id === selectedEdge.target_port_id)
    .map((port) => ({
      label: `${presentationsByKey.get(`${orderedDefinition.type_id}@${orderedDefinition.version}`)?.ports.find((item) => item.direction === 'input' && item.port_id === port.port_id)?.label ?? port.port_id} · 输入顺序`,
      edges: graph.edges.filter((edge) => edge.target_node_id === orderedTarget!.node_id && edge.target_port_id === port.port_id),
    })) ?? []
  const authoringPanel = studioState && graphEditable ? <AuthoringViewPanel
    key={status?.project_session_id ?? 'empty'}
    studioState={studioState}
    selectedNode={selectedNode}
    selectedNodeTitle={selectedPresentation?.title}
    selectedNodeIds={selectedNodeIds}
    disabled={busy || homeActionBusy || parameterDraftDirty}
    onEditStudioState={editStudioState}
    onUpdateNodeView={updateNodeView}
  /> : null

  return (
    <main className={`app-shell studio-workspace ${bottomOpen ? 'has-bottom-drawer' : ''}`}>
      <nav className="studio-skip-links" aria-label="跳转到工作区"><a href="#workflow-canvas">跳到画布</a><a href="#node-palette">跳到节点面板</a><a href="#node-inspector">跳到步骤设置</a></nav>
      {retryOpen && <RetryImpactDialog preview={retryPreview} nodeLabel={nodeLabel} busy={retryBusy} error={retryError}
        onConfirm={() => void confirmRerun()} onCancel={() => { retryTokenRef.current = null; retryBindingRef.current = null; setRetryOpen(false); setRetryBusy(false) }} />}
      <ProjectHome
        desktopControls={homeOpen && <DesktopExit hostBridge={effectiveHostBridge} unsaved={dirty || parameterDraftDirty || authoring.saving} operationBusy={handoffImport.busy} />}
        busy={serviceBusy || homeActionBusy}
        hasOpenProject={draft !== null}
        hostBridgeAvailable={hostCapabilityAvailable('open_file') && hostCapabilityAvailable('save_file')}
        loading={loading}
        onClose={() => {
          invalidateHomeAction()
          setHomeOpen(false)
        }}
        onCreateBlank={createBlankProject}
        onCreateGuided={() => {
          invalidateHomeAction()
          setTemplateMode('create')
          setHomeOpen(false)
          setTemplateOpen(true)
        }}
        onOpenExisting={openExistingProject}
        onOpenRecent={openRecentProject}
        onRetryService={() => setReconnectEpoch((value) => value + 1)}
        open={homeOpen}
        recentProjects={recentProjects}
        serviceMessage={statusHealth.stale ? '无法连接本机工程服务；工程和媒体都没有被修改。' : hostError}
        serviceErrorDetails={statusHealth.stale ? null : hostErrorDetails}
        serviceUnavailable={!loading && statusHealth.stale}
      />
      <AvEnhanceV27Wizard
        busy={serviceBusy || homeActionBusy || health.status.stale}
        currentProjectId={projectId}
        currentProjectName={projectName}
        currentProjectPath={projectPath}
        currentSnapshot={draft}
        mode={templateMode}
        onClose={() => setTemplateOpen(false)}
        onCreate={createAvEnhanceV27}
        onExpand={expandAvEnhanceV27}
        onLocateNode={locateTemplateNode}
        onPickOutputDirectory={pickTemplateOutputDirectory}
        onRevealOutputDirectory={revealTemplateOutputDirectory}
        onPickProjectPath={async (suggestedName) => (await pickHostPaths('save_file', {
          title: '保存 ZNIKU 视频工程',
          suggested_name: suggestedName,
          extensions: ['.zniku'],
        }))?.[0] ?? null}
        onPickSources={async (multiple) => await pickHostPaths(
          multiple ? 'open_files' : 'open_file',
          { title: multiple ? '选择全部章节视频' : '选择视频素材' },
        )}
        onPreview={previewAvEnhanceV27}
        onPreviewPublication={previewAvEnhanceV27Publication}
        onStartPreparationRun={startPreparationRun}
        open={templateOpen}
        pickerAvailable={hostCapabilityAvailable('open_file') && hostCapabilityAvailable('save_file') && hostCapabilityAvailable('select_directory')}
        projectIdFactory={projectIdFactory}
        runSummaries={allSummaries}
        serviceError={boundaryError ? '本机工程服务暂时不可用；当前工程和媒体没有被修改。' : null}
      />
      <ProjectShell
        desktopControls={!homeOpen && <DesktopExit hostBridge={effectiveHostBridge} unsaved={dirty || parameterDraftDirty || authoring.saving} operationBusy={handoffImport.busy} />}
        projectName={draft?.project.name ?? null}
        projectId={draft?.project.project_id ?? null}
        nodeCount={draft?.project.graph.nodes.length ?? 0}
        dirty={dirty}
        saving={authoring.saving}
        draftBlocked={diagnostics.length > 0}
        saveError={authoring.error}
        canUndo={graphEditable && !parameterDraftDirty && authoring.canUndo}
        canRedo={graphEditable && !parameterDraftDirty && authoring.canRedo}
        advanced={advanced}
        onUndo={() => travel('undo')}
        onRedo={() => travel('redo')}
        onToggleAdvanced={() => {
          const next = !advanced
          setAdvanced(next)
          try { localStorage.setItem('zniku.studio.density', next ? 'advanced' : 'creator') } catch { /* 个人偏好不可写不阻断工程编辑。 */ }
        }}
        onReloadProject={() => {
          const path = statusRef.current?.project_path
          if (!path || !window.confirm('重新载入将放弃本地未保存的编辑和撤销历史。是否继续？')) return
          void executeCommands([{ operation: 'open_project', path }], { discardLocal: true })
        }}
        profile={visibleTemplateProfile}
        projectPath={projectPath}
        projectIdDraft={projectId}
        projectNameDraft={projectName}
        serviceBusy={busy || homeActionBusy}
        projectSwitchBlocked={serviceBusy || homeActionBusy}
        statusStale={health.status.stale}
        canSave={!busy && !homeActionBusy && !health.status.stale && !!draft && dirty && !parameterDraftDirty}
        hostBridgeAvailable={hostCapabilityAvailable('open_file')}
        canResumeGuided={hasAvEnhanceV27Nodes}
        onProjectPathChange={setProjectPath}
        onOpenTemplates={() => {
          if (changeSelection(new Set(), new Set())) {
            setTemplateMode('resume')
            setTemplateOpen(true)
          }
        }}
        onHome={() => {
          if (!changeSelection(new Set(), new Set())) return
          invalidateHomeAction()
          setHomeOpen(true)
        }}
        onOpenWithPicker={() => void openExistingProject()}
        onOpenProject={() => {
          if (changeSelection(new Set(), new Set())) void executeCommands([{ operation: 'open_project', path: projectPath.trim() }])
        }}
        onCreateProject={() => {
          if (changeSelection(new Set(), new Set())) void executeCommands([{ operation: 'create_project', path: projectPath.trim(), name: projectName.trim() }])
        }}
        onSaveProject={() => void saveProject()}
        runCenter={(
          <RunCenter
            advanced={advanced}
            nodeLabel={nodeLabel}
            primaryAction={primaryAction}
            blockedReasons={{ runAll: blockedRunReason,
              runTo: blockedRunReason ?? (!singleSelectedNodeId ? '先选择一个步骤。' : showRunSnapshot ? '请切回当前工作流后再执行局部处理。' : undefined),
              rerun: blockedDetailReason ?? (!singleSelectedNodeId ? '先选择一个步骤。' : !rerunId || !rerunNodeIncluded ? '所选步骤不属于当前查看任务的执行范围。' : undefined) }}
            onRecoverService={() => setReconnectEpoch((value) => value + 1)}
            health={health}
            status={status}
            summaries={allSummaries}
            viewRunId={viewRunId}
            runBlocked={runBlocked}
            runToBlocked={runBlocked || !singleSelectedNodeId || showRunSnapshot}
            rerunBlocked={detailMutationBlocked || parameterDraftDirty || !singleSelectedNodeId || !rerunId || !rerunNodeIncluded}
            onSelectRun={selectRun}
            onRunAll={() => void saveThenRun({ operation: 'run_all' })}
            onRunTo={() => singleSelectedNodeId && void saveThenRun({ operation: 'run_to', node_id: singleSelectedNodeId })}
            onRerun={() => singleSelectedNodeId && rerunId && void requestRerun(singleSelectedNodeId, rerunId)}
          />
        )}
      />

      <NodePalette
        advanced={advanced}
        definitionCount={draft?.definitions.length ?? 0}
        projectId={projectId}
        projectName={projectName}
        query={query}
        groups={catalogGroups}
        canEditGraph={!!draft && graphEditable}
        busy={busy || homeActionBusy}
        selectedNodeCount={selectedNodeIds.size}
        selectedEdgeCount={selectedEdgeIds.size}
        onProjectNameChange={setProjectName}
        onQueryChange={setQuery}
        onAddDefinition={addDefinition}
        onCopySelection={copySelected}
        onDeleteSelection={deleteSelected}
      />

      <GraphCanvas
        key={status?.project_session_id ?? 'empty'}
        graph={graph}
        definitions={definitions}
        groups={(studioState?.groups ?? []).map((group) => ({ ...group, node_ids: (studioState?.node_views ?? []).filter((view) => view.group_id === group.group_id && graph.nodes.some((node) => node.node_id === view.node_id)).map((view) => view.node_id) }))}
        viewport={studioState?.viewport ?? null}
        onViewportChange={(viewport) => editStudioState('调整画布视口', (state) => ({ ...state, viewport }))}
        onToggleGroup={(groupId) => editStudioState('折叠分组', (state) => ({ ...state, groups: state.groups.map((group) => group.group_id === groupId ? { ...group, collapsed: !group.collapsed } : group) }))}
        onAutoLayout={() => { if (!parameterDraftDirty) updateGraph(autoLayoutGraph) }}
        definitionLabel={(definition) => presentationsByKey.get(`${definition.type_id}@${definition.version}`)?.title ?? definition.type_id}
        portLabel={(definition, direction, portId) => presentationsByKey.get(`${definition.type_id}@${definition.version}`)?.ports.find((port) => port.direction === direction && port.port_id === portId)?.label ?? portId}
        onAddConnectedNodes={(request) => {
          if (!draft || parameterDraftDirty) return
          const result = addConnectedNodes(draft.project.graph, draft.definitions, request.sources, request.definition, request.targetPortId, nodeIdFactory, request.position)
          if (!result) { setClientHint('图已变化，无法连接这些端口，请重新选择。'); return }
          editAuthoring('添加下一步', (current) => ({ ...current, snapshot: replaceGraph(current.snapshot, result.graph) }))
          setSelectedNodeIds(new Set(result.added_node_ids))
          setSelectedEdgeIds(new Set())
        }}
        onNodeDragStart={beginMove}
        onNodeDragStop={endMove}
        nodes={flowNodes}
        edges={flowEdges}
        editable={graphEditable}
        busy={busy || homeActionBusy}
        advanced={advanced}
        showingSnapshot={showRunSnapshot && currentRun !== null}
        modeLabel={showRunSnapshot && currentRun ? advanced ? 'Run snapshot' : '本次处理的工作流' : advanced ? 'Current Graph' : '当前工作流'}
        contextLabel={showRunSnapshot && currentRun ? advanced && viewedSummary ? `${viewedSummary.run_id} · ${targetLabel(viewedSummary)} · ${viewedSummary.state}` : '只读记录；编辑当前工作流不改变这次处理。' : '自由编辑，修改将用于下一次处理。'}
        snapshotChanged={snapshotChanged}
        canToggleSnapshot={currentRun !== null}
        loading={loading}
        boundaryError={boundaryError}
        hasProject={draft !== null}
        onToggleSnapshot={() => {
          if (!changeSelection(new Set(), new Set())) return
          setShowRunSnapshot((value) => !value)
        }}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onSelectionChange={handleSelection}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        onConnect={handleConnect}
        isValidConnection={connectionIsValid}
        overlays={(
          <RunCanvasOverlays
            advanced={advanced}
            nodeLabel={nodeLabel}
            viewedSummary={viewedSummary}
            firstWaiting={firstWaiting}
            firstWaitingInputPaths={firstWaitingInputPaths}
            firstWaitingReadinessLabel={health.readiness.stale ? '检测离线，保留上次观察' : firstWaitingCheckCurrent ? '完整检查通过，等待你提交' : firstWaiting ? readinessLabel(firstWaitingObserved) : ''}
            firstWaitingElapsedLabel={firstWaiting ? elapsedLabel(firstWaiting.started_at ?? firstWaiting.created_at) : ''}
            globalActionSummary={globalActionSummary}
            firstFailed={firstFailed}
            sameRun={globalActionSummary?.run_id === viewRunId}
            onLocateNode={locateRunNode}
            onSelectRun={selectRun}
          />
        )}
      />

      <NodeInspector
        advanced={advanced}
        mediaPreview={status?.project_session_id && selectedNodeRun && <MediaPreview
          key={`${status.project_session_id}:${selectedNodeRun.run_id}:${selectedNodeRun.node_run_id}:${selectedNodeRun.state}`}
          hostBridge={effectiveHostBridge} projectSessionId={status.project_session_id}
          candidates={previewCandidates} disabled={health.detail.stale || health.status.stale} advanced={advanced}
        />}
        advancedDetailsOpen={advanced || runtimeDiagnosticsOpen}
        onToggleDiagnostics={setRuntimeDiagnosticsOpen}
        selectedLatestResult={selectedNode ? latestResults.get(selectedNode.node_id) ?? null : null}
        authoringPanel={authoringPanel}
        orderedInputs={orderedInputs}
        nodeLabel={nodeLabel}
        sourcePortLabel={(nodeId, portId) => {
          const node = graph.nodes.find((item) => item.node_id === nodeId)
          return node ? presentationsByKey.get(`${node.type_id}@${node.definition_version}`)?.ports.find((port) => port.direction === 'output' && port.port_id === portId)?.label ?? portId : portId
        }}
        selectedNode={selectedNode}
        selectedDefinition={selectedDefinition}
        selectedPresentation={selectedPresentation}
        selectedEdge={selectedEdge}
        parameterDraft={parameterDraft}
        parameterText={parameterText}
        parameterDirty={parameterDraftDirty}
        parameterRawError={parameterRawError}
        parameterValidation={parameterValidation}
        graphEditable={graphEditable}
        busy={busy || homeActionBusy}
        selectedNodeRun={selectedNodeRun}
        selectedProgress={selectedProgress}
        selectedLog={selectedLog}
        logStale={health.log.stale}
        readinessStale={health.readiness.stale}
        mutationBlocked={detailMutationBlocked}
        selectedOutputs={selectedOutputs}
        handoffInputs={handoffInputs}
        readiness={selectedNodeRun ? readiness.get(selectedNodeRun.node_run_id) ?? null : null}
        detail={currentDetail}
        lastFullPrecheckFailure={currentRun && selectedNodeRun?.external_handoff ? lastFullPrecheckFailures.get(handoffResourceKey(currentRun.run_id, selectedNodeRun.node_run_id, selectedNodeRun.external_handoff.handoff_id)) ?? null : null}
        handoffCenter={(
          <HandoffCenter
            advanced={advanced}
            nodeLabel={nodeLabel}
            selectedNodeId={selectedNodeIds.size === 1 ? selectedNode?.node_id ?? null : null}
            importController={handoffImport}
            canImportHandoff={hostCapabilityAvailable('open_file') && !!effectiveHostBridge.previewHandoffImport && !!effectiveHostBridge.confirmHandoffImport}
            checkedOutputs={checkedOutputs}
            checkingNodeRunId={checkingNodeRunId}
            submittingNodeRunId={submittingNodeRunId}
            onCheckOutput={(nodeRun) => void checkOutput(nodeRun)}
            onSubmitOutput={(nodeRun) => void submitOutput(nodeRun)}
            canRevealHandoff={hostCapabilityAvailable('reveal_in_file_manager')}
            canOpenHandoffInput={hostCapabilityAvailable('open_with_system_player')}
            onLaunchHandoff={(nodeRun, capability, selector) => void launchHandoff(nodeRun, capability, selector)}
            waitingNodeRuns={waitingNodeRuns}
            detail={currentDetail}
            artifactsById={artifactsById}
            readiness={readiness}
            lastFullPrecheckFailures={lastFullPrecheckFailures}
            mutationBlocked={detailMutationBlocked}
            readinessStale={health.readiness.stale}
            onSelectNode={locateRunNode}
            onCopyPath={(path) => void copyPath(path)}
          />
        )}
        actionableRun={viewedSummary?.actionable ?? false}
        actionableRunIsRunning={(viewedSummary?.state_counts.running ?? 0) > 0}
        clientHint={(!advanced && formatHostBridgeError(clientHint)) || (clientHint && !advanced && /(?:E_[A-Z_]+|probe|validator|Submit|Run detail|stdout|stderr)/.test(clientHint)
          ? '操作暂未完成。请查看问题提示；详细原因保留在高级诊断中。'
          : clientHint ?? (status?.studio_warnings.map((warning) => warning.message).join('；') || presentationError))}
        boundaryError={draft ? boundaryError : null}
        onParameterDraftChange={changeParameterDraft}
        onPickParameterPath={pickParameterPath}
        onParameterPickerError={(error) => {
          const message = error instanceof Error ? error.message : '桌面文件选择器暂时不可用。'
          setHostError(formatHostBridgeError(error) ?? message)
          setHostErrorDetails(message)
          setClientHint(message)
        }}
        onParameterTextChange={changeParameterText}
        onApplyParameters={applyParameters}
        onDiscardParameters={discardParameters}
        onCopyPath={(path) => void copyPath(path)}
        canRevealArtifact={hostCapabilityAvailable('reveal_in_file_manager')}
        canOpenArtifact={hostCapabilityAvailable('open_with_system_player')}
        onRevealArtifact={(artifactId) => void launchArtifact('reveal_in_file_manager', artifactId)}
        onOpenArtifact={(artifactId) => void launchArtifact('open_with_system_player', artifactId)}
        onReorderEdge={(id, ordinal) => updateGraph((current) => reorderEdge(current, id, ordinal))}
        onDeleteEdge={deleteSelected}
        onAbandonRun={() => {
          if (viewRunId && window.confirm('放弃此任务会停止等待中的步骤；已完成结果仍保留。是否继续？')) void executeCommands([{ operation: 'abandon_run', run_id: viewRunId }])
        }}
      />

      <DiagnosticsPanel
        advanced={advanced}
        nodeLabel={nodeLabel}
        runtimeProblems={[...runLatestAttempts.values()].flatMap((item) => item.error ? [{ code: item.error.reason, message: item.error.message, node_id: item.node_id }] : [])}
        onRecoverService={() => setReconnectEpoch((value) => value + 1)}
        open={bottomOpen}
        diagnostics={diagnostics}
        serviceError={commandFailure ?? status?.error ?? (boundaryError ? { code: 'E_STUDIO_SERVICE_UNAVAILABLE', message: boundaryError, related_run_ids: [] } : null)}
        hasOlderRuns={historyCursor !== null}
        historyBusy={historyBusy}
        onToggle={() => setBottomOpen((open) => !open)}
        onLocateNode={locateCurrentNode}
        onLocateRuntimeNode={locateRunNode}
        onLocateEdge={(id) => { if (changeSelection(new Set(), new Set([id]))) setShowRunSnapshot(false) }}
        onLoadOlderRuns={() => void loadOlderRuns()}
      />
    </main>
  )
}
