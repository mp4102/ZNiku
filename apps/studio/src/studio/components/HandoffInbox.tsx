/** 当前任务的专属收件箱：只读发现、显式选择、确认收纳；从不自动 Submit。 */
import { useEffect, useRef, useState } from 'react'
import type {
  HandoffImportBinding, HandoffInboxCandidate, HandoffInboxObserveEnvelope,
  HandoffInboxPreviewEnvelope, HostBridge,
} from '../host-bridge'
import { HostBridgeError } from '../host-bridge'
import { formatHandoffValidationMessage } from '../HandoffContract'
import { formatHostBridgeError } from '../host-error-presentation'
import { CanvasDialog } from './ConnectNodesDialog'

export interface HandoffInboxProps {
  readonly bridge: HostBridge
  readonly binding: HandoffImportBinding
  /** 只包含其他操作/连接状态，不要把此组件自己的 onBusyChange 回灌到这里。 */
  readonly disabled: boolean
  readonly onCollected: () => void
  readonly onBusyChange?: (busy: boolean) => void
}

type Phase = 'idle' | 'previewing' | 'preview' | 'collecting'
function bindingKey(value: HandoffImportBinding): string {
  return [value.project_session_id, value.run_id, value.node_run_id, value.handoff_id, value.port_id, value.ordinal].join('/')
}
function matches(left: HandoffImportBinding, right: HandoffImportBinding): boolean {
  return left.contract_version === right.contract_version && bindingKey(left) === bindingKey(right)
}
function friendlyError(error: unknown): string {
  const host = formatHostBridgeError(error)
  if (host) return host
  const code = error instanceof HostBridgeError ? error.code : null
  if (code === 'E_HANDOFF_INBOX_EXPIRED') return '这次来件观察已失效，请刷新收件箱后重新选择。'
  if (code === 'E_HANDOFF_INBOX_CHANGED' || code === 'E_HANDOFF_IMPORT_CHANGED') return '文件仍在写入或已经被替换。请等外部工具完成后重新检查，已有产物未被覆盖。'
  if (code === 'E_HANDOFF_INBOX_LIMIT') return '收件箱文件过多，请只保留本任务的候选文件后刷新。'
  if (code === 'E_HANDOFF_INBOX_IO') return '未能收纳来件。请检查文件占用、磁盘权限和文件系统能力；也可以使用“选择处理好的文件”复制导入。'
  if (code === 'E_HANDOFF_INBOX_PARTIAL') return '收件原名称已变化，正式目标已保留供检查。请先检查输出及原始详情，不要重复收纳；任务没有自动提交。'
  if (code === 'E_HANDOFF_IMPORT_PATH') return '此任务的收件目录暂不可用。请确认工程数据磁盘已连接；旧任务仍可通过“选择处理好的文件”导入。'
  const message = error instanceof Error ? formatHandoffValidationMessage(error.message) : null
  return message ?? '收件操作未完成。请刷新确认实际文件状态，检查原始详情后重试；不会自动提交任务。'
}

export function HandoffInbox(props: HandoffInboxProps) {
  // 精确身份变化强制重建视图，不能让 A 的确认票据留在 B 的界面里。
  return <BoundHandoffInbox key={bindingKey(props.binding)} {...props} />
}

function BoundHandoffInbox(props: HandoffInboxProps) {
  const latest = useRef(props)
  latest.current = props
  const mounted = useRef(true)
  const phaseRef = useRef<Phase>('idle')
  const observingRef = useRef(false)
  const busyNotified = useRef(false)
  const observationFailed = useRef(false)
  const [phase, setPhase] = useState<Phase>('idle')
  const [observed, setObserved] = useState<HandoffInboxObserveEnvelope | null>(null)
  const [observing, setObserving] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [preview, setPreview] = useState<HandoffInboxPreviewEnvelope | null>(null)
  const [overwrite, setOverwrite] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rawError, setRawError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const available = props.bridge.configured && !!props.bridge.observeHandoffInbox &&
    !!props.bridge.previewHandoffInbox && !!props.bridge.confirmHandoffInbox

  const notifyBusy = (busy: boolean) => {
    if (busyNotified.current === busy) return
    busyNotified.current = busy
    latest.current.onBusyChange?.(busy)
  }
  const changePhase = (value: Phase) => {
    phaseRef.current = value
    if (mounted.current) setPhase(value)
  }
  const showError = (value: unknown) => {
    if (!mounted.current) return
    setError(friendlyError(value))
    setRawError(value instanceof Error ? value.message : String(value))
  }
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      // 已确认的移动不能假装取消；后台返回后才释放共享互斥。
      if (phaseRef.current !== 'collecting' && busyNotified.current) {
        busyNotified.current = false
        latest.current.onBusyChange?.(false)
      }
    }
  }, [])

  useEffect(() => {
    if (!available || props.disabled || phase !== 'idle') return
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const observe = async () => {
      if (disposed) return
      observingRef.current = true
      setObserving(true)
      try {
        const value = await props.bridge.observeHandoffInbox!(props.binding)
        if (!matches(value, props.binding)) throw new Error('收件观察响应不属于当前任务。')
        if (!disposed && mounted.current && phaseRef.current === 'idle') {
          setObserved(value)
          if (observationFailed.current) { setError(null); setRawError(null); observationFailed.current = false }
        }
      } catch (value) {
        if (!disposed) { observationFailed.current = true; setObserved(null); showError(value) }
      } finally {
        if (!disposed && mounted.current) {
          observingRef.current = false
          setObserving(false)
          timer = setTimeout(() => { void observe() }, 2_000)
        }
      }
    }
    void observe()
    return () => { disposed = true; observingRef.current = false; if (timer !== undefined) clearTimeout(timer) }
    // binding 的内容由外层 key 冻结；不用父组件每次构造的新对象重启轮询。
  }, [available, props.bridge, props.disabled, phase, refresh])

  const choose = async (candidate: HandoffInboxCandidate) => {
    if (!available || props.disabled || observingRef.current || phaseRef.current !== 'idle') return
    changePhase('previewing')
    notifyBusy(true)
    observationFailed.current = false
    setError(null); setRawError(null); setMessage(null); setOverwrite(false)
    try {
      const value = await props.bridge.previewHandoffInbox!({ ...props.binding, candidate_handle: candidate.candidate_handle })
      if (!mounted.current) return
      if (!matches(value, props.binding) || value.source_name !== candidate.name || value.source_size !== candidate.size) {
        throw new Error('收纳预览不属于当前任务与所选来件。')
      }
      setPreview(value)
      changePhase('preview')
    } catch (value) {
      showError(value)
      changePhase('idle')
      notifyBusy(false)
    }
  }

  const cancel = () => {
    if (phaseRef.current === 'collecting') return
    setPreview(null); setOverwrite(false)
    changePhase('idle')
    notifyBusy(false)
  }

  const collect = async () => {
    if (!preview || props.disabled || phaseRef.current !== 'preview' || (preview.replace_existing && !overwrite)) return
    changePhase('collecting')
    try {
      const result = await props.bridge.confirmHandoffInbox!({ contract_version: '0.3.0', inbox_id: preview.inbox_id, overwrite })
      if (!mounted.current) return
      if (!matches(result, props.binding) || result.inbox_id !== preview.inbox_id || result.source_name !== preview.source_name ||
        result.source_size !== preview.source_size || result.target_path !== preview.target_path || result.status !== 'collected') {
        throw new Error('收纳回执不属于已确认的任务，请刷新检查实际文件。')
      }
      setMessage('文件已按规范名称收纳。请检查输出，再由你“提交并继续”；系统没有自动提交。')
      latest.current.onCollected()
    } catch (value) {
      showError(value)
    } finally {
      if (mounted.current) { setPreview(null); setOverwrite(false); setObserved(null) }
      changePhase('idle')
      notifyBusy(false)
    }
  }

  const openInbox = async () => {
    if (!available || props.disabled || phaseRef.current !== 'idle') return
    try {
      await props.bridge.launch('reveal_in_file_manager', {
        kind: 'handoff', run_id: props.binding.run_id, node_run_id: props.binding.node_run_id,
        handoff_id: props.binding.handoff_id,
        selector: { role: 'incoming_directory', port_id: props.binding.port_id, ordinal: props.binding.ordinal },
      })
    } catch (value) { showError(value) }
  }

  return <section className="handoff-inbox" aria-label="当前任务收件箱">
    <h4>把处理好的文件放进本任务收件箱</h4>
    <p>来件名称不限。放入一个或多个候选后，由你选择并确认收纳；不会自动读取其他任务，也不会自动提交。</p>
    <div className="artifact-host-actions">
      <button type="button" disabled={!available || props.disabled || phase !== 'idle'} onClick={() => void openInbox()}>打开收件文件夹</button>
      <button type="button" disabled={!available || props.disabled || phase !== 'idle' || observing} onClick={() => { setError(null); setRawError(null); setRefresh((value) => value + 1) }}>刷新收件箱</button>
    </div>
    {observed && <p><code className="handoff-target-path">{observed.inbox_path}</code></p>}
    {!available && <p role="status">当前宿主尚未提供收件箱能力，请使用“选择处理好的文件”导入。</p>}
    {available && !observed && !error && <p role="status">{props.disabled ? '其他操作进行中，暂不检查收件箱。' : '正在查看此任务收件箱…'}</p>}
    {observed?.candidates.length === 0 && <p role="status">收件箱中尚无可用的 {observed.allowed_suffix} 文件。请等待外部工具完成，再把文件复制到这里。</p>}
    {observed && observed.candidates.length > 1 && <p>发现多个候选，请明确选择一个；系统不会猜最新或最大的文件。</p>}
    {observed && observed.candidates.length > 0 && <ul className="handoff-inbox-candidates">
      {observed.candidates.map((candidate) => <li key={candidate.candidate_handle}>
        <strong>{candidate.name}</strong> <span>{candidate.size.toLocaleString()} 字节</span>
        <button type="button" aria-label={`检查并收纳：${candidate.name}`} disabled={props.disabled || phase !== 'idle' || observing}
          onClick={() => void choose(candidate)}>检查并收纳</button>
      </li>)}
    </ul>}
    {!!observed?.rejected_count && <p>另有 {observed.rejected_count} 个条目不符合普通非空文件或声明后缀要求，未作为候选。</p>}
    <p>文件大小暂时不变不代表处理完成。收纳会检查实际输出要求，失败时不会推进任务。</p>
    {phase === 'previewing' && <p role="status">正在准备所选来件的收纳确认…</p>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
    {rawError && <details><summary>高级 → 收件原始详情</summary><pre>{rawError}</pre></details>}
    {preview && <CanvasDialog title="确认收纳外部处理文件" onClose={cancel}>
      <p>确认后先检查本任务要求，再将收件箱中的文件移动并命名为正式产物；来件原名称将不再保留。不会复制第二份大文件，也不会自动提交。</p>
      <dl className="handoff-import-review">
        <div><dt>本次来件</dt><dd>{preview.source_name}</dd></div>
        <div><dt>文件大小</dt><dd>{preview.source_size.toLocaleString()} 字节</dd></div>
        <div><dt>收纳为</dt><dd><code>{preview.target_path}</code></dd></div>
      </dl>
      {preview.replace_existing && <label><input type="checkbox" checked={overwrite} disabled={phase === 'collecting'} onChange={(event) => setOverwrite(event.target.checked)} />允许替换此任务已有产物</label>}
      {phase === 'collecting' && <p role="status">正在检查并收纳文件，请等待结果；这不会提交任务。</p>}
      <div className="dialog-actions">
        <button type="button" disabled={phase === 'collecting'} onClick={cancel}>取消</button>
        <button type="button" disabled={props.disabled || phase === 'collecting' || (preview.replace_existing && !overwrite)}
          onClick={() => void collect()}>{phase === 'collecting' ? '正在检查与收纳…' : '确认检查并收纳'}</button>
      </div>
    </CanvasDialog>}
  </section>
}
