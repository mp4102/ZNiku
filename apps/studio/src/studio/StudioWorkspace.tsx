/**
 * 编排 ZNIKU 0.3.0 单一正式 Studio 工作区的 authority 状态与组件边界。
 *
 * Designer 编辑 Project 当前 Graph；运行视图默认展示操作者显式选择的 Run snapshot。status、Run
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
  AvEnhanceV27ExpandRequestWire,
  AvEnhanceV27PrepareRequestWire,
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
  StatusEnvelope,
  StudioCommand,
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
  nodeExecutionSignatureMatches,
  reorderEdge,
  selectionChangeBlocked,
} from './graph'
import { createStudioGateway, StudioGatewayError, type StudioGateway } from './gateway'
import {
  createHostBridge,
  type HostBridge,
  type HostCapabilitiesEnvelope,
  type HostDialogCapability,
} from './host-bridge'
import { readRecentProjects, rememberRecentProject } from './recent-projects'
import type { ParameterPickerRequest } from './SchemaParameterForm'
import { groupStudioDefinitions } from './catalog'
import { asParameterSchema, getPointer, validateParameterDraft } from './parameter-draft'
import { DiagnosticsPanel } from './components/DiagnosticsPanel'
import { GraphCanvas } from './components/GraphCanvas'
import { HandoffCenter, elapsedLabel, handoffResourceKey, readinessLabel } from './components/HandoffCenter'
import { NodeInspector } from './components/NodeInspector'
import { NodePalette } from './components/NodePalette'
import { ProjectHome } from './components/ProjectHome'
import { ProjectShell } from './components/ProjectShell'
import { RunCanvasOverlays, RunCenter, targetLabel } from './components/RunCenter'
const failureBackoff = [750, 1_500, 3_000, 5_000] as const

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
  const [logs, setLogs] = useState<ReadonlyMap<string, NodeLogEnvelope>>(new Map())
  const [statusHealth, setStatusHealth] = useState<ChannelHealth>(initialHealth.status)
  const [resourceHealth, setResourceHealth] = useState<ResourceHealth>(emptyResourceHealth)
  const [draft, setDraft] = useState<ProjectSnapshotWire | null>(null)
  const [showRunSnapshot, setShowRunSnapshot] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [historyBusy, setHistoryBusy] = useState(false)
  const [boundaryError, setBoundaryError] = useState<string | null>(null)
  const [clientHint, setClientHint] = useState<string | null>(null)
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
  const [bottomOpen, setBottomOpen] = useState(true)
  const [pollEpoch, setPollEpoch] = useState(0)
  const [detailPollEpoch, setDetailPollEpoch] = useState(0)
  const [templateOpen, setTemplateOpen] = useState(false)
  const [templateMode, setTemplateMode] = useState<AvEnhanceV27WizardMode>('create')
  const [homeOpen, setHomeOpen] = useState(true)
  const [homeActionBusy, setHomeActionBusy] = useState(false)
  const [recentProjects, setRecentProjects] = useState(() => readRecentProjects())
  const [hostCapabilities, setHostCapabilities] = useState<HostCapabilitiesEnvelope | null>(null)
  const [hostError, setHostError] = useState<string | null>(null)
  const [reconnectEpoch, setReconnectEpoch] = useState(0)
  const [templateProfile, setTemplateProfile] = useState<{
    readonly status: string
    readonly compatible: boolean
    readonly modified: boolean
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
      },
    ) => {
      const previous = statusRef.current
      const pathChanged = previous !== null && previous.project_path !== next.project_path
      const firstAuthority = previous === null
      statusRef.current = next
      setStatus(next)
      if (next.project_path !== null) setProjectPath(next.project_path)
      if (options.replaceProject || pathChanged) {
        setDraft(next.snapshot)
        setDirty(false)
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
    },
    [markStatusHealth, replaceHistory],
  )

  const setTrustedViewRunId = useCallback((runId: string | null, clearResources = true) => {
    viewRunIdRef.current = runId
    setViewRunId(runId)
    if (clearResources) {
      detailRef.current = null
      setDetail(null)
      setReadiness(new Map())
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
  }, [])

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
          acceptedResourceSequenceRef.current.readiness.set(resourceKey, sequence)
          setReadiness((current) => new Map(current).set(nodeRunId, next))
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
    [effectiveGateway, markResourceHealth],
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
        acceptStatus(next, { replaceProject: true })
        const selected = defaultRunId(next.run_summaries)
        setTrustedViewRunId(selected)
        setShowRunSnapshot(selected !== null)
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
      acceptStatus(nextStatus, {
        replaceProject: false,
        resetHistory: recoveredMissingRunId !== null,
      })
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
  const diagnostics = useMemo(() => (draft ? inspectGraph(draft) : []), [draft])
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

  const flowNodes = useMemo<WorkflowNode[]>(
    () =>
      graph.nodes.flatMap((node, index) => {
        const definition = definitionsByKey.get(`${node.type_id}@${node.definition_version}`)
        if (!definition) return []
        const presentation = presentationsByKey.get(`${node.type_id}@${node.definition_version}`) ?? null
        const nodeRun = activeNodeRuns.get(node.node_id) ?? null
        const data: WorkflowNodeData = {
          label: presentation?.title ?? node.node_id,
          instanceId: node.node_id,
          summaries: (presentation?.card_summary_paths ?? []).flatMap((pointer) => {
            const value = getPointer(node.parameters, pointer)
            if (value === undefined) return []
            const parameterPresentation = presentation?.parameters.find(
              (item) => item.parameter_pointer === pointer,
            )
            const label = parameterPresentation?.label ?? pointer
            const text = cardSummaryValue(value, parameterPresentation)
            return [`${label}：${text}`]
          }),
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
        label: edge.ordinal === null ? undefined : `#${edge.ordinal}`,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        data: { ordinal: edge.ordinal },
      })),
    [graph.edges, selectedEdgeIds],
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
  }, [currentRun, waitingNodeRuns])
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
    if (!viewRunId || !selectedNodeRun?.log_path || !selectedLogResourceKey) return
    void loadLog(viewRunId, selectedNodeRun.node_run_id, generationRef.current)
  }, [loadLog, selectedLogResourceKey, selectedNodeRun?.log_path, selectedNodeRun?.node_run_id, viewRunId])

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
    setDraft((current) => (current ? replaceGraph(current, updater(current.project.graph)) : current))
    setDirty(true)
    setTemplateProfile((current) =>
      current
        ? { status: 'modified · profile check required', compatible: false, modified: true }
        : current,
    )
    setClientHint(null)
  }, [])

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
        updateGraph((current) => ({
          ...current,
          nodes: current.nodes.map((node) =>
            positions.has(node.node_id)
              ? { ...node, ui_position: positions.get(node.node_id)! }
              : node,
          ),
        }))
      }
      if (removed.size > 0) updateGraph((current) => deleteSelection(current, removed, new Set()))
    },
    [graphEditable, parameterDraftDirty, updateGraph],
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
    updateGraph(() => copied.graph)
    setSelectedNodeIds(copied.copied_node_ids)
    setSelectedEdgeIds(new Set())
  }, [draft, graphEditable, nodeIdFactory, parameterDraftDirty, selectedNodeIds, updateGraph])

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
      if (isEditingTarget(event.target) || !graphEditable) return
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
  }, [copySelected, deleteSelected, graphEditable, selectedEdgeIds.size, selectedNodeIds.size])

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
      options: { readonly preferCreatedRun?: boolean } = {},
    ): Promise<StatusEnvelope | null> => {
      if (busyRef.current) return null
      busyRef.current = true
      setBusy(true)
      setBoundaryError(null)
      setClientHint(null)
      commandErrorRef.current = null
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
          next = await effectiveGateway.command(command)
          if (generation !== generationRef.current) return null
          acceptStatus(next, {
            replaceProject:
              command.operation === 'open_project' ||
              command.operation === 'create_project' ||
              command.operation === 'save_project' ||
              command.operation === 'create_av_enhance_v27' ||
              command.operation === 'expand_av_enhance_v27',
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
          setRecentProjects(rememberRecentProject({
            path: next.project_path,
            name: next.snapshot.project.name,
          }))
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
        } else if (
          options.preferCreatedRun &&
          next.active_run_id &&
          (last?.operation === 'run_all' ||
            last?.operation === 'run_to' ||
            last?.operation === 'rerun_from_here')
        ) {
          selected = next.active_run_id
        }
        setTrustedViewRunId(selected)
        setShowRunSnapshot(selected !== null)
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
          setClientHint(`${error.code}: 本次操作未完成，工程和媒体没有被修改。`)
        } else {
          const message = error instanceof Error ? error.message : '本机工程服务操作失败'
          commandErrorRef.current = message
          setBoundaryError('本机工程服务操作失败；工程和媒体没有被修改。')
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
    [acceptStatus, effectiveGateway, loadDetail, setTrustedViewRunId],
  )

  const saveProject = useCallback(async (): Promise<StatusEnvelope | null> => {
    if (!draft) return null
    return executeCommands([{ operation: 'save_project', project: draft.project }])
  }, [draft, executeCommands])

  const saveThenRun = useCallback(
    async (command: StudioCommand) => {
      if (!draft) return
      if (parameterDraftDirty) {
        setClientHint('当前节点有未应用设置；请先“应用设置”或“放弃未应用更改”再运行。')
        return
      }
      await executeCommands(
        [{ operation: 'save_project', project: draft.project }, command],
        { preferCreatedRun: true },
      )
    },
    [draft, executeCommands, parameterDraftDirty],
  )

  const previewAvEnhanceV27 = useCallback(
    async (
      request: AvEnhanceV27TemplatePreviewRequestWire,
    ): Promise<AvEnhanceV27TemplatePreviewEnvelope | null> => {
      latestTemplatePreviewRef.current = null
      setClientHint(null)
      setBoundaryError(null)
      const next = await effectiveGateway.previewAvEnhanceV27(request)
      latestTemplatePreviewRef.current = {
        requestJson: JSON.stringify(request),
        envelope: next,
      }
      return next
    },
    [effectiveGateway],
  )

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
      const next = await executeCommands([command])
      if (!next) {
        latestTemplatePreviewRef.current = null
        return false
      }
      setTemplateProfile({
        status: authority.envelope.profile.status,
        compatible: authority.envelope.profile.compatible,
        modified: false,
      })
      latestTemplatePreviewRef.current = null
      setShowRunSnapshot(false)
      setSelectedNodeIds(new Set())
      setSelectedEdgeIds(new Set())
      setFitViewEpoch((value) => value + 1)
      return true
    },
    [executeCommands, parameterDraftDirty],
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

  const startPreparationRun = useCallback(async (): Promise<string | null> => {
    const next = await executeCommands([{ operation: 'run_all' }], { preferCreatedRun: true })
    // 只绑定本次 command response 明确返回的 active_run_id；不得从历史、时间或节点形状猜测。
    return next?.active_run_id ?? null
  }, [executeCommands])

  useEffect(() => {
    if (fitViewEpoch === 0) return
    const timer = window.setTimeout(() => {
      void fitView({ duration: 350, padding: 0.16 })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [fitView, fitViewEpoch])

  const validateAndSubmit = useCallback(
    async (nodeRun: NodeRunWire) => {
      if (!viewRunId || !nodeRun.external_handoff || health.readiness.stale) return
      const runId = viewRunId
      const nodeRunId = nodeRun.node_run_id
      const handoffId = nodeRun.external_handoff.handoff_id
      const checked = await loadReadiness(
        runId,
        nodeRunId,
        true,
        generationRef.current,
      )
      if (!checked?.ready_for_submit) {
        const reasons = checked?.targets.flatMap((target) => target.message ? [`${target.port_id}: ${target.message}`] : []).join('；')
        setClientHint(`外部输出尚未通过完整 probe/validator，未发送 Submit。${reasons ? ` ${reasons}` : ''}`)
        return
      }
      if (
        checked.run_id !== runId ||
        checked.node_run_id !== nodeRunId ||
        checked.handoff_id !== handoffId
      ) {
        setClientHint('E_STUDIO_HANDOFF_IDENTITY_MISMATCH：probe 身份不匹配，未发送 Submit。')
        return
      }
      await executeCommands([
        {
          operation: 'submit_external',
          run_id: runId,
          node_run_id: nodeRunId,
          handoff_id: handoffId,
        },
      ])
    },
    [executeCommands, health.readiness.stale, loadReadiness, viewRunId],
  )

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
  const serviceBusy = busy || operationActive
  const runBlocked = serviceBusy || homeActionBusy || health.status.stale || !draft || diagnostics.length > 0 || parameterDraftDirty
  const detailMutationBlocked = serviceBusy || homeActionBusy || health.status.stale || health.detail.stale
  const firstWaiting = waitingNodeRuns[0] ?? null
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
    templateProfile ??
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
    } catch {
      if (!homeActionIsCurrent(epoch)) return
      setHostError('无法打开工程选择器；工程和媒体都没有被修改。')
      setHomeOpen(true)
    } finally {
      finishHomeAction(epoch)
    }
  }

  const createBlankProject = async (name: string): Promise<void> => {
    const epoch = beginHomeAction()
    if (epoch === null) return
    setHostError(null)
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
    } catch {
      if (homeActionIsCurrent(epoch)) {
        setHostError('无法创建空白工程；已有文件和媒体都没有被修改。')
      }
    } finally {
      finishHomeAction(epoch)
    }
  }

  const openRecentProject = async (path: string): Promise<void> => {
    const epoch = beginHomeAction()
    if (epoch === null) return
    setHostError(null)
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
      setClientHint(message)
    }
  }

  return (
    <main className={`app-shell studio-workspace ${bottomOpen ? 'has-bottom-drawer' : ''}`}>
      <ProjectHome
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
        onPickOutputDirectory={async () => (await pickHostPaths('select_directory', { title: '选择成片文件夹' }))?.[0] ?? null}
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
        onStartPreparationRun={startPreparationRun}
        open={templateOpen}
        pickerAvailable={hostCapabilityAvailable('open_file') && hostCapabilityAvailable('save_file') && hostCapabilityAvailable('select_directory')}
        projectIdFactory={projectIdFactory}
        runSummaries={allSummaries}
        serviceError={boundaryError ? '本机工程服务暂时不可用；当前工程和媒体没有被修改。' : null}
      />
      <ProjectShell
        projectName={draft?.project.name ?? null}
        projectId={draft?.project.project_id ?? null}
        nodeCount={draft?.project.graph.nodes.length ?? 0}
        dirty={dirty}
        profile={visibleTemplateProfile}
        projectPath={projectPath}
        projectIdDraft={projectId}
        projectNameDraft={projectName}
        serviceBusy={serviceBusy || homeActionBusy}
        statusStale={health.status.stale}
        canSave={!serviceBusy && !homeActionBusy && !health.status.stale && !!draft && diagnostics.length === 0 && dirty && !parameterDraftDirty}
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
            onRerun={() => singleSelectedNodeId && rerunId && void executeCommands([{ operation: 'rerun_from_here', run_id: rerunId, node_id: singleSelectedNodeId }], { preferCreatedRun: true })}
          />
        )}
      />

      <NodePalette
        definitionCount={draft?.definitions.length ?? 0}
        projectId={projectId}
        projectName={projectName}
        query={query}
        groups={catalogGroups}
        canEditGraph={!!draft && graphEditable}
        busy={serviceBusy || homeActionBusy}
        selectedNodeCount={selectedNodeIds.size}
        selectedEdgeCount={selectedEdgeIds.size}
        onProjectNameChange={setProjectName}
        onQueryChange={setQuery}
        onAddDefinition={addDefinition}
        onCopySelection={copySelected}
        onDeleteSelection={deleteSelected}
      />

      <GraphCanvas
        nodes={flowNodes}
        edges={flowEdges}
        editable={graphEditable}
        busy={serviceBusy || homeActionBusy}
        modeLabel={showRunSnapshot && currentRun ? 'Run snapshot' : 'Current Graph'}
        contextLabel={viewedSummary ? `${viewedSummary.run_id} · ${targetLabel(viewedSummary)} · ${viewedSummary.state}` : '编辑与运行使用同一 Project authority'}
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
            viewedSummary={viewedSummary}
            firstWaiting={firstWaiting}
            firstWaitingInputPaths={firstWaitingInputPaths}
            firstWaitingReadinessLabel={firstWaiting ? readinessLabel(readiness.get(firstWaiting.node_run_id) ?? null) : ''}
            firstWaitingElapsedLabel={firstWaiting ? elapsedLabel(firstWaiting.started_at ?? firstWaiting.created_at) : ''}
            globalActionSummary={globalActionSummary}
            firstFailed={firstFailed}
            sameRun={globalActionSummary?.run_id === viewRunId}
            onLocateNode={(nodeId) => changeSelection(new Set([nodeId]), new Set())}
            onSelectRun={selectRun}
          />
        )}
      />

      <NodeInspector
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
        busy={serviceBusy || homeActionBusy}
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
            waitingNodeRuns={waitingNodeRuns}
            detail={currentDetail}
            artifactsById={artifactsById}
            readiness={readiness}
            lastFullPrecheckFailures={lastFullPrecheckFailures}
            mutationBlocked={detailMutationBlocked}
            readinessStale={health.readiness.stale}
            onSelectNode={(nodeId) => changeSelection(new Set([nodeId]), new Set())}
            onCopyPath={(path) => void copyPath(path)}
            onValidateAndSubmit={(nodeRun) => void validateAndSubmit(nodeRun)}
          />
        )}
        actionableRun={viewedSummary?.actionable ?? false}
        actionableRunIsRunning={(viewedSummary?.state_counts.running ?? 0) > 0}
        clientHint={clientHint ?? presentationError}
        boundaryError={draft ? boundaryError : null}
        onParameterDraftChange={changeParameterDraft}
        onPickParameterPath={pickParameterPath}
        onParameterPickerError={(error) => {
          const message = error instanceof Error ? error.message : '桌面文件选择器暂时不可用。'
          setHostError(message)
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
        onValidateAndSubmit={(nodeRun) => void validateAndSubmit(nodeRun)}
        onReorderEdge={(id, ordinal) => updateGraph((current) => reorderEdge(current, id, ordinal))}
        onDeleteEdge={deleteSelected}
        onAbandonRun={() => viewRunId && void executeCommands([{ operation: 'abandon_run', run_id: viewRunId }])}
      />

      <DiagnosticsPanel
        open={bottomOpen}
        diagnostics={diagnostics}
        serviceError={status?.error ?? null}
        hasOlderRuns={historyCursor !== null}
        historyBusy={historyBusy}
        onToggle={() => setBottomOpen((open) => !open)}
        onLocateNode={(nodeId) => changeSelection(new Set([nodeId]), new Set())}
        onLocateEdge={(id) => changeSelection(new Set(), new Set([id]))}
        onLoadOlderRuns={() => void loadOlderRuns()}
      />
    </main>
  )
}
