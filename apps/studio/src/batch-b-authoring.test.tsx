/** B 批 authoring 集成：使用正式 App/保存控制器与显式合成测量，不把 JSDOM 当浏览器尺寸证据。 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { RunDetailEnvelope, StatusEnvelope, StudioCommand } from './studio/contracts'
import type { StudioGateway } from './studio/gateway'
import type { WorkflowNode } from './model'
import type { CanvasMeasurement } from './studio/components/CanvasGeometryObserver'
import { geometryShape } from './studio/components/use-canvas-node-geometry'
import { studioEnvelope, threeRunDetail, threeRunEnvelope, threeRunFixtureIds,
  handoffLogEnvelope, handoffReadinessEnvelope, rerunPreviewEnvelope } from './studio/test-fixtures'

// 只替代 JSDOM 不具备的布局测量。实际 GraphCanvas、确认、参数草稿、Undo 和 CAS 保存全部使用生产代码。
vi.mock('./studio/components/CanvasGeometryObserver', async () => {
  const { useEffect } = await import('react')
  const { useNodes } = await import('@xyflow/react')
  return { CanvasGeometryObserver: ({ viewKey, onMeasure }: { viewKey: string; onMeasure: (key: string, value: CanvasMeasurement) => void }) => {
    const flowNodes = useNodes<WorkflowNode>()
    const nodes = flowNodes.map((node) => ({ id: node.id, ...node.position, width: 248, height: node.id === 'transform' ? 240 : 180,
      inputs: Object.fromEntries(node.data.inputs.map((port, index) => [port.port_id, { x: node.position.x - 4.5, y: node.position.y + 100 + index * 32 }])),
      outputs: Object.fromEntries(node.data.outputs.map((port, index) => [port.port_id, { x: node.position.x + 252.5, y: node.position.y + 100 + index * 32 }])),
    }))
    const shapeKey = JSON.stringify(flowNodes.map((node) => [node.id, geometryShape(node)]))
    const key = JSON.stringify([nodes, shapeKey])
    useEffect(() => { onMeasure(viewKey, { nodes, shapeKey, key, dragging: false, readMs: 0 }) }, [key, onMeasure, viewKey])
    return null
  } }
})
afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks() })

function setup(initial = studioEnvelope()) {
  let state = initial
  const commands: StudioCommand[] = []
  const details = new Map<string, RunDetailEnvelope>()
  const gateway: StudioGateway = {
    inspect: async () => state,
    listRuns: async () => ({ contract_version: '0.3.0', run_summaries: [], next_run_cursor: null }),
    inspectRun: async (id) => {
      if (!details.has(id)) details.set(id, threeRunDetail(id))
      return details.get(id)!
    },
    inspectLog: async () => handoffLogEnvelope(),
    inspectReadiness: async (_run, _node, probe) => handoffReadinessEnvelope(probe ? 'probe_passed' : 'present', probe),
    previewRerun: async (request) => rerunPreviewEnvelope(request),
    previewAvEnhanceV27: async () => { throw new Error('此测试不得进入配方生成') },
    command: async (command) => {
      commands.push(command)
      if (command.operation !== 'save_project') throw new Error(`布局不得发送 ${command.operation}`)
      state = { ...state, snapshot: { ...state.snapshot!, project: command.project }, studio_state: command.studio_state,
        project_session_id: command.project_session_id, storage_revision: command.expected_storage_revision + 1 }
      return state
    },
  }
  return { gateway, commands, details, state: () => state }
}
async function open(initial?: StatusEnvelope) {
  const test = setup(initial)
  const result = render(<App gateway={test.gateway} />)
  const home = await screen.findByRole('button', { name: '关闭工程首页' })
  fireEvent.click(home)
  await screen.findByLabelText('test.transform 节点')
  await waitFor(() => expect(screen.getByRole('button', { name: '整理布局' })).toBeEnabled())
  return { ...test, ...result }
}
function withoutPositions(state: StatusEnvelope) {
  return state.snapshot!.project.graph.nodes.map(({ ui_position: _position, ...node }) => node)
}

describe('批次 B 布局 authoring 集成', () => {
  it('未应用草稿整理后保留选择/参数，只存一次位置；解除原草稿门禁后一次 Undo 恢复', async () => {
    const test = await open()
    const original = structuredClone(test.state())
    fireEvent.click(screen.getByLabelText('test.transform 节点'))
    const field = await screen.findByRole('spinbutton', { name: 'strength' })
    fireEvent.change(field, { target: { value: '4' } })
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '整理布局' }))
    const dialog = await screen.findByRole('dialog', { name: '整理布局' })
    expect(test.commands).toEqual([])
    fireEvent.click(within(dialog).getByRole('button', { name: '应用布局' }))
    await waitFor(() => expect(test.commands).toHaveLength(1), { timeout: 2000 })
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(4)
    expect(screen.getByLabelText('test.transform 节点')).toHaveClass('is-selected')
    expect(test.state().snapshot!.project.graph.nodes[1]!.parameters.strength).toBe(3)
    expect(withoutPositions(test.state())).toEqual(withoutPositions(original))
    expect(test.state().snapshot!.project.graph.edges).toEqual(original.snapshot!.project.graph.edges)
    expect(test.state().studio_state).toEqual(original.studio_state)
    expect(test.state().snapshot!.project.graph.nodes.map((node) => node.ui_position))
      .not.toEqual(original.snapshot!.project.graph.nodes.map((node) => node.ui_position))
    expect(test.commands.map((command) => command.operation)).toEqual(['save_project'])
    // 原先 dirty 参数期间不能 Undo；显式放弃草稿后，唯一一次布局历史项才可撤销。
    fireEvent.click(screen.getByRole('button', { name: '放弃未应用更改' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '撤销' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    await waitFor(() => expect(test.commands).toHaveLength(2), { timeout: 2000 })
    expect(test.state().snapshot!.project.graph).toEqual(original.snapshot!.project.graph)
    expect(screen.getByLabelText('test.transform 节点')).toHaveClass('is-selected')
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled()
    expect(test.commands.map((command) => command.operation)).toEqual(['save_project', 'save_project'])
  })
  it('取消整理不提交位置、Undo 或保存，也不应用当前草稿', async () => {
    const test = await open()
    const original = structuredClone(test.state())
    fireEvent.click(screen.getByLabelText('test.transform 节点'))
    fireEvent.change(await screen.findByRole('spinbutton', { name: 'strength' }), { target: { value: '4' } })
    fireEvent.click(screen.getByRole('button', { name: '整理布局' }))
    const dialog = await screen.findByRole('dialog', { name: '整理布局' })
    fireEvent.click(within(dialog).getByRole('button', { name: '取消整理' }))
    expect(screen.queryByRole('dialog', { name: '整理布局' })).not.toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(4)
    expect(screen.getByLabelText('test.transform 节点')).toHaveClass('is-selected')
    expect(test.state()).toEqual(original)
    fireEvent.click(screen.getByRole('button', { name: '放弃未应用更改' }))
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled()
    // 等过正式 500ms debounce，确认取消没有偷偷排入保存队列。
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 650)) })
    expect(test.commands).toEqual([])
  })
  it('只读历史始终禁用整理；返回编辑不会改写历史 snapshot 或启动命令', async () => {
    const initial = threeRunEnvelope()
    const test = await open(initial)
    const snapshot = structuredClone(test.state().snapshot)
    fireEvent.click(screen.getByRole('button', { name: '处理记录' }))
    const drawer = screen.getByRole('region', { name: '任务抽屉' })
    const history = within(drawer).getByRole('tabpanel', { name: '历史记录' })
    const entry = within(history).getAllByRole('button').find((button) => (button as HTMLButtonElement).value === threeRunFixtureIds.earlierLocalRun)
    expect(entry).toBeDefined()
    fireEvent.click(entry!)
    await waitFor(() => expect(test.details.has(threeRunFixtureIds.earlierLocalRun)).toBe(true))
    const historical = structuredClone(test.details.get(threeRunFixtureIds.earlierLocalRun))
    expect(historical).toBeDefined()
    const showHistory = screen.queryByRole('button', { name: '查看本次处理流程' })
    if (showHistory) fireEvent.click(showHistory)
    await waitFor(() => expect(screen.getByRole('button', { name: '整理布局' })).toBeDisabled())
    expect(screen.getByText('本次处理的流程（只读）')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '整理布局' }))
    expect(screen.queryByRole('dialog', { name: '整理布局' })).not.toBeInTheDocument()
    fireEvent.click(within(screen.getByLabelText('Studio Designer 画布')).getByRole('button', { name: '返回当前编辑' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '整理布局' })).toBeEnabled())
    expect(test.state().snapshot).toEqual(snapshot)
    expect(test.details.get(threeRunFixtureIds.earlierLocalRun)).toEqual(historical)
    expect(test.commands).toEqual([])
  })
})
