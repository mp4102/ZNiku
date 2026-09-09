/** 节点卡状态只投影正式字段；外部节点即使收到错误进度数据也不展示百分比。 */
import { act, cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { NodeProps } from '@xyflow/react'
import type { HTMLAttributes } from 'react'
import type { WorkflowNode, WorkflowNodeData } from '../model'
import type { PortSpecWire } from '../studio/contracts'
import { handoffRun } from '../studio/test-fixtures'
import { WorkflowNodeCard } from './WorkflowNodeCard'

const updateNodeInternals = vi.hoisted(() => vi.fn())
vi.mock('@xyflow/react', () => ({ Position: { Left: 'left', Right: 'right' },
  useUpdateNodeInternals: () => updateNodeInternals,
  Handle: ({ id, type, position, ...attributes }: HTMLAttributes<HTMLDivElement> & { id: string; type: string; position: string }) =>
    <div data-handle-id={id} data-handle-type={type} data-handle-position={position} {...attributes} />,
}))
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals() })
function props(overrides: Partial<WorkflowNodeData> = {}): NodeProps<WorkflowNode> {
  const data: WorkflowNodeData = { label: '画质增强', instanceId: 'technical-node-id', typeId: 'private.type', definitionVersion: '0.2.0', executorKind: 'python',
    inputs: [], outputs: [], summaries: [], nodeRun: { ...handoffRun().node_runs[0]!, reused_from_result_id: 'synthetic-result' },
    latestResult: { node_id: 'technical-node-id', result_id: 'synthetic-result', stale: true, stale_reason: 'graph_changed', updated_at: '2026-09-05T00:00:00Z' },
    progress: { mode: 'determinate', fraction: .6, measurement: null, elapsed: '已等待 1 分钟' }, advanced: false, ...overrides }
  return { id: 'technical-node-id', data, selected: false } as NodeProps<WorkflowNode>
}
function ports(count: number, direction: 'input' | 'output'): PortSpecWire[] {
  return Array.from({ length: count }, (_, index) => ({ port_id: `${direction}-${index}`, data_type: 'Video',
    required: direction === 'input', cardinality: 'one' }))
}
describe('节点运行状态展示', () => {
  it('参数摘要保留独立列表项，创作者耗时使用中文；高级仍保留原投影', () => {
    const progress = { mode: 'none' as const, fraction: null, measurement: null, elapsed: 'elapsed 28s' }
    const options = props({ summaries: ['synthetic tool', '模型 · synthetic model', '目标 · 4K'], progress })
    const { rerender } = render(<WorkflowNodeCard {...options} />)
    expect(within(screen.getByRole('list', { name: '关键设置' })).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['synthetic tool', '模型 · synthetic model', '目标 · 4K'])
    expect(screen.getByText('已用时 28 秒')).toBeInTheDocument()
    expect(screen.queryByText('elapsed 28s')).not.toBeInTheDocument()
    rerender(<WorkflowNodeCard {...props({ advanced: true, progress })} />)
    expect(screen.getByText('elapsed 28s')).toBeInTheDocument()
  })
  it('人工等待将耗时准确呈现为已等待，不变成剩余时间', () => {
    render(<WorkflowNodeCard {...props({ executorKind: 'manual_external', nodeRun: handoffRun().node_runs[1]!, progress: { mode: 'none', fraction: null, measurement: null, elapsed: 'elapsed 2m 8s' } })} />)
    expect(screen.getByText('已等待 2 分钟 8 秒')).toBeInTheDocument()
    expect(screen.queryByText(/剩余|ETA|elapsed/)).not.toBeInTheDocument()
  })
  it('默认友好状态、复用和失效不泄漏技术身份', () => {
    render(<WorkflowNodeCard {...props()} />)
    expect(screen.getByLabelText('画质增强 节点')).toBeInTheDocument()
    expect(screen.getByText('已完成')).toBeInTheDocument()
    expect(screen.getByText('已复用完成结果')).toBeInTheDocument()
    expect(screen.getByText('结果需要更新')).toBeInTheDocument()
    expect(screen.queryByText('private.type')).not.toBeInTheDocument()
  })
  it('高级展示保留精确状态和身份', () => {
    render(<WorkflowNodeCard {...props({ advanced: true })} />)
    expect(screen.getByText('Completed')).toBeInTheDocument()
    expect(screen.getByText('Reused')).toBeInTheDocument()
    expect(screen.getByText('Stale')).toBeInTheDocument()
    expect(screen.getByText('private.type')).toBeInTheDocument()
  })
  it('人工外部节点不显示传入的百分比，仅保留等待时长', () => {
    render(<WorkflowNodeCard {...props({ executorKind: 'manual_external', nodeRun: handoffRun().node_runs[1]! })} />)
    expect(screen.getByText('等待外部处理')).toBeInTheDocument()
    expect(screen.queryByText('60%')).not.toBeInTheDocument()
    expect(screen.getByText('已等待 1 分钟')).toBeInTheDocument()
  })
  it.each([1, 2, 6, 8, 16])('%i 个端口每侧都有独立实际行、完整身份和可聚焦名称，折叠后仍可追踪', (count) => {
    const inputs = ports(count, 'input'), outputs = ports(count, 'output')
    const initial = props({ inputs, outputs })
    const { container, rerender } = render(<WorkflowNodeCard {...initial} />)
    for (const collapsed of [false, true]) {
      rerender(<WorkflowNodeCard {...props({ inputs, outputs, collapsed })} />)
      expect(container.querySelectorAll('.node-port-row')).toHaveLength(count * 2)
      expect(container.querySelectorAll('.typed-handle')).toHaveLength(count * 2)
      for (const direction of ['input', 'output'] as const) {
        const rows = [...container.querySelectorAll(`.node-port-row--${direction}`)]
        expect(rows.map((row) => row.getAttribute('data-port-id'))).toEqual(ports(count, direction).map((port) => port.port_id))
        for (const [index, row] of rows.entries()) {
          const handle = row.querySelector('.typed-handle')!
          expect(handle).toHaveAttribute('data-handle-id', `${direction}-${index}`)
          expect(handle).toHaveAttribute('data-handle-type', direction === 'input' ? 'target' : 'source')
          expect(handle).not.toHaveAttribute('style')
          const name = row.querySelector('.node-port-label')!
          expect(name).toHaveAttribute('tabindex', '0')
          expect(name).not.toHaveAttribute('role', 'button')
          expect(name).toHaveAttribute('data-node-id', 'technical-node-id')
          expect(name).toHaveAttribute('data-port-direction', direction)
          expect(name).toHaveAttribute('data-port-id', `${direction}-${index}`)
        }
      }
    }
  })
  it('长名称保留完整可访问内容，摘要只取三项；折叠不能隐藏错误或 required 提示', () => {
    const label = '需要完整保留的很长合成节点名称'.repeat(5)
    const portLabel = '需要完整保留的很长输入端口名称'.repeat(5)
    const options = props({ label, inputs: ports(1, 'input'), summaries: ['设置一', '设置二', '设置三', '设置四'],
      portLabels: { input: { 'input-0': portLabel }, output: {} }, problemSummary: '还缺少输入连接：主要视频' })
    const { container, rerender } = render(<WorkflowNodeCard {...options} />)
    expect(container.querySelector('.node-card-title')).toHaveAttribute('title', label)
    expect(screen.getByLabelText(`${label} 节点`)).toBeInTheDocument()
    expect(screen.getByLabelText(`输入：${portLabel}`)).toHaveAttribute('title', `输入：${portLabel}`)
    expect(within(screen.getByRole('list', { name: '关键设置' })).getAllByRole('listitem')).toHaveLength(3)
    expect(screen.queryByText('设置四')).not.toBeInTheDocument()
    rerender(<WorkflowNodeCard {...options} data={{ ...options.data, collapsed: true }} />)
    expect(screen.queryByRole('list', { name: '关键设置' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('需要处理：还缺少输入连接：主要视频')).toBeVisible()
    expect(screen.getByLabelText(`输入：${portLabel}`)).toBeVisible()
  })
  it('服务失败只显示可信人类摘要，不把原始路径或长错误塞进节点卡', () => {
    const nodeRun = { ...handoffRun().node_runs[0]!, state: 'failed' as const,
      error: { reason: 'validation_failed' as const, message: 'X:/synthetic/private-output.mov failed long internal check' } }
    const { container } = render(<WorkflowNodeCard {...props({ nodeRun, collapsed: true })} />)
    expect(screen.getByLabelText('需要处理：处理输出未通过检查')).toBeVisible()
    expect(container).not.toHaveTextContent(nodeRun.error.message)
  })
  it('结构变化更新本节点的锚点；相同结构、普通进度、选择和坐标变化不主动触发测量', () => {
    const options = props({ inputs: ports(2, 'input'), outputs: ports(1, 'output') })
    const { rerender } = render(<WorkflowNodeCard {...options} />)
    updateNodeInternals.mockClear()
    for (let current = 1; current <= 10; current++) {
      rerender(<WorkflowNodeCard {...options} selected={current % 2 === 0} positionAbsoluteX={current}
        data={{ ...options.data, progress: { ...options.data.progress, fraction: current / 10, elapsed: `elapsed ${current}s` } }} />)
    }
    expect(updateNodeInternals).not.toHaveBeenCalled()
    rerender(<WorkflowNodeCard {...options} data={{ ...options.data, collapsed: true }} />)
    expect(updateNodeInternals).toHaveBeenLastCalledWith('technical-node-id')
    updateNodeInternals.mockClear()
    rerender(<WorkflowNodeCard {...options} data={{ ...options.data, inputs: ports(16, 'input'), outputs: ports(16, 'output') }} />)
    expect(updateNodeInternals).toHaveBeenCalledOnce()
  })
  it('实际卡片尺寸和字体完成变化重新测量；重复尺寸和卸载后的回调不更新', async () => {
    let resize: ResizeObserverCallback | null = null
    const disconnect = vi.fn()
    vi.stubGlobal('ResizeObserver', class { constructor(callback: ResizeObserverCallback) { resize = callback } observe() {} disconnect = disconnect })
    const fontEvents = new EventTarget()
    let finishFonts: (() => void) | undefined
    const originalFonts = Object.getOwnPropertyDescriptor(document, 'fonts')
    const ready = new Promise<void>((resolve) => { finishFonts = resolve })
    Object.defineProperty(document, 'fonts', { configurable: true, value: Object.assign(fontEvents, { ready }) })
    try {
      const { unmount } = render(<WorkflowNodeCard {...props()} />)
      updateNodeInternals.mockClear()
      const emitSize = (height: number) => act(() => resize?.([{ contentRect: { width: 248, height } } as ResizeObserverEntry], {} as ResizeObserver))
      emitSize(180); emitSize(180); emitSize(260)
      expect(updateNodeInternals).toHaveBeenCalledTimes(2)
      act(() => { fontEvents.dispatchEvent(new Event('loadingdone')) })
      expect(updateNodeInternals).toHaveBeenCalledTimes(3)
      unmount()
      expect(disconnect).toHaveBeenCalledOnce()
      await act(async () => { finishFonts?.(); await ready })
      emitSize(300)
      act(() => { fontEvents.dispatchEvent(new Event('loadingdone')) })
      expect(updateNodeInternals).toHaveBeenCalledTimes(3)
    } finally {
      if (originalFonts) Object.defineProperty(document, 'fonts', originalFonts)
      else Reflect.deleteProperty(document, 'fonts')
    }
  })
})
