/** 只展示 Python 的人工交付说明及已登记媒体信息，不推导帧数、FPS 或规划。 */

import { useState } from 'react'
import type { ArtifactWire, ExternalHandoffContractProjectionWire, ExternalHandoffReadiness, JsonObject } from './contracts'
import { failurePresentation } from './run-presentation'

/** 只缩短显示名；复制与系统动作仍使用完整正式路径或身份引用。 */
export function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path
}

/** 只展示已知校验器的说明；不解析数值、不推导 N，也不决定是否可提交。 */
export function formatHandoffValidationMessage(message: string | null): string | null {
  const match = message?.match(/^(?:E_[A-Z0-9_]+: )*E_AV27_ENHANCEMENT_FRAME_COUNT: (.+)$/u)
  if (!match) return null
  return match[1] === 'Enhancement 输出不满足 N -> N'
    ? '增强结果与所选任务要求的帧数不一致。请确认文件是否对应这个分段，再重新检查。'
    : match[1]!
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
      <p className="runtime-error" role="status">{fileName(target.path)}：{formatHandoffValidationMessage(target.message) ?? failurePresentation('external_submission_invalid').cause}</p>
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
      {failure.targets.filter((target) => target.state !== 'probe_passed' && formatHandoffValidationMessage(target.message)).map((target) => (
        <p key={`${target.port_id}-${target.ordinal ?? 'one'}`}>上次原因：{formatHandoffValidationMessage(target.message)}</p>
      ))}
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

/** 仅对服务端已返回的纯数据做缩进展示与显式复制，不读取磁盘或改写报告。 */
export function ReadOnlyJson({ value, copyLabel }: { readonly value: JsonObject; readonly copyLabel: string }) {
  const [notice, setNotice] = useState<string | null>(null)
  const text = JSON.stringify(value, null, 2)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setNotice('JSON 已复制。')
    } catch {
      setNotice('无法复制，请在下方只读文本中手动选择并复制。')
    }
  }
  return <><button type="button" onClick={() => void copy()}>{copyLabel}</button>
    {notice && <p role="status">{notice}</p>}<pre>{text}</pre></>
}

function displayValue(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : '不可用（未登记）'
}

const signalLabels: Readonly<Record<string, string>> = {
  color_range: '色彩范围', color_space: '色彩空间', color_transfer: '传递特性', color_primaries: '色彩原色',
  chroma_location: '色度位置', sample_aspect_ratio: '像素比例', field_order: '扫描方式', rotation: '旋转角度',
}

function readableSignal(value: unknown): string {
  const signal = objectValue(value)
  if (!signal) return displayValue(value)
  return Object.entries(signalLabels).filter(([key]) => signal[key] !== undefined && signal[key] !== null)
    .map(([key, label]) => `${label}：${signal[key] === 'progressive' ? '逐行' : displayValue(signal[key])}`).join('；') || '不可用（未登记）'
}

function readableGeometry(value: unknown): string {
  const geometry = objectValue(value)
  return geometry ? `${displayValue(geometry.width)} × ${displayValue(geometry.height)}` : displayValue(value)
}

function AudioTracks({ value }: { readonly value: unknown }) {
  if (!Array.isArray(value)) return <span>不可用（未登记）</span>
  if (!value.length) return <span>无音轨（已登记）</span>
  return <ul>{value.map((item, index) => {
    const track = objectValue(item)
    const fields = track ? [
      track.codec === undefined ? null : `编码 ${displayValue(track.codec)}`,
      track.channels === undefined || track.channels === null ? null : `${displayValue(track.channels)} 声道`,
      track.sample_rate === undefined || track.sample_rate === null ? null : `${displayValue(track.sample_rate)} Hz`,
      typeof track.language === 'string' ? `语言 ${track.language}` : null,
      typeof track.title === 'string' ? track.title : null,
    ].filter(Boolean) : []
    return <li key={index}>音轨 {index + 1}：{fields.join(' · ') || '详情未登记'}</li>
  })}</ul>
}

export function ArtifactMediaSummary({ artifact, label }: { readonly artifact: ArtifactWire; readonly label?: string }) {
  const admission = objectValue(artifact.media_info['zniku.source.admission'])
  const overlap = objectValue(artifact.media_info['zniku.source.admitted']) ?? objectValue(artifact.media_info['zniku.source.aligned']) ?? objectValue(artifact.media_info['zniku.chapter.overlap'])
  const metadata = overlap ?? objectValue(artifact.media_info['zniku.avenhance.v27'])
  const source = objectValue(metadata?.source)
  const roles: Readonly<Record<string, string>> = { external: '已验收外部前处理视频', context: '包含邻章上下文的补帧输入', fi: '外部原始补帧结果（保留）', crop: '精确裁边后正式章节', split: '正式章内处理段', enhancement: '已增强处理段', merge: '章内合并增强视频', program: '连续编码视频', final: '最终封装媒体' }
  const role = typeof overlap?.role === 'string' && Object.hasOwn(roles, overlap.role) ? roles[overlap.role] : null
  const fiProfile = objectValue(overlap?.fi_profile)
  return (
    <section className="artifact-media-summary" aria-label={`媒体信息：${label ?? fileName(artifact.path)}`}>
      <h4>媒体信息</h4>
      {metadata && <dl>
        {role && <div><dt>产物角色</dt><dd>{role}</dd></div>}
        {fiProfile && <div><dt>补帧候选声明</dt><dd>{displayValue(fiProfile.model_name)} · 软件 {displayValue(fiProfile.software_version)} · {fiProfile.status === 'pending_real_acceptance' ? '待真实验收' : displayValue(fiProfile.status)}</dd></div>}
        <div><dt>{metadata.frame_count === undefined && source ? '绑定源帧数' : '精确帧数'}</dt><dd>{displayValue(metadata.frame_count ?? source?.frame_count)}</dd></div>
        <div><dt>{metadata.frame_rate === undefined && source ? '绑定源帧率' : '精确帧率'}</dt><dd>{displayValue(metadata.frame_rate ?? source?.frame_rate)}</dd></div>
        <div><dt>画面尺寸</dt><dd>{readableGeometry(metadata.geometry)}</dd></div>
        {!overlap && <div><dt>探测时长（秒）</dt><dd>{displayValue(metadata.duration_seconds)}</dd></div>}
        <div><dt>色彩信号</dt><dd>{readableSignal(metadata.signal)}</dd></div>
        {!overlap && <div><dt>原始音轨</dt><dd><AudioTracks value={metadata.audio_tracks} /></dd></div>}
      </dl>}
      {admission && <dl><div><dt>源准入合同</dt><dd>{displayValue(admission.source_contract)}</dd></div></dl>}
      {Array.isArray(admission?.warnings) && admission.warnings.length > 0 && <details><summary>素材分析提示</summary>
        <ul>{admission.warnings.filter((item): item is string => typeof item === 'string').map((warning, index) => <li key={index}>{warning}</li>)}</ul>
      </details>}
      {!metadata && <p>尚无可展示的媒体摘要；不会由文件名推测媒体属性。</p>}
      <details><summary>高级 → 完整媒体登记信息（只读）</summary><code>{artifact.artifact_id}</code>
        <ReadOnlyJson value={artifact.media_info} copyLabel="复制媒体登记 JSON" />
      </details>
    </section>
  )
}
