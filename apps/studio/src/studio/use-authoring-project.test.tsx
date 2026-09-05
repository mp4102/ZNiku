import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StatusEnvelope, StudioCommand } from './contracts'
import type { StudioGateway } from './gateway'
import { StudioGatewayError } from './gateway'
import { defaultStudioState, projectSessionId, studioEnvelope } from './test-fixtures'
import { useAuthoringProject } from './use-authoring-project'
import type { AuthoringDocument } from './use-authoring-project'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolveValue, rejectValue) => { resolve = resolveValue; reject = rejectValue })
  return { promise, resolve, reject }
}

function gatewayWith(command = vi.fn<(command: StudioCommand) => Promise<StatusEnvelope>>()): StudioGateway {
  return {
    command, inspect: vi.fn(), listRuns: vi.fn(), inspectRun: vi.fn(), inspectLog: vi.fn(),
    inspectReadiness: vi.fn(), previewAvEnhanceV27: vi.fn(),
  }
}

function saved(command: StudioCommand, revision: number, overrides: Partial<StatusEnvelope> = {}): StatusEnvelope {
  if (command.operation !== 'save_project') throw new Error('合成 gateway 只接受保存')
  const base = studioEnvelope()
  return studioEnvelope({
    snapshot: { ...base.snapshot!, project: command.project }, studio_state: command.studio_state,
    storage_revision: revision, ...overrides,
  })
}

const rename = (name: string) => (value: AuthoringDocument): AuthoringDocument => ({
  ...value, snapshot: { ...value.snapshot, project: { ...value.snapshot.project, name } },
})

describe('Project authoring 会话集成', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('快速本地编辑自动保存同一 Graph 与 StudioState，普通 ack 保留 Undo', async () => {
    const command = vi.fn<(value: StudioCommand) => Promise<StatusEnvelope>>()
      .mockImplementation(async (value) => saved(value, 1))
    const gateway = gatewayWith(command)
    const { result } = renderHook(() => useAuthoringProject(gateway))
    act(() => { result.current.ingest(studioEnvelope()) })
    act(() => {
      result.current.edit('改工程名', rename('第一版'))
      result.current.edit('改工程名', rename('第二版'))
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(command).toHaveBeenCalledTimes(1)
    expect(command.mock.calls[0][0]).toMatchObject({
      operation: 'save_project', project: { name: '第二版' }, studio_state: defaultStudioState,
      expected_storage_revision: 0, project_session_id: projectSessionId,
    })
    expect(result.current.dirty).toBe(false)
    expect(result.current.canUndo).toBe(true)
    expect(result.current.precondition().expected_storage_revision).toBe(1)
  })

  it('保存期间新编辑与重连状态不被旧 ack 覆盖，flush 等最新快照', async () => {
    const first = deferred<StatusEnvelope>()
    const second = deferred<StatusEnvelope>()
    const command = vi.fn<(value: StudioCommand) => Promise<StatusEnvelope>>()
      .mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const gateway = gatewayWith(command)
    const { result } = renderHook(() => useAuthoringProject(gateway))
    act(() => {
      result.current.ingest(studioEnvelope())
      result.current.onSaved.current = (status) => { result.current.ingest(status) }
      result.current.edit('一', rename('一'))
    })
    let flushed!: Promise<unknown>
    act(() => { flushed = result.current.flush() })
    act(() => {
      result.current.edit('二', rename('二'))
      result.current.ingest(studioEnvelope())
    })
    await act(async () => { first.resolve(saved(command.mock.calls[0][0], 1)); await Promise.resolve() })
    expect(command).toHaveBeenCalledTimes(2)
    expect(result.current.document?.snapshot.project.name).toBe('二')
    expect(result.current.dirty).toBe(true)
    await act(async () => { second.resolve(saved(command.mock.calls[1][0], 2)); await flushed })
    expect(result.current.document?.snapshot.project.name).toBe('二')
    expect(result.current.canUndo).toBe(true)
    expect(result.current.dirty).toBe(false)
  })

  it('离线重连保留本地编辑，CAS 冲突保持 dirty 且只在明确强制载入后放弃', async () => {
    const command = vi.fn<(value: StudioCommand) => Promise<StatusEnvelope>>()
      .mockRejectedValueOnce(new Error('offline'))
      .mockRejectedValueOnce(new StudioGatewayError('工程已在其他窗口修改', { code: 'E_STORAGE_REVISION_CONFLICT' }))
    const gateway = gatewayWith(command)
    const { result } = renderHook(() => useAuthoringProject(gateway))
    act(() => { result.current.ingest(studioEnvelope()); result.current.edit('本地改名', rename('本地编辑')) })
    await act(async () => { await expect(result.current.flush()).rejects.toThrow('offline') })
    const remote = studioEnvelope({ storage_revision: 9 })
    act(() => { result.current.ingest(remote) })
    expect(result.current.document?.snapshot.project.name).toBe('本地编辑')
    expect(result.current.precondition().expected_storage_revision).toBe(0)
    await act(async () => { await expect(result.current.retry()).rejects.toThrow('其他窗口') })
    expect(command.mock.calls.map(([value]) => 'expected_storage_revision' in value ? value.expected_storage_revision : null))
      .toEqual([0, 0])
    expect(result.current.dirty).toBe(true)
    act(() => { result.current.ingest(remote, true) })
    expect(result.current.dirty).toBe(false)
    expect(result.current.canUndo).toBe(false)
    expect(result.current.error).toBeNull()
    expect(result.current.precondition().expected_storage_revision).toBe(9)
  })

  it('无本地修改时接纳新远端 revision，拒绝之后到达的旧状态', () => {
    const gateway = gatewayWith()
    const { result } = renderHook(() => useAuthoringProject(gateway))
    const base = studioEnvelope()
    const newer = studioEnvelope({
      storage_revision: 2, snapshot: { ...base.snapshot!, project: { ...base.snapshot!.project, name: '远端新名称' } },
    })
    act(() => { result.current.ingest(base); result.current.ingest(newer); result.current.ingest(base) })
    expect(result.current.document?.snapshot.project.name).toBe('远端新名称')
    expect(result.current.precondition().expected_storage_revision).toBe(2)
    expect(result.current.canUndo).toBe(false)
  })

  it('已落盘批量宏只产生一条历史，不重复保存；迟到宏回执不回退状态', async () => {
    const command = vi.fn<(value: StudioCommand) => Promise<StatusEnvelope>>().mockImplementation(async (value) => saved(value, 2))
    const gateway = gatewayWith(command)
    const { result } = renderHook(() => useAuthoringProject(gateway))
    const base = studioEnvelope()
    const macro = studioEnvelope({
      storage_revision: 1,
      snapshot: { ...base.snapshot!, project: { ...base.snapshot!.project, name: '完整处理宏' } },
      studio_state: {
        ...defaultStudioState,
        node_views: [{ node_id: 'source', display_name: '第一章', group_id: null, collapsed: true }],
      },
    })
    act(() => {
      result.current.ingest(base)
      result.current.ingest(macro, false, '批量生成工作流')
      result.current.ingest(base, false, '迟到的旧宏')
    })
    await act(async () => { await vi.runAllTimersAsync() })
    expect(command).not.toHaveBeenCalled()
    expect(result.current.document?.snapshot.project.name).toBe('完整处理宏')
    expect(result.current.undoLabel).toBe('批量生成工作流')
    expect(result.current.precondition().expected_storage_revision).toBe(1)
    act(() => { result.current.travel('undo') })
    expect(result.current.document?.snapshot).toEqual(base.snapshot)
    expect(result.current.document?.studioState).toEqual(defaultStudioState)
    expect(result.current.canUndo).toBe(false)
    await act(async () => { await result.current.flush() })
    expect(command.mock.calls[0][0]).toMatchObject({ expected_storage_revision: 1, project: base.snapshot!.project })
    act(() => { result.current.travel('redo') })
    expect(result.current.document?.snapshot).toEqual(macro.snapshot)
  })

  it('拖动只在结束时保存和记一条 Undo，删除节点同步清理并可恢复 view', async () => {
    const command = vi.fn<(value: StudioCommand) => Promise<StatusEnvelope>>().mockImplementation(async (value) => saved(value, 1))
    const gateway = gatewayWith(command)
    const { result } = renderHook(() => useAuthoringProject(gateway))
    const studio_state = {
      ...defaultStudioState,
      node_views: [{ node_id: 'source', display_name: '素材', group_id: null, collapsed: false }],
    }
    act(() => {
      result.current.ingest(studioEnvelope({ studio_state }))
      result.current.begin()
      for (const x of [120, 200, 250]) result.current.edit('预览拖动', (value) => ({
        ...value,
        snapshot: { ...value.snapshot, project: { ...value.snapshot.project, graph: {
          ...value.snapshot.project.graph,
          nodes: value.snapshot.project.graph.nodes.map((node) => node.node_id === 'source' ? { ...node, ui_position: { x, y: 100 } } : node),
        } } },
      }))
    })
    await act(async () => { await vi.runAllTimersAsync() })
    expect(command).not.toHaveBeenCalled()
    await expect(result.current.flush()).rejects.toThrow('结束节点拖动')
    act(() => { result.current.end() })
    await act(async () => { await result.current.flush() })
    expect(command).toHaveBeenCalledTimes(1)
    act(() => { result.current.travel('undo') })
    expect(result.current.document?.snapshot.project.graph.nodes[0].ui_position?.x).toBe(80)
    expect(result.current.canUndo).toBe(false)
    act(() => { result.current.edit('删除节点', (value) => ({
      ...value, snapshot: { ...value.snapshot, project: { ...value.snapshot.project, graph: { nodes: [], edges: [] } } },
    })) })
    expect(result.current.document?.studioState.node_views).toEqual([])
    act(() => { result.current.travel('undo') })
    expect(result.current.document?.studioState.node_views).toEqual(studio_state.node_views)
  })

  it('未保存内容遇到其他工程会话时保留本地并阻止运行，明确载入才切换', async () => {
    const gateway = gatewayWith()
    const { result } = renderHook(() => useAuthoringProject(gateway))
    const other = studioEnvelope({ project_session_id: '00000000-0000-4000-8000-000000000200', storage_revision: 10 })
    act(() => { result.current.ingest(studioEnvelope()); result.current.edit('修改', rename('本地')) })
    let accepted = true
    act(() => { accepted = result.current.ingest(other) })
    expect(accepted).toBe(false)
    expect(result.current.document?.snapshot.project.name).toBe('本地')
    await expect(result.current.flush()).rejects.toThrow('其他窗口切换')
    act(() => { result.current.ingest(other, true) })
    expect(result.current.error).toBeNull()
    expect(result.current.dirty).toBe(false)
    expect(result.current.precondition().project_session_id).toBe(other.project_session_id)
  })

  it('Graph 已保存但参数尚未应用时，重连不能替换同一节点的编辑来源', () => {
    const gateway = gatewayWith()
    const { result } = renderHook(() => useAuthoringProject(gateway))
    const base = studioEnvelope()
    const newer = studioEnvelope({
      storage_revision: 4,
      snapshot: { ...base.snapshot!, project: { ...base.snapshot!.project, name: '其他窗口修改' } },
    })
    act(() => { result.current.ingest(base); result.current.ingest(newer, false, undefined, true) })
    expect(result.current.document?.snapshot.project).toEqual(base.snapshot!.project)
    expect(result.current.precondition().expected_storage_revision).toBe(0)
    // 只有 Workspace 确认应用或放弃参数后，后续可信 status 才能更新编辑来源。
    act(() => { result.current.ingest(newer, false, undefined, false) })
    expect(result.current.document?.snapshot.project.name).toBe('其他窗口修改')
    expect(result.current.precondition().expected_storage_revision).toBe(4)
  })

  it('仅未应用参数也会阻止 foreign session；显式放弃并重载清除冲突', async () => {
    const gateway = gatewayWith()
    const { result } = renderHook(() => useAuthoringProject(gateway))
    const other = studioEnvelope({ project_session_id: '00000000-0000-4000-8000-000000000200' })
    act(() => { result.current.ingest(studioEnvelope()) })
    let accepted = true
    act(() => { accepted = result.current.ingest(other, false, undefined, true) })
    expect(accepted).toBe(false)
    expect(result.current.error).toContain('其他窗口切换')
    await expect(result.current.flush()).rejects.toThrow('其他窗口切换')
    act(() => { result.current.ingest(studioEnvelope({ snapshot: null }), true, undefined, false) })
    expect(result.current.document).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it.each(['project', 'studio_state', 'project_path', 'project_session_id', 'storage_revision'] as const)(
    '不接受错配 %s 的保存回执为已保存', async (field) => {
      const command = vi.fn<(value: StudioCommand) => Promise<StatusEnvelope>>().mockImplementation(async (value) => {
        const status = saved(value, 1)
        if (field === 'project') return { ...status, snapshot: { ...status.snapshot!, project: { ...status.snapshot!.project, name: '旧名称' } } }
        if (field === 'studio_state') return { ...status, studio_state: { ...defaultStudioState, viewport: { x: 1, y: 0, zoom: 1 } } }
        if (field === 'project_path') return { ...status, project_path: 'C:\\synthetic\\other.zniku' }
        if (field === 'storage_revision') return { ...status, storage_revision: 2 }
        return { ...status, project_session_id: '00000000-0000-4000-8000-000000000200' }
      })
      const gateway = gatewayWith(command)
      const { result } = renderHook(() => useAuthoringProject(gateway))
      act(() => { result.current.ingest(studioEnvelope()); result.current.edit('修改', rename('本地')) })
      await act(async () => { await expect(result.current.flush()).rejects.toThrow('回执与当前工程') })
      expect(result.current.dirty).toBe(true)
      expect(result.current.document?.snapshot.project.name).toBe('本地')
      expect(result.current.precondition().expected_storage_revision).toBe(0)
    },
  )

  it('字段重排的等价回执有效，但当前会话销毁后旧回执不会通知', async () => {
    const pending = deferred<StatusEnvelope>()
    const command = vi.fn<(value: StudioCommand) => Promise<StatusEnvelope>>().mockReturnValue(pending.promise)
    const gateway = gatewayWith(command)
    const onSaved = vi.fn()
    const { result, unmount } = renderHook(() => useAuthoringProject(gateway))
    act(() => { result.current.ingest(studioEnvelope()); result.current.onSaved.current = onSaved; result.current.edit('修改', rename('本地')) })
    let flush!: Promise<unknown>
    act(() => { flush = result.current.flush() })
    const status = saved(command.mock.calls[0][0], 1)
    const project = status.snapshot!.project
    await act(async () => {
      pending.resolve({ ...status, snapshot: { ...status.snapshot!, project: { graph: project.graph, name: project.name, project_id: project.project_id } } })
      await flush
    })
    expect(result.current.dirty).toBe(false)
    expect(onSaved).toHaveBeenCalledTimes(1)
    const late = deferred<StatusEnvelope>()
    command.mockReturnValueOnce(late.promise)
    act(() => { result.current.edit('再修改', rename('第二版')) })
    let rejected!: Promise<unknown>
    act(() => { rejected = expect(result.current.flush()).rejects.toThrow('会话已结束') })
    unmount()
    await rejected
    await act(async () => { late.resolve(saved(command.mock.calls[1][0], 2)); await Promise.resolve() })
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('flush 等待期间开始拖动时不返回运行资格，结束后再次 flush 保存最终位置', async () => {
    const first = deferred<StatusEnvelope>()
    const command = vi.fn<(value: StudioCommand) => Promise<StatusEnvelope>>()
      .mockReturnValueOnce(first.promise).mockImplementation(async (value) => saved(value, 2))
    const gateway = gatewayWith(command)
    const { result } = renderHook(() => useAuthoringProject(gateway))
    act(() => { result.current.ingest(studioEnvelope()); result.current.edit('修改', rename('编辑一')) })
    let rejected!: Promise<unknown>
    act(() => { rejected = expect(result.current.flush()).rejects.toThrow('结束节点拖动') })
    act(() => { result.current.begin(); result.current.edit('移动', rename('编辑二')) })
    await act(async () => { first.resolve(saved(command.mock.calls[0][0], 1)); await rejected })
    act(() => { result.current.end() })
    await act(async () => { await result.current.flush() })
    expect(command.mock.calls[1][0]).toMatchObject({ expected_storage_revision: 1, project: { name: '编辑二' } })
    expect(result.current.precondition().expected_storage_revision).toBe(2)
  })

  it('block API 稳定并冻结发送和轮询替换，唯显式 force 载入可以恢复', async () => {
    const command = vi.fn<(value: StudioCommand) => Promise<StatusEnvelope>>()
      .mockImplementation(async (value) => saved(value, 8))
    const gateway = gatewayWith(command)
    const { result } = renderHook(() => useAuthoringProject(gateway))
    const block = result.current.block
    act(() => { result.current.ingest(studioEnvelope()); result.current.edit('本地编辑', rename('尚未保存')) })
    act(() => { result.current.block('模板回执已变化，请重新载入工程。') })
    expect(result.current.block).toBe(block)
    act(() => {
      result.current.ingest(studioEnvelope({ storage_revision: 7 }))
      result.current.edit('继续编辑', rename('保留的最新内容'))
    })
    await act(async () => { await vi.runAllTimersAsync() })
    expect(command).not.toHaveBeenCalled()
    expect(result.current.document?.snapshot.project.name).toBe('保留的最新内容')
    await expect(result.current.flush()).rejects.toThrow('模板回执已变化')
    await expect(result.current.retry()).rejects.toThrow('模板回执已变化')
    act(() => { result.current.ingest(studioEnvelope({ storage_revision: 7 }), true) })
    expect(result.current.error).toBeNull()
    expect(result.current.dirty).toBe(false)
    act(() => { result.current.edit('新编辑', rename('载入后编辑')) })
    await act(async () => { await result.current.flush() })
    expect(command.mock.calls[0][0]).toMatchObject({ expected_storage_revision: 7, project: { name: '载入后编辑' } })
  })
})
