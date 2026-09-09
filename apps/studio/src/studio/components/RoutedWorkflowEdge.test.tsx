/** 真实端点优先于异步路线；临时显示和降级不得改变端口、Edge 身份或 ordered_many。 */
import { cleanup, render } from '@testing-library/react'
import { getSmoothStepPath, Position, type BaseEdgeProps, type EdgeProps } from '@xyflow/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowEdge } from '../../model'
import type { GeometryRoute } from '../geometry/types'
import { RoutedWorkflowEdge, routeLabelPosition } from './RoutedWorkflowEdge'

const baseEdge = vi.hoisted(() => vi.fn())
vi.mock('@xyflow/react', async (original) => ({
  ...await original<typeof import('@xyflow/react')>(),
  BaseEdge: (props: BaseEdgeProps) => { baseEdge(props); return <><path className="react-flow__edge-path" d={props.path} markerEnd={props.markerEnd} /><text>{props.label}</text></> },
}))
afterEach(() => { cleanup(); vi.clearAllMocks() })

const route: GeometryRoute = {
  id: 'edge.synthetic', path: 'M100 120 L300 120 L300 220 L600 220', reason: null,
  points: [{ x: 100, y: 120 }, { x: 300, y: 120 }, { x: 300, y: 220 }, { x: 600, y: 220 }],
  bounds: { left: 100, right: 600, top: 120, bottom: 220 },
}
function props(overrides: Partial<EdgeProps<WorkflowEdge>> = {}): EdgeProps<WorkflowEdge> {
  return { id: 'edge.synthetic', source: 'source', target: 'target', sourceHandleId: 'video', targetHandleId: 'clips',
    sourceX: 100, sourceY: 120, targetX: 600, targetY: 220, sourcePosition: Position.Right, targetPosition: Position.Left,
    markerEnd: 'url(#arrow)', label: '第 3 路输入', labelStyle: { fill: '#abcdef' }, labelBgStyle: { fill: '#123456' },
    data: { ordinal: 2, route, start: { x: 100, y: 120 }, end: { x: 600, y: 220 },
      accessibleLabel: '合成输入（主要视频）→ 合成汇合（有序输入），第 3 路输入' },
    ...overrides } as EdgeProps<WorkflowEdge>
}
function path(container: HTMLElement): string { return container.querySelector('.react-flow__edge-path')!.getAttribute('d')! }
function expectedFallback(options: EdgeProps<WorkflowEdge>): string {
  return getSmoothStepPath({ sourceX: options.sourceX, sourceY: options.sourceY, targetX: options.targetX, targetY: options.targetY,
    sourcePosition: options.sourcePosition, targetPosition: options.targetPosition })[0]
}

describe('临时路线渲染', () => {
  it('顺序标签按折线长度放在中部，不因只有两个点而堆到输入端口', () => {
    expect(routeLabelPosition([{ x: 10, y: 20 }, { x: 210, y: 20 }])).toEqual({ x: 110, y: 20 })
    expect(routeLabelPosition([{ x: 10, y: 20 }, { x: 10, y: 220 }])).toEqual({ x: 10, y: 120 })
    expect(routeLabelPosition([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 200 }, { x: 200, y: 200 }])).toEqual({ x: 100, y: 100 })
    expect(routeLabelPosition([])).toBeUndefined()
  })
  it('当前成功路线原样绘制；方向、名称、端口绑定与顺序标签保持', () => {
    const options = props(), before = JSON.stringify(options)
    const { container } = render(<svg><RoutedWorkflowEdge {...options} /></svg>)
    expect(path(container)).toBe(route.path)
    expect(container.querySelector('g')).toHaveAttribute('data-route-status', 'routed')
    expect(container.querySelector('g')).toHaveAttribute('data-route-reason', '')
    expect(container.querySelector('g')).toHaveAttribute('data-source-port', 'video')
    expect(container.querySelector('g')).toHaveAttribute('data-target-port', 'clips')
    expect(container.querySelector('title')).toHaveTextContent(options.data!.accessibleLabel!)
    expect(baseEdge).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'edge.synthetic', markerEnd: 'url(#arrow)',
      interactionWidth: 20, label: '第 3 路输入', labelStyle: { fill: '#abcdef' }, labelBgStyle: { fill: '#123456' } }))
    expect(JSON.stringify(options)).toBe(before)
    expect(container.querySelector('circle')).toBeNull()
  })
  it.each(['sourceX', 'sourceY', 'targetX', 'targetY'] as const)('%s 改变后拒绝旧路线和旧交叉断口，立即贴合当帧真实端点', (coordinate) => {
    const initial = props({ data: { ...props().data!, displayPath: 'M100 120 L245 120 M255 120 L600 220' } })
    const { container, rerender } = render(<svg><RoutedWorkflowEdge {...initial} /></svg>)
    const current = { ...initial, [coordinate]: initial[coordinate] + 17 }
    rerender(<svg><RoutedWorkflowEdge {...current} /></svg>)
    expect(path(container)).toBe(expectedFallback(current))
    expect(path(container)).not.toBe(route.path)
    expect(path(container)).not.toBe(initial.data!.displayPath)
    expect(container.querySelector('.route-crossing-halo')).toHaveAttribute('d', path(container))
    expect(container.querySelector('g')).toHaveAttribute('data-route-status', 'pending')
    expect(container.querySelector('g')).toHaveAttribute('data-route-reason', 'measuring')
    expect(container.querySelector('text')).toHaveTextContent('第 3 路输入')
  })
  it.each(['blocked_port', 'work_budget', 'time_budget', 'no_channel'] as const)('%s 降级仍有非空真实路径和原因，不冒充避障成功', (reason) => {
    const initial = props()
    const degraded = { ...route, path: expectedFallback(initial), points: [], reason }
    const options = props({ data: { ...initial.data!, route: degraded } })
    const { container } = render(<svg><RoutedWorkflowEdge {...options} /></svg>)
    expect(path(container)).toBe(degraded.path)
    expect(path(container).length).toBeGreaterThan(10)
    expect(container.querySelector('g')).toHaveClass('is-degraded')
    expect(container.querySelector('g')).toHaveAttribute('data-route-status', 'degraded')
    expect(container.querySelector('g')).toHaveAttribute('data-route-reason', reason)
    expect(container.querySelector('title')).toHaveTextContent('简化路线，建议整理布局')
  })
  it('首次测量无路线也保持可见；新路线到达后才去除 pending 标志', () => {
    const options = props({ data: { ordinal: 2 } })
    const { container, rerender } = render(<svg><RoutedWorkflowEdge {...options} /></svg>)
    expect(path(container)).toBe(expectedFallback(options))
    expect(container.querySelector('g')).toHaveAttribute('data-route-reason', 'measuring')
    rerender(<svg><RoutedWorkflowEdge {...props()} /></svg>)
    expect(path(container)).toBe(route.path)
    expect(container.querySelector('g')).not.toHaveClass('is-degraded')
  })
  it('交叉只切开临时显示，底层路线不变；被选中的边不能被低权重样式淹没', () => {
    const displayPath = 'M100 120 L245 120 M255 120 L300 120 L300 220 L600 220'
    const options = props({ selected: true, data: { ...props().data!, displayPath, highlighted: false, subdued: true } })
    const { container } = render(<svg><RoutedWorkflowEdge {...options} /></svg>)
    expect(path(container)).toBe(displayPath)
    expect(container.querySelector('g')).toHaveClass('is-highlighted')
    expect(container.querySelector('g')).not.toHaveClass('is-subdued')
    expect(route.path).toBe('M100 120 L300 120 L300 220 L600 220')
    expect(options.data!.ordinal).toBe(2)
    expect(container.querySelector('circle')).toBeNull()
  })
})
