/** 先只读选择，再显式检查和收纳；检查票据与提交意图精确绑定，迟到响应不进入新任务。 */
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import type { NodeRunWire } from './contracts'
import type { HandoffImportBinding, HostBridge } from './host-bridge'
import { assertIntakeBinding, type HandoffIntakeCandidate, type HandoffIntakeObserveEnvelope,
  type HandoffIntakeSelectEnvelope, type HandoffIntakeJobEnvelope } from './handoff-intake-contracts'
import { formatHostBridgeError } from './host-error-presentation'
import { formatHandoffValidationMessage } from './HandoffContract'

type Phase = 'idle' | 'selecting' | 'checking' | 'copying' | 'submitting'
interface State {
  readonly scope: string
  readonly phase: Phase
  readonly observation: HandoffIntakeObserveEnvelope | null
  readonly selected: HandoffIntakeSelectEnvelope | null
  readonly job: HandoffIntakeJobEnvelope | null
  readonly recoveredPath: string | null
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
  readonly onCheckPublished: (nodeRun: NodeRunWire, token: symbol) => Promise<string>
  readonly onSubmit: (nodeRun: NodeRunWire, readyId: string | null, token: symbol, recoveredPath: string | null, isCurrent: () => boolean) => Promise<void>
}
interface Flight {
  readonly token: symbol
  readonly scope: string
  readonly nodeRun: NodeRunWire
  readonly binding: HandoffImportBinding
  phase: Phase
  invalidated: boolean
}
export interface HandoffIntakeController extends Omit<State, 'scope'> {
  readonly available: boolean
  readonly busy: boolean
  refresh(): Promise<void>
  choose(candidate?: HandoffIntakeCandidate): Promise<void>
  check(overwrite: boolean): Promise<void>
  checkPublished(): Promise<void>
  submit(): Promise<void>
}
const idle = (scope: string): State => ({ scope, phase: 'idle', observation: null, selected: null, job: null, recoveredPath: null, message: null, error: null, rawError: null })

export function useHandoffIntake(options: Options): HandoffIntakeController {
  const latest = useRef(options)
  latest.current = options
  const mounted = useRef(true)
  const active = useRef<Flight | null>(null)
  const pause = useRef<{ timer: ReturnType<typeof setTimeout>; resolve: () => void } | null>(null)
  const [state, setState] = useState<State>(() => idle(options.scope))
  const stateRef = useRef(state)
  stateRef.current = state
  const current = useCallback((flight: Flight) => mounted.current && !flight.invalidated && active.current === flight &&
    latest.current.scope === flight.scope && latest.current.operationRef.current === flight.token && latest.current.isCurrent(flight.nodeRun), [])
  const release = useCallback((flight: Flight) => {
    if (active.current !== flight) return
    active.current = null
    if (latest.current.operationRef.current === flight.token) latest.current.operationRef.current = null
    latest.current.onBusyChange(false)
    if (mounted.current) setState((before) => before.scope === latest.current.scope ? { ...before, phase: 'idle' } : idle(latest.current.scope))
  }, [])
  useEffect(() => {
    const flight = active.current
    if (flight && (flight.scope !== options.scope || !options.isCurrent(flight.nodeRun))) {
      flight.invalidated = true
      // 已开始的检查/复制仍属旧任务；切选区不冒充取消，也不接纳 A→B→A 后的旧回执。
      if (flight.phase === 'selecting') release(flight)
    }
    setState((before) => before.scope === options.scope ? before : idle(options.scope))
  }, [options.scope, options.isCurrent, release])
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (pause.current) { clearTimeout(pause.current.timer); pause.current.resolve(); pause.current = null }
      if (active.current) { active.current.invalidated = true; release(active.current) }
    }
  }, [release])
  const start = useCallback((phase: Phase): Flight | null => {
    const settings = latest.current, nodeRun = settings.nodeRun, handoff = nodeRun?.external_handoff
    const target = handoff?.output_targets.length === 1 ? handoff.output_targets[0] : null
    if (!settings.hostBridge.configured || !settings.projectSessionId || !nodeRun || !handoff || !target || target.ordinal !== null ||
        active.current || settings.operationRef.current || !settings.canStart() || !settings.isCurrent(nodeRun)) return null
    const flight: Flight = { token: Symbol('handoff-intake'), scope: settings.scope, nodeRun, phase, invalidated: false,
      binding: { contract_version: '0.3.0', project_session_id: settings.projectSessionId, run_id: nodeRun.run_id,
        node_run_id: nodeRun.node_run_id, handoff_id: handoff.handoff_id, port_id: target.port_id, ordinal: null } }
    active.current = flight
    settings.operationRef.current = flight.token
    settings.onBusyChange(true)
    setState((before) => ({ ...(before.scope === settings.scope ? before : idle(settings.scope)), phase, error: null, rawError: null, message: null }))
    return flight
  }, [])
  const fail = useCallback((flight: Flight, error: unknown, fallback: string) => {
    if (!current(flight)) return
    const raw = error instanceof Error ? error.message : String(error)
    const explanation = formatHostBridgeError(error) ?? formatHandoffValidationMessage(raw)
    setState((before) => ({ ...before, error: explanation ? `${fallback}${explanation}` : fallback, rawError: raw, job: null, recoveredPath: null }))
  }, [current])
  const select = useCallback(async (flight: Flight, candidate: HandoffIntakeCandidate | undefined) => {
    const bridge = latest.current.hostBridge
    if (!bridge.selectHandoffIntake) return
    const selection = candidate ? null : await bridge.pick('open_file', { title: '选择处理好的文件' })
    if (!current(flight) || (!candidate && !selection?.length)) return
    if (!candidate && selection!.length !== 1) throw new Error('请选择且只选择一个处理好的文件。')
    const value = await bridge.selectHandoffIntake({ ...flight.binding, selection_handle: selection?.[0]?.selection_handle ?? null,
      candidate_handle: candidate?.candidate_handle ?? null })
    if (!current(flight)) return
    assertIntakeBinding(flight.binding, value)
    if (candidate && (value.source_name !== candidate.name || value.source_size !== candidate.size || value.container !== candidate.container)) {
      throw new Error('候选文件已变化，请刷新后重新选择。')
    }
    latest.current.onChanged(flight.nodeRun)
    setState((before) => ({ ...before, selected: value, job: null, recoveredPath: null, message: '已选择文件，尚未复制或重命名。请检查并导入。' }))
  }, [current])
  const refresh = useCallback(async () => {
    const bridge = latest.current.hostBridge
    if (!bridge.observeHandoffIntake || !bridge.selectHandoffIntake) return
    const flight = start('selecting')
    if (!flight) return
    // 刷新代表重新观察文件；旧检查资格不能随着相同文件名被沿用。
    setState((before) => ({ ...before, selected: null, job: null, recoveredPath: null }))
    latest.current.onChanged(flight.nodeRun)
    try {
      const value = await bridge.observeHandoffIntake(flight.binding)
      if (!current(flight)) return
      assertIntakeBinding(flight.binding, value)
      setState((before) => ({ ...before, observation: value }))
      if (value.candidates.length === 1) await select(flight, value.candidates[0])
    } catch (error) { fail(flight, error, '暂时无法读取交回文件，请刷新或重新选择。') }
    finally { release(flight) }
  }, [current, fail, release, select, start])
  useEffect(() => { void refresh() }, [options.scope, options.nodeRun?.node_run_id, options.projectSessionId, refresh])
  const choose = useCallback(async (candidate?: HandoffIntakeCandidate) => {
    const flight = start('selecting')
    if (!flight) return
    try { await select(flight, candidate) }
    catch (error) { fail(flight, error, '未能选择处理结果，没有复制文件。请重新选择。') }
    finally { release(flight) }
  }, [fail, release, select, start])
  const check = useCallback(async (overwrite: boolean) => {
    const settings = latest.current, selected = stateRef.current.scope === settings.scope ? stateRef.current.selected : null
    if (!selected || !settings.hostBridge.checkHandoffIntake || !settings.hostBridge.inspectHandoffIntake || (selected.replace_existing && !overwrite)) return
    const flight = start('checking')
    if (!flight) return
    setState((before) => ({ ...before, job: null, recoveredPath: null }))
    settings.onChanged(flight.nodeRun)
    try {
      const job = await settings.hostBridge.checkHandoffIntake({ contract_version: '0.3.0', ticket_id: selected.ticket_id, overwrite })
      // 用户可以离开选区，已确认的后台收纳仍需等待终态才能释放共用操作锁。
      while (mounted.current && active.current === flight) {
        const value = await settings.hostBridge.inspectHandoffIntake(job)
        if (value.job_id !== job.job_id || (value.phase === 'ready') !== (value.ready_id !== null) || value.bytes_done > value.total_bytes) {
          throw new Error('检查回执不属于本次检查任务。')
        }
        if (current(flight)) setState((before) => ({ ...before, job: value,
          phase: value.phase === 'copying' ? 'copying' : value.phase === 'checking' ? 'checking' : before.phase,
          message: value.phase === 'ready' ? '检查通过，文件已收纳。等待你提交并继续。' : value.message }))
        if (value.phase === 'failed') throw new Error(value.message ?? '文件检查或收纳未完成。')
        if (value.phase === 'ready') break
        flight.phase = value.phase
        await new Promise<void>((resolve) => { const timer = setTimeout(() => { pause.current = null; resolve() }, 750); pause.current = { timer, resolve } })
      }
    } catch (error) {
      fail(flight, error, '检查或导入未完成，没有提交任务。请确认文件写入结束并重新选择。')
      if (current(flight)) setState((before) => ({ ...before, selected: null }))
    } finally { release(flight) }
  }, [current, fail, release, start])
  const checkPublished = useCallback(async () => {
    const flight = start('checking')
    if (!flight) return
    setState((before) => ({ ...before, selected: null, job: null, recoveredPath: null }))
    try {
      const path = await latest.current.onCheckPublished(flight.nodeRun, flight.token)
      if (current(flight)) setState((before) => ({ ...before, recoveredPath: path, message: '已收纳输出检查通过；没有复制文件，等待你提交并继续。' }))
    } catch (error) { fail(flight, error, '尚未找到可提交的已收纳输出。请刷新交回目录或选择处理好的文件。') }
    finally { release(flight) }
  }, [current, fail, release, start])
  const submit = useCallback(async () => {
    const settings = latest.current, visible = stateRef.current.scope === settings.scope ? stateRef.current : null
    const readyId = visible?.job?.ready_id ?? null, recoveredPath = visible?.recoveredPath ?? null
    if (!readyId && !recoveredPath) return
    const flight = start('submitting')
    if (!flight) return
    setState((before) => ({ ...before, job: null, recoveredPath: null }))
    try {
      await settings.onSubmit(flight.nodeRun, readyId, flight.token, recoveredPath, () => current(flight))
      if (current(flight)) setState((before) => ({ ...before, selected: null, message: '已提交；请查看节点的最新处理状态。' }))
    } catch (error) { fail(flight, error, '没有确认提交成功。已收文件保留，请核对节点状态和原始详情后再操作。') }
    finally { release(flight) }
  }, [current, fail, release, start])
  const visible = state.scope === options.scope ? state : idle(options.scope)
  const bridge = options.hostBridge
  return { ...visible, busy: active.current !== null, available: bridge.configured && !!bridge.observeHandoffIntake &&
    !!bridge.selectHandoffIntake && !!bridge.checkHandoffIntake && !!bridge.inspectHandoffIntake && !!bridge.publishHandoffIntake,
    choose, refresh, check, checkPublished, submit }
}
