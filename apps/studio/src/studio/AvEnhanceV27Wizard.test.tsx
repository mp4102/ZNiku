import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AvEnhanceV27Wizard } from './AvEnhanceV27Wizard'
import type {
  AvEnhanceV27ExpandRequestWire,
  AvEnhanceV27PrepareRequestWire,
  AvEnhanceV27TemplatePreviewEnvelope,
  AvEnhanceV27TemplatePreviewRequestWire,
  ProjectSnapshotWire,
  RunSummaryWire,
} from './contracts'

afterEach(cleanup)

const runId = '00000000-0000-4000-8000-000000000027'
const secondRunId = '00000000-0000-4000-8000-000000000028'

function runSummary(id = runId, state: RunSummaryWire['state'] = 'completed'): RunSummaryWire {
  return {
    run_id: id, project_id: 'project.av27', target_mode: 'all', selected_targets: [], state,
    node_count: 2,
    state_counts: { pending: state === 'pending' ? 2 : 0, running: 0, waiting_external: 0, completed: state === 'completed' ? 2 : 0, failed: state === 'failed' ? 1 : 0 },
    actionable: state === 'pending', requires_operator_action: false,
    created_at: '2026-09-02T01:00:00Z', started_at: state === 'pending' ? null : '2026-09-02T01:00:01Z',
    ended_at: state === 'pending' ? null : '2026-09-02T01:00:03Z', latest_activity_at: '2026-09-02T01:00:03Z',
    error: state === 'failed' ? { reason: 'execution_error', message: 'synthetic failure' } : null,
  }
}

function snapshot(sourceMode: 'program' | 'pre_chaptered' = 'program'): ProjectSnapshotWire {
  return {
    project: {
      project_id: 'project.av27', name: 'Synthetic AV27',
      graph: { nodes: [{ node_id: 'admission', type_id: 'zniku.avenhance.v27.source_admission', definition_version: '0.2.1', parameters: { source_mode: sourceMode }, ui_position: { x: 320, y: 120 } }], edges: [] },
    },
    definitions: [],
  }
}

function arbitrarySnapshot(): ProjectSnapshotWire {
  return { project: { project_id: 'project.other', name: '其他工程', graph: { nodes: [], edges: [] } }, definitions: [] }
}

function preview(
  phase: 'preparation' | 'expanded',
  preparationRunId = runId,
): AvEnhanceV27TemplatePreviewEnvelope {
  return {
    contract_version: '0.3.0', profile_version: '2.7.0', phase,
    project: {
      project_id: 'project.av27', name: 'Synthetic AV27',
      graph: {
        nodes: [
          { node_id: 'source', type_id: 'zniku.avenhance.v27.source_program', definition_version: '0.2.1', parameters: {}, ui_position: { x: 0, y: 0 } },
          { node_id: 'admission', type_id: 'zniku.avenhance.v27.source_admission', definition_version: '0.2.1', parameters: {}, ui_position: { x: 200, y: 0 } },
        ],
        edges: [],
      },
    },
    definitions: [],
    profile: { profile_version: '2.7.0', phase, status: phase === 'preparation' ? 'preparation-compatible' : 'expanded-compatible', compatible: true, diagnostics: [] },
    plan: {
      source_count: 1, chapter_count: phase === 'expanded' ? 1 : 0, leaf_count: phase === 'expanded' ? 2 : 0,
      mr_mode: 'off', preparation_run_id: phase === 'expanded' ? preparationRunId : null,
      effective_video_artifact_ids: phase === 'expanded' ? ['artifact.internal'] : [],
      chapters: phase === 'expanded' ? [{
        chapter_id: 'chapter-0001', chapter_ordinal: 0, label: '第一章', source_ordinal: 0,
        start_frame: 0, end_frame: 1801, start_time_seconds: '0', end_time_seconds: '1802801/30000',
        start_timecode: '00:00:00.000', end_timecode: '00:01:00.093', leaves: [],
      }] : [],
      manual_stages: phase === 'expanded' ? [
        { stage: 'enhancement', node_count: 2, output_container: '.mov' },
        { stage: 'frame_interpolation', node_count: 1, output_container: '.mov' },
      ] : [],
      output_target_path: phase === 'expanded' ? 'D:\\Library\\Movie (2026) - Enhanced FI59p94 2160p.mkv' : null,
    },
    creator: {
      analyzed: phase === 'expanded',
      sources: phase === 'expanded' ? [{
        source_ordinal: 0, chapter_label: null, display_name: 'source.mkv', size_bytes: 123456789,
        size_label: '117.7 MiB', container: 'Matroska', video_codec: 'HEVC', pixel_format: 'yuv420p10le',
        resolution: '3840 × 2160', frame_rate: '30000/1001 fps（29.970）', duration: '1 分 0 秒', frame_count: '1,801 帧',
        audio_tracks: [{ ordinal: 0, codec: 'AAC', channels: 2, sample_rate: 48000, language: 'jpn', title: null, label: 'AAC · 2 声道 · 48 kHz · jpn' }],
      }] : [],
      estimated_step_count: 2, estimated_steps: '预计 2 个处理步骤',
    },
  }
}

function baseProps() {
  return {
    open: true, mode: 'create' as const, busy: false, currentSnapshot: null,
    currentProjectPath: '', currentProjectId: '', currentProjectName: '',
    runSummaries: [] as ReadonlyArray<RunSummaryWire>, projectIdFactory: () => 'project.hidden-session-id', pickerAvailable: true,
    onPickProjectPath: vi.fn(async () => 'D:\\Projects\\guided.zniku'),
    onPickSources: vi.fn<(_multiple: boolean) => Promise<ReadonlyArray<string> | null>>(async () => ['D:\\Media\\source.mkv']),
    onPickOutputDirectory: vi.fn(async () => 'D:\\Library'), onClose: vi.fn(),
    onPreview: vi.fn(async (request: AvEnhanceV27TemplatePreviewRequestWire) => preview(
      request.action === 'prepare' ? 'preparation' : 'expanded',
      request.action === 'expand' ? request.request.preparation_run_id : runId,
    )),
    onCreate: vi.fn(async (_request: AvEnhanceV27PrepareRequestWire) => true),
    onStartPreparationRun: vi.fn(async () => runId),
    onExpand: vi.fn(async (_request: AvEnhanceV27ExpandRequestWire) => true), onLocateNode: vi.fn(),
  }
}

async function reachAnalysis(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
  await user.click(screen.getByRole('button', { name: '选择 .zniku 保存位置' }))
  await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
  await user.click(screen.getByRole('button', { name: '下一步：设置' }))
  await user.type(screen.getByLabelText('Publication title'), 'Movie')
  await user.type(screen.getByLabelText('Publication year'), '2026')
  await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
  await user.click(screen.getByRole('button', { name: '下一步：分析' }))
}

describe('AVEnhanceFlow v2.7 创作者向导', () => {
  it('以显式五步动作绑定本次分析 Run，并只展示 Python creator 摘要', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    expect(screen.getByRole('dialog', { name: 'AVEnhanceFlow v2.7.0 创作者向导' })).toBeVisible()
    expect(screen.queryByText('project.hidden-session-id')).not.toBeInTheDocument()
    await reachAnalysis(user)
    expect(screen.queryByText('D:\\Media\\source.mkv')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledTimes(1))
    expect(props.onCreate).toHaveBeenCalledWith(expect.objectContaining({
      project_id: 'project.hidden-session-id', sources: [{ source_path: 'D:\\Media\\source.mkv', source_ordinal: 0 }],
    }))
    rerender(<AvEnhanceV27Wizard {...props} runSummaries={[runSummary()]} />)
    await waitFor(() => expect(props.onPreview).toHaveBeenCalledWith({ action: 'expand', request: expect.objectContaining({ preparation_run_id: runId }) }))
    expect(await screen.findByText('117.7 MiB · Matroska')).toBeVisible()
    expect(screen.getByText('30000/1001 fps（29.970）')).toBeVisible()
    expect(screen.getByText('1,801 帧')).toBeVisible()
    expect(screen.getByText('AAC · 2 声道 · 48 kHz · jpn')).toBeVisible()
    expect(screen.queryByText('artifact.internal')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    expect(props.onExpand).toHaveBeenCalledWith(expect.objectContaining({ preparation_run_id: runId }))
  })

  it('resume 只展示人类时间供明确选择，不显示或猜测 Run ID', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} currentProjectPath="D:\\Projects\\existing.zniku" currentProjectId="project.av27" currentProjectName="Existing" runSummaries={[runSummary()]} />)
    expect(screen.getByRole('heading', { name: '设置成片目标' })).toBeVisible()
    await user.type(screen.getByLabelText('Publication title'), 'Movie')
    await user.type(screen.getByLabelText('Publication year'), '2026')
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(screen.queryByText(runId)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /2026.*完成/ }))
    await waitFor(() => expect(props.onPreview).toHaveBeenCalledWith({ action: 'expand', request: expect.objectContaining({ preparation_run_id: runId }) }))
  })

  it('create mode 不因当前打开任意工程而误判为恢复流程', () => {
    const props = baseProps()
    render(<AvEnhanceV27Wizard {...props} currentSnapshot={arbitrarySnapshot()} currentProjectPath="D:\\Projects\\other.zniku" currentProjectId="project.other" currentProjectName="其他工程" />)
    expect(screen.getByRole('heading', { name: '选择要处理的视频' })).toBeVisible()
    expect(screen.queryByText('other.zniku')).not.toBeInTheDocument()
    expect(screen.queryByText('project.other')).not.toBeInTheDocument()
  })

  it('pre_chaptered 多素材保持 picker 顺序与连续 ordinal', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onPickSources.mockResolvedValue(['D:\\Media\\b.mkv', 'D:\\Media\\a.mkv'])
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await user.selectOptions(screen.getByLabelText('模板 Source mode'), 'pre_chaptered')
    await user.click(screen.getByRole('button', { name: /选择全部章节视频/ }))
    await user.clear(screen.getByLabelText('Source 1 chapter label'))
    await user.type(screen.getByLabelText('Source 1 chapter label'), '前篇')
    await user.clear(screen.getByLabelText('Source 2 chapter label'))
    await user.type(screen.getByLabelText('Source 2 chapter label'), '后篇')
    await user.click(screen.getByRole('button', { name: '选择 .zniku 保存位置' }))
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    await user.click(screen.getByRole('button', { name: '下一步：设置' }))
    await user.type(screen.getByLabelText('Publication title'), 'Movie')
    await user.type(screen.getByLabelText('Publication year'), '2026')
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith(expect.objectContaining({
      source_mode: 'pre_chaptered',
      sources: [
        { source_path: 'D:\\Media\\b.mkv', source_ordinal: 0, chapter_label: '前篇' },
        { source_path: 'D:\\Media\\a.mkv', source_ordinal: 1, chapter_label: '后篇' },
      ],
    })))
    await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledTimes(1))
    rerender(<AvEnhanceV27Wizard {...props} runSummaries={[runSummary()]} />)
    await waitFor(() => {
      const expansion = props.onPreview.mock.calls
        .map(([request]) => request)
        .find((request) => request.action === 'expand')
      expect(expansion).toEqual({
        action: 'expand',
        request: expect.not.objectContaining({ chapter_selector: expect.anything() }),
      })
    })
  })

  it('picker 取消、拒绝与迟到选择都不修改当前表单', async () => {
    const user = userEvent.setup()
    let resolveLate!: (value: ReadonlyArray<string> | null) => void
    const props = baseProps()
    props.onPickSources.mockImplementationOnce(async () => null)
      .mockImplementationOnce(async () => { throw new Error('选择器暂时不可用') })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveLate = resolve }))
    render(<AvEnhanceV27Wizard {...props} />)
    await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
    expect(screen.getByText('尚未选择')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('选择器暂时不可用')
    await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
    await user.type(screen.getByLabelText('模板 Project name'), '新')
    resolveLate(['D:\\Media\\late.mkv'])
    await Promise.resolve()
    expect(screen.queryByText('late.mkv')).not.toBeInTheDocument()
    expect(screen.getByText('尚未选择')).toBeVisible()
  })

  it('失败后重新分析直接创建新 Run，不读取旧 render closure', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onStartPreparationRun.mockResolvedValueOnce(runId).mockResolvedValueOnce(secondRunId)
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await reachAnalysis(user)
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    rerender(<AvEnhanceV27Wizard {...props} runSummaries={[runSummary(runId, 'failed')]} />)
    expect(await screen.findByText('素材分析没有完成')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '重新分析' }))
    await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledTimes(2))
    expect(props.onPreview).not.toHaveBeenCalledWith(expect.objectContaining({ action: 'expand', request: expect.objectContaining({ preparation_run_id: runId }) }))
  })

  it('同一分钟的完成记录仍可区分，错误绑定由 Python 拒绝后可改选 exact Run', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const first = {
      ...runSummary(runId),
      created_at: '2026-09-02T01:00:01Z',
      started_at: '2026-09-02T01:00:01Z',
      ended_at: '2026-09-02T01:00:03Z',
      latest_activity_at: '2026-09-02T01:00:03Z',
    }
    const second = {
      ...runSummary(secondRunId),
      created_at: '2026-09-02T01:00:02Z',
      started_at: '2026-09-02T01:00:02Z',
      ended_at: '2026-09-02T01:00:04Z',
      latest_activity_at: '2026-09-02T01:00:04Z',
    }
    props.onPreview.mockImplementation(async (request) => {
      if (request.action === 'expand' && request.request.preparation_run_id === runId) {
        throw new Error('这条记录不是可扩展的素材分析')
      }
      return preview(
        request.action === 'prepare' ? 'preparation' : 'expanded',
        request.action === 'expand' ? request.request.preparation_run_id : runId,
      )
    })
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} currentProjectPath="D:\\Projects\\existing.zniku" currentProjectId="project.av27" currentProjectName="Existing" runSummaries={[first, second]} />)
    await user.type(screen.getByLabelText('Publication title'), 'Movie')
    await user.type(screen.getByLabelText('Publication year'), '2026')
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))

    const records = screen.getAllByRole('button', { name: /分析记录 [12]/ })
    expect(records).toHaveLength(2)
    expect(records[0]).not.toHaveAccessibleName(records[1]!.textContent ?? '')
    expect(records[0]!.textContent).not.toBe(records[1]!.textContent)
    expect(screen.queryByText(runId)).not.toBeInTheDocument()
    await user.click(records[0]!)
    expect(await screen.findByRole('alert')).toHaveTextContent('这条记录不是可扩展的素材分析')
    expect(screen.getAllByRole('button', { name: /分析记录 [12]/ })).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: /分析记录 2/ }))
    await waitFor(() => expect(props.onPreview).toHaveBeenCalledWith({
      action: 'expand',
      request: expect.objectContaining({ preparation_run_id: secondRunId }),
    }))
  })

  it('确认页返回设置会使旧预览失效，899 精确帧只进入新的 server preview', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await reachAnalysis(user)
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledTimes(1))
    rerender(<AvEnhanceV27Wizard {...props} runSummaries={[runSummary()]} />)
    expect(await screen.findByRole('heading', { name: '确认工作流' })).toBeVisible()

    await user.click(screen.getByRole('button', { name: '返回设置' }))
    expect(screen.queryByRole('button', { name: '确认并创建工作流' })).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Chapter selector mode'), 'exact_frames')
    await user.type(screen.getByLabelText('Exact chapter frames'), '899')
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(props.onExpand).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '生成工作流预览' }))
    await waitFor(() => expect(props.onPreview).toHaveBeenCalledWith({
      action: 'expand',
      request: expect.objectContaining({
        preparation_run_id: runId,
        chapter_selector: { mode: 'exact_frames', frames: [899] },
      }),
    }))
  })

  it('expand 返回错误 phase 时失败关闭，并允许保留 Preparation 后修改设置', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onPreview.mockImplementation(async () => preview('preparation'))
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await reachAnalysis(user)
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledTimes(1))
    rerender(<AvEnhanceV27Wizard {...props} runSummaries={[runSummary()]} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('工作流分析阶段不一致')
    expect(props.onExpand).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '修改设置' }))
    expect(screen.getByRole('heading', { name: '设置成片目标' })).toBeVisible()
    expect(props.onStartPreparationRun).toHaveBeenCalledTimes(1)
  })

  it('打开和关闭时保持 modal 焦点边界，并用 Escape 恢复触发点', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const closed = { ...props, open: false }
    const { rerender } = render(
      <><button type="button">启动向导</button><AvEnhanceV27Wizard {...closed} /></>,
    )
    const trigger = screen.getByRole('button', { name: '启动向导' })
    trigger.focus()
    rerender(<><button type="button">启动向导</button><AvEnhanceV27Wizard {...props} /></>)
    const close = screen.getByRole('button', { name: '关闭模板向导' })
    expect(close).toHaveFocus()
    const next = screen.getByRole('button', { name: '下一步：处理方案' })
    next.focus()
    await user.tab()
    expect(close).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(props.onClose).toHaveBeenCalledTimes(1)
    rerender(<><button type="button">启动向导</button><AvEnhanceV27Wizard {...closed} /></>)
    expect(trigger).toHaveFocus()
  })

  it('重新打开向导时清除上一次输出设置', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const { rerender } = render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} />)
    await user.type(screen.getByLabelText('Publication title'), 'Old title')
    await user.type(screen.getByLabelText('Publication year'), '2026')
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    expect(screen.getByText('已选择：Library')).toBeVisible()
    rerender(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} open={false} />)
    rerender(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} open />)
    expect(screen.getByLabelText('Publication title')).toHaveValue('')
    expect(screen.getByLabelText('Publication year')).toHaveValue('')
    expect(screen.queryByText('已选择：Library')).not.toBeInTheDocument()
  })
})
