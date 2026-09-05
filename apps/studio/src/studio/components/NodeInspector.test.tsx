import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { handoffDetailEnvelope, handoffLogEnvelope, sourceDefinition, transformDefinition } from '../test-fixtures'
import { NodeInspector, type NodeInspectorProps } from './NodeInspector'

afterEach(cleanup)
const detail = handoffDetailEnvelope()
const waiting = detail.run.node_runs.find((item) => item.state === 'waiting_external')!

function props(overrides: Partial<NodeInspectorProps> = {}): NodeInspectorProps {
  return {
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

describe('创作者节点 Inspector', () => {
  it('外部任务的状态与唯一助手先于长参数表，参数仍是同一份可访问draft', () => {
    const onChange = vi.fn()
    render(<NodeInspector {...props({
      authoringPanel: <section aria-label="节点展示设置">别名与分组</section>,
      handoffCenter: <section aria-label="合成交接助手">检查输出后提交</section>,
      parameterDraft: { strength: 4 }, onParameterDraftChange: onChange,
    })} />)
    const status = screen.getByLabelText('步骤处理状态')
    const assistant = screen.getByLabelText('合成交接助手')
    const authoring = screen.getByLabelText('节点展示设置')
    const parameter = screen.getByRole('spinbutton', { name: 'strength' })
    expect(status.compareDocumentPosition(assistant) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(assistant.compareDocumentPosition(authoring) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(assistant.compareDocumentPosition(parameter) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getAllByLabelText('合成交接助手')).toHaveLength(1)
    expect(screen.getAllByRole('spinbutton', { name: 'strength' })).toHaveLength(1)
    expect(parameter).toHaveValue(4)
    expect(parameter).toBeVisible()
    fireEvent.change(parameter, { target: { value: '5' } })
    expect(onChange).toHaveBeenCalledExactlyOnceWith({ strength: 5 })
  })

  it.each(['running', 'failed', 'completed'] as const)('%s 状态优先于编辑表单，不把全局助手重复插入', (state) => {
    render(<NodeInspector {...props({ selectedNodeRun: { ...waiting, state, external_handoff: null }, handoffCenter: <section aria-label="其他交接任务">其他等待任务</section> })} />)
    const status = screen.getByLabelText('步骤处理状态')
    const settings = screen.getByRole('heading', { name: '设置' })
    expect(status.compareDocumentPosition(settings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getAllByLabelText('步骤处理状态')).toHaveLength(1)
    expect(screen.getAllByLabelText('其他交接任务')).toHaveLength(1)
  })

  it('默认不挂载运行身份或日志，原始失败只在显式高级详情显示', () => {
    const nodeRun = { ...waiting, state: 'failed' as const, error: { reason: 'execution_error' as const, message: 'E_SYNTHETIC_UNKNOWN: secret runtime diagnostic' } }
    const onToggle = vi.fn()
    const value = props({ selectedNodeRun: nodeRun, onToggleDiagnostics: onToggle })
    const { rerender } = render(<NodeInspector {...value} />)
    expect(screen.queryByText(waiting.node_run_id)).not.toBeInTheDocument()
    expect(screen.queryByText('stdout')).not.toBeInTheDocument()
    expect(screen.queryByText(/secret runtime diagnostic/)).not.toBeInTheDocument()
    const advanced = screen.getByText('高级 → 运行身份与日志').closest('details')!
    fireEvent(advanced, new Event('toggle'))
    expect(onToggle).toHaveBeenCalled()
    rerender(<NodeInspector {...value} advancedDetailsOpen />)
    expect(screen.getByText(waiting.node_run_id)).toBeVisible()
    expect(screen.getByText(/secret runtime diagnostic/)).toBeVisible()
    expect(screen.getByText('stdout')).toBeVisible()
  })

  it('manual external 忽略任何百分比和测量，仅显示等待与助手定位', () => {
    render(<NodeInspector {...props({ selectedNodeRun: { ...waiting, progress: 0.5 }, selectedProgress: {
      mode: 'determinate', fraction: 0.5, measurement: { node_run_id: waiting.node_run_id, fraction: 0.5, current: 50, total: 100, unit: 'frames', observed_at: waiting.created_at }, elapsed: 'ETA 3 秒',
    } })} />)
    expect(screen.getByText('等待外部处理')).toBeVisible()
    expect(screen.getByRole('link', { name: '外部处理助手' })).toHaveAttribute('href', '#external-processing-assistant')
    expect(screen.queryByText(/50%|50 \/ 100|ETA/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '提交并继续' })).not.toBeInTheDocument()
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

  it('正式 stale 与 reused 保留原意，输出动作仍按 Artifact 身份发送', () => {
    const artifact = detail.artifacts[0]!
    const value = props({ selectedNodeRun: { ...waiting, state: 'completed', reused_from_result_id: 'synthetic-result' }, selectedLatestResult: {
      node_id: waiting.node_id, result_id: 'synthetic-result', stale: true, stale_reason: 'graph_changed', updated_at: waiting.created_at,
    }, selectedOutputs: [artifact] })
    render(<NodeInspector {...value} />)
    expect(screen.getByText(/当前工程的此步骤需要重新处理/)).toBeVisible()
    expect(screen.getByText('已复用上次有效结果，本次没有重复处理。')).toBeVisible()
    expect(screen.queryByText('synthetic-result')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '播放' }))
    expect(value.onOpenArtifact).toHaveBeenCalledExactlyOnceWith(artifact.artifact_id)
    fireEvent.click(screen.getByRole('button', { name: '复制输出路径' }))
    expect(value.onCopyPath).toHaveBeenCalledExactlyOnceWith(artifact.path)
  })

  it('日志离线保留最后可信原文，放弃运行只在高级操作且运行时不可点', () => {
    render(<NodeInspector {...props({ advancedDetailsOpen: true, logStale: true, actionableRunIsRunning: true })} />)
    expect(screen.getByText('日志通道离线，以下保留最后可信内容。')).toBeVisible()
    expect(screen.getByText('等待外部输出')).toBeVisible()
    const abandon = screen.getByRole('button', { name: 'Abandon Run', hidden: true })
    expect(abandon).not.toBeVisible()
    expect(abandon).toBeDisabled()
    fireEvent.click(screen.getByText('高级 → 放弃本次处理'))
    expect(abandon).toBeVisible()
  })
})
