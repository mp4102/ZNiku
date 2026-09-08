import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { handoffDetailEnvelope, handoffReadinessEnvelope } from '../test-fixtures'
import { HandoffCenter, handoffResourceKey, type HandoffCenterProps } from './HandoffCenter'

afterEach(cleanup)

const detail = handoffDetailEnvelope()
const waiting = detail.run.node_runs.find((item) => item.state === 'waiting_external')!
const key = handoffResourceKey(waiting.run_id, waiting.node_run_id, waiting.external_handoff!.handoff_id)
const present = handoffReadinessEnvelope('present', false)
const passed = handoffReadinessEnvelope('probe_passed', true)
function props(overrides: Partial<HandoffCenterProps> = {}): HandoffCenterProps {
  return {
    waitingNodeRuns: [waiting], selectedNodeId: waiting.node_id, nodeLabel: () => '画质增强', detail,
    artifactsById: new Map(detail.artifacts.map((item) => [item.artifact_id, item])),
    readiness: new Map([[waiting.node_run_id, present]]), checkedOutputs: new Map(),
    lastFullPrecheckFailures: new Map(), checkingNodeRunId: null, submittingNodeRunId: null,
    mutationBlocked: false, readinessStale: false, canRevealHandoff: true, canOpenHandoffInput: true,
    onSelectNode: vi.fn(), onCopyPath: vi.fn(), onCheckOutput: vi.fn(), onSubmitOutput: vi.fn(), onLaunchHandoff: vi.fn(),
    ...overrides,
  }
}

describe('创作者外部处理助手', () => {
  it('发现文件只允许检查，不自动提交；正式检查通过后仍需要独立点击提交', () => {
    const value = props()
    const { rerender } = render(<HandoffCenter {...value} />)
    expect(screen.getByRole('button', { name: '检查输出' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '提交并继续' })).toBeDisabled()
    expect(value.onSubmitOutput).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '检查输出' }))
    expect(value.onCheckOutput).toHaveBeenCalledExactlyOnceWith(waiting)
    expect(value.onSubmitOutput).not.toHaveBeenCalled()
    rerender(<HandoffCenter {...value} checkedOutputs={new Map([[key, passed]])} />)
    expect(screen.getByRole('button', { name: '提交并继续' })).toBeEnabled()
    expect(value.onSubmitOutput).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '提交并继续' }))
    expect(value.onSubmitOutput).toHaveBeenCalledExactlyOnceWith(waiting)
  })

  it('被动 present 不覆盖已通过检查，但文件替换会撤销提交资格', () => {
    const value = props({ checkedOutputs: new Map([[key, passed]]) })
    const { rerender } = render(<HandoffCenter {...value} />)
    expect(screen.getByRole('status')).toHaveTextContent('完整检查通过')
    const changed = { ...present, targets: present.targets.map((target) => ({ ...target, mtime_ns: target.mtime_ns! + 1 })) }
    rerender(<HandoffCenter {...value} readiness={new Map([[waiting.node_run_id, changed]])} />)
    expect(screen.getByRole('button', { name: '提交并继续' })).toBeDisabled()
    expect(screen.getByText('目标文件或检查结果已变化，请重新检查输出。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '检查输出' })).toBeEnabled()
  })

  it('不同交接的检测与检查结果不能启用任何提交', () => {
    render(<HandoffCenter {...props({
      readiness: new Map([[waiting.node_run_id, { ...present, handoff_id: 'another-handoff' }]]),
      checkedOutputs: new Map([[key, { ...passed, handoff_id: 'another-handoff' }]]),
    })} />)
    expect(screen.getByRole('button', { name: '检查输出' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '提交并继续' })).toBeDisabled()
  })

  it('不合格文件保留失败详情并允许重新检查，原始错误仅在高级详情', () => {
    const failed = handoffReadinessEnvelope('probe_failed', true)
    render(<HandoffCenter {...props({ readiness: new Map([[waiting.node_run_id, failed]]), lastFullPrecheckFailures: new Map([[key, failed]]) })} />)
    expect(screen.getByRole('button', { name: '检查输出' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '提交并继续' })).toBeDisabled()
    expect(screen.getByText(/上次输出未通过完整检查/)).toBeVisible()
    for (const raw of screen.getAllByText(/synthetic probe failed/)) expect(raw).not.toBeVisible()
  })

  it('系统动作只提交当前交接的输入/目录引用，复制保留完整路径', () => {
    const value = props()
    render(<HandoffCenter {...value} />)
    const artifact = detail.artifacts[0]!
    fireEvent.click(screen.getByRole('button', { name: '打开输入' }))
    expect(value.onLaunchHandoff).toHaveBeenLastCalledWith(waiting, 'open_with_system_player', { role: 'input_artifact', artifact_id: artifact.artifact_id })
    fireEvent.click(screen.getByRole('button', { name: '打开工作目录' }))
    expect(value.onLaunchHandoff).toHaveBeenLastCalledWith(waiting, 'reveal_in_file_manager', { role: 'work_directory' })
    fireEvent.click(screen.getByRole('button', { name: '复制输入路径' }))
    expect(value.onCopyPath).toHaveBeenLastCalledWith(artifact.path)
    fireEvent.click(screen.getByRole('button', { name: '复制目标路径' }))
    expect(value.onCopyPath).toHaveBeenLastCalledWith(waiting.external_handoff!.output_targets[0]!.path)
  })

  it('手工任务只显示已等待时间，技术身份隐藏且没有百分比/ETA/倒计时', () => {
    render(<HandoffCenter {...props({ waitingNodeRuns: [{ ...waiting, progress: 0.95 }] })} />)
    expect(screen.getByRole('article', { name: '外部处理：画质增强' })).toBeInTheDocument()
    expect(screen.getByText('output.mkv')).toBeVisible()
    expect(screen.getByText(waiting.run_id)).not.toBeVisible()
    expect(screen.queryByText(/95%|ETA|倒计时/)).not.toBeInTheDocument()
    expect(screen.getByText(/已等待/)).toBeVisible()
  })

  it.each(['missing', 'empty'] as const)('%s 文件、离线和在途操作都给出明确禁用理由', (state) => {
    const value = props({ readiness: new Map([[waiting.node_run_id, handoffReadinessEnvelope(state)]]) })
    const { rerender } = render(<HandoffCenter {...value} />)
    expect(screen.getByRole('button', { name: '检查输出' })).toBeDisabled()
    expect(screen.getByText('请先在外部工具中完成输出，保存为下方目标文件。')).toBeVisible()
    rerender(<HandoffCenter {...props({ readinessStale: true, checkedOutputs: new Map([[key, passed]]) })} />)
    expect(screen.getByRole('button', { name: '提交并继续' })).toBeDisabled()
    expect(screen.getByText(/文件检测暂时离线/)).toBeVisible()
    rerender(<HandoffCenter {...props({ checkingNodeRunId: waiting.node_run_id })} />)
    expect(screen.getByRole('button', { name: '正在完整检查…' })).toBeDisabled()
    expect(screen.getByText('正在检查或提交，请等待本次操作完成。')).toBeVisible()
  })
})
