import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MediaPreview, type PreviewCandidate } from './MediaPreview'
import type { HostBridge, MediaPreviewEnvelope, MediaPreviewRequest } from '../host-bridge'

const candidates: PreviewCandidate[] = [
  { id: 'input', label: '输入 · before.mkv', side: 'input', reference: { kind: 'artifact', run_id: 'run', artifact_id: 'input' } },
  { id: 'output', label: '输出 · after.mkv', side: 'output', reference: { kind: 'artifact', run_id: 'run', artifact_id: 'output' } },
]
const image = 'data:image/png;base64,iVBORw0KGgo='
afterEach(cleanup)
function frame(request: MediaPreviewRequest): MediaPreviewEnvelope {
  return { ...request, image_data_url: image, width: 160, height: 90, cache_hit: false }
}
function bridge(preview = vi.fn(async (request: MediaPreviewRequest) => frame(request))): HostBridge {
  return { configured: true, inspectCapabilities: vi.fn(), pick: vi.fn(), launch: vi.fn(), preview }
}

describe('只读媒体静帧', () => {
  it('只在显式点击后读取A/B，不启动播放器、提交或自动请求', async () => {
    const host = bridge()
    render(<MediaPreview hostBridge={host} projectSessionId="session" candidates={candidates} />)
    fireEvent.click(screen.getByText('画面预览与前后比较'))
    expect(host.preview).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '加载画面' }))
    await waitFor(() => expect(screen.getAllByRole('img')).toHaveLength(2))
    expect(host.preview).toHaveBeenCalledTimes(2)
    expect(host.launch).not.toHaveBeenCalled()
    expect(screen.getByText(/A\/B 不保证同一时间点/)).toBeInTheDocument()
  })

  it('切换比较引用立即清空旧图，迟到响应不能重新显示', async () => {
    let resolve!: (value: MediaPreviewEnvelope) => void
    const preview = vi.fn((_request: MediaPreviewRequest) => new Promise<MediaPreviewEnvelope>((done) => { resolve = done }))
    render(<MediaPreview hostBridge={bridge(preview)} projectSessionId="session" candidates={candidates} />)
    fireEvent.click(screen.getByText('画面预览与前后比较'))
    fireEvent.click(screen.getByRole('button', { name: '加载画面' }))
    fireEvent.change(screen.getByLabelText('B 对比画面'), { target: { value: '' } })
    await act(async () => resolve(frame({ contract_version: '0.3.0', project_session_id: 'session', reference: candidates[0]!.reference })))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(preview).toHaveBeenCalledTimes(1)
  })

  it('卸载后不会继续请求B；不可用和失败均提供恢复说明', async () => {
    let resolve!: (value: MediaPreviewEnvelope) => void
    const preview = vi.fn((_request: MediaPreviewRequest) => new Promise<MediaPreviewEnvelope>((done) => { resolve = done }))
    const view = render(<MediaPreview hostBridge={bridge(preview)} projectSessionId="session" candidates={candidates} />)
    fireEvent.click(screen.getByText('画面预览与前后比较'))
    fireEvent.click(screen.getByRole('button', { name: '加载画面' }))
    view.unmount()
    await act(async () => resolve(frame({ contract_version: '0.3.0', project_session_id: 'session', reference: candidates[0]!.reference })))
    expect(preview).toHaveBeenCalledTimes(1)
    render(<MediaPreview hostBridge={{ ...bridge(), configured: false }} projectSessionId="other" candidates={candidates} />)
    fireEvent.click(screen.getByText('画面预览与前后比较'))
    expect(screen.getByRole('button', { name: '加载画面' })).toBeDisabled()
    expect(screen.getByText(/本机预览暂不可用/)).toBeInTheDocument()
  })

  it('坏媒体错误不展示半套A/B，也不泄漏默认诊断', async () => {
    const preview = vi.fn(async (request: MediaPreviewRequest) => frame(request))
      .mockImplementationOnce(async (request) => frame(request)).mockRejectedValueOnce(new Error('E_PRIVATE_PATH: private'))
    render(<MediaPreview hostBridge={bridge(preview)} projectSessionId="session" candidates={candidates} />)
    fireEvent.click(screen.getByText('画面预览与前后比较'))
    fireEvent.click(screen.getByRole('button', { name: '加载画面' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('无法读取画面'))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByText(/E_PRIVATE_PATH/)).not.toBeInTheDocument()
  })
})
