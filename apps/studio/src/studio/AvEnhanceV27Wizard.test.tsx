import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AvEnhanceV27Wizard } from './AvEnhanceV27Wizard'
import { StudioGatewayError } from './gateway'
import { HostBridgeError } from './host-bridge'
import type {
  AvEnhanceV27ExpandRequestWire,
  AvEnhanceV27PrepareRequestWire,
  AvEnhanceV27PublicationPreviewEnvelope,
  AvEnhanceV27PublicationPreviewRequestWire,
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
      output_directory_to_create: null,
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
    onPreviewPublication: vi.fn(async (request: AvEnhanceV27PublicationPreviewRequestWire): Promise<AvEnhanceV27PublicationPreviewEnvelope> => ({
      contract_version: '0.3.0', layout: request.request.layout ?? 'direct', resolved_output_root: 'D:\\Library',
      output_directory: 'D:\\Library', will_create_directory: false,
    })),
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
  await user.type(screen.getByLabelText('片名'), 'Movie')
  await user.type(screen.getByLabelText('年份'), '2026')
  await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
  await user.click(screen.getByRole('button', { name: '下一步：分析' }))
}

async function reachResumeAnalysis(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText('片名'), 'Movie')
  await user.type(screen.getByLabelText('年份'), '2026')
  await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
  await user.click(screen.getByRole('button', { name: '下一步：分析' }))
  await user.click(screen.getByRole('button', { name: /分析记录 1/ }))
}

describe('AVEnhanceFlow v2.7 创作者向导', () => {
  it('专用数据父目录只在显式开始分析后随media_basename交给创建命令', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const onPickDataDirectory = vi.fn(async () => 'E:\\archive-parent')
    render(<AvEnhanceV27Wizard {...props} onPickDataDirectory={onPickDataDirectory} />)
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    expect(screen.getByText('专用磁盘父目录：E:\\archive-parent')).toBeVisible()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
    await reachAnalysis(user)
    expect(props.onCreate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith(expect.objectContaining({ project_id: 'project.hidden-session-id' }), {
      data_parent_directory: 'E:\\archive-parent', media_basename: 'Movie (2026)',
    }))
    expect(props.onStartPreparationRun).toHaveBeenCalledTimes(1)
  })

  it('数据父目录取消后仍使用默认位置，恢复默认不会把旧磁盘带入创建', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const onPickDataDirectory = vi.fn<() => Promise<string | null>>().mockResolvedValueOnce(null).mockResolvedValueOnce('E:\\old-choice')
    render(<AvEnhanceV27Wizard {...props} onPickDataDirectory={onPickDataDirectory} />)
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    expect(screen.getByText('使用工程旁默认位置')).toBeVisible()
    expect(props.onCreate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    expect(screen.getByText('专用磁盘父目录：E:\\old-choice')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    await reachAnalysis(user)
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith(expect.anything(), { media_basename: 'Movie (2026)' }))
  })

  it.each(['edit', 'close'] as const)('数据父目录%s后迟到选择不改变当前表单或创建工程', async (action) => {
    const user = userEvent.setup()
    const props = baseProps()
    let finish!: (value: string | null) => void
    const onPickDataDirectory = vi.fn<() => Promise<string | null>>(() => new Promise((resolve) => { finish = resolve }))
    const { rerender } = render(<AvEnhanceV27Wizard {...props} onPickDataDirectory={onPickDataDirectory} />)
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    if (action === 'edit') await user.type(screen.getByLabelText('工程名称'), '已变化')
    else {
      rerender(<AvEnhanceV27Wizard {...props} open={false} onPickDataDirectory={onPickDataDirectory} />)
      rerender(<AvEnhanceV27Wizard {...props} open onPickDataDirectory={onPickDataDirectory} />)
    }
    await act(async () => finish('E:\\late-disk'))
    expect(screen.getByText('使用工程旁默认位置')).toBeVisible()
    expect(screen.queryByText(/late-disk/)).not.toBeInTheDocument()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
  })

  it('以显式五步动作绑定本次分析 Run，并只展示 Python creator 摘要', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    expect(screen.getByRole('dialog', { name: 'AVEnhanceFlow v2.7.0 创作者向导' })).toBeVisible()
    expect(screen.queryByText('project.hidden-session-id')).not.toBeInTheDocument()
    await reachAnalysis(user)
    expect(props.onPreviewPublication).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.0', request: {
      output_root: 'D:\\Library', title: 'Movie', year: '2026', overwrite: false, layout: 'direct',
    } })
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(screen.queryByText('D:\\Media\\source.mkv')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledTimes(1))
    expect(props.onCreate).toHaveBeenCalledWith(expect.objectContaining({
      project_id: 'project.hidden-session-id', sources: [{ source_path: 'D:\\Media\\source.mkv', source_ordinal: 0 }],
    }), { media_basename: 'Movie (2026)' })
    rerender(<AvEnhanceV27Wizard {...props} runSummaries={[runSummary()]} />)
    await waitFor(() => expect(props.onPreview).toHaveBeenCalledWith({ action: 'expand', request: expect.objectContaining({ preparation_run_id: runId }) }))
    expect(await screen.findByText('117.7 MiB · Matroska')).toBeVisible()
    expect(screen.getByText('30000/1001 fps（29.970）')).toBeVisible()
    expect(screen.getByText('1,801 帧')).toBeVisible()
    expect(screen.getByText('AAC · 2 声道 · 48 kHz · jpn')).toBeVisible()
    expect(screen.getByText('D:\\Library\\Movie (2026) - Enhanced FI59p94 2160p.mkv')).toBeVisible()
    expect(screen.queryByText('artifact.internal')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    expect(props.onExpand).toHaveBeenCalledWith(expect.objectContaining({ preparation_run_id: runId }))
  })

  it('目录检查失败留在设置，开始分析前明确阻断且不创建工程', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onPreviewPublication.mockRejectedValueOnce(new StudioGatewayError('E_AV27_NAMING_PARENT', {
      code: 'E_AV27_NAMING_PARENT', serviceMessage: '所选目录不可访问，请选择可写目录。',
    }))
    render(<AvEnhanceV27Wizard {...props} />)
    await reachAnalysis(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('输出位置未通过检查')
    expect(screen.getByRole('heading', { name: '设置成片目标' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /开始分析素材/ })).not.toBeInTheDocument()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(await screen.findByRole('button', { name: /开始分析素材/ })).toBeEnabled()
    expect(props.onPreviewPublication).toHaveBeenCalledTimes(2)
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it('按片名整理仅传递意图并显示 Python 目录，切换后复用 exact 分析且确认前不 mutation', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onPreviewPublication.mockImplementation(async (request) => ({
      contract_version: '0.3.0', layout: request.request.layout ?? 'direct', resolved_output_root: 'D:\\Library',
      output_directory: request.request.layout === 'title_subdirectory' ? 'D:\\Library\\服务端片名 (2026)' : 'D:\\Library',
      will_create_directory: request.request.layout === 'title_subdirectory',
    }))
    props.onPreview.mockImplementation(async (request) => {
      const result = preview('expanded')
      return request.action === 'expand' && request.request.publication.layout === 'title_subdirectory'
        ? { ...result, plan: { ...result.plan, output_target_path: 'D:\\Library\\服务端片名 (2026)\\Python 成片.mkv', output_directory_to_create: 'D:\\Library\\服务端片名 (2026)' } }
        : result
    })
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} runSummaries={[runSummary()]} />)
    expect(screen.getByLabelText('按片名创建子文件夹')).not.toBeChecked()
    await reachResumeAnalysis(user)
    await user.click(screen.getByRole('button', { name: '返回设置' }))
    await user.click(screen.getByLabelText('按片名创建子文件夹'))
    expect(screen.queryByRole('button', { name: '确认并创建工作流' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(await screen.findByText('D:\\Library\\服务端片名 (2026)', { exact: true })).toBeVisible()
    expect(screen.queryByText('D:\\Library\\Movie (2026)', { exact: true })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '生成工作流预览' }))
    expect(await screen.findByText('D:\\Library\\服务端片名 (2026)\\Python 成片.mkv')).toBeVisible()
    expect(screen.getByText(/将在开始处理后的输出步骤创建文件夹：D:/)).toBeVisible()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    expect(props.onExpand).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      preparation_run_id: runId, publication: expect.objectContaining({ layout: 'title_subdirectory' }),
    }))
  })

  it.each(['cancel', 'change', 'close'] as const)('输出检查 %s 后拒绝迟到结果，不开始分析或创建目录', async (action) => {
    const user = userEvent.setup()
    const props = baseProps()
    let resolveOld!: (value: AvEnhanceV27PublicationPreviewEnvelope) => void
    props.onPreviewPublication.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    const options = { ...props, mode: 'resume' as const, currentSnapshot: snapshot(), runSummaries: [runSummary()] }
    const { rerender } = render(<AvEnhanceV27Wizard {...options} />)
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(screen.getByRole('button', { name: '正在检查输出位置…' })).toBeDisabled()
    if (action === 'cancel') await user.click(screen.getByRole('button', { name: '取消检查' }))
    else if (action === 'change') await user.click(screen.getByLabelText('按片名创建子文件夹'))
    else {
      rerender(<AvEnhanceV27Wizard {...options} open={false} />)
      rerender(<AvEnhanceV27Wizard {...options} open />)
    }
    await act(async () => { resolveOld({ contract_version: '0.3.0', layout: 'direct', resolved_output_root: 'D:\\Late', output_directory: 'D:\\Late', will_create_directory: false }) })
    expect(screen.getByRole('heading', { name: '设置成片目标' })).toBeVisible()
    expect(screen.queryByText('D:\\Late')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一步：分析' })).toBeEnabled()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
  })

  it('resume 只展示人类时间供明确选择，不显示或猜测 Run ID', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} currentProjectPath="D:\\Projects\\existing.zniku" currentProjectId="project.av27" currentProjectName="Existing" runSummaries={[runSummary()]} />)
    expect(screen.getByRole('heading', { name: '设置成片目标' })).toBeVisible()
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
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
    await user.selectOptions(screen.getByLabelText('素材组织方式'), 'pre_chaptered')
    await user.click(screen.getByRole('button', { name: /选择全部章节视频/ }))
    await user.clear(screen.getByLabelText('第 1 章名称'))
    await user.type(screen.getByLabelText('第 1 章名称'), '前篇')
    await user.clear(screen.getByLabelText('第 2 章名称'))
    await user.type(screen.getByLabelText('第 2 章名称'), '后篇')
    await user.click(screen.getByRole('button', { name: '选择 .zniku 保存位置' }))
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    await user.click(screen.getByRole('button', { name: '下一步：设置' }))
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith(expect.objectContaining({
      source_mode: 'pre_chaptered',
      sources: [
        { source_path: 'D:\\Media\\b.mkv', source_ordinal: 0, chapter_label: '前篇' },
        { source_path: 'D:\\Media\\a.mkv', source_ordinal: 1, chapter_label: '后篇' },
      ],
    }), { media_basename: 'Movie (2026)' }))
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
    await user.type(screen.getByLabelText('工程名称'), '新')
    resolveLate(['D:\\Media\\late.mkv'])
    await Promise.resolve()
    expect(screen.queryByText('late.mkv')).not.toBeInTheDocument()
    expect(screen.getByText('尚未选择')).toBeVisible()
  })

  it.each(['source', 'project', 'output'] as const)('%s 选择窗口忙碌统一提示已有窗口，保留高级原文且不发起分析', async (kind) => {
    const user = userEvent.setup()
    const props = baseProps()
    const rawMessage = 'E_HOST_BRIDGE_DIALOG_BUSY: synthetic native chooser is active'
    const error = new HostBridgeError(rawMessage, { code: 'E_HOST_BRIDGE_DIALOG_BUSY', httpStatus: 409 })
    const pick = kind === 'source' ? props.onPickSources : kind === 'project' ? props.onPickProjectPath : props.onPickOutputDirectory
    pick.mockRejectedValueOnce(error)
    render(<AvEnhanceV27Wizard {...props} {...(kind === 'output' ? { mode: 'resume', currentSnapshot: snapshot() } : {})} />)
    await user.click(screen.getByRole('button', { name: kind === 'source' ? /选择视频素材/ : kind === 'project' ? '选择 .zniku 保存位置' : '选择成片文件夹' }))
    const message = '已有文件/文件夹选择窗口打开，请先完成或取消；它可能在浏览器后面。'
    expect(await screen.findByText(message)).toBeVisible()
    expect(screen.getByText(rawMessage)).not.toBeVisible()
    await user.click(screen.getByText('高级 → 选择窗口原始详情'))
    expect(screen.getByText(rawMessage)).toBeVisible()
    expect(pick).toHaveBeenCalledTimes(1)
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
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
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))

    const records = screen.getAllByRole('button', { name: /分析记录 [12]/ })
    expect(records).toHaveLength(2)
    expect(records[0]).not.toHaveAccessibleName(records[1]!.textContent ?? '')
    expect(records[0]!.textContent).not.toBe(records[1]!.textContent)
    expect(screen.queryByText(runId)).not.toBeInTheDocument()
    await user.click(records[0]!)
    expect(await screen.findByRole('alert')).toHaveTextContent('这条记录不是可扩展的素材分析')
    await user.click(screen.getByRole('button', { name: '改选分析记录' }))
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
    await user.type(screen.getByLabelText('片名'), 'Old title')
    await user.type(screen.getByLabelText('年份'), '2026')
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    expect(screen.getByText('已选择输出目录：D:\\Library')).toBeVisible()
    rerender(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} open={false} />)
    rerender(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} open />)
    expect(screen.getByLabelText('片名')).toHaveValue('')
    expect(screen.getByLabelText('年份')).toHaveValue('')
    expect(screen.queryByText('已选择输出目录：D:\\Library')).not.toBeInTheDocument()
  })

  it.each(['create', 'resume'] as const)('%s 命名目录未就绪保留 completed 分析，同 Run 显式重试不重建工程或分析', async (mode) => {
    const user = userEvent.setup()
    const props = baseProps()
    const serviceMessage = '输出目录现在不可访问：D:\\Library'
    const raw = `E_AV27_NAMING_PARENT: ${serviceMessage}`
    let expansionCalls = 0
    let resolveRetry!: (result: AvEnhanceV27TemplatePreviewEnvelope) => void
    props.onPreview.mockImplementation(async (request) => {
      if (request.action === 'prepare') return preview('preparation')
      expansionCalls += 1
      if (expansionCalls === 1) throw new StudioGatewayError(raw, { code: 'E_AV27_NAMING_PARENT', serviceMessage })
      return new Promise((resolve) => { resolveRetry = resolve })
    })
    const reveal = vi.fn(async (_path: string) => undefined)
    const options = { ...props, mode, onRevealOutputDirectory: reveal,
      currentSnapshot: mode === 'resume' ? snapshot() : null,
      runSummaries: mode === 'resume' ? [runSummary()] : [] }
    const { rerender } = render(<AvEnhanceV27Wizard {...options} />)
    if (mode === 'create') {
      await reachAnalysis(user)
      await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
      await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledTimes(1))
      rerender(<AvEnhanceV27Wizard {...options} runSummaries={[runSummary()]} />)
    } else await reachResumeAnalysis(user)
    expect(await screen.findByText('素材分析已完成，输出位置尚未就绪')).toBeVisible()
    expect(screen.getByText(serviceMessage, { exact: true })).toBeVisible()
    expect(screen.getByText(raw, { exact: true })).not.toBeVisible()
    expect(screen.queryByText('正在从这次准确结果生成工作流预览。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '确认并创建工作流' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新分析' })).not.toBeInTheDocument()
    rerender(<AvEnhanceV27Wizard {...options} runSummaries={[{ ...runSummary() }]} />)
    expect(expansionCalls).toBe(1)
    await user.click(screen.getByRole('button', { name: '打开所选输出文件夹' }))
    expect(reveal).toHaveBeenCalledExactlyOnceWith('D:\\Library')
    const retry = screen.getByRole('button', { name: '重新检查输出位置' })
    fireEvent.click(retry)
    fireEvent.click(retry)
    expect(expansionCalls).toBe(2)
    expect(props.onCreate).toHaveBeenCalledTimes(mode === 'create' ? 1 : 0)
    expect(props.onStartPreparationRun).toHaveBeenCalledTimes(mode === 'create' ? 1 : 0)
    expect(props.onExpand).not.toHaveBeenCalled()
    await act(async () => { resolveRetry(preview('expanded', runId)) })
    expect(await screen.findByRole('heading', { name: '确认工作流' })).toBeVisible()
    expect(props.onPreview.mock.calls.filter(([request]) => request.action === 'expand').every(([request]) => request.action === 'expand' && request.request.preparation_run_id === runId)).toBe(true)
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    expect(props.onExpand).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ preparation_run_id: runId }))
  })

  it('未知预览错误不解析 message 猜测命名原因，停止假等待并允许同分析重试', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const raw = '未知服务错误内容里含 E_AV27_NAMING_PARENT 但不是该错误码'
    props.onPreview.mockRejectedValueOnce(new StudioGatewayError(raw, { code: 'E_OTHER_FAILURE' }))
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} runSummaries={[runSummary()]} />)
    await reachResumeAnalysis(user)
    expect(await screen.findByText('素材分析已完成，工作流预览尚未就绪')).toBeVisible()
    expect(screen.getByText(raw, { exact: true })).not.toBeVisible()
    expect(screen.queryByRole('button', { name: '重新检查输出位置' })).not.toBeInTheDocument()
    expect(screen.queryByText('正在从这次准确结果生成工作流预览。')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '修改设置' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '重新生成工作流预览' }))
    expect(await screen.findByRole('heading', { name: '确认工作流' })).toBeVisible()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it.each(['E_AV27_NAMING_ROOT', 'E_AV27_NAMING_EXISTS', 'E_AV27_NAMING_TARGET', 'E_AV27_NAMING_SOURCE'])('%s 归到输出设置，保留分析且可显式调整覆盖策略', async (code) => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onPreview.mockRejectedValueOnce(new StudioGatewayError(code, { code, serviceMessage: '服务端给出的输出问题' }))
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} runSummaries={[runSummary()]} />)
    await reachResumeAnalysis(user)
    expect(await screen.findByText('素材分析已完成，输出位置尚未就绪')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '修改设置' }))
    expect(screen.getByLabelText('允许覆盖发布目标')).not.toBeChecked()
    await user.click(screen.getByLabelText('允许覆盖发布目标'))
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    await user.click(screen.getByRole('button', { name: '生成工作流预览' }))
    expect(props.onPreview).toHaveBeenLastCalledWith({ action: 'expand', request: expect.objectContaining({
      preparation_run_id: runId, publication: expect.objectContaining({ overwrite: true }),
    }) })
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it('输出根打开只能在显式选择后点击触发，取消与系统动作失败不创建目录/工程或生成 canonical 路径', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const pick = vi.fn<() => Promise<string | null>>().mockResolvedValueOnce(null).mockResolvedValueOnce('D:\\Library')
    const reveal = vi.fn(async (_path: string) => { throw new Error('native unavailable') })
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} onPickOutputDirectory={pick} onRevealOutputDirectory={reveal} />)
    expect(screen.getByText('引导式视频工作流')).toBeVisible()
    expect(screen.getByText(/默认直接保存到所选目录，无需预先创建片名文件夹/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '打开所选输出文件夹' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    expect(screen.queryByRole('button', { name: '打开所选输出文件夹' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    expect(reveal).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '打开所选输出文件夹' }))
    expect(reveal).toHaveBeenCalledExactlyOnceWith('D:\\Library')
    expect(await screen.findByRole('alert')).toHaveTextContent('无法打开所选输出文件夹')
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it('关闭重开后迟到目录错误不能污染新设置，迟到同 Run preview 也不能确认旧工作流', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    let rejectOld!: (reason: Error) => void
    props.onPreview.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOld = reject }))
    const options = { ...props, mode: 'resume' as const, currentSnapshot: snapshot(), runSummaries: [runSummary()] }
    const { rerender } = render(<AvEnhanceV27Wizard {...options} />)
    await reachResumeAnalysis(user)
    expect(props.onPreview).toHaveBeenCalledTimes(1)
    rerender(<AvEnhanceV27Wizard {...options} open={false} />)
    rerender(<AvEnhanceV27Wizard {...options} open />)
    await reachResumeAnalysis(user)
    expect(await screen.findByRole('heading', { name: '确认工作流' })).toBeVisible()
    await act(async () => { rejectOld(new StudioGatewayError('迟到的命名错误', { code: 'E_AV27_NAMING_PARENT', serviceMessage: '不能进入新页面的路径' })) })
    expect(screen.queryByText('不能进入新页面的路径')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认并创建工作流' })).toBeEnabled()
    expect(props.onExpand).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
  })

  it('组件卸载后迟到 preparation 检查不能创建 Project 或启动 Run', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    let resolveOld!: (result: AvEnhanceV27TemplatePreviewEnvelope) => void
    props.onPreview.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    const { unmount } = render(<AvEnhanceV27Wizard {...props} />)
    await reachAnalysis(user)
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    unmount()
    await act(async () => { resolveOld(preview('preparation')) })
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })
})
