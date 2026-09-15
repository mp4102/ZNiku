/** 0.3.5 新建和候选交互专项；只使用内存 fixture，不访问服务或媒体。 */
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AvEnhanceV27Wizard, type AvEnhanceV27WizardProps } from './AvEnhanceV27Wizard'
import example from './__fixtures__/source-admitted-preview.json'
import { parseSourceAdmittedFullEnvelope, type SourceAdmittedFullIntent, type SourceAdmittedProcessingRequest } from './source-admitted-contracts'
import type { RunSummaryWire } from './contracts'

afterEach(cleanup)
const runId = '00000000-0000-4000-8000-000000000035'
const nextRunId = '00000000-0000-4000-8000-000000000036'
function run(state: 'running' | 'failed'): RunSummaryWire {
  return { run_id: runId, project_id: 'synthetic.admitted', target_mode: 'all', selected_targets: [], state,
    node_count: 2, state_counts: { pending: 1, running: state === 'running' ? 1 : 0,
      waiting_external: 0, completed: 0, failed: state === 'failed' ? 1 : 0 }, actionable: false,
    requires_operator_action: state === 'failed', created_at: '2026-09-15T00:00:00Z',
    started_at: '2026-09-15T00:00:01Z', ended_at: state === 'failed' ? '2026-09-15T00:00:02Z' : null,
    latest_activity_at: '2026-09-15T00:00:02Z', error: state === 'failed' ? { reason: 'execution_error', message: 'synthetic source rejected' } : null }
}
function props() {
  return {
    open: true, mode: 'create' as const, busy: false, currentSnapshot: null, connectionEpoch: 0,
    currentProjectPath: '', currentProjectId: '', currentProjectName: '', runSummaries: [] as RunSummaryWire[],
    projectIdFactory: () => 'synthetic.admitted', pickerAvailable: true,
    onPickSources: vi.fn<(_multiple: boolean) => Promise<ReadonlyArray<string> | null>>(async () => ['C:\\synthetic\\source.mp4']),
    onPickProjectPath: vi.fn(async () => 'C:\\synthetic\\project.zniku'), onClose: vi.fn(),
    onPreview: vi.fn(async () => null), onCreate: vi.fn(async () => true), onExpand: vi.fn(async () => true), onLocateNode: vi.fn(),
    onPreviewPublication: vi.fn<AvEnhanceV27WizardProps['onPreviewPublication']>(async (request) => ({
      contract_version: '0.3.0', layout: request.request.layout ?? 'title_subdirectory',
      resolved_output_root: 'C:\\synthetic', output_directory: 'C:\\synthetic\\Synthetic (2026)', will_create_directory: true,
    })),
    onCreateSourceAdmitted: vi.fn(async () => true), onReplaceSourceAdmitted: vi.fn(async (_path: string) => true),
    onCancelSourceAdmitted: vi.fn(async (_run: string) => {}), onStartPreparationRun: vi.fn(async () => runId),
    onPreviewSourceAdmittedProcessing: vi.fn(async (request: SourceAdmittedProcessingRequest) => ({ ...request, status: 'pending_real_acceptance' as const })),
    onPreviewSourceAdmitted: vi.fn(async (request: SourceAdmittedFullIntent) => parseSourceAdmittedFullEnvelope({
      ...example, contract_version: '0.3.5', profile_version: '0.3.5', profile_id: 'zniku.source-admitted-overlap',
      preparation_run_id: request.preparation_run_id, processing: request.processing, warnings: [],
    })),
    onExpandSourceAdmitted: vi.fn(async () => true),
  }
}
async function reachAnalysis(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole('button', { name: '选择视频素材' }))
  await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
  await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
  expect(screen.getByLabelText('工作流方案')).toHaveValue('source-admitted')
  await user.click(screen.getByRole('button', { name: '下一步：处理与成片设置' }))
  await user.type(screen.getByLabelText('片名'), 'Synthetic')
  await user.type(screen.getByLabelText('年份'), '2026')
  await user.click(screen.getByRole('button', { name: '下一步：分析' }))
}
async function begin(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await reachAnalysis(user)
  await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
}

describe('0.3.5 单一准入创作者交互', () => {
  it('取消后的非终态 Run 可在向导从头重分析，不被解释为需要修复原片', async () => {
    const user = userEvent.setup(), value = props()
    const cancelled = { ...run('running'), state_counts: { pending: 1, running: 0, waiting_external: 0, completed: 0, failed: 1 },
      requires_operator_action: true, error: { reason: 'cancelled' as const, message: '操作者取消分析' } }
    render(<AvEnhanceV27Wizard {...value} runSummaries={[cancelled]} />)
    await begin(user)
    expect(await screen.findByText('素材分析已取消')).toBeVisible()
    expect(screen.queryByRole('region', { name: '外部修复候选' })).not.toBeInTheDocument()
    value.onStartPreparationRun.mockResolvedValueOnce(nextRunId)
    await user.click(screen.getByRole('button', { name: '重新分析' }))
    await waitFor(() => expect(value.onStartPreparationRun).toHaveBeenCalledTimes(2))
    expect(value.onReplaceSourceAdmitted).not.toHaveBeenCalled()
  })
  it('新能力存在也不把恢复中的旧 Source 工程升级为新准入', async () => {
    const user = userEvent.setup(), value = props()
    render(<AvEnhanceV27Wizard {...value} mode="resume" currentProjectPath="C:\\synthetic\\old.zniku"
      currentSnapshot={{ project: { project_id: 'synthetic.old', name: 'Old Project', graph: {
        nodes: [{ node_id: 'source', type_id: 'zniku.avenhance.v27.source_program', definition_version: '0.2.1',
          parameters: { source_path: 'C:\\synthetic\\old.mp4', source_ordinal: 0 }, ui_position: { x: 0, y: 0 } },
        { node_id: 'admission', type_id: 'zniku.avenhance.v27.source_admission', definition_version: '0.2.1',
          parameters: { source_mode: 'program' }, ui_position: { x: 200, y: 0 } }], edges: [],
      } }, definitions: [] }} />)
    await user.click(screen.getByRole('button', { name: '上一步' }))
    expect(screen.getByLabelText('工作流方案')).toHaveValue('av27')
    expect(value.onCreateSourceAdmitted).not.toHaveBeenCalled()
    expect(value.onReplaceSourceAdmitted).not.toHaveBeenCalled()
  })
  it('新建默认新版本，MR默认关；点击分析前不创建、不调用旧预览兜底', async () => {
    const user = userEvent.setup(), value = props()
    render(<AvEnhanceV27Wizard {...value} runSummaries={[run('running')]} />)
    await reachAnalysis(user)
    expect(value.onCreateSourceAdmitted).not.toHaveBeenCalled()
    expect(value.onPreviewSourceAdmittedProcessing).toHaveBeenCalledWith(expect.objectContaining({
      contract_version: '0.3.5', processing: expect.objectContaining({ mr: { mode: 'off' },
        settings: expect.objectContaining({ leaf_max_minutes: 5 }) }),
    }))
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(value.onCreateSourceAdmitted).toHaveBeenCalledOnce())
    expect(value.onCreateSourceAdmitted).toHaveBeenCalledWith(expect.objectContaining({ contract_version: '0.3.5',
      request: expect.objectContaining({ mr: { mode: 'off' } }) }))
    expect(value.onCreate).not.toHaveBeenCalled()
    expect(value.onPreview).not.toHaveBeenCalled()
  })
  it('只显示当前分析身份的真实进度，取消请求不伪造已停止', async () => {
    const user = userEvent.setup(), value = props()
    const view = render(<AvEnhanceV27Wizard {...value} runSummaries={[run('running')]} analysisProgress={{ run_id: runId, fraction: 0.35 }} />)
    await begin(user)
    expect(await screen.findByRole('progressbar', { name: '源媒体分析进度' })).toHaveAttribute('value', '0.35')
    await user.click(screen.getByRole('button', { name: '取消分析' }))
    expect(value.onCancelSourceAdmitted).toHaveBeenCalledExactlyOnceWith(runId)
    expect(screen.getByRole('progressbar', { name: '源媒体分析进度' })).toBeVisible()
    view.rerender(<AvEnhanceV27Wizard {...value} runSummaries={[run('running')]} analysisProgress={{ run_id: nextRunId, fraction: 0.99 }} />)
    expect(screen.getByRole('progressbar', { name: '源媒体分析进度' })).not.toHaveAttribute('value')
  })
  it('候选选择不自动提交，明确确认后才替换引用并创建新分析', async () => {
    const user = userEvent.setup(), value = props()
    render(<AvEnhanceV27Wizard {...value} runSummaries={[run('failed')]} />)
    await begin(user)
    value.onPickSources.mockResolvedValueOnce(['C:\\synthetic\\repaired.mp4'])
    await user.click(await screen.findByRole('button', { name: '选择修复后的视频' }))
    expect(screen.getByRole('group', { name: '确认新的处理参考' })).toHaveTextContent('不会复制、覆盖或删除原片')
    expect(value.onReplaceSourceAdmitted).not.toHaveBeenCalled()
    expect(value.onStartPreparationRun).toHaveBeenCalledOnce()
    value.onStartPreparationRun.mockResolvedValueOnce(nextRunId)
    await user.click(screen.getByRole('button', { name: '确认选用并重新分析' }))
    expect(value.onReplaceSourceAdmitted).toHaveBeenCalledExactlyOnceWith('C:\\synthetic\\repaired.mp4')
    await waitFor(() => expect(value.onStartPreparationRun).toHaveBeenCalledTimes(2))
    expect(value.onExpandSourceAdmitted).not.toHaveBeenCalled()
  })
  it('取消原生选择或候选确认均不改变引用、不重新分析', async () => {
    const user = userEvent.setup(), value = props()
    render(<AvEnhanceV27Wizard {...value} runSummaries={[run('failed')]} />)
    await begin(user)
    value.onPickSources.mockResolvedValueOnce(null)
    await user.click(await screen.findByRole('button', { name: '选择修复后的视频' }))
    expect(screen.queryByRole('group', { name: '确认新的处理参考' })).not.toBeInTheDocument()
    value.onPickSources.mockResolvedValueOnce(['C:\\synthetic\\candidate.mov'])
    await user.click(screen.getByRole('button', { name: '选择修复后的视频' }))
    await user.click(screen.getByRole('button', { name: '取消选择' }))
    expect(screen.queryByRole('group', { name: '确认新的处理参考' })).not.toBeInTheDocument()
    expect(value.onReplaceSourceAdmitted).not.toHaveBeenCalled()
    expect(value.onStartPreparationRun).toHaveBeenCalledOnce()
  })
  it('断线后迟到的选择结果失效，不能带回候选确认权限', async () => {
    const user = userEvent.setup(), value = props()
    const view = render(<AvEnhanceV27Wizard {...value} runSummaries={[run('failed')]} />)
    await begin(user)
    let resolve!: (paths: ReadonlyArray<string>) => void
    value.onPickSources.mockReturnValueOnce(new Promise((done) => { resolve = done }))
    await user.click(await screen.findByRole('button', { name: '选择修复后的视频' }))
    view.rerender(<AvEnhanceV27Wizard {...value} runSummaries={[run('failed')]} serviceUnavailable connectionEpoch={1} />)
    await act(async () => { resolve(['C:\\synthetic\\late.mp4']) })
    expect(screen.queryByRole('group', { name: '确认新的处理参考' })).not.toBeInTheDocument()
    expect(value.onReplaceSourceAdmitted).not.toHaveBeenCalled()
  })
  it('服务拒绝候选引用时保留失败记录，不启动新分析或自动采用候选', async () => {
    const user = userEvent.setup(), value = props()
    render(<AvEnhanceV27Wizard {...value} runSummaries={[run('failed')]} />)
    await begin(user)
    value.onPickSources.mockResolvedValueOnce(['C:\\synthetic\\candidate.mp4'])
    value.onReplaceSourceAdmitted.mockResolvedValueOnce(false)
    await user.click(await screen.findByRole('button', { name: '选择修复后的视频' }))
    await user.click(screen.getByRole('button', { name: '确认选用并重新分析' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('未能选用候选')
    expect(value.onStartPreparationRun).toHaveBeenCalledOnce()
    expect(value.onPreviewSourceAdmitted).not.toHaveBeenCalled()
    expect(value.onExpandSourceAdmitted).not.toHaveBeenCalled()
  })
})
