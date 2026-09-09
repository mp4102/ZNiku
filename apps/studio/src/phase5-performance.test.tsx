/** 合成大图/高频投影及桌面偏好恢复回归；jsdom 不承担真实交互 p95 或原生窗口性能证明。 */

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { NodeRunWire, RunDetailEnvelope, RunSummaryWire, StatusEnvelope } from './studio/contracts'
import type { StudioGateway } from './studio/gateway'
import { readDesktopPreferences, type DesktopPreferences, type HostBridge, type HostCapabilitiesEnvelope } from './studio/host-bridge'
import { readRecentProjects } from './studio/recent-projects'
import { projectedProgressDetail, projectSnapshot, runningProgressEnvelope, sourceDefinition, studioEnvelope } from './studio/test-fixtures'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  window.localStorage.clear()
  delete window.__ZNIKU_DESKTOP__
})

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
}

function openViewAction(name: string): HTMLElement {
  const trigger = screen.getByRole('button', { name: '视图' })
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.click(trigger)
  return within(screen.getByRole('group', { name: '视图菜单' })).getByRole('button', { name })
}

function unusedGatewayMethods() {
  return {
    inspectLog: vi.fn(async () => { throw new Error('未显式打开日志') }),
    inspectReadiness: vi.fn(async () => { throw new Error('此图没有人工交接') }),
    previewAvEnhanceV27: vi.fn(async () => { throw new Error('此图不是模板向导') }),
    command: vi.fn(async () => { throw new Error('只读投影不允许 mutation') }),
  }
}

describe('Phase 5 合成规模与偏好恢复', () => {
  it('200 节点连续投影更新只读取有界历史窗口，不自动拉日志/全历史或修改 Graph', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-24T00:01:00Z'))
    window.localStorage.setItem('zniku.studio.density', 'advanced')
    const base = runningProgressEnvelope(.01)
    const originalDetail = projectedProgressDetail(0, .01)
    const graph = { nodes: Array.from({ length: 200 }, (_, index) => ({
      node_id: `source-${index}`, type_id: sourceDefinition.type_id, definition_version: sourceDefinition.version,
      parameters: {}, ui_position: { x: index % 10 * 250, y: Math.floor(index / 10) * 180 },
    })), edges: [] }
    const snapshot = { project: { ...projectSnapshot.project, graph }, definitions: [sourceDefinition] }
    const before = JSON.stringify(snapshot)
    const firstNodeRun = { ...originalDetail.run.node_runs[0]!, node_id: 'source-0' }
    const nodeRuns: NodeRunWire[] = graph.nodes.map((node, index) => index === 0 ? firstNodeRun : {
      ...firstNodeRun, node_id: node.node_id, node_run_id: `00000000-0000-4000-8000-${String(index + 3000).padStart(12, '0')}`,
      state: 'pending', started_at: null, progress: null, log_path: null,
    })
    const running: RunSummaryWire = { ...base.run_summaries[0]!, node_count: 200,
      state_counts: { pending: 199, running: 1, waiting_external: 0, completed: 0, failed: 0 } }
    // 服务端拥有 1000 条历史，但 status 只返回 20 条终态加当前运行；UI 不能自行追完所有游标。
    const history: RunSummaryWire[] = Array.from({ length: 1000 }, (_, index) => ({
      ...running, run_id: `00000000-0000-4000-8000-${String(index + 5000).padStart(12, '0')}`,
      state: 'completed', actionable: false, requires_operator_action: false,
      ended_at: '2026-08-23T00:00:00Z', created_at: '2026-08-23T00:00:00Z',
      state_counts: { pending: 0, running: 0, waiting_external: 0, completed: 200, failed: 0 },
    }))
    let current = 1
    const extras = unusedGatewayMethods()
    const inspect = vi.fn(async (): Promise<StatusEnvelope> => structuredClone({
      ...base, snapshot, run_summaries: [running, ...history.slice(0, 20)], next_run_cursor: 'history.20',
    }))
    const inspectRun = vi.fn(async (): Promise<RunDetailEnvelope> => structuredClone({
      ...originalDetail,
      run: { ...originalDetail.run, graph_snapshot: graph, definitions_snapshot: [sourceDefinition], node_runs: nodeRuns },
      progress_samples: [{ ...originalDetail.progress_samples[0]!, fraction: current / 100, current,
        observed_at: new Date(Date.now()).toISOString() }],
    }))
    const listRuns = vi.fn(async (cursor?: string | null, limit = 20) => {
      expect(cursor).toBe('history.20')
      expect(limit).toBe(20)
      return { contract_version: '0.3.0' as const, run_summaries: history.slice(20, 20 + limit), next_run_cursor: 'history.40' }
    })
    const gateway: StudioGateway = { ...extras, inspect, inspectRun, listRuns }
    const { container, unmount } = render(<App gateway={gateway} />)
    await flush()
    expect(container.querySelectorAll('.workflow-node')).toHaveLength(200)
    const node = screen.getByLabelText('source-0 节点')
    expect(within(node).getByText('1%')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '处理记录' }))
    // 只在已经可见的历史面板核对条目，避免每轮从200节点/任务行的整棵DOM重复扫描可访问角色。
    // 仍逐轮验证21条、显式翻页后41条；不改变轮询数、请求预算或20秒上限。
    const historyPanel = screen.getByRole('tabpanel', { name: '历史记录' })
    expect(historyPanel).toBeVisible()
    for (let tick = 1; tick <= 16; tick += 1) {
      current = tick * 5
      await act(async () => { await vi.advanceTimersByTimeAsync(751) })
      expect(within(node).getByText(`${current}%`)).toBeInTheDocument()
      expect(within(historyPanel).getAllByRole('button', { name: /^查看处理记录 \d+：/ })).toHaveLength(21)
    }
    expect(inspect.mock.calls.length).toBeGreaterThanOrEqual(17)
    expect(inspectRun.mock.calls.length).toBeGreaterThanOrEqual(17)
    expect(listRuns).not.toHaveBeenCalled()
    expect(extras.command).not.toHaveBeenCalled()
    expect(extras.inspectLog).not.toHaveBeenCalled()
    expect(extras.inspectReadiness).not.toHaveBeenCalled()
    expect(JSON.stringify(snapshot)).toBe(before)
    expect(JSON.stringify(originalDetail.run.graph_snapshot)).toBe(JSON.stringify(projectSnapshot.project.graph))
    // 只有用户请求下一页才读取一次；后续进度轮询不扩张已加载的历史范围。
    fireEvent.click(within(historyPanel).getByRole('button', { name: '加载更早记录' }))
    await flush()
    expect(listRuns).toHaveBeenCalledExactlyOnceWith('history.20', 20)
    await act(async () => { await vi.advanceTimersByTimeAsync(751) })
    expect(within(historyPanel).getAllByRole('button', { name: /^查看处理记录 \d+：/ })).toHaveLength(41)
    expect(listRuns).toHaveBeenCalledTimes(1)
    expect(extras.command).not.toHaveBeenCalled()
    unmount()
    const calls = inspect.mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(inspect).toHaveBeenCalledTimes(calls)
  }, 20_000)

  it.each(['creator', 'advanced'] as const)('新 Origin 恢复 %s 与最近工程，迟挂载不回写旧偏好，显式变化仍保存', async (density) => {
    // launcher 每次使用新端口时 localStorage 可以为空；本测试模拟这个存储条件，而不伪称重启浏览器。
    window.localStorage.clear()
    const preferences: DesktopPreferences = { density, recent_projects: [
      { path: 'C:\\synthetic\\recent-a.zniku', name: '合成最近工程 A', opened_at: '2026-09-05T00:00:00Z' },
      { path: 'C:\\synthetic\\recent-b.zniku', name: '合成最近工程 B', opened_at: '2026-09-04T00:00:00Z' },
    ] }
    const instanceId = '00000000-0000-4000-8000-000000009001'
    window.__ZNIKU_DESKTOP__ = { contractVersion: '0.3.0', instanceId, preferences }
    expect(readRecentProjects()).toEqual([])
    expect(readDesktopPreferences()).toEqual(preferences)
    const capabilities: HostCapabilitiesEnvelope = { contract_version: '0.3.0', capabilities: (['open_file', 'open_files', 'select_directory', 'save_file', 'reveal_in_file_manager', 'open_with_system_player'] as const)
      .map((capability) => ({ capability, available: true, unavailable_reason: null })) }
    // 模拟页面拿到 bootstrap 后，另一标签先保存了新偏好；本标签挂载不应写回旧快照。
    let persisted: DesktopPreferences = { ...preferences, density: density === 'creator' ? 'advanced' : 'creator' }
    const saveDesktopPreferences = vi.fn(async (_id: string, next: DesktopPreferences) => { persisted = next })
    const hostBridge: HostBridge = { configured: true, inspectCapabilities: vi.fn(async () => capabilities),
      pick: vi.fn(async () => null), launch: vi.fn(async () => undefined), saveDesktopPreferences }
    let status = studioEnvelope({ snapshot: null })
    const extras = unusedGatewayMethods()
    const command = vi.fn(async (value: Parameters<StudioGateway['command']>[0]) => {
      expect(value).toEqual({ operation: 'open_project', path: preferences.recent_projects[1]!.path })
      status = studioEnvelope({ project_path: preferences.recent_projects[1]!.path,
        snapshot: { ...projectSnapshot, project: { ...projectSnapshot.project, name: preferences.recent_projects[1]!.name } } })
      return status
    })
    const gateway: StudioGateway = { ...extras, command, inspect: vi.fn(async () => status),
      inspectRun: vi.fn(async () => { throw new Error('尚未运行') }),
      listRuns: vi.fn(async () => ({ contract_version: '0.3.0' as const, run_summaries: [], next_run_cursor: null })) }
    render(<StrictMode><App gateway={gateway} hostBridge={hostBridge} /></StrictMode>)
    await flush()
    expect(openViewAction(density === 'advanced' ? '返回创作者模式' : '高级节点图')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /合成最近工程 A/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /合成最近工程 B/ })).toBeVisible()
    expect(saveDesktopPreferences).not.toHaveBeenCalled()
    expect(persisted.density).not.toBe(density)
    expect(command).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /合成最近工程 B/ }))
    await flush()
    expect(command).toHaveBeenCalledTimes(1)
    const saved = saveDesktopPreferences.mock.calls.at(-1)![1]
    expect(saved.density).toBe(density)
    expect(saved.recent_projects.map((item) => item.path)).toEqual([preferences.recent_projects[1]!.path, preferences.recent_projects[0]!.path])
    expect(saveDesktopPreferences.mock.calls.every((call) => call[1].recent_projects.length === 2)).toBe(true)
    expect(readRecentProjects().map((item) => item.name)).toEqual(['合成最近工程 B', '合成最近工程 A'])
    fireEvent.click(openViewAction(density === 'advanced' ? '返回创作者模式' : '高级节点图'))
    await flush()
    expect(persisted.density).not.toBe(density)
    fireEvent.click(openViewAction(density === 'advanced' ? '高级节点图' : '返回创作者模式'))
    await flush()
    // 回到初始密度也必须写回，不能始终只与 bootstrap 比较而跳过真正的用户修改。
    expect(persisted.density).toBe(density)
    expect(saveDesktopPreferences.mock.calls.at(-1)![0]).toBe(instanceId)
    for (const [, value] of saveDesktopPreferences.mock.calls) {
      expect(Object.keys(value).sort()).toEqual(['density', 'recent_projects'])
    }
  })
})
