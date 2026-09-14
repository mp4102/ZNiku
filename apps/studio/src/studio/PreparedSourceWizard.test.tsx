/** 新素材检查先行路径门禁；旧向导由原测试集独立保持。 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AvEnhanceV27Wizard, type AvEnhanceV27WizardProps } from './AvEnhanceV27Wizard'
import type { PreparedSourceFullIntent, PreparedSourceProcessingRequest } from './prepared-source-contracts'
import { admissionRunId, diagnosisRunId, preparedPreview, preparedView } from './prepared-source.test-fixtures'
import type { ProjectSnapshotWire, RunSummaryWire } from './contracts'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
function props() {
  return {
    open: true, mode: 'create' as const, busy: false, currentSnapshot: null,
    currentProjectPath: '', currentProjectId: '', currentProjectName: '', runSummaries: [] as RunSummaryWire[],
    projectIdFactory: () => 'project.synthetic', pickerAvailable: true, onClose: vi.fn(),
    onPickProjectPath: vi.fn(async () => 'D:\\Synthetic\\new.zniku'), onPickSources: vi.fn(async () => ['D:\\Synthetic\\source.mkv']),
    onPreview: vi.fn(async () => null), onCreate: vi.fn(async () => false), onExpand: vi.fn(async () => false), onLocateNode: vi.fn(),
    onPreviewPublication: vi.fn<AvEnhanceV27WizardProps['onPreviewPublication']>(async () => ({ contract_version: '0.3.0', layout: 'title_subdirectory', resolved_output_root: 'D:\\Synthetic', output_directory: 'D:\\Synthetic\\Synthetic (2026)', will_create_directory: true })),
    onCreatePreparedSource: vi.fn(async () => true),
    onStartPreparationRun: vi.fn(async () => diagnosisRunId).mockResolvedValueOnce(diagnosisRunId).mockResolvedValue(admissionRunId),
    onInspectPreparedSource: vi.fn(async (runId: string) => runId === diagnosisRunId ? preparedView() : preparedView({ run_id: admissionRunId, state: 'ready', route: 'direct', reference_path: 'D:\\Synthetic\\source.mkv', admission_status: 'completed' })),
    onChoosePreparedSource: vi.fn(async () => true),
    onPreviewPreparedSourceProcessing: vi.fn(async (request: PreparedSourceProcessingRequest) => ({ ...request, status: 'pending_real_acceptance' as const })),
    onPreviewPreparedSource: vi.fn(async (request: PreparedSourceFullIntent) => ({ ...preparedPreview(), preparation_run_id: request.preparation_run_id, processing: request.processing })),
    onExpandPreparedSource: vi.fn(async () => true), onCancelPreparationRun: vi.fn(async () => {}),
    onOpenExternalTasks: vi.fn(async () => {}), onOpenAnalysisProblems: vi.fn(async () => {}), onCreateNewWorkSource: vi.fn(),
  }
}
async function reachCheck(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '选择视频素材' }))
  await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
  await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
  expect(screen.getByLabelText('工作流方案')).toHaveValue('prepared-source')
  await user.click(screen.getByRole('button', { name: '下一步：素材检查与准备' }))
}
async function startCheck(user: ReturnType<typeof userEvent.setup>) {
  await reachCheck(user)
  await user.click(screen.getByRole('button', { name: /开始检查素材/ }))
}
const repairActions = [{ route: 'builtin' as const, label: '内置工作副本', enabled: false, reason: '尚未启用', strategy_id: 't1', estimated_additional_bytes: null },
  { route: 'external' as const, label: '外部保内容修复', enabled: true, reason: '保留完整内容', strategy_id: null, estimated_additional_bytes: null }]
function snapshot(expanded = false): ProjectSnapshotWire {
  const nodes = [{ node_id: 'source-preparation-source', type_id: 'zniku.source_preparation.source', definition_version: '0.3.4', parameters: { source_path: 'D:\\Synthetic\\source.mkv' }, ui_position: { x: 0, y: 0 } }]
  return { project: { project_id: 'project.synthetic', name: 'Synthetic', graph: { nodes: expanded ? [...nodes, { ...nodes[0]!, node_id: 'split', type_id: 'zniku.prepared.overlap.split.leaves.1' }] : nodes, edges: [] } }, definitions: [] }
}
function summary(): RunSummaryWire {
  return { run_id: diagnosisRunId, project_id: 'project.synthetic', target_mode: 'all', selected_targets: [], state: 'completed', node_count: 2,
    state_counts: { pending: 0, running: 0, waiting_external: 0, completed: 2, failed: 0 }, actionable: false, requires_operator_action: false,
    created_at: '2026-09-14T01:00:00Z', started_at: '2026-09-14T01:00:01Z', ended_at: '2026-09-14T01:00:03Z', latest_activity_at: '2026-09-14T01:00:03Z', error: null }
}

describe('0.3.4 工作源准入前置向导', () => {
  it('四个基本输入后先检查，未填写片名/年份也能创建；正常源只自动选择一次direct准入', async () => {
    const user = userEvent.setup(), callbacks = props()
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await reachCheck(user)
    expect(callbacks.onCreatePreparedSource).not.toHaveBeenCalled()
    expect(screen.queryByLabelText('片名')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一步：处理与成片设置' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /开始检查素材/ }))
    await screen.findByText('工作源已通过准入，可以继续设置')
    expect(callbacks.onCreatePreparedSource).toHaveBeenCalledExactlyOnceWith({ contract_version: '0.3.4', project_path: 'D:\\Synthetic\\new.zniku', project_id: 'project.synthetic', project_name: '未命名视频工程', source_path: 'D:\\Synthetic\\source.mkv' })
    expect(callbacks.onChoosePreparedSource).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'direct', run_id: diagnosisRunId }))
    expect(callbacks.onStartPreparationRun).toHaveBeenCalledTimes(2)
    expect(callbacks.onCreate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '下一步：处理与成片设置' }))
    expect(screen.getByLabelText('平均章数')).toHaveValue('1')
    expect(screen.getByLabelText('每段最长时长（分叶）')).toHaveValue('5')
    await user.type(screen.getByLabelText('片名'), 'Synthetic')
    await user.type(screen.getByLabelText('年份'), '2026')
    await user.click(screen.getByRole('button', { name: '下一步：确认工作流' }))
    await screen.findByRole('region', { name: '确认重叠补帧工作流' })
    expect(callbacks.onPreviewPreparedSource).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ contract_version: '0.3.4', preparation_run_id: admissionRunId }))
    expect(callbacks.onExpandPreparedSource).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    expect(callbacks.onExpandPreparedSource).toHaveBeenCalledOnce()
  })
  it('报告完成但准入未完成仍不可进入设置，禁用内置策略不自动运行', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.onInspectPreparedSource.mockImplementation(async () => preparedView({ available_actions: repairActions }))
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await startCheck(user)
    await screen.findByText('素材需要准备')
    expect(screen.getByRole('button', { name: '下一步：处理与成片设置' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '查看处理与成片设置' })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /内置工作副本/ })).toBeDisabled()
    expect(callbacks.onChoosePreparedSource).not.toHaveBeenCalled()
    expect(callbacks.onStartPreparationRun).toHaveBeenCalledTimes(1)
  })
  it('显式外部路线创建图并普通Run；助手只打开当前等待，不自动Submit', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.onInspectPreparedSource.mockImplementation(async (runId) => runId === diagnosisRunId ? preparedView({ available_actions: repairActions })
      : preparedView({ run_id: admissionRunId, state: 'waiting_external', route: 'external', handoff: { run_id: admissionRunId, node_run_id: diagnosisRunId, node_id: 'source-preparation-prepare' } }))
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await startCheck(user)
    await user.click(await screen.findByRole('button', { name: '建立外部保内容修复任务' }))
    await user.click(await screen.findByRole('button', { name: '打开当前外部修复助手' }))
    expect(callbacks.onChoosePreparedSource).toHaveBeenCalledExactlyOnceWith({ run_id: diagnosisRunId, route: 'external', target_frame_rate: '30000/1001', external_format: 'mkv' })
    expect(callbacks.onOpenExternalTasks).toHaveBeenCalledExactlyOnceWith(admissionRunId)
    expect(callbacks.onExpandPreparedSource).not.toHaveBeenCalled()
  })
  it('direct要求选率的报告不会自动choose，用户确认后才运行', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.onInspectPreparedSource.mockImplementation(async (runId) => runId === diagnosisRunId ? preparedView({ frame_rate: null, frame_rate_choices: ['30/1'] })
      : preparedView({ run_id: admissionRunId, route: 'direct', state: 'preparing' }))
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await startCheck(user)
    await screen.findByText('素材需要准备')
    expect(callbacks.onChoosePreparedSource).not.toHaveBeenCalled()
    const action = screen.getByRole('button', { name: '使用原件并检查准入' })
    expect(action).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('修复目标精确帧率'), '30/1')
    await user.click(action)
    await screen.findByText('正在准备工作参考')
    expect(callbacks.onChoosePreparedSource).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ route: 'direct', target_frame_rate: '30/1' }))
  })
  it('后台检查允许返回和关闭；停止请求不冒充完成状态', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.onInspectPreparedSource.mockImplementation(async () => preparedView({ state: 'checking', diagnosis_status: 'pending', available_actions: [], stage_progress: { stage: '完整解码', current: 10, total: 120, unit: 'frames', elapsed_seconds: 1, rate_per_second: 10 } }))
    const view = render(<AvEnhanceV27Wizard {...callbacks} />)
    await startCheck(user)
    await screen.findByText('正在检查素材')
    view.rerender(<AvEnhanceV27Wizard {...callbacks} busy />)
    await user.click(screen.getByRole('button', { name: '停止当前检查或验证' }))
    expect(callbacks.onCancelPreparationRun).toHaveBeenCalledExactlyOnceWith(diagnosisRunId)
    expect(screen.getByText('正在检查素材')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '上一步' }))
    expect(screen.getByLabelText('工作流方案')).toBeDisabled()
    expect(callbacks.onChoosePreparedSource).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '关闭模板向导' }))
    expect(callbacks.onClose).toHaveBeenCalledOnce()
  })
  it('返回之前页不更改准备图或重跑，回来重新只读状态', async () => {
    const user = userEvent.setup(), callbacks = props()
    callbacks.onInspectPreparedSource.mockImplementation(async () => preparedView({ available_actions: repairActions }))
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await startCheck(user)
    await screen.findByText('素材需要准备')
    await user.click(screen.getByRole('button', { name: '查看选择素材' }))
    expect(screen.getByRole('button', { name: '选择视频素材' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    await user.click(screen.getByRole('button', { name: '下一步：素材检查与准备' }))
    expect(callbacks.onCreatePreparedSource).toHaveBeenCalledTimes(1)
    expect(callbacks.onChoosePreparedSource).not.toHaveBeenCalled()
    expect(callbacks.onStartPreparationRun).toHaveBeenCalledTimes(1)
  })
  it('作为新工作源只重置新草稿，不继承当前分析或外部结果', async () => {
    const user = userEvent.setup(), callbacks = props()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    callbacks.onInspectPreparedSource.mockImplementation(async () => preparedView({ available_actions: repairActions }))
    render(<AvEnhanceV27Wizard {...callbacks} />)
    await startCheck(user)
    await user.click(await screen.findByRole('button', { name: '作为新工作源新建工程' }))
    expect(callbacks.onCreateNewWorkSource).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '选择视频素材' })).toBeEnabled()
    expect(callbacks.onCreatePreparedSource).toHaveBeenCalledTimes(1)
    expect(callbacks.onExpandPreparedSource).not.toHaveBeenCalled()
  })
  it('恢复准备工程必须显式选择记录，只读needs_choice不自动direct', async () => {
    const user = userEvent.setup(), callbacks = props()
    render(<AvEnhanceV27Wizard {...callbacks} mode="resume" currentSnapshot={snapshot()} runSummaries={[summary()]} />)
    expect(screen.getByRole('region', { name: '选择素材准备记录' })).toBeVisible()
    expect(callbacks.onInspectPreparedSource).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /查看检查与准备/ }))
    await screen.findByText('素材需要准备')
    expect(callbacks.onChoosePreparedSource).not.toHaveBeenCalled()
    expect(callbacks.onStartPreparationRun).not.toHaveBeenCalled()
  })
  it('已展开新图只显示保留图提示，不能用旧准备记录再展开覆盖编辑', () => {
    const callbacks = props()
    render(<AvEnhanceV27Wizard {...callbacks} mode="resume" currentSnapshot={snapshot(true)} runSummaries={[summary()]} />)
    expect(screen.queryByRole('button', { name: /开始检查素材/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '确认并创建工作流' })).not.toBeInTheDocument()
    expect(callbacks.onInspectPreparedSource).not.toHaveBeenCalled()
    expect(callbacks.onExpandPreparedSource).not.toHaveBeenCalled()
  })
  it('旧pre_chaptered不升级新profile，新建字段保留原四项', async () => {
    const user = userEvent.setup(), callbacks = props()
    render(<AvEnhanceV27Wizard {...callbacks} />)
    const source = screen.getByRole('region', { name: '选择素材' })
    expect(within(source).queryByLabelText('片名')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('素材组织方式'), { target: { value: 'pre_chaptered' } })
    await user.click(screen.getByRole('button', { name: '选择全部章节视频' }))
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByLabelText('工作流方案')).toHaveValue('av27')
    expect(screen.getByRole('button', { name: '下一步：处理与成片设置' })).toBeVisible()
  })
  it('迟到的view在关闭后不自动发起direct或写图', async () => {
    const user = userEvent.setup(), callbacks = props()
    let resolveView!: (value: ReturnType<typeof preparedView>) => void
    callbacks.onInspectPreparedSource.mockImplementation(() => new Promise((resolve) => { resolveView = resolve }))
    const view = render(<AvEnhanceV27Wizard {...callbacks} />)
    await startCheck(user)
    await waitFor(() => expect(callbacks.onInspectPreparedSource).toHaveBeenCalled())
    view.rerender(<AvEnhanceV27Wizard {...callbacks} open={false} />)
    resolveView(preparedView())
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(callbacks.onChoosePreparedSource).not.toHaveBeenCalled()
  })
})
