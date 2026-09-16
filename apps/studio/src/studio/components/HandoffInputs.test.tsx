/** 合成输入证明报告与视频分层、JSON只读复制和身份引用隔离，不执行媒体I/O。 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ArtifactInputReportWire } from '../contracts'
import { handoffDetailEnvelope } from '../test-fixtures'
import { HandoffInputs } from './HandoffInputs'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const detail = handoffDetailEnvelope()
const video = { ...detail.artifacts[0]!, path: 'D:\\synthetic\\example.repaired.mp4', media_info: {
  'zniku.avenhance.v27': { frame_count: 902, frame_rate: '30000/1001', geometry: { width: 1920, height: 1080 },
    signal: { color_space: 'bt709', field_order: 'progressive' }, audio_tracks: [{ codec: 'aac', channels: 2, sample_rate: 48000 }] },
  'zniku.source.admission': { source_contract: 'AVEnhanceFlow-2.7.0', warnings: ['合成的已登记提示'] },
} }
const data = { ...video, artifact_id: 'synthetic-report', kind: 'DataFile', path: 'D:\\synthetic\\admission.json', media_info: {} }
const nodeRun = { ...detail.run.node_runs.find((item) => item.external_handoff)!,
  external_handoff: { ...detail.run.node_runs.find((item) => item.external_handoff)!.external_handoff!,
    input_artifact_ids: [data.artifact_id, video.artifact_id] },
}
const report: ArtifactInputReportWire = { artifact_id: data.artifact_id, title: '素材分析报告',
  fields: [{ label: '分析结论', value: '已登记的源准入报告' }, { label: '素材数量', value: '1' }],
  document: { schema: 'synthetic.report/1', sources: [{ frame_count: 902 }] }, message: null,
}
function fixture() {
  return { nodeRun, artifactsById: new Map([data, video].map((item) => [item.artifact_id, item])), reports: [report],
    mutationBlocked: false, canRevealHandoff: true, canOpenHandoffInput: true, onCopyPath: vi.fn(), onLaunchHandoff: vi.fn() }
}

describe('外部处理输入的用户可读展示', () => {
  it('视频主区先展示并可打开，报告默认折叠且没有播放器按钮', () => {
    const props = fixture()
    render(<HandoffInputs {...props} />)
    const media = screen.getByRole('region', { name: '待处理视频：example.repaired.mp4' })
    expect(within(media).getByText('1920 × 1080')).toBeVisible()
    expect(within(media).getByText('30000/1001')).toBeVisible()
    expect(within(media).getByText('902')).toBeVisible()
    expect(within(media).getByText(/音轨 1：编码 aac · 2 声道 · 48000 Hz/)).toBeVisible()
    expect(within(media).getByText('AVEnhanceFlow-2.7.0')).toBeVisible()
    expect(screen.getByText('素材分析报告')).toBeVisible()
    expect(screen.getByText('admission.json')).not.toBeVisible()
    expect(screen.getAllByRole('button', { name: '打开输入' })).toHaveLength(1)
    fireEvent.click(within(media).getByRole('button', { name: '打开输入' }))
    expect(props.onLaunchHandoff).toHaveBeenCalledExactlyOnceWith(nodeRun, 'open_with_system_player', { role: 'input_artifact', artifact_id: video.artifact_id })
    fireEvent.click(screen.getByText('素材分析报告'))
    expect(screen.getByText('已登记的源准入报告')).toBeVisible()
    expect(screen.getByText(/不需要送入外部视频工具/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '显示报告位置' }))
    expect(props.onLaunchHandoff).toHaveBeenLastCalledWith(nodeRun, 'reveal_in_file_manager', { role: 'input_artifact', artifact_id: data.artifact_id })
    expect(props.onCopyPath).not.toHaveBeenCalled()
  })

  it('完整JSON仅格式化服务端投影，显式复制保留对象和值且不执行HTML', async () => {
    const writeText = vi.fn(async () => undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const value = { ...report, document: { ...report.document, text: '<img src=x onerror=alert(1)>' } }
    render(<HandoffInputs {...fixture()} reports={[value]} />)
    fireEvent.click(screen.getByText('素材分析报告'))
    fireEvent.click(screen.getByText('高级 → 完整报告 JSON（只读）'))
    const raw = screen.getByText(/"synthetic.report\/1"/)
    expect(raw.tagName).toBe('PRE')
    expect(raw.textContent).toBe(JSON.stringify(value.document, null, 2))
    expect(document.querySelector('img')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '复制报告 JSON' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('JSON 已复制。'))
    expect(writeText).toHaveBeenCalledExactlyOnceWith(JSON.stringify(value.document, null, 2))
    expect(value.document.text).toBe('<img src=x onerror=alert(1)>')
  })

  it('只匹配当前输入的报告身份，缺失与文件变化不给出伪报告或播放器', () => {
    const props = fixture()
    const { rerender } = render(<HandoffInputs {...props} reports={[{ ...report, artifact_id: 'other-report' }]} />)
    fireEvent.click(screen.getByText('分析 / 参考报告'))
    expect(screen.getByText('当前报告摘要不可用；原文件仍然保留。')).toBeVisible()
    expect(screen.queryByText('素材分析报告')).not.toBeInTheDocument()
    rerender(<HandoffInputs {...props} reports={[{ ...report, document: null, fields: [], message: '报告文件已变化，请重新分析。' }]} />)
    expect(screen.getByText('报告文件已变化，请重新分析。')).toBeVisible()
    expect(screen.queryByText('高级 → 完整报告 JSON（只读）')).not.toBeInTheDocument()
  })

  it('未知Artifact kind即使扩展名mp4也不当作媒体，缺少系统能力明确禁用', () => {
    const unknown = { ...video, kind: 'CustomPayload' }
    render(<HandoffInputs {...fixture()} artifactsById={new Map([[unknown.artifact_id, unknown], [data.artifact_id, data]])} canRevealHandoff={false} />)
    expect(screen.getByText('参考输入')).toBeVisible()
    expect(screen.queryByRole('button', { name: '打开输入' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '显示输入位置' })).toBeDisabled()
  })

  it('浏览器拒绝剪贴板时给出手动复制方案，不冒充复制成功', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn(async () => { throw new Error('denied') }) } })
    render(<HandoffInputs {...fixture()} />)
    fireEvent.click(screen.getByText('素材分析报告'))
    fireEvent.click(screen.getByText('高级 → 完整报告 JSON（只读）'))
    fireEvent.click(screen.getByRole('button', { name: '复制报告 JSON' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('无法复制'))
  })
})
