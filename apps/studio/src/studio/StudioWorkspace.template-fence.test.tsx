/**
 * 挂载正式 Workspace 与真实 AuthoringSaveController，验证向导父 callback 的发送前边界。
 * 只把向导视图替换为回调探针；保存、重连 effect、CAS 回执与命令调度均不替换。
 * gateway 和路径均为合成内存数据，不读写工程或媒体。
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import type { AvEnhanceV27WizardProps } from './AvEnhanceV27Wizard'
import type { AvEnhanceV27PrepareRequestWire, AvEnhanceV27TemplatePreviewEnvelope, StatusEnvelope, StudioCommand } from './contracts'
import type { StudioGateway } from './gateway'
import { failedDetailEnvelope, failedStatusEnvelope, studioEnvelope } from './test-fixtures'
import overlapExample from './__fixtures__/overlap-preview.json'
import { parseOverlapFullEnvelope, type OverlapFullEnvelope, type OverlapFullIntent, type OverlapFullRequest } from './chapter-overlap-contracts'

const probe = vi.hoisted(() => ({ props: null as AvEnhanceV27WizardProps | null }))
vi.mock('./AvEnhanceV27Wizard', () => ({
  AvEnhanceV27Wizard: (props: AvEnhanceV27WizardProps) => { probe.props = props; return null },
}))

afterEach(() => { cleanup(); probe.props = null; window.localStorage.clear(); vi.restoreAllMocks() })

function wizard(): AvEnhanceV27WizardProps {
  if (!probe.props) throw new Error('Workspace 尚未挂载向导父回调')
  return probe.props
}

const prepare: AvEnhanceV27PrepareRequestWire = {
  profile_version: '2.7.0', project_path: 'C:\\synthetic\\new.zniku', project_id: 'project.new',
  project_name: '合成新工程', source_mode: 'program', sources: [{ source_path: 'C:\\synthetic\\source.mkv', source_ordinal: 0 }],
  mr: { mode: 'off' },
}

function preparationPreview(): AvEnhanceV27TemplatePreviewEnvelope {
  const snapshot = studioEnvelope().snapshot!
  return {
    contract_version: '0.3.0', profile_version: '2.7.0', phase: 'preparation',
    project: { ...snapshot.project, project_id: prepare.project_id, name: prepare.project_name },
    definitions: snapshot.definitions,
    profile: { profile_version: '2.7.0', phase: 'preparation', status: 'preparation-compatible', compatible: true, diagnostics: [] },
    plan: { source_count: 1, chapter_count: 0, leaf_count: 0, mr_mode: 'off', preparation_run_id: null,
      effective_video_artifact_ids: [], chapters: [], manual_stages: [], output_target_path: null, output_directory_to_create: null },
    creator: { analyzed: false, sources: [], estimated_step_count: 2, estimated_steps: '预计 2 个步骤' },
  }
}

function overlapIntent(): OverlapFullIntent {
  const preview = parseOverlapFullEnvelope(overlapExample)
  return { contract_version: '0.3.2', processing: preview.processing, preparation_run_id: preview.preparation_run_id,
    publication: { output_root: 'C:\\synthetic', title: 'Synthetic', year: '2026', overwrite: false } }
}
function overlapEnvelope(request: OverlapFullRequest): OverlapFullEnvelope {
  const graph = studioEnvelope().snapshot!.project.graph
  return { ...parseOverlapFullEnvelope(overlapExample), project_session_id: request.project_session_id,
    storage_revision: request.expected_storage_revision, preparation_run_id: request.preparation_run_id,
    processing: request.processing, node_count: graph.nodes.length, edge_count: graph.edges.length }
}
function overlapGateway(): StudioGateway {
  return { inspect: vi.fn(async () => studioEnvelope()), command: vi.fn(), inspectRun: vi.fn(), inspectLog: vi.fn(), inspectReadiness: vi.fn(),
    listRuns: vi.fn(async () => ({ contract_version: '0.3.0' as const, run_summaries: [], next_run_cursor: null })),
    previewAvEnhanceV27: vi.fn(), previewOverlapProcessing: vi.fn(async (request) => ({ ...request, status: 'pending_real_acceptance' as const })),
    previewOverlap: vi.fn(async (request) => overlapEnvelope(request)),
    expandOverlap: vi.fn(async (request) => ({ ...studioEnvelope(), project_session_id: request.project_session_id, storage_revision: request.expected_storage_revision + 1 })),
  }
}

describe('Workspace 新候选 preview 与 mutation fences', () => {
  it('停止准备独立于已占用的普通命令互斥，不发送abandon或伪造终态', async () => {
    const gateway = overlapGateway()
    let release!: (status: StatusEnvelope) => void
    gateway.command = vi.fn(() => new Promise<StatusEnvelope>((resolve) => { release = resolve }))
    gateway.cancelPreparedSource = vi.fn(async () => studioEnvelope())
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<App gateway={gateway} />)
    await waitFor(() => expect(wizard().currentSnapshot).not.toBeNull())
    let pending!: Promise<string | null>
    await act(async () => { pending = wizard().onStartPreparationRun!() })
    await waitFor(() => expect(gateway.command).toHaveBeenCalled())
    await act(async () => { await wizard().onCancelPreparationRun!('00000000-0000-4000-8000-000000000041') })
    expect(gateway.cancelPreparedSource).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.4', project_session_id: studioEnvelope().project_session_id,
      run_id: '00000000-0000-4000-8000-000000000041' })
    expect(gateway.command).not.toHaveBeenCalledWith(expect.objectContaining({ operation: 'abandon_run' }))
    await act(async () => { release(studioEnvelope()); await pending })
  })
  it('向导查看失败分析精确加载该 Run 的问题并定位失败步骤，不新建 Run 或提交外部产物', async () => {
    const failed = failedDetailEnvelope('interrupted'), status = failedStatusEnvelope('interrupted')
    const raw = 'E_RUNNER_VALIDATION_REJECTED: E_AV27_SOURCE_FPS_AMBIGUOUS: Source 全片 cadence 置信度不足'
    const detail = { ...failed, run: { ...failed.run, state: 'running' as const, ended_at: null, error: null,
      node_runs: failed.run.node_runs.map((item) => item.state === 'failed' ? { ...item, error: { reason: 'validation_failed' as const, message: raw } } : item) } }
    const gateway: StudioGateway = { ...overlapGateway(),
      inspect: vi.fn(async () => ({ ...status, run_summaries: status.run_summaries.map((item) => ({ ...item,
        state: 'running' as const, ended_at: null, error: null, requires_operator_action: true })) })),
      inspectRun: vi.fn(async () => detail),
    }
    render(<App gateway={gateway} />)
    await waitFor(() => expect(wizard().currentSnapshot).not.toBeNull())
    await userEvent.click(screen.getByRole('button', { name: '关闭工程首页' }))
    await act(async () => { await wizard().onOpenAnalysisProblems!(detail.run.run_id) })
    expect(gateway.inspectRun).toHaveBeenCalledWith(detail.run.run_id)
    expect(screen.getByRole('tab', { name: /^问题/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByText('原片帧率或时间轴未通过检查').length).toBeGreaterThan(0)
    expect(screen.getByLabelText('test.transform 节点')).toHaveClass('is-selected')
    expect(wizard().analysisProblems).toEqual({ run_id: detail.run.run_id,
      problems: [{ label: 'test.transform', error: { reason: 'validation_failed', message: raw } }] })
    expect(gateway.command).not.toHaveBeenCalled()
    expect(gateway.inspectReadiness).not.toHaveBeenCalled()
  })

  it('仅通过对应独立 endpoint 写入，确认后不再复用旧 preview', async () => {
    const gateway = overlapGateway()
    render(<App gateway={gateway} />)
    await waitFor(() => expect(wizard().currentSnapshot).not.toBeNull())
    const intent = overlapIntent()
    await act(async () => { await wizard().onPreviewOverlap!(intent) })
    let result = false
    await act(async () => { result = await wizard().onExpandOverlap!(intent) })
    expect(result).toBe(true)
    expect(gateway.expandOverlap).toHaveBeenCalledTimes(1)
    expect(gateway.command).not.toHaveBeenCalled()
    await act(async () => { result = await wizard().onExpandOverlap!(intent) })
    expect(result).toBe(false)
    expect(gateway.expandOverlap).toHaveBeenCalledTimes(1)
  })
  it('参数变化不能复用已经展示的 preview', async () => {
    const gateway = overlapGateway()
    render(<App gateway={gateway} />)
    await waitFor(() => expect(wizard().currentSnapshot).not.toBeNull())
    const intent = overlapIntent()
    await act(async () => { await wizard().onPreviewOverlap!(intent) })
    let result = true
    await act(async () => { result = await wizard().onExpandOverlap!({ ...intent, processing: { ...intent.processing, settings: { ...intent.processing.settings, leaf_max_minutes: 6 } } }) })
    expect(result).toBe(false)
    expect(gateway.expandOverlap).not.toHaveBeenCalled()
  })
  it('连接变化后迟到的 full preview 不恢复确认权限', async () => {
    const gateway = overlapGateway()
    let resolve!: (value: OverlapFullEnvelope) => void
    let sent!: OverlapFullRequest
    gateway.previewOverlap = vi.fn(async (request: OverlapFullRequest) => { sent = request; return new Promise<OverlapFullEnvelope>((done) => { resolve = done }) })
    render(<App gateway={gateway} />)
    await waitFor(() => expect(wizard().currentSnapshot).not.toBeNull())
    const intent = overlapIntent()
    let pending!: Promise<unknown>
    act(() => { pending = wizard().onPreviewOverlap!(intent).then((value) => value, (error: unknown) => error) })
    await waitFor(() => expect(gateway.previewOverlap).toHaveBeenCalledTimes(1))
    act(() => wizard().onReconnect!())
    await waitFor(() => expect(wizard().connectionEpoch).toBe(1))
    let result: unknown
    await act(async () => { resolve(overlapEnvelope(sent)); result = await pending })
    expect(result).toBeInstanceOf(Error)
    await act(async () => { expect(await wizard().onExpandOverlap!(intent)).toBe(false) })
    expect(gateway.expandOverlap).not.toHaveBeenCalled()
  })
})

describe('Workspace 向导等待保存期间的连接 fence', () => {
  it.each([
    ['create', 'offline'], ['create', 'reconnected'],
    ['run', 'offline'], ['run', 'reconnected'],
    ['create', 'stable'], ['run', 'stable'],
  ] as const)('%s 保存挂起后 %s：只允许未中断的对照发送后续命令', async (operation, recovery) => {
    const user = userEvent.setup()
    let current = studioEnvelope()
    let offline = false
    let resolveSave: ((next: StatusEnvelope) => void) | null = null
    let saveRequest: Extract<StudioCommand, { operation: 'save_project' }> | null = null
    const command = vi.fn<StudioGateway['command']>(async (value) => {
      // 稳定连接对照允许到达本哨兵，但不实现运行或建项；断线场景必须连这里都不能到达。
      if (value.operation !== 'save_project') throw new Error('测试哨兵只记录发送，不实现创建或运行')
      saveRequest = value
      return new Promise<StatusEnvelope>((resolve) => { resolveSave = resolve })
    })
    const inspect = vi.fn<StudioGateway['inspect']>(async () => {
      if (offline) throw new Error('Failed to fetch')
      return current
    })
    const gateway: StudioGateway = {
      command, inspect,
      listRuns: vi.fn(async () => ({ contract_version: '0.3.0' as const, run_summaries: [], next_run_cursor: null })),
      inspectRun: vi.fn(), inspectReadiness: vi.fn(), inspectLog: vi.fn(),
      previewAvEnhanceV27: vi.fn(async () => preparationPreview()),
    }
    render(<App gateway={gateway} />)
    await user.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    fireEvent.click(await screen.findByLabelText('test.transform 节点'))
    await user.click(screen.getByText('显示设置', { selector: 'summary' }))
    const alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '等待保存的别名' } })
    fireEvent.blur(alias)

    if (operation === 'create') {
      await act(async () => { await wizard().onPreview({ action: 'prepare', request: prepare }) })
    }
    // 先注册结果观察，避免故意触发的失败 Promise 成为 unhandled rejection。
    let pending!: Promise<unknown>
    act(() => {
      const action = operation === 'create' ? wizard().onCreate(prepare) : wizard().onStartPreparationRun!()
      pending = Promise.resolve(action).then((value) => ({ value }), (error: unknown) => ({ error }))
    })
    await waitFor(() => expect(command).toHaveBeenCalledTimes(1))
    expect(command.mock.calls[0]![0].operation).toBe('save_project')
    expect(resolveSave).not.toBeNull()

    // 不替换 gateway 对象，原 AuthoringSaveController 和 deferred save 必须仍然存活。
    if (recovery !== 'stable') {
      offline = true
      act(() => { wizard().onReconnect!() })
      await waitFor(() => expect(wizard().serviceUnavailable).toBe(true))
    }
    if (recovery === 'reconnected') {
      offline = false
      act(() => { wizard().onReconnect!() })
      await waitFor(() => {
        expect(wizard().serviceUnavailable).toBe(false)
        expect(wizard().reconnecting).toBe(false)
      })
    }
    const sent = saveRequest! as Extract<StudioCommand, { operation: 'save_project' }>
    current = { ...current, snapshot: { ...current.snapshot!, project: sent.project },
      studio_state: sent.studio_state, storage_revision: sent.expected_storage_revision + 1 }
    let result: unknown
    await act(async () => {
      ;(resolveSave! as (next: StatusEnvelope) => void)(current)
      result = await pending
    })
    if (recovery === 'stable') {
      // 防止测试因其他门禁提前拒绝而假通过：同一 deferred-save 场景未中断时确实发送。
      expect(command).toHaveBeenCalledTimes(2)
      expect(command.mock.calls[1]![0].operation).toBe(operation === 'create' ? 'create_av_enhance_v27' : 'run_all')
      return
    }
    expect(command).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('节点别名')).toHaveValue('等待保存的别名')
    if (operation === 'create') expect(result).toEqual({ value: false })
    else expect(result).toEqual({ error: expect.objectContaining({ message: expect.stringContaining('未启动素材分析') }) })
    expect(gateway.previewAvEnhanceV27).toHaveBeenCalledTimes(operation === 'create' ? 1 : 0)
  })
})
