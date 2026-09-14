/** 验证取消通道在外部任务 UI 忙碌时仍可达，进度只属于当前 attempt。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PreparedSourceOperationMonitor } from './PreparedSourceOperationMonitor'
import { diagnosisRunId, admissionRunId, preparedSessionId, preparedOperation } from './prepared-source.test-fixtures'
import type { PreparedSourceOperationEnvelope } from './prepared-source-contracts'

afterEach(cleanup)
function props() {
  return { projectSessionId: preparedSessionId, runId: diagnosisRunId, nodeRunId: admissionRunId, unavailable: false, operationPending: true,
    inspect: vi.fn(async () => preparedOperation()),
    cancel: vi.fn(async () => {}) }
}
describe('外部素材验证监视', () => {
  it('忙碌导入仍能发送停止，当前真实进度不可误算整项完成', async () => {
    const value = props()
    render(<PreparedSourceOperationMonitor {...value} />)
    await screen.findByRole('heading', { name: '完整验证外部视频' })
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '10')
    fireEvent.click(screen.getByRole('button', { name: '停止当前检查或验证' }))
    await waitFor(() => expect(value.cancel).toHaveBeenCalledExactlyOnceWith(diagnosisRunId, admissionRunId))
    expect(screen.getByText(/以服务返回的最终状态为准/)).toBeVisible()
  })
  it('其他attempt的进度不显示，仍允许停止当前Run的忙操作', async () => {
    const value = props()
    value.inspect.mockImplementation(async () => preparedOperation({ node_run_id: diagnosisRunId }))
    render(<PreparedSourceOperationMonitor {...value} />)
    await waitFor(() => expect(value.inspect).toHaveBeenCalled())
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    expect(screen.queryByRole('heading', { name: '完整验证外部视频' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止当前检查或验证' })).toBeEnabled()
  })
  it('断线不能继续发送；本地未知进度不补百分比', () => {
    const value = props()
    render(<PreparedSourceOperationMonitor {...value} unavailable />)
    expect(value.inspect).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '停止当前检查或验证' })).toBeDisabled()
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
  })
  it('只在检查/验证时展示，不给普通人工等待虚构进度', async () => {
    const value = props()
    value.inspect.mockImplementation(async () => preparedOperation({ active: false, operation: null, stage_progress: null }))
    render(<PreparedSourceOperationMonitor {...value} operationPending={false} />)
    await waitFor(() => expect(value.inspect).toHaveBeenCalled())
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(value.cancel).not.toHaveBeenCalled()
  })
  it('切换到其他 attempt 后旧进度和旧停止响应均丢弃', async () => {
    const value = props()
    let releaseView!: (view: PreparedSourceOperationEnvelope) => void, releaseCancel!: () => void
    value.inspect.mockImplementationOnce(() => new Promise((resolve) => { releaseView = resolve }))
    value.cancel.mockImplementationOnce(() => new Promise<void>((resolve) => { releaseCancel = resolve }))
    const { rerender } = render(<PreparedSourceOperationMonitor {...value} />)
    fireEvent.click(screen.getByRole('button', { name: '停止当前检查或验证' }))
    value.inspect.mockImplementation(async () => preparedOperation({ node_run_id: diagnosisRunId, stage_progress: null }))
    rerender(<PreparedSourceOperationMonitor {...value} nodeRunId={diagnosisRunId} />)
    await act(async () => { releaseView(preparedOperation()); releaseCancel() })
    expect(screen.queryByRole('heading', { name: '完整验证外部视频' })).not.toBeInTheDocument()
    expect(screen.queryByText(/停止请求处理完毕/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '停止当前检查或验证' }))
    expect(value.cancel).toHaveBeenLastCalledWith(diagnosisRunId, diagnosisRunId)
  })
  it('其他工程相同Run/attempt不能展示；后端停止中不重复发送', async () => {
    const value = props()
    value.inspect.mockImplementation(async () => preparedOperation({ project_session_id: diagnosisRunId }))
    const { rerender } = render(<PreparedSourceOperationMonitor {...value} />)
    await waitFor(() => expect(value.inspect).toHaveBeenCalled())
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value')
    const inspect = vi.fn(async () => preparedOperation({ cancel_requested: true }))
    rerender(<PreparedSourceOperationMonitor {...value} inspect={inspect} />)
    expect(await screen.findByRole('button', { name: '已请求停止，等待收尾…' })).toBeDisabled()
  })
  it('即使本地复制仍忙，后端明确idle也不展示可取消的伪操作', async () => {
    const value = props()
    value.inspect.mockImplementation(async () => preparedOperation({ active: false, operation: null, stage_progress: null }))
    render(<PreparedSourceOperationMonitor {...value} />)
    await waitFor(() => expect(screen.queryByRole('progressbar')).not.toBeInTheDocument())
  })
})
