/** 页签只重组同一草稿和控制器，不产生运行、导入或日志读取命令。 */

import { useEffect, useState } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { EdgeWire, JsonObject } from '../contracts'
import { edgeId } from '../graph'
import { handoffDetailEnvelope, handoffLogEnvelope, sourceDefinition, transformDefinition } from '../test-fixtures'
import { NodeInspector, type InspectorTab, type NodeInspectorProps } from './NodeInspector'

afterEach(cleanup)
const detail = handoffDetailEnvelope()
const waiting = detail.run.node_runs.find((item) => item.state === 'waiting_external')!

function props(overrides: Partial<NodeInspectorProps> = {}): NodeInspectorProps {
  return {
    tab: 'settings', onTabChange: vi.fn(),
    nodeLabel: () => '画质增强', selectedNode: { node_id: waiting.node_id, type_id: transformDefinition.type_id, definition_version: transformDefinition.version },
    selectedDefinition: transformDefinition, selectedPresentation: null, selectedEdge: null,
    parameterDraft: {}, parameterText: '{}', parameterDirty: false, parameterRawError: null,
    parameterValidation: { valid: true, errors: [], compileError: null }, graphEditable: true, busy: false,
    selectedNodeRun: waiting, selectedProgress: null, selectedLog: handoffLogEnvelope().log,
    logStale: false, readinessStale: false, mutationBlocked: false, selectedOutputs: [],
    handoffInputs: [], readiness: null, detail, lastFullPrecheckFailure: null, handoffCenter: null,
    actionableRun: true, actionableRunIsRunning: false, clientHint: null, boundaryError: null,
    onParameterDraftChange: vi.fn(), onParameterTextChange: vi.fn(), onApplyParameters: vi.fn(), onDiscardParameters: vi.fn(),
    onCopyPath: vi.fn(), canRevealArtifact: true, canOpenArtifact: true, onRevealArtifact: vi.fn(), onOpenArtifact: vi.fn(),
    onReorderEdge: vi.fn(), onDeleteEdge: vi.fn(), onAbandonRun: vi.fn(), ...overrides,
  }
}

describe('创作者节点 Inspector 工作区', () => {
  it('参数优先，显示设置默认折叠，唯一文件助手只在文件页可见', () => {
    const value = props({ authoringPanel: <section aria-label="节点展示设置">别名与分组</section>,
      handoffCenter: <section aria-label="合成交接助手">检查输出后提交</section>, parameterDraft: { strength: 4 } })
    const { rerender } = render(<NodeInspector {...value} />)
    const parameter = screen.getByRole('spinbutton', { name: 'strength' })
    const authoring = screen.getByLabelText('节点展示设置')
    expect(parameter).toHaveValue(4)
    expect(parameter).toBeVisible()
    expect(parameter.compareDocumentPosition(authoring) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('显示设置').closest('details')).not.toHaveAttribute('open')
    expect(authoring).not.toBeVisible()
    expect(screen.getAllByLabelText('合成交接助手')).toHaveLength(1)
    expect(screen.getByLabelText('合成交接助手')).not.toBeVisible()
    expect(screen.queryByText('stdout')).not.toBeInTheDocument()
    fireEvent.change(parameter, { target: { value: '5' } })
    expect(value.onParameterDraftChange).toHaveBeenCalledExactlyOnceWith({ strength: 5 })
    rerender(<NodeInspector {...value} tab="files" />)
    expect(screen.getByLabelText('合成交接助手')).toBeVisible()
    expect(parameter).not.toBeVisible()
    expect(screen.getByRole('tabpanel', { name: '文件' })).toBeVisible()
  })

  it('页签完全受控，节点、轮询与高级模式变化不擅自切页或提交', () => {
    const value = props({ parameterDirty: true })
    const { rerender } = render(<NodeInspector {...value} />)
    fireEvent.click(screen.getByRole('tab', { name: '文件' }))
    expect(value.onTabChange).toHaveBeenCalledExactlyOnceWith('files')
    expect(screen.getByRole('tab', { name: '设置' })).toHaveAttribute('aria-selected', 'true')
    rerender(<NodeInspector {...value} tab="files" selectedNode={{ ...value.selectedNode!, node_id: 'other-synthetic-node' }} selectedNodeRun={{ ...waiting, progress: 0.4 }} advanced />)
    expect(screen.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
    expect(value.onTabChange).toHaveBeenCalledTimes(1)
    for (const callback of [value.onApplyParameters, value.onDiscardParameters, value.onAbandonRun,
      value.onParameterDraftChange, value.onParameterTextChange, value.onOpenArtifact, value.onRevealArtifact]) expect(callback).not.toHaveBeenCalled()
  })

  it('表单与原始 JSON 共用父级草稿，跨页保留且只在显式应用后发出回调', () => {
    const value = props()
    function SharedDraft() {
      const [tab, setTab] = useState<InspectorTab>('settings')
      const [draft, setDraft] = useState<JsonObject>({ strength: 4 })
      return <NodeInspector {...value} tab={tab} onTabChange={setTab} parameterDraft={draft}
        parameterText={JSON.stringify(draft)} parameterDirty onParameterDraftChange={setDraft}
        onParameterTextChange={(text) => setDraft(JSON.parse(text) as JsonObject)} />
    }
    render(<SharedDraft />)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'strength' }), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('tab', { name: '文件' }))
    fireEvent.click(screen.getByRole('tab', { name: '诊断' }))
    fireEvent.click(screen.getByText('高级 → 原始参数'))
    const raw = screen.getByRole('textbox', { name: '节点参数 JSON' })
    expect(raw).toHaveValue('{"strength":5}')
    fireEvent.change(raw, { target: { value: '{"strength":6}' } })
    expect(value.onApplyParameters).not.toHaveBeenCalled()
    expect(value.onDiscardParameters).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '返回设置' }))
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toHaveValue(6)
    fireEvent.click(screen.getByRole('button', { name: '应用设置' }))
    expect(value.onApplyParameters).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: '放弃未应用更改' }))
    expect(value.onDiscardParameters).toHaveBeenCalledOnce()
  })

  it('文件页稳定挂载唯一收件控制器，切页保持候选确认状态而不提交', () => {
    const mount = vi.fn()
    const unmount = vi.fn()
    const submit = vi.fn()
    function Inbox() {
      const [candidate, setCandidate] = useState(false)
      useEffect(() => { mount(); return unmount }, [])
      return <section aria-label="唯一收件控制器">
        <button type="button" onClick={() => setCandidate(true)}>选择合成候选</button>
        {candidate && <p>候选等待确认</p>}
        <button type="button" onClick={submit}>显式提交合成候选</button>
      </section>
    }
    function Controlled() {
      const [tab, setTab] = useState<InspectorTab>('settings')
      return <NodeInspector {...props()} tab={tab} onTabChange={setTab} handoffCenter={<Inbox />} />
    }
    render(<Controlled />)
    expect(mount).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('唯一收件控制器')).not.toBeVisible()
    fireEvent.click(screen.getByRole('tab', { name: '文件' }))
    fireEvent.click(screen.getByRole('button', { name: '选择合成候选' }))
    fireEvent.click(screen.getByRole('tab', { name: '设置' }))
    fireEvent.click(screen.getByRole('tab', { name: '诊断' }))
    expect(screen.getByText('候选等待确认')).not.toBeVisible()
    fireEvent.click(screen.getByRole('tab', { name: '文件' }))
    expect(screen.getByText('候选等待确认')).toBeVisible()
    expect(screen.getAllByLabelText('唯一收件控制器')).toHaveLength(1)
    expect(mount).toHaveBeenCalledTimes(1)
    expect(unmount).not.toHaveBeenCalled()
    expect(submit).not.toHaveBeenCalled()
  })

  it('页签具备键盘关联，方向键仅移动焦点，Enter 才请求导航', async () => {
    const user = userEvent.setup()
    const value = props()
    render(<NodeInspector {...value} />)
    const settings = screen.getByRole('tab', { name: '设置' })
    const files = screen.getByRole('tab', { name: '文件' })
    const diagnostics = screen.getByRole('tab', { name: '诊断' })
    expect(screen.getByRole('tabpanel', { name: '设置' })).toHaveAttribute('id', settings.getAttribute('aria-controls'))
    settings.focus()
    await user.keyboard('{ArrowRight}')
    expect(files).toHaveFocus()
    expect(value.onTabChange).not.toHaveBeenCalled()
    await user.keyboard('{End}')
    expect(diagnostics).toHaveFocus()
    await user.keyboard('{Home}{ArrowLeft}{Enter}')
    expect(diagnostics).toHaveFocus()
    expect(value.onTabChange).toHaveBeenCalledExactlyOnceWith('diagnostics')
  })

  it('高级图和旧展开状态都不挂载日志；只有诊断页显示精确原文', () => {
    const nodeRun = { ...waiting, state: 'failed' as const, error: { reason: 'execution_error' as const, message: 'E_SYNTHETIC_UNKNOWN: runtime diagnostic' } }
    const onToggle = vi.fn()
    const value = props({ selectedNodeRun: nodeRun, advanced: true, advancedDetailsOpen: true, onToggleDiagnostics: onToggle,
      logStale: true, serviceDiagnostics: <section>合成服务诊断</section> })
    const { rerender } = render(<NodeInspector {...value} />)
    expect(screen.getByLabelText('步骤处理状态')).toBeVisible()
    expect(screen.queryByText(waiting.node_run_id)).not.toBeInTheDocument()
    expect(screen.queryByText('stdout')).not.toBeInTheDocument()
    expect(screen.queryByText(/runtime diagnostic/)).not.toBeInTheDocument()
    expect(screen.queryByText('合成服务诊断')).not.toBeInTheDocument()
    expect(onToggle).not.toHaveBeenCalled()
    rerender(<NodeInspector {...value} tab="diagnostics" />)
    expect(screen.getByText(waiting.node_run_id)).toBeVisible()
    expect(screen.getByText(/runtime diagnostic/)).toBeVisible()
    expect(screen.getByText('stdout')).toBeVisible()
    expect(screen.getByText('等待外部输出')).toBeVisible()
    expect(screen.getByText('日志通道离线，以下保留最后可信内容。')).toBeVisible()
    expect(screen.getByText('合成服务诊断')).toBeVisible()
    expect(screen.getByText(/诊断可能包含本机路径/)).toBeVisible()
    expect(screen.getByText('高级 → parameter_schema')).toBeVisible()
    expect(screen.getByText('高级 → 节点合同')).toBeVisible()
    expect(onToggle).not.toHaveBeenCalled()
  })

  it('历史只读仍能导航和查看，表单、原始参数与应用均不可写', async () => {
    const user = userEvent.setup()
    const value = props({ graphEditable: false, parameterDraft: { strength: 4 }, parameterText: '{"strength":4}', parameterDirty: true })
    const { rerender } = render(<NodeInspector {...value} />)
    expect(screen.getByText(/正在查看处理记录，只读/)).toBeVisible()
    expect(screen.getByRole('spinbutton', { name: 'strength' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '应用设置' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '应用设置' }))
    await user.click(screen.getByRole('tab', { name: '诊断' }))
    expect(value.onTabChange).toHaveBeenCalledExactlyOnceWith('diagnostics')
    rerender(<NodeInspector {...value} tab="diagnostics" />)
    await user.click(screen.getByText('高级 → 原始参数'))
    const raw = screen.getByRole('textbox', { name: '节点参数 JSON' })
    expect(raw).toHaveAttribute('readonly')
    await user.type(raw, 'bad edit')
    expect(value.onParameterTextChange).not.toHaveBeenCalled()
    expect(value.onParameterDraftChange).not.toHaveBeenCalled()
    expect(value.onApplyParameters).not.toHaveBeenCalled()
  })

  it('应用栏位于滚动区之外，非法原始参数保留错误并禁用应用', () => {
    const value = props({ parameterDirty: true, parameterRawError: 'JSON 尚未闭合' })
    render(<NodeInspector {...value} />)
    expect(screen.getByLabelText('参数应用操作').closest('.inspector-workspace-scroll')).toBeNull()
    expect(screen.getByRole('button', { name: '应用设置' })).toBeDisabled()
    expect(screen.getByText('JSON 尚未闭合')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '查看原始参数' }))
    expect(value.onTabChange).toHaveBeenCalledExactlyOnceWith('diagnostics')
    expect(value.onApplyParameters).not.toHaveBeenCalled()
  })

  it.each(['running', 'failed', 'completed'] as const)('%s 只展示所选步骤，不嵌入其他任务助手', (state) => {
    render(<NodeInspector {...props({ tab: 'files', selectedNodeRun: { ...waiting, state, external_handoff: null }, handoffCenter: <section aria-label="其他交接任务">其他等待任务</section> })} />)
    expect(screen.getAllByLabelText('步骤处理状态')).toHaveLength(1)
    expect(screen.queryByLabelText('其他交接任务')).not.toBeInTheDocument()
  })

  it('manual external 忽略百分比和测量，只显示实际等待摘要与文件导航', () => {
    const value = props({ selectedNodeRun: { ...waiting, progress: 0.5 }, selectedProgress: {
      mode: 'determinate', fraction: 0.5, measurement: { node_run_id: waiting.node_run_id, fraction: 0.5, current: 50, total: 100, unit: 'frames', observed_at: waiting.created_at }, elapsed: 'ETA 3 秒',
    } })
    render(<NodeInspector {...value} />)
    expect(screen.getByText('等待外部处理')).toBeVisible()
    expect(screen.getByText(/已等待/)).toBeVisible()
    expect(screen.queryByText(/50%|50 \/ 100|ETA/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看外部文件' }))
    expect(value.onTabChange).toHaveBeenCalledExactlyOnceWith('files')
    expect(value.onApplyParameters).not.toHaveBeenCalled()
  })

  it('automatic 只展示服务投影测量，无总量时不伪造百分比', () => {
    const value = props({ selectedDefinition: sourceDefinition, selectedNodeRun: { ...waiting, state: 'running', external_handoff: null }, selectedProgress: {
      mode: 'determinate', fraction: 0.5, measurement: { node_run_id: waiting.node_run_id, fraction: 0.5, current: 50, total: 100, unit: 'frames', observed_at: waiting.created_at }, elapsed: '已运行 10 秒',
    } })
    const { rerender } = render(<NodeInspector {...value} />)
    expect(screen.getByText('50%')).toBeVisible()
    expect(screen.getByText('50 / 100 帧')).toBeVisible()
    rerender(<NodeInspector {...value} selectedProgress={{ mode: 'indeterminate', fraction: null, measurement: null, elapsed: '已运行 11 秒' }} />)
    expect(screen.queryByText('50%')).not.toBeInTheDocument()
    expect(screen.getByText('正在处理，暂时没有可计算的百分比。')).toBeVisible()
  })

  it('stale 与 reused 摘要正常可见，文件动作仍按原始 Artifact 身份发送', () => {
    const artifact = detail.artifacts[0]!
    const value = props({ selectedNodeRun: { ...waiting, state: 'completed', reused_from_result_id: 'synthetic-result' }, selectedLatestResult: {
      node_id: waiting.node_id, result_id: 'synthetic-result', stale: true, stale_reason: 'graph_changed', updated_at: waiting.created_at,
    }, selectedOutputs: [artifact] })
    const { rerender } = render(<NodeInspector {...value} />)
    expect(screen.getByText(/当前工程的此步骤需要重新处理/)).toBeVisible()
    expect(screen.getByText('已复用上次有效结果，本次没有重复处理。')).toBeVisible()
    expect(screen.queryByText('synthetic-result')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '播放' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看输出（1）' }))
    expect(value.onTabChange).toHaveBeenCalledExactlyOnceWith('files')
    rerender(<NodeInspector {...value} tab="files" />)
    fireEvent.click(screen.getByRole('button', { name: '播放' }))
    expect(value.onOpenArtifact).toHaveBeenCalledExactlyOnceWith(artifact.artifact_id)
    fireEvent.click(screen.getByRole('button', { name: '在文件夹中显示' }))
    expect(value.onRevealArtifact).toHaveBeenCalledExactlyOnceWith(artifact.artifact_id)
    fireEvent.click(screen.getByRole('button', { name: '复制输出路径' }))
    expect(value.onCopyPath).toHaveBeenCalledExactlyOnceWith(artifact.path)
  })

  it('节点动作保持调用者所有权，Inspector 不再承载整个 Run 的放弃操作', () => {
    const action = vi.fn()
    const value = props({ nodeActions: <button type="button" onClick={action}>处理此步骤</button> })
    const { rerender } = render(<NodeInspector {...value} />)
    fireEvent.click(screen.getByRole('button', { name: '处理此步骤' }))
    expect(action).toHaveBeenCalledOnce()
    rerender(<NodeInspector {...value} tab="diagnostics" />)
    expect(screen.queryByRole('button', { name: /Abandon Run|放弃本次处理/, hidden: true })).not.toBeInTheDocument()
    expect(value.onAbandonRun).not.toHaveBeenCalled()
  })

  it('空选与多选给出温和提示，不显示单节点设置或批量覆盖入口', () => {
    const value = props({ selectedNode: null, selectedNodeRun: null, selectedDefinition: null, parameterValidation: null })
    const { rerender } = render(<NodeInspector {...value} />)
    expect(screen.getByRole('tabpanel', { name: '设置' })).toHaveTextContent('选择一个步骤或连接')
    expect(screen.queryByRole('button', { name: '应用设置' })).not.toBeInTheDocument()
    rerender(<NodeInspector {...props({ selectedNodeCount: 2, nodeActions: <button>处理单节点</button>, authoringPanel: <p>共同显示设置</p> })} />)
    expect(screen.getByRole('heading', { name: '已选择 2 个步骤' })).toBeVisible()
    expect(screen.getByRole('tabpanel', { name: '设置' })).toHaveTextContent('多选时不批量覆盖处理参数')
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '处理单节点' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '应用设置' })).not.toBeInTheDocument()
  })

  it('边设置保留人类顺序与精确重排回调，不按画布位置猜测 ordinal', () => {
    const edge: EdgeWire = { source_node_id: 'first', source_port_id: 'out', target_node_id: 'merge', target_port_id: 'in', ordinal: 0 }
    const second = { ...edge, source_node_id: 'second', ordinal: 1 }
    const value = props({ selectedNode: null, selectedNodeRun: null, selectedEdge: edge, advanced: true,
      nodeLabel: (id) => id, orderedInputs: [{ label: '合并输入', edges: [second, edge] }] })
    render(<NodeInspector {...value} />)
    expect(screen.getByRole('heading', { name: '连接设置' })).toBeVisible()
    expect(screen.getByText(/合并顺序：第 1 项/)).toBeVisible()
    expect(within(screen.getByRole('list', { name: '输入顺序' })).getAllByRole('listitem')[0]).toHaveTextContent('first')
    fireEvent.click(screen.getByRole('button', { name: '下移 first · out' }))
    expect(value.onReorderEdge).toHaveBeenCalledExactlyOnceWith(edgeId(edge), 1)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Edge ordinal' }), { target: { value: '1' } })
    expect(value.onReorderEdge).toHaveBeenLastCalledWith(edgeId(edge), 1)
    fireEvent.click(screen.getByRole('button', { name: '删除所选连接' }))
    expect(value.onDeleteEdge).toHaveBeenCalledOnce()
  })

  it.each(['settings', 'files', 'diagnostics'] as const)('%s 保留全局提示，精确边界错误只在诊断页显示', (tab) => {
    render(<NodeInspector {...props({ tab, clientHint: '合成操作结果仍待确认', boundaryError: 'E_SYNTHETIC_INTERNAL: detail' })} />)
    expect(screen.getByRole('status', { name: '操作提示' })).toHaveTextContent('合成操作结果仍待确认')
    expect(screen.getByText(/这次操作未能完成/)).toBeVisible()
    if (tab === 'diagnostics') expect(screen.getByText('E_SYNTHETIC_INTERNAL: detail')).toBeVisible()
    else expect(screen.queryByText('E_SYNTHETIC_INTERNAL: detail')).not.toBeInTheDocument()
  })
})
