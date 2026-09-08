/**
 * 外部文件导入只绑定当前选择的正式交接目标；预览与复制分离，绝不代替显式 Submit。
 * 原生选择、迟到预览和复制回执都受 session/Run/选区 fence 约束，切换视图不能把 A 文件投给 B。
 */
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import type { ExternalOutputTargetWire, NodeRunWire } from './contracts'
import type { HostBridge, HandoffImportPreviewEnvelope, HandoffImportPreviewRequest } from './host-bridge'
import { formatHostBridgeError } from './host-error-presentation'
import { formatHandoffValidationMessage } from './HandoffContract'

export interface HandoffImportView {
  readonly envelope: HandoffImportPreviewEnvelope
  readonly sourcePath: string
  readonly nodeTitle: string
}
export interface HandoffImportController {
  readonly busy: boolean
  readonly copying: boolean
  readonly preview: HandoffImportView | null
  readonly error: string | null
  readonly rawError: string | null
  readonly message: string | null
  choose(nodeRun: NodeRunWire, target: ExternalOutputTargetWire, nodeTitle: string): Promise<void>
  confirm(overwrite: boolean): Promise<void>
  cancel(): void
}
interface ImportOptions {
  readonly hostBridge: HostBridge
  readonly projectSessionId: string | null
  readonly scope: string
  readonly operationRef: RefObject<symbol | null>
  readonly canStart: () => boolean
  readonly isCurrent: (nodeRun: NodeRunWire) => boolean
  readonly onBusyChange: (busy: boolean) => void
  readonly onImportStarted: (nodeRun: NodeRunWire) => void
  readonly onImported: (nodeRun: NodeRunWire) => Promise<void>
}
interface Flight {
  readonly token: symbol
  readonly scope: string
  readonly nodeRun: NodeRunWire
  readonly target: ExternalOutputTargetWire
  phase: 'selecting' | 'preview' | 'copying'
  view: HandoffImportView | null
}
interface ImportState {
  readonly scope: string
  readonly phase: 'idle' | 'selecting' | 'preview' | 'copying'
  readonly preview: HandoffImportView | null
  readonly error: string | null
  readonly rawError: string | null
  readonly message: string | null
}
const idle = (scope: string): ImportState => ({ scope, phase: 'idle', preview: null, error: null, rawError: null, message: null })

function importErrorMessage(error: unknown): string | null {
  const hostMessage = formatHostBridgeError(error)
  if (hostMessage) return hostMessage
  if (!(error instanceof Error)) return null
  if (/\bE_[A-Z_]*SAME_FILE\b/.test(error.message)) return '文件已经在当前任务的目标位置，无需再次导入；请直接检查输出。'
  return formatHandoffValidationMessage(error.message)
}

function sameBinding(request: HandoffImportPreviewRequest, response: HandoffImportPreviewRequest): boolean {
  return request.project_session_id === response.project_session_id && request.run_id === response.run_id &&
    request.node_run_id === response.node_run_id && request.handoff_id === response.handoff_id &&
    request.port_id === response.port_id && request.ordinal === response.ordinal && request.selection_handle === response.selection_handle
}

export function useHandoffImport(options: ImportOptions): HandoffImportController {
  const latest = useRef(options)
  latest.current = options
  const active = useRef<Flight | null>(null)
  const [state, setState] = useState<ImportState>(() => idle(options.scope))
  const current = useCallback((flight: Flight) => active.current === flight &&
    latest.current.scope === flight.scope && latest.current.operationRef.current === flight.token &&
    latest.current.isCurrent(flight.nodeRun), [])
  const release = useCallback((flight: Flight) => {
    if (active.current !== flight) return
    active.current = null
    if (latest.current.operationRef.current === flight.token) latest.current.operationRef.current = null
    latest.current.onBusyChange(false)
    setState((previous) => previous.phase === 'idle' ? { ...previous } : idle(latest.current.scope))
  }, [])
  useEffect(() => {
    const flight = active.current
    if (!flight || (flight.scope === options.scope && options.isCurrent(flight.nodeRun))) return
    // 已显式确认的复制不能假装被取消；它仍绑定旧任务，直到后台返回才释放互斥。
    if (flight.phase !== 'copying') release(flight)
    setState((previous) => previous.scope === options.scope && previous.phase === 'idle' ? previous : idle(options.scope))
  }, [options.scope, options.isCurrent, release])
  useEffect(() => () => {
    const flight = active.current
    if (flight) release(flight)
  }, [release])

  const choose = useCallback(async (nodeRun: NodeRunWire, target: ExternalOutputTargetWire, nodeTitle: string) => {
    const settings = latest.current
    const handoff = nodeRun.external_handoff
    const previewImport = settings.hostBridge.previewHandoffImport
    if (!handoff || handoff.output_targets.length !== 1 || !settings.projectSessionId || !previewImport || !settings.hostBridge.confirmHandoffImport ||
        settings.operationRef.current || active.current || !settings.canStart() || !settings.isCurrent(nodeRun) ||
        !handoff.output_targets.some((item) => item.port_id === target.port_id && item.ordinal === target.ordinal && item.path === target.path)) return
    const flight: Flight = { token: Symbol('import-handoff'), scope: settings.scope, nodeRun, target, phase: 'selecting', view: null }
    active.current = flight
    settings.operationRef.current = flight.token
    settings.onBusyChange(true)
    setState({ ...idle(flight.scope), phase: 'selecting', message: '正在选择处理好的文件…' })
    try {
      const choices = await settings.hostBridge.pick('open_file', { title: `选择处理好的文件 · ${nodeTitle}` })
      if (!current(flight)) return
      if (!choices?.length) { setState(idle(flight.scope)); return }
      if (choices.length !== 1) throw new Error('只能为当前目标选择一个处理好的文件。')
      const selection = choices[0]!
      const request: HandoffImportPreviewRequest = {
        contract_version: '0.3.0', selection_handle: selection.selection_handle, project_session_id: settings.projectSessionId,
        run_id: nodeRun.run_id, node_run_id: nodeRun.node_run_id, handoff_id: handoff.handoff_id,
        port_id: target.port_id, ordinal: target.ordinal,
      }
      const preview = await previewImport.call(settings.hostBridge, request)
      if (!current(flight)) return
      if (!sameBinding(request, preview) || preview.target_path !== target.path) throw new Error('返回的文件预览与当前任务不一致；没有复制文件，请重新选择。')
      flight.phase = 'preview'
      flight.view = { envelope: preview, sourcePath: selection.path, nodeTitle }
      setState({ ...idle(flight.scope), phase: 'preview', preview: flight.view })
    } catch (error) {
      if (current(flight)) setState({ ...idle(flight.scope), rawError: error instanceof Error ? error.message : String(error),
        error: `当前任务“${nodeTitle}”未能准备导入。源文件与目标均未改动。${importErrorMessage(error) ?? '请检查所选文件与任务要求，重新选择。'}` })
    } finally {
      if (flight.phase !== 'preview') release(flight)
    }
  }, [current, release])

  const confirm = useCallback(async (overwrite: boolean) => {
    const flight = active.current
    if (!flight || flight.phase !== 'preview' || !flight.view || !current(flight)) return
    const view = flight.view
    if (view.envelope.replace_existing && !overwrite) return
    const confirmImport = latest.current.hostBridge.confirmHandoffImport
    if (!confirmImport) return
    flight.phase = 'copying'
    latest.current.onImportStarted(flight.nodeRun)
    setState({ ...idle(flight.scope), phase: 'copying', preview: view, message: '正在导入外部文件…复制与验证完成前请勿关闭应用。' })
    try {
      const result = await confirmImport.call(latest.current.hostBridge, { contract_version: '0.3.0', import_id: view.envelope.import_id, overwrite })
      if (!current(flight)) return
      if (result.import_id !== view.envelope.import_id || result.project_session_id !== view.envelope.project_session_id ||
          result.run_id !== flight.nodeRun.run_id || result.node_run_id !== flight.nodeRun.node_run_id ||
          result.handoff_id !== flight.nodeRun.external_handoff?.handoff_id || result.port_id !== flight.target.port_id ||
          result.ordinal !== flight.target.ordinal || result.target_path !== flight.target.path || result.status !== 'imported') {
        throw new Error('导入回执与当前任务不一致。')
      }
      await latest.current.onImported(flight.nodeRun)
      if (current(flight)) setState({ ...idle(flight.scope), message: `已导入到“${view.nodeTitle}”。尚未提交；请检查输出，再显式“提交并继续”。` })
    } catch (error) {
      if (current(flight)) setState({ ...idle(flight.scope), rawError: error instanceof Error ? error.message : String(error),
        error: `当前任务“${view.nodeTitle}”未确认导入成功，没有自动提交。源文件与已完成上游保留。${importErrorMessage(error) ?? '请核对目标文件与任务要求，再重新选择或检查输出。'}` })
    } finally { release(flight) }
  }, [current, release])
  const cancel = useCallback(() => {
    const flight = active.current
    if (!flight || flight.phase === 'copying') return
    release(flight)
    setState(idle(latest.current.scope))
  }, [release])
  const visible = state.scope === options.scope ? state : idle(options.scope)
  return { busy: active.current !== null, copying: active.current?.phase === 'copying',
    preview: visible.preview, message: visible.message, error: visible.error, rawError: visible.rawError, choose, confirm, cancel }
}
