/** 只展示 Python 的人工交付说明及已登记媒体信息，不推导帧数、FPS 或规划。 */

import type { ArtifactWire, ExternalHandoffContractProjectionWire, ExternalHandoffReadiness, JsonObject } from './contracts'
import { failurePresentation } from './run-presentation'

/** 只缩短显示名；复制与系统动作仍使用完整正式路径或身份引用。 */
export function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path
}

const contractLabels: Readonly<Record<string, string>> = {
  'Model name(操作者声明)': '使用模型（你声明的名称）',
  'Model version(操作者声明)': '模型版本（你声明的版本）',
  '输入 exact N': '输入精确帧数',
  '输出 exact N': '输出精确帧数',
  '输入 canonical FPS': '输入精确帧率',
  '输出 canonical FPS': '输出精确帧率',
  '输出 geometry': '输出画面尺寸',
  '输出 signal': '输出色彩信号',
  'SAR / field / rotation': '像素比例 / 扫描方式 / 旋转',
  '视频 codec / pixel format': '视频编码 / 像素格式',
  'Actual scale factor': '实际放大倍率',
}

export function HandoffContract({ contract }: { readonly contract: ExternalHandoffContractProjectionWire }) {
  return (
    <section className="handoff-contract" aria-label={contract.title}>
      <h4>{contract.title}</h4>
      <small>以下要求来自本次处理任务和实际输入，只读展示。检查与提交都会按正式要求验证。</small>
      <dl>
        {contract.fields.map((field) => <div key={field.label}><dt>{Object.hasOwn(contractLabels, field.label) ? contractLabels[field.label] : field.label}</dt><dd>{field.value}</dd></div>)}
      </dl>
    </section>
  )
}

export function ReadinessMessages({ readiness }: { readonly readiness: ExternalHandoffReadiness | null }) {
  return <>{readiness?.targets.filter((target) => target.message !== null).map((target) => (
    <section className="handoff-problem" key={`${target.port_id}-${target.ordinal ?? 'one'}`}>
      <p className="runtime-error" role="status">{fileName(target.path)}：{failurePresentation('external_submission_invalid').cause}</p>
      <p>上游结果和已有文件仍然保留。请按处理要求修正输出，再重新检查。</p>
      <details><summary>高级 → 检测原始详情</summary><pre>{target.port_id} · {target.message}</pre></details>
    </section>
  ))}</>
}

export function HandoffPrecheckFailure({ failure, resolved = false }: { readonly failure: ExternalHandoffReadiness | null; readonly resolved?: boolean }) {
  if (!failure) return null
  return (
    <section className="handoff-contract" aria-label="上次完整预检失败">
      <h4>上次完整预检失败</h4>
      <p>{resolved ? '新的完整检查已通过；以下仅保留上次失败记录，不影响本次提交。' : '上次输出未通过完整检查，未登记为可用结果。替换文件后，请重新检查输出，再提交并继续。'}</p>
      <small>已完成的上游结果仍然保留；这是上次检查记录，不代表当前文件仍然失败。</small>
      <details><summary>高级 → 上次检查原始详情</summary>
        <time dateTime={failure.checked_at}>{failure.checked_at}</time>
        {failure.targets.filter((target) => target.state !== 'probe_passed').map((target) => (
          <pre key={`${target.port_id}-${target.ordinal ?? 'one'}`}>{target.port_id} · {target.message ?? target.state}</pre>
        ))}
      </details>
    </section>
  )
}

function objectValue(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : null
}

function displayValue(value: unknown): string {
  return value === null || value === undefined ? '不可用（未登记）' : typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export function ArtifactMediaSummary({ artifact, label }: { readonly artifact: ArtifactWire; readonly label?: string }) {
  const metadata = objectValue(artifact.media_info['zniku.avenhance.v27'])
  return (
    <section className="artifact-media-summary" aria-label={`媒体信息：${label ?? fileName(artifact.path)}`}>
      <h4>媒体信息</h4>
      {metadata && <dl>
        <div><dt>精确帧数</dt><dd>{displayValue(metadata.frame_count)}</dd></div>
        <div><dt>精确帧率</dt><dd>{displayValue(metadata.frame_rate)}</dd></div>
        <div><dt>画面尺寸</dt><dd>{displayValue(metadata.geometry)}</dd></div>
        <div><dt>探测时长（秒）</dt><dd>{displayValue(metadata.duration_seconds)}</dd></div>
        <div><dt>色彩信号</dt><dd>{displayValue(metadata.signal)}</dd></div>
        <div><dt>原始音轨</dt><dd>{displayValue(metadata.audio_tracks)}</dd></div>
      </dl>}
      <details><summary>高级 → 完整媒体登记信息（只读）</summary><code>{artifact.artifact_id}</code><pre>{JSON.stringify(artifact.media_info, null, 2)}</pre></details>
    </section>
  )
}
