/** 普通工作源复用五步向导：不复制计划、不自动确认、不升级旧图。 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AvEnhanceV27Wizard, type AvEnhanceV27WizardProps } from './AvEnhanceV27Wizard'
import { WORK_SOURCE_VERSION, type WorkChoice, type WorkFullIntent, type WorkProcessingRequest } from './working-source-contracts'
import { workView, workPreview } from './working-source.test-fixtures'
import { colorView, colorPreview } from './prepared-color.test-fixtures'
import { COLOR_PREPARED_VERSION, type ColorPreparedSourceFullIntent, type ColorPreparedSourceProcessingRequest } from './prepared-color-contracts'
import { admissionRunId, diagnosisRunId } from './prepared-source.test-fixtures'
import type { ProjectSnapshotWire, RunSummaryWire } from './contracts'
afterEach(() => { cleanup(); vi.restoreAllMocks() })
function callbacks() {
  return {
    open: true, mode: 'create' as const, busy: false, currentSnapshot: null, currentProjectPath: '', currentProjectId: '', currentProjectName: '', runSummaries: [] as RunSummaryWire[],
    projectIdFactory: () => 'project.synthetic', pickerAvailable: true, onClose: vi.fn(),
    onPickProjectPath: vi.fn(async () => 'D:\\Synthetic\\new.zniku'), onPickSources: vi.fn(async () => ['D:\\Synthetic\\source.mkv']),
    onPreview: vi.fn(async () => null), onCreate: vi.fn(async () => false), onExpand: vi.fn(async () => false), onLocateNode: vi.fn(),
    onPreviewPublication: vi.fn<AvEnhanceV27WizardProps['onPreviewPublication']>(async () => ({ contract_version: '0.3.0', layout: 'title_subdirectory', resolved_output_root: 'D:\\Synthetic', output_directory: 'D:\\Synthetic\\Synthetic (2026)', will_create_directory: true })),
    onStartPreparationRun: vi.fn<NonNullable<AvEnhanceV27WizardProps['onStartPreparationRun']>>(async () => admissionRunId).mockResolvedValueOnce(diagnosisRunId),
    onCancelPreparationRun: vi.fn(async () => {}), onOpenExternalTasks: vi.fn(async () => {}),
    preparedColor: {
      create: vi.fn(async () => true), choose: vi.fn(async () => true), inspect: vi.fn(async () => colorView()),
      processing: vi.fn(async (request: ColorPreparedSourceProcessingRequest) => ({ ...request, status: 'pending_real_acceptance' as const })),
      preview: vi.fn(async (request: ColorPreparedSourceFullIntent) => ({ ...colorPreview(), processing: request.processing })), expand: vi.fn(async () => true),
    },
    workingSource: {
      create: vi.fn(async () => true), choose: vi.fn(async (_choice: WorkChoice) => true),
      inspect: vi.fn(async (runId: string) => runId === diagnosisRunId ? workView() : workView({ run_id: admissionRunId, state: 'ready', route: 'direct', reference_path: 'D:\\Synthetic\\source.mkv', admission_status: 'completed', interpretation_policy: 'operator_confirmed_bt709_limited_left', working_signal_basis: 'operator-confirmed-working-fixture', current_settings: { ...workView().current_settings, interpretation_policy: 'operator_confirmed_bt709_limited_left', confirmations: ['color_interpretation'] } })),
      processing: vi.fn(async (request: WorkProcessingRequest) => ({ ...request, status: 'pending_real_acceptance' as const })),
      preview: vi.fn(async (request: WorkFullIntent) => ({ ...workPreview(), processing: request.processing })), expand: vi.fn(async () => true),
    },
  }
}
async function reachProfile(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '选择视频素材' }))
  await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
  await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
}
async function reachCheck(user: ReturnType<typeof userEvent.setup>) {
  await reachProfile(user)
  expect(screen.getByLabelText('工作流方案')).toHaveValue('working-source')
  await user.click(screen.getByRole('button', { name: '下一步：素材检查与准备' }))
  await user.click(screen.getByRole('button', { name: /开始检查素材/ }))
}
function snapshot(version: string): ProjectSnapshotWire {
  return { project: { project_id: 'project.synthetic', name: 'Synthetic', graph: { nodes: [{ node_id: 'custom-source', type_id: 'zniku.source_preparation.source', definition_version: version, parameters: { source_path: 'D:\\Synthetic\\source.mkv' }, ui_position: { x: 0, y: 0 } }], edges: [] } }, definitions: [] }
}
const summary: RunSummaryWire = { run_id: diagnosisRunId, project_id: 'project.synthetic', target_mode: 'all', selected_targets: [], state: 'completed', node_count: 2,
  state_counts: { pending: 0, running: 0, waiting_external: 0, completed: 2, failed: 0 }, actionable: false, requires_operator_action: false,
  created_at: '2026-09-14T01:00:00Z', started_at: '2026-09-14T01:00:01Z', ended_at: '2026-09-14T01:00:03Z', latest_activity_at: '2026-09-14T01:00:03Z', error: null }
describe('普通工作源向导 default 与显式授权', () => {
  it('默认普通路线；缺色彩确认后使用新wire准备、预览和展开同一Graph', async () => {
    const user = userEvent.setup(), props = callbacks()
    render(<AvEnhanceV27Wizard {...props} />)
    await reachCheck(user)
    await screen.findByText('可直接处理')
    expect(props.workingSource.choose).not.toHaveBeenCalled()
    expect(props.preparedColor.create).not.toHaveBeenCalled()
    expect(props.workingSource.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ contract_version: WORK_SOURCE_VERSION }))
    await user.click(screen.getByRole('radio', { name: /我确认本工程采用 SDR BT.709/ }))
    await user.click(screen.getByRole('button', { name: '确认并使用原件' }))
    await screen.findByText('工作素材已准备好，可以继续')
    expect(props.workingSource.choose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'direct', confirmations: ['color_interpretation'] }))
    expect(screen.getByText(/operator-confirmed-working-fixture/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: '下一步：处理与成片设置' }))
    expect(screen.getByText(/成片使用 HEVC 10-bit 编码/)).toHaveTextContent('工作源绑定的音轨')
    await user.type(screen.getByLabelText('片名'), 'Synthetic')
    await user.type(screen.getByLabelText('年份'), '2026')
    await user.click(screen.getByRole('button', { name: '下一步：确认工作流' }))
    const preview = await screen.findByRole('region', { name: '确认重叠补帧工作流' })
    expect(preview).toHaveTextContent('工作源绑定音轨')
    expect(preview).not.toHaveTextContent('原音轨')
    expect(props.workingSource.processing).toHaveBeenCalledWith(expect.objectContaining({ contract_version: WORK_SOURCE_VERSION }))
    expect(props.workingSource.preview).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ contract_version: WORK_SOURCE_VERSION, preparation_run_id: admissionRunId }))
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    expect(props.workingSource.expand).toHaveBeenCalledOnce()
    expect(props.preparedColor.expand).not.toHaveBeenCalled()
  })
  it('正常源后端无需确认时只自动direct一次，不自动采用解释', async () => {
    const user = userEvent.setup(), props = callbacks()
    props.workingSource.inspect.mockImplementation(async (runId) => workView({ run_id: runId, color_interpretation_required: false, required_confirmations: [], findings: [],
      ...(runId === admissionRunId ? { state: 'ready', reference_path: 'D:\\Synthetic\\source.mkv', admission_status: 'completed' } : {}) }))
    render(<AvEnhanceV27Wizard {...props} />)
    await reachCheck(user)
    await screen.findByText('工作素材已准备好，可以继续')
    expect(props.workingSource.choose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'direct', interpretation_policy: 'declared_only', confirmations: [] }))
  })
  it('旧严格路线是显式高级选择，不是跳过检查或自动升级', async () => {
    const user = userEvent.setup(), props = callbacks()
    render(<AvEnhanceV27Wizard {...props} />)
    await reachProfile(user)
    const selector = screen.getByLabelText('工作流方案')
    expect(selector.closest('details')).not.toHaveAttribute('open')
    await user.click(screen.getByText('高级 · 旧严格准备路线与兼容流程'))
    await user.selectOptions(selector, 'prepared-color')
    await user.click(screen.getByRole('button', { name: '下一步：素材检查与准备' }))
    await user.click(screen.getByRole('button', { name: /开始检查素材/ }))
    await screen.findByText('素材需要准备')
    expect(props.preparedColor.create).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ contract_version: COLOR_PREPARED_VERSION }))
    expect(props.workingSource.create).not.toHaveBeenCalled()
  })
  it('旧color工程重开保持旧wire与设置，普通新能力不接管', async () => {
    const user = userEvent.setup(), props = callbacks()
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot(COLOR_PREPARED_VERSION)} runSummaries={[summary]} />)
    await user.click(screen.getByRole('button', { name: /查看检查与准备/ }))
    await screen.findByText('素材需要准备')
    expect(props.preparedColor.inspect).toHaveBeenCalledWith(diagnosisRunId)
    expect(props.workingSource.inspect).not.toHaveBeenCalled()
    expect(props.workingSource.choose).not.toHaveBeenCalled()
  })
  it('外部候选失败后发送backend实际retry_target，保持MOV和已完成任务', async () => {
    const user = userEvent.setup(), props = callbacks(), target = { run_id: diagnosisRunId, node_id: 'actual-admission', node_run_id: admissionRunId }
    props.workingSource.inspect.mockImplementation(async () => workView({ state: 'failed', route: 'external', admission_status: 'failed', retry_target: target,
      available_actions: [{ route: 'external', label: '外部新参考', reason: '候选已完成', enabled: true, strategy_id: null, estimated_additional_bytes: null }],
      current_settings: { route: 'external', target_frame_rate: '30/1', external_format: 'mov', interpretation_policy: 'declared_only', confirmations: ['external_reference'], audio_source: 'reference' },
      required_confirmations: [{ id: 'color_interpretation', routes: ['external'], label: '确认候选解释', description: '新参考文件未声明' }] }))
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot(WORK_SOURCE_VERSION)} runSummaries={[summary]} />)
    await user.click(screen.getByRole('button', { name: /查看检查与准备/ }))
    await user.click(await screen.findByRole('radio', { name: /我确认本工程采用 SDR BT.709/ }))
    expect(screen.getByText(/以下解释针对本次提交的新参考文件/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: '结束失败批次并重新检查' }))
    await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledExactlyOnceWith({ ...target, contract_version: WORK_SOURCE_VERSION }))
    expect(props.workingSource.choose).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ external_format: 'mov', target_frame_rate: '30/1', interpretation_policy: 'operator_confirmed_bt709_limited_left' }))
    expect(props.onOpenExternalTasks).not.toHaveBeenCalled()
  })
  it('pre_chaptered保持旧处理方案，不隐藏升级到普通单一源', async () => {
    const user = userEvent.setup(), props = callbacks()
    render(<AvEnhanceV27Wizard {...props} />)
    fireEvent.change(screen.getByLabelText('素材组织方式'), { target: { value: 'pre_chaptered' } })
    await user.click(screen.getByRole('button', { name: '选择全部章节视频' }))
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByLabelText('工作流方案')).toHaveValue('av27')
    expect(props.workingSource.create).not.toHaveBeenCalled()
  })
  it('普通检查失败直接使用实际retry_target重试，不修改路线或逼用户操作节点诊断', async () => {
    const user = userEvent.setup(), props = callbacks(), target = { run_id: diagnosisRunId, node_id: 'actual-diagnostics', node_run_id: admissionRunId }
    props.workingSource.inspect.mockImplementation(async () => workView({ state: 'failed', diagnosis_status: 'failed', decision: null, inspection_scope: null,
      available_actions: [], retry_target: target, color_interpretation_required: false, required_confirmations: [] }))
    render(<AvEnhanceV27Wizard {...props} mode="resume" currentSnapshot={snapshot(WORK_SOURCE_VERSION)} runSummaries={[summary]} />)
    await user.click(screen.getByRole('button', { name: /查看检查与准备/ }))
    await user.click(await screen.findByRole('button', { name: '结束失败批次并重新检查' }))
    await waitFor(() => expect(props.onStartPreparationRun).toHaveBeenCalledExactlyOnceWith({ ...target, contract_version: WORK_SOURCE_VERSION }))
    expect(props.workingSource.choose).not.toHaveBeenCalled()
    expect(props.preparedColor.choose).not.toHaveBeenCalled()
  })
  it('关闭后的迟到direct不触发选择或Run', async () => {
    const user = userEvent.setup(), props = callbacks()
    let resolve!: (value: ReturnType<typeof workView>) => void
    props.workingSource.inspect.mockImplementation(() => new Promise((done) => { resolve = done }))
    const mounted = render(<AvEnhanceV27Wizard {...props} />)
    await reachCheck(user)
    await waitFor(() => expect(props.workingSource.inspect).toHaveBeenCalled())
    mounted.rerender(<AvEnhanceV27Wizard {...props} open={false} />)
    resolve(workView({ color_interpretation_required: false, required_confirmations: [] }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(props.workingSource.choose).not.toHaveBeenCalled()
    expect(props.onStartPreparationRun).toHaveBeenCalledOnce()
  })
})
