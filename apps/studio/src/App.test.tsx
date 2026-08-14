import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { App } from './App'
import { PythonFixtureGateway } from './test/formal-gateway'

afterEach(cleanup)

function commandIds(...ids: string[]): () => string {
  let index = 0
  return () => ids[index++] ?? `command.ui.${index}`
}

describe('ZNIKU Studio Phase 2A formal Designer', () => {
  it('Python bridge 缺失时 fail closed，且不回退 GUI-0 mock', async () => {
    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Python Authoring authority 不可用')
    expect(screen.getByText('AUTHORITY UNAVAILABLE')).toBeInTheDocument()
    expect(screen.queryByText('MOCK · NO MEDIA I/O')).not.toBeInTheDocument()
    expect(screen.queryByText('编译预览')).not.toBeInTheDocument()
  })

  it('加载 Python authority、精确 typed ports 与阻塞诊断', async () => {
    render(<App gateway={new PythonFixtureGateway()} />)

    expect(await screen.findByText('workflow.synthetic.program')).toBeInTheDocument()
    expect(screen.getByText('revision 0', { exact: false })).toBeInTheDocument()
    expect(screen.getAllByText('Synthetic Program Filter').length).toBeGreaterThan(0)
    expect(screen.getByText('E_GRAPH_INPUT_CARDINALITY')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Expanded Plan' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Run Monitor' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: /Synthetic Program Filter/ }))
    expect(screen.getAllByText('media/program_media · program · one')).toHaveLength(2)
    expect(screen.getByText('example.synthetic.program_filter')).toBeInTheDocument()
  })

  it('通过 typed command 连接端口并接受 Python authoring-valid revision', async () => {
    const user = userEvent.setup()
    render(
      <App
        gateway={new PythonFixtureGateway()}
        commandIdFactory={commandIds('command.ui.connect')}
      />,
    )
    await screen.findByText('workflow.synthetic.program')

    await user.selectOptions(
      screen.getByLabelText('Source endpoint'),
      'node.engine.filter|program_out',
    )
    await user.selectOptions(
      screen.getByLabelText('Target endpoint'),
      'node.final.program|program',
    )
    await user.click(screen.getByRole('button', { name: '提交连接' }))

    expect(await screen.findByText('revision 1', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('authoring_valid · 无阻塞诊断')).toBeInTheDocument()
    expect(screen.queryByText('E_GRAPH_INPUT_CARDINALITY')).not.toBeInTheDocument()
  })

  it('完整替换参数并展示 Python Manifest diagnostic，可定位到 EngineStage', async () => {
    const user = userEvent.setup()
    render(
      <App
        gateway={new PythonFixtureGateway()}
        commandIdFactory={commandIds('command.ui.connect', 'command.ui.parameters')}
      />,
    )
    await screen.findByText('workflow.synthetic.program')
    await user.selectOptions(
      screen.getByLabelText('Source endpoint'),
      'node.engine.filter|program_out',
    )
    await user.selectOptions(
      screen.getByLabelText('Target endpoint'),
      'node.final.program|program',
    )
    await user.click(screen.getByRole('button', { name: '提交连接' }))
    await screen.findByText('authoring_valid · 无阻塞诊断')

    await user.click(screen.getByRole('button', { name: /Synthetic Program Filter/ }))
    const editor = screen.getByLabelText('Engine 参数 JSON')
    fireEvent.change(editor, { target: { value: '{"strength":99}' } })
    await user.click(screen.getByRole('button', { name: '替换参数并验证' }))

    expect(await screen.findByText('E_ENGINE_PARAMETERS_INVALID')).toBeInTheDocument()
    const diagnostic = screen.getByText('E_ENGINE_PARAMETERS_INVALID').closest('article')
    expect(diagnostic).not.toBeNull()
    await user.click(screen.getByRole('button', { name: /Program Source/ }))
    await user.click(within(diagnostic!).getByRole('button', { name: '定位' }))
    expect(screen.getByRole('heading', { name: 'Synthetic Program Filter' })).toBeInTheDocument()
  })

  it('通过正式 edge ID 断开连接并接收新 revision', async () => {
    const user = userEvent.setup()
    render(
      <App
        gateway={new PythonFixtureGateway()}
        commandIdFactory={commandIds('command.ui.connect', 'command.ui.disconnect')}
      />,
    )
    await screen.findByText('workflow.synthetic.program')
    await user.selectOptions(
      screen.getByLabelText('Source endpoint'),
      'node.engine.filter|program_out',
    )
    await user.selectOptions(
      screen.getByLabelText('Target endpoint'),
      'node.final.program|program',
    )
    await user.click(screen.getByRole('button', { name: '提交连接' }))
    await screen.findByText('revision 1', { exact: false })

    const disconnectButtons = screen.getAllByRole('button', { name: /^断开 edge\./ })
    const generated = disconnectButtons.find((button) => !button.textContent?.includes('source.filter'))
    expect(generated).toBeDefined()
    await user.click(generated!)

    expect(await screen.findByText('revision 2', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('E_GRAPH_INPUT_CARDINALITY')).toBeInTheDocument()
  })

  it('显示 stale command rejection 并要求刷新 authority', async () => {
    const user = userEvent.setup()
    render(
      <App
        gateway={new PythonFixtureGateway()}
        commandIdFactory={commandIds('command.fixture.stale')}
      />,
    )
    await screen.findByText('workflow.synthetic.program')
    await user.click(screen.getByRole('button', { name: '提交连接' }))

    expect(await screen.findByText('E_DRAFT_REVISION_STALE')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('请刷新 authority')
    await waitFor(() => expect(screen.getByText('revision 0', { exact: false })).toBeInTheDocument())
  })
})
