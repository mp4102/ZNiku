import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AvEnhanceV27Wizard } from './AvEnhanceV27Wizard'
import { StudioGatewayError } from './gateway'
import { HostBridgeError } from './host-bridge'
import overlapExample from './__fixtures__/overlap-preview.json'
import { parseOverlapFullEnvelope, type OverlapFullIntent, type OverlapProcessingRequest } from './chapter-overlap-contracts'
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
const defaultProcessing = {
  chapter_selector: { mode: 'single' }, leaf_duration_minutes: 1,
  enhancement: { model_name: 'Starlight Precise' }, frame_interpolation: { model_name: 'Aion' }, program_encode: { encoder: 'gpu' },
} as const

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
    currentProjectPath: 'D:\\Projects\\guided.zniku', currentProjectId: '', currentProjectName: '',
    runSummaries: [] as ReadonlyArray<RunSummaryWire>, projectIdFactory: () => 'project.hidden-session-id', pickerAvailable: true,
    onPickProjectPath: vi.fn(async () => 'D:\\Projects\\guided.zniku'),
    onPickSources: vi.fn<(_multiple: boolean) => Promise<ReadonlyArray<string> | null>>(async () => ['D:\\Media\\source.mkv']),
    onPickOutputDirectory: vi.fn(async () => 'D:\\Library'), onClose: vi.fn(),
    onPreviewPublication: vi.fn(async (request: AvEnhanceV27PublicationPreviewRequestWire): Promise<AvEnhanceV27PublicationPreviewEnvelope> => {
      const root = typeof request.request.output_root === 'string' ? request.request.output_root : 'D:\\Projects'
      const layout = request.request.layout ?? 'title_subdirectory'
      return { contract_version: '0.3.0', layout, resolved_output_root: root,
        output_directory: layout === 'title_subdirectory' ? `${root}\\${request.request.title} (${request.request.year})` : root,
        will_create_directory: layout === 'title_subdirectory' }
    }),
    onPreview: vi.fn(async (request: AvEnhanceV27TemplatePreviewRequestWire) => preview(
      request.action === 'prepare' ? 'preparation' : 'expanded',
      request.action === 'expand' ? request.request.preparation_run_id : runId,
    )),
    onCreate: vi.fn(async (_request: AvEnhanceV27PrepareRequestWire) => true),
    onStartPreparationRun: vi.fn(async () => runId),
    onExpand: vi.fn(async (_request: AvEnhanceV27ExpandRequestWire) => true), onLocateNode: vi.fn(),
  }
}

async function reachAnalysis(user: ReturnType<typeof userEvent.setup>, customOutput = false): Promise<void> {
  await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
  await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
  await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
  await user.click(screen.getByRole('button', { name: '下一步：成片设置' }))
  await user.type(screen.getByLabelText('片名'), 'Movie')
  await user.type(screen.getByLabelText('年份'), '2026')
  if (customOutput) await chooseOutputParent(user)
  await user.click(screen.getByRole('button', { name: '下一步：分析' }))
}

function overlapProps() {
  return { ...baseProps(),
    onPreviewOverlapProcessing: vi.fn(async (request: OverlapProcessingRequest) => ({ ...request, status: 'pending_real_acceptance' as const })),
    onPreviewOverlap: vi.fn(async (request: OverlapFullIntent) => ({ ...parseOverlapFullEnvelope(overlapExample), preparation_run_id: request.preparation_run_id, processing: request.processing })),
    onExpandOverlap: vi.fn(async (_request: OverlapFullIntent) => true),
  }
}

async function selectOverlap(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '选择视频素材' }))
  await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
  await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
  await user.selectOptions(screen.getByLabelText('工作流方案'), 'overlap')
  await user.click(screen.getByRole('button', { name: '下一步：成片设置' }))
  await user.type(screen.getByLabelText('片名'), 'Synthetic')
  await user.type(screen.getByLabelText('年份'), '2026')
}

describe('独立重叠 FI 候选向导', () => {
  it('明确选择后默认 1 章 / 5 分钟 / Aion v1.0，复用分析后写入新普通图', async () => {
    const user = userEvent.setup(), props = overlapProps()
    render(<AvEnhanceV27Wizard {...props} runSummaries={[runSummary()]} />)
    await selectOverlap(user)
    expect(screen.getByLabelText('平均章数')).toHaveValue('1')
    expect(screen.getByLabelText('每段最长时长（分叶）')).toHaveValue('5')
    expect(screen.queryByLabelText('FI model version')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(props.onPreviewOverlapProcessing).toHaveBeenCalledWith(expect.objectContaining({ processing: expect.objectContaining({ settings: { chapter_selector: { mode: 'average', count: 1 }, leaf_max_minutes: 5 }, fi_profile: expect.objectContaining({ software_version: 'v1.0', model_name: 'Aion', status: 'pending_real_acceptance', left_context_frames: 32, right_context_frames: 32, minimum_input_frames: 2 }) }) }))
    expect(props.onPreviewPublication.mock.calls.every(([value]) => value.processing === undefined)).toBe(true)
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    expect(await screen.findByRole('region', { name: '确认重叠补帧工作流' })).toBeVisible()
    expect(props.onPreview).toHaveBeenCalledTimes(1)
    expect(props.onCreate).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    expect(props.onExpand).not.toHaveBeenCalled()
    expect(props.onExpandOverlap).toHaveBeenCalledWith(props.onPreviewOverlap.mock.calls[0]![0])
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })
  it.each(['exact_times', 'exact_frames'] as const)('%s 不转换或重排切点，服务行错误回到原行', async (mode) => {
    const user = userEvent.setup(), props = overlapProps()
    const field = mode === 'exact_times' ? 'times' : 'frames'
    props.onPreviewOverlapProcessing.mockRejectedValue(new StudioGatewayError('切点不合法', { code: 'E_CHAPTER_SELECTOR_ORDER', serviceMessage: '切点必须递增', fieldPath: ['processing', 'settings', 'chapter_selector', field, 1] }))
    render(<AvEnhanceV27Wizard {...props} />)
    await selectOverlap(user)
    await user.selectOptions(screen.getByLabelText('章节切分方式'), mode)
    const label = mode === 'exact_times' ? '时间' : '帧'
    await user.type(screen.getByLabelText(`第 1 个${label}切分点`), mode === 'exact_times' ? '00:00:02' : '899')
    await user.click(screen.getByRole('button', { name: '添加切分点' }))
    await user.type(screen.getByLabelText(`第 2 个${label}切分点`), mode === 'exact_times' ? '00:00:01' : '10')
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    await waitFor(() => expect(screen.getByLabelText(`第 2 个${label}切分点`)).toHaveFocus())
    expect(props.onPreviewOverlapProcessing.mock.calls[0]![0].processing.settings.chapter_selector).toEqual(mode === 'exact_times' ? { mode, times: ['00:00:02', '00:00:01'] } : { mode, frames: [899, 10] })
    expect(props.onCreate).not.toHaveBeenCalled()
  })
  it('已分章输入不可选候选，不改变旧默认分叶', async () => {
    const user = userEvent.setup(), props = overlapProps()
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot('pre_chaptered')} />)
    expect(screen.getByRole('option', { name: /ZNIKU 重叠 FI/ })).toBeDisabled()
    expect(screen.getByLabelText('Leaf duration minutes')).toHaveValue('1')
    expect(props.onPreviewOverlap).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '关闭模板向导' }))
  })
  it('已展开新图只提示去节点图配置，不再次展开或迁移结果', () => {
    const props = overlapProps(), existing = snapshot()
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={{ ...existing, project: { ...existing.project, graph: { ...existing.project.graph, nodes: [{ ...existing.project.graph.nodes[0]!, type_id: 'zniku.overlap.fi_context' }] } } }} />)
    expect(screen.getByRole('button', { name: '返回当前节点图' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /下一步/ })).not.toBeInTheDocument()
    expect(props.onPreviewOverlap).not.toHaveBeenCalled()
    expect(props.onExpandOverlap).not.toHaveBeenCalled()
  })
})

async function reachResumeAnalysis(user: ReturnType<typeof userEvent.setup>, customOutput = false): Promise<void> {
  await user.type(screen.getByLabelText('片名'), 'Movie')
  await user.type(screen.getByLabelText('年份'), '2026')
  if (customOutput) await chooseOutputParent(user)
  await user.click(screen.getByRole('button', { name: '下一步：分析' }))
  await user.click(screen.getByRole('button', { name: /分析记录 1/ }))
}

async function openOutputOptions(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const summary = screen.getByText('其他选项（可选）')
  if (!summary.closest('details')?.open) await user.click(summary)
}

async function chooseOutputParent(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await openOutputOptions(user)
  await user.click(screen.getByRole('button', { name: '更改成片父目录' }))
}

describe('AVEnhanceFlow v2.7 创作者向导', () => {
  it('默认仅依次显示四项基础设置，其余收进统一高级且开合不触发任何操作', async () => {
    const user = userEvent.setup()
    const props = { ...baseProps(), onPickDataDirectory: vi.fn(async () => 'E:\\archive-parent') }
    render(<AvEnhanceV27Wizard {...props} />)
    const summary = screen.getByText('高级选项（可选）')
    const details = summary.closest('details')!
    expect(details).not.toHaveAttribute('open')
    const basicControls = [
      screen.getByRole('combobox', { name: '素材组织方式' }),
      screen.getByRole('button', { name: /选择视频素材/ }),
      screen.getByLabelText('工程名称'),
      screen.getByRole('button', { name: '选择工程保存位置' }),
    ]
    for (const [index, control] of basicControls.entries()) {
      expect(control).toBeVisible()
      if (index > 0) expect(basicControls[index - 1]!.compareDocumentPosition(control) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    expect(screen.getByRole('region', { name: '素材设置' }).querySelectorAll('.creator-setup-row')).toHaveLength(2)
    expect(screen.getByRole('region', { name: '工程设置' }).querySelectorAll('.creator-setup-row')).toHaveLength(2)
    expect(details.querySelectorAll('details')).toHaveLength(0)
    expect(screen.queryByText('高级存储设置（可选）')).not.toBeInTheDocument()
    expect(screen.queryByText('开发浏览器高级入口')).not.toBeInTheDocument()
    expect(screen.getByText('选择工作数据父目录')).not.toBeVisible()
    expect(screen.getByText('使用工程旁默认位置')).not.toBeVisible()
    expect(screen.getByLabelText('模板工程路径')).not.toBeVisible()
    expect(screen.getByLabelText('Source 1 path')).not.toBeVisible()
    expect(screen.getByRole('button', { name: '选择工程保存位置' })).toBeEnabled()
    await user.click(summary)
    expect(details).toHaveAttribute('open')
    expect(screen.getByRole('button', { name: '选择工作数据父目录' })).toBeVisible()
    expect(screen.getByRole('region', { name: '工作数据位置' })).toHaveTextContent('创建工程时，将在工程文件旁建立同名 .data 文件夹。')
    expect(screen.getByRole('region', { name: '工作数据位置' })).toHaveTextContent('中间产物和外部处理结果会长期保留，完成或退出不会自动清除。')
    expect(screen.getByLabelText('模板工程路径')).toBeVisible()
    expect(screen.getByLabelText('Source 1 path')).toBeVisible()
    await user.click(summary)
    expect(details).not.toHaveAttribute('open')
    expect(screen.getByText('使用工程旁默认位置')).not.toBeVisible()
    expect(props.onPickProjectPath).not.toHaveBeenCalled()
    expect(props.onPickDataDirectory).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onPreviewPublication).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it('默认流程只选一次工程文件位置，不调用数据选择器且创建不传 data_parent_directory', async () => {
    const user = userEvent.setup()
    const props = { ...baseProps(), onPickDataDirectory: vi.fn(async () => 'E:\\not-requested') }
    render(<AvEnhanceV27Wizard {...props} />)
    await reachAnalysis(user)
    expect(props.onPickProjectPath).toHaveBeenCalledOnce()
    expect(props.onPickDataDirectory).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledWith(expect.objectContaining({
      project_path: 'D:\\Projects\\guided.zniku',
    }), { media_basename: 'Movie (2026)' }))
    expect(props.onPickDataDirectory).not.toHaveBeenCalled()
  })

  it('成片设置常驻基础信息与四组处理参数，仅输出例外项折叠且不触发创建或运行', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} currentProjectPath="D:\\Projects\\existing.zniku" />)
    expect(screen.getByText('成片设置', { selector: 'ol strong' })).toBeVisible()
    for (const label of ['片名', '年份', 'Chapter selector mode', 'Leaf duration minutes', 'Enhancement model name',
      'Enhancement model version', 'Enhancement actual scale factor', 'FI model name', 'FI model version', 'Program encoder']) {
      expect(screen.getByLabelText(label)).toBeVisible()
    }
    for (const name of ['章节与分段', '画质增强', '章节补帧', '成片编码']) {
      const group = screen.getByRole('region', { name })
      expect(group).toBeVisible()
      expect(group.closest('details')).toBeNull()
    }
    const summary = screen.getByText('其他选项（可选）')
    const details = summary.closest('details')!
    expect(details).not.toHaveAttribute('open')
    expect(screen.getByText('更改成片父目录')).not.toBeVisible()
    expect(screen.getByLabelText('按片名创建子文件夹')).toBeChecked()
    expect(screen.getByLabelText('按片名创建子文件夹')).not.toBeVisible()
    expect(screen.getByLabelText('允许覆盖发布目标')).not.toBeChecked()
    expect(screen.getByLabelText('允许覆盖发布目标')).not.toBeVisible()
    expect(screen.getByLabelText('Publication output root')).not.toBeVisible()
    await user.click(summary)
    expect(screen.getByRole('button', { name: '更改成片父目录' })).toBeVisible()
    expect(screen.getByLabelText('Publication output root')).toBeVisible()
    await user.click(summary)
    expect(details).not.toHaveAttribute('open')
    expect(props.onPickOutputDirectory).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it('默认工程目录自动只读预览，下一步仍重验且正式扩展只使用 Python 返回的输出根', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onPreviewPublication.mockResolvedValue({ contract_version: '0.3.0', layout: 'title_subdirectory',
      resolved_output_root: 'P:\\Python-resolved', output_directory: 'P:\\Python-resolved\\Movie (2026)', will_create_directory: true })
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} runSummaries={[runSummary()]} />)
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    await waitFor(() => expect(props.onPreviewPublication).toHaveBeenCalledWith({ contract_version: '0.3.0', request: {
      project_path: 'D:\\Projects\\guided.zniku', title: 'Movie', year: '2026', overwrite: false, layout: 'title_subdirectory',
    } }))
    expect(await screen.findByText('P:\\Python-resolved\\Movie (2026)', { exact: true })).toBeVisible()
    expect(props.onPickOutputDirectory).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    const checksBeforeNext = props.onPreviewPublication.mock.calls.length
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(props.onPreviewPublication).toHaveBeenCalledTimes(checksBeforeNext + 1)
    expect(props.onPreviewPublication).toHaveBeenLastCalledWith(expect.objectContaining({ processing: defaultProcessing }))
    await user.click(screen.getByRole('button', { name: /分析记录 1/ }))
    await waitFor(() => expect(props.onPreview).toHaveBeenCalledWith({ action: 'expand', request: expect.objectContaining({
      publication: { output_root: 'P:\\Python-resolved', title: 'Movie', year: '2026', overwrite: false, layout: 'title_subdirectory' },
    }) }))
    const expansion = props.onPreview.mock.calls.find(([request]) => request.action === 'expand')![0]
    expect(expansion.request).not.toHaveProperty('project_path')
    expect(expansion.request).not.toHaveProperty('publication.project_path')
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it.each(['resolve', 'reject'] as const)('恢复工程目录后拒绝迟到输出父目录 %s，不覆盖默认选择或产生副作用', async (result) => {
    const user = userEvent.setup()
    let finish!: (path: string | null) => void
    let fail!: (error: Error) => void
    const props = { ...baseProps(), onPickOutputDirectory: vi.fn<() => Promise<string | null>>()
      .mockResolvedValueOnce('E:\\chosen-output')
      .mockImplementationOnce(() => new Promise((resolve, reject) => { finish = resolve; fail = reject })) }
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} />)
    await chooseOutputParent(user)
    expect(screen.getByText('已选择输出目录：E:\\chosen-output')).toBeVisible()
    await chooseOutputParent(user)
    await user.click(screen.getByRole('button', { name: '恢复工程目录' }))
    await act(async () => { if (result === 'resolve') finish('E:\\late-output'); else fail(new Error('late output picker failure')) })
    expect(screen.queryByText(/chosen-output|late-output|late output picker failure/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Publication output root')).toHaveValue('')
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it('恢复已有工程的父目录失败留在成片设置，定位自定义目录并复用已有准确分析', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const successfulPreview = props.onPreviewPublication.getMockImplementation()!
    props.onPreviewPublication.mockImplementation(async (request) => {
      if (typeof request.request.project_path === 'string') throw new StudioGatewayError('E_AV27_NAMING_PROJECT_PATH', {
        code: 'E_AV27_NAMING_PROJECT_PATH', serviceMessage: '工程所在磁盘当前不可访问。',
      })
      return successfulPreview(request)
    })
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} runSummaries={[runSummary()]} />)
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('工程所在磁盘当前不可访问。')
    expect(screen.getByRole('heading', { name: '成片设置' })).toBeVisible()
    expect(screen.getByText('其他选项（可选）').closest('details')).toHaveAttribute('open')
    await waitFor(() => expect(screen.getByRole('button', { name: '更改成片父目录' })).toHaveFocus())
    expect(screen.queryByRole('button', { name: '选择工程保存位置' })).not.toBeInTheDocument()
    await chooseOutputParent(user)
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    await user.click(screen.getByRole('button', { name: /分析记录 1/ }))
    await waitFor(() => expect(props.onPreview).toHaveBeenCalledWith({ action: 'expand', request: expect.objectContaining({
      preparation_run_id: runId, publication: expect.objectContaining({ output_root: 'D:\\Library' }),
    }) }))
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it.each([
    ['CHAPTER_SELECTOR', 'Chapter selector mode', 'single', ''],
    ['CHAPTER_SELECTOR', 'Exact chapter frames', 'exact_frames', '899,899'],
    ['CHAPTER_SELECTOR', 'Exact chapter times', 'exact_times', 'abc'],
    ['LEAF_DURATION', 'Leaf duration minutes', 'single', ''],
    ['ENHANCEMENT_MODEL_NAME', 'Enhancement model name', 'single', ''],
    ['ENHANCEMENT_MODEL_VERSION', 'Enhancement model version', 'single', ''],
    ['ENHANCEMENT_SCALE', 'Enhancement actual scale factor', 'single', ''],
    ['FI_MODEL_NAME', 'FI model name', 'single', ''],
    ['FI_MODEL_VERSION', 'FI model version', 'single', ''],
    ['ENCODER', 'Program encoder', 'single', ''],
  ] as const)('Python 处理设置错误 %s 定位 %s，不创建或启动分析', async (suffix, field, selector, value) => {
    const user = userEvent.setup()
    const props = baseProps()
    const successfulPreview = props.onPreviewPublication.getMockImplementation()!
    const code = `E_AV27_SETTINGS_${suffix}`
    props.onPreviewPublication.mockImplementation(async (request) => {
      if (request.processing) throw new StudioGatewayError(code, { code, serviceMessage: '合成参数未通过领域检查。' })
      return successfulPreview(request)
    })
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} runSummaries={[runSummary()]} />)
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    if (selector !== 'single') {
      await user.selectOptions(screen.getByLabelText('Chapter selector mode'), selector)
      await user.type(screen.getByLabelText(field), value)
    }
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('处理设置未通过检查。工程、素材和已有分析均保持不变。合成参数未通过领域检查。')
    expect(screen.getByRole('heading', { name: '成片设置' })).toBeVisible()
    await waitFor(() => expect(screen.getByLabelText(field)).toHaveFocus())
    expect(props.onPreviewPublication).toHaveBeenLastCalledWith(expect.objectContaining({ processing: expect.objectContaining({
      chapter_selector: selector === 'exact_frames' ? { mode: selector, frames: [899, 899] }
        : selector === 'exact_times' ? { mode: selector, times: ['abc'] } : { mode: 'single' },
    }) }))
    expect(screen.getByLabelText('片名')).toHaveValue('Movie')
    expect(screen.getByLabelText('年份')).toHaveValue('2026')
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it.each(['resolve', 'reject'] as const)('自动目录预览的旧请求 %s 不覆盖更新后的片名和 Python 目录', async (result) => {
    const user = userEvent.setup()
    const props = baseProps()
    const pending: Array<{ resolve: (value: AvEnhanceV27PublicationPreviewEnvelope) => void; reject: (error: Error) => void }> = []
    props.onPreviewPublication.mockImplementation(() => new Promise((resolve, reject) => { pending.push({ resolve, reject }) }))
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot()} />)
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    await waitFor(() => expect(pending).toHaveLength(1))
    await user.type(screen.getByLabelText('片名'), ' New')
    await waitFor(() => expect(pending).toHaveLength(2))
    await act(async () => { pending[1]!.resolve({ contract_version: '0.3.0', layout: 'title_subdirectory',
      resolved_output_root: 'D:\\Projects', output_directory: 'D:\\Projects\\Current by Python', will_create_directory: true }) })
    expect(screen.getByText('D:\\Projects\\Current by Python', { exact: true })).toBeVisible()
    await act(async () => {
      if (result === 'resolve') pending[0]!.resolve({ contract_version: '0.3.0', layout: 'title_subdirectory',
        resolved_output_root: 'D:\\Old', output_directory: 'D:\\Old\\late-old-directory', will_create_directory: true })
      else pending[0]!.reject(new Error('late old directory error'))
    })
    expect(screen.getByLabelText('片名')).toHaveValue('Movie New')
    expect(screen.getByText('D:\\Projects\\Current by Python', { exact: true })).toBeVisible()
    expect(screen.queryByText(/late-old-directory|late old directory error/)).not.toBeInTheDocument()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it('只选工作数据不能代替工程位置，错误单独说明缺项并聚焦工程保存按钮', async () => {
    const user = userEvent.setup()
    const props = { ...baseProps(), onPickDataDirectory: vi.fn(async () => 'E:\\archive-parent') }
    render(<AvEnhanceV27Wizard {...props} />)
    await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
    await user.click(screen.getByText('高级选项（可选）'))
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    await user.click(screen.getByText('高级选项（可选）'))
    expect(screen.getByText('专用磁盘父目录：E:\\archive-parent')).not.toBeVisible()
    expect(screen.getByText('已自定义工作数据位置')).toBeVisible()
    expect(screen.getByText('已自定义工作数据位置').closest('summary')).toHaveTextContent('高级选项（可选）')
    expect(screen.getByText('选择工作数据父目录')).not.toBeVisible()
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByRole('alert')).toHaveTextContent('请选择工程保存位置。工作数据位置不能代替工程文件位置。')
    expect(screen.getByRole('heading', { name: '选择要处理的视频' })).toBeVisible()
    await waitFor(() => expect(screen.getByRole('button', { name: '选择工程保存位置' })).toHaveFocus())
    expect(screen.getByRole('button', { name: '选择工程保存位置' })).toHaveAccessibleDescription('请选择工程保存位置。工作数据位置不能代替工程文件位置。')
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '选择工程保存位置' })).toHaveFocus())
    expect(screen.getByLabelText('工程名称')).toHaveValue('未命名视频工程')
    expect(props.onPickProjectPath).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
  })

  it('已选工程位置但缺工程名称只提示名称并聚焦输入框，不要求重选位置', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    render(<AvEnhanceV27Wizard {...props} />)
    await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.clear(screen.getByLabelText('工程名称'))
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByRole('alert')).toHaveTextContent('请填写工程名称。')
    expect(screen.getByRole('alert')).not.toHaveTextContent('请选择工程保存位置')
    await waitFor(() => expect(screen.getByLabelText('工程名称')).toHaveFocus())
    expect(screen.getByLabelText('工程名称')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('工程名称')).toHaveAccessibleDescription('请填写工程名称。')
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    await waitFor(() => expect(screen.getByLabelText('工程名称')).toHaveFocus())
    expect(screen.getByText('已选择：guided.zniku')).toBeVisible()
    expect(props.onPickProjectPath).toHaveBeenCalledOnce()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
  })

  it('开发环境无选择器时缺位置定位到展开后的手输入口，修正只更新本地表单', async () => {
    const user = userEvent.setup()
    const props = { ...baseProps(), pickerAvailable: false }
    render(<AvEnhanceV27Wizard {...props} />)
    const projectPath = screen.getByLabelText('模板工程路径')
    const advanced = screen.getByText('高级选项（可选）').closest('details')!
    expect(advanced).not.toHaveAttribute('open')
    expect(projectPath).not.toBeVisible()
    expect(screen.getByRole('button', { name: '选择工程保存位置' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    await waitFor(() => expect(projectPath).toHaveFocus())
    expect(advanced).toHaveAttribute('open')
    expect(projectPath).toBeVisible()
    expect(projectPath).toHaveAccessibleDescription('请选择工程保存位置。工作数据位置不能代替工程文件位置。')
    await user.type(projectPath, 'D:\\Projects\\typed.zniku')
    await user.type(screen.getByLabelText('Source 1 path'), 'D:\\Media\\source.mkv')
    expect(projectPath).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByRole('heading', { name: '选择处理方案' })).toBeVisible()
    expect(props.onPickProjectPath).not.toHaveBeenCalled()
    expect(props.onPickSources).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
  })

  it('取消工程或数据选择保留原位置和名称，收起高级只显示自定义提示而隐藏存储详情', async () => {
    const user = userEvent.setup()
    const props = { ...baseProps(),
      onPickProjectPath: vi.fn<() => Promise<string | null>>().mockResolvedValueOnce('D:\\Projects\\guided.zniku').mockResolvedValueOnce(null),
      onPickDataDirectory: vi.fn<() => Promise<string | null>>().mockResolvedValueOnce('E:\\archive-parent').mockResolvedValueOnce(null),
    }
    render(<AvEnhanceV27Wizard {...props} />)
    await user.clear(screen.getByLabelText('工程名称'))
    await user.type(screen.getByLabelText('工程名称'), '我的工程')
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    expect(props.onPickDataDirectory).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.click(screen.getByText('高级选项（可选）'))
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    await user.click(screen.getByText('高级选项（可选）'))
    expect(screen.getByLabelText('工程名称')).toHaveValue('我的工程')
    expect(screen.getByText('已选择：guided.zniku')).toBeVisible()
    expect(screen.getByText('专用磁盘父目录：E:\\archive-parent')).not.toBeVisible()
    expect(screen.getByText('已自定义工作数据位置')).toBeVisible()
    expect(screen.getByText('选择工作数据父目录')).not.toBeVisible()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
  })

  it('断线不是忙碌：允许关闭/Escape/本地默认位置，并保留重开草稿，不自动重试', async () => {
    const user = userEvent.setup()
    const props = { ...baseProps(), onPickDataDirectory: vi.fn(async () => 'E:\\synthetic-data'), onReconnect: vi.fn() }
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await user.clear(screen.getByLabelText('工程名称'))
    await user.type(screen.getByLabelText('工程名称'), '断线前草稿')
    await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.click(screen.getByText('高级选项（可选）'))
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    rerender(<AvEnhanceV27Wizard {...props} busy serviceUnavailable />)
    expect(screen.getByRole('button', { name: '关闭模板向导' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '选择工作数据父目录' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    expect(screen.getByText('使用工程旁默认位置')).toBeVisible()
    await user.keyboard('{Escape}')
    expect(props.onClose).toHaveBeenCalledOnce()
    rerender(<AvEnhanceV27Wizard {...props} busy serviceUnavailable open={false} />)
    rerender(<AvEnhanceV27Wizard {...props} busy serviceUnavailable />)
    expect(screen.getByLabelText('工程名称')).toHaveValue('断线前草稿')
    expect(screen.getByText('source.mkv')).toBeVisible()
    expect(screen.getByText('已选择：guided.zniku')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '重新连接本机服务' }))
    expect(props.onReconnect).toHaveBeenCalledOnce()
    rerender(<AvEnhanceV27Wizard {...props} connectionEpoch={1} />)
    expect(screen.getByLabelText('工程名称')).toHaveValue('断线前草稿')
    await user.click(screen.getByRole('button', { name: '关闭模板向导' }))
    expect(props.onClose).toHaveBeenCalledTimes(2)
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onPreviewPublication).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it('服务仍在线且显式写入进行中时，不允许关闭向导或 Escape', async () => {
    const props = baseProps()
    render(<AvEnhanceV27Wizard {...props} busy />)
    expect(screen.getByRole('button', { name: '关闭模板向导' })).toBeDisabled()
    await userEvent.keyboard('{Escape}')
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it('重连已成功但旧请求仍未结算时，不会再次锁住关闭与 Escape', async () => {
    const props = baseProps()
    const { rerender } = render(<AvEnhanceV27Wizard {...props} busy />)
    rerender(<AvEnhanceV27Wizard {...props} busy serviceUnavailable />)
    rerender(<AvEnhanceV27Wizard {...props} busy connectionEpoch={1} />)
    expect(screen.getByRole('button', { name: '关闭模板向导' })).toBeEnabled()
    expect(screen.getByRole('button', { name: /选择视频素材/ })).toBeDisabled()
    await userEvent.keyboard('{Escape}')
    expect(props.onClose).toHaveBeenCalledOnce()
  })

  it('可以显式放弃断线草稿；取消确认保留内容，确认后只重置表单', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await user.clear(screen.getByLabelText('工程名称'))
    await user.type(screen.getByLabelText('工程名称'), '保留草稿')
    rerender(<AvEnhanceV27Wizard {...props} serviceUnavailable />)
    await user.click(screen.getByRole('button', { name: '放弃保留的向导草稿' }))
    expect(screen.getByLabelText('工程名称')).toHaveValue('保留草稿')
    await user.click(screen.getByRole('button', { name: '放弃保留的向导草稿' }))
    expect(screen.getByLabelText('工程名称')).toHaveValue('未命名视频工程')
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
    confirm.mockRestore()
  })

  it('中断的 resume 草稿不会带到另一个工程', async () => {
    const user = userEvent.setup()
    const props = { ...baseProps(), mode: 'resume' as const, currentSnapshot: snapshot() }
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await user.type(screen.getByLabelText('片名'), '旧工程输出')
    rerender(<AvEnhanceV27Wizard {...props} serviceUnavailable />)
    rerender(<AvEnhanceV27Wizard {...props} serviceUnavailable open={false} />)
    rerender(<AvEnhanceV27Wizard {...props} currentSnapshot={arbitrarySnapshot()} connectionEpoch={1} open={false} />)
    rerender(<AvEnhanceV27Wizard {...props} currentSnapshot={arbitrarySnapshot()} connectionEpoch={1} />)
    expect(screen.getByLabelText('片名')).toHaveValue('')
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
  })

  it('断线后仍可返回前一步，设置草稿与数据位置只在当前页面内存保留', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await reachAnalysis(user)
    const checksBeforeDisconnect = props.onPreviewPublication.mock.calls.length
    expect(checksBeforeDisconnect).toBeGreaterThan(0)
    rerender(<AvEnhanceV27Wizard {...props} serviceUnavailable />)
    expect(screen.getByLabelText('片名')).toHaveValue('Movie')
    expect(screen.getByRole('button', { name: '下一步：分析' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '上一步' }))
    expect(screen.getByRole('heading', { name: '选择处理方案' })).toBeVisible()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreviewPublication).toHaveBeenCalledTimes(checksBeforeDisconnect)
  })

  it.each(['resolve', 'reject'] as const)('恢复默认后迟到的数据目录 %s 不覆盖本地选择', async (result) => {
    const user = userEvent.setup()
    const props = baseProps()
    let finish!: (path: string | null) => void
    let fail!: (error: Error) => void
    const onPickDataDirectory = vi.fn<() => Promise<string | null>>()
      .mockResolvedValueOnce('E:\\chosen')
      .mockImplementationOnce(() => new Promise((resolve, reject) => { finish = resolve; fail = reject }))
    render(<AvEnhanceV27Wizard {...props} onPickDataDirectory={onPickDataDirectory} />)
    await user.click(screen.getByText('高级选项（可选）'))
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    await user.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    await act(async () => { if (result === 'resolve') finish('E:\\late'); else fail(new Error('late picker failure')) })
    expect(screen.getByText('使用工程旁默认位置')).toBeVisible()
    expect(screen.queryByText(/late/)).not.toBeInTheDocument()
    expect(props.onCreate).not.toHaveBeenCalled()
  })

  it('断线与重连撤销迟到 picker 以及旧输出打开句柄，不自动执行系统动作', async () => {
    const user = userEvent.setup()
    let finish!: (path: string | null) => void
    const props = { ...baseProps(), mode: 'resume' as const, currentSnapshot: snapshot(),
      onRevealOutputDirectory: vi.fn(async () => undefined),
      onPickOutputDirectory: vi.fn<() => Promise<string | null>>().mockResolvedValueOnce('D:\\chosen')
        .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve })) }
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await chooseOutputParent(user)
    await chooseOutputParent(user)
    rerender(<AvEnhanceV27Wizard {...props} serviceUnavailable />)
    rerender(<AvEnhanceV27Wizard {...props} connectionEpoch={1} />)
    await act(async () => finish('D:\\late'))
    expect(screen.getByText('已选择输出目录：D:\\chosen')).toBeVisible()
    expect(screen.queryByText(/D:\\late/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开所选输出文件夹' })).toBeDisabled()
    expect(props.onRevealOutputDirectory).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
  })

  it('断线清除确认预览，重连/重开不自动获取 preview 或确认旧工作流', async () => {
    const user = userEvent.setup()
    const props = { ...baseProps(), mode: 'resume' as const, currentSnapshot: snapshot(), runSummaries: [runSummary()] }
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await reachResumeAnalysis(user)
    expect(await screen.findByRole('button', { name: '确认并创建工作流' })).toBeEnabled()
    const checksBeforeDisconnect = props.onPreviewPublication.mock.calls.length
    rerender(<AvEnhanceV27Wizard {...props} serviceUnavailable />)
    expect(screen.queryByRole('button', { name: '确认并创建工作流' })).not.toBeInTheDocument()
    rerender(<AvEnhanceV27Wizard {...props} connectionEpoch={1} open={false} />)
    rerender(<AvEnhanceV27Wizard {...props} connectionEpoch={1} />)
    expect(screen.getByLabelText('片名')).toHaveValue('Movie')
    expect(props.onPreview).toHaveBeenCalledTimes(1)
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)) })
    expect(props.onPreviewPublication).toHaveBeenCalledTimes(checksBeforeDisconnect)
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onExpand).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    await user.click(await screen.findByRole('button', { name: '生成工作流预览' }))
    expect(await screen.findByRole('button', { name: '确认并创建工作流' })).toBeEnabled()
    expect(props.onPreview).toHaveBeenCalledTimes(2)
  })

  it('创建中断线后允许离开，但迟到创建成功不得自动接着运行或重放创建', async () => {
    const user = userEvent.setup()
    let finish!: (created: boolean) => void
    const props = { ...baseProps(), onCreate: vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve })) }
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await reachAnalysis(user)
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledOnce())
    expect(screen.getByRole('button', { name: '关闭模板向导' })).toBeDisabled()
    rerender(<AvEnhanceV27Wizard {...props} serviceUnavailable />)
    expect(screen.getByRole('button', { name: '关闭模板向导' })).toBeEnabled()
    expect(screen.getByText(/上次创建或运行请求的结果尚未确认/)).toBeVisible()
    rerender(<AvEnhanceV27Wizard {...props} connectionEpoch={1} />)
    await act(async () => finish(true))
    expect(props.onCreate).toHaveBeenCalledOnce()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '下一步：分析' })).toBeDisabled()
  })

  it('专用数据父目录只在显式开始分析后随media_basename交给创建命令', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const onPickDataDirectory = vi.fn(async () => 'E:\\archive-parent')
    render(<AvEnhanceV27Wizard {...props} onPickDataDirectory={onPickDataDirectory} />)
    await user.click(screen.getByText('高级选项（可选）'))
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
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.clear(screen.getByLabelText('工程名称'))
    await user.type(screen.getByLabelText('工程名称'), '保留工程身份')
    await user.click(screen.getByText('高级选项（可选）'))
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    expect(screen.getByText('使用工程旁默认位置')).toBeVisible()
    expect(props.onCreate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    expect(screen.getByText('专用磁盘父目录：E:\\old-choice')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '恢复工程旁默认' }))
    expect(screen.getByText('已选择：guided.zniku')).toBeVisible()
    expect(screen.getByLabelText('工程名称')).toHaveValue('保留工程身份')
    expect(props.onPickProjectPath).toHaveBeenCalledOnce()
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
    await user.click(screen.getByText('高级选项（可选）'))
    await user.click(screen.getByRole('button', { name: '选择工作数据父目录' }))
    if (action === 'edit') await user.type(screen.getByLabelText('工程名称'), '已变化')
    else {
      rerender(<AvEnhanceV27Wizard {...props} open={false} onPickDataDirectory={onPickDataDirectory} />)
      rerender(<AvEnhanceV27Wizard {...props} open onPickDataDirectory={onPickDataDirectory} />)
    }
    await act(async () => finish('E:\\late-disk'))
    if (action === 'close') expect(screen.getByText('使用工程旁默认位置')).not.toBeVisible()
    else expect(screen.getByText('使用工程旁默认位置')).toBeVisible()
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
    expect(props.onPreviewPublication).toHaveBeenLastCalledWith({ contract_version: '0.3.0', processing: defaultProcessing, request: {
      project_path: 'D:\\Projects\\guided.zniku', title: 'Movie', year: '2026', overwrite: false, layout: 'title_subdirectory',
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
    const workingPreview = props.onPreviewPublication.getMockImplementation()!
    props.onPreviewPublication.mockRejectedValue(new StudioGatewayError('E_AV27_NAMING_PARENT', {
      code: 'E_AV27_NAMING_PARENT', serviceMessage: '所选目录不可访问，请选择可写目录。',
    }))
    render(<AvEnhanceV27Wizard {...props} />)
    await reachAnalysis(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('输出位置未通过检查')
    expect(screen.getByRole('heading', { name: '成片设置' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /开始分析素材/ })).not.toBeInTheDocument()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
    const checksBeforeRetry = props.onPreviewPublication.mock.calls.length
    props.onPreviewPublication.mockImplementation(workingPreview)
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(await screen.findByRole('button', { name: /开始分析素材/ })).toBeEnabled()
    expect(props.onPreviewPublication).toHaveBeenCalledTimes(checksBeforeRetry + 1)
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
    expect(screen.getByLabelText('按片名创建子文件夹')).toBeChecked()
    await openOutputOptions(user)
    await user.click(screen.getByLabelText('按片名创建子文件夹'))
    await reachResumeAnalysis(user)
    await user.click(screen.getByRole('button', { name: '返回设置' }))
    await openOutputOptions(user)
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
    const pending: Array<(value: AvEnhanceV27PublicationPreviewEnvelope) => void> = []
    props.onPreviewPublication.mockImplementation(() => new Promise((resolve) => { pending.push(resolve) }))
    const options = { ...props, mode: 'resume' as const, currentSnapshot: snapshot(), runSummaries: [runSummary()] }
    const { rerender } = render(<AvEnhanceV27Wizard {...options} />)
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    await chooseOutputParent(user)
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    expect(screen.getByRole('button', { name: '正在检查输出位置…' })).toBeDisabled()
    if (action === 'cancel') await user.click(screen.getByRole('button', { name: '取消检查' }))
    else if (action === 'change') await user.click(screen.getByLabelText('按片名创建子文件夹'))
    else {
      rerender(<AvEnhanceV27Wizard {...options} open={false} />)
      rerender(<AvEnhanceV27Wizard {...options} open />)
    }
    await act(async () => { pending.forEach((resolve) => resolve({ contract_version: '0.3.0', layout: 'direct', resolved_output_root: 'D:\\Late', output_directory: 'D:\\Late', will_create_directory: false })) })
    expect(screen.getByRole('heading', { name: '成片设置' })).toBeVisible()
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
    expect(screen.getByRole('heading', { name: '成片设置' })).toBeVisible()
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    await chooseOutputParent(user)
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

  it('预选素材后切为多章节会补齐空白章名，默认可继续且不要求打开高级填写', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    render(<AvEnhanceV27Wizard {...props} />)
    await user.click(screen.getByRole('button', { name: '选择视频素材' }))
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.selectOptions(screen.getByLabelText('素材组织方式'), 'pre_chaptered')
    expect(screen.getByLabelText('第 1 章名称')).toHaveValue('章节 1')
    expect(screen.getByLabelText('第 1 章名称')).not.toBeVisible()
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByRole('heading', { name: '选择处理方案' })).toBeVisible()

    await user.click(screen.getByRole('button', { name: '上一步' }))
    await user.click(screen.getByText('高级选项（可选）'))
    await user.clear(screen.getByLabelText('第 1 章名称'))
    await user.type(screen.getByLabelText('第 1 章名称'), '   ')
    await user.selectOptions(screen.getByLabelText('素材组织方式'), 'program')
    await user.selectOptions(screen.getByLabelText('素材组织方式'), 'pre_chaptered')
    expect(screen.getByLabelText('第 1 章名称')).toHaveValue('章节 1')
    await user.click(screen.getByText('高级选项（可选）'))
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByRole('heading', { name: '选择处理方案' })).toBeVisible()
    expect(props.onPickSources).toHaveBeenCalledOnce()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
  })

  it('切换模式保留已填章名，主动清空后错误展开统一高级并重复聚焦具体章节', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    render(<AvEnhanceV27Wizard {...props} />)
    await user.selectOptions(screen.getByLabelText('素材组织方式'), 'pre_chaptered')
    await user.click(screen.getByRole('button', { name: '选择全部章节视频' }))
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    const advanced = screen.getByText('高级选项（可选）').closest('details')!
    await user.click(screen.getByText('高级选项（可选）'))
    await user.clear(screen.getByLabelText('第 1 章名称'))
    await user.type(screen.getByLabelText('第 1 章名称'), '自定义前篇')
    await user.selectOptions(screen.getByLabelText('素材组织方式'), 'program')
    await user.selectOptions(screen.getByLabelText('素材组织方式'), 'pre_chaptered')
    const chapterName = screen.getByLabelText('第 1 章名称')
    expect(chapterName).toHaveValue('自定义前篇')
    await user.clear(chapterName)
    await user.click(screen.getByText('高级选项（可选）'))
    expect(advanced).not.toHaveAttribute('open')
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByRole('alert')).toHaveTextContent('请填写第 1 段的章节名称。')
    expect(advanced).toHaveAttribute('open')
    await waitFor(() => expect(chapterName).toHaveFocus())
    expect(chapterName).toHaveAttribute('aria-invalid', 'true')
    expect(chapterName).toHaveAccessibleDescription('请填写第 1 段的章节名称。')
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    await waitFor(() => expect(chapterName).toHaveFocus())
    await user.type(chapterName, '已修正前篇')
    expect(chapterName).not.toHaveAttribute('aria-invalid')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(props.onCreate).not.toHaveBeenCalled()
    expect(props.onPreview).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).not.toHaveBeenCalled()
  })

  it('pre_chaptered 多素材保持 picker 顺序与连续 ordinal', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onPickSources.mockResolvedValue(['D:\\Media\\b.mkv', 'D:\\Media\\a.mkv'])
    const { rerender } = render(<AvEnhanceV27Wizard {...props} />)
    await user.selectOptions(screen.getByLabelText('素材组织方式'), 'pre_chaptered')
    await user.click(screen.getByRole('button', { name: /选择全部章节视频/ }))
    expect(screen.getByLabelText('第 1 章名称')).not.toBeVisible()
    await user.click(screen.getByText('高级选项（可选）'))
    await user.clear(screen.getByLabelText('第 1 章名称'))
    await user.type(screen.getByLabelText('第 1 章名称'), '前篇')
    await user.clear(screen.getByLabelText('第 2 章名称'))
    await user.type(screen.getByLabelText('第 2 章名称'), '后篇')
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    await user.click(screen.getByRole('button', { name: '下一步：成片设置' }))
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    await chooseOutputParent(user)
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
    expect(screen.getByLabelText('Source 1 path')).toHaveValue('')
    await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('选择器暂时不可用')
    await user.click(screen.getByRole('button', { name: /选择视频素材/ }))
    await user.type(screen.getByLabelText('工程名称'), '新')
    resolveLate(['D:\\Media\\late.mkv'])
    await Promise.resolve()
    expect(screen.queryByText('late.mkv')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Source 1 path')).toHaveValue('')
  })

  it.each(['source', 'project', 'output'] as const)('%s 选择窗口忙碌统一提示已有窗口，保留高级原文且不发起分析', async (kind) => {
    const user = userEvent.setup()
    const props = baseProps()
    const rawMessage = 'E_HOST_BRIDGE_DIALOG_BUSY: synthetic native chooser is active'
    const error = new HostBridgeError(rawMessage, { code: 'E_HOST_BRIDGE_DIALOG_BUSY', httpStatus: 409 })
    const pick = kind === 'source' ? props.onPickSources : kind === 'project' ? props.onPickProjectPath : props.onPickOutputDirectory
    pick.mockRejectedValueOnce(error)
    render(<AvEnhanceV27Wizard {...props} {...(kind === 'output' ? { mode: 'resume', currentSnapshot: snapshot() } : {})} />)
    if (kind === 'output') await openOutputOptions(user)
    await user.click(screen.getByRole('button', { name: kind === 'source' ? /选择视频素材/ : kind === 'project' ? '选择工程保存位置' : '更改成片父目录' }))
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
    await chooseOutputParent(user)
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
    expect(screen.getByRole('heading', { name: '成片设置' })).toBeVisible()
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
    await chooseOutputParent(user)
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
      await reachAnalysis(user, true)
      await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
      await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledTimes(1))
      rerender(<AvEnhanceV27Wizard {...options} runSummaries={[runSummary()]} />)
    } else await reachResumeAnalysis(user, true)
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
    await openOutputOptions(user)
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
    expect(screen.getByLabelText('成片目录预览')).toHaveTextContent('工程目录')
    expect(screen.queryByRole('button', { name: '打开所选输出文件夹' })).not.toBeInTheDocument()
    await chooseOutputParent(user)
    expect(screen.queryByRole('button', { name: '打开所选输出文件夹' })).not.toBeInTheDocument()
    await chooseOutputParent(user)
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
