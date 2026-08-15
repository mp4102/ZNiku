import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  FetchRealMediaGateway,
  type RealHostCommand,
  type RealHostEnvelope,
  type RealMediaGateway,
} from './real-media'

interface Props {
  readonly gateway?: RealMediaGateway
}

export function RealMediaAcceptanceView({ gateway }: Props) {
  const effectiveGateway = useMemo(() => gateway ?? new FetchRealMediaGateway(), [gateway])
  const [envelope, setEnvelope] = useState<RealHostEnvelope | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setEnvelope(await effectiveGateway.inspect())
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Runtime host 读取失败')
    }
  }, [effectiveGateway])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 2_000)
    return () => window.clearInterval(timer)
  }, [refresh])

  const command = async (value: RealHostCommand) => {
    if (busy) return
    setBusy(true)
    try {
      setEnvelope(await effectiveGateway.command(value))
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Runtime command 失败')
    } finally {
      setBusy(false)
    }
  }

  const projection = envelope?.projection ?? null
  const counts = new Map<string, number>()
  for (const node of projection?.nodes ?? []) counts.set(node.state, (counts.get(node.state) ?? 0) + 1)
  const readyManual = projection?.nodes.filter(
    (node) => node.state === 'ready' && node.execution_mode === 'manual_external',
  ) ?? []
  const failed = projection?.nodes.filter((node) => node.state === 'failed') ?? []

  return (
    <div className="authority-view real-media-view" aria-label="Real Media Acceptance Candidate">
      <header className="authority-view__header">
        <div>
          <span className="eyebrow">FRESH LOCAL RUNTIME AUTHORITY</span>
          <h2>0.1.0 Real Media Acceptance Candidate</h2>
        </div>
        <button className="button button--ghost" type="button" disabled={busy} onClick={() => void refresh()}>
          刷新
        </button>
      </header>

      {error && <div className="authority-empty" role="alert"><strong>RUNTIME HOST UNAVAILABLE</strong><p>{error}</p><p>Studio 不会回退静态状态或浏览器内媒体执行。</p></div>}

      {!error && envelope && !envelope.candidate_exists && (
        <div className="real-media-start">
          <p>参考源和工作根由 loopback host 启动参数固定；浏览器不会提交本机路径。</p>
          <button className="button button--primary" type="button" disabled={busy} onClick={() => void command({ operation: 'start' })}>
            启动真实媒体验收候选
          </button>
        </div>
      )}

      {projection && (
        <>
          <div className="authority-metrics">
            {['complete', 'ready', 'blocked', 'failed'].map((state) => (
              <span key={state} className={`state-metric state-metric--${state}`}><strong>{counts.get(state) ?? 0}</strong> {state}</span>
            ))}
            <span><strong>{projection.evidence_count}</strong> Evidence</span>
            <span><strong>{projection.artifact_count}</strong> Artifacts</span>
          </div>
          <section className="audio-authority-card">
            <span className="eyebrow">READ-ONLY SOURCE AUTHORITY</span>
            <strong>{projection.reference_filename}</strong>
            <p>第 {projection.chapter_split_source_second} 秒切分 · {projection.chapters.map((item) => `${item.start_frame}–${item.end_frame}`).join(' / ')} frames</p>
            <code>{projection.reference_digest}</code>
          </section>
          <div className="real-media-actions">
            <button className="button button--primary" type="button" disabled={busy || projection.final_verified} onClick={() => void command({ operation: 'advance_automatic' })}>
              执行全部 ready 自动节点
            </button>
            {readyManual.map((node) => (
              <button className="button button--primary" type="button" disabled={busy} key={node.plan_node_id} onClick={() => void command({ operation: 'acceptance_fixture', plan_node_id: node.plan_node_id })}>
                生成并验收 fixture · {node.plan_node_id}
              </button>
            ))}
            {failed.map((node) => (
              <button className="button button--ghost" type="button" disabled={busy} key={node.plan_node_id} onClick={() => void command({ operation: 'retry', plan_node_id: node.plan_node_id })}>
                显式 retry · {node.plan_node_id}
              </button>
            ))}
          </div>
          <div className="run-list">
            {projection.nodes.map((node) => (
              <article key={node.plan_node_id} className={`run-row run-row--${node.state}`}>
                <i aria-hidden="true" />
                <div><strong>{node.plan_node_id}</strong><span>{node.execution_mode} · attempt {node.attempt}</span></div>
                <em>{node.state}</em>
                <code>{node.evidence_id ?? '等待 Runtime Evidence'}</code>
              </article>
            ))}
          </div>
          {projection.final_verified && (
            <footer className="authority-digest real-media-final">
              <span>FULL VERIFIED · ORIGINAL AUDIO BITSTREAM PRESERVED</span>
              <code>{projection.final_digest}</code>
            </footer>
          )}
        </>
      )}
    </div>
  )
}
