/** 融合候选只经显式选择进入新合同；纯合成内存样本，不读写媒体。 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AvEnhanceV27Wizard, type AvEnhanceV27WizardProps } from './AvEnhanceV27Wizard'
import example from './__fixtures__/source-admitted-preview.json'
import { parseSourceAdmittedFullEnvelope } from './source-admitted-contracts'
import { parseFusedFullEnvelope, parseFusedFullRequest, type FusedFullRequest } from './chapter-batch-fused-contracts'
import { FetchStudioGateway } from './gateway'
import { studioEnvelope } from './test-fixtures'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
const previewValue = { ...example, contract_version: '0.3.6', profile_version: '0.3.6', profile_id: 'zniku.source-admitted.chapter-batch-fused', export_cropped_chapters: false }
function request(): FusedFullRequest {
  const base = parseSourceAdmittedFullEnvelope(example)
  return { contract_version: '0.3.6', project_session_id: base.project_session_id, expected_storage_revision: base.storage_revision,
    preparation_run_id: base.preparation_run_id, processing: base.processing, export_cropped_chapters: false,
    publication: { title: 'Synthetic', year: '2026', output_root: 'C:\\synthetic', overwrite: false, layout: 'direct' } }
}
const response = (value: unknown) => new Response(JSON.stringify(value))

describe('0.3.6 独立融合候选', () => {
  it('旧合同拒绝新字段，新合同拒绝旧版本和未知字段，失败不发网络', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    for (const patch of [{ contract_version: '0.3.5' }, { unexpected: true }]) {
      await expect(gateway.previewFused({ ...request(), ...patch } as FusedFullRequest)).rejects.toThrow()
    }
    expect(() => parseFusedFullRequest(request())).not.toThrow()
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('独立路由严格校验工程与导出设置；展开不降级为旧路由', async () => {
    const payload = request(), preview = parseFusedFullEnvelope(previewValue)
    const fetcher = vi.fn().mockResolvedValueOnce(response(preview))
      .mockResolvedValueOnce(response({ ...preview, export_cropped_chapters: true }))
      .mockResolvedValueOnce(response(studioEnvelope({ project_session_id: payload.project_session_id, storage_revision: payload.expected_storage_revision + 1 })))
    vi.stubGlobal('fetch', fetcher)
    const gateway = new FetchStudioGateway('http://127.0.0.1:1')
    await expect(gateway.previewFused(payload)).resolves.toEqual(preview)
    expect(fetcher).toHaveBeenLastCalledWith('http://127.0.0.1:1/api/studio/templates/chapter-batch-fused/full-preview', expect.objectContaining({ body: JSON.stringify(payload) }))
    await expect(gateway.previewFused(payload)).rejects.toThrow('不一致')
    await gateway.expandFused(payload)
    expect(fetcher).toHaveBeenLastCalledWith('http://127.0.0.1:1/api/studio/templates/chapter-batch-fused/expand', expect.objectContaining({ body: JSON.stringify(payload) }))
  })
  it('新建默认035，显式候选与额外导出进入036，但分析创建仍035', async () => {
    const user = userEvent.setup()
    const onCreateSourceAdmitted = vi.fn(async () => true)
    const onPreviewFused = vi.fn<NonNullable<AvEnhanceV27WizardProps['onPreviewFused']>>(async (intent) => parseFusedFullEnvelope({ ...previewValue, processing: intent.processing, export_cropped_chapters: intent.export_cropped_chapters }))
    const onExpandFused = vi.fn(async () => true)
    const onPreviewSourceAdmitted = vi.fn(async () => parseSourceAdmittedFullEnvelope(example))
    const runId = example.preparation_run_id
    const props: AvEnhanceV27WizardProps = {
      open: true, mode: 'create', busy: false, currentSnapshot: null, currentProjectPath: '', currentProjectId: '', currentProjectName: '',
      runSummaries: [], pickerAvailable: true, onClose: vi.fn(), onPickSources: async () => ['C:\\synthetic\\source.mp4'], onPickProjectPath: async () => 'C:\\synthetic\\project.zniku',
      onPreview: async () => null, onCreate: async () => true, onExpand: async () => true, onLocateNode: vi.fn(),
      onCreateSourceAdmitted, onPreviewSourceAdmitted, onExpandSourceAdmitted: async () => true,
      onPreviewFused, onExpandFused, onStartPreparationRun: async () => runId,
      onPreviewSourceAdmittedProcessing: async (value) => ({ ...value, status: 'pending_real_acceptance' }),
      onPreviewPublication: async (value) => ({ contract_version: '0.3.0', layout: value.request.layout ?? 'title_subdirectory', resolved_output_root: 'C:\\synthetic', output_directory: 'C:\\synthetic\\Synthetic (2026)', will_create_directory: true }),
    }
    const view = render(<AvEnhanceV27Wizard {...props} />)
    await user.click(screen.getByRole('button', { name: '选择视频素材' }))
    await user.click(screen.getByRole('button', { name: '选择工程保存位置' }))
    await user.click(screen.getByRole('button', { name: '下一步：处理方案' }))
    expect(screen.getByLabelText('工作流方案')).toHaveValue('source-admitted')
    await user.selectOptions(screen.getByLabelText('工作流方案'), 'fused')
    const exportBox = screen.getByRole('checkbox', { name: /同时导出裁后章节/ })
    expect(exportBox).not.toBeChecked()
    await user.click(exportBox)
    await user.click(screen.getByRole('button', { name: '下一步：处理与成片设置' }))
    await user.type(screen.getByLabelText('片名'), 'Synthetic')
    await user.type(screen.getByLabelText('年份'), '2026')
    await user.click(screen.getByRole('button', { name: '下一步：分析' }))
    await user.click(screen.getByRole('button', { name: /开始分析素材/ }))
    await waitFor(() => expect(onCreateSourceAdmitted).toHaveBeenCalledWith(expect.objectContaining({ contract_version: '0.3.5' })))
    view.rerender(<AvEnhanceV27Wizard {...props} runSummaries={[{
      run_id: runId, project_id: 'synthetic', target_mode: 'all', selected_targets: [], state: 'completed', node_count: 2,
      state_counts: { pending: 0, running: 0, waiting_external: 0, completed: 2, failed: 0 }, actionable: false, requires_operator_action: false,
      created_at: '2026-09-23T00:00:00Z', started_at: '2026-09-23T00:00:00Z', ended_at: '2026-09-23T00:00:01Z', latest_activity_at: '2026-09-23T00:00:01Z', error: null,
    }]} />)
    await waitFor(() => expect(onPreviewFused).toHaveBeenCalledWith(expect.objectContaining({ contract_version: '0.3.6', export_cropped_chapters: true })))
    expect(onPreviewSourceAdmitted).not.toHaveBeenCalled()
    expect(await screen.findByText('已选择额外导出裁后章节，会增加读写与存储。', { exact: false })).toBeVisible()
    await user.click(screen.getByRole('button', { name: '确认并创建工作流' }))
    await waitFor(() => expect(onExpandFused).toHaveBeenCalledWith(expect.objectContaining({ contract_version: '0.3.6', export_cropped_chapters: true })))
  })
})
