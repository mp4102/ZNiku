/**
 * 实现 ZNIKU 0.2.1 的单一正式 Studio 工作区与 Run 可观察性。
 *
 * Designer 编辑 Project 当前 Graph；运行视图默认展示操作者显式选择的 Run snapshot。status、Run
 * detail、readiness 与日志分别从 Python authority 读取，并通过 generation/sequence 丢弃迟到响应。
 * 页面只投影状态，不生成第二套 Run、Artifact、进度或人工交接 authority。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type EdgeMouseHandler,
  type NodeChange,
  type NodeMouseHandler,
  type OnSelectionChangeParams,
} from '@xyflow/react'
import { WorkflowNodeCard } from '../components/WorkflowNodeCard'
import { AvEnhanceV27Wizard } from './AvEnhanceV27Wizard'
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
} from './graph'
import { createStudioGateway, StudioGatewayError, type StudioGateway } from './gateway'
import { groupStudioDefinitions } from './catalog'

const nodeTypes = { workflow: WorkflowNodeCard }
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
  readonly nodeIdFactory?: () => string
}

function defaultNodeId(): string {
  return `node.${globalThis.crypto.randomUUID()}`
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

function targetLabel(summary: RunSummaryWire): string {
  return summary.target_mode === 'all'
    ? 'Run all'
    : `Run to ${summary.selected_targets.join(', ')}`
}

function summaryOptionLabel(summary: RunSummaryWire): string {
  const counts = summary.state_counts
  return `${summary.created_at} · ${targetLabel(summary)} · ${summary.state} · ${counts.completed}/${summary.node_count} completed · ${counts.running} running · ${counts.waiting_external} waiting external · ${counts.failed} failed`
}

function readinessLabel(value: ExternalHandoffReadiness | null): string {
  if (!value) return 'checking'
  if (value.ready_for_submit) return 'probe passed'
  const states = [...new Set(value.targets.map((target) => target.state))]
  return states.join(', ') || 'no targets'
}

function elapsedLabel(createdAt: string): string {
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

function parameterTextValue(parameters: JsonObject, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = parameters[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return null
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

export function StudioWorkspace({ gateway, nodeIdFactory = defaultNodeId }: StudioWorkspaceProps) {
  const effectiveGateway = useMemo(() => gateway ?? createStudioGateway(), [gateway])
  const { fitView } = useReactFlow()
  const [status, setStatus] = useState<StatusEnvelope | null>(null)
  const [historySummaries, setHistorySummaries] = useState<ReadonlyArray<RunSummaryWire>>([])
  const [historyCursor, setHistoryCursor] = useState<string | null>(null)
  const [viewRunId, setViewRunId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RunDetailEnvelope | null>(null)
  const [readiness, setReadiness] = useState<ReadonlyMap<string, ExternalHandoffReadiness>>(
    new Map(),
  )
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
  const [projectId, setProjectId] = useState('project.local')
  const [projectName, setProjectName] = useState('ZNIKU Project')
  const [query, setQuery] = useState('')
  const [selectedNodeIds, setSelectedNodeIds] = useState<ReadonlySet<string>>(new Set())
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<ReadonlySet<string>>(new Set())
  const [parameterText, setParameterText] = useState('{}')
  const [bottomOpen, setBottomOpen] = useState(true)
  const [pollEpoch, setPollEpoch] = useState(0)
  const [detailPollEpoch, setDetailPollEpoch] = useState(0)
  const [templateOpen, setTemplateOpen] = useState(false)
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
  const detailSummaryRevisionRef = useRef('')
  const detailRequestedSummaryRevisionRef = useRef('')
  const latestTemplatePreviewRef = useRef<{
    readonly requestJson: string
    readonly envelope: AvEnhanceV27TemplatePreviewEnvelope
  } | null>(null)

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
            const message = error instanceof Error ? error.message : 'Run detail 读取失败'
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
          const message = error instanceof Error ? error.message : 'Project Service inspect 失败'
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
  }, [acceptStatus, effectiveGateway, loadDetail, loadReadiness, markStatusHealth, setTrustedViewRunId])

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
          const message = error instanceof Error ? error.message : 'Project Service status 读取失败'
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
  const currentRun = detail?.run.run_id === viewRunId ? detail.run : null
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

  const flowNodes = useMemo<WorkflowNode[]>(
    () =>
      graph.nodes.flatMap((node, index) => {
        const definition = definitionsByKey.get(`${node.type_id}@${node.definition_version}`)
        if (!definition) return []
        const nodeRun = activeNodeRuns.get(node.node_id) ?? null
        const data: WorkflowNodeData = {
          label: node.node_id,
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

  useEffect(() => {
    setParameterText(JSON.stringify(selectedNode?.parameters ?? {}, null, 2))
  }, [selectedNode])

  useEffect(() => {
    selectedLogResourceRef.current = selectedLogResourceKey
    if (!viewRunId || !selectedNodeRun?.log_path || !selectedLogResourceKey) return
    void loadLog(viewRunId, selectedNodeRun.node_run_id, generationRef.current)
  }, [loadLog, selectedLogResourceKey, selectedNodeRun?.log_path, selectedNodeRun?.node_run_id, viewRunId])

  const selectRun = useCallback(
    (runId: string) => {
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
    [loadDetail, setTrustedViewRunId],
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
    [graphEditable, updateGraph],
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
    [draft, graphEditable, nodeIdFactory, updateGraph],
  )

  const copySelected = useCallback(() => {
    if (!draft || !graphEditable || selectedNodeIds.size === 0) return
    const copied = copySelection(draft.project.graph, selectedNodeIds, nodeIdFactory)
    updateGraph(() => copied.graph)
    setSelectedNodeIds(copied.copied_node_ids)
    setSelectedEdgeIds(new Set())
  }, [draft, graphEditable, nodeIdFactory, selectedNodeIds, updateGraph])

  const deleteSelected = useCallback(() => {
    if (!graphEditable || (selectedNodeIds.size === 0 && selectedEdgeIds.size === 0)) return
    updateGraph((current) => deleteSelection(current, selectedNodeIds, selectedEdgeIds))
    setSelectedNodeIds(new Set())
    setSelectedEdgeIds(new Set())
  }, [graphEditable, selectedEdgeIds, selectedNodeIds, updateGraph])

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
  }, [graphEditable, parameterText, selectedNode, updateGraph])

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
          setClientHint(`${error.code}：已有非终态 Run，请先继续或放弃它。`)
          if (existing) {
            setTrustedViewRunId(existing)
            setShowRunSnapshot(true)
          }
        } else if (error instanceof StudioGatewayError && error.code) {
          setClientHint(`${error.code}: ${error.message}`)
        } else {
          setBoundaryError(error instanceof Error ? error.message : 'Project Service command 失败')
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
      await executeCommands(
        [{ operation: 'save_project', project: draft.project }, command],
        { preferCreatedRun: true },
      )
    },
    [draft, executeCommands],
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
    [executeCommands],
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
        setClientHint('外部输出尚未通过完整 probe/validator，未发送 Submit。')
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

  const catalogGroups = groupStudioDefinitions(draft?.definitions ?? [], query)
  const singleSelectedNodeId = selectedNodeIds.size === 1 ? selectedNode?.node_id ?? null : null
  const rerunId = currentRun?.run_id ?? null
  const rerunNodeIncluded = singleSelectedNodeId
    ? runLatestAttempts.has(singleSelectedNodeId)
    : false
  const serviceBusy = busy || operationActive
  const runBlocked = serviceBusy || health.status.stale || !draft || diagnostics.length > 0
  const detailMutationBlocked = serviceBusy || health.status.stale || health.detail.stale
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

  const locateTemplateNode = (nodeId: string) => {
    if (!draft?.project.graph.nodes.some((node) => node.node_id === nodeId)) {
      setClientHint(`Template preview diagnostic 指向 ${nodeId}；该节点尚未写入当前 Project。`)
      return
    }
    setTemplateOpen(false)
    setShowRunSnapshot(false)
    setSelectedNodeIds(new Set([nodeId]))
    setSelectedEdgeIds(new Set())
  }

  return (
    <main className={`app-shell studio-workspace ${bottomOpen ? 'has-bottom-drawer' : ''}`}>
      <AvEnhanceV27Wizard
        busy={serviceBusy || health.status.stale}
        currentProjectId={projectId}
        currentProjectName={projectName}
        currentProjectPath={projectPath}
        currentSnapshot={draft}
        onClose={() => setTemplateOpen(false)}
        onCreate={createAvEnhanceV27}
        onExpand={expandAvEnhanceV27}
        onLocateNode={locateTemplateNode}
        onPreview={previewAvEnhanceV27}
        open={templateOpen}
        runSummaries={allSummaries}
        serviceError={clientHint ?? boundaryError}
      />
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">ZN</div>
          <div><span className="brand-name">ZNIKU</span><span className="brand-subtitle">Studio</span></div>
        </div>

        <div className="workflow-identity">
          <span className="eyebrow">PROJECT GRAPH</span>
          <strong>{draft?.project.name ?? '打开或新建 .zniku 工程'}</strong>
          <span className="identity-meta">
            {draft
              ? `${draft.project.project_id} · ${draft.project.graph.nodes.length} nodes · ${dirty ? '未保存' : '已保存'}`
              : '0.2.1 Project Service wire authority'}
          </span>
          {visibleTemplateProfile && (
            <span
              className={`workflow-profile-state ${visibleTemplateProfile.compatible ? 'is-compatible' : 'is-unverified'}`}
              role="status"
            >
              AVEnhanceFlow 2.7 · {visibleTemplateProfile.status}
              {visibleTemplateProfile.modified ? ' · 自由编辑后已降级' : ''}
            </span>
          )}
        </div>

        <div className="project-location">
          <button
            className="button button--template"
            disabled={serviceBusy || health.status.stale}
            onClick={() => setTemplateOpen(true)}
            type="button"
          >
            Templates
          </button>
          <input aria-label="工程路径" value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="D:\\Projects\\example.zniku" />
          <button className="button button--ghost" type="button" disabled={serviceBusy || health.status.stale || !projectPath.trim()} onClick={() => void executeCommands([{ operation: 'open_project', path: projectPath.trim() }])}>打开</button>
          <button className="button button--ghost" type="button" disabled={serviceBusy || health.status.stale || !projectPath.trim() || !projectId.trim() || !projectName.trim()} onClick={() => void executeCommands([{ operation: 'create_project', path: projectPath.trim(), project_id: projectId.trim(), name: projectName.trim() }])}>新建</button>
          <button className="button button--ghost" type="button" disabled={serviceBusy || health.status.stale || !draft || diagnostics.length > 0 || !dirty} onClick={() => void saveProject()}>保存</button>
        </div>

        <div className="top-actions">
          <span className={`authority-badge ${boundaryError || health.status.stale ? 'is-unavailable' : ''}`}>
            {health.status.stale ? 'STATUS STALE' : status?.active_operation ? `HOST · ${status.active_operation.toUpperCase()}` : 'PROJECT SERVICE'}
          </span>
          <div className="channel-health" aria-label="Resource channel health">
            {(Object.entries(health) as Array<[ChannelName, ChannelHealth]>).map(
              ([channel, value]) => (
                <span className={value.stale ? 'is-stale' : ''} key={channel}>
                  {channel.toUpperCase()} {value.stale ? 'STALE' : 'OK'} · {value.lastSuccess ?? 'never'}
                </span>
              ),
            )}
          </div>
          <label className="run-selector">
            <span>查看 Run</span>
            <select aria-label="查看 Run" value={viewRunId ?? ''} onChange={(event) => event.target.value && selectRun(event.target.value)} disabled={allSummaries.length === 0}>
              {allSummaries.length === 0 && <option value="">No Runs</option>}
              {allSummaries.map((summary) => <option key={summary.run_id} value={summary.run_id}>{summaryOptionLabel(summary)}</option>)}
            </select>
          </label>
          <button className="button button--primary" type="button" disabled={runBlocked} onClick={() => void saveThenRun({ operation: 'run_all' })}>Run all</button>
          <button className="button button--ghost" type="button" disabled={runBlocked || !singleSelectedNodeId || showRunSnapshot} onClick={() => singleSelectedNodeId && void saveThenRun({ operation: 'run_to', node_id: singleSelectedNodeId })}>Run to here</button>
          <button className="button button--ghost" type="button" disabled={detailMutationBlocked || !singleSelectedNodeId || !rerunId || !rerunNodeIncluded} onClick={() => singleSelectedNodeId && rerunId && void executeCommands([{ operation: 'rerun_from_here', run_id: rerunId, node_id: singleSelectedNodeId }], { preferCreatedRun: true })}>Rerun from here</button>
        </div>
      </header>

      <aside className="palette-panel">
        <div className="panel-heading"><span className="eyebrow">NODE DEFINITIONS</span><h2>节点面板</h2><span className="registry-state"><i /> {draft?.definitions.length ?? 0} exact versions</span></div>
        <div className="project-fields">
          <label>Project ID<input aria-label="Project ID" value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label>
          <label>Project name<input aria-label="Project name" value={projectName} onChange={(event) => setProjectName(event.target.value)} /></label>
        </div>
        <label className="search-box"><span>⌕</span><input aria-label="搜索节点" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="type、port、mode 或 executor" /></label>
        <div className="palette-list" aria-label="节点定义列表">
          {catalogGroups.map((group) => (
            <section className="palette-group" aria-label={group.label} key={group.id}>
              <header><span><strong>{group.label}</strong><small>{group.description}</small></span><em>{group.entries.length}</em></header>
              {group.entries.map(({ definition, role }) => (
                <button className={`palette-item palette-item--${definition.executor.kind}`} type="button" key={`${definition.type_id}@${definition.version}`} disabled={!draft || busy || !graphEditable} onClick={() => addDefinition(definition)}>
                  <span className="palette-icon">{definition.executor.kind === 'manual_external' ? 'ME' : definition.executor.kind === 'command' ? 'CM' : 'PY'}</span>
                  <span><strong>{definition.type_id}</strong><small>{role} · {definition.execution_mode} · {definition.input_ports.length} in / {definition.output_ports.length} out</small></span><em>{definition.version}</em>
                </button>
              ))}
            </section>
          ))}
          {catalogGroups.length === 0 && <p className="palette-empty">没有匹配的 exact definition。</p>}
        </div>
        <div className="selection-actions">
          <button className="button button--ghost" type="button" disabled={selectedNodeIds.size === 0 || busy || !graphEditable} onClick={copySelected}>复制所选</button>
          <button className="button button--danger" type="button" disabled={(selectedNodeIds.size === 0 && selectedEdgeIds.size === 0) || busy || !graphEditable} onClick={deleteSelected}>删除所选</button>
        </div>
        <div className="palette-note"><span>自由 DAG</span><p>节点与 presets 来自当前 .zniku 的 Python catalog。Run snapshot 只读；切回当前 Graph 后才能编辑。</p></div>
      </aside>

      <section className="canvas-panel" aria-label="Studio Designer 画布">
        <div className="canvas-context">
          <div><span className="context-mode">{showRunSnapshot && currentRun ? 'Run snapshot' : 'Current Graph'}</span><strong>{viewedSummary ? `${viewedSummary.run_id} · ${targetLabel(viewedSummary)} · ${viewedSummary.state}` : '编辑与运行使用同一 Project authority'}</strong></div>
          <div className="canvas-context-actions">
            {currentRun && <button type="button" onClick={() => { setShowRunSnapshot((value) => !value); setSelectedNodeIds(new Set()); setSelectedEdgeIds(new Set()) }}>{showRunSnapshot ? '查看当前 Graph' : '查看 Run snapshot'}</button>}
            {snapshotChanged && <span>Run snapshot / 当前 Graph 已变化</span>}
          </div>
        </div>
        {viewedSummary && <div className="run-summary-strip" aria-label="Run summary"><strong>{targetLabel(viewedSummary)}</strong><span>{viewedSummary.state_counts.completed}/{viewedSummary.node_count} completed</span><span>{viewedSummary.state_counts.running} running</span><span>{viewedSummary.state_counts.waiting_external} waiting external</span><span>{viewedSummary.state_counts.failed} failed</span></div>}
        {firstWaiting && <div className="next-action-banner" role="status"><span className="eyebrow">NEXT ACTION</span><div className="next-action-copy"><strong>{firstWaiting.node_id} 等待人工外部输出</strong>{firstWaiting.external_handoff?.instructions && <small>{firstWaiting.external_handoff.instructions}</small>}<code>{firstWaitingInputPaths.join(', ')} → {firstWaiting.external_handoff?.output_targets.map((target) => target.path).join(', ')}</code></div><span>{readinessLabel(readiness.get(firstWaiting.node_run_id) ?? null)} · {elapsedLabel(firstWaiting.created_at)}</span><button type="button" onClick={() => { setSelectedNodeIds(new Set([firstWaiting.node_id])); setSelectedEdgeIds(new Set()) }}>定位等待节点</button></div>}
        {!firstWaiting && globalActionSummary && <div className="next-action-banner" role="status"><span className="eyebrow">NEXT ACTION</span><strong>{globalActionSummary.run_id} 需要操作者处理</strong><span>{globalActionSummary.state_counts.waiting_external} waiting external · {globalActionSummary.state_counts.failed} failed</span><button type="button" onClick={() => { if (globalActionSummary.run_id === viewRunId && firstFailed) { setSelectedNodeIds(new Set([firstFailed.node_id])); setSelectedEdgeIds(new Set()) } else { selectRun(globalActionSummary.run_id) } }}>{globalActionSummary.run_id === viewRunId && firstFailed ? '定位失败节点' : '查看需处理 Run'}</button></div>}
        {loading ? (
          <div className="authority-empty" role="status"><strong>正在连接 Project Service…</strong></div>
        ) : boundaryError && !draft ? (
          <div className="authority-empty" role="alert"><strong>Project Service 不可用</strong><p>{boundaryError}</p><p>Studio 不会回退浏览器内存数据。</p></div>
        ) : !draft ? (
          <div className="authority-empty"><strong>尚未打开工程</strong><p>输入本地 .zniku 路径后选择“打开”或“新建”。</p></div>
        ) : (
          <ReactFlow nodes={flowNodes} edges={flowEdges} nodeTypes={nodeTypes} onNodesChange={handleNodesChange} onEdgesChange={handleEdgesChange} onSelectionChange={handleSelection} onNodeClick={handleNodeClick} onEdgeClick={handleEdgeClick} onConnect={handleConnect} isValidConnection={connectionIsValid} nodesDraggable={!busy && graphEditable} nodesConnectable={!busy && graphEditable} edgesReconnectable={false} deleteKeyCode={null} selectionOnDrag multiSelectionKeyCode={['Control', 'Meta']} fitView minZoom={0.2} maxZoom={1.8} colorMode="dark" proOptions={{ hideAttribution: true }}>
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.1} color="#263344" /><Controls position="bottom-left" showInteractive={false} /><MiniMap position="bottom-right" pannable zoomable nodeColor="#d89b45" />
          </ReactFlow>
        )}
      </section>

      <aside className="inspector-panel">
        <div className="panel-heading inspector-heading"><span className="eyebrow">INSPECTOR</span><h2>{selectedNode?.node_id ?? (selectedEdge ? 'Data edge' : '未选择实体')}</h2>{(selectedNode || selectedEdge) && <code>{selectedNode ? `${selectedNode.type_id}@${selectedNode.definition_version}` : edgeId(selectedEdge!)}</code>}</div>
        {selectedNode && selectedDefinition ? (
          <div className="inspector-content">
            <section><h3>Node binding</h3><dl className="property-list"><div><dt>type_id</dt><dd>{selectedNode.type_id}</dd></div><div><dt>version</dt><dd>{selectedNode.definition_version}</dd></div><div><dt>executor</dt><dd>{selectedDefinition.executor.kind}</dd></div></dl></section>
            <section><h3>Typed ports</h3>{(['input_ports', 'output_ports'] as const).map((direction) => <div className="port-group" key={direction}><span className="port-group-label">{direction}</span>{selectedDefinition[direction].length ? selectedDefinition[direction].map((port) => <div className="port-summary" key={port.port_id}><span>{port.port_id}</span><code>{port.data_type} · {port.cardinality}{port.required ? ' · required' : ''}</code></div>) : <div className="port-empty">none</div>}</div>)}</section>
            <section><h3>Parameters</h3><textarea aria-label="节点参数 JSON" value={parameterText} onChange={(event) => setParameterText(event.target.value)} rows={8} readOnly={!graphEditable} /><button className="button button--primary inspector-action" type="button" disabled={busy || !graphEditable} onClick={applyParameters}>应用参数到 Draft</button><details><summary>parameter_schema</summary><pre>{JSON.stringify(selectedDefinition.parameter_schema, null, 2)}</pre></details></section>
            {selectedNodeRun && <section aria-label="Runtime details"><h3>Runtime · attempt {selectedNodeRun.attempt}</h3><div className={`runtime-status runtime-status--${selectedNodeRun.state}`}>{selectedNodeRun.state}{selectedNodeRun.progress !== null ? ` · ${Math.round(selectedNodeRun.progress * 100)}%` : ''}{selectedNodeRun.reused_from_result_id ? ' · reused' : ''}</div>{selectedNodeRun.error && <p className="runtime-error">{selectedNodeRun.error.reason}<br />{selectedNodeRun.error.message}</p>}{selectedOutputs.map((artifact) => <div className="output-path" key={artifact.artifact_id}><span>{artifact.producer_port_id}</span><code>{artifact.path}</code></div>)}{selectedNodeRun.external_handoff && <div className="handoff-panel"><strong>External handoff</strong>{selectedNodeRun.external_handoff.instructions && <p>{selectedNodeRun.external_handoff.instructions}</p>}<span>Inputs</span>{handoffInputs.map((path, index) => <div className="handoff-path" key={`${selectedNodeRun.external_handoff!.input_artifact_ids[index]}-${index}`}><code>{path}</code><button type="button" onClick={() => void copyPath(path)}>Copy input path</button></div>)}<span>Targets</span>{selectedNodeRun.external_handoff.output_targets.map((target) => <div className="handoff-path" key={`${target.port_id}-${target.ordinal ?? 'one'}`}><code>{target.port_id}{target.ordinal === null ? '' : ` #${target.ordinal}`} · {target.path}</code><button type="button" onClick={() => void copyPath(target.path)}>Copy target path</button></div>)}<div className="readiness-state">Readiness · {readinessLabel(readiness.get(selectedNodeRun.node_run_id) ?? null)}</div><button className="button button--primary inspector-action" type="button" disabled={detailMutationBlocked || health.readiness.stale || selectedNodeRun.state !== 'waiting_external' || !readiness.get(selectedNodeRun.node_run_id) || readiness.get(selectedNodeRun.node_run_id)!.targets.some((target) => target.state !== 'present' && target.state !== 'probe_passed')} onClick={() => void validateAndSubmit(selectedNodeRun)}>Validate and submit</button></div>}{(selectedLog || selectedNodeRun.log_path) && <div className="node-logs">{selectedNodeRun.log_path && <code>{selectedNodeRun.log_path}</code>}<h4>stdout{selectedLog?.stdout_truncated ? '（尾部截断）' : ''}</h4><pre>{health.log.stale ? '（日志通道离线，保留最后可信内容）' : selectedLog?.stdout_available ? selectedLog.stdout || '（空）' : '（不可用）'}</pre><h4>stderr{selectedLog?.stderr_truncated ? '（尾部截断）' : ''}</h4><pre>{health.log.stale ? '（日志通道离线，保留最后可信内容）' : selectedLog?.stderr_available ? selectedLog.stderr || '（空）' : '（不可用）'}</pre></div>}</section>}
            {selectedNodeRun && selectedProgress && (
              selectedProgress.mode === 'indeterminate' ||
              selectedProgress.measurement !== null ||
              selectedProgress.elapsed !== null
            ) && (
              <div className="runtime-progress-detail" aria-label="Runtime progress details">
                {selectedProgress.mode === 'indeterminate' && <span>indeterminate</span>}
                {selectedProgress.measurement && (
                  <span>
                    {selectedProgress.measurement.current} / {selectedProgress.measurement.total}{' '}
                    {selectedProgress.measurement.unit}
                  </span>
                )}
                {selectedProgress.elapsed && <span>{selectedProgress.elapsed}</span>}
              </div>
            )}
          </div>
        ) : selectedEdge ? (
          <div className="inspector-content"><section><h3>Edge</h3><p>{selectedEdge.source_node_id}.{selectedEdge.source_port_id} → {selectedEdge.target_node_id}.{selectedEdge.target_port_id}</p>{selectedEdge.ordinal !== null && <label className="ordinal-editor">Ordinal<input aria-label="Edge ordinal" type="number" min={0} value={selectedEdge.ordinal} disabled={!graphEditable} onChange={(event) => updateGraph((current) => reorderEdge(current, edgeId(selectedEdge), Number(event.target.value)))} /></label>}<button className="button button--danger inspector-action" type="button" disabled={!graphEditable} onClick={deleteSelected}>删除所选连接</button></section></div>
        ) : <div className="empty-inspector">选择节点或连接查看配置、运行状态、日志和输出。</div>}
        {waitingNodeRuns.length > 0 && (
          <section className="handoff-queue" aria-label="External Handoff 队列">
            <h3>External Handoff Queue</h3>
            {waitingNodeRuns.map((nodeRun) => {
              const node = currentRun?.graph_snapshot.nodes.find(
                (item) => item.node_id === nodeRun.node_id,
              )
              const modelName = node
                ? parameterTextValue(node.parameters, 'actual_model_name', 'model_name')
                : null
              const modelVersion = node
                ? parameterTextValue(node.parameters, 'actual_model_version', 'model_version')
                : null
              const handoff = nodeRun.external_handoff!
              const observedReadiness = readiness.get(nodeRun.node_run_id) ?? null
              const inputPaths = handoff.input_artifact_ids.map(
                (artifactId) => artifactsById.get(artifactId)?.path ?? `未解析 Artifact：${artifactId}`,
              )
              const canValidate =
                !detailMutationBlocked &&
                !health.readiness.stale &&
                observedReadiness !== null &&
                observedReadiness.targets.every(
                  (target) => target.state === 'present' || target.state === 'probe_passed',
                )
              return (
                <article aria-label={`Handoff ${nodeRun.node_id}`} key={nodeRun.node_run_id}>
                  <button
                    className="handoff-queue-select"
                    type="button"
                    onClick={() => {
                      setSelectedNodeIds(new Set([nodeRun.node_id]))
                      setSelectedEdgeIds(new Set())
                    }}
                  >
                    <strong>{nodeRun.node_id}</strong>
                    <span>{modelName ?? 'model not declared'}{modelVersion ? ` · ${modelVersion}` : ''}</span>
                    <em>{readinessLabel(observedReadiness)} · {elapsedLabel(nodeRun.created_at)}</em>
                  </button>
                  {handoff.instructions && <p>{handoff.instructions}</p>}
                  <span className="handoff-queue-label">Inputs</span>
                  {inputPaths.map((path, index) => (
                    <div className="handoff-path" key={`${handoff.input_artifact_ids[index]}-${index}`}>
                      <code>{path}</code>
                      <button type="button" onClick={() => void copyPath(path)}>Copy input path</button>
                    </div>
                  ))}
                  <span className="handoff-queue-label">Targets</span>
                  {handoff.output_targets.map((target) => (
                    <div className="handoff-path" key={`${target.port_id}-${target.ordinal ?? 'one'}`}>
                      <code>{target.port_id}{target.ordinal === null ? '' : ` #${target.ordinal}`} · {target.path}</code>
                      <button type="button" onClick={() => void copyPath(target.path)}>Copy target path</button>
                    </div>
                  ))}
                  <button
                    className="button button--primary handoff-queue-submit"
                    type="button"
                    disabled={!canValidate}
                    onClick={() => void validateAndSubmit(nodeRun)}
                  >
                    Validate and submit
                  </button>
                </article>
              )
            })}
          </section>
        )}
        {viewedSummary?.actionable && <button className="button button--danger abandon-run" type="button" disabled={detailMutationBlocked || viewedSummary.state_counts.running > 0} onClick={() => viewRunId && void executeCommands([{ operation: 'abandon_run', run_id: viewRunId }])}>Abandon Run</button>}
        {clientHint && <p className="client-hint" role="status">{clientHint}</p>}
        {boundaryError && draft && <p className="client-hint client-hint--error" role="alert">{boundaryError}</p>}
      </aside>

      <section className={`bottom-drawer ${bottomOpen ? 'is-open' : ''}`}>
        <button className="drawer-toggle" type="button" onClick={() => setBottomOpen((open) => !open)}><span>Graph diagnostics</span><strong>{diagnostics.length + (status?.error ? 1 : 0)}</strong><i>{bottomOpen ? '收起' : '展开'}</i></button>
        {bottomOpen && <div className="diagnostic-list">{diagnostics.length === 0 && !status?.error ? <div className="diagnostic-empty">graph_valid · 可保存和运行</div> : diagnostics.map((diagnostic) => <article className="diagnostic diagnostic--error" key={`${diagnostic.code}-${diagnostic.node_id ?? diagnostic.edge_id ?? 'graph'}`}><span className="diagnostic-icon">×</span><div><span className="diagnostic-code">{diagnostic.code}</span><strong>Graph Core</strong><p>{diagnostic.message}</p></div><button type="button" onClick={() => { if (diagnostic.node_id) setSelectedNodeIds(new Set([diagnostic.node_id])); if (diagnostic.edge_id) setSelectedEdgeIds(new Set([diagnostic.edge_id])) }}>定位</button></article>)}{status?.error && <article className="diagnostic diagnostic--error"><span className="diagnostic-icon">×</span><div><span className="diagnostic-code">{status.error.code}</span><strong>Project Service</strong><p>{status.error.message}</p></div></article>}{historyCursor && <button className="button button--ghost" type="button" disabled={historyBusy} onClick={() => void loadOlderRuns()}>{historyBusy ? '读取历史…' : '加载更早 Run'}</button>}</div>}
      </section>
    </main>
  )
}
