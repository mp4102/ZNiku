import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
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

const completedPreparationRun: RunSummaryWire = {
  run_id: '00000000-0000-4000-8000-000000000027',
  project_id: 'project.av27',
  target_mode: 'all',
  selected_targets: [],
  state: 'completed',
  node_count: 2,
  state_counts: {
    pending: 0,
    running: 0,
    waiting_external: 0,
    completed: 2,
    failed: 0,
  },
  actionable: false,
  requires_operator_action: false,
  created_at: '2026-09-02T01:00:00Z',
  started_at: '2026-09-02T01:00:01Z',
  ended_at: '2026-09-02T01:00:03Z',
  latest_activity_at: '2026-09-02T01:00:03Z',
  error: null,
}

function snapshot(sourceMode: 'program' | 'pre_chaptered'): ProjectSnapshotWire {
  return {
    project: {
      project_id: 'project.av27',
      name: 'Synthetic AV27',
      graph: {
        nodes: [
          {
            node_id: 'admission',
            type_id: 'zniku.avenhance.v27.source_admission',
            definition_version: '0.2.1',
            parameters: { source_mode: sourceMode },
            ui_position: { x: 320, y: 120 },
          },
        ],
        edges: [],
      },
    },
    definitions: [],
  }
}

function preview(
  phase: 'preparation' | 'expanded',
  compatible = true,
): AvEnhanceV27TemplatePreviewEnvelope {
  return {
    contract_version: '0.3.0',
    profile_version: '2.7.0',
    phase,
    project: {
      project_id: 'project.av27',
      name: 'Synthetic AV27',
      graph: {
        nodes: [
          {
            node_id: 'source.program',
            type_id: 'zniku.avenhance.v27.source_program',
            definition_version: '0.2.1',
            parameters: { source_path: 'C:\\synthetic\\source.mkv', source_ordinal: 0 },
            ui_position: { x: 80, y: 120 },
          },
          {
            node_id: 'admission',
            type_id: 'zniku.avenhance.v27.source_admission',
            definition_version: '0.2.1',
            parameters: { source_mode: 'program' },
            ui_position: { x: 320, y: 120 },
          },
        ],
        edges: [],
      },
    },
    definitions: [],
    profile: {
      profile_version: '2.7.0',
      phase,
      status: compatible ? `${phase}-compatible` : 'incompatible',
      compatible,
      diagnostics: compatible
        ? []
        : [
            {
              code: 'E_AV27_PREFLIGHT_NODE_SHAPE',
              field_path: 'nodes.admission.parameters.source_mode',
              message: 'Source mode 与模板形状不一致。',
              node_id: 'admission',
            },
          ],
    },
    plan: {
      source_count: 1,
      chapter_count: phase === 'expanded' ? 1 : 0,
      leaf_count: phase === 'expanded' ? 2 : 0,
      mr_mode: 'off',
      preparation_run_id: phase === 'expanded' ? completedPreparationRun.run_id : null,
      effective_video_artifact_ids: phase === 'expanded' ? ['artifact.video'] : [],
      chapters:
        phase === 'expanded'
          ? [
              {
                chapter_id: 'chapter-0001',
                chapter_ordinal: 0,
                label: 'A',
                source_ordinal: 0,
                start_frame: 0,
                end_frame: 3600,
                start_time_seconds: '0',
                end_time_seconds: '120',
                start_timecode: '00:00:00.000',
                end_timecode: '00:02:00.000',
                leaves: [
                  {
                    leaf_id: 'leaf-0001',
                    leaf_ordinal: 0,
                    port_id: 'leaf-0001',
                    start_frame: 0,
                    end_frame: 1800,
                    start_time_seconds: '0',
                    end_time_seconds: '60',
                    start_timecode: '00:00:00.000',
                    end_timecode: '00:01:00.000',
                  },
                  {
                    leaf_id: 'leaf-0002',
                    leaf_ordinal: 1,
                    port_id: 'leaf-0002',
                    start_frame: 1800,
                    end_frame: 3600,
                    start_time_seconds: '60',
                    end_time_seconds: '120',
                    start_timecode: '00:01:00.000',
                    end_timecode: '00:02:00.000',
                  },
                ],
              },
            ]
          : [],
      manual_stages:
        phase === 'preparation'
          ? [{ stage: 'mosaic_restoration', node_count: 1, output_container: '.mkv' }]
          : [
              { stage: 'enhancement', node_count: 2, output_container: '.mov' },
              { stage: 'frame_interpolation', node_count: 1, output_container: '.mov' },
            ],
      output_target_path:
        phase === 'expanded'
          ? 'D:\\Library\\Movie (2026)\\Movie (2026) - Enhanced FI59p94 1080p.mkv'
          : null,
    },
  }
}

function baseProps() {
  return {
    open: true,
    busy: false,
    currentProjectPath: '',
    currentProjectId: 'project.av27',
    currentProjectName: 'Synthetic AV27',
    runSummaries: [] as ReadonlyArray<RunSummaryWire>,
    onClose: vi.fn(),
    onPreview: vi.fn(
      async (_request: AvEnhanceV27TemplatePreviewRequestWire) => preview('preparation'),
    ),
    onCreate: vi.fn(async (_request: AvEnhanceV27PrepareRequestWire) => true),
    onExpand: vi.fn(async (_request: AvEnhanceV27ExpandRequestWire) => true),
    onLocateNode: vi.fn((_nodeId: string) => undefined),
  }
}

describe('AVEnhanceFlow v2.7 template wizard', () => {
  it('打开 modal 后接管键盘焦点，并允许 Escape 请求关闭', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    render(<AvEnhanceV27Wizard {...props} currentSnapshot={null} />)

    expect(screen.getByRole('button', { name: '关闭模板向导' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it('只发送操作者字段创建 preparation preview，并在 compatible 后允许原子 create', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const onPreview = vi.fn(
      async (_request: AvEnhanceV27TemplatePreviewRequestWire) => preview('preparation'),
    )
    render(
      <AvEnhanceV27Wizard
        {...props}
        currentSnapshot={null}
        onPreview={onPreview}
      />,
    )

    expect(screen.getByRole('dialog', { name: 'AVEnhanceFlow v2.7.0 模板向导' })).toBeVisible()
    expect(screen.getByText(/只生成 SourceProgram、SourceAdmission/)).toBeVisible()
    await user.type(screen.getByLabelText('模板工程路径'), 'C:\\synthetic\\av27.zniku')
    await user.type(screen.getByLabelText('Source 1 path'), 'C:\\synthetic\\source.mkv')
    await user.selectOptions(screen.getByLabelText('模板 MR mode'), 'external')
    await user.type(screen.getByLabelText('MR model name'), 'Jasna')
    await user.type(screen.getByLabelText('MR model version'), '0.10.0')
    await user.click(screen.getByRole('button', { name: 'Server preview' }))

    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(1))
    expect(onPreview).toHaveBeenCalledWith({
      action: 'prepare',
      request: {
        profile_version: '2.7.0',
        project_path: 'C:\\synthetic\\av27.zniku',
        project_id: 'project.av27',
        project_name: 'Synthetic AV27',
        source_mode: 'program',
        sources: [
          {
            source_path: 'C:\\synthetic\\source.mkv',
            source_ordinal: 0,
          },
        ],
        mr: { mode: 'external', model_name: 'Jasna', model_version: '0.10.0' },
      },
    })
    expect(await screen.findByText('preparation-compatible')).toBeVisible()
    expect(screen.getByText('source.program')).toBeVisible()

    await user.click(screen.getByRole('button', { name: '创建 Preparation Project' }))
    await waitFor(() => expect(props.onCreate).toHaveBeenCalledTimes(1))
    expect(props.onCreate.mock.calls[0]?.[0]).not.toHaveProperty('graph')
    expect(props.onCreate.mock.calls[0]?.[0]).not.toHaveProperty('definitions')
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it('pre_chaptered preparation 由表单生成连续 source ordinal 与显式 chapter label', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const onPreview = vi.fn(
      async (_request: AvEnhanceV27TemplatePreviewRequestWire) => preview('preparation'),
    )
    render(
      <AvEnhanceV27Wizard
        {...props}
        currentSnapshot={null}
        onPreview={onPreview}
      />,
    )

    await user.type(screen.getByLabelText('模板工程路径'), 'C:\\synthetic\\chapters.zniku')
    await user.selectOptions(screen.getByLabelText('模板 Source mode'), 'pre_chaptered')
    await user.type(screen.getByLabelText('Source 1 path'), 'C:\\synthetic\\chapter-a.mkv')
    await user.type(screen.getByLabelText('Source 1 chapter label'), 'A')
    await user.click(screen.getByRole('button', { name: '添加 Source' }))
    expect(screen.getByRole('button', { name: '移除 Source 2' })).toBeVisible()
    await user.type(screen.getByLabelText('Source 2 path'), 'C:\\synthetic\\chapter-b.mkv')
    await user.type(screen.getByLabelText('Source 2 chapter label'), 'B')
    await user.click(screen.getByRole('button', { name: 'Server preview' }))

    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(1))
    expect(onPreview.mock.calls[0]?.[0]).toMatchObject({
      action: 'prepare',
      request: {
        source_mode: 'pre_chaptered',
        sources: [
          {
            source_path: 'C:\\synthetic\\chapter-a.mkv',
            source_ordinal: 0,
            chapter_label: 'A',
          },
          {
            source_path: 'C:\\synthetic\\chapter-b.mkv',
            source_ordinal: 1,
            chapter_label: 'B',
          },
        ],
      },
    })
  })

  it('pre_chaptered expand 完全省略 selector，并逐字显示 Python 给出的时间、帧与容器', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const onPreview = vi.fn(
      async (_request: AvEnhanceV27TemplatePreviewRequestWire) => preview('expanded'),
    )
    render(
      <AvEnhanceV27Wizard
        {...props}
        currentProjectPath="C:\\synthetic\\av27.zniku"
        currentSnapshot={snapshot('pre_chaptered')}
        onPreview={onPreview}
        runSummaries={[completedPreparationRun]}
      />,
    )

    expect(screen.getByText(/请求不会发送 chapter_selector/)).toBeVisible()
    await user.selectOptions(screen.getByLabelText('Preparation Run'), completedPreparationRun.run_id)
    await user.type(screen.getByLabelText('Enhancement model name'), 'Starlight Precise')
    await user.type(screen.getByLabelText('FI model name'), 'Aion')
    await user.type(screen.getByLabelText('Publication output root'), 'D:\\Library')
    await user.type(screen.getByLabelText('Publication title'), 'Movie')
    await user.type(screen.getByLabelText('Publication year'), '2026')
    await user.click(screen.getByRole('button', { name: 'Server preview' }))

    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(1))
    const request = onPreview.mock.calls[0]?.[0]
    expect(request).toMatchObject({
      action: 'expand',
      request: {
        profile_version: '2.7.0',
        preparation_run_id: completedPreparationRun.run_id,
        leaf_duration_minutes: 1,
        enhancement: { model_name: 'Starlight Precise' },
        frame_interpolation: { model_name: 'Aion' },
        program_encode: { encoder: 'gpu' },
        publication: {
          output_root: 'D:\\Library',
          title: 'Movie',
          year: '2026',
          overwrite: false,
        },
      },
    })
    expect(request?.request).not.toHaveProperty('chapter_selector')

    const plan = await screen.findByRole('region', { name: '服务端 Chapter 与 Leaf plan' })
    expect(within(plan).getByText(/00:00:00\.000 \(0s \/ frame 0\)/)).toBeVisible()
    expect(within(plan).getByText('[0, 1800) · leaf-0001')).toBeVisible()
    expect(screen.getByText('2 nodes')).toBeVisible()
    expect(screen.getAllByText('.mov')).toHaveLength(2)
    expect(screen.getByText(/Movie \(2026\) - Enhanced FI59p94 1080p\.mkv/)).toBeVisible()

    await user.click(screen.getByRole('button', { name: '展开 Production Graph' }))
    await waitFor(() => expect(props.onExpand).toHaveBeenCalledTimes(1))
    expect(props.onExpand.mock.calls[0]?.[0]).not.toHaveProperty('graph')
    expect(props.onExpand.mock.calls[0]?.[0]).not.toHaveProperty('definitions')
  })

  it('program exact_times 原样发送 canonical rational，并可按 diagnostic node_id 定位', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    const onPreview = vi.fn(
      async (_request: AvEnhanceV27TemplatePreviewRequestWire) => preview('expanded', false),
    )
    render(
      <AvEnhanceV27Wizard
        {...props}
        currentSnapshot={snapshot('program')}
        onPreview={onPreview}
        runSummaries={[completedPreparationRun]}
      />,
    )

    await user.selectOptions(screen.getByLabelText('Preparation Run'), completedPreparationRun.run_id)
    await user.selectOptions(screen.getByLabelText('Chapter selector mode'), 'exact_times')
    await user.type(screen.getByLabelText('Exact chapter times'), '1800, 7207200/1001')
    await user.type(screen.getByLabelText('Enhancement model name'), 'Starlight Precise')
    await user.type(screen.getByLabelText('FI model name'), 'Aion')
    await user.type(screen.getByLabelText('Publication output root'), 'D:\\Library')
    await user.type(screen.getByLabelText('Publication title'), 'Movie')
    await user.type(screen.getByLabelText('Publication year'), '2026')
    await user.click(screen.getByRole('button', { name: 'Server preview' }))

    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(1))
    expect(onPreview.mock.calls[0]?.[0]).toMatchObject({
      action: 'expand',
      request: {
        chapter_selector: { mode: 'exact_times', times: ['1800', '7207200/1001'] },
      },
    })
    expect(await screen.findByText('E_AV27_PREFLIGHT_NODE_SHAPE')).toBeVisible()
    expect(screen.getByRole('button', { name: '展开 Production Graph' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '定位 admission' }))
    expect(props.onLocateNode).toHaveBeenCalledWith('admission')
  })

  it('任何表单变化及失败 mutation 都使 preview 失效', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onCreate.mockResolvedValue(false)
    render(<AvEnhanceV27Wizard {...props} currentSnapshot={null} />)

    await user.type(screen.getByLabelText('模板工程路径'), 'C:\\synthetic\\av27.zniku')
    await user.type(screen.getByLabelText('Source 1 path'), 'C:\\synthetic\\source.mkv')
    await user.click(screen.getByRole('button', { name: 'Server preview' }))
    expect(await screen.findByText('preparation-compatible')).toBeVisible()
    expect(screen.getByRole('button', { name: '创建 Preparation Project' })).toBeEnabled()

    await user.type(screen.getByLabelText('模板 Project name'), ' changed')
    expect(screen.queryByText('preparation-compatible')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建 Preparation Project' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Server preview' }))
    expect(await screen.findByText('preparation-compatible')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '创建 Preparation Project' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('preview 已失效')
    expect(screen.getByRole('button', { name: '创建 Preparation Project' })).toBeDisabled()
    expect(props.onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ project_name: 'Synthetic AV27 changed' }),
    )
  })

  it('拒绝展示与请求 action 不一致的 server preview phase', async () => {
    const user = userEvent.setup()
    const props = baseProps()
    props.onPreview.mockResolvedValue(preview('expanded'))
    render(<AvEnhanceV27Wizard {...props} currentSnapshot={null} />)

    await user.type(screen.getByLabelText('模板工程路径'), 'C:\\synthetic\\av27.zniku')
    await user.type(screen.getByLabelText('Source 1 path'), 'C:\\synthetic\\source.mkv')
    await user.click(screen.getByRole('button', { name: 'Server preview' }))

    expect(await screen.findByText(/preview phase 与请求 action 不一致/)).toBeVisible()
    expect(screen.queryByText('expanded-compatible')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建 Preparation Project' })).toBeDisabled()
  })
})
