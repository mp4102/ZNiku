/** 稳定/未知错误都保留高级原文；定位不产生重试或运行副作用。 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DiagnosticsPanel, type DiagnosticsPanelProps } from './DiagnosticsPanel'

afterEach(cleanup)
function props(overrides: Partial<DiagnosticsPanelProps> = {}): DiagnosticsPanelProps {
  return { open: true, diagnostics: [], serviceError: null, hasOlderRuns: false, historyBusy: false,
    onToggle: vi.fn(), onLocateNode: vi.fn(), onLocateEdge: vi.fn(), onLoadOlderRuns: vi.fn(), ...overrides }
}
describe('问题与恢复卡片', () => {
  it('缺失输入给原因、保留和下一步，并定位 exact 节点', () => {
    const options = props({ diagnostics: [{ code: 'E_REQUIRED_INPUT_MISSING', message: 'raw diagnostic', node_id: 'node-1', edge_id: null }], nodeLabel: () => '画质增强' })
    const { container } = render(<DiagnosticsPanel {...options} />)
    expect(screen.getByText('还缺少输入连接')).toBeInTheDocument()
    expect(screen.getByText('保留的内容')).toBeInTheDocument()
    expect(screen.getByText('下一步')).toBeInTheDocument()
    expect(container.querySelector('details')).not.toHaveAttribute('open')
    fireEvent.click(screen.getByRole('button', { name: '定位步骤与设置' }))
    expect(options.onLocateNode).toHaveBeenCalledWith('node-1')
  })
  it('未知错误保持保守解释且原始信息以纯文本完整保留', () => {
    const raw = '<script>notExecutable()</script> original detail'
    const { container } = render(<DiagnosticsPanel {...props({ runtimeProblems: [{ code: 'E_UNKNOWN_PLUGIN', message: raw, node_id: 'node-x' }] })} />)
    expect(screen.getByText('出现尚未识别的问题')).toBeInTheDocument()
    expect(screen.getByText('高级详情（保留原始错误）')).toBeInTheDocument()
    expect(container.querySelector('pre')).toHaveTextContent(raw)
    expect(container.querySelector('script')).toBeNull()
  })
  it('高级层显示原始 code/message，缺少定位目标不提供无效按钮', () => {
    render(<DiagnosticsPanel {...props({ advanced: true, serviceError: { code: 'E_PROJECT_SERVICE_BUSY', message: 'exact busy', related_run_ids: [] } })} />)
    expect(screen.getByText('E_PROJECT_SERVICE_BUSY')).toBeVisible()
    expect(screen.getByText('exact busy')).toBeVisible()
    expect(screen.queryByRole('button', { name: /定位/ })).not.toBeInTheDocument()
  })
  it('运行问题使用历史定位 callback，不能误定位当前 Graph', () => {
    const current = vi.fn(); const historical = vi.fn()
    render(<DiagnosticsPanel {...props({ runtimeProblems: [{ code: 'interrupted', message: 'synthetic', node_id: 'old-node' }], onLocateNode: current, onLocateRuntimeNode: historical })} />)
    fireEvent.click(screen.getByRole('button', { name: '定位步骤与设置' }))
    expect(historical).toHaveBeenCalledWith('old-node')
    expect(current).not.toHaveBeenCalled()
  })
})
