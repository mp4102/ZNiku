/** 选择/预览不复制；收件/检查不提交。每个异步回执同时绑定选区、工程、Run 与 handoff。 */
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import type { ExternalHandoffReadiness, NodeRunWire } from './contracts'
import type { HostBridge } from './host-bridge'
import { assertBatchObservation, assertBatchTargets, type HandoffBatchBinding, type HandoffBatchObserveEnvelope,
  type HandoffBatchPreviewEnvelope, type HandoffBatchConfirmRequest } from './handoff-batch-contracts'
import { isFullCheck } from './handoff-check'
import { formatHostBridgeError } from './host-error-presentation'
import type { ActivityListener, LocalActivity } from './operation-presentation'

type Phase = 'idle' | 'selecting' | 'preview' | 'copying' | 'checking'
interface State {
  readonly scope: string
  readonly phase: Phase
  readonly observation: HandoffBatchObserveEnvelope | null
  readonly preview: HandoffBatchPreviewEnvelope | null
  readonly message: string | null
  readonly error: string | null
  readonly rawError: string | null
}
interface Options {
  readonly hostBridge: HostBridge
  readonly projectSessionId: string | null
  readonly nodeRun: NodeRunWire | null
  readonly scope: string
  readonly operationRef: RefObject<symbol | null>
  readonly canStart: () => boolean
  readonly isCurrent: (nodeRun: NodeRunWire) => boolean
  readonly onBusyChange: (busy: boolean) => void
  readonly onChanged: (nodeRun: NodeRunWire) => void
  readonly onChecked: (nodeRun: NodeRunWire, readiness: ExternalHandoffReadiness) => void
  readonly onActivity?: ActivityListener
  readonly taskLabel?: string
}
interface Flight {
  readonly token: symbol
  readonly scope: string
  readonly nodeRun: NodeRunWire
  readonly binding: HandoffBatchBinding
  phase: Exclude<Phase, 'idle'>
  preview: HandoffBatchPreviewEnvelope | null
  invalidated: boolean
  readonly label: string
  activityResolved: boolean
  requestedAt: number
}
export interface HandoffBatchController extends Omit<State, 'scope'> {
  readonly busy: boolean
  readonly available: boolean
  choose(mode: 'open_files' | 'select_directory' | 'inbox'): Promise<void>
  confirm(items: HandoffBatchConfirmRequest['items']): Promise<void>
  check(overwritePorts: ReadonlyArray<string>): Promise<void>
  refresh(): Promise<void>
  cancel(): void
}
const idle = (scope: string): State => ({ scope, phase: 'idle', observation: null, preview: null, message: null, error: null, rawError: null })

export function useHandoffBatch(options: Options): HandoffBatchController {
  const latest = useRef(options)
  latest.current = options
  const active = useRef<Flight | null>(null)
  const mounted = useRef(true)
  const reads = useRef(0)
  const readScope = useRef(options.scope)
  if (readScope.current !== options.scope) {
    readScope.current = options.scope
    reads.current++
  }
  const [state, setState] = useState<State>(() => idle(options.scope))
  const announce = (flight: Flight, phase: LocalActivity['phase'], message: string, replace = false) => {
    flight.activityResolved = phase !== 'requesting'
    latest.current.onActivity?.({ token: flight.token, projectSessionId: flight.binding.project_session_id,
      nodeRun: flight.nodeRun, label: flight.label, phase, message, requestedAt: flight.requestedAt }, replace)
  }
  const stateRef = useRef(state)
  stateRef.current = state
  const current = useCallback((flight: Flight) => mounted.current && !flight.invalidated && active.current === flight && latest.current.scope === flight.scope &&
    latest.current.operationRef.current === flight.token && latest.current.isCurrent(flight.nodeRun), [])
  const release = useCallback((flight: Flight) => {
    // UI 选区失效不等于请求仍在运行；迟到预览必须结束全局等待，但不注入新选区。
    if (!flight.activityResolved) announce(flight, flight.phase === 'selecting' ? 'needs_user' : 'uncertain',
      flight.phase === 'selecting' ? '预览交互已结束；未收件，请在当前任务重新选择' : '操作响应未能确认，请回到原任务核对正式状态，不自动重试')
    if (active.current !== flight) return
    active.current = null
    if (latest.current.operationRef.current === flight.token) latest.current.operationRef.current = null
    latest.current.onBusyChange(false)
    if (mounted.current) setState((before) => before.scope === latest.current.scope ? { ...before, phase: 'idle', preview: null } : idle(latest.current.scope))
  }, [])
  useEffect(() => {
    const flight = active.current
    if (flight && (flight.scope !== options.scope || !options.isCurrent(flight.nodeRun))) {
      // A→B→A 也不能重新接纳离开选区前的在途回执；身份相同不等于同一交互世代。
      flight.invalidated = true
      // 已确认的文件副作用继续绑定旧任务，不能在切换时解锁并假装取消；其迟到结果不进入新选区。
      if (flight.phase !== 'copying' && flight.phase !== 'checking') release(flight)
    }
    setState((before) => before.scope === options.scope ? before : idle(options.scope))
  }, [options.scope, options.isCurrent, release])
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      reads.current++
      const flight = active.current
      if (flight && flight.phase !== 'copying' && flight.phase !== 'checking') release(flight)
    }
  }, [release])
  const bindingFor = (settings: Options): HandoffBatchBinding | null => settings.projectSessionId && settings.nodeRun?.external_handoff ? {
    contract_version: '0.3.0', project_session_id: settings.projectSessionId, run_id: settings.nodeRun.run_id,
    node_run_id: settings.nodeRun.node_run_id, handoff_id: settings.nodeRun.external_handoff.handoff_id,
  } : null
  const accept = (binding: HandoffBatchBinding, value: HandoffBatchObserveEnvelope, nodeRun: NodeRunWire) => {
    assertBatchObservation(binding, value)
    assertBatchTargets(value, nodeRun)
  }
  const refresh = useCallback(async () => {
    const settings = latest.current, binding = bindingFor(settings), nodeRun = settings.nodeRun
    if (!binding || !nodeRun || !settings.hostBridge.observeHandoffBatch || active.current || settings.operationRef.current ||
        !settings.canStart() || !settings.isCurrent(nodeRun)) return
    const sequence = ++reads.current, scope = settings.scope
    const valid = () => mounted.current && sequence === reads.current && scope === latest.current.scope &&
      !active.current && latest.current.isCurrent(nodeRun)
    try {
      const result = await settings.hostBridge.observeHandoffBatch(binding)
      if (!valid()) return
      accept(binding, result, nodeRun)
      setState((before) => ({ ...before, observation: result }))
    } catch (error) {
      if (valid()) setState((before) => ({ ...before, observation: null, error: '暂时无法读取本章收件状态，请刷新后再操作。',
        rawError: error instanceof Error ? error.message : String(error) }))
    }
  }, [])
  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => { void refresh() }, 3_000)
    return () => window.clearInterval(timer)
  }, [options.scope, refresh])

  const start = useCallback((phase: Flight['phase']): Flight | null => {
    const settings = latest.current, binding = bindingFor(settings), nodeRun = settings.nodeRun
    if (!binding || !nodeRun || active.current || settings.operationRef.current || !settings.canStart() || !settings.isCurrent(nodeRun)) return null
    const flight: Flight = { token: Symbol('chapter-handoff'), scope: settings.scope, nodeRun, binding, phase, preview: null, invalidated: false, label: settings.taskLabel ?? nodeRun.node_id, activityResolved: false, requestedAt: Date.now() }
    active.current = flight
    reads.current++
    settings.operationRef.current = flight.token
    settings.onBusyChange(true)
    announce(flight, 'requesting', phase === 'checking' ? '正在请求整章检查，等待服务响应' : '正在准备选择或收件预览', true)
    setState((before) => ({ ...before, phase, message: null, error: null, rawError: null }))
    return flight
  }, [])
  const fail = useCallback((flight: Flight, error: unknown, message: string) => {
    announce(flight, 'uncertain', message)
    if (current(flight)) setState((before) => ({ ...before, error: `${message}${formatHostBridgeError(error) ?? ''}`,
      rawError: error instanceof Error ? error.message : String(error) }))
  }, [current])
  const choose = useCallback(async (mode: 'open_files' | 'select_directory' | 'inbox') => {
    const settings = latest.current
    if (!settings.hostBridge.previewHandoffBatch || !settings.hostBridge.confirmHandoffBatch) return
    const flight = start('selecting')
    if (!flight) return
    try {
      const selections = mode === 'inbox' ? [] : await settings.hostBridge.pick(mode, { title: '选择本章处理好的文件' })
      if (!current(flight) || (mode !== 'inbox' && !selections?.length)) { announce(flight, 'needs_user', '未收件，等待你选择处理结果'); return }
      const result = await settings.hostBridge.previewHandoffBatch({ ...flight.binding, selection_handles: selections!.map((item) => item.selection_handle) })
      if (!current(flight)) return
      accept(flight.binding, result, flight.nodeRun)
      flight.phase = 'preview'
      flight.preview = result
      announce(flight, 'needs_user', '匹配预览已就绪，等待你确认收件')
      setState((before) => ({ ...before, phase: 'preview', observation: result, preview: result }))
    } catch (error) { fail(flight, error, '未能准备匹配预览，没有复制文件。请重新选择或发现来件。') }
    finally { if (flight.phase !== 'preview') release(flight) }
  }, [current, fail, release, start])
  const confirm = useCallback(async (items: HandoffBatchConfirmRequest['items']) => {
    const flight = active.current, method = latest.current.hostBridge.confirmHandoffBatch
    if (!flight || flight.phase !== 'preview' || !flight.preview || !current(flight) || !method || !items.length) return
    const preview = flight.preview
    if (new Set(items.map((item) => item.port_id)).size !== items.length || new Set(items.map((item) => item.candidate_handle)).size !== items.length ||
        items.some((item) => !preview.rows.some((row) => row.port_id === item.port_id && (!row.collected || item.overwrite || preview.candidates.some((candidate) => candidate.candidate_handle === item.candidate_handle && candidate.unchanged_port_ids?.includes(item.port_id)))) ||
          !preview.candidates.some((candidate) => candidate.candidate_handle === item.candidate_handle))) return
    flight.phase = 'copying'
    flight.requestedAt = Date.now()
    announce(flight, 'requesting', '正在请求收件，等待服务响应；请勿重复操作')
    latest.current.onChanged(flight.nodeRun)
    setState((before) => ({ ...before, phase: 'copying', message: '正在请求收件，等待服务响应；尚无可计算的复制进度，请勿重复操作。' }))
    try {
      const result = await method.call(latest.current.hostBridge, { contract_version: '0.3.0', batch_id: preview.batch_id, items })
      accept(flight.binding, result, flight.nodeRun)
      if (result.batch_id !== preview.batch_id) throw new Error('收件回执与当前预览不一致。')
      if (!current(flight)) { announce(flight, 'uncertain', '收件响应已返回，但选区或任务已变化；请回到原任务核对实际文件，不自动重试'); return }
      announce(flight, 'needs_user', result.results.some((item) => item.status === 'failed') ? '部分收件失败，请核对保留文件再补件' : '收件响应已返回，齐全后请整章检查')
      if (!current(flight)) return
      accept(flight.binding, result, flight.nodeRun)
      const failures = result.results.filter((item) => item.status === 'failed')
      setState((before) => ({ ...before, observation: result, message: failures.length ? '部分文件未收件成功，已收文件保留；请补件后重新检查。' : '已收件。可继续补件；齐全后请整章检查。尚未提交。',
        error: failures.length ? failures.map((item) => item.message ?? '收件失败').join('；') : null }))
    } catch (error) { fail(flight, error, '未确认全部收件成功；没有自动提交。请刷新核对后重新预览。') }
    finally { release(flight) }
  }, [current, fail, release])
  const check = useCallback(async (overwritePorts: ReadonlyArray<string>) => {
    const settings = latest.current
    if (!settings.hostBridge.checkHandoffBatch || stateRef.current.scope !== settings.scope || !stateRef.current.observation?.complete) return
    const flight = start('checking')
    if (!flight) return
    settings.onChanged(flight.nodeRun)
    setState((before) => ({ ...before, message: '正在请求整章检查，等待服务响应；这不是外部 AI 处理进度。' }))
    try {
      const result = await settings.hostBridge.checkHandoffBatch({ ...flight.binding, overwrite_ports: overwritePorts })
      accept(flight.binding, result, flight.nodeRun)
      if (!current(flight)) { announce(flight, 'uncertain', '检查响应已返回，但选区或任务已变化；请回到原任务重新核对，不授予提交资格'); return }
      announce(flight, 'needs_user', result.published && result.readiness && !result.validation_error && isFullCheck(result.readiness, flight.nodeRun) ? '整章检查通过，等待你提交并继续' : '整章检查未通过，请核对问题后重新检查')
      if (!current(flight)) return
      accept(flight.binding, result, flight.nodeRun)
      setState((before) => ({ ...before, observation: result }))
      if (!result.published || !result.readiness || result.validation_error || !isFullCheck(result.readiness, flight.nodeRun)) {
        setState((before) => ({ ...before, message: null,
          error: `整章检查未通过。${result.validation_error?.message ?? ''} 已收件与上游结果保留，请替换问题文件后重新检查；没有提交。`,
          rawError: result.validation_error ? `${result.validation_error.code}: ${result.validation_error.message}` : null }))
        return
      }
      settings.onChecked(flight.nodeRun, result.readiness)
      setState((before) => ({ ...before, message: '整章检查通过，等待你显式提交并继续。', error: null, rawError: null }))
    } catch (error) { fail(flight, error, '整章检查未确认通过；没有提交。请核对文件后重试检查。') }
    finally { release(flight) }
  }, [current, fail, release, start])
  const cancel = useCallback(() => {
    const flight = active.current
    if (!flight || flight.phase === 'copying' || flight.phase === 'checking') return
    announce(flight, 'needs_user', '已取消预览，等待你交回处理结果；文件未改动')
    release(flight)
  }, [release])
  const visible = state.scope === options.scope ? state : idle(options.scope)
  return { ...visible, busy: active.current !== null, available: !!options.hostBridge.observeHandoffBatch && !!options.hostBridge.previewHandoffBatch &&
    !!options.hostBridge.confirmHandoffBatch && !!options.hostBridge.checkHandoffBatch, choose, confirm, check, refresh, cancel }
}
