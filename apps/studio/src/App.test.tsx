import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type {
  AvEnhanceV27TemplatePreviewEnvelope,
  AvEnhanceV27TemplatePreviewRequestWire,
  ExternalHandoffReadiness,
  NodeLogEnvelope,
  PresentationCatalogEnvelopeWire,
  RunDetailEnvelope,
  RunSummaryWire,
  RunSummaryPageEnvelope,
  StatusEnvelope,
  StudioCommand,
  RerunPreviewEnvelope,
  RerunPreviewRequest,
} from './studio/contracts'
import { StudioGatewayError, type StudioGateway } from './studio/gateway'
import { inspectGraph } from './studio/graph'
import type { HostBridge, HostCapabilitiesEnvelope, HostSelection } from './studio/host-bridge'
import {
  failedDetailEnvelope,
  failedStatusEnvelope,
  handoffDetailEnvelope,
  handoffEnvelope,
  handoffFixtureIds,
  handoffLogEnvelope,
  handoffReadinessEnvelope,
  projectSnapshot,
  projectedProgressDetail,
  runningProgressDetail,
  runningProgressEnvelope,
  sourceDefinition,
  studioEnvelope,
  threeRunDetail,
  threeRunEnvelope,
  threeRunFixtureIds,
  transformDefinition,
  rerunPreviewEnvelope,
} from './studio/test-fixtures'

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

class Deferred<T> {
  readonly promise: Promise<T>
  resolve!: (value: T) => void
  reject!: (error: unknown) => void

  constructor() {
    this.promise = new Promise<T>((resolve, reject) => {
      this.resolve = resolve
      this.reject = reject
    })
  }
}

interface GatewayOptions {
  readonly rerunPreview?: (request: RerunPreviewRequest) => Promise<RerunPreviewEnvelope> | RerunPreviewEnvelope
  readonly templatePreview?: (
    request: AvEnhanceV27TemplatePreviewRequestWire,
  ) => Promise<AvEnhanceV27TemplatePreviewEnvelope> | AvEnhanceV27TemplatePreviewEnvelope
  readonly inspect?: (viewRunId: string | null, count: number) => Promise<StatusEnvelope> | StatusEnvelope
  readonly listRuns?: (
    cursor: string | null,
    limit: number,
  ) => Promise<RunSummaryPageEnvelope> | RunSummaryPageEnvelope
  readonly detail?: (runId: string, count: number) => Promise<RunDetailEnvelope> | RunDetailEnvelope
  readonly readiness?: (
    runId: string,
    nodeRunId: string,
    probe: boolean,
  ) => Promise<ExternalHandoffReadiness> | ExternalHandoffReadiness
  readonly log?: (runId: string, nodeRunId: string) => Promise<NodeLogEnvelope> | NodeLogEnvelope
  readonly command?: (command: StudioCommand) => Promise<StatusEnvelope> | StatusEnvelope
  readonly presentations?: (
    count: number,
  ) => Promise<PresentationCatalogEnvelopeWire> | PresentationCatalogEnvelopeWire
}

class RecordingGateway implements StudioGateway {
  readonly rerunPreviewArguments: RerunPreviewRequest[] = []
  readonly commands: StudioCommand[] = []
  readonly inspectArguments: Array<string | null> = []
  readonly readinessArguments: Array<readonly [string, string, boolean]> = []
  readonly historyArguments: Array<readonly [string | null, number]> = []
  readonly logArguments: Array<readonly [string, string]> = []
  readonly templatePreviewArguments: AvEnhanceV27TemplatePreviewRequestWire[] = []
  inspectCount = 0
  inspectRunCount = 0
  inspectPresentationCount = 0
  readonly inspectPresentations?: () => Promise<PresentationCatalogEnvelopeWire>

  constructor(
    public envelope: StatusEnvelope = studioEnvelope(),
    private readonly options: GatewayOptions = {},
  ) {
    if (options.presentations) {
      this.inspectPresentations = async () => {
        this.inspectPresentationCount += 1
        return options.presentations!(this.inspectPresentationCount)
      }
    }
  }

  async inspect(viewRunId: string | null = null): Promise<StatusEnvelope> {
    this.inspectCount += 1
    this.inspectArguments.push(viewRunId)
    return this.options.inspect?.(viewRunId, this.inspectCount) ?? this.envelope
  }

  async listRuns(cursor: string | null = null, limit = 20): Promise<RunSummaryPageEnvelope> {
    this.historyArguments.push([cursor, limit])
    if (this.options.listRuns) return this.options.listRuns(cursor, limit)
    return { contract_version: '0.3.0', run_summaries: [], next_run_cursor: null }
  }

  async inspectRun(runId: string): Promise<RunDetailEnvelope> {
    this.inspectRunCount += 1
    if (this.options.detail) return this.options.detail(runId, this.inspectRunCount)
    const reason = this.envelope.run_summaries.find((item) => item.run_id === runId)?.error?.reason
    if (reason === 'cancelled' || reason === 'interrupted') return failedDetailEnvelope(reason)
    if (Object.values(threeRunFixtureIds).includes(runId as never)) return threeRunDetail(runId)
    return handoffDetailEnvelope()
  }

  async previewRerun(request: RerunPreviewRequest): Promise<RerunPreviewEnvelope> {
    this.rerunPreviewArguments.push(request)
    return this.options.rerunPreview?.(request) ?? rerunPreviewEnvelope(request)
  }

  async inspectLog(runId: string, nodeRunId: string): Promise<NodeLogEnvelope> {
    this.logArguments.push([runId, nodeRunId])
    if (this.options.log) return this.options.log(runId, nodeRunId)
    const envelope = handoffLogEnvelope()
    return { ...envelope, run_id: runId, log: { ...envelope.log, node_run_id: nodeRunId } }
  }

  async inspectReadiness(
    runId: string,
    nodeRunId: string,
    probe: boolean,
  ): Promise<ExternalHandoffReadiness> {
    this.readinessArguments.push([runId, nodeRunId, probe])
    if (this.options.readiness) return this.options.readiness(runId, nodeRunId, probe)
    return probe
      ? handoffReadinessEnvelope('probe_passed', true)
      : handoffReadinessEnvelope('present', false)
  }

  async previewAvEnhanceV27(
    request: AvEnhanceV27TemplatePreviewRequestWire,
  ): Promise<AvEnhanceV27TemplatePreviewEnvelope> {
    this.templatePreviewArguments.push(request)
    if (this.options.templatePreview) return this.options.templatePreview(request)
    throw new Error('本测试未配置 AVEnhanceFlow v2.7 template preview')
  }

  async command(command: StudioCommand): Promise<StatusEnvelope> {
    this.commands.push(command)
    const response = await this.options.command?.(command) ?? this.envelope
    if (command.operation === 'save_project' && response.snapshot) {
      const snapshot = { ...response.snapshot, project: command.project }
      this.envelope = { ...response, snapshot, studio_state: command.studio_state,
        storage_revision: command.expected_storage_revision + 1, project_session_id: command.project_session_id,
        authoring_diagnostics: inspectGraph(snapshot).map((item) => ({ code: item.code, path: '', message: item.message, validator_keyword: null })),
      }
      return this.envelope
    }
    if (command.operation === 'expand_av_enhance_v27') {
      this.envelope = { ...response, storage_revision: command.expected_storage_revision + 1 }
      return this.envelope
    }
    return response
  }
}

function presentationEnvelope(
  nodes: PresentationCatalogEnvelopeWire['catalog']['nodes'],
  categories: PresentationCatalogEnvelopeWire['catalog']['categories'],
): PresentationCatalogEnvelopeWire {
  return {
    contract_version: '0.3.0',
    catalog: { contract_version: '0.3.0', locale: 'zh-CN', categories, nodes },
    diagnostics: [],
  }
}

function nodePresentation(
  definition: typeof sourceDefinition,
  title: string,
  options: {
    readonly categoryId?: string
    readonly iconToken?: 'source' | 'transform' | 'output'
    readonly parameters?: PresentationCatalogEnvelopeWire['catalog']['nodes'][number]['parameters']
    readonly cardSummaryPaths?: ReadonlyArray<string>
  } = {},
): PresentationCatalogEnvelopeWire['catalog']['nodes'][number] {
  return {
    type_id: definition.type_id,
    definition_version: definition.version,
    title,
    description: `${title}说明。`,
    category_id: options.categoryId ?? 'test',
    icon_token: options.iconToken ?? 'transform',
    palette_level: 'primary',
    keywords: [],
    parameter_groups: [],
    parameters: options.parameters ?? [],
    ports: [],
    card_summary_paths: options.cardSummaryPaths ?? [],
  }
}

function unavailableGateway(message: string): StudioGateway {
  const reject = () => Promise.reject(new Error(message))
  return {
    inspect: reject,
    listRuns: reject,
    inspectRun: reject,
    inspectLog: reject,
    inspectReadiness: reject,
    previewAvEnhanceV27: reject,
    command: reject,
  }
}

async function flushReact(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

function hostCapabilitiesEnvelope(): HostCapabilitiesEnvelope {
  const capabilities: HostCapabilitiesEnvelope['capabilities'] = ([
    'open_file', 'open_files', 'select_directory', 'save_file',
    'reveal_in_file_manager', 'open_with_system_player',
  ] as const).map((capability) => ({ capability, available: true, unavailable_reason: null }))
  return { contract_version: '0.3.0', capabilities }
}

describe('ZNIKU Studio 0.3.0 Project workspace', () => {
  // 原有精确命令、身份和日志回归在高级层验证；Phase 4 创作者路径在后文显式使用默认模式。
  beforeEach(() => { window.localStorage.setItem('zniku.studio.density', 'advanced') })
  it('Project Service 缺失时失败关闭，不回退旧正式投影或浏览器 mock', async () => {
    render(<App gateway={unavailableGateway('loopback offline')} />)

    expect(await screen.findByText('本机服务暂时不可用')).toBeVisible()
    expect(screen.getByText('Project Service 不可用')).toBeVisible()
    expect(screen.getAllByText('loopback offline').some((item) => item.tagName === 'P')).toBe(true)
    expect(screen.queryByText('GUI-0 Prototype')).not.toBeInTheDocument()
    expect(screen.queryByText('Expanded Plan')).not.toBeInTheDocument()
    expect(screen.queryByText('Real Acceptance')).not.toBeInTheDocument()
  })

  it('Project Service 曾在线后变 stale，首页仍提供恢复动作且保留旧工程', async () => {
    window.localStorage.removeItem('zniku.studio.density')
    vi.useFakeTimers()
    const current = studioEnvelope()
    const gateway = new RecordingGateway(current, {
      inspect: (_viewRunId, count) => {
        if (count === 1) return current
        throw new Error('D:\\private\\service.sock offline')
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    expect(screen.getByText('Synthetic Studio Project')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(5_001) })
    expect(screen.getByText('本机服务暂时不可用')).toBeVisible()
    for (const button of screen.getAllByRole('button', { name: '重新连接' })) expect(button).toBeEnabled()
    expect(screen.getByText('Synthetic Studio Project')).toBeInTheDocument()
    for (const raw of screen.queryAllByText('D:\\private\\service.sock offline')) expect(raw).not.toBeVisible()
  })

  it('普通工程默认隐藏身份与路径，非 AV 工程不能进入伪恢复向导', async () => {
    window.localStorage.removeItem('zniku.studio.density')
    render(<App gateway={new RecordingGateway()} />)
    await screen.findByText('Synthetic Studio Project')
    const developer = screen.getByText('开发入口').closest('details')
    const advanced = screen.getByText('高级工程信息').closest('details')
    expect(developer).not.toHaveAttribute('open')
    expect(advanced).not.toHaveAttribute('open')
    expect(screen.getByLabelText('开发入口 Project ID')).not.toBeVisible()
    expect(screen.getByText(projectSnapshot.project.project_id)).not.toBeVisible()
    expect(screen.getByRole('button', { name: '处理向导' })).toBeDisabled()
  })

  it('节点卡按 Presentation picker hint 隐藏绝对路径，只展示人类文件摘要', async () => {
    window.localStorage.removeItem('zniku.studio.density')
    const pathDefinition = {
      ...sourceDefinition,
      type_id: 'test.path_source',
      parameter_schema: {
        $schema: 'https://json-schema.org/draft/2020-12/schema',
        type: 'object' as const,
        properties: {
          source_path: { type: 'string' as const },
          output_root: { type: 'string' as const },
          source_paths: { type: 'array' as const, items: { type: 'string' as const } },
        },
        required: ['source_path', 'output_root', 'source_paths'],
        additionalProperties: false,
      },
    }
    const pathSnapshot = {
      project: {
        project_id: 'project.paths',
        name: 'Path summary',
        graph: {
          nodes: [{
            node_id: 'path.source',
            type_id: pathDefinition.type_id,
            definition_version: pathDefinition.version,
            parameters: {
              source_path: 'D:\\private\\source.mkv',
              output_root: 'D:\\private\\Library',
              source_paths: ['D:\\private\\a.mkv', 'D:\\private\\b.mkv'],
            },
            ui_position: { x: 0, y: 0 },
          }],
          edges: [],
        },
      },
      definitions: [pathDefinition],
    }
    const gateway = new RecordingGateway(studioEnvelope({ snapshot: pathSnapshot }), {
      presentations: () => presentationEnvelope([
        nodePresentation(pathDefinition, '素材输入', {
          parameters: [
            { parameter_pointer: '/source_path', label: '素材', description: null, group_id: 'paths', order: 1, importance: 'primary', control_hint: 'file_path', unit: null, placeholder: null, enum_labels: [], picker: { extensions: ['.mkv'] } },
            { parameter_pointer: '/output_root', label: '成片文件夹', description: null, group_id: 'paths', order: 2, importance: 'primary', control_hint: 'directory_path', unit: null, placeholder: null, enum_labels: [], picker: null },
            { parameter_pointer: '/source_paths', label: '章节', description: null, group_id: 'paths', order: 3, importance: 'primary', control_hint: 'file_paths', unit: null, placeholder: null, enum_labels: [], picker: { extensions: ['.mkv'] } },
          ],
          cardSummaryPaths: ['/source_path', '/output_root', '/source_paths'],
        }),
      ], [{ category_id: 'test', title: '测试节点', description: null, order: 1 }]),
    })
    render(<App gateway={gateway} />)

    expect(await screen.findByText('素材：source.mkv')).toBeInTheDocument()
    expect(screen.getByText('成片文件夹：Library')).toBeInTheDocument()
    expect(screen.getByText('章节：a.mkv、b.mkv')).toBeInTheDocument()
    expect(screen.queryByText('D:\\private\\source.mkv')).not.toBeInTheDocument()
    expect(screen.queryByText('D:\\private\\Library')).not.toBeInTheDocument()
  })

  it('首页原生选择器 single-flight，等待期间锁定保存/关闭且卸载后迟到结果不 mutation', async () => {
    const user = userEvent.setup()
    const picker = new Deferred<ReadonlyArray<HostSelection> | null>()
    const capabilities = hostCapabilitiesEnvelope()
    const pick = vi.fn(() => picker.promise)
    const hostBridge: HostBridge = {
      configured: true,
      inspectCapabilities: vi.fn(async () => capabilities),
      pick,
      launch: vi.fn(async () => undefined),
    }
    const gateway = new RecordingGateway()
    const { unmount } = render(<App gateway={gateway} hostBridge={hostBridge} />)
    const close = await screen.findByRole('button', { name: '关闭工程首页' })
    await user.click(close)
    fireEvent.click(screen.getByLabelText('transform 节点'))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'strength' }), { target: { value: '7' } })
    await user.click(screen.getByRole('button', { name: '应用设置' }))
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '工程首页' }))
    const open = await screen.findByRole('button', { name: /打开已有工程/ })
    await user.click(open)
    expect(pick).toHaveBeenCalledTimes(1)
    expect(open).toBeDisabled()
    expect(screen.getByRole('button', { name: '关闭工程首页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()

    unmount()
    await act(async () => {
      picker.resolve([{ selection_handle: 'selection_1234567890_1234567890', path: 'D:\\private\\late.zniku' }])
      await Promise.resolve()
    })
    expect(gateway.commands).not.toContainEqual(expect.objectContaining({ operation: 'open_project' }))
  })

  it('HostBridge capability 检查失败可从首页显式重试并恢复 picker', async () => {
    const user = userEvent.setup()
    let inspections = 0
    const hostBridge: HostBridge = {
      configured: true,
      inspectCapabilities: vi.fn(async () => {
        inspections += 1
        if (inspections === 1) throw new Error('host unavailable')
        return hostCapabilitiesEnvelope()
      }),
      pick: vi.fn(async () => null),
      launch: vi.fn(async () => undefined),
    }
    render(<App gateway={new RecordingGateway()} hostBridge={hostBridge} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('桌面文件选择器暂时不可用')
    await user.click(screen.getByRole('button', { name: '重试桌面连接' }))
    await waitFor(() => expect(hostBridge.inspectCapabilities).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('button', { name: /打开已有工程/ })).toBeEnabled()
  })

  it('最近工程失败只展示安全任务文案，不把服务端绝对路径带回首页', async () => {
    window.localStorage.removeItem('zniku.studio.density')
    window.localStorage.setItem('zniku.studio.recent-projects.v1', JSON.stringify({
      version: 1,
      projects: [{
        path: 'D:\\private\\missing.zniku',
        name: 'Missing project',
        opened_at: '2026-09-04T02:00:00Z',
      }],
    }))
    const gateway = new RecordingGateway(studioEnvelope(), {
      command: (command) => {
        if (command.operation === 'open_project') {
          throw new StudioGatewayError('D:\\private\\missing.zniku already vanished', {
            code: 'E_PROJECT_SERVICE_PROJECT_NOT_FOUND',
          })
        }
        return studioEnvelope()
      },
    })
    render(<App gateway={gateway} />)
    await userEvent.click(await screen.findByRole('button', { name: /Missing project/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('这个最近工程当前无法打开')
    for (const raw of screen.queryAllByText(/D:\\private\\missing\.zniku already vanished/)) expect(raw).not.toBeVisible()
  })

  it.each([false, true])('创作者向导绑定 exact Run；展开回执冲突=%s 时隔离并发内容', async (responseConflict) => {
    window.localStorage.removeItem('zniku.studio.density')
    const user = userEvent.setup()
    const targetPath = 'C:\\synthetic\\av27.zniku'
    const exactRunId = '00000000-0000-4000-8000-000000000027'
    const historicalRunId = '00000000-0000-4000-8000-000000000026'
    const avSourceDefinition = {
      ...sourceDefinition,
      type_id: 'zniku.avenhance.v27.source_program',
      version: '0.2.1',
      parameter_schema: {
        $schema: 'https://json-schema.org/draft/2020-12/schema',
        type: 'object' as const,
        properties: {
          source_path: { type: 'string' as const },
          source_ordinal: { type: 'integer' as const, minimum: 0 },
        },
        required: ['source_path', 'source_ordinal'],
        additionalProperties: false,
      },
    }
    const templateProject = {
      project_id: 'project.guided',
      name: 'ZNIKU Project',
      graph: {
        nodes: [{
          node_id: 'source.program',
          type_id: avSourceDefinition.type_id,
          definition_version: avSourceDefinition.version,
          parameters: { source_path: 'C:\\synthetic\\source.mkv', source_ordinal: 0 },
          ui_position: { x: 80, y: 120 },
        }],
        edges: [],
      },
    }
    const templatePreview: AvEnhanceV27TemplatePreviewEnvelope = {
      contract_version: '0.3.0',
      profile_version: '2.7.0',
      phase: 'preparation',
      project: templateProject,
      definitions: [avSourceDefinition],
      profile: {
        profile_version: '2.7.0',
        phase: 'preparation',
        status: 'preparation-compatible',
        compatible: true,
        diagnostics: [],
      },
      plan: {
        source_count: 1,
        chapter_count: 0,
        leaf_count: 0,
        mr_mode: 'off',
        preparation_run_id: null,
        effective_video_artifact_ids: [],
        chapters: [],
        manual_stages: [],
        output_target_path: null,
      },
      creator: {
        analyzed: false,
        sources: [],
        estimated_step_count: 1,
        estimated_steps: '预计 1 个处理步骤',
      },
    }
    const created = studioEnvelope({
      project_path: targetPath,
      snapshot: { project: templateProject, definitions: [avSourceDefinition] },
    })
    const completedSummary = (runId: string, createdAt: string): RunSummaryWire => ({
      run_id: runId,
      project_id: 'project.guided',
      target_mode: 'all',
      selected_targets: [],
      state: 'completed',
      node_count: 1,
      state_counts: { pending: 0, running: 0, waiting_external: 0, completed: 1, failed: 0 },
      actionable: false,
      requires_operator_action: false,
      created_at: createdAt,
      started_at: createdAt,
      ended_at: createdAt,
      latest_activity_at: createdAt,
      error: null,
    })
    const historicalSummary = completedSummary(historicalRunId, '2026-09-02T01:00:00Z')
    const exactSummary = completedSummary(exactRunId, '2026-09-02T01:01:00Z')
    const expandedProject = { ...templateProject, graph: {
      nodes: [...templateProject.graph.nodes, projectSnapshot.project.graph.nodes[1]!],
      edges: [{ source_node_id: 'source.program', source_port_id: 'out', target_node_id: 'transform', target_port_id: 'in', ordinal: null }],
    } }
    const expandedPreview = (preparationRunId: string): AvEnhanceV27TemplatePreviewEnvelope => ({
      ...templatePreview,
      project: expandedProject,
      definitions: [avSourceDefinition, transformDefinition],
      phase: 'expanded',
      profile: {
        ...templatePreview.profile,
        phase: 'expanded',
        status: 'expanded-compatible',
      },
      plan: {
        ...templatePreview.plan,
        chapter_count: 1,
        leaf_count: 2,
        preparation_run_id: preparationRunId,
        effective_video_artifact_ids: ['artifact.internal'],
        manual_stages: [
          { stage: 'enhancement', node_count: 2, output_container: '.mov' },
          { stage: 'frame_interpolation', node_count: 1, output_container: '.mov' },
        ],
        output_target_path: 'D:\\Library\\Movie (2026) - Enhanced FI59p94 2160p.mkv',
      },
      creator: {
        analyzed: true,
        estimated_step_count: 3,
        estimated_steps: '预计 3 个处理步骤',
        sources: [{
          source_ordinal: 0,
          chapter_label: null,
          display_name: 'source.mkv',
          size_bytes: 123456789,
          size_label: '117.7 MiB',
          container: 'Matroska',
          video_codec: 'HEVC',
          pixel_format: 'yuv420p10le',
          resolution: '3840 × 2160',
          frame_rate: '30000/1001 fps（29.970）',
          duration: '1 分 0 秒',
          frame_count: '1,801 帧',
          audio_tracks: [{
            ordinal: 0,
            codec: 'AAC',
            channels: 2,
            sample_rate: 48000,
            language: 'jpn',
            title: null,
            label: 'AAC · 2 声道 · 48 kHz · jpn',
          }],
        }],
      },
    })
    let gateway: RecordingGateway
    gateway = new RecordingGateway(
      studioEnvelope({ project_path: null, snapshot: null }),
      {
        templatePreview: (request) => request.action === 'prepare'
          ? templatePreview
          : expandedPreview(request.request.preparation_run_id),
        command: (command) => {
          if (command.operation === 'create_av_enhance_v27') {
            gateway.envelope = { ...created, run_summaries: [historicalSummary] }
          } else if (command.operation === 'run_all') {
            gateway.envelope = {
              ...created,
              active_run_id: exactRunId,
              run_summaries: [historicalSummary, exactSummary],
            }
          } else if (command.operation === 'expand_av_enhance_v27') {
            gateway.envelope = { ...created, run_summaries: [historicalSummary, exactSummary], snapshot: {
              project: responseConflict ? { ...expandedProject, name: '另一窗口的更新' } : expandedProject,
              definitions: [avSourceDefinition, transformDefinition],
            } }
          }
          return gateway.envelope
        },
      },
    )
    render(<App gateway={gateway} projectIdFactory={() => 'project.guided'} />)

    expect(await screen.findByText('尚未打开工程')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /新建视频工程/ }))
    fireEvent.change(screen.getByLabelText('模板工程路径'), { target: { value: targetPath } })
    fireEvent.change(screen.getByLabelText('Source 1 path'), {
      target: { value: 'C:\\synthetic\\source.mkv' },
    })
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    await user.click(screen.getByRole('button', { name: '下一步：设置' }))
    await user.type(screen.getByLabelText('Publication title'), 'Movie')
    await user.type(screen.getByLabelText('Publication year'), '2026')
    fireEvent.change(screen.getByLabelText('Publication output root'), {
      target: { value: 'D:\\Library' },
    })
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))

    await waitFor(() => expect(gateway.commands).toContainEqual({
      operation: 'create_av_enhance_v27',
      request: expect.objectContaining({
        profile_version: '2.7.0',
        project_path: targetPath,
        project_id: 'project.guided',
        source_mode: 'program',
        sources: [{ source_path: 'C:\\synthetic\\source.mkv', source_ordinal: 0 }],
      }),
    }))
    await waitFor(() => expect(gateway.templatePreviewArguments).toContainEqual({
      action: 'expand',
      request: expect.objectContaining({ preparation_run_id: exactRunId }),
    }))
    expect(gateway.templatePreviewArguments).not.toContainEqual({
      action: 'expand',
      request: expect.objectContaining({ preparation_run_id: historicalRunId }),
    })
    expect(await screen.findByText('117.7 MiB · Matroska')).toBeVisible()
    for (const identity of screen.queryAllByText(exactRunId)) expect(identity).not.toBeVisible()
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    await waitFor(() => expect(gateway.commands).toContainEqual({
      operation: 'expand_av_enhance_v27',
      project_session_id: expect.any(String),
      expected_storage_revision: expect.any(Number),
      request: expect.objectContaining({ preparation_run_id: exactRunId }),
    }))

    if (responseConflict) {
      expect(await screen.findByRole('button', { name: '重新载入磁盘版本' })).toBeInTheDocument()
      expect(screen.queryByText('增强工作流已就绪')).not.toBeInTheDocument()
      expect(screen.queryByText('另一窗口的更新')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Run all' })).toBeDisabled()
      return
    }

    expect(screen.queryByRole('dialog', { name: 'AVEnhanceFlow v2.7.0 创作者向导' })).not.toBeInTheDocument()
    expect(await screen.findByLabelText('zniku.avenhance.v27.source_program 节点')).toBeInTheDocument()
    expect(screen.queryByText('C:\\synthetic\\source.mkv')).not.toBeInTheDocument()
    expect(screen.getByText('增强工作流已就绪')).toBeVisible()
    expect(gateway.templatePreviewArguments[0]).not.toHaveProperty('graph')
    expect(gateway.templatePreviewArguments[0]).not.toHaveProperty('definitions')

    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(screen.getByText(/增强工作流已调整/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '重做' }))
    expect(screen.getByText('增强工作流已就绪')).toBeVisible()

    fireEvent.click(screen.getByLabelText('zniku.avenhance.v27.source_program 节点'))
    fireEvent.click(screen.getByRole('button', { name: '复制所选' }))
    expect(screen.getByText(/增强工作流已调整/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(screen.getByText('增强工作流已就绪')).toBeVisible()
    fireEvent.click(screen.getByLabelText('zniku.avenhance.v27.source_program 节点'))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'source_ordinal（必填）' }), {
      target: { value: '1' },
    })
    await user.click(screen.getByRole('button', { name: '应用设置' }))
    expect(screen.getByText(/增强工作流已调整/)).toBeVisible()
  })

  it('使用单一画布搜索添加、复制节点，并实时阻断缺失 required input', async () => {
    const user = userEvent.setup()
    const ids = ['node.added', 'node.copied']
    render(<App gateway={new RecordingGateway()} nodeIdFactory={() => ids.shift()!} />)

    expect(await screen.findByText('Synthetic Studio Project')).toBeInTheDocument()
    expect(screen.getByText('Current Graph')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Studio Designer 画布' })).toBeInTheDocument()

    await user.type(screen.getByLabelText('搜索节点'), 'manual_external')
    const palette = screen.getByRole('generic', { name: '节点定义列表' })
    await user.click(within(palette).getByRole('button', { name: /test\.transform/ }))
    expect(await screen.findByLabelText('node.added 节点')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '复制所选' }))
    expect(await screen.findByLabelText('node.copied 节点')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '删除所选' }))
    await waitFor(() => expect(screen.queryByLabelText('node.copied 节点')).not.toBeInTheDocument())
    expect(screen.getByLabelText('node.added 节点')).toBeInTheDocument()
    expect(screen.getAllByText('E_REQUIRED_INPUT_MISSING').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Run all' })).toBeDisabled()
  })

  it('按 Python catalog 展示基础媒体节点与 VideoTransform presets', async () => {
    const user = userEvent.setup()
    const mediaSource = { ...sourceDefinition, type_id: 'zniku.media.source.video' }
    const mrPreset = { ...transformDefinition, type_id: 'zniku.media.video_transform.mr.external' }
    const createdEnvelope = studioEnvelope({
      snapshot: {
        project: { ...projectSnapshot.project, graph: { nodes: [], edges: [] } },
        definitions: [mediaSource, mrPreset],
      },
    })
    let currentEnvelope = studioEnvelope({ project_path: null, snapshot: null })
    let gateway: RecordingGateway
    gateway = new RecordingGateway(currentEnvelope, {
      presentations: () => presentationEnvelope(
        [
          {
            type_id: mediaSource.type_id, definition_version: mediaSource.version,
            title: '导入视频', description: '选择视频输入。', category_id: 'media',
            icon_token: 'source', palette_level: 'primary', keywords: [],
            parameter_groups: [], parameters: [], ports: [], card_summary_paths: [],
          },
          {
            type_id: mrPreset.type_id, definition_version: mrPreset.version,
            title: '马赛克修复', description: '外部视频处理。', category_id: 'transform',
            icon_token: 'transform', palette_level: 'primary', keywords: [],
            parameter_groups: [], parameters: [], ports: [], card_summary_paths: [],
          },
        ],
        [
          { category_id: 'media', title: '基础媒体节点', description: null, order: 1 },
          { category_id: 'transform', title: 'VideoTransform presets', description: null, order: 2 },
        ],
      ),
      command: (command) => {
        if (command.operation === 'create_project') currentEnvelope = createdEnvelope
        gateway.envelope = currentEnvelope
        return currentEnvelope
      },
    })
    render(<App gateway={gateway} nodeIdFactory={() => 'node.mr'} />)

    expect(await screen.findByText('尚未打开工程')).toBeInTheDocument()
    await user.type(screen.getByLabelText('工程路径'), 'C:\\synthetic\\media.zniku')
    await user.click(screen.getByRole('button', { name: '新建' }))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('create_project'))

    expect(await screen.findByRole('region', { name: '基础媒体节点' })).toBeInTheDocument()
    const presets = screen.getByRole('region', { name: 'VideoTransform presets' })
    await user.click(within(presets).getByRole('button', { name: /zniku\.media\.video_transform\.mr\.external/ }))
    expect(await screen.findByLabelText('node.mr 节点')).toBeInTheDocument()
    expect(screen.getByLabelText('节点参数 JSON')).toHaveValue(
      '{\n  "strength": 3,\n  "model_name": "Synthetic Model"\n}',
    )
  })

  it('打开、新建、参数保存并通过结构化命令执行 Run all', async () => {
    const user = userEvent.setup()
    const gateway = new RecordingGateway()
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')

    await user.click(screen.getByText('开发入口'))
    await user.click(screen.getByRole('button', { name: '打开' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'open_project',
      path: 'C:\\synthetic\\project.zniku',
    }))

    await user.clear(screen.getByLabelText('工程路径'))
    await user.type(screen.getByLabelText('工程路径'), 'C:\\synthetic\\new.zniku')
    await user.click(screen.getByText('高级工程信息'))
    await user.clear(screen.getByLabelText('Project name'))
    await user.type(screen.getByLabelText('Project name'), 'New Project')
    await user.click(screen.getByRole('button', { name: '新建' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'create_project',
      path: 'C:\\synthetic\\new.zniku',
      name: 'New Project',
    }))

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    fireEvent.change(await screen.findByLabelText('节点参数 JSON'), {
      target: { value: '{"strength":7,"model_name":"Synthetic Model"}' },
    })
    await user.click(screen.getByRole('button', { name: '应用设置' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => {
      const command = gateway.commands.at(-1)
      expect(command?.operation).toBe('save_project')
      if (command?.operation === 'save_project') {
        expect(command.project.graph.nodes.find((node) => node.node_id === 'transform')?.parameters)
          .toEqual({ strength: 7, model_name: 'Synthetic Model' })
      }
    })

    await user.click(screen.getByRole('button', { name: 'Run all' }))
    await waitFor(() => expect(gateway.commands.slice(-2).map((command) => command.operation)).toEqual([
      'save_project',
      'run_all',
    ]))
  })

  it('Handoff 显示服务端合同/失败原因，等待时长不计入上游执行时间', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-24T00:00:12Z'))
    const base = handoffDetailEnvelope()
    const projected: RunDetailEnvelope = {
      ...base,
      run: { ...base.run, node_runs: base.run.node_runs.map((item) => item.node_id !== 'transform' ? item : {
        ...item, started_at: '2026-08-24T00:00:10Z',
        external_handoff: { ...item.external_handoff!, created_at: '2026-08-24T00:00:10Z' },
      }) },
      handoff_contracts: [{
        node_run_id: handoffFixtureIds.transformNodeRun, handoff_id: handoffFixtureIds.handoff,
        input_artifact_id: base.artifacts[0]!.artifact_id, title: 'Synthetic 输出合同',
        fields: [{ label: '输出 exact N', value: '199' }, { label: '输出 canonical FPS', value: '60000/1001' }],
      }],
    }
    const gateway = new RecordingGateway(handoffEnvelope(), {
      detail: () => projected,
      readiness: (_runId, _nodeRunId, probe) => handoffReadinessEnvelope(probe ? 'probe_failed' : 'present', probe),
    })
    render(<App gateway={gateway} />)
    await flushReact()
    const queue = screen.getByLabelText('外部处理：test.transform')
    expect(queue).toHaveTextContent('199')
    expect(queue).toHaveTextContent('60000/1001')
    expect(queue).toHaveTextContent('已等待 2 秒')
    expect(queue).not.toHaveTextContent('已等待 11 秒')
    fireEvent.click(within(queue).getByRole('button', { name: '检查输出' }))
    await flushReact()
    expect(queue).toHaveTextContent('synthetic probe failed')
    fireEvent.click(within(queue).getByRole('button', { name: /transform/ }))
    expect(screen.getAllByLabelText('Synthetic 输出合同')).toHaveLength(1)
    expect(screen.getAllByText('out · synthetic probe failed')).toHaveLength(2)
    expect(within(queue).getByRole('button', { name: '检查输出' })).toBeEnabled()
    expect(within(queue).getByRole('button', { name: '提交并继续' })).toBeDisabled()
    expect(gateway.commands).toEqual([])
  })

  it('完整预检失败说明跨三轮 passive polling 保留，下一次显式检查后清除且不代替 readiness', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-24T00:00:12Z'))
    let fullChecks = 0
    const gateway = new RecordingGateway(handoffEnvelope(), {
      readiness: (_runId, _nodeRunId, probe) => {
        if (!probe) return handoffReadinessEnvelope('present', false)
        fullChecks += 1
        if (fullChecks > 1) return handoffReadinessEnvelope('probe_passed', true)
        const result = handoffReadinessEnvelope('probe_failed', true)
        return { ...result, targets: result.targets.map((target) => ({ ...target, message: 'E_AV27_FI_DOUBLE_COUNT: expected 199, observed 200' })) }
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    const queue = screen.getByLabelText('外部处理：test.transform')
    fireEvent.click(within(queue).getByRole('button', { name: '检查输出' }))
    await flushReact()
    expect(within(queue).getByLabelText('上次完整预检失败')).toHaveTextContent('E_AV27_FI_DOUBLE_COUNT')
    expect(gateway.commands).toEqual([])
    await act(async () => { await vi.advanceTimersByTimeAsync(4_501) })
    expect(gateway.readinessArguments.filter(([, , probe]) => !probe).length).toBeGreaterThanOrEqual(4)
    const previousFailure = within(queue).getByLabelText('上次完整预检失败')
    expect(previousFailure).toHaveTextContent('E_AV27_FI_DOUBLE_COUNT')
    expect(previousFailure).toHaveTextContent('不代表当前文件仍然失败')
    expect(queue).toHaveTextContent('已发现目标文件，尚未完成检查')
    expect(within(queue).getByRole('button', { name: '检查输出' })).toBeEnabled()
    fireEvent.click(within(queue).getByRole('button', { name: '检查输出' }))
    await flushReact()
    expect(fullChecks).toBe(2)
    expect(screen.queryByLabelText('上次完整预检失败')).not.toBeInTheDocument()
    expect(gateway.commands).toEqual([])
    fireEvent.click(within(queue).getByRole('button', { name: '提交并继续' }))
    await flushReact()
    expect(gateway.commands.at(-1)?.operation).toBe('submit_external')
  })

  it('上次完整预检失败绑定 handoff identity，不能串到新 attempt 或新 Project', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-24T00:00:12Z'))
    let selectedDetail = handoffDetailEnvelope()
    let message = 'E_OLD_HANDOFF: previous output is invalid'
    const gateway = new RecordingGateway(handoffEnvelope(), {
      detail: () => selectedDetail,
      readiness: (runId, nodeRunId, probe) => {
        const handoff = selectedDetail.run.node_runs.find((item) => item.node_run_id === nodeRunId)!.external_handoff!
        const result = handoffReadinessEnvelope(probe ? 'probe_failed' : 'present', probe)
        return { ...result, run_id: runId, node_run_id: nodeRunId, handoff_id: handoff.handoff_id,
          targets: result.targets.map((target) => ({ ...target, message: probe ? message : null })) }
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    fireEvent.click(within(screen.getByLabelText('外部处理：test.transform')).getByRole('button', { name: '检查输出' }))
    await flushReact()
    expect(screen.getByLabelText('上次完整预检失败')).toHaveTextContent('E_OLD_HANDOFF')
    const prior = selectedDetail.run.node_runs.find((item) => item.node_id === 'transform')!
    const nextNodeRunId = '00000000-0000-4000-8000-000000000081'
    selectedDetail = { ...selectedDetail, run: { ...selectedDetail.run, node_runs: [
      ...selectedDetail.run.node_runs,
      { ...prior, node_run_id: nextNodeRunId, attempt: 2,
        external_handoff: { ...prior.external_handoff!, node_run_id: nextNodeRunId, handoff_id: '00000000-0000-4000-8000-000000000082' } },
    ] } }
    await act(async () => { await vi.advanceTimersByTimeAsync(1_501) })
    expect(screen.queryByLabelText('上次完整预检失败')).not.toBeInTheDocument()
    message = 'E_NEW_HANDOFF: current output is invalid'
    fireEvent.click(within(screen.getByLabelText('外部处理：test.transform')).getByRole('button', { name: '检查输出' }))
    await flushReact()
    expect(screen.getByLabelText('上次完整预检失败')).toHaveTextContent('E_NEW_HANDOFF')
    expect(screen.getByLabelText('上次完整预检失败')).not.toHaveTextContent('E_OLD_HANDOFF')
    // 即使服务错误复用相同 Run/NodeRun IDs，resolved Project path 切换仍清除页面检查历史。
    gateway.envelope = { ...gateway.envelope, project_path: 'C:\\synthetic\\other-project.zniku' }
    fireEvent.click(screen.getByRole('button', { name: '打开' }))
    await flushReact()
    expect(gateway.commands.at(-1)?.operation).toBe('open_project')
    expect(screen.queryByLabelText('上次完整预检失败')).not.toBeInTheDocument()
  })

  it('External Handoff 队列展示路径、模型、日志并执行两阶段精确 Submit', async () => {
    const user = userEvent.setup()
    const gateway = new RecordingGateway(handoffEnvelope())
    render(<App gateway={gateway} />)

    let queueItem = await screen.findByLabelText('外部处理：test.transform')
    expect(queueItem).toHaveTextContent('test.transform')
    expect(queueItem).toHaveTextContent('已等待')
    expect(queueItem).toHaveTextContent('C:\\synthetic\\source.mkv')
    expect(queueItem).toHaveTextContent('C:\\synthetic\\attempt-transform\\output.mkv')
    expect(within(queueItem).getByRole('button', { name: '复制输入路径' })).toBeEnabled()
    expect(within(queueItem).getByRole('button', { name: '复制目标路径' })).toBeEnabled()

    await user.click(within(queueItem).getByRole('button', { name: /transform/ }))
    expect(await screen.findByRole('link', { name: '外部处理助手' })).toBeInTheDocument()
    expect(await screen.findByText('等待外部输出')).toBeInTheDocument()
    // 选中人工步骤后，助手移到参数表之前；重新取得实际挂载的区域，而非点击旧 DOM。
    queueItem = await screen.findByLabelText('外部处理：test.transform')

    await user.click(within(queueItem).getByRole('button', { name: '检查输出' }))
    await waitFor(() => expect(gateway.readinessArguments.filter((call) => call[2])).toEqual([[
      handoffFixtureIds.run,
      handoffFixtureIds.transformNodeRun,
      true,
    ]]))
    expect(gateway.commands).toEqual([])
    await waitFor(() => expect(within(queueItem).getByRole('button', { name: '提交并继续' })).toBeEnabled())
    await user.click(within(queueItem).getByRole('button', { name: '提交并继续' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'submit_external',
      run_id: handoffFixtureIds.run,
      node_run_id: handoffFixtureIds.transformNodeRun,
      handoff_id: handoffFixtureIds.handoff,
    }))

    await user.click(screen.getByRole('button', { name: '查看当前 Graph' }))
    fireEvent.click(await screen.findByLabelText('transform 节点'))
    await user.click(screen.getByRole('button', { name: 'Run to here' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toMatchObject({
      operation: 'run_to',
      node_id: 'transform',
    }))

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    await user.click(screen.getByRole('button', { name: 'Rerun from here' }))
    expect(gateway.commands.at(-1)?.operation).toBe('run_to')
    await user.click(await screen.findByRole('button', { name: '确认从头重新处理' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'rerun_from_here',
      expected_storage_revision: 0,
      project_session_id: studioEnvelope().project_session_id,
      run_id: handoffFixtureIds.run,
      node_id: 'transform',
    }))
  })

  it.each(['cancelled', 'interrupted'] as const)(
    '显示 completed/stale/failed、%s 原因，并将 failed Run 投影为 Next action',
    async (reason) => {
      render(<App gateway={new RecordingGateway(failedStatusEnvelope(reason))} />)
      expect(await screen.findByText('test.transform · 需要处理问题')).toBeInTheDocument()

      const sourceCard = await screen.findByLabelText('source 节点')
      expect(within(sourceCard).getByText('Completed')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: '查看当前 Graph' }))
      const currentSourceCard = await screen.findByLabelText('source 节点')
      expect(within(currentSourceCard).getByText('Stale')).toBeInTheDocument()
      fireEvent.click(currentSourceCard)
      expect(await screen.findByText('source.mkv')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: '查看失败步骤' }))
      const runtime = await screen.findByLabelText('步骤处理状态')
      expect(runtime).toHaveTextContent('failed')
      expect(runtime).not.toHaveTextContent('40%')
      expect(runtime).toHaveTextContent(reason)
      expect(runtime).toHaveTextContent(reason === 'cancelled' ? '操作者取消' : '应用重启中断')
    },
  )

  it('active_operation 期间禁用工程切换与 Runtime mutation，但允许编辑当前图', async () => {
    const gateway = new RecordingGateway({ ...handoffEnvelope(), active_operation: 'abandon_run' })
    render(<App gateway={gateway} />)
    const queueItem = await screen.findByLabelText('外部处理：test.transform')

    for (const name of ['打开', '新建', '保存', 'Run all', 'Run to here', 'Rerun from here']) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
    }
    expect(within(queueItem).getByRole('button', { name: '检查输出' })).toBeDisabled()
    fireEvent.click(screen.getByText('高级 → 放弃本次处理'))
    expect(screen.getByRole('button', { name: 'Abandon Run' })).toBeDisabled()
  })

  it('后启动的 completed 局部 Run 不隐藏仍 waiting 的整图 Run', async () => {
    render(<App gateway={new RecordingGateway(threeRunEnvelope())} />)

    const selector = await screen.findByRole('combobox', { name: '查看 Run' })
    expect(selector).toHaveValue(threeRunFixtureIds.waitingFullRun)
    expect(within(selector).getAllByRole('option')).toHaveLength(3)
    expect(await screen.findByText('test.transform · 等待外部处理')).toBeInTheDocument()
    expect(within(screen.getByLabelText('transform 节点')).getByText('Waiting external'))
      .toBeInTheDocument()
  })

  it('手动选择 terminal Run 后 refresh 不抢占选择且全局 Next action 保持更新', async () => {
    vi.useFakeTimers()
    const gateway = new RecordingGateway(threeRunEnvelope())
    render(<App gateway={gateway} />)
    await flushReact()

    const selector = screen.getByRole('combobox', { name: '查看 Run' })
    fireEvent.change(selector, { target: { value: threeRunFixtureIds.laterLocalRun } })
    await flushReact()
    expect(selector).toHaveValue(threeRunFixtureIds.laterLocalRun)
    expect(screen.getByRole('button', { name: '查看需处理记录' }))
      .toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_501)
    })
    expect(gateway.inspectCount).toBeGreaterThan(1)
    expect(selector).toHaveValue(threeRunFixtureIds.laterLocalRun)
  })

  it('Run 非终态且 active_operation=null 时仍持续轮询', async () => {
    vi.useFakeTimers()
    const gateway = new RecordingGateway(handoffEnvelope())
    render(<App gateway={gateway} />)
    await flushReact()
    expect(gateway.inspectCount).toBe(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_501)
    })
    expect(gateway.inspectCount).toBeGreaterThan(1)
  })

  it('view Run 404 后仅按 fresh authority 重建选择，不重新选回缓存旧 Run', async () => {
    vi.useFakeTimers()
    const oldPage = new Deferred<RunSummaryPageEnvelope>()
    const initial = { ...threeRunEnvelope(), next_run_cursor: 'cursor.old' }
    const freshSummary = initial.run_summaries.find(
      (item) => item.run_id === threeRunFixtureIds.laterLocalRun,
    )!
    const fresh = {
      ...initial,
      // 模拟 status 窗口短暂滞后：精确 404 的 ID 仍出现在 fresh status 中，也不得重选。
      run_summaries: [initial.run_summaries[1]!, freshSummary],
      active_run_id: threeRunFixtureIds.waitingFullRun,
      next_run_cursor: null,
    }
    const gateway = new RecordingGateway(initial, {
      listRuns: () => oldPage.promise,
      inspect: (viewRunId, count) => {
        if (count === 1) return initial
        if (count === 2) {
          expect(viewRunId).toBe(threeRunFixtureIds.waitingFullRun)
          throw new StudioGatewayError('Run 不存在', {
            code: 'E_PROJECT_SERVICE_RUN_NOT_FOUND',
            httpStatus: 404,
          })
        }
        expect(viewRunId).toBeNull()
        return fresh
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    const selector = screen.getByRole('combobox', { name: '查看 Run' }) as HTMLSelectElement
    expect(selector).toHaveValue(threeRunFixtureIds.waitingFullRun)
    fireEvent.click(screen.getByRole('button', { name: '加载更早 Run' }))
    await flushReact()
    expect(gateway.historyArguments).toEqual([['cursor.old', 20]])

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_501)
    })

    expect(gateway.inspectArguments.slice(0, 3)).toEqual([
      null,
      threeRunFixtureIds.waitingFullRun,
      null,
    ])
    expect(selector).toHaveValue(threeRunFixtureIds.laterLocalRun)
    expect([...selector.options].map((option) => option.value)).not.toContain(
      threeRunFixtureIds.waitingFullRun,
    )
    expect(screen.getByRole('status')).toHaveTextContent(
      '先前选择的 Run 已不存在，已重新选择可用 Run。',
    )

    await act(async () => oldPage.resolve({
      contract_version: '0.3.0',
      run_summaries: [initial.run_summaries[1]!],
      next_run_cursor: 'cursor.stale',
    }))
    expect([...selector.options].map((option) => option.value)).not.toContain(
      threeRunFixtureIds.waitingFullRun,
    )
    expect(screen.queryByRole('button', { name: '加载更早 Run' })).not.toBeInTheDocument()
  })

  it('非终态 Run 的慢 status inspect 保持 single-flight', async () => {
    vi.useFakeTimers()
    const slow = new Deferred<StatusEnvelope>()
    let progress = 0.1
    const gateway = new RecordingGateway(runningProgressEnvelope(progress, null), {
      inspect: (_viewRunId, count) => {
        if (count === 1) return runningProgressEnvelope(0.1, null)
        if (count === 2) return slow.promise
        return runningProgressEnvelope(progress, null)
      },
      detail: () => projectedProgressDetail(0.05, progress),
    })
    render(<App gateway={gateway} />)
    await flushReact()
    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(within(screen.getByLabelText('步骤处理状态')).getByText('10%')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000)
    })
    expect(gateway.inspectCount).toBe(2)

    progress = 0.8
    await act(async () => {
      slow.resolve(runningProgressEnvelope(progress, null))
      await Promise.resolve()
    })
    expect(within(screen.getByLabelText('步骤处理状态')).getByText('80%')).toBeInTheDocument()
  })

  it('优先展示 live projection，并在节点卡与 Inspector 显示测量值和 wall elapsed', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-24T00:00:05Z'))
    const detail = projectedProgressDetail(0.2, 0.8, {
      current: 80,
      total: 100,
      unit: 'frames',
    })
    const gateway = new RecordingGateway(runningProgressEnvelope(0.2, null), {
      detail: () => detail,
    })
    render(<App gateway={gateway} />)
    await flushReact()

    const card = screen.getByLabelText('source 节点')
    expect(within(card).getByText('80%')).toBeInTheDocument()
    expect(within(card).getByText('80 / 100 frames')).toBeInTheDocument()
    expect(within(card).getByText('elapsed 5s')).toBeInTheDocument()
    expect(screen.getByLabelText('Run summary')).not.toHaveTextContent('%')

    fireEvent.click(card)
    expect(screen.getByLabelText('步骤处理状态')).toHaveTextContent('80%')
    expect(screen.getByLabelText('步骤实测进度')).toHaveTextContent('80 / 100 帧')
    expect(screen.getByLabelText('步骤实测进度')).toHaveTextContent('elapsed 5s')
  })

  it('running automatic 无可信 fraction 时显示 indeterminate，waiting/completed 不伪造百分比', async () => {
    const commandBase = runningProgressDetail(0.4)
    const commandDetail: RunDetailEnvelope = {
      ...commandBase,
      run: {
        ...commandBase.run,
        definitions_snapshot: commandBase.run.definitions_snapshot.map((definition) =>
          definition.type_id === sourceDefinition.type_id
            ? {
                ...definition,
                executor: {
                  kind: 'command' as const,
                  executable: 'synthetic',
                  argv: [],
                  output_paths: [],
                },
              }
            : definition,
        ),
        node_runs: commandBase.run.node_runs,
      },
    }
    render(
      <App
        gateway={new RecordingGateway(runningProgressEnvelope(0, null), {
          detail: () => commandDetail,
        })}
      />,
    )
    const runningCard = await screen.findByLabelText('source 节点')
    expect(within(runningCard).getByLabelText('进度不确定')).toHaveTextContent('Working…')
    expect(within(runningCard).queryByText('0%')).not.toBeInTheDocument()
    expect(within(runningCard).queryByText('40%')).not.toBeInTheDocument()
    fireEvent.click(runningCard)
    expect(screen.getByLabelText('步骤处理状态')).not.toHaveTextContent('0%')
    expect(screen.getByLabelText('步骤处理状态')).not.toHaveTextContent('40%')
    expect(screen.getByLabelText('步骤实测进度')).toHaveTextContent('正在处理，暂时没有可计算的百分比。')

    cleanup()
    const waitingBase = handoffDetailEnvelope()
    const waitingDetail: RunDetailEnvelope = {
      ...waitingBase,
      run: {
        ...waitingBase.run,
        node_runs: waitingBase.run.node_runs.map((item) =>
          item.node_id === 'transform' ? { ...item, progress: 0 } : item,
        ),
      },
    }
    render(
      <App
        gateway={new RecordingGateway(handoffEnvelope(), { detail: () => waitingDetail })}
      />,
    )
    const waitingCard = await screen.findByLabelText('transform 节点')
    expect(within(waitingCard).getByText('Waiting external')).toBeInTheDocument()
    expect(within(waitingCard).queryByText('0%')).not.toBeInTheDocument()
    const completedCard = screen.getByLabelText('source 节点')
    expect(within(completedCard).getByText('Completed')).toBeInTheDocument()
    expect(within(completedCard).queryByText('100%')).not.toBeInTheDocument()
    fireEvent.click(waitingCard)
    expect(screen.getByLabelText('步骤处理状态')).not.toHaveTextContent('0%')
  })

  it('failed automatic 显示最后可信 persisted fraction 和失败原因，不补到 100%', async () => {
    const base = runningProgressDetail(0.4)
    const error = { reason: 'cancelled' as const, message: '操作者取消 automatic attempt' }
    const failedDetail: RunDetailEnvelope = {
      ...base,
      run: {
        ...base.run,
        state: 'failed',
        ended_at: '2026-08-24T00:00:03Z',
        error,
        node_runs: base.run.node_runs.map((item) =>
          item.node_id === 'source'
            ? {
                ...item,
                state: 'failed' as const,
                ended_at: '2026-08-24T00:00:03Z',
                progress: 0.4,
                error,
              }
            : item,
        ),
      },
    }
    render(
      <App
        gateway={new RecordingGateway(failedStatusEnvelope('cancelled'), {
          detail: () => failedDetail,
        })}
      />,
    )
    const card = await screen.findByLabelText('source 节点')
    expect(within(card).getByText('40%')).toBeInTheDocument()
    expect(within(card).queryByText('100%')).not.toBeInTheDocument()
    fireEvent.click(card)
    const runtime = screen.getByLabelText('步骤处理状态')
    expect(runtime).toHaveTextContent('40%')
    expect(runtime).toHaveTextContent('操作者取消 automatic attempt')
    expect(runtime).not.toHaveTextContent('100%')
  })

  it('Project generation 变化后迟到 status 不得回退可见进度', async () => {
    vi.useFakeTimers()
    const slow = new Deferred<StatusEnvelope>()
    let progress = 0.1
    const gateway = new RecordingGateway(runningProgressEnvelope(progress, null), {
      inspect: (_viewRunId, count) =>
        count === 1 ? runningProgressEnvelope(0.1, null) : slow.promise,
      detail: () => runningProgressDetail(progress),
      command: () => {
        progress = 0.8
        return { ...runningProgressEnvelope(progress, null), project_path: 'C:\\synthetic\\reopen.zniku' }
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(within(screen.getByLabelText('步骤处理状态')).getByText('10%')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    expect(gateway.inspectCount).toBe(2)

    fireEvent.change(screen.getByLabelText('工程路径'), {
      target: { value: 'C:\\synthetic\\reopen.zniku' },
    })
    fireEvent.click(screen.getByRole('button', { name: '打开' }))
    await flushReact()
    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(within(screen.getByLabelText('步骤处理状态')).getByText('80%')).toBeInTheDocument()

    await act(async () => {
      slow.resolve(runningProgressEnvelope(0.2, null))
      await Promise.resolve()
    })
    expect(within(screen.getByLabelText('步骤处理状态')).getByText('80%')).toBeInTheDocument()
    expect(within(screen.getByLabelText('步骤处理状态')).queryByText('20%')).not.toBeInTheDocument()
  })

  it('切换 view Run 后旧 Run 的迟到 progress projection 不得覆盖新 Run', async () => {
    vi.useFakeTimers()
    const runA = handoffFixtureIds.run
    const runB = '00000000-0000-4000-8000-000000000020'
    const sourceB = '00000000-0000-4000-8000-000000000021'
    const initial = runningProgressEnvelope(0.2, null)
    const summaryA = initial.run_summaries[0]!
    const envelope: StatusEnvelope = {
      ...initial,
      run_summaries: [
        summaryA,
        {
          ...summaryA,
          run_id: runB,
          created_at: '2026-08-23T23:59:00Z',
          started_at: '2026-08-23T23:59:00Z',
          latest_activity_at: '2026-08-23T23:59:02Z',
        },
      ],
    }
    const detailA = projectedProgressDetail(0.2, 0.8, { current: 80, total: 100 })
    const baseB = projectedProgressDetail(0.1, 0.3, { current: 30, total: 100 })
    const detailB: RunDetailEnvelope = {
      ...baseB,
      progress_samples: baseB.progress_samples.map((sample) => ({
        ...sample,
        node_run_id: sourceB,
      })),
      run: {
        ...baseB.run,
        run_id: runB,
        node_runs: baseB.run.node_runs.map((item) => ({
          ...item,
          run_id: runB,
          node_run_id: item.node_id === 'source' ? sourceB : `${item.node_run_id.slice(0, -2)}2${item.node_run_id.slice(-1)}`,
        })),
      },
    }
    const slowA = new Deferred<RunDetailEnvelope>()
    let runACalls = 0
    const gateway = new RecordingGateway(envelope, {
      detail: (runId) => {
        if (runId === runB) return detailB
        runACalls += 1
        return runACalls === 1 ? detailA : slowA.promise
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    expect(within(screen.getByLabelText('source 节点')).getByText('80%')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    fireEvent.change(screen.getByRole('combobox', { name: '查看 Run' }), {
      target: { value: runB },
    })
    await flushReact()
    expect(within(screen.getByLabelText('source 节点')).getByText('30%')).toBeInTheDocument()

    await act(async () => {
      slowA.resolve(projectedProgressDetail(0.2, 0.9, { current: 90, total: 100 }))
      await Promise.resolve()
    })
    expect(screen.getByRole('combobox', { name: '查看 Run' })).toHaveValue(runB)
    expect(within(screen.getByLabelText('source 节点')).getByText('30%')).toBeInTheDocument()
    expect(within(screen.getByLabelText('source 节点')).queryByText('90%')).not.toBeInTheDocument()
    expect(runA).toBe(handoffFixtureIds.run)
  })

  it('命令等待时切换 viewRunId 仍由命令 owner 释放 busy', async () => {
    const slowRun = new Deferred<StatusEnvelope>()
    const envelope = threeRunEnvelope()
    const gateway = new RecordingGateway(envelope, {
      command: (command) => command.operation === 'run_all' ? slowRun.promise : envelope,
    })
    render(<App gateway={gateway} />)
    await screen.findByRole('combobox', { name: '查看 Run' })

    fireEvent.click(screen.getByRole('button', { name: 'Run all' }))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('run_all'))
    fireEvent.change(screen.getByRole('combobox', { name: '查看 Run' }), {
      target: { value: threeRunFixtureIds.laterLocalRun },
    })
    expect(screen.getByRole('button', { name: 'Run all' })).toBeDisabled()

    await act(async () => {
      slowRun.resolve(envelope)
      await Promise.resolve()
    })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run all' })).toBeEnabled())
  })

  it('detail channel 失败时保留最后可信进度，只禁用 detail mutation', async () => {
    vi.useFakeTimers()
    let detailCount = 0
    const gateway = new RecordingGateway(runningProgressEnvelope(0.6, null), {
      detail: () => {
        detailCount += 1
        if (detailCount === 1) return runningProgressDetail(0.6)
        throw new Error('detail offline')
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(within(screen.getByLabelText('步骤处理状态')).getByText('60%')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    expect(within(screen.getByLabelText('步骤处理状态')).getByText('60%')).toBeInTheDocument()
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('DETAIL STALE')
    expect(screen.getByRole('button', { name: 'Rerun from here' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Run all' })).toBeEnabled()
  })

  it.each([
    ['projection 回退', projectedProgressDetail(0.2, 0.6, { current: 60, total: 100 })],
    ['projection 消失', runningProgressDetail(0.2)],
  ])('同 attempt 的%s保留最后可信值并立即定向 reinspect', async (_label, regressed) => {
    vi.useFakeTimers()
    const fresh = new Deferred<RunDetailEnvelope>()
    const first = projectedProgressDetail(0.2, 0.8, { current: 80, total: 100 })
    const gateway = new RecordingGateway(runningProgressEnvelope(0.2, null), {
      detail: (_runId, count) => {
        if (count === 1) return first
        if (count === 2) return regressed
        return fresh.promise
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    expect(within(screen.getByLabelText('source 节点')).getByText('80%')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    expect(gateway.inspectRunCount).toBe(3)
    expect(within(screen.getByLabelText('source 节点')).getByText('80%')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('E_STUDIO_PROGRESS_REGRESSION')

    await act(async () => {
      fresh.resolve(projectedProgressDetail(0.2, 0.9, { current: 90, total: 100 }))
      await Promise.resolve()
    })
    expect(within(screen.getByLabelText('source 节点')).getByText('90%')).toBeInTheDocument()
  })

  it('同 attempt 持续回退时只立即重查一次，之后恢复 750ms 节奏', async () => {
    vi.useFakeTimers()
    const repeatedRegression = new Deferred<RunDetailEnvelope>()
    const nextScheduled = new Deferred<RunDetailEnvelope>()
    const first = projectedProgressDetail(0.2, 0.8, { current: 80, total: 100 })
    const regressed = projectedProgressDetail(0.2, 0.6, { current: 60, total: 100 })
    const gateway = new RecordingGateway(runningProgressEnvelope(0.2, null), {
      detail: (_runId, count) => {
        if (count === 1) return first
        if (count === 2) return regressed
        if (count === 3) return repeatedRegression.promise
        return nextScheduled.promise
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    expect(gateway.inspectRunCount).toBe(3)

    await act(async () => {
      repeatedRegression.resolve(regressed)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(gateway.inspectRunCount).toBe(3)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(749)
    })
    expect(gateway.inspectRunCount).toBe(3)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(gateway.inspectRunCount).toBe(4)
  })

  it('terminal status 不得让旧 running detail flight 冒领 final refresh', async () => {
    vi.useFakeTimers()
    const terminalStatus = new Deferred<StatusEnvelope>()
    const staleRunningDetail = new Deferred<RunDetailEnvelope>()
    const initialStatus = runningProgressEnvelope(0.2, null)
    const first = projectedProgressDetail(0.2, 0.4, { current: 40, total: 100 })
    const endedAt = '2026-08-24T00:00:03Z'
    const completedDetail: RunDetailEnvelope = {
      ...first,
      progress_samples: [],
      run: {
        ...first.run,
        state: 'completed',
        ended_at: endedAt,
        node_runs: first.run.node_runs.map((nodeRun) => ({
          ...nodeRun,
          state: 'completed' as const,
          started_at: nodeRun.started_at ?? endedAt,
          ended_at: endedAt,
          progress: nodeRun.node_id === 'source' ? 1 : null,
          error: null,
          external_handoff: null,
        })),
      },
    }
    const completedStatus: StatusEnvelope = {
      ...initialStatus,
      active_operation: null,
      run_summaries: initialStatus.run_summaries.map((summary) => ({
        ...summary,
        state: 'completed' as const,
        state_counts: {
          pending: 0,
          running: 0,
          waiting_external: 0,
          completed: summary.node_count,
          failed: 0,
        },
        actionable: false,
        requires_operator_action: false,
        ended_at: endedAt,
        latest_activity_at: endedAt,
      })),
    }
    const gateway = new RecordingGateway(initialStatus, {
      inspect: (_viewRunId, count) => (count === 1 ? initialStatus : terminalStatus.promise),
      detail: (_runId, count) => {
        if (count === 1) return first
        if (count === 2) return staleRunningDetail.promise
        return completedDetail
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    expect(gateway.inspectCount).toBe(2)
    expect(gateway.inspectRunCount).toBe(2)

    await act(async () => {
      terminalStatus.resolve(completedStatus)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(gateway.inspectRunCount).toBe(2)

    await act(async () => {
      staleRunningDetail.resolve(first)
      await Promise.resolve()
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(gateway.inspectRunCount).toBe(3)
    expect(within(screen.getByLabelText('source 节点')).getByText('Completed')).toBeInTheDocument()
    expect(within(screen.getByLabelText('source 节点')).queryByText('40%')).not.toBeInTheDocument()
  })

  it('terminal fresh 已排队时旧 detail 失败仍必须遵守 750ms backoff', async () => {
    vi.useFakeTimers()
    const terminalStatus = new Deferred<StatusEnvelope>()
    const failingRunningDetail = new Deferred<RunDetailEnvelope>()
    const initialStatus = runningProgressEnvelope(0.2, null)
    const first = projectedProgressDetail(0.2, 0.4, { current: 40, total: 100 })
    const endedAt = '2026-08-24T00:00:03Z'
    const completedDetail: RunDetailEnvelope = {
      ...first,
      progress_samples: [],
      run: {
        ...first.run,
        state: 'completed',
        ended_at: endedAt,
        node_runs: first.run.node_runs.map((nodeRun) => ({
          ...nodeRun,
          state: 'completed' as const,
          started_at: nodeRun.started_at ?? endedAt,
          ended_at: endedAt,
          progress: nodeRun.node_id === 'source' ? 1 : null,
          error: null,
          external_handoff: null,
        })),
      },
    }
    const completedStatus: StatusEnvelope = {
      ...initialStatus,
      active_operation: null,
      run_summaries: initialStatus.run_summaries.map((summary) => ({
        ...summary,
        state: 'completed' as const,
        state_counts: {
          pending: 0,
          running: 0,
          waiting_external: 0,
          completed: summary.node_count,
          failed: 0,
        },
        actionable: false,
        requires_operator_action: false,
        ended_at: endedAt,
        latest_activity_at: endedAt,
      })),
    }
    const gateway = new RecordingGateway(initialStatus, {
      inspect: (_viewRunId, count) => (count === 1 ? initialStatus : terminalStatus.promise),
      detail: (_runId, count) => {
        if (count === 1) return first
        if (count === 2) return failingRunningDetail.promise
        return completedDetail
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    expect(gateway.inspectRunCount).toBe(2)

    await act(async () => {
      terminalStatus.resolve(completedStatus)
      await Promise.resolve()
      await Promise.resolve()
    })
    await act(async () => {
      failingRunningDetail.reject(new Error('stale running detail failed'))
      await Promise.resolve()
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(gateway.inspectRunCount).toBe(2)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(749)
    })
    expect(gateway.inspectRunCount).toBe(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(gateway.inspectRunCount).toBe(3)
    expect(within(screen.getByLabelText('source 节点')).getByText('Completed')).toBeInTheDocument()
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('DETAIL OK')
  })

  it('running projection 转 completed 时按终态显示且不误触发回退重查', async () => {
    vi.useFakeTimers()
    const first = projectedProgressDetail(0.2, 0.8, { current: 80, total: 100 })
    const completed: RunDetailEnvelope = {
      ...first,
      progress_samples: [],
      run: {
        ...first.run,
        state: 'completed',
        ended_at: '2026-08-24T00:00:03Z',
        node_runs: first.run.node_runs.map((nodeRun) =>
          nodeRun.node_id === 'source'
            ? {
                ...nodeRun,
                state: 'completed',
                progress: null,
                ended_at: '2026-08-24T00:00:03Z',
              }
            : nodeRun,
        ),
      },
    }
    const gateway = new RecordingGateway(runningProgressEnvelope(0.2, null), {
      detail: (_runId, count) => (count === 1 ? first : completed),
    })
    render(<App gateway={gateway} />)
    await flushReact()
    const sourceCard = screen.getByLabelText('source 节点')
    expect(within(sourceCard).getByText('80%')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    expect(gateway.inspectRunCount).toBe(2)
    expect(within(sourceCard).getByText('Completed')).toBeInTheDocument()
    expect(within(sourceCard).queryByText('80%')).not.toBeInTheDocument()
    expect(screen.queryByText(/E_STUDIO_PROGRESS_REGRESSION/)).not.toBeInTheDocument()
  })

  it('更高 attempt 使用新 node_run_id 建立新的 progress 生命周期', async () => {
    vi.useFakeTimers()
    const first = projectedProgressDetail(0.2, 0.8, { current: 80, total: 100 })
    const oldSource = first.run.node_runs[0]!
    const next: RunDetailEnvelope = {
      ...first,
      progress_samples: [],
      run: {
        ...first.run,
        node_runs: [
          {
            ...oldSource,
            state: 'failed',
            ended_at: '2026-08-24T00:00:03Z',
            progress: 0.8,
            error: { reason: 'interrupted', message: '旧 attempt 已结束' },
          },
          {
            ...oldSource,
            node_run_id: '00000000-0000-4000-8000-000000000099',
            attempt: 2,
            created_at: '2026-08-24T00:00:04Z',
            started_at: '2026-08-24T00:00:04Z',
            progress: 0.1,
          },
          ...first.run.node_runs.slice(1),
        ],
      },
    }
    const gateway = new RecordingGateway(runningProgressEnvelope(0.2, null), {
      detail: (_runId, count) => (count === 1 ? first : next),
    })
    render(<App gateway={gateway} />)
    await flushReact()
    expect(within(screen.getByLabelText('source 节点')).getByText('80%')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    expect(within(screen.getByLabelText('source 节点')).getByText('10%')).toBeInTheDocument()
    expect(screen.queryByText(/E_STUDIO_PROGRESS_REGRESSION/)).not.toBeInTheDocument()
  })

  it('detail 轮询保持 single-flight，失败按独立 750/1500/3000/5000 退避且不拖慢 status', async () => {
    vi.useFakeTimers()
    const slow = new Deferred<RunDetailEnvelope>()
    let mode: 'slow' | 'failed' | 'recovered' = 'slow'
    const gateway = new RecordingGateway(runningProgressEnvelope(0.4, null), {
      inspect: (_viewRunId, count) => {
        const envelope = runningProgressEnvelope(0.4, null)
        return {
          ...envelope,
          run_summaries: envelope.run_summaries.map((summary) => ({
            ...summary,
            latest_activity_at: `2026-08-24T00:00:${String(Math.min(59, count)).padStart(2, '0')}Z`,
          })),
        }
      },
      detail: (_runId, count) => {
        if (count === 1) return runningProgressDetail(0.4)
        if (mode === 'slow') return slow.promise
        if (mode === 'recovered') return runningProgressDetail(0.7)
        throw new Error('detail offline')
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000)
    })
    expect(gateway.inspectRunCount).toBe(2)
    expect(gateway.inspectCount).toBeGreaterThan(2)

    mode = 'failed'
    await act(async () => {
      slow.reject(new Error('detail offline'))
      await Promise.resolve()
    })
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('DETAIL STALE')
    expect(screen.getByRole('alert')).toHaveTextContent('这次操作未能完成')
    expect(screen.getAllByText('detail offline').length).toBeGreaterThan(0)
    const afterSlowFailure = gateway.inspectRunCount

    await act(async () => {
      await vi.advanceTimersByTimeAsync(749)
    })
    expect(gateway.inspectRunCount).toBe(afterSlowFailure)
    expect(screen.getByRole('alert')).toHaveTextContent('这次操作未能完成')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(gateway.inspectRunCount).toBe(afterSlowFailure + 1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_499)
    })
    expect(gateway.inspectRunCount).toBe(afterSlowFailure + 1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(gateway.inspectRunCount).toBe(afterSlowFailure + 2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_999)
    })
    expect(gateway.inspectRunCount).toBe(afterSlowFailure + 2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(gateway.inspectRunCount).toBe(afterSlowFailure + 3)
    mode = 'recovered'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_999)
    })
    expect(gateway.inspectRunCount).toBe(afterSlowFailure + 3)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(gateway.inspectRunCount).toBe(afterSlowFailure + 4)
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('DETAIL OK')
    expect(within(screen.getByLabelText('source 节点')).getByText('70%')).toBeInTheDocument()
    expect(screen.queryByText('detail offline')).not.toBeInTheDocument()
  })

  it('只允许对引用 Run 执行闭包内的节点发起 Rerun', async () => {
    const envelope = threeRunEnvelope()
    const summary = envelope.run_summaries.find(
      (item) => item.run_id === threeRunFixtureIds.laterLocalRun,
    )!
    const gateway = new RecordingGateway(
      studioEnvelope({ active_run_id: summary.run_id, run_summaries: [summary] }),
      { detail: () => threeRunDetail(summary.run_id) },
    )
    render(<App gateway={gateway} />)
    await screen.findByText('Run snapshot')

    fireEvent.click(screen.getByRole('button', { name: '查看当前 Graph' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    expect(screen.getByRole('button', { name: 'Run to here' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Rerun from here' })).toBeDisabled()
  })

  it('actionable waiting Run 可显式 abandon，命令保持精确 Run identity', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const gateway = new RecordingGateway(handoffEnvelope())
    render(<App gateway={gateway} />)
    fireEvent.click(await screen.findByText('高级 → 放弃本次处理'))
    const button = screen.getByRole('button', { name: 'Abandon Run' })
    expect(button).toBeEnabled()

    fireEvent.click(button)
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'abandon_run',
      run_id: handoffFixtureIds.run,
    }))
  })

  it('Run history 使用每页 next cursor 连续翻页并保留 session 已见 summaries', async () => {
    const user = userEvent.setup()
    const fixture = threeRunEnvelope()
    const first = fixture.run_summaries[0]!
    const second = fixture.run_summaries[1]!
    const third = fixture.run_summaries[2]!
    const gateway = new RecordingGateway(
      { ...fixture, run_summaries: [first], next_run_cursor: 'cursor.page.1' },
      {
        listRuns: (cursor) =>
          cursor === 'cursor.page.1'
            ? {
                contract_version: '0.3.0',
                run_summaries: [second],
                next_run_cursor: 'cursor.page.2',
              }
            : {
                contract_version: '0.3.0',
                run_summaries: [third],
                next_run_cursor: null,
              },
      },
    )
    render(<App gateway={gateway} />)

    await user.click(await screen.findByRole('button', { name: '加载更早 Run' }))
    await waitFor(() => expect(gateway.historyArguments).toEqual([['cursor.page.1', 20]]))
    await user.click(await screen.findByRole('button', { name: '加载更早 Run' }))
    await waitFor(() => expect(gateway.historyArguments).toEqual([
      ['cursor.page.1', 20],
      ['cursor.page.2', 20],
    ]))

    const selector = screen.getByRole('combobox', { name: '查看 Run' }) as HTMLSelectElement
    expect([...selector.options].map((option) => option.value)).toEqual(expect.arrayContaining([
      first.run_id,
      second.run_id,
      third.run_id,
    ]))
    expect(screen.queryByRole('button', { name: '加载更早 Run' })).not.toBeInTheDocument()
  })

  it('旧工程历史分页失败不能污染新工程或释放新分页 owner', async () => {
    const user = userEvent.setup()
    const oldPage = new Deferred<RunSummaryPageEnvelope>()
    const newPage = new Deferred<RunSummaryPageEnvelope>()
    const other = studioEnvelope({
      project_path: 'C:\\synthetic\\other.zniku',
      run_summaries: [],
      next_run_cursor: 'cursor.new',
      active_run_id: null,
    })
    let gateway: RecordingGateway
    gateway = new RecordingGateway(
      studioEnvelope({ next_run_cursor: 'cursor.old' }),
      {
        listRuns: (cursor) => (cursor === 'cursor.old' ? oldPage.promise : newPage.promise),
        command: (command) => {
          if (command.operation === 'open_project') gateway.envelope = other
          return gateway.envelope
        },
      },
    )
    render(<App gateway={gateway} />)

    await user.click(await screen.findByRole('button', { name: '加载更早 Run' }))
    fireEvent.change(screen.getByLabelText('工程路径'), {
      target: { value: 'C:\\synthetic\\other.zniku' },
    })
    await user.click(screen.getByRole('button', { name: '打开' }))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('open_project'))

    const newPageButton = await screen.findByRole('button', { name: '加载更早 Run' })
    await user.click(newPageButton)
    await waitFor(() => expect(gateway.historyArguments.at(-1)?.[0]).toBe('cursor.new'))
    await act(async () => oldPage.reject(new Error('old history failed')))

    expect(screen.queryByText('old history failed')).not.toBeInTheDocument()
    expect(newPageButton).toBeDisabled()

    await act(async () => newPage.resolve({
      contract_version: '0.3.0',
      run_summaries: [],
      next_run_cursor: null,
    }))
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '加载更早 Run' })).not.toBeInTheDocument()
    })
  })

  it('Node A 慢日志不能覆盖或丢失随后选中的 Node B 日志', async () => {
    const slowA = new Deferred<NodeLogEnvelope>()
    let sourceCalls = 0
    const makeLog = (nodeRunId: string, stdout: string): NodeLogEnvelope => ({
      contract_version: '0.3.0',
      run_id: handoffFixtureIds.run,
      log: {
        ...handoffLogEnvelope().log,
        node_run_id: nodeRunId,
        stdout,
      },
    })
    const gateway = new RecordingGateway(handoffEnvelope(), {
      log: (_runId, nodeRunId) => {
        if (nodeRunId === handoffFixtureIds.sourceNodeRun && sourceCalls++ === 0) {
          return slowA.promise
        }
        return makeLog(
          nodeRunId,
          nodeRunId === handoffFixtureIds.sourceNodeRun ? 'A newest log' : 'B trusted log',
        )
      },
    })
    render(<App gateway={gateway} />)
    await screen.findByLabelText('source 节点')

    fireEvent.click(screen.getByLabelText('source 节点'))
    await waitFor(() => expect(gateway.logArguments.at(-1)?.[1]).toBe(handoffFixtureIds.sourceNodeRun))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    expect(await screen.findByText('B trusted log')).toBeInTheDocument()

    await act(async () => {
      slowA.resolve(makeLog(handoffFixtureIds.sourceNodeRun, 'A late log'))
      await Promise.resolve()
    })
    expect(screen.getByText('B trusted log')).toBeInTheDocument()
    expect(screen.queryByText('A late log')).not.toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(await screen.findByText('A newest log')).toBeInTheDocument()
  })

  it('同一 log 资源的旧 failure 不能把更新成功标为 stale', async () => {
    const slowFailure = new Deferred<NodeLogEnvelope>()
    let sourceCalls = 0
    const makeLog = (nodeRunId: string, stdout: string): NodeLogEnvelope => ({
      contract_version: '0.3.0',
      run_id: handoffFixtureIds.run,
      log: { ...handoffLogEnvelope().log, node_run_id: nodeRunId, stdout },
    })
    const gateway = new RecordingGateway(handoffEnvelope(), {
      log: (_runId, nodeRunId) => {
        if (nodeRunId === handoffFixtureIds.sourceNodeRun && sourceCalls++ === 0) {
          return slowFailure.promise
        }
        return makeLog(nodeRunId, nodeRunId === handoffFixtureIds.sourceNodeRun ? 'A recovered' : 'B log')
      },
    })
    render(<App gateway={gateway} />)
    await screen.findByLabelText('source 节点')

    fireEvent.click(screen.getByLabelText('source 节点'))
    await waitFor(() => expect(gateway.logArguments.at(-1)?.[1]).toBe(handoffFixtureIds.sourceNodeRun))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    expect(await screen.findByText('B log')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(await screen.findByText('A recovered')).toBeInTheDocument()

    await act(async () => {
      slowFailure.reject(new Error('A obsolete failure'))
      await Promise.resolve()
    })
    expect(screen.getByText('A recovered')).toBeInTheDocument()
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('LOG OK')
    expect(screen.queryByText('A obsolete failure')).not.toBeInTheDocument()
  })

  it('旧 generation 的 detail failure 不得把新成功标为 stale', async () => {
    vi.useFakeTimers()
    const slowFailure = new Deferred<RunDetailEnvelope>()
    let progress = 0.1
    let gateway: RecordingGateway
    gateway = new RecordingGateway(runningProgressEnvelope(progress, null), {
      detail: (_runId, count) => {
        if (count === 1) return runningProgressDetail(0.1)
        if (count === 2) return slowFailure.promise
        return runningProgressDetail(progress)
      },
      command: () => {
        progress = 0.8
        const next = {
          ...runningProgressEnvelope(progress, null),
          project_path: 'C:\\synthetic\\detail-new.zniku',
        }
        gateway.envelope = next
        return next
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(751)
    })
    expect(gateway.inspectRunCount).toBe(2)

    fireEvent.change(screen.getByLabelText('工程路径'), {
      target: { value: 'C:\\synthetic\\detail-new.zniku' },
    })
    fireEvent.click(screen.getByRole('button', { name: '打开' }))
    await flushReact()
    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(within(screen.getByLabelText('步骤处理状态')).getByText('80%')).toBeInTheDocument()

    await act(async () => {
      slowFailure.reject(new Error('old detail offline'))
      await Promise.resolve()
    })
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('DETAIL OK')
    expect(screen.queryByText('old detail offline')).not.toBeInTheDocument()
  })

  it('长时间显式 probe 独占同 handoff、去重点击并最终精确 Submit', async () => {
    vi.useFakeTimers()
    const slowProbe = new Deferred<ExternalHandoffReadiness>()
    const gateway = new RecordingGateway(handoffEnvelope(), {
      readiness: (_runId, _nodeRunId, probe) =>
        probe ? slowProbe.promise : handoffReadinessEnvelope('present', false),
    })
    render(<App gateway={gateway} />)
    await flushReact()
    const submit = within(screen.getByLabelText('外部处理：test.transform')).getByRole('button', {
      name: '检查输出',
    })
    fireEvent.click(submit)
    fireEvent.click(submit)
    await flushReact()
    expect(gateway.readinessArguments.filter((item) => item[2])).toHaveLength(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_501)
    })
    expect(gateway.readinessArguments.filter((item) => !item[2])).toHaveLength(1)

    await act(async () => {
      slowProbe.resolve(handoffReadinessEnvelope('probe_passed', true))
      await Promise.resolve()
    })
    await flushReact()
    expect(gateway.commands.filter((command) => command.operation === 'submit_external')).toHaveLength(0)
    fireEvent.click(within(screen.getByLabelText('外部处理：test.transform')).getByRole('button', { name: '提交并继续' }))
    await flushReact()
    expect(gateway.commands.filter(
      (command) => command.operation === 'submit_external',
    )).toEqual([{
      operation: 'submit_external',
      run_id: handoffFixtureIds.run,
      node_run_id: handoffFixtureIds.transformNodeRun,
      handoff_id: handoffFixtureIds.handoff,
    }])
  })

  it('generation 切换后迟到的显式 probe 不得 Submit 或回写旧 readiness', async () => {
    vi.useFakeTimers()
    const slowProbe = new Deferred<ExternalHandoffReadiness>()
    let gateway: RecordingGateway
    gateway = new RecordingGateway(handoffEnvelope(), {
      readiness: (_runId, _nodeRunId, probe) =>
        probe ? slowProbe.promise : handoffReadinessEnvelope('present', false),
      command: (command) => {
        if (command.operation === 'open_project') {
          gateway.envelope = studioEnvelope({
            project_path: 'C:\\synthetic\\probe-new.zniku',
            run_summaries: [],
            active_run_id: null,
          })
        }
        return gateway.envelope
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    fireEvent.click(
      within(screen.getByLabelText('外部处理：test.transform')).getByRole('button', {
        name: '检查输出',
      }),
    )
    await flushReact()

    fireEvent.change(screen.getByLabelText('工程路径'), {
      target: { value: 'C:\\synthetic\\probe-new.zniku' },
    })
    fireEvent.click(screen.getByRole('button', { name: '打开' }))
    await flushReact()

    await act(async () => {
      slowProbe.resolve(handoffReadinessEnvelope('probe_passed', true))
      await Promise.resolve()
    })
    expect(gateway.commands.filter((command) => command.operation === 'submit_external')).toHaveLength(0)
    expect(screen.queryByText('Ready to submit')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('C:\\synthetic\\probe-new.zniku')).toBeInTheDocument()
  })

  it('多 handoff readiness 按当前可操作资源集合聚合 stale', async () => {
    const baseDetail = structuredClone(handoffDetailEnvelope())
    const firstWaiting = baseDetail.run.node_runs.find((item) => item.node_id === 'transform')!
    const secondNodeRunId = '00000000-0000-4000-8000-000000000082'
    const secondHandoffId = '00000000-0000-4000-8000-000000000083'
    const graphSnapshot = {
      ...baseDetail.run.graph_snapshot,
      nodes: [
        ...baseDetail.run.graph_snapshot.nodes,
        {
          ...baseDetail.run.graph_snapshot.nodes.find((item) => item.node_id === 'transform')!,
          node_id: 'transform.b',
        },
      ],
    }
    const nodeRuns = [
      ...baseDetail.run.node_runs,
      {
        ...firstWaiting,
        node_run_id: secondNodeRunId,
        node_id: 'transform.b',
        external_handoff: {
          ...firstWaiting.external_handoff!,
          handoff_id: secondHandoffId,
          node_run_id: secondNodeRunId,
          output_targets: [
            {
              ...firstWaiting.external_handoff!.output_targets[0]!,
              path: 'C:\\synthetic\\attempt-transform-b\\output.mkv',
            },
          ],
        },
      },
    ]
    const detail: RunDetailEnvelope = {
      ...baseDetail,
      run: { ...baseDetail.run, graph_snapshot: graphSnapshot, node_runs: nodeRuns },
    }
    const baseStatus = handoffEnvelope()
    const status: StatusEnvelope = {
      ...baseStatus,
      run_summaries: [
      {
        ...baseStatus.run_summaries[0]!,
        node_count: 4,
        state_counts: {
          pending: 1,
          running: 0,
          waiting_external: 2,
          completed: 1,
          failed: 0,
        },
      },
      ],
    }
    const gateway = new RecordingGateway(status, {
      detail: () => detail,
      readiness: (runId, nodeRunId) => {
        if (nodeRunId === handoffFixtureIds.transformNodeRun) throw new Error('handoff A offline')
        return {
          ...handoffReadinessEnvelope('present', false),
          run_id: runId,
          node_run_id: nodeRunId,
          handoff_id: secondHandoffId,
        }
      },
    })
    render(<App gateway={gateway} />)

    expect(await screen.findAllByLabelText('外部处理：test.transform')).toHaveLength(2)
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('READINESS STALE')
    const submitButtons = screen.getAllByRole('button', { name: '检查输出' })
    expect(submitButtons).toHaveLength(2)
    for (const button of submitButtons) expect(button).toBeDisabled()
  })

  it('Presentation 文本按纯文本渲染，并用 exact title 与声明路径生成节点摘要', async () => {
    const hostileTitle = '<img src=x onerror=alert(1)>'
    const gateway = new RecordingGateway(studioEnvelope(), {
      presentations: () => presentationEnvelope(
        [nodePresentation(transformDefinition, hostileTitle, {
          parameters: [{
            parameter_pointer: '/strength', label: '强度', description: null,
            group_id: 'generic', order: 1, importance: 'primary', control_hint: 'integer',
            unit: null, placeholder: null, enum_labels: [], picker: null,
          }],
          cardSummaryPaths: ['/strength'],
        })],
        [{ category_id: 'test', title: '测试节点', description: null, order: 1 }],
      ),
    })
    render(<App gateway={gateway} />)

    expect((await screen.findAllByText(hostileTitle)).length).toBeGreaterThan(0)
    expect(screen.getByText('强度：3')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    fireEvent.click(screen.getByLabelText('transform 节点'))
    expect(screen.getByRole('heading', { name: hostileTitle })).toBeVisible()
  })

  it('definitions exact identity 变化后刷新 Presentation，接纳动态节点而不保留旧 generic 投影', async () => {
    const initialSnapshot = {
      project: {
        ...projectSnapshot.project,
        graph: { nodes: [projectSnapshot.project.graph.nodes[0]!], edges: [] },
      },
      definitions: [sourceDefinition],
    }
    const expandedSnapshot = { ...initialSnapshot, definitions: [sourceDefinition, transformDefinition] }
    let expanded = false
    let gateway: RecordingGateway
    gateway = new RecordingGateway(studioEnvelope({ snapshot: initialSnapshot }), {
      presentations: () => presentationEnvelope(
        expanded
          ? [nodePresentation(sourceDefinition, '媒体输入', { iconToken: 'source' }), nodePresentation(transformDefinition, '动态增强')]
          : [nodePresentation(sourceDefinition, '媒体输入', { iconToken: 'source' })],
        [{ category_id: 'test', title: '创作者节点', description: null, order: 1 }],
      ),
      command: (command) => {
        if (command.operation === 'open_project') {
          expanded = true
          gateway.envelope = studioEnvelope({ snapshot: expandedSnapshot })
        }
        return gateway.envelope
      },
    })
    render(<App gateway={gateway} />)

    expect(await screen.findByRole('button', { name: /媒体输入 · test\.source@0\.2\.0/ })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: '打开' }))
    expect(await screen.findByRole('button', { name: /动态增强 · test\.transform@0\.2\.0/ })).toBeVisible()
    expect(gateway.inspectPresentationCount).toBeGreaterThanOrEqual(3)
  })

  it('未应用 ParameterDraft 阻断运行与离开节点，form/raw 双向同步后可显式放弃', async () => {
    const gateway = new RecordingGateway()
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')
    fireEvent.click(screen.getByLabelText('transform 节点'))

    const strength = screen.getByRole('spinbutton', { name: 'strength' })
    fireEvent.change(strength, { target: { value: '4' } })
    expect((screen.getByLabelText('节点参数 JSON') as HTMLTextAreaElement).value).toContain('"strength": 4')
    fireEvent.change(screen.getByLabelText('节点参数 JSON'), {
      target: { value: '{"strength":5,"model_name":"Synthetic Model"}' },
    })
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(5)
    expect(screen.getByRole('button', { name: 'Run all' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Run to here' })).toBeDisabled()

    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(screen.getByRole('heading', { name: 'test.transform' })).toBeVisible()
    expect(screen.getByText(/当前节点有未应用设置/)).toBeVisible()

    const pane = document.querySelector('.react-flow__pane')
    expect(pane).not.toBeNull()
    fireEvent.click(pane!)
    expect(screen.getByRole('heading', { name: 'test.transform' })).toBeVisible()
    expect(gateway.commands.some((command) => command.operation === 'run_all')).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: '放弃未应用更改' }))
    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(screen.getByRole('heading', { name: 'test.source' })).toBeVisible()
  })

  it('已保存安全草稿明确不可运行，Python nodes[index] 诊断可以定位', async () => {
    const gateway = new RecordingGateway(studioEnvelope({
      snapshot: { ...projectSnapshot, project: { ...projectSnapshot.project, graph: { ...projectSnapshot.project.graph, edges: [] } } },
      authoring_diagnostics: [{ code: 'E_REQUIRED_INPUT_MISSING', path: 'nodes[1].inputs.in', message: '缺少输入视频', validator_keyword: null }],
    }))
    render(<App gateway={gateway} />)
    expect(await screen.findByText(/已保存，但暂不可运行/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Run all' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '定位步骤与设置' }))
    expect(screen.getByRole('heading', { name: 'test.transform' })).toBeVisible()
    expect(gateway.commands).toHaveLength(0)
  })

  it('保存等待期间出现未应用参数时取消 Run，不把输入丢进运行快照', async () => {
    const flight = new Deferred<StatusEnvelope>()
    const gateway = new RecordingGateway(studioEnvelope(), {
      command: (command) => command.operation === 'save_project' ? flight.promise : gateway.envelope,
    })
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')
    fireEvent.click(screen.getByLabelText('transform 节点'))
    const alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '等待保存' } })
    fireEvent.blur(alias)
    fireEvent.click(screen.getByRole('button', { name: 'Run all' }))
    await waitFor(() => expect(gateway.commands).toHaveLength(1))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'strength' }), { target: { value: '8' } })
    await act(async () => { flight.resolve(studioEnvelope()) })
    expect(await screen.findByText(/保存等待期间节点设置已变化/)).toBeVisible()
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(8)
    expect(gateway.commands.map((command) => command.operation)).toEqual(['save_project'])
  })

  it('节点别名、分组与参数应用可撤销重做；新编辑清空 redo，模式不产生保存', async () => {
    const gateway = new RecordingGateway()
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')
    fireEvent.click(screen.getByRole('button', { name: '关闭工程首页' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    const alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '第一章增强' } })
    fireEvent.blur(alias)
    expect(screen.getByRole('heading', { name: '第一章增强' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(screen.getByRole('heading', { name: 'test.transform' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '重做' }))
    expect(screen.getByRole('heading', { name: '第一章增强' })).toBeVisible()
    fireEvent.change(screen.getByLabelText('新分组名称'), { target: { value: '第一章 / Leaf A' } })
    fireEvent.click(screen.getByRole('button', { name: '将所选节点分组' }))
    expect(screen.getByRole('combobox', { name: '所属分组' })).not.toHaveValue('')
    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(screen.getByRole('combobox', { name: '所属分组' })).toHaveValue('')
    fireEvent.click(screen.getByLabelText('折叠节点摘要'))
    expect(screen.getByRole('button', { name: '重做' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('节点参数 JSON'), { target: { value: '{"strength":8,"model_name":"Synthetic Model"}' } })
    fireEvent.click(screen.getByRole('button', { name: '应用设置' }))
    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(3)
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(gateway.envelope.studio_state?.node_views[0]?.display_name).toBe('第一章增强'))
    expect(gateway.envelope.snapshot?.project.graph).toEqual(projectSnapshot.project.graph)
    const commandCount = gateway.commands.length
    fireEvent.click(screen.getByRole('button', { name: '返回创作者模式' }))
    fireEvent.click(screen.getByRole('button', { name: '高级节点图' }))
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 550)) })
    expect(gateway.commands).toHaveLength(commandCount)
  })

  it('自动保存失败保留图和离开保护；冲突不能运行，明确重新载入后才丢弃本地更改', async () => {
    const gateway = new RecordingGateway(studioEnvelope(), {
      command: (command) => {
        if (command.operation === 'save_project') throw new StudioGatewayError('另一窗口已保存更新，请重新载入。', { code: 'E_PROJECT_REVISION_CONFLICT' })
        return studioEnvelope()
      },
    })
    const confirmation = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')
    fireEvent.click(screen.getByLabelText('transform 节点'))
    const alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '未保存的增强' } })
    fireEvent.blur(alias)
    expect((await screen.findAllByText(/另一窗口已保存更新/)).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: '未保存的增强' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Run all' })).toBeDisabled()
    const leaving = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(leaving)
    expect(leaving.defaultPrevented).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '重新载入磁盘版本' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: '重新载入磁盘版本' })).not.toBeInTheDocument())
    expect(confirmation).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled()
    expect(gateway.commands.filter((command) => command.operation === 'save_project')).toHaveLength(1)
    const cleanLeaving = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(cleanLeaving)
    expect(cleanLeaving.defaultPrevented).toBe(false)
    confirmation.mockRestore()
  })

  it('Run all 等待在途自动保存和期间新编辑全部落盘，使用最后一次 CAS revision', async () => {
    const flight = new Deferred<StatusEnvelope>()
    let first = true
    const gateway = new RecordingGateway(studioEnvelope(), {
      command: (command) => {
        if (command.operation === 'save_project' && first) { first = false; return flight.promise }
        return gateway.envelope
      },
    })
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')
    fireEvent.click(screen.getByLabelText('transform 节点'))
    let alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '第一次编辑' } })
    fireEvent.blur(alias)
    await waitFor(() => expect(gateway.commands).toHaveLength(1))
    alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '最后一次编辑' } })
    fireEvent.blur(alias)
    fireEvent.click(screen.getByRole('button', { name: 'Run all' }))
    expect(gateway.commands).toHaveLength(1)
    await act(async () => { flight.resolve(studioEnvelope()) })
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('run_all'))
    const saves = gateway.commands.filter((command) => command.operation === 'save_project')
    expect(saves).toHaveLength(2)
    expect(saves[1]?.studio_state.node_views[0]?.display_name).toBe('最后一次编辑')
    expect(gateway.commands.at(-1)).toMatchObject({ operation: 'run_all', expected_storage_revision: 2, project_session_id: studioEnvelope().project_session_id })
  })

  it('运行中编辑当前图可自动保存，已选 Run snapshot 和旧参数保持不变', async () => {
    const gateway = new RecordingGateway(runningProgressEnvelope(0.3))
    render(<App gateway={gateway} />)
    await screen.findByRole('button', { name: '查看当前 Graph' })
    fireEvent.click(screen.getByRole('button', { name: '查看当前 Graph' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    fireEvent.change(screen.getByLabelText('节点参数 JSON'), { target: { value: '{"strength":9,"model_name":"Synthetic Model"}' } })
    fireEvent.click(screen.getByRole('button', { name: '应用设置' }))
    await waitFor(() => expect(gateway.envelope.snapshot?.project.graph.nodes[1]?.parameters.strength).toBe(9))
    fireEvent.click(screen.getByRole('button', { name: '查看 Run snapshot' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(3)
    expect(screen.getByRole('button', { name: '应用设置' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled()
  })

  it('same-project reopen 与 save 保留历史 terminal 选择，真实换 path 才清理', async () => {
    const user = userEvent.setup()
    const fixture = threeRunEnvelope()
    const latest = fixture.run_summaries[0]!
    const terminal = fixture.run_summaries[2]!
    const initial = {
      ...fixture,
      run_summaries: [latest],
      next_run_cursor: 'cursor.terminal',
    }
    let gateway: RecordingGateway
    gateway = new RecordingGateway(initial, {
      listRuns: () => ({
        contract_version: '0.3.0',
        run_summaries: [terminal],
        next_run_cursor: null,
      }),
      command: (command) => {
        const next =
          command.operation === 'open_project' && command.path === 'C:\\synthetic\\other.zniku'
            ? studioEnvelope({
                project_path: 'C:\\synthetic\\other.zniku',
                run_summaries: [],
                active_run_id: null,
              })
            : initial
        gateway.envelope = next
        return next
      },
    })
    render(<App gateway={gateway} />)

    await user.click(await screen.findByRole('button', { name: '加载更早 Run' }))
    const selector = screen.getByRole('combobox', { name: '查看 Run' }) as HTMLSelectElement
    fireEvent.change(selector, { target: { value: terminal.run_id } })
    await waitFor(() => expect(selector).toHaveValue(terminal.run_id))

    await user.click(screen.getByRole('button', { name: '打开' }))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('open_project'))
    expect(selector).toHaveValue(terminal.run_id)
    expect([...selector.options].map((option) => option.value)).toContain(terminal.run_id)

    await user.click(screen.getByRole('button', { name: '查看当前 Graph' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    fireEvent.change(screen.getByLabelText('节点参数 JSON'), {
      target: { value: '{"strength":4,"model_name":"Synthetic Model"}' },
    })
    await user.click(screen.getByRole('button', { name: '应用设置' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('save_project'))
    expect(selector).toHaveValue(terminal.run_id)
    expect([...selector.options].map((option) => option.value)).toContain(terminal.run_id)

    await user.clear(screen.getByLabelText('工程路径'))
    await user.type(screen.getByLabelText('工程路径'), 'C:\\synthetic\\other.zniku')
    await user.click(screen.getByRole('button', { name: '打开' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'open_project',
      path: 'C:\\synthetic\\other.zniku',
    }))
    expect(selector).toHaveValue('')
    expect([...selector.options].map((option) => option.value)).not.toContain(terminal.run_id)
  })
})
