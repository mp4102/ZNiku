/** 两个同名外部任务只通过明确选择显示各自详情；复制和导入不能串用另一任务的路径/身份。 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { NodeRunWire } from '../contracts'
import type { HandoffImportController } from '../use-handoff-import'
import { handoffDetailEnvelope } from '../test-fixtures'
import { HandoffCenter, type HandoffCenterProps } from './HandoffCenter'

afterEach(cleanup)
const base = handoffDetailEnvelope()
const first = base.run.node_runs.find((item) => item.external_handoff)!
const pair = ['A', 'B'].map((suffix): NodeRunWire => ({ ...first, node_id: `enhance-${suffix}`, node_run_id: `run-${suffix}`,
  external_handoff: { ...first.external_handoff!, handoff_id: `handoff-${suffix}`, node_run_id: `run-${suffix}`,
    input_artifact_ids: [`input-${suffix}`], output_targets: [{ port_id: 'out', ordinal: null, path: `D:\\synthetic\\task-${suffix}\\out.mkv` }] },
}))
function fixture(): HandoffCenterProps {
  const artifacts = ['A', 'B'].map((suffix) => ({ ...base.artifacts[0]!, artifact_id: `input-${suffix}`, path: `D:\\synthetic\\input-${suffix}.mkv` }))
  return { selectedNodeId: null, nodeLabel: (id) => `画质增强（${id.endsWith('A') ? 1 : 2}）`, waitingNodeRuns: pair,
    detail: { ...base, run: { ...base.run, node_runs: pair }, artifacts,
      handoff_contracts: pair.map((nodeRun, index) => ({ node_run_id: nodeRun.node_run_id, handoff_id: nodeRun.external_handoff!.handoff_id,
        title: '正式处理要求', input_artifact_id: `input-${index === 0 ? 'A' : 'B'}`, fields: [{ label: '输出 exact N', value: index === 0 ? '899' : '902' }] })) },
    artifactsById: new Map(artifacts.map((artifact) => [artifact.artifact_id, artifact])), readiness: new Map(), checkedOutputs: new Map(),
    lastFullPrecheckFailures: new Map(), checkingNodeRunId: null, submittingNodeRunId: null, mutationBlocked: false, readinessStale: false,
    canRevealHandoff: true, canOpenHandoffInput: true, canImportHandoff: true,
    onSelectNode: vi.fn(), onCopyPath: vi.fn(), onCheckOutput: vi.fn(), onSubmitOutput: vi.fn(), onLaunchHandoff: vi.fn(),
    importController: { busy: false, copying: false, preview: null, error: null, rawError: null, message: null,
      choose: vi.fn(async () => undefined), confirm: vi.fn(async () => undefined), cancel: vi.fn() },
  }
}

describe('外部处理当前任务隔离', () => {
  it('未选中时仅显示两个可辨识导航，A/B详情各只有正确输入、帧数、完整目标与动作绑定', () => {
    const value = fixture()
    const { rerender } = render(<HandoffCenter {...value} />)
    const list = screen.getByRole('region', { name: '待外部处理任务' })
    expect(within(list).getAllByRole('button')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: '复制目标路径' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '选择处理好的文件' })).not.toBeInTheDocument()
    fireEvent.click(within(list).getByRole('button', { name: '查看外部任务：画质增强（2）' }))
    expect(value.onSelectNode).toHaveBeenCalledExactlyOnceWith('enhance-B')
    for (const [index, suffix] of ['A', 'B'].entries()) {
      const node = pair[index]!
      rerender(<HandoffCenter {...value} selectedNodeId={node.node_id} />)
      const selected = screen.getByRole('article', { name: `外部处理：画质增强（${index + 1}）` })
      expect(screen.getAllByRole('article')).toHaveLength(1)
      expect(within(selected).getByText(`input-${suffix}.mkv`)).toBeVisible()
      expect(within(selected).getByText(`预期输出：${index === 0 ? 899 : 902} 帧`)).toBeVisible()
      expect(within(selected).getByText(node.external_handoff!.output_targets[0]!.path)).toBeVisible()
      expect(within(selected).queryByText(`input-${suffix === 'A' ? 'B' : 'A'}.mkv`)).not.toBeInTheDocument()
      fireEvent.click(within(selected).getByRole('button', { name: '复制目标路径' }))
      expect(value.onCopyPath).toHaveBeenLastCalledWith(node.external_handoff!.output_targets[0]!.path)
      fireEvent.click(within(selected).getByRole('button', { name: '选择处理好的文件' }))
      expect(value.importController!.choose).toHaveBeenLastCalledWith(node, node.external_handoff!.output_targets[0], `画质增强（${index + 1}）`)
    }
    rerender(<HandoffCenter {...value} selectedNodeId="completed-source" />)
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '复制目标路径' })).not.toBeInTheDocument()
  })

  it('多输出任务只保留逐目标手动路径，不能发起局部导入', () => {
    const value = fixture()
    const node = pair[0]!
    const multiple = { ...node, external_handoff: { ...node.external_handoff!, output_targets: [
      ...node.external_handoff!.output_targets, { port_id: 'other', ordinal: null, path: 'D:\\synthetic\\task-A\\second.mkv' },
    ] } }
    render(<HandoffCenter {...value} selectedNodeId={node.node_id} waitingNodeRuns={[multiple]} />)
    expect(screen.queryByRole('button', { name: '选择处理好的文件' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '复制目标路径' })).toHaveLength(2)
    expect(screen.getByText('多输出任务请按各目标放置文件后统一检查。')).toBeVisible()
  })

  it('每次 import_id 重新确认覆盖，原始错误只在高级详情显示', () => {
    const value = fixture()
    const controller: HandoffImportController = { ...value.importController!, preview: {
      nodeTitle: '画质增强（1）', sourcePath: 'D:\\external\\finished-A.mkv', envelope: {
        contract_version: '0.3.0', selection_handle: 'selection-a', project_session_id: 'project-a', run_id: pair[0]!.run_id,
        node_run_id: pair[0]!.node_run_id, handoff_id: pair[0]!.external_handoff!.handoff_id, port_id: 'out', ordinal: null,
        import_id: 'first-import', source_name: 'finished-A.mkv', source_size: 1024,
        target_path: pair[0]!.external_handoff!.output_targets[0]!.path, replace_existing: true, expires_in_seconds: 300,
      },
    } }
    const { rerender } = render(<HandoffCenter {...value} selectedNodeId={pair[0]!.node_id} importController={controller} />)
    const dialog = screen.getByRole('dialog', { name: '确认导入外部处理文件' })
    expect(within(dialog).getByRole('button', { name: '确认复制到此任务' })).toBeDisabled()
    fireEvent.click(within(dialog).getByRole('checkbox', { name: '允许替换此任务已有目标文件' }))
    expect(within(dialog).getByRole('button', { name: '确认复制到此任务' })).toBeEnabled()
    rerender(<HandoffCenter {...value} selectedNodeId={pair[0]!.node_id} importController={{ ...controller,
      preview: { ...controller.preview!, envelope: { ...controller.preview!.envelope, import_id: 'second-import' } },
    }} />)
    expect(screen.getByRole('checkbox', { name: '允许替换此任务已有目标文件' })).not.toBeChecked()
    rerender(<HandoffCenter {...value} selectedNodeId={pair[0]!.node_id} importController={{ ...controller,
      preview: null, error: '当前任务未能导入', rawError: 'E_SYNTHETIC_UNKNOWN: diagnostic retained',
    }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('当前任务未能导入')
    expect(screen.getByText('E_SYNTHETIC_UNKNOWN: diagnostic retained')).not.toBeVisible()
  })
})
