/** 真实 Workspace 的自由图展示绑定；服务结论为合成投影，不代替后端 exact-shape 门禁。 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import type { StudioGateway } from './gateway'
import { runningProgressDetail, runningProgressEnvelope } from './test-fixtures'
import { preparedOperation } from './prepared-source.test-fixtures'
import { colorOperation } from './prepared-color.test-fixtures'
import { COLOR_PREPARED_VERSION } from './prepared-color-contracts'
import { WORK_SOURCE_VERSION } from './working-source-contracts'

afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks() })

function mixedGateway(version = '0.3.4') {
  const original = runningProgressDetail(.1), initial = runningProgressEnvelope(.1)
  const typeId = 'zniku.source_preparation.diagnostics', renamedId = 'custom-check-without-template-prefix'
  const graph = { ...original.run.graph_snapshot,
    nodes: original.run.graph_snapshot.nodes.map((node) => node.node_id === 'source' ? { ...node, node_id: renamedId, type_id: typeId, definition_version: version } : node),
    edges: original.run.graph_snapshot.edges.map((edge) => ({ ...edge, source_node_id: edge.source_node_id === 'source' ? renamedId : edge.source_node_id })) }
  const definitions = original.run.definitions_snapshot.map((definition) => definition.type_id === 'test.source' ? { ...definition, type_id: typeId, version } : definition)
  const detail = { ...original, run: { ...original.run, graph_snapshot: graph, definitions_snapshot: definitions,
    node_runs: original.run.node_runs.map((attempt) => attempt.node_id === 'source' ? { ...attempt, node_id: renamedId, definition_version: version } : attempt) } }
  const status = { ...initial, snapshot: { ...initial.snapshot!, definitions, project: { ...initial.snapshot!.project, graph } } }
  const gateway: StudioGateway = { inspect: vi.fn(async () => status), command: vi.fn(), inspectRun: vi.fn(async () => detail), inspectLog: vi.fn(),
    inspectReadiness: vi.fn(), previewAvEnhanceV27: vi.fn(),
    listRuns: vi.fn(async () => ({ contract_version: '0.3.0' as const, run_summaries: status.run_summaries, next_run_cursor: null })),
    inspectPreparedSourceOperation: vi.fn(async (request) => preparedOperation({ ...request, operation: 'run_all' })),
    cancelPreparedSourceOperation: vi.fn(async () => status), cancelPreparedSource: vi.fn(),
    inspectColorPreparedSourceOperation: vi.fn(async (request) => colorOperation({ ...request, operation: 'run_all' })),
    cancelColorPreparedSourceOperation: vi.fn(async () => status), cancelColorPreparedSource: vi.fn(),
    workingSource: { create: vi.fn(), choose: vi.fn(), inspect: vi.fn(), processing: vi.fn(), preview: vi.fn(), expand: vi.fn(), cancel: vi.fn(),
      inspectOperation: vi.fn(async (request) => ({ ...preparedOperation(), ...request, operation: 'run_all' })), cancelOperation: vi.fn(async () => status) },
  }
  return { gateway, status, detail }
}

describe('自由图自动素材节点不依赖向导或实例名称', () => {
  it('普通work改名节点混图仍按真实exact attempt监视并停止，不接旧接口', async () => {
    const { gateway, status, detail } = mixedGateway(WORK_SOURCE_VERSION)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    render(<App gateway={gateway} />)
    await user.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    await user.click(screen.getByRole('button', { name: '展开任务区' }))
    await screen.findByRole('heading', { name: '完整验证外部视频' })
    expect(screen.queryByText(/工作副本写入后仍须完成内容验证和工作源准入/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '停止当前检查或验证' }))
    await waitFor(() => expect(gateway.workingSource!.cancelOperation).toHaveBeenCalledExactlyOnceWith({ contract_version: WORK_SOURCE_VERSION,
      project_session_id: status.project_session_id, run_id: detail.run.run_id, node_run_id: detail.run.node_runs[0]!.node_run_id }))
    expect(gateway.inspectColorPreparedSourceOperation).not.toHaveBeenCalled()
    expect(gateway.inspectPreparedSourceOperation).not.toHaveBeenCalled()
    expect(gateway.cancelPreparedSourceOperation).not.toHaveBeenCalled()
    expect(gateway.command).not.toHaveBeenCalled()
  })
  it.each(['0.3.4', COLOR_PREPARED_VERSION])('%s改名准备节点和混图在busy时仍按exact版本停止当前attempt', async (version) => {
    const { gateway, status, detail } = mixedGateway(version)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<App gateway={gateway} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    await user.click(screen.getByRole('button', { name: '展开任务区' }))
    await screen.findByRole('heading', { name: '完整验证外部视频' })
    await user.click(screen.getByRole('button', { name: '停止当前检查或验证' }))
    const cancel = version === COLOR_PREPARED_VERSION ? gateway.cancelColorPreparedSourceOperation : gateway.cancelPreparedSourceOperation
    await waitFor(() => expect(cancel).toHaveBeenCalledExactlyOnceWith({ contract_version: version,
      project_session_id: status.project_session_id, run_id: detail.run.run_id, node_run_id: detail.run.node_runs[0]!.node_run_id }))
    expect(version === COLOR_PREPARED_VERSION ? gateway.cancelPreparedSourceOperation : gateway.cancelColorPreparedSourceOperation).not.toHaveBeenCalled()
    expect(version === COLOR_PREPARED_VERSION ? gateway.inspectPreparedSourceOperation : gateway.inspectColorPreparedSourceOperation).not.toHaveBeenCalled()
    expect(gateway.cancelPreparedSource).not.toHaveBeenCalled()
    expect(gateway.command).not.toHaveBeenCalled()
  })
  it('相同类型但旧精确版本不挂新操作监视器', async () => {
    const { gateway } = mixedGateway('0.3.3')
    render(<App gateway={gateway} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '关闭工程首页' }))
    await user.click(screen.getByRole('button', { name: '展开任务区' }))
    await waitFor(() => expect(gateway.inspectRun).toHaveBeenCalled())
    expect(gateway.inspectPreparedSourceOperation).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '停止当前检查或验证' })).not.toBeInTheDocument()
  })
})
