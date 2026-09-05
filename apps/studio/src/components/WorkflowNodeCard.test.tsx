/** 节点卡状态只投影正式字段；外部节点即使收到错误进度数据也不展示百分比。 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { NodeProps } from '@xyflow/react'
import type { WorkflowNode, WorkflowNodeData } from '../model'
import { handoffRun } from '../studio/test-fixtures'
import { WorkflowNodeCard } from './WorkflowNodeCard'

vi.mock('@xyflow/react', () => ({ Position: { Left: 'left', Right: 'right' }, Handle: () => null }))
afterEach(cleanup)
function props(overrides: Partial<WorkflowNodeData> = {}): NodeProps<WorkflowNode> {
  const data: WorkflowNodeData = { label: '画质增强', instanceId: 'technical-node-id', typeId: 'private.type', definitionVersion: '0.2.0', executorKind: 'python',
    inputs: [], outputs: [], summaries: [], nodeRun: { ...handoffRun().node_runs[0]!, reused_from_result_id: 'synthetic-result' },
    latestResult: { node_id: 'technical-node-id', result_id: 'synthetic-result', stale: true, stale_reason: 'graph_changed', updated_at: '2026-09-05T00:00:00Z' },
    progress: { mode: 'determinate', fraction: .6, measurement: null, elapsed: '已等待 1 分钟' }, advanced: false, ...overrides }
  return { data, selected: false } as NodeProps<WorkflowNode>
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
})
