/** 新工作解释流程只通过显式用户决定与版本化服务；旧准备图单独保留。 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AvEnhanceV27Wizard, type AvEnhanceV27WizardProps } from './AvEnhanceV27Wizard'
import { SourcePreparationPanel } from './SourcePreparationPanel'
import { COLOR_PREPARED_VERSION, type ColorPreparedSourceChoice, type ColorPreparedSourceFullIntent, type ColorPreparedSourceProcessingRequest } from './prepared-color-contracts'
import { colorPreview, colorView } from './prepared-color.test-fixtures'
import { admissionRunId, diagnosisRunId, preparedPreview, preparedView } from './prepared-source.test-fixtures'
import type { PreparedSourceFullIntent, PreparedSourceProcessingRequest } from './prepared-source-contracts'
import type { ProjectSnapshotWire, RunSummaryWire } from './contracts'
import { recordedPreparationFormat } from './preparation-rerun'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
function props() {
  return {
    open: true, mode: 'create' as const, busy: false, currentSnapshot: null,
    currentProjectPath: '', currentProjectId: '', currentProjectName: '', runSummaries: [] as RunSummaryWire[],
    projectIdFactory: () => 'project.synthetic', pickerAvailable: true, onClose: vi.fn(),
    onPickProjectPath: vi.fn(async () => 'D:\\Synthetic\\new.zniku'), onPickSources: vi.fn(async () => ['D:\\Synthetic\\source.mkv']),
    onPreview: vi.fn(async () => null), onCreate: vi.fn(async () => false), onExpand: vi.fn(async () => false), onLocateNode: vi.fn(),
    onPreviewPublication: vi.fn<AvEnhanceV27WizardProps['onPreviewPublication']>(async () => ({ contract_version: '0.3.0', layout: 'title_subdirectory', resolved_output_root: 'D:\\Synthetic', output_directory: 'D:\\Synthetic\\Synthetic (2026)', will_create_directory: true })),
    onCreatePreparedSource: vi.fn(async () => true), onInspectPreparedSource: vi.fn(async () => preparedView()), onChoosePreparedSource: vi.fn(async () => true),
    onPreviewPreparedSourceProcessing: vi.fn(async (request: PreparedSourceProcessingRequest) => ({ ...request, status: 'pending_real_acceptance' as const })),
    onPreviewPreparedSource: vi.fn(async (request: PreparedSourceFullIntent) => ({ ...preparedPreview(), processing: request.processing })), onExpandPreparedSource: vi.fn(async () => true),
    onStartPreparationRun: vi.fn(async () => diagnosisRunId).mockResolvedValueOnce(diagnosisRunId).mockResolvedValue(admissionRunId),
    onCancelPreparationRun: vi.fn(async () => {}), onOpenExternalTasks: vi.fn(async () => {}), onCreateNewWorkSource: vi.fn(),
    preparedColor: {
      create: vi.fn(async () => true), choose: vi.fn(async (_choice: ColorPreparedSourceChoice) => true),
      inspect: vi.fn(async (runId: string) => runId === diagnosisRunId ? colorView() : colorView({ run_id: admissionRunId, state: 'ready', route: 'direct', reference_path: 'D:\\Synthetic\\source.mkv', admission_status: 'completed', interpretation_policy: 'operator_confirmed_bt709_limited_left', working_signal_basis: 'operator-confirmed-fixture' })),
      processing: vi.fn(async (request: ColorPreparedSourceProcessingRequest) => ({ ...request, status: 'pending_real_acceptance' as const })),
      preview: vi.fn(async (request: ColorPreparedSourceFullIntent) => ({ ...colorPreview(), processing: request.processing })), expand: vi.fn(async () => true),
    },
  }
}
async function reachCheck(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '选择视频素材' }))
  await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
  await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
  expect(screen.getByLabelText('工作流方案')).toHaveValue('prepared-color')
  await user.click(screen.getByRole('button', { name: '下一步：素材检查与准备' }))
  await user.click(screen.getByRole('button', { name: /开始检查素材/ }))
}
const operatorName = /我确认本工程采用 SDR BT.709/
const defaultName = /要求素材已明确声明/
const repairActions = [{ route: 'external' as const, label: '外部保内容修复', enabled: true, reason: '保留缺省声明，不等于工作源准入', strategy_id: null, estimated_additional_bytes: null }]
function existing(version = '0.3.4'): ProjectSnapshotWire {
  return { project: { project_id: 'project.synthetic', name: 'Synthetic', graph: { nodes: [{ node_id: 'source-preparation-source', type_id: 'zniku.source_preparation.source', definition_version: version, parameters: { source_path: 'D:\\Synthetic\\source.mkv' }, ui_position: { x: 0, y: 0 } }], edges: [] } }, definitions: [] }
}
function summary(): RunSummaryWire {
  return { run_id: diagnosisRunId, project_id: 'project.synthetic', target_mode: 'all', selected_targets: [], state: 'completed', node_count: 2,
    state_counts: { pending: 0, running: 0, waiting_external: 0, completed: 2, failed: 0 }, actionable: false, requires_operator_action: false,
    created_at: '2026-09-14T01:00:00Z', started_at: '2026-09-14T01:00:01Z', ended_at: '2026-09-14T01:00:03Z', latest_activity_at: '2026-09-14T01:00:03Z', error: null }
}
describe('新建工作解释向导', () => {
  it('默认不采用解释，主动选择后才准入与新版本预览/展开', async () => {
    const user = userEvent.setup(), callbacks = props()
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await reachCheck(user)
    const region = await screen.findByRole('region', { name: '本工程工作色彩解释' })
    expect(within(region).getByRole('radio', { name: defaultName })).toBeChecked()
    expect(region.closest('details')).toBeNull()
    expect(screen.getByRole('button', { name: '使用原件并检查准入' })).toBeDisabled()
    expect(callbacks.preparedColor.choose).not.toHaveBeenCalled()
    expect(callbacks.onCreatePreparedSource).not.toHaveBeenCalled()
    expect(callbacks.preparedColor.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ contract_version: COLOR_PREPARED_VERSION }))
    await user.click(screen.getByRole('radio', { name: operatorName }))
    expect(callbacks.preparedColor.choose).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '使用原件并检查准入' }))
    await screen.findByText('工作源已通过准入，可以继续设置')
    expect(callbacks.preparedColor.choose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ interpretation_policy: 'operator_confirmed_bt709_limited_left', run_id: diagnosisRunId }))
    expect(screen.getByText(/operator-confirmed-fixture/)).toBeVisible()
    expect(screen.getByRole('radio', { name: operatorName })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '下一步：处理与成片设置' }))
    await user.type(screen.getByLabelText('片名'), 'Synthetic')
    await user.type(screen.getByLabelText('年份'), '2026')
    await user.click(screen.getByRole('button', { name: '下一步：确认工作流' }))
    await screen.findByRole('region', { name: '确认重叠补帧工作流' })
    expect(callbacks.preparedColor.preview).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ contract_version: COLOR_PREPARED_VERSION, preparation_run_id: admissionRunId }))
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    expect(callbacks.preparedColor.expand).toHaveBeenCalledOnce()
    expect(callbacks.onExpandPreparedSource).not.toHaveBeenCalled()
  })
  it('完整明确声明的正常源仅自动direct一次，policy仍declared_only', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.preparedColor.inspect.mockImplementation(async (runId) => runId === diagnosisRunId ? colorView({ color_interpretation_required: false, findings: [] })
      : colorView({ run_id: admissionRunId, state: 'ready', reference_path: 'D:\\Synthetic\\source.mkv', admission_status: 'completed', color_interpretation_required: false, working_signal_basis: 'declared-fixture' }))
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await reachCheck(user)
    await screen.findByText('工作源已通过准入，可以继续设置')
    expect(callbacks.preparedColor.choose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'direct', interpretation_policy: 'declared_only' }))
    expect(callbacks.onStartPreparationRun).toHaveBeenCalledTimes(2)
  })
  it('默认缺声明仍可显式建立外部修复；不自动采用解释或提交产物', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.preparedColor.inspect.mockImplementation(async (runId) => runId === diagnosisRunId ? colorView({ available_actions: repairActions })
      : colorView({ run_id: admissionRunId, state: 'waiting_external', route: 'external', handoff: { run_id: admissionRunId, node_run_id: diagnosisRunId, node_id: 'source-preparation-prepare' } }))
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await reachCheck(user)
    const button = await screen.findByRole('button', { name: '建立外部保内容修复任务' })
    expect(button).toBeEnabled()
    expect(screen.getByText(/修复成功不代表可以进入增强/)).toBeVisible()
    await user.click(button)
    await user.click(await screen.findByRole('button', { name: '打开当前外部修复助手' }))
    expect(callbacks.preparedColor.choose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'external', interpretation_policy: 'declared_only' }))
    expect(callbacks.onOpenExternalTasks).toHaveBeenCalledExactlyOnceWith(admissionRunId)
    expect(callbacks.preparedColor.expand).not.toHaveBeenCalled()
  })
  it('服务判定冲突/解析不可用时不能勾选operator或绕过disabled路线', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.preparedColor.inspect.mockImplementation(async () => colorView({ color_interpretation_available: false, color_interpretation_required: false,
      color_interpretation_reason: '容器与参数集明确冲突，不能采用用户解释。', available_actions: colorView().available_actions.map((action) => ({ ...action, enabled: false })) }))
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await reachCheck(user)
    expect(await screen.findByRole('radio', { name: operatorName })).toBeDisabled()
    expect(screen.getByRole('button', { name: '使用原件并检查准入' })).toBeDisabled()
    expect(callbacks.preparedColor.choose).not.toHaveBeenCalled()
  })
  it('旧0.3.4工程即使服务提供新政策也只恢复旧wire，未迁移Graph', async () => {
    const user = userEvent.setup(), callbacks = props()
    render(<AvEnhanceV27Wizard {...callbacks} mode="resume" currentSnapshot={existing()} runSummaries={[summary()]} />)
    await user.click(screen.getByRole('button', { name: /查看检查与准备/ }))
    await screen.findByText('素材需要准备')
    expect(callbacks.onInspectPreparedSource).toHaveBeenCalledWith(diagnosisRunId)
    expect(callbacks.preparedColor.inspect).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: '本工程工作色彩解释' })).not.toBeInTheDocument()
    expect(callbacks.preparedColor.choose).not.toHaveBeenCalled()
  })
  it('新工程重开采用Graph记录的operator，仅展示不自动choose或Run', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.preparedColor.inspect.mockImplementation(async () => colorView({ interpretation_policy: 'operator_confirmed_bt709_limited_left' }))
    render(<AvEnhanceV27Wizard {...callbacks} mode="resume" currentSnapshot={existing(COLOR_PREPARED_VERSION)} runSummaries={[summary()]} />)
    await user.click(screen.getByRole('button', { name: /查看检查与准备/ }))
    expect(await screen.findByRole('radio', { name: operatorName })).toBeChecked()
    expect(callbacks.onInspectPreparedSource).not.toHaveBeenCalled()
    expect(callbacks.preparedColor.choose).not.toHaveBeenCalled()
    expect(callbacks.onStartPreparationRun).not.toHaveBeenCalled()
  })
  it('已完成外部准备的准入失败后，更新解释显式请求原Run准入重试而非run_all', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.preparedColor.inspect.mockImplementation(async () => colorView({ state: 'failed', route: 'external', admission_status: 'failed', available_actions: repairActions }))
    const snapshot = existing(COLOR_PREPARED_VERSION), node = snapshot.project.graph.nodes[0]!
    const preparedSnapshot = { ...snapshot, project: { ...snapshot.project, graph: { ...snapshot.project.graph,
      nodes: [...snapshot.project.graph.nodes, { ...node, node_id: 'custom-external', type_id: 'zniku.source_preparation.video_repair.external.mov' }] } } }
    render(<AvEnhanceV27Wizard {...callbacks} mode="resume" currentSnapshot={preparedSnapshot} runSummaries={[summary()]} />)
    await user.click(screen.getByRole('button', { name: /查看检查与准备/ }))
    await user.click(await screen.findByRole('radio', { name: operatorName }))
    await user.click(screen.getByRole('button', { name: '更新工作解释并重新检查准入' }))
    await waitFor(() => expect(callbacks.onStartPreparationRun).toHaveBeenCalledExactlyOnceWith({ run_id: diagnosisRunId }))
    expect(callbacks.preparedColor.choose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'external', external_format: 'mov', interpretation_policy: 'operator_confirmed_bt709_limited_left' }))
  })
  it('pre_chaptered仍使用旧流程，不因新能力已发布自动升级', async () => {
    const user = userEvent.setup(), callbacks = props()
    render(<AvEnhanceV27Wizard {...callbacks} />)
    fireEvent.change(screen.getByLabelText('素材组织方式'), { target: { value: 'pre_chaptered' } })
    await user.click(screen.getByRole('button', { name: '选择全部章节视频' }))
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByLabelText('工作流方案')).toHaveValue('av27')
    expect(callbacks.preparedColor.create).not.toHaveBeenCalled()
  })
  it('关闭后迟到的正常视图不触发自动准入', async () => {
    const user = userEvent.setup(), callbacks = props()
    let resolve!: (value: ReturnType<typeof colorView>) => void
    callbacks.preparedColor.inspect.mockImplementation(() => new Promise((done) => { resolve = done }))
    const mounted = render(<AvEnhanceV27Wizard {...callbacks} />)
    await reachCheck(user)
    await waitFor(() => expect(callbacks.preparedColor.inspect).toHaveBeenCalled())
    mounted.rerender(<AvEnhanceV27Wizard {...callbacks} open={false} />)
    resolve(colorView({ color_interpretation_required: false }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(callbacks.preparedColor.choose).not.toHaveBeenCalled()
  })
})
describe('工作解释草稿与已准入事实分离', () => {
  it('外部准备完成而准入失败后，按钮说明重新准入，仍发原路线和显式策略', async () => {
    const user = userEvent.setup(), callbacks = { disabled: false, onChoose: vi.fn(), onOpenExternal: vi.fn(), onNewSource: vi.fn(), onRetry: vi.fn() }
    render(<SourcePreparationPanel {...callbacks} recordedExternalFormat="mkv" view={colorView({ route: 'external', state: 'failed', admission_status: 'failed', available_actions: repairActions })} />)
    await user.click(screen.getByRole('radio', { name: operatorName }))
    await user.click(screen.getByRole('button', { name: '更新工作解释并重新检查准入' }))
    expect(callbacks.onChoose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'external', interpretation_policy: 'operator_confirmed_bt709_limited_left' }))
    expect(screen.queryByRole('button', { name: '建立外部保内容修复任务' })).not.toBeInTheDocument()
  })
  it.each(['mp4', 'mov'] as const)('面板重建从同一Graph恢复%s，不把已完成副本改成MKV', async (format) => {
    const user = userEvent.setup(), callbacks = { disabled: false, onChoose: vi.fn(), onOpenExternal: vi.fn(), onNewSource: vi.fn(), onRetry: vi.fn() }
    const snapshot = existing(COLOR_PREPARED_VERSION), node = snapshot.project.graph.nodes[0]!
    const preparedSnapshot = { ...snapshot, project: { ...snapshot.project, graph: { ...snapshot.project.graph,
      nodes: [...snapshot.project.graph.nodes, { ...node, node_id: 'renamed-repair', type_id: `zniku.source_preparation.video_repair.external.${format}` }] } } }
    const view = colorView({ route: 'external', state: 'failed', admission_status: 'failed', available_actions: [...colorView().available_actions, ...repairActions] })
    const initial = render(<SourcePreparationPanel {...callbacks} view={view} recordedExternalFormat={recordedPreparationFormat(preparedSnapshot, COLOR_PREPARED_VERSION)} />)
    initial.unmount()
    render(<SourcePreparationPanel {...callbacks} view={view} recordedExternalFormat={recordedPreparationFormat(preparedSnapshot, COLOR_PREPARED_VERSION)} />)
    expect(screen.getByLabelText('外部保内容修复格式')).toHaveValue(format)
    expect(screen.getByLabelText('外部保内容修复格式')).toBeDisabled()
    expect(screen.getByLabelText('修复目标精确帧率')).toBeDisabled()
    await user.click(screen.getByRole('radio', { name: operatorName }))
    await user.click(screen.getByRole('button', { name: '更新工作解释并重新检查准入' }))
    expect(callbacks.onChoose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'external', external_format: format, target_frame_rate: view.frame_rate }))
  })
  it('已完成external缺少Graph格式绑定时不使用MKV默认重试', async () => {
    const user = userEvent.setup(), onChoose = vi.fn()
    render(<SourcePreparationPanel disabled={false} onChoose={onChoose} onOpenExternal={vi.fn()} onNewSource={vi.fn()} onRetry={vi.fn()}
      view={colorView({ route: 'external', state: 'failed', admission_status: 'failed', available_actions: repairActions })} />)
    await user.click(screen.getByRole('radio', { name: operatorName }))
    expect(screen.getByRole('button', { name: '更新工作解释并重新检查准入' })).toBeDisabled()
    expect(onChoose).not.toHaveBeenCalled()
  })
  it('轮询发现operator不再可用时不发送过期选择，仍可显式退回declared_only做保内容准备', async () => {
    const user = userEvent.setup(), callbacks = { disabled: false, onChoose: vi.fn(), onOpenExternal: vi.fn(), onNewSource: vi.fn(), onRetry: vi.fn() }
    const view = colorView({ available_actions: repairActions })
    const mounted = render(<SourcePreparationPanel {...callbacks} view={view} />)
    await user.click(screen.getByRole('radio', { name: operatorName }))
    mounted.rerender(<SourcePreparationPanel {...callbacks} view={{ ...view, color_interpretation_available: false }} />)
    expect(screen.getByRole('button', { name: '建立外部保内容修复任务' })).toBeDisabled()
    await user.click(screen.getByRole('radio', { name: defaultName }))
    expect(screen.getByRole('button', { name: '建立外部保内容修复任务' })).toBeEnabled()
    expect(callbacks.onChoose).not.toHaveBeenCalled()
  })
  it('未保存的operator选择不会跨Run继承，失败回显的Graph policy才是记录', async () => {
    const user = userEvent.setup(), callbacks = { disabled: false, onChoose: vi.fn(), onOpenExternal: vi.fn(), onNewSource: vi.fn(), onRetry: vi.fn() }
    const mounted = render(<SourcePreparationPanel {...callbacks} view={colorView()} />)
    await user.click(screen.getByRole('radio', { name: operatorName }))
    expect(callbacks.onChoose).not.toHaveBeenCalled()
    mounted.rerender(<SourcePreparationPanel {...callbacks} view={colorView({ run_id: admissionRunId })} />)
    expect(screen.getByRole('radio', { name: defaultName })).toBeChecked()
    expect(screen.getByRole('button', { name: '使用原件并检查准入' })).toBeDisabled()
  })
})
