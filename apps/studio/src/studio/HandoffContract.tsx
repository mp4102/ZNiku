/** 只展示 Python 的人工交付说明及已登记媒体信息，不推导帧数、FPS 或规划。 */

import type { ArtifactWire, ExternalHandoffContractProjectionWire, ExternalHandoffReadiness, JsonObject } from './contracts'

export function HandoffContract({ contract }: { readonly contract: ExternalHandoffContractProjectionWire }) {
  return (
    <section className="handoff-contract" aria-label={contract.title}>
      <h4>{contract.title}</h4>
      <small>原 Run snapshot + input Artifact；只读展示，Submit 仍重新验证。</small>
      <dl>
        {contract.fields.map((field) => <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}
      </dl>
    </section>
  )
}

export function ReadinessMessages({ readiness }: { readonly readiness: ExternalHandoffReadiness | null }) {
  return <>{readiness?.targets.filter((target) => target.message !== null).map((target) => (
    <p className="runtime-error" role="status" key={`${target.port_id}-${target.ordinal ?? 'one'}`}>
      {target.port_id} · {target.message}
    </p>
  ))}</>
}

export function HandoffPrecheckFailure({ failure }: { readonly failure: ExternalHandoffReadiness | null }) {
  if (!failure) return null
  return (
    <section className="handoff-contract" aria-label="上次完整预检失败">
      <h4>上次完整预检失败</h4>
      <time dateTime={failure.checked_at}>{failure.checked_at}</time>
      {failure.targets.filter((target) => target.state !== 'probe_passed').map((target) => (
        <p className="runtime-error" key={`${target.port_id}-${target.ordinal ?? 'one'}`}>
          {target.port_id} · {target.message ?? target.state}
        </p>
      ))}
      <small>这是此 handoff 上次完整检查的结果，不代表当前文件仍然失败；修改输出后可重新 Validate and submit。</small>
    </section>
  )
}

function objectValue(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : null
}

function displayValue(value: unknown): string {
  return value === null || value === undefined ? '不可用（未登记）' : typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export function ArtifactMediaSummary({ artifact }: { readonly artifact: ArtifactWire }) {
  const metadata = objectValue(artifact.media_info['zniku.avenhance.v27'])
  return (
    <section className="artifact-media-summary" aria-label={`Artifact media ${artifact.producer_port_id}`}>
      <h4>已登记媒体 · {artifact.producer_port_id}</h4>
      <code>{artifact.artifact_id}</code>
      {metadata && <dl>
        <div><dt>Exact N</dt><dd>{displayValue(metadata.frame_count)}</dd></div>
        <div><dt>Canonical rational FPS</dt><dd>{displayValue(metadata.frame_rate)}</dd></div>
        <div><dt>Geometry</dt><dd>{displayValue(metadata.geometry)}</dd></div>
        <div><dt>Observed duration (seconds)</dt><dd>{displayValue(metadata.duration_seconds)}</dd></div>
        <div><dt>Signal</dt><dd>{displayValue(metadata.signal)}</dd></div>
        <div><dt>Original audio tracks</dt><dd>{displayValue(metadata.audio_tracks)}</dd></div>
      </dl>}
      <details><summary>完整 media_info（只读）</summary><pre>{JSON.stringify(artifact.media_info, null, 2)}</pre></details>
    </section>
  )
}
