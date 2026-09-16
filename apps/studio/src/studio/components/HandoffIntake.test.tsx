/** 两种来件方式共用先检查后收纳，没有重复确认弹窗和虚假媒体进度。 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HandoffIntake } from './HandoffIntake'
import type { HandoffIntakeController } from '../use-handoff-intake'
import { intakeJob, intakeObservation, intakeSelection, intakeNodeRun, intakeDetail } from '../handoff-intake-fixtures'
import { isIntakeHandoff } from '../handoff-intake-contracts'
afterEach(cleanup)
function controller(): HandoffIntakeController { return { phase: 'idle', available: true, busy: false, observation: intakeObservation,
  selected: intakeSelection, job: null, recoveredPath: null, message: null, error: null, rawError: null,
  refresh: vi.fn(), choose: vi.fn(), check: vi.fn(), checkPublished: vi.fn(), submit: vi.fn() } }
function props(control = controller()) { return { controller: control, disabled: false, canPick: true, canReveal: true, onReveal: vi.fn(), onCopyPath: vi.fn() } }
describe('单视频交回界面', () => {
  it('两种入口直接显示来源和归档目标，没有复制确认弹窗', () => {
    const value = props()
    render(<ol><HandoffIntake {...value} /></ol>)
    fireEvent.click(screen.getByRole('button', { name: '选择处理好的文件' }))
    expect(value.controller.choose).toHaveBeenCalledOnce()
    expect(screen.getByText(intakeSelection.source_path)).toBeVisible()
    expect(screen.getByText(intakeSelection.incoming_path)).toBeVisible()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('button', { name: '提交并继续' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '检查并导入' }))
    expect(value.controller.check).toHaveBeenCalledWith(false)
    expect(value.controller.submit).not.toHaveBeenCalled()
  })
  it('覆盖选择跟随当前票据，换文件后必须重新确认', () => {
    const control = { ...controller(), selected: { ...intakeSelection, replace_existing: true } }
    const { rerender } = render(<ol><HandoffIntake {...props(control)} /></ol>)
    expect(screen.getByRole('button', { name: '检查并导入' })).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button', { name: '检查并导入' })).toBeEnabled()
    rerender(<ol><HandoffIntake {...props({ ...control, selected: { ...control.selected, ticket_id: 'other-ticket' } })} /></ol>)
    expect(screen.getByRole('checkbox')).not.toBeChecked()
  })
  it('检查不显示百分比，只有实际复制字节显示进度', () => {
    const control = { ...controller(), phase: 'checking' as const, busy: true }
    const { rerender } = render(<ol><HandoffIntake {...props(control)} /></ol>)
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.getByText(/正在原位置检查/)).toBeVisible()
    rerender(<ol><HandoffIntake {...props({ ...control, phase: 'copying', job: { ...intakeJob, phase: 'copying', ready_id: null, bytes_done: 512 } })} /></ol>)
    expect(screen.getByRole('progressbar', { name: '文件复制进度' })).toHaveAttribute('value', '512')
    expect(screen.getByRole('progressbar')).toHaveAttribute('max', '1024')
  })
  it('检查成功不会自动提交，仅独立点击才提交', () => {
    const value = props({ ...controller(), job: intakeJob })
    render(<ol><HandoffIntake {...value} /></ol>)
    expect(value.controller.submit).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '检查并导入' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '提交并继续' }))
    expect(value.controller.submit).toHaveBeenCalledOnce()
  })
  it('新交互只由Python明确能力开启，旧任务不按业务type猜测', () => {
    const contract = { node_run_id: intakeNodeRun.node_run_id, handoff_id: intakeNodeRun.external_handoff!.handoff_id,
      input_artifact_id: null, title: '外部处理', fields: [] }
    expect(isIntakeHandoff(intakeNodeRun, { ...intakeDetail, handoff_contracts: [contract] })).toBe(false)
    expect(isIntakeHandoff(intakeNodeRun, { ...intakeDetail, handoff_contracts: [{ ...contract, intake_supported: true }] })).toBe(true)
    expect(isIntakeHandoff(intakeNodeRun, { ...intakeDetail, handoff_contracts: [{ ...contract, handoff_id: 'wrong', intake_supported: true }] })).toBe(false)
  })
})
