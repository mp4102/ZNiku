import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type {
  AvEnhanceV27TemplatePreviewEnvelope,
  AvEnhanceV27TemplatePreviewRequestWire,
  AvEnhanceV27PublicationPreviewRequestWire,
  AvEnhanceV27PublicationPreviewEnvelope,
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
  NodeRunWire,
} from './studio/contracts'
import { StudioGatewayError, type StudioGateway } from './studio/gateway'
import { inspectGraph } from './studio/graph'
import type { HostBridge, HostCapabilitiesEnvelope, HostSelection, HandoffImportPreviewEnvelope, StorageInspection, HandoffInboxPreviewEnvelope } from './studio/host-bridge'
import { HostBridgeError } from './studio/host-bridge'
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

/** 测试与操作者使用同一显式入口，不对隐藏控件触发事件。 */
function openDetails(label: string): void {
  const summary = screen.getByText(label, { selector: 'summary' })
  if (!summary.closest('details')?.open) fireEvent.click(summary)
}

function projectButton(name: string): HTMLElement {
  const trigger = screen.getByRole('button', { name: '工程' })
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.click(trigger)
  if (name === '打开' || name === '新建') openDetails('开发入口')
  return within(screen.getByRole('group', { name: '工程菜单' })).getByRole('button', { name })
}

function projectPath(): HTMLElement {
  projectButton('打开')
  return screen.getByLabelText('工程路径')
}

function viewButton(name: string): HTMLElement {
  const trigger = screen.getByRole('button', { name: '视图' })
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.click(trigger)
  return within(screen.getByRole('group', { name: '视图菜单' })).getByRole('button', { name })
}

function openTaskTab(name: '当前处理' | '历史记录' | '问题'): HTMLElement {
  const expand = screen.queryByRole('button', { name: '展开任务区' })
  if (expand) fireEvent.click(expand)
  const drawer = screen.getByRole('region', { name: '任务抽屉' })
  fireEvent.click(within(drawer).getByRole('tab', { name: new RegExp(`^${name}`) }))
  return drawer
}

function runAllButton(): HTMLElement {
  if (!screen.queryByText('当前编辑的完整处理命令', { selector: 'summary' })) {
    fireEvent.click(viewButton('高级节点图'))
  }
  openTaskTab('当前处理')
  openDetails('当前编辑的完整处理命令')
  return screen.getByRole('button', { name: '开始新的完整处理' })
}

function selectHistory(runId: string): void {
  const drawer = openTaskTab('历史记录')
  const button = within(drawer).getAllByRole('button').find((item) => (item as HTMLButtonElement).value === runId)
  if (!button) throw new Error(`合成历史记录未加载：${runId}`)
  fireEvent.click(button)
}

function openInspectorTab(name: '设置' | '文件' | '诊断'): void {
  fireEvent.click(screen.getByRole('tab', { name }))
}

function rawParameters(): HTMLElement {
  openInspectorTab('诊断')
  openDetails('高级 → 原始参数')
  return screen.getByLabelText('节点参数 JSON')
}

function displaySettings(): void {
  openInspectorTab('设置')
  openDetails('显示设置')
}

function openLibrary(): void {
  const button = screen.getByRole('button', { name: '添加节点' })
  if (button.getAttribute('aria-expanded') !== 'true') fireEvent.click(button)
}

function resourceHealth(): HTMLElement {
  openInspectorTab('诊断')
  openDetails('服务高级诊断')
  return screen.getByLabelText('Resource channel health')
}

/** 全局等待队列只是导航；需要操作文件的测试先显式选择对应交接任务。 */
async function selectHandoffTask(title = 'test.transform'): Promise<HTMLElement> {
  openInspectorTab('文件')
  const label = `外部处理：${title}`
  const selected = screen.queryByLabelText(label)
  if (selected) return selected
  const navigation = screen.queryByRole('button', { name: `查看外部任务：${title}` }) ??
    await screen.findByRole('button', { name: `查看外部任务：${title}` })
  fireEvent.click(navigation)
  return screen.queryByLabelText(label) ?? screen.findByLabelText(label)
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

  async previewAvEnhanceV27Publication(request: AvEnhanceV27PublicationPreviewRequestWire): Promise<AvEnhanceV27PublicationPreviewEnvelope> {
    return { contract_version: '0.3.0', layout: request.request.layout ?? 'direct', resolved_output_root: 'D:\\Library', output_directory: 'D:\\Library', will_create_directory: false }
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

function storageInspectionFixture(): StorageInspection {
  return { contract_version: '0.3.0', configured: true, attempt_count: 0,
    storage: { contract_version: '0.3.0', mode: 'adjacent', data_root: 'D:\\synthetic.data', attempts_root: 'D:\\synthetic.data\\attempts', retention: 'keep', media_basename: null },
    registered_file_count: 0, registered_bytes: 0, managed_file_count: 0, managed_bytes: 0,
    missing: [], external_dependencies: [], coverage: 'registered_artifacts', warnings: ['纯合成登记资产检查。'] }
}

describe('ZNIKU Studio 0.3.0 Project workspace', () => {
  // 原有精确命令、身份和日志回归在高级层验证；Phase 4 创作者路径在后文显式使用默认模式。
  beforeEach(() => { window.localStorage.setItem('zniku.studio.density', 'advanced') })

  it('工程数据先等未完成保存落盘，再用相同session和最新revision打开维护面板', async () => {
    const saving = new Deferred<StatusEnvelope>()
    const gateway = new RecordingGateway(studioEnvelope(), {
      command: (command) => command.operation === 'save_project' ? saving.promise : gateway.envelope,
    })
    const inspectStorage = vi.fn(async (_session: string) => storageInspectionFixture())
    const configureStorage = vi.fn<NonNullable<HostBridge['configureStorage']>>(async () => storageInspectionFixture())
    const host: HostBridge = { configured: true, inspectCapabilities: async () => hostCapabilitiesEnvelope(),
      pick: vi.fn(async () => null), launch: vi.fn(), inspectStorage, configureStorage }
    render(<App gateway={gateway} hostBridge={host} />)
    fireEvent.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    const alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '换盘前最后编辑' } })
    fireEvent.blur(alias)
    await waitFor(() => expect(gateway.commands.filter((item) => item.operation === 'save_project')).toHaveLength(1))
    fireEvent.click(projectButton('工程数据'))
    expect(inspectStorage).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog', { name: '工程数据与归档检查' })).not.toBeInTheDocument()
    await act(async () => saving.resolve(studioEnvelope()))
    const dialog = await screen.findByRole('dialog', { name: '工程数据与归档检查' })
    await within(dialog).findByText('纯合成登记资产检查。')
    expect(inspectStorage).toHaveBeenCalledWith(gateway.envelope.project_session_id)
    expect(gateway.envelope.studio_state?.node_views[0]?.display_name).toBe('换盘前最后编辑')
    expect(gateway.envelope.snapshot?.project.graph).toEqual(projectSnapshot.project.graph)
    fireEvent.click(within(dialog).getByRole('button', { name: '恢复工程旁默认' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '保存数据位置' }))
    await waitFor(() => expect(configureStorage).toHaveBeenCalledWith({ project_session_id: gateway.envelope.project_session_id, expected_storage_revision: 1, selection_handle: null }))
    expect(gateway.commands.every((item) => item.operation === 'save_project')).toBe(true)
  })

  it('工程数据不会丢弃尚未应用的参数编辑，也不会为此打开存储维护', async () => {
    const gateway = new RecordingGateway()
    const inspectStorage = vi.fn(async (_session: string) => storageInspectionFixture())
    const host: HostBridge = { configured: true, inspectCapabilities: async () => hostCapabilitiesEnvelope(), pick: vi.fn(async () => null), launch: vi.fn(), inspectStorage }
    render(<App gateway={gateway} hostBridge={host} />)
    fireEvent.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'strength' }), { target: { value: '9' } })
    fireEvent.click(projectButton('工程数据'))
    await flushReact()
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(9)
    expect(screen.getByRole('button', { name: '应用设置' })).toBeEnabled()
    expect(inspectStorage).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog', { name: '工程数据与归档检查' })).not.toBeInTheDocument()
    expect(gateway.commands).toHaveLength(0)
    expect(gateway.envelope.snapshot?.project.graph).toEqual(projectSnapshot.project.graph)
  })

  it('收件箱自身busy不会锁死收纳确认，收纳完成清除检查资格但不自动Submit', async () => {
    window.localStorage.removeItem('zniku.studio.density')
    const gateway = new RecordingGateway(handoffEnvelope())
    let preview!: HandoffInboxPreviewEnvelope
    const observeHandoffInbox = vi.fn<NonNullable<HostBridge['observeHandoffInbox']>>(async (binding) => ({ ...binding,
      inbox_path: 'C:\\synthetic\\incoming\\out', allowed_suffix: '.mkv', rejected_count: 0, expires_in_seconds: 300,
      candidates: [{ candidate_handle: 'candidate-synthetic', name: 'external-result.mkv', size: 4096, mtime_ns: 1 }] }))
    const previewHandoffInbox = vi.fn<NonNullable<HostBridge['previewHandoffInbox']>>(async (request) => {
      preview = { ...request, inbox_id: 'inbox-synthetic', source_name: 'external-result.mkv', source_size: 4096,
        target_path: handoffDetailEnvelope().run.node_runs.find((item) => item.external_handoff)!.external_handoff!.output_targets[0]!.path,
        replace_existing: false, action: 'move', expires_in_seconds: 300 }
      return preview
    })
    const confirmHandoffInbox = vi.fn<NonNullable<HostBridge['confirmHandoffInbox']>>(async () => ({ ...preview, status: 'collected' }))
    const host: HostBridge = { configured: true, inspectCapabilities: async () => hostCapabilitiesEnvelope(), pick: vi.fn(async () => null), launch: vi.fn(), observeHandoffInbox, previewHandoffInbox, confirmHandoffInbox }
    render(<App gateway={gateway} hostBridge={host} />)
    fireEvent.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    const task = await selectHandoffTask()
    fireEvent.click(within(task).getByRole('button', { name: '检查输出' }))
    await waitFor(() => expect(within(task).getByRole('button', { name: '提交并继续' })).toBeEnabled())
    fireEvent.click(await within(task).findByRole('button', { name: '检查并收纳：external-result.mkv' }))
    const dialog = await screen.findByRole('dialog', { name: '确认收纳外部处理文件' })
    const confirm = within(dialog).getByRole('button', { name: '确认检查并收纳' })
    expect(confirm).toBeEnabled()
    expect(confirmHandoffInbox).not.toHaveBeenCalled()
    const readinessCount = gateway.readinessArguments.length
    fireEvent.click(confirm)
    await within(task).findByText('文件已按规范名称收纳。请检查输出，再由你“提交并继续”；系统没有自动提交。')
    expect(confirmHandoffInbox).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.0', inbox_id: 'inbox-synthetic', overwrite: false })
    await waitFor(() => expect(gateway.readinessArguments.length).toBeGreaterThan(readinessCount))
    expect(gateway.readinessArguments.at(-1)).toEqual([handoffFixtureIds.run, handoffFixtureIds.transformNodeRun, false])
    expect(within(task).getByRole('button', { name: '提交并继续' })).toBeDisabled()
    expect(gateway.commands).toHaveLength(0)
  })
  it('Project Service 缺失时失败关闭，不回退旧正式投影或浏览器 mock', async () => {
    render(<App gateway={unavailableGateway('loopback offline')} />)

    expect(await screen.findByText('本机服务暂时不可用')).toBeVisible()
    expect(screen.getByText('本机工程服务暂时不可用')).toBeVisible()
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
    expect(projectButton('处理向导')).toBeDisabled()
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
    expect(projectButton('保存')).toBeEnabled()
    await user.click(projectButton('工程首页'))
    const open = await screen.findByRole('button', { name: /打开已有工程/ })
    await user.click(open)
    expect(pick).toHaveBeenCalledTimes(1)
    expect(open).toBeDisabled()
    expect(screen.getByRole('button', { name: '关闭工程首页' })).toBeDisabled()
    expect(projectButton('保存')).toBeDisabled()

    unmount()
    await act(async () => {
      picker.resolve([{ selection_handle: 'selection_1234567890_1234567890', path: 'D:\\private\\late.zniku' }])
      await Promise.resolve()
    })
    expect(gateway.commands).not.toContainEqual(expect.objectContaining({ operation: 'open_project' }))
  })

  it.each(['open', 'save'] as const)('首页 %s 原生窗口忙碌指向已打开窗口，原文折叠保留且不修改工程或自动重试', async (kind) => {
    const user = userEvent.setup()
    const rawMessage = 'E_HOST_BRIDGE_DIALOG_BUSY: synthetic native chooser is active'
    const pick = vi.fn<HostBridge['pick']>().mockRejectedValueOnce(new HostBridgeError(rawMessage, { code: 'E_HOST_BRIDGE_DIALOG_BUSY', httpStatus: 409 }))
    const hostBridge: HostBridge = { configured: true, inspectCapabilities: async () => hostCapabilitiesEnvelope(), pick, launch: vi.fn() }
    const gateway = new RecordingGateway()
    render(<App gateway={gateway} hostBridge={hostBridge} />)
    if (kind === 'open') {
      const button = await screen.findByRole('button', { name: /打开已有工程/ })
      await waitFor(() => expect(button).toBeEnabled())
      await user.click(button)
    } else {
      const button = await screen.findByRole('button', { name: /空白工作流/ })
      await waitFor(() => expect(button).toBeEnabled())
      await user.click(button)
      await user.click(screen.getByRole('button', { name: '选择保存位置' }))
    }
    expect(await screen.findByRole('alert')).toHaveTextContent('已有文件/文件夹选择窗口打开，请先完成或取消；它可能在浏览器后面。')
    expect(screen.getByText(rawMessage)).not.toBeVisible()
    await user.click(screen.getByText('高级 → 选择窗口原始详情'))
    expect(screen.getByText(rawMessage)).toBeVisible()
    expect(pick).toHaveBeenCalledTimes(1)
    expect(gateway.commands).toEqual([])
    expect(screen.getByRole('button', { name: '关闭工程首页' })).toBeEnabled()
  })

  it('向导打开输出根只使用当前原生选择句柄，取消保留选择，手输或重开不能借用旧句柄', async () => {
    const user = userEvent.setup()
    const selection = { path: 'D:\\Synthetic\\Output', selection_handle: 'selection_synthetic_output_001' }
    const pick = vi.fn<HostBridge['pick']>().mockResolvedValueOnce([selection]).mockResolvedValueOnce(null)
    const launch = vi.fn<HostBridge['launch']>(async () => undefined)
    const hostBridge: HostBridge = {
      configured: true, inspectCapabilities: async () => hostCapabilitiesEnvelope(), pick, launch,
    }
    const gateway = new RecordingGateway()
    render(<App gateway={gateway} hostBridge={hostBridge} />)
    const enterSettings = async () => {
      await user.click(await screen.findByRole('button', { name: /新建视频工程/ }))
      fireEvent.change(screen.getByLabelText('模板工程路径'), { target: { value: 'D:\\Synthetic\\guided.zniku' } })
      fireEvent.change(screen.getByLabelText('Source 1 path'), { target: { value: 'D:\\Synthetic\\source.mkv' } })
      await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
      await user.click(screen.getByRole('button', { name: '下一步：设置' }))
    }
    await enterSettings()
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    await user.click(screen.getByRole('button', { name: '打开所选输出文件夹' }))
    expect(launch).toHaveBeenLastCalledWith('reveal_in_file_manager', {
      kind: 'picker_selection', selection_handle: selection.selection_handle,
    })
    expect(JSON.stringify(launch.mock.calls)).not.toContain(selection.path)
    await user.click(screen.getByRole('button', { name: '选择成片文件夹' }))
    await user.click(screen.getByRole('button', { name: '打开所选输出文件夹' }))
    expect(launch).toHaveBeenCalledTimes(2)
    fireEvent.change(screen.getByLabelText('Publication output root'), { target: { value: 'D:\\Synthetic\\Different' } })
    await user.click(screen.getByRole('button', { name: '打开所选输出文件夹' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('请重新选择输出目录后再试')
    expect(launch).toHaveBeenCalledTimes(2)
    await user.click(screen.getByRole('button', { name: '关闭模板向导' }))
    await user.click(projectButton('工程首页'))
    await enterSettings()
    fireEvent.change(screen.getByLabelText('Publication output root'), { target: { value: selection.path } })
    await user.click(screen.getByRole('button', { name: '打开所选输出文件夹' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('请重新选择输出目录后再试')
    expect(launch).toHaveBeenCalledTimes(2)
    expect(gateway.commands).toEqual([])
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
        output_directory_to_create: null,
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
    await user.type(screen.getByLabelText('片名'), 'Movie')
    await user.type(screen.getByLabelText('年份'), '2026')
    fireEvent.change(screen.getByLabelText('Publication output root'), {
      target: { value: 'D:\\Library' },
    })
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))

    await waitFor(() => expect(gateway.commands).toContainEqual({
      operation: 'create_av_enhance_v27',
      media_basename: 'Movie (2026)',
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
      expect(runAllButton()).toBeDisabled()
      return
    }

    expect(screen.queryByRole('dialog', { name: 'AVEnhanceFlow v2.7.0 创作者向导' })).not.toBeInTheDocument()
    expect(await screen.findByLabelText('zniku.avenhance.v27.source_program 节点')).toBeInTheDocument()
    expect(screen.queryByText('C:\\synthetic\\source.mkv')).not.toBeInTheDocument()
    projectButton('继续处理向导')
    expect(screen.getByText('增强工作流已就绪')).toBeVisible()
    expect(gateway.templatePreviewArguments[0]).not.toHaveProperty('graph')
    expect(gateway.templatePreviewArguments[0]).not.toHaveProperty('definitions')

    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    projectButton('继续处理向导')
    expect(screen.getByText(/增强工作流已调整/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '重做' }))
    expect(screen.getByText('增强工作流已就绪')).toBeVisible()

    fireEvent.click(screen.getByLabelText('zniku.avenhance.v27.source_program 节点'))
    openLibrary()
    fireEvent.click(screen.getByRole('button', { name: '复制所选' }))
    expect(screen.getByText(/增强工作流已调整/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    projectButton('继续处理向导')
    expect(screen.getByText('增强工作流已就绪')).toBeVisible()
    fireEvent.click(screen.getByLabelText('zniku.avenhance.v27.source_program 节点'))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'source_ordinal（必填）' }), {
      target: { value: '1' },
    })
    await user.click(screen.getByRole('button', { name: '应用设置' }))
    projectButton('继续处理向导')
    expect(screen.getByText(/增强工作流已调整/)).toBeVisible()
  })

  it('使用单一画布搜索添加、复制节点，并实时阻断缺失 required input', async () => {
    const user = userEvent.setup()
    const ids = ['node.added', 'node.copied']
    render(<App gateway={new RecordingGateway()} nodeIdFactory={() => ids.shift()!} />)

    expect(await screen.findByText('Synthetic Studio Project')).toBeInTheDocument()
    expect(screen.getByText('当前编辑')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Studio Designer 画布' })).toBeInTheDocument()

    openLibrary()
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
    expect(projectButton('保存')).toBeEnabled()
    expect(runAllButton()).toBeDisabled()
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
    await user.type(projectPath(), 'C:\\synthetic\\media.zniku')
    await user.click(projectButton('新建'))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('create_project'))

    openLibrary()
    expect(await screen.findByRole('region', { name: '基础媒体节点' })).toBeInTheDocument()
    const presets = screen.getByRole('region', { name: 'VideoTransform presets' })
    await user.click(within(presets).getByRole('button', { name: /zniku\.media\.video_transform\.mr\.external/ }))
    expect(await screen.findByLabelText('node.mr 节点')).toBeInTheDocument()
    expect(rawParameters()).toHaveValue(
      '{\n  "strength": 3,\n  "model_name": "Synthetic Model"\n}',
    )
  })

  it('打开、新建、参数保存并通过结构化命令执行 Run all', async () => {
    const user = userEvent.setup()
    const gateway = new RecordingGateway()
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')

    projectButton('打开')
    await user.click(projectButton('打开'))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'open_project',
      path: 'C:\\synthetic\\project.zniku',
    }))

    await user.clear(projectPath())
    await user.type(projectPath(), 'C:\\synthetic\\new.zniku')
    await user.click(screen.getByText('高级工程信息'))
    await user.clear(screen.getByLabelText('Project name'))
    await user.type(screen.getByLabelText('Project name'), 'New Project')
    await user.click(projectButton('新建'))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'create_project',
      path: 'C:\\synthetic\\new.zniku',
      name: 'New Project',
    }))

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    fireEvent.change(rawParameters(), {
      target: { value: '{"strength":7,"model_name":"Synthetic Model"}' },
    })
    openInspectorTab('设置')
    await user.click(screen.getByRole('button', { name: '应用设置' }))
    await user.click(projectButton('保存'))
    await waitFor(() => {
      const command = gateway.commands.at(-1)
      expect(command?.operation).toBe('save_project')
      if (command?.operation === 'save_project') {
        expect(command.project.graph.nodes.find((node) => node.node_id === 'transform')?.parameters)
          .toEqual({ strength: 7, model_name: 'Synthetic Model' })
      }
    })

    await user.click(runAllButton())
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
    const queue = await selectHandoffTask()
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
    const queue = await selectHandoffTask()
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
    fireEvent.click(within(await selectHandoffTask()).getByRole('button', { name: '检查输出' }))
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
    fireEvent.click(within(await selectHandoffTask()).getByRole('button', { name: '检查输出' }))
    await flushReact()
    expect(screen.getByLabelText('上次完整预检失败')).toHaveTextContent('E_NEW_HANDOFF')
    expect(screen.getByLabelText('上次完整预检失败')).not.toHaveTextContent('E_OLD_HANDOFF')
    // 即使服务错误复用相同 Run/NodeRun IDs，resolved Project path 切换仍清除页面检查历史。
    gateway.envelope = { ...gateway.envelope, project_path: 'C:\\synthetic\\other-project.zniku' }
    fireEvent.click(projectButton('打开'))
    await flushReact()
    expect(gateway.commands.at(-1)?.operation).toBe('open_project')
    expect(screen.queryByLabelText('上次完整预检失败')).not.toBeInTheDocument()
  })

  it('External Handoff 队列展示路径、模型、日志并执行两阶段精确 Submit', async () => {
    const user = userEvent.setup()
    const gateway = new RecordingGateway(handoffEnvelope())
    render(<App gateway={gateway} />)

    let queueItem = await selectHandoffTask()
    expect(queueItem).toHaveTextContent('test.transform')
    expect(queueItem).toHaveTextContent('已等待')
    expect(queueItem).toHaveTextContent('C:\\synthetic\\source.mkv')
    expect(queueItem).toHaveTextContent('C:\\synthetic\\attempt-transform\\output.mkv')
    expect(within(queueItem).getByRole('button', { name: '复制输入路径' })).toBeEnabled()
    expect(within(queueItem).getByRole('button', { name: '复制目标路径' })).toBeEnabled()

    await user.click(within(queueItem).getByRole('button', { name: /transform/ }))
    expect(screen.getByRole('tab', { name: '文件', selected: true })).toBeInTheDocument()
    expect(screen.getByLabelText('步骤处理状态')).toHaveTextContent('等待外部处理')
    // 文件助手保持在文件页；操作始终绑定所选精确交接任务。
    queueItem = await selectHandoffTask()

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

    await user.click(screen.getByRole('button', { name: '返回当前编辑' }))
    fireEvent.click(await screen.findByLabelText('transform 节点'))
    openInspectorTab('设置')
    await user.click(screen.getByRole('button', { name: '处理到此步骤' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toMatchObject({
      operation: 'run_to',
      node_id: 'transform',
    }))

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    await user.click(screen.getByRole('button', { name: '从此步骤重新处理' }))
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
      expect(await screen.findByRole('button', { name: '查看问题' })).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: '查看本次处理流程' }))
      const sourceCard = await screen.findByLabelText('source 节点')
      expect(within(sourceCard).getByText('Completed')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: '返回当前编辑' }))
      const currentSourceCard = await screen.findByLabelText('source 节点')
      expect(within(currentSourceCard).getByText('Stale')).toBeInTheDocument()
      fireEvent.click(currentSourceCard)
      openInspectorTab('文件')
      expect(await screen.findByText('source.mkv', { selector: 'strong' })).toBeInTheDocument()

      openTaskTab('问题')
      fireEvent.click(screen.getByRole('button', { name: '定位步骤与设置' }))
      const runtime = await screen.findByLabelText('步骤处理状态')
      expect(runtime).toHaveTextContent('需要处理问题')
      expect(runtime).not.toHaveTextContent('40%')
      openInspectorTab('诊断')
      const details = screen.getByLabelText('运行身份与日志')
      expect(details).toHaveTextContent(reason)
      expect(details).toHaveTextContent(reason === 'cancelled' ? '操作者取消' : '应用重启中断')
    },
  )

  it('active_operation 期间禁用工程切换与 Runtime mutation，但允许编辑当前图', async () => {
    const gateway = new RecordingGateway({ ...handoffEnvelope(), active_operation: 'abandon_run' })
    render(<App gateway={gateway} />)
    // 队列详情先于 ReactFlow 初始化到达；本例验证已就绪画布中的 mutation 资格，不抢初始选区。
    await screen.findByLabelText('transform 节点')
    const queueItem = await selectHandoffTask()

    for (const name of ['打开', '新建', '保存']) expect(projectButton(name)).toBeDisabled()
    expect(runAllButton()).toBeDisabled()
    openInspectorTab('设置')
    for (const name of ['处理到此步骤', '从此步骤重新处理']) expect(screen.getByRole('button', { name })).toBeDisabled()
    openInspectorTab('文件')
    expect(within(queueItem).getByRole('button', { name: '检查输出' })).toBeDisabled()
    openTaskTab('当前处理')
    expect(screen.getByRole('button', { name: '放弃本次处理' })).toBeDisabled()
  })

  it('后启动的 completed 局部 Run 不隐藏仍 waiting 的整图 Run', async () => {
    render(<App gateway={new RecordingGateway(threeRunEnvelope())} />)

    await screen.findByLabelText('transform 节点')
    const history = openTaskTab('历史记录')
    const rows = within(history).getAllByRole('button', { name: /^查看处理记录 / })
    expect(rows).toHaveLength(3)
    expect(rows.find((button) => (button as HTMLButtonElement).value === threeRunFixtureIds.waitingFullRun)).toHaveAttribute('aria-current', 'true')
    expect(screen.getByLabelText('任务摘要')).toHaveTextContent('1 项等待外部处理')
    expect(within(screen.getByLabelText('transform 节点')).getByText('Waiting external'))
      .toBeInTheDocument()
  })

  it('手动选择 terminal Run 后 refresh 不抢占选择且其他活动待办仍可发现', async () => {
    vi.useFakeTimers()
    const gateway = new RecordingGateway(threeRunEnvelope())
    render(<App gateway={gateway} />)
    await flushReact()

    selectHistory(threeRunFixtureIds.laterLocalRun)
    await flushReact()
    const selected = () => within(openTaskTab('历史记录')).getAllByRole('button', { name: /^查看处理记录 / })
      .find((button) => (button as HTMLButtonElement).value === threeRunFixtureIds.laterLocalRun)
    expect(selected()).toHaveAttribute('aria-current', 'true')
    expect(screen.getByLabelText('任务摘要')).toHaveTextContent('当前另有 1 项任务待操作')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_501)
    })
    expect(gateway.inspectCount).toBeGreaterThan(1)
    expect(selected()).toHaveAttribute('aria-current', 'true')
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
    const historyRows = () => within(openTaskTab('历史记录')).getAllByRole('button', { name: /^查看处理记录 / }) as HTMLButtonElement[]
    expect(historyRows().find((button) => button.value === threeRunFixtureIds.waitingFullRun)).toHaveAttribute('aria-current', 'true')
    fireEvent.click(screen.getByRole('button', { name: '加载更早记录' }))
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
    expect(historyRows().find((button) => button.value === threeRunFixtureIds.laterLocalRun)).toHaveAttribute('aria-current', 'true')
    expect(historyRows().map((button) => button.value)).not.toContain(
      threeRunFixtureIds.waitingFullRun,
    )
    expect(screen.getByRole('status', { name: '操作提示' })).toHaveTextContent(
      '先前选择的 Run 已不存在，已重新选择可用 Run。',
    )

    await act(async () => oldPage.resolve({
      contract_version: '0.3.0',
      run_summaries: [initial.run_summaries[1]!],
      next_run_cursor: 'cursor.stale',
    }))
    expect(historyRows().map((button) => button.value)).not.toContain(
      threeRunFixtureIds.waitingFullRun,
    )
    expect(screen.queryByRole('button', { name: '加载更早记录' })).not.toBeInTheDocument()
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
    expect(screen.getByLabelText('任务摘要')).not.toHaveTextContent('%')

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
    fireEvent.click(await screen.findByRole('button', { name: '查看本次处理流程' }))
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
    expect(runtime).toHaveTextContent('处理已取消')
    expect(runtime).not.toHaveTextContent('100%')
    openInspectorTab('诊断')
    expect(screen.getByLabelText('运行身份与日志')).toHaveTextContent('操作者取消 automatic attempt')
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

    fireEvent.change(projectPath(), {
      target: { value: 'C:\\synthetic\\reopen.zniku' },
    })
    fireEvent.click(projectButton('打开'))
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
    selectHistory(runB)
    await flushReact()
    expect(within(screen.getByLabelText('source 节点')).getByText('30%')).toBeInTheDocument()

    await act(async () => {
      slowA.resolve(projectedProgressDetail(0.2, 0.9, { current: 90, total: 100 }))
      await Promise.resolve()
    })
    expect(within(openTaskTab('历史记录')).getAllByRole('button', { name: /^查看处理记录 / })
      .find((button) => (button as HTMLButtonElement).value === runB)).toHaveAttribute('aria-current', 'true')
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
    await screen.findByLabelText('source 节点')

    fireEvent.click(runAllButton())
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('run_all'))
    selectHistory(threeRunFixtureIds.laterLocalRun)
    expect(runAllButton()).toBeDisabled()

    await act(async () => {
      slowRun.resolve(envelope)
      await Promise.resolve()
    })
    await waitFor(() => expect(runAllButton()).toBeEnabled())
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
    expect(resourceHealth()).toHaveTextContent('DETAIL STALE')
    openInspectorTab('设置')
    expect(screen.getByRole('button', { name: '从此步骤重新处理' })).toBeDisabled()
    expect(runAllButton()).toBeEnabled()
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
    expect(screen.getByRole('status', { name: '操作提示' })).toHaveTextContent('E_STUDIO_PROGRESS_REGRESSION')

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
    expect(resourceHealth()).toHaveTextContent('DETAIL OK')
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
    expect(resourceHealth()).toHaveTextContent('DETAIL STALE')
    openTaskTab('问题')
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
    expect(resourceHealth()).toHaveTextContent('DETAIL OK')
    expect(within(screen.getByLabelText('source 节点')).getByText('70%')).toBeInTheDocument()
    expect(screen.queryByText('detail offline')).not.toBeInTheDocument()
  })

  it('打开工程默认保留完整当前图；历史快照只在显式查看时显示且不被保存改写', async () => {
    const detail = handoffDetailEnvelope()
    const originalRun = JSON.stringify(detail.run)
    const extra = { ...projectSnapshot.project.graph.nodes[0]!, node_id: 'draft-only', ui_position: { x: 910, y: 220 } }
    const snapshot = { ...projectSnapshot, project: { ...projectSnapshot.project, graph: {
      ...projectSnapshot.project.graph, nodes: [...projectSnapshot.project.graph.nodes, extra],
    } } }
    const gateway = new RecordingGateway({ ...handoffEnvelope(), snapshot }, { detail: () => detail })
    const { unmount } = render(<App gateway={gateway} />)
    expect(await screen.findByLabelText('draft-only 节点')).toBeInTheDocument()
    expect(screen.getByText('当前编辑')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: '查看本次处理流程' }))
    expect(screen.queryByLabelText('draft-only 节点')).not.toBeInTheDocument()
    expect(screen.getByText('本次处理的流程（只读）')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '整理布局' })).toBeDisabled()
    fireEvent.click(projectButton('打开'))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('open_project'))
    expect(await screen.findByLabelText('draft-only 节点')).toBeInTheDocument()
    expect(screen.getByText('当前编辑')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('draft-only 节点'))
    displaySettings()
    fireEvent.change(screen.getByLabelText('节点别名'), { target: { value: '新增素材' } })
    fireEvent.blur(screen.getByLabelText('节点别名'))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('save_project'))
    expect(screen.getByText('当前编辑')).toBeInTheDocument()
    expect(JSON.stringify(detail.run)).toBe(originalRun)
    unmount()
    render(<App gateway={gateway} />)
    expect(await screen.findByLabelText('draft-only 节点')).toHaveTextContent('新增素材')
    expect(screen.getByText('当前编辑')).toBeInTheDocument()
    expect(JSON.stringify(detail.run)).toBe(originalRun)
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
    await screen.findByText('当前编辑')
    fireEvent.click(await screen.findByLabelText('transform 节点'))
    expect(screen.getByRole('button', { name: '处理到此步骤' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '从此步骤重新处理' })).toBeDisabled()
  })

  it('actionable waiting Run 可显式 abandon，命令保持精确 Run identity', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const gateway = new RecordingGateway(handoffEnvelope())
    render(<App gateway={gateway} />)
    await screen.findByLabelText('source 节点')
    const button = within(openTaskTab('当前处理')).getByRole('button', { name: '放弃本次处理' })
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

    await screen.findByLabelText('source 节点')
    openTaskTab('历史记录')
    await user.click(await screen.findByRole('button', { name: '加载更早记录' }))
    await waitFor(() => expect(gateway.historyArguments).toEqual([['cursor.page.1', 20]]))
    await user.click(await screen.findByRole('button', { name: '加载更早记录' }))
    await waitFor(() => expect(gateway.historyArguments).toEqual([
      ['cursor.page.1', 20],
      ['cursor.page.2', 20],
    ]))

    const rows = within(openTaskTab('历史记录')).getAllByRole('button', { name: /^查看处理记录 / }) as HTMLButtonElement[]
    expect(rows.map((button) => button.value)).toEqual(expect.arrayContaining([
      first.run_id,
      second.run_id,
      third.run_id,
    ]))
    expect(screen.queryByRole('button', { name: '加载更早记录' })).not.toBeInTheDocument()
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

    await screen.findByLabelText('source 节点')
    openTaskTab('历史记录')
    await user.click(await screen.findByRole('button', { name: '加载更早记录' }))
    fireEvent.change(projectPath(), {
      target: { value: 'C:\\synthetic\\other.zniku' },
    })
    await user.click(projectButton('打开'))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('open_project'))

    openTaskTab('历史记录')
    const newPageButton = await screen.findByRole('button', { name: '加载更早记录' })
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
      expect(screen.queryByRole('button', { name: '加载更早记录' })).not.toBeInTheDocument()
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
    openInspectorTab('诊断')
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
    openInspectorTab('诊断')
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
    expect(resourceHealth()).toHaveTextContent('LOG OK')
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

    fireEvent.change(projectPath(), {
      target: { value: 'C:\\synthetic\\detail-new.zniku' },
    })
    fireEvent.click(projectButton('打开'))
    await flushReact()
    fireEvent.click(screen.getByLabelText('source 节点'))
    expect(within(screen.getByLabelText('步骤处理状态')).getByText('80%')).toBeInTheDocument()

    await act(async () => {
      slowFailure.reject(new Error('old detail offline'))
      await Promise.resolve()
    })
    expect(resourceHealth()).toHaveTextContent('DETAIL OK')
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
    const submit = within(await selectHandoffTask()).getByRole('button', {
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
    fireEvent.click(within(await selectHandoffTask()).getByRole('button', { name: '提交并继续' }))
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

  it.each(['success', 'failure'] as const)('Submit 先排空同任务的被动读取，迟到 %s 不覆盖显式检查', async (outcome) => {
    vi.useFakeTimers()
    const passive = new Deferred<ExternalHandoffReadiness>()
    let delayPassive = false
    const gateway = new RecordingGateway(handoffEnvelope(), {
      readiness: (_runId, _nodeRunId, probe) => probe
        ? handoffReadinessEnvelope('probe_passed', true)
        : delayPassive ? passive.promise : handoffReadinessEnvelope('present', false),
    })
    render(<App gateway={gateway} />)
    await flushReact()
    fireEvent.click(within(await selectHandoffTask()).getByRole('button', { name: '检查输出' }))
    await flushReact()
    delayPassive = true
    await act(async () => { await vi.advanceTimersByTimeAsync(1_501) })
    expect(gateway.readinessArguments.filter((item) => !item[2])).toHaveLength(2)

    fireEvent.click(within(await selectHandoffTask()).getByRole('button', { name: '提交并继续' }))
    await flushReact()
    // 未结束的 HTTP 不能只丢弃结果：必须在真正 Submit 前结束，否则服务仍可能收到过期读取。
    expect(gateway.readinessArguments.filter((item) => item[2])).toHaveLength(1)
    expect(gateway.commands.filter((command) => command.operation === 'submit_external')).toHaveLength(0)
    await act(async () => {
      if (outcome === 'success') passive.resolve(handoffReadinessEnvelope('missing', false))
      else passive.reject(new Error('synthetic old passive failure'))
    })
    await flushReact()
    expect(gateway.readinessArguments.filter((item) => item[2])).toHaveLength(2)
    expect(gateway.commands.filter((command) => command.operation === 'submit_external')).toEqual([{
      operation: 'submit_external', run_id: handoffFixtureIds.run,
      node_run_id: handoffFixtureIds.transformNodeRun, handoff_id: handoffFixtureIds.handoff,
    }])
    openInspectorTab('诊断')
    openDetails('服务高级诊断')
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('READINESS OK')
    expect(screen.queryByText('synthetic old passive failure')).not.toBeInTheDocument()
  })

  it('被动读取排空超时只取消本次 Submit，迟到响应不会自动重试', async () => {
    vi.useFakeTimers()
    const passive = new Deferred<ExternalHandoffReadiness>()
    let delayPassive = false
    const gateway = new RecordingGateway(handoffEnvelope(), {
      readiness: (_runId, _nodeRunId, probe) => probe
        ? handoffReadinessEnvelope('probe_passed', true)
        : delayPassive ? passive.promise : handoffReadinessEnvelope('present', false),
    })
    render(<App gateway={gateway} />)
    await flushReact()
    fireEvent.click(within(await selectHandoffTask()).getByRole('button', { name: '检查输出' }))
    await flushReact()
    delayPassive = true
    await act(async () => { await vi.advanceTimersByTimeAsync(1_501) })
    fireEvent.click(within(await selectHandoffTask()).getByRole('button', { name: '提交并继续' }))
    await flushReact()
    await act(async () => { await vi.advanceTimersByTimeAsync(5_001) })
    expect(gateway.commands.filter((command) => command.operation === 'submit_external')).toHaveLength(0)
    expect(gateway.readinessArguments.filter((item) => item[2])).toHaveLength(1)
    expect(screen.getByRole('status', { name: '操作提示' })).toHaveTextContent('仍未结束；本次没有提交')
    expect(within(await selectHandoffTask()).getByRole('button', { name: '提交并继续' })).toBeEnabled()
    await act(async () => { passive.reject(new Error('synthetic expired passive failure')) })
    await flushReact()
    expect(gateway.commands.filter((command) => command.operation === 'submit_external')).toHaveLength(0)
    openInspectorTab('诊断')
    openDetails('服务高级诊断')
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('READINESS OK')
    expect(screen.queryByText('synthetic expired passive failure')).not.toBeInTheDocument()
  })

  it('跨 Run 导航保留在途被动读取但最多八个，旧请求结束后才释放名额', async () => {
    vi.useFakeTimers()
    const initialStatus = handoffEnvelope()
    const runIds = Array.from({ length: 9 }, (_, index) => `00000000-0000-4000-8000-${String(100 + index).padStart(12, '0')}`)
    const initialDetail = handoffDetailEnvelope()
    const details = new Map(runIds.map((runId, index) => [runId, {
      ...initialDetail, run: { ...initialDetail.run, run_id: runId,
        node_runs: initialDetail.run.node_runs.map((nodeRun, ordinal) => {
          const id = `00000000-0000-4000-8000-${String(1000 + index * 10 + ordinal).padStart(12, '0')}`
          return { ...nodeRun, run_id: runId, node_run_id: id,
            external_handoff: nodeRun.external_handoff ? { ...nodeRun.external_handoff, node_run_id: id,
              handoff_id: `00000000-0000-4000-8000-${String(2000 + index).padStart(12, '0')}` } : null,
          }
        }),
      },
    }]))
    const pending: Array<Deferred<ExternalHandoffReadiness>> = []
    let delayPassive = false
    const envelope: StatusEnvelope = { ...initialStatus, active_run_id: runIds[0]!,
      run_summaries: runIds.map((runId) => ({ ...initialStatus.run_summaries[0]!, run_id: runId })),
    }
    const gateway = new RecordingGateway(envelope, {
      detail: (runId) => details.get(runId)!,
      readiness: (runId, nodeRunId) => {
        if (delayPassive) {
          const flight = new Deferred<ExternalHandoffReadiness>()
          pending.push(flight)
          return flight.promise
        }
        const handoff = details.get(runId)!.run.node_runs.find((item) => item.external_handoff)!.external_handoff!
        return { ...handoffReadinessEnvelope('present', false), run_id: runId, node_run_id: nodeRunId, handoff_id: handoff.handoff_id }
      },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    delayPassive = true
    await act(async () => { await vi.advanceTimersByTimeAsync(1_501) })
    for (const runId of runIds.slice(1)) {
      selectHistory(runId)
      await flushReact()
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    }
    expect(pending).toHaveLength(8)
    expect(gateway.readinessArguments).toHaveLength(9)
    await act(async () => { pending[0]!.reject(new Error('synthetic inactive Run failure')) })
    await act(async () => { await vi.advanceTimersByTimeAsync(1_501) })
    expect(pending).toHaveLength(9)
    expect(gateway.readinessArguments.at(-1)?.[0]).toBe(runIds.at(-1))
    openInspectorTab('诊断')
    openDetails('服务高级诊断')
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('READINESS OK')
    expect(screen.queryByText('synthetic inactive Run failure')).not.toBeInTheDocument()
  })

  it.each(['进行中', '已完成'] as const)('Submit %s 时迟到的旧 waiting detail 不重启被动读取', async (phase) => {
    vi.useFakeTimers()
    const command = new Deferred<StatusEnvelope>()
    const oldWaitingDetail = new Deferred<RunDetailEnvelope>()
    const finalDetail = new Deferred<RunDetailEnvelope>()
    let submitting = false
    let finished = false
    const waitingDetail = handoffDetailEnvelope()
    const endedAt = '2026-08-24T00:00:03Z'
    const completedDetail: RunDetailEnvelope = {
      ...waitingDetail,
      run: { ...waitingDetail.run, state: 'completed', ended_at: endedAt,
        node_runs: waitingDetail.run.node_runs.map((nodeRun) => ({ ...nodeRun,
          state: 'completed', started_at: nodeRun.started_at ?? endedAt, ended_at: endedAt,
          progress: 1, external_handoff: null,
        })),
      },
    }
    const initialStatus = handoffEnvelope()
    const completedStatus: StatusEnvelope = { ...initialStatus,
      run_summaries: initialStatus.run_summaries.map((summary) => ({ ...summary,
        state: 'completed', actionable: false, requires_operator_action: false,
        ended_at: endedAt, latest_activity_at: endedAt,
        state_counts: { pending: 0, running: 0, waiting_external: 0, completed: summary.node_count, failed: 0 },
      })),
    }
    const gateway = new RecordingGateway(initialStatus, {
      detail: () => finished ? finalDetail.promise : submitting ? oldWaitingDetail.promise : waitingDetail,
      command: () => { submitting = true; return command.promise },
    })
    render(<App gateway={gateway} />)
    await flushReact()
    fireEvent.click(within(await selectHandoffTask()).getByRole('button', { name: '检查输出' }))
    await flushReact()
    fireEvent.click(within(await selectHandoffTask()).getByRole('button', { name: '提交并继续' }))
    await flushReact()
    expect(gateway.commands.filter((item) => item.operation === 'submit_external')).toHaveLength(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(1_501) })
    expect(gateway.inspectRunCount).toBeGreaterThan(1)
    if (phase === '已完成') {
      finished = true
      gateway.envelope = completedStatus
      await act(async () => { command.resolve(completedStatus) })
      await flushReact()
    }
    await act(async () => { oldWaitingDetail.resolve(waitingDetail) })
    await flushReact()
    expect(gateway.readinessArguments.filter((item) => !item[2])).toHaveLength(1)

    finished = true
    gateway.envelope = completedStatus
    await act(async () => { command.resolve(completedStatus) })
    await flushReact()
    // 完成通知已到，但新的最终 detail 还在路上；旧 waiting 不能在这个窗口重新取得观察资格。
    await act(async () => { await vi.advanceTimersByTimeAsync(1_501) })
    expect(gateway.readinessArguments.filter((item) => !item[2])).toHaveLength(1)
    await act(async () => { finalDetail.resolve(completedDetail) })
    await flushReact()
    await act(async () => { await vi.advanceTimersByTimeAsync(3_001) })
    expect(gateway.readinessArguments.filter((item) => !item[2])).toHaveLength(1)
    openInspectorTab('诊断')
    openDetails('服务高级诊断')
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('READINESS OK')
    expect(screen.queryByRole('button', { name: '提交并继续' })).not.toBeInTheDocument()
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
      within(await selectHandoffTask()).getByRole('button', {
        name: '检查输出',
      }),
    )
    await flushReact()

    fireEvent.change(projectPath(), {
      target: { value: 'C:\\synthetic\\probe-new.zniku' },
    })
    fireEvent.click(projectButton('打开'))
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
    fireEvent.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    await flushReact()
    openInspectorTab('文件')
    const taskList = await screen.findByRole('region', { name: '待外部处理任务' })
    expect(within(taskList).getAllByRole('button')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: '检查输出' })).not.toBeInTheDocument()
    openInspectorTab('诊断')
    openDetails('服务高级诊断')
    expect(screen.getByLabelText('Resource channel health')).toHaveTextContent('READINESS STALE')
    openInspectorTab('文件')
    fireEvent.click(within(taskList).getByRole('button', { name: /^查看外部任务：test\.transform(?:（1）)?$/ }))
    expect(await screen.findAllByRole('button', { name: '检查输出' })).toHaveLength(1)
    expect(screen.getByRole('button', { name: '检查输出' })).toBeDisabled()
  })

  it('两个同名外部节点的画布、Inspector与任务导航一致，复制和导入只绑定所选任务', async () => {
    window.localStorage.removeItem('zniku.studio.density')
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    const original = handoffDetailEnvelope()
    const waiting = original.run.node_runs.find((item) => item.external_handoff)!
    const pair = ['A', 'B'].map((suffix, index): NodeRunWire => ({ ...waiting,
      node_id: index === 0 ? 'transform' : 'transform.b', node_run_id: `node-run-${suffix}`,
      external_handoff: { ...waiting.external_handoff!, handoff_id: `handoff-${suffix}`, node_run_id: `node-run-${suffix}`,
        input_artifact_ids: [`input-${suffix}`], output_targets: [{ port_id: 'out', ordinal: null, path: `D:\\synthetic\\task-${suffix}\\out.mkv` }] },
    }))
    const graph = { ...original.run.graph_snapshot, nodes: [...original.run.graph_snapshot.nodes,
      { ...original.run.graph_snapshot.nodes.find((item) => item.node_id === 'transform')!, node_id: 'transform.b' }],
      edges: [...original.run.graph_snapshot.edges, { source_node_id: 'source', source_port_id: 'out', target_node_id: 'transform.b', target_port_id: 'in', ordinal: null }] }
    const artifacts = ['A', 'B'].map((suffix) => ({ ...original.artifacts[0]!, artifact_id: `input-${suffix}`, path: `D:\\synthetic\\input-${suffix}.mkv` }))
    const detail: RunDetailEnvelope = { ...original, artifacts, run: { ...original.run, graph_snapshot: graph,
      node_runs: [...original.run.node_runs.filter((item) => item.node_id !== 'transform'), ...pair] },
      handoff_contracts: pair.map((nodeRun, index) => ({ node_run_id: nodeRun.node_run_id, handoff_id: nodeRun.external_handoff!.handoff_id,
        input_artifact_id: `input-${index === 0 ? 'A' : 'B'}`, title: '正式处理要求', fields: [{ label: '输出 exact N', value: index === 0 ? '899' : '902' }] })) }
    const baseStatus = handoffEnvelope()
    const gateway = new RecordingGateway({ ...baseStatus, snapshot: { ...baseStatus.snapshot!, project: { ...baseStatus.snapshot!.project, graph } },
      run_summaries: baseStatus.run_summaries.map((summary) => ({ ...summary, node_count: 4, state_counts: { ...summary.state_counts, waiting_external: 2 } })) }, {
      detail: () => detail,
      presentations: () => presentationEnvelope([nodePresentation(transformDefinition, '画质增强')], [{ category_id: 'test', title: '处理', description: null, order: 1 }]),
      readiness: (_runId, nodeRunId, probe) => {
        const node = pair.find((item) => item.node_run_id === nodeRunId)!
        const observed = handoffReadinessEnvelope(probe ? 'probe_passed' : 'present', probe)
        return { ...observed, node_run_id: nodeRunId, handoff_id: node.external_handoff!.handoff_id,
          targets: observed.targets.map((target) => ({ ...target, path: node.external_handoff!.output_targets[0]!.path })) }
      },
    })
    let preview!: HandoffImportPreviewEnvelope
    const host: HostBridge = { configured: true, inspectCapabilities: vi.fn(async () => hostCapabilitiesEnvelope()), launch: vi.fn(),
      pick: vi.fn(async () => [{ selection_handle: 'finished-b', path: 'D:\\external\\finished-B.mkv' }]),
      previewHandoffImport: vi.fn(async (request) => { preview = { ...request, import_id: 'import-b', source_name: 'finished-B.mkv', source_size: 2048,
        target_path: pair[1]!.external_handoff!.output_targets[0]!.path, replace_existing: false, expires_in_seconds: 300 }; return preview }),
      confirmHandoffImport: vi.fn(async () => ({ ...preview, status: 'imported' as const })),
    }
    render(<App gateway={gateway} hostBridge={host} />)
    await user.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    openInspectorTab('文件')
    const list = await screen.findByRole('region', { name: '待外部处理任务' })
    expect(within(list).getAllByRole('button')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: '复制目标路径' })).not.toBeInTheDocument()
    expect(within(list).getByRole('button', { name: '查看外部任务：画质增强（1）' })).toBeVisible()
    fireEvent.click(await screen.findByLabelText('画质增强（1） 节点'))
    const inspector = screen.getByRole('complementary', { name: '步骤设置与输出' })
    expect(within(inspector).getByRole('heading', { level: 2 })).toHaveTextContent('画质增强（1）')
    expect(within(inspector).getByRole('article', { name: '外部处理：画质增强（1）' })).toBeVisible()
    expect(within(inspector).queryByText('input-B.mkv')).not.toBeInTheDocument()
    await user.click(within(inspector).getByRole('button', { name: '复制目标路径' }))
    expect(writeText).toHaveBeenLastCalledWith(pair[0]!.external_handoff!.output_targets[0]!.path)
    fireEvent.click(screen.getByLabelText('画质增强（2） 节点'))
    expect(within(inspector).getByRole('heading', { level: 2 })).toHaveTextContent('画质增强（2）')
    expect(within(inspector).queryByText('input-A.mkv')).not.toBeInTheDocument()
    expect(within(inspector).getByText('预期输出：902 帧')).toBeVisible()
    await user.click(within(inspector).getByRole('button', { name: '选择处理好的文件' }))
    const dialog = await screen.findByRole('dialog', { name: '确认导入外部处理文件' })
    expect(host.previewHandoffImport).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.0', selection_handle: 'finished-b',
      project_session_id: baseStatus.project_session_id, run_id: pair[1]!.run_id, node_run_id: pair[1]!.node_run_id,
      handoff_id: pair[1]!.external_handoff!.handoff_id, port_id: 'out', ordinal: null })
    expect(within(dialog).getByText(pair[1]!.external_handoff!.output_targets[0]!.path)).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: '确认复制到此任务' }))
    await waitFor(() => expect(within(inspector).getByText(/已导入到“画质增强（2）”/)).toBeVisible())
    expect(host.confirmHandoffImport).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.0', import_id: 'import-b', overwrite: false })
    expect(within(inspector).getByRole('button', { name: '提交并继续' })).toBeDisabled()
    expect(gateway.commands).toHaveLength(0)
    fireEvent.click(screen.getByLabelText('test.source 节点'))
    expect(within(inspector).queryByRole('article')).not.toBeInTheDocument()
    expect(within(inspector).queryByRole('button', { name: '选择处理好的文件' })).not.toBeInTheDocument()
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
    fireEvent.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    openLibrary()
    expect(await screen.findByRole('button', { name: /媒体输入 · test\.source@0\.2\.0/ })).toBeVisible()
    await userEvent.click(projectButton('打开'))
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
    expect((rawParameters() as HTMLTextAreaElement).value).toContain('"strength": 4')
    fireEvent.change(rawParameters(), {
      target: { value: '{"strength":5,"model_name":"Synthetic Model"}' },
    })
    openInspectorTab('设置')
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(5)
    expect(runAllButton()).toBeDisabled()
    expect(screen.getByRole('button', { name: '处理到此步骤' })).toBeDisabled()

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
    expect(runAllButton()).toBeDisabled()
    openTaskTab('问题')
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
    displaySettings()
    const alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '等待保存' } })
    fireEvent.blur(alias)
    fireEvent.click(runAllButton())
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
    displaySettings()
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
    fireEvent.change(rawParameters(), { target: { value: '{"strength":8,"model_name":"Synthetic Model"}' } })
    openInspectorTab('设置')
    fireEvent.click(screen.getByRole('button', { name: '应用设置' }))
    fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(3)
    fireEvent.click(projectButton('保存'))
    await waitFor(() => expect(gateway.envelope.studio_state?.node_views[0]?.display_name).toBe('第一章增强'))
    expect(gateway.envelope.snapshot?.project.graph).toEqual(projectSnapshot.project.graph)
    const commandCount = gateway.commands.length
    fireEvent.click(viewButton('返回创作者模式'))
    fireEvent.click(viewButton('高级节点图'))
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
    displaySettings()
    const alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '未保存的增强' } })
    fireEvent.blur(alias)
    expect((await screen.findAllByText(/另一窗口已保存更新/)).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: '未保存的增强' })).toBeVisible()
    expect(runAllButton()).toBeDisabled()
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
    displaySettings()
    let alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '第一次编辑' } })
    fireEvent.blur(alias)
    await waitFor(() => expect(gateway.commands).toHaveLength(1))
    alias = screen.getByLabelText('节点别名')
    fireEvent.change(alias, { target: { value: '最后一次编辑' } })
    fireEvent.blur(alias)
    fireEvent.click(runAllButton())
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
    await screen.findByRole('button', { name: '查看本次处理流程' })
    // 运行入口来自轻量状态，不代表异步详情与实际画布节点已经挂载。
    fireEvent.click(await screen.findByLabelText('transform 节点'))
    fireEvent.change(rawParameters(), { target: { value: '{"strength":9,"model_name":"Synthetic Model"}' } })
    openInspectorTab('设置')
    fireEvent.click(screen.getByRole('button', { name: '应用设置' }))
    await waitFor(() => expect(gateway.envelope.snapshot?.project.graph.nodes[1]?.parameters.strength).toBe(9))
    fireEvent.click(screen.getByRole('button', { name: '查看本次处理流程' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(3)
    expect(screen.getByRole('button', { name: '应用设置' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled()
  })

  it('Inspector 页签切换与轮询保持同一未应用草稿，高级模式不自动读取日志', async () => {
    vi.useFakeTimers()
    const gateway = new RecordingGateway(handoffEnvelope())
    render(<App gateway={gateway} />)
    await flushReact()
    fireEvent.click(screen.getByRole('button', { name: '关闭工程首页' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    expect(screen.getByRole('tab', { name: '设置' })).toHaveAttribute('aria-selected', 'true')
    expect(gateway.logArguments).toHaveLength(0)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'strength' }), { target: { value: '4' } })
    openInspectorTab('文件')
    await act(async () => { await vi.advanceTimersByTimeAsync(1_501) })
    expect(screen.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
    expect(gateway.logArguments).toHaveLength(0)
    expect(gateway.commands).toHaveLength(0)
    const raw = rawParameters()
    await flushReact()
    expect(gateway.logArguments.at(-1)?.[1]).toBe(handoffFixtureIds.transformNodeRun)
    expect((raw as HTMLTextAreaElement).value).toContain('"strength": 4')
    fireEvent.change(raw, { target: { value: '{"strength":5,"model_name":"Synthetic Model"}' } })
    openInspectorTab('设置')
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(5)
    expect(screen.getByRole('button', { name: '应用设置' })).toBeEnabled()
    expect(gateway.commands).toHaveLength(0)
    expect(gateway.envelope.snapshot?.project.graph.nodes[1]?.parameters.strength).toBe(3)
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

    await screen.findByText('Synthetic Studio Project')
    openTaskTab('历史记录')
    await user.click(screen.getByRole('button', { name: '加载更早记录' }))
    selectHistory(terminal.run_id)
    const terminalRow = () => within(screen.getByRole('region', { name: '任务抽屉' })).getAllByRole('button')
      .find((button) => (button as HTMLButtonElement).value === terminal.run_id)
    await waitFor(() => expect(terminalRow()).toHaveAttribute('aria-current', 'true'))

    await user.click(projectButton('打开'))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('open_project'))
    expect(terminalRow()).toHaveAttribute('aria-current', 'true')

    expect(screen.getByText('当前编辑')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('transform 节点'))
    fireEvent.change(rawParameters(), {
      target: { value: '{"strength":4,"model_name":"Synthetic Model"}' },
    })
    openInspectorTab('设置')
    await user.click(screen.getByRole('button', { name: '应用设置' }))
    await user.click(projectButton('保存'))
    await waitFor(() => expect(gateway.commands.at(-1)?.operation).toBe('save_project'))
    expect(terminalRow()).toHaveAttribute('aria-current', 'true')

    await user.clear(projectPath())
    await user.type(projectPath(), 'C:\\synthetic\\other.zniku')
    await user.click(projectButton('打开'))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'open_project',
      path: 'C:\\synthetic\\other.zniku',
    }))
    openTaskTab('历史记录')
    expect(terminalRow()).toBeUndefined()
    expect(screen.getByText('尚无处理记录。')).toBeVisible()
  })

  it('同设置的 completed 历史与另一 waiting 共存时，输出导航和返回编辑保留未应用草稿', async () => {
    const completedId = threeRunFixtureIds.laterLocalRun
    const completed = threeRunDetail(completedId)
    const transformRunId = '00000000-0000-4000-8000-000000000081'
    const outputId = '00000000-0000-4000-8000-000000000082'
    const output = { ...completed.artifacts[0]!, artifact_id: outputId,
      producer_node_run_id: transformRunId, path: 'C:\\synthetic\\completed-transform\\output.mkv' }
    const completedDetail: RunDetailEnvelope = { ...completed, artifacts: [...completed.artifacts, output],
      run: { ...completed.run, selected_targets: ['transform'], node_runs: [...completed.run.node_runs, {
        ...handoffDetailEnvelope().run.node_runs[1]!, node_run_id: transformRunId, run_id: completedId,
        state: 'completed', external_handoff: null, input_artifact_ids: [completed.artifacts[0]!.artifact_id],
        output_artifact_ids: [outputId], ended_at: completed.run.ended_at, progress: 1,
      }] } }
    const fixture = threeRunEnvelope()
    const envelope: StatusEnvelope = { ...fixture, run_summaries: fixture.run_summaries.map((summary) => summary.run_id === completedId
      ? { ...summary, selected_targets: ['transform'], node_count: 2,
        state_counts: { pending: 0, running: 0, waiting_external: 0, completed: 2, failed: 0 } }
      : summary) }
    const gateway = new RecordingGateway(envelope, { detail: (runId) => runId === completedId ? completedDetail : threeRunDetail(runId) })
    render(<App gateway={gateway} />)
    await screen.findByLabelText('transform 节点')
    selectHistory(completedId)
    await waitFor(() => expect(screen.getByRole('button', { name: '查看本次输出' })).toBeVisible())
    const canvas = within(screen.getByRole('region', { name: 'Studio Designer 画布' }))
    fireEvent.click(canvas.getByRole('button', { name: '返回当前编辑' }))
    fireEvent.click(screen.getByLabelText('transform 节点'))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'strength' }), { target: { value: '4' } })
    expect(gateway.commands).toHaveLength(0)

    openTaskTab('当前处理')
    const outputList = screen.getByRole('region', { name: '本次输出列表' })
    // 当前草稿与该 snapshot 的已应用设置均为3；只读文件导航仍属于已选择的 completed Run。
    fireEvent.click(within(outputList).getAllByRole('button', { name: '查看文件详情' })[1]!)
    expect(canvas.getByText('本次处理的流程（只读）')).toBeVisible()
    expect(screen.getByRole('button', { name: '查看本次输出' })).toBeVisible()
    expect(screen.getByLabelText('任务摘要')).toHaveTextContent('当前另有 1 项任务待操作')
    const waitingTask = within(screen.getByRole('region', { name: '活动任务导航' })).getAllByRole('button')
      .find((button) => (button as HTMLButtonElement).value === threeRunFixtureIds.waitingFullRun)
    expect(waitingTask).toBeVisible()
    openInspectorTab('设置')
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(4)
    expect(screen.getByRole('button', { name: '应用设置' })).toBeDisabled()
    expect(gateway.commands).toHaveLength(0)
    expect(gateway.envelope.snapshot?.project.graph.nodes[1]?.parameters.strength).toBe(3)

    fireEvent.click(canvas.getByRole('button', { name: '返回当前编辑' }))
    expect(canvas.getByText('当前编辑')).toBeVisible()
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(4)
    expect(screen.getByRole('button', { name: '应用设置' })).toBeEnabled()
    expect(gateway.commands).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: '应用设置' }))
    await waitFor(() => expect(gateway.envelope.snapshot?.project.graph.nodes[1]?.parameters.strength).toBe(4))
    expect(gateway.commands.every((command) => command.operation === 'save_project')).toBe(true)
    expect(completedDetail.run.graph_snapshot.nodes[1]?.parameters.strength).toBe(3)
  })

  it.each(['parameters', 'definition'] as const)('未应用草稿不能导航到同名但 %s 不同的历史节点', async (difference) => {
    const detail = handoffDetailEnvelope()
    const changedDetail: RunDetailEnvelope = { ...detail, run: { ...detail.run,
      graph_snapshot: { ...detail.run.graph_snapshot, nodes: detail.run.graph_snapshot.nodes.map((node) => node.node_id !== 'transform' ? node : {
        ...node, ...(difference === 'parameters' ? { parameters: { ...node.parameters, strength: 9 } } : { definition_version: '0.2.9' }),
      }) },
      definitions_snapshot: detail.run.definitions_snapshot.map((definition) => difference === 'definition' && definition.type_id === transformDefinition.type_id ? { ...definition, version: '0.2.9' } : definition),
    } }
    const gateway = new RecordingGateway(handoffEnvelope(), { detail: () => changedDetail })
    render(<App gateway={gateway} />)
    fireEvent.click(await screen.findByLabelText('transform 节点'))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'strength' }), { target: { value: '4' } })
    fireEvent.click(screen.getByRole('button', { name: '处理外部文件' }))
    expect(within(screen.getByRole('region', { name: 'Studio Designer 画布' })).getByText('当前编辑')).toBeVisible()
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(4)
    expect(screen.getByRole('button', { name: '应用设置' })).toBeEnabled()
    expect(screen.getByLabelText('操作提示')).toHaveTextContent('请先应用或放弃')
    expect(gateway.commands).toHaveLength(0)
  })

  it('manual waiting 同节点草稿经主操作外部文件与只读设置返回编辑后仍可应用4', async () => {
    const gateway = new RecordingGateway(handoffEnvelope())
    render(<App gateway={gateway} />)
    fireEvent.click(await screen.findByLabelText('transform 节点'))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'strength' }), { target: { value: '4' } })
    fireEvent.click(screen.getByRole('button', { name: '处理外部文件' }))
    expect(screen.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('外部处理：test.transform')).toBeVisible()
    const canvas = within(screen.getByRole('region', { name: 'Studio Designer 画布' }))
    expect(canvas.getByText('本次处理的流程（只读）')).toBeVisible()
    openInspectorTab('设置')
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(4)
    expect(screen.getByRole('button', { name: '应用设置' })).toBeDisabled()
    expect(gateway.commands).toHaveLength(0)
    expect(gateway.readinessArguments.every(([runId, nodeRunId, probe]) => runId === handoffFixtureIds.run && nodeRunId === handoffFixtureIds.transformNodeRun && !probe)).toBe(true)

    fireEvent.click(canvas.getByRole('button', { name: '返回当前编辑' }))
    expect(canvas.getByText('当前编辑')).toBeVisible()
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(4)
    expect(screen.getByRole('button', { name: '应用设置' })).toBeEnabled()
    expect(gateway.commands).toHaveLength(0)
    expect(gateway.envelope.snapshot?.project.graph.nodes[1]?.parameters.strength).toBe(3)
    fireEvent.click(screen.getByRole('button', { name: '应用设置' }))
    await waitFor(() => expect(gateway.envelope.snapshot?.project.graph.nodes[1]?.parameters.strength).toBe(4))
    expect(gateway.commands.every((command) => command.operation === 'save_project')).toBe(true)
  })
})
