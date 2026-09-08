/** 显式加载绑定媒体的代表帧；只保存两张 session 图片，不参与检查、提交或运行资格。 */

import { useEffect, useRef, useState } from 'react'
import type { HostBridge, HostPathReference, MediaPreviewEnvelope } from '../host-bridge'

export interface PreviewCandidate {
  readonly id: string
  readonly label: string
  readonly reference: HostPathReference
  readonly side: 'input' | 'output'
}

interface MediaPreviewProps {
  readonly hostBridge: HostBridge
  readonly projectSessionId: string
  readonly candidates: ReadonlyArray<PreviewCandidate>
  readonly disabled?: boolean
  readonly advanced?: boolean
}

export function MediaPreview({ hostBridge, projectSessionId, candidates, disabled, advanced }: MediaPreviewProps) {
  const [leftId, setLeftId] = useState(candidates.find((item) => item.side === 'input')?.id ?? candidates[0]?.id ?? '')
  const [rightId, setRightId] = useState(candidates.find((item) => item.side === 'output')?.id ?? '')
  const [frames, setFrames] = useState<ReadonlyArray<{ readonly id: string; readonly frame: MediaPreviewEnvelope }>>([])
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const sequence = useRef(0)
  // 切节点/Run/工程由父级 key 卸载；旧请求不能在新的 Inspector 中显示或泄露旧图。
  useEffect(() => () => { sequence.current += 1 }, [])
  if (!candidates.length) return null
  const unavailable = disabled || !hostBridge.configured || !hostBridge.preview
  function select(side: 'left' | 'right', id: string) {
    sequence.current += 1
    setLoading(false)
    setFrames([])
    setFailure(null)
    if (side === 'left') setLeftId(id)
    else setRightId(id)
  }
  async function load() {
    if (unavailable || !hostBridge.preview || loading) return
    const selected = [...new Set([leftId, rightId].filter(Boolean))]
      .flatMap((id) => candidates.find((item) => item.id === id) ?? [])
    const requestSequence = ++sequence.current
    setLoading(true)
    setFailure(null)
    setFrames([])
    try {
      // 依次解码，避免 A/B 抢占有限 preview worker；两张都成功才显示这次比较。
      const result: Array<{ readonly id: string; readonly frame: MediaPreviewEnvelope }> = []
      for (const candidate of selected) {
        const frame = await hostBridge.preview({ contract_version: '0.3.0', project_session_id: projectSessionId, reference: candidate.reference })
        if (sequence.current !== requestSequence) return
        result.push({ id: candidate.id, frame })
      }
      setFrames(result)
    } catch (error) {
      if (sequence.current === requestSequence) setFailure(error instanceof Error ? error.message : '媒体暂时无法预览。')
    } finally {
      if (sequence.current === requestSequence) setLoading(false)
    }
  }
  return <section className="media-preview" aria-label="媒体静帧预览">
    <details>
      <summary>画面预览与前后比较</summary>
      <p>每个文件的首个可解码视频帧，缩小后显示。A/B 不保证同一时间点；预览不代表画质或产物检查通过。</p>
      <div className="preview-selectors">
        <label>A · 输入或参考画面<select aria-label="A 参考画面" value={leftId} onChange={(event) => select('left', event.target.value)}>
          {candidates.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select></label>
        <label>B · 处理后画面<select aria-label="B 对比画面" value={rightId} onChange={(event) => select('right', event.target.value)}>
          <option value="">不比较，只看 A</option>
          {candidates.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select></label>
      </div>
      <button type="button" className="button button--ghost" disabled={unavailable || loading} onClick={() => void load()}>{loading ? '正在读取代表帧…' : frames.length ? '刷新画面' : '加载画面'}</button>
      {unavailable && <p>本机预览暂不可用，请使用桌面入口启动并恢复服务连接。已有文件与处理结果不受影响。</p>}
      <p role="status">{loading ? '仅生成小尺寸静帧，不在浏览器中载入整段视频。' : frames.length ? '静帧已就绪；正式检查仍在外部处理助手中完成。' : ''}</p>
      {failure && <div role="alert"><p>无法读取画面。文件可能尚未就绪、不含视频或已变化；请检查文件后重新加载。预览失败不会改变处理结果。</p>{advanced && <details><summary>高级 → 预览详情</summary><pre>{failure}</pre></details>}</div>}
      <div className="preview-frames">
        {([['A', leftId], ['B', rightId]] as const).map(([side, id]) => {
          const item = frames.find((entry) => entry.id === id)
          const candidate = candidates.find((entry) => entry.id === id)
          return item && candidate ? <figure key={side}><img src={item.frame.image_data_url} width={item.frame.width} height={item.frame.height} alt={`${side} · ${candidate.label}的代表帧`} /><figcaption>{side} · {candidate.label}</figcaption></figure> : null
        })}
      </div>
    </details>
  </section>
}
