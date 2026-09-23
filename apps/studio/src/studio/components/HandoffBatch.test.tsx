/** 批量UI不隐藏真正的多目标，不以收件代替Submit；N=1新章节点仍使用章级体验。 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HandoffBatch } from './HandoffBatch'
import { batchNodeRun, batchObservation, batchPreview } from '../handoff-batch-fixtures'
import { isBatchHandoff } from '../handoff-batch-contracts'
import { handoffDetailEnvelope } from '../test-fixtures'
import type { HandoffBatchController } from '../use-handoff-batch'

afterEach(cleanup)
function controller(): HandoffBatchController { return { phase: 'idle', busy: false, available: true, observation: batchObservation,
  preview: null, message: null, error: null, rawError: null, choose: vi.fn(), confirm: vi.fn(), check: vi.fn(), refresh: vi.fn(), cancel: vi.fn() } }
function props(value = controller()) { return { controller: value, disabled: false, canPickFiles: true, canPickDirectory: true, canReveal: true,
  checked: false, submitting: false, submitReason: '请检查', readiness: null, failure: null, onReveal: vi.fn(), onCopyPath: vi.fn(), onSubmit: vi.fn() } }
describe('章级批量UI', () => {
  it('5份乱序外部后缀的服务建议直接选满，仍只在点击后提交映射', () => {
    const order = [10, 2, 4, 1, 3]
    const rows = [1, 2, 3, 4, 10].map((leaf) => ({ ...batchPreview.rows[0]!, port_id: `leaf-${leaf}`, display_label: `A 章 · 第 ${leaf} 段`, target_name: `Synthetic.A.leaf-${String(leaf).padStart(4, '0')}.enhancement.mov` }))
    const candidates = order.map((leaf) => ({ ...batchPreview.candidates[0]!, candidate_handle: `candidate-${leaf}`, name: `Synthetic.A.leaf-${String(leaf).padStart(4, '0')}_slp.mov` }))
    const matches = rows.map((row) => ({ port_id: row.port_id, candidate_handle: `candidate-${row.port_id.slice(5)}`, state: 'matched' as const, basis: 'chapter_leaf' as const, reason: '服务唯一章叶建议' }))
    const control = { ...controller(), phase: 'preview' as const, preview: { ...batchPreview, rows, candidates, matches } }
    render(<ol><HandoffBatch {...props(control)} /></ol>)
    const dialog = screen.getByRole('dialog', { name: '确认本章收件匹配' })
    const selectors = within(dialog).getAllByRole('combobox')
    rows.forEach((row, index) => expect(selectors[index]).toHaveValue(matches[index]!.candidate_handle))
    expect(control.confirm).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: '确认收件' }))
    expect(control.confirm).toHaveBeenCalledExactlyOnceWith(matches.map((match) => ({ port_id: match.port_id, candidate_handle: match.candidate_handle, overwrite: false })))
  })
  it('服务建议直接预选，手动改选不会被同票据重绘覆盖；路径折叠且角色明确', () => {
    const control = { ...controller(), phase: 'preview' as const, preview: batchPreview }
    const view = render(<ol><HandoffBatch {...props(control)} /></ol>)
    const dialog = screen.getByRole('dialog', { name: '确认本章收件匹配' })
    const selects = within(dialog).getAllByRole('combobox')
    expect(selects[0]).toHaveValue(batchPreview.candidates[0]!.candidate_handle)
    expect(within(dialog).getByText('A 章 · 第 1 段')).toBeVisible()
    expect(within(dialog).getAllByText(/复制到本章收件位置，保留所选原件/)).toHaveLength(2)
    expect(within(dialog).getAllByText('查看文件位置与角色')[0]!.closest('details')).not.toHaveAttribute('open')
    fireEvent.change(selects[0]!, { target: { value: '' } })
    view.rerender(<ol><HandoffBatch {...props({ ...control })} /></ol>)
    expect(within(dialog).getAllByRole('combobox')[0]).toHaveValue('')
    expect(within(dialog).getByText('已手动调整，请核对')).toBeVisible()
    expect(control.confirm).not.toHaveBeenCalled()
  })
  it('服务明确原位同一收件时不要求覆盖，也不声称会复制', () => {
    const preview = { ...batchPreview, rows: batchPreview.rows.map((row) => ({ ...row, collected: true })), complete: true,
      candidates: batchPreview.candidates.map((item, index) => ({ ...item, unchanged_port_ids: [batchPreview.rows[index]!.port_id] })) }
    render(<ol><HandoffBatch {...props({ ...controller(), phase: 'preview', preview })} /></ol>)
    const dialog = screen.getByRole('dialog', { name: '确认本章收件匹配' })
    expect(within(dialog).getAllByText(/文件已在本行收件位置，不复制、不移动/)).toHaveLength(2)
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '确认收件' })).toBeEnabled()
  })
  it('主界面提供多文件/目录/整章目录发现，不提供逐叶Submit', () => {
    const value = props()
    render(<ol><HandoffBatch {...value} /></ol>)
    expect(screen.getByText('已收 0/2')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '选择本章多个文件' }))
    fireEvent.click(screen.getByRole('button', { name: '选择本章文件目录' }))
    fireEvent.click(screen.getByRole('button', { name: '发现本章收件目录' }))
    expect(value.controller.choose).toHaveBeenNthCalledWith(1, 'open_files')
    expect(value.controller.choose).toHaveBeenNthCalledWith(2, 'select_directory')
    expect(value.controller.choose).toHaveBeenNthCalledWith(3, 'inbox')
    expect(screen.getByRole('button', { name: '检查本章全部输出' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '提交本章并继续' })).toBeDisabled()
    expect(value.onSubmit).not.toHaveBeenCalled()
  })
  it('预览逐项覆盖且一个来源不能匹配两个目标；取消不确认', () => {
    const control = { ...controller(), phase: 'preview' as const, preview: { ...batchPreview, rows: batchPreview.rows.map((row) => ({ ...row, collected: true, size: 1000 })), complete: true } }
    render(<ol><HandoffBatch {...props(control)} /></ol>)
    const dialog = screen.getByRole('dialog', { name: '确认本章收件匹配' })
    const confirm = within(dialog).getByRole('button', { name: '确认收件' })
    expect(confirm).toBeDisabled()
    for (const checkbox of within(dialog).getAllByRole('checkbox')) fireEvent.click(checkbox)
    expect(confirm).toBeEnabled()
    fireEvent.change(within(dialog).getAllByRole('combobox')[1]!, { target: { value: batchPreview.candidates[0]!.candidate_handle } })
    expect(confirm).toBeDisabled()
    expect(within(dialog).getByRole('alert')).toHaveTextContent('不能分配给多个目标')
    fireEvent.click(within(dialog).getByRole('button', { name: '取消，不复制' }))
    expect(control.cancel).toHaveBeenCalledOnce()
    expect(control.confirm).not.toHaveBeenCalled()
  })
  it('本章目录候选明确显示原位收纳，而外部候选保留原件', () => {
    const control = { ...controller(), phase: 'preview' as const, preview: { ...batchPreview, candidates: batchPreview.candidates.map((item, index) => ({ ...item, action: index ? 'copy' as const : 'move' as const })) } }
    render(<ol><HandoffBatch {...props(control)} /></ol>)
    expect(screen.getAllByRole('option', { name: /原位收纳，不保留原名/ })).toHaveLength(2)
    expect(screen.getAllByRole('option', { name: /复制，保留原件/ })).toHaveLength(2)
  })
  it('新N=1章节点使用batch；旧单输出与多输出能力不混淆', () => {
    const detail = handoffDetailEnvelope(), single = { ...batchNodeRun, external_handoff: { ...batchNodeRun.external_handoff, output_targets: batchNodeRun.external_handoff.output_targets.slice(0, 1) } }
    expect(isBatchHandoff(batchNodeRun, detail)).toBe(true)
    expect(isBatchHandoff(single, detail)).toBe(false)
    const first = detail.run.graph_snapshot.nodes[0]!
    const chapterDetail = { ...detail, run: { ...detail.run, graph_snapshot: { ...detail.run.graph_snapshot, nodes: [{ ...first,
      node_id: single.node_id, type_id: 'zniku.source-admitted.enhancement-batch.1', definition_version: '0.3.5' }] } } }
    expect(isBatchHandoff(single, chapterDetail)).toBe(true)
  })
})
