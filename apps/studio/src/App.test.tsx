import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { App } from './App'
import type { StudioCommand, StudioEnvelope } from './studio/contracts'
import type { StudioGateway } from './studio/gateway'
import {
  handoffEnvelope,
  projectSnapshot,
  sourceDefinition,
  studioEnvelope,
  transformDefinition,
} from './studio/test-fixtures'

afterEach(cleanup)

class RecordingGateway implements StudioGateway {
  readonly commands: StudioCommand[] = []
  inspectCount = 0

  constructor(public envelope: StudioEnvelope = studioEnvelope()) {}

  async inspect(): Promise<StudioEnvelope> {
    this.inspectCount += 1
    return this.envelope
  }

  async command(command: StudioCommand): Promise<StudioEnvelope> {
    this.commands.push(command)
    return this.envelope
  }
}

describe('ZNIKU Studio 0.2.0 formal workspace', () => {
  it('Project Service 缺失时失败关闭，不回退旧正式投影或浏览器 mock', async () => {
    const gateway: StudioGateway = {
      inspect: () => Promise.reject(new Error('loopback offline')),
      command: () => Promise.reject(new Error('loopback offline')),
    }
    render(<App gateway={gateway} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Project Service 不可用')
    expect(screen.getByRole('alert')).toHaveTextContent('loopback offline')
    expect(screen.queryByText('GUI-0 Prototype')).not.toBeInTheDocument()
    expect(screen.queryByText('Expanded Plan')).not.toBeInTheDocument()
    expect(screen.queryByText('Real Acceptance')).not.toBeInTheDocument()
  })

  it('使用单一画布搜索添加、复制节点，并实时阻断缺失 required input', async () => {
    const user = userEvent.setup()
    const ids = ['node.added', 'node.copied']
    render(<App gateway={new RecordingGateway()} nodeIdFactory={() => ids.shift()!} />)

    expect(await screen.findByText('Synthetic Studio Project')).toBeInTheDocument()
    expect(screen.getByText('Designer + Runtime')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Studio Designer 画布' })).toBeInTheDocument()

    await user.type(screen.getByLabelText('搜索节点'), 'manual_external')
    const palette = screen.getByRole('generic', { name: '节点定义列表' })
    await user.click(within(palette).getByRole('button', { name: /test\.transform/ }))
    expect(await screen.findByLabelText('node.added 节点')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '复制所选' }))
    expect(await screen.findByLabelText('node.copied 节点')).toBeInTheDocument()
    expect(screen.getAllByText('E_REQUIRED_INPUT_MISSING').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('按 Python catalog 展示基础媒体节点与 VideoTransform presets', async () => {
    const user = userEvent.setup()
    const mediaSource = { ...sourceDefinition, type_id: 'zniku.media.source.video' }
    const mrPreset = {
      ...transformDefinition,
      type_id: 'zniku.media.video_transform.mr.external',
    }
    const createdEnvelope = studioEnvelope({
      snapshot: {
        project: { ...projectSnapshot.project, graph: { nodes: [], edges: [] } },
        definitions: [mediaSource, mrPreset],
      },
    })
    let currentEnvelope = studioEnvelope({ project_path: null, snapshot: null })
    const commands: StudioCommand[] = []
    const gateway: StudioGateway = {
      inspect: async () => currentEnvelope,
      command: async (command) => {
        commands.push(command)
        if (command.operation === 'create_project') currentEnvelope = createdEnvelope
        return currentEnvelope
      },
    }
    render(<App gateway={gateway} nodeIdFactory={() => 'node.mr'} />)

    expect(await screen.findByText('尚未打开工程')).toBeInTheDocument()
    await user.type(screen.getByLabelText('工程路径'), 'C:\\synthetic\\media.zniku')
    await user.click(screen.getByRole('button', { name: '新建' }))
    await waitFor(() => expect(commands.at(-1)?.operation).toBe('create_project'))

    expect(await screen.findByRole('region', { name: '基础媒体节点' })).toBeInTheDocument()
    const presets = screen.getByRole('region', { name: 'VideoTransform presets' })
    expect(within(presets).getByText('zniku.media.video_transform.mr.external')).toBeInTheDocument()
    await user.click(within(presets).getByRole('button', { name: /zniku\.media\.video_transform\.mr\.external/ }))

    expect(await screen.findByLabelText('node.mr 节点')).toBeInTheDocument()
    expect(screen.getByLabelText('节点参数 JSON')).toHaveValue('{\n  "strength": 3\n}')
  })

  it('打开、新建、参数保存并通过真实命令执行 Run all', async () => {
    const user = userEvent.setup()
    const gateway = new RecordingGateway()
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')

    await user.click(screen.getByRole('button', { name: '打开' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'open_project',
      path: 'C:\\synthetic\\project.zniku',
    }))

    await user.clear(screen.getByLabelText('工程路径'))
    await user.type(screen.getByLabelText('工程路径'), 'C:\\synthetic\\new.zniku')
    await user.clear(screen.getByLabelText('Project ID'))
    await user.type(screen.getByLabelText('Project ID'), 'project.new')
    await user.clear(screen.getByLabelText('Project name'))
    await user.type(screen.getByLabelText('Project name'), 'New Project')
    await user.click(screen.getByRole('button', { name: '新建' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'create_project',
      path: 'C:\\synthetic\\new.zniku',
      project_id: 'project.new',
      name: 'New Project',
    }))

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    const editor = await screen.findByLabelText('节点参数 JSON')
    fireEvent.change(editor, { target: { value: '{"strength":7}' } })
    await user.click(screen.getByRole('button', { name: '应用参数到 Draft' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => {
      const command = gateway.commands.at(-1)
      expect(command?.operation).toBe('save_project')
      if (command?.operation === 'save_project') {
        expect(command.project.graph.nodes.find((node) => node.node_id === 'transform')?.parameters).toEqual({ strength: 7 })
        expect('definitions' in command.project).toBe(false)
      }
    })

    await user.click(screen.getByRole('button', { name: 'Run all' }))
    await waitFor(() => expect(gateway.commands.slice(-2).map((command) => command.operation)).toEqual([
      'save_project',
      'run_all',
    ]))
  })

  it('同图展示 external handoff、日志和路径，并发送 Submit / Run to / Rerun', async () => {
    const user = userEvent.setup()
    const gateway = new RecordingGateway(handoffEnvelope())
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    expect(await screen.findByText('External handoff')).toBeInTheDocument()
    expect(screen.getByText('C:\\synthetic\\source.mkv')).toBeInTheDocument()
    expect(screen.getByText(/output\.mkv/)).toBeInTheDocument()
    expect(screen.getByText('等待外部输出')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Submit external output' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'submit_external',
      node_run_id: '00000000-0000-4000-8000-000000000012',
    }))

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    await user.click(screen.getByRole('button', { name: 'Run to here' }))
    await waitFor(() => expect(gateway.commands.slice(-2).map((command) => command.operation)).toEqual([
      'save_project',
      'run_to',
    ]))
    expect(gateway.commands.at(-1)).toMatchObject({ node_id: 'transform' })

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    await user.click(screen.getByRole('button', { name: 'Rerun from here' }))
    await waitFor(() => expect(gateway.commands.at(-1)).toEqual({
      operation: 'rerun_from_here',
      run_id: '00000000-0000-4000-8000-000000000010',
      node_id: 'transform',
    }))
  })

  it('active_operation 期间禁用全部 Project Service 动作', async () => {
    const gateway = new RecordingGateway(
      handoffEnvelope(),
    )
    gateway.envelope = { ...gateway.envelope, active_operation: 'run_all' }
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    for (const name of [
      '打开',
      '新建',
      '保存',
      'Run all',
      'Run to here',
      'Rerun from here',
      'Submit external output',
    ]) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
    }
  })

  it('Run 为 running 但没有 active_operation 时不持续轮询', async () => {
    const gateway = new RecordingGateway(handoffEnvelope())
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')
    expect(gateway.inspectCount).toBe(1)

    await new Promise((resolve) => window.setTimeout(resolve, 850))
    expect(gateway.inspectCount).toBe(1)
  })

  it('只允许对引用 Run 执行闭包内的节点发起 Rerun', async () => {
    const envelope = handoffEnvelope()
    const run = envelope.runs[0]!
    const sourceOnly = {
      ...run,
      selected_targets: ['source'],
      node_runs: run.node_runs.filter((nodeRun) => nodeRun.node_id === 'source'),
    }
    const gateway = new RecordingGateway({
      ...envelope,
      runs: [sourceOnly],
      active_run_id: sourceOnly.run_id,
    })
    render(<App gateway={gateway} />)
    await screen.findByText('Synthetic Studio Project')

    fireEvent.click(await screen.findByLabelText('transform 节点'))
    expect(screen.getByRole('button', { name: 'Run to here' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Rerun from here' })).toBeDisabled()
  })
})
