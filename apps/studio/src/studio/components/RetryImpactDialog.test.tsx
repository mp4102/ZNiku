/** 重试清单完全来自服务；没有成功预检不能确认，也不能自动发起命令。 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RerunPreviewEnvelope } from '../contracts'
import { RetryImpactDialog } from './RetryImpactDialog'

afterEach(cleanup)
const preview: RerunPreviewEnvelope = { contract_version: '0.3.0', project_session_id: 'synthetic-session', storage_revision: 3,
  run_id: 'synthetic-run', node_id: 'enhance', mode: 'new_run', rerun_node_ids: ['output', 'enhance'], reusable_node_ids: ['source'], projected_at: '2026-09-05T00:00:00Z' }
const labels = (id: string) => ({ output: '输出成片', enhance: '画质增强', source: '导入视频' })[id] ?? id
describe('重试影响确认', () => {
  it('不推导节点集合或顺序，只呈现服务清单并等待明确确认', () => {
    const confirm = vi.fn()
    render(<RetryImpactDialog preview={preview} nodeLabel={labels} busy={false} error={null} onConfirm={confirm} onCancel={vi.fn()} />)
    expect(within(screen.getByRole('region', { name: '将重新处理的步骤' })).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['输出成片', '画质增强'])
    expect(screen.getByText('导入视频')).toBeInTheDocument()
    expect(confirm).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).not.toHaveTextContent(/synthetic-run|storage_revision/)
    fireEvent.click(screen.getByRole('button', { name: '确认从头重新处理' }))
    expect(confirm).toHaveBeenCalledTimes(1)
  })
  it.each([{ busy: true, error: null, preview }, { busy: false, error: '清单已过期', preview }, { busy: false, error: null, preview: null }])('等待、失败或无清单均禁止确认', (state) => {
    const confirm = vi.fn()
    render(<RetryImpactDialog {...state} nodeLabel={labels} onConfirm={confirm} onCancel={vi.fn()} />)
    const button = screen.getByRole('button', { name: '确认从头重新处理' })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(confirm).not.toHaveBeenCalled()
  })
  it('Escape 取消，且取消不确认', () => {
    const cancel = vi.fn(); const confirm = vi.fn()
    render(<RetryImpactDialog preview={preview} nodeLabel={labels} busy={false} error={null} onConfirm={confirm} onCancel={cancel} />)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(cancel).toHaveBeenCalledTimes(1)
    expect(confirm).not.toHaveBeenCalled()
  })
})
