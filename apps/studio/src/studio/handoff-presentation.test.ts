/** 同名序号和帧数摘要只读取正式展示资料，不改变 Graph、StudioState 或媒体约束。 */
import { describe, expect, it } from 'vitest'
import { distinctNodeLabels, handoffExpectedFrames, handoffSummary } from './handoff-presentation'
import { handoffDetailEnvelope, projectSnapshot } from './test-fixtures'

describe('交接任务可辨识展示', () => {
  it('仅重复名称增加稳定序号，原始节点与别名不改变', () => {
    const nodes = projectSnapshot.project.graph.nodes
    const original = structuredClone(nodes)
    const label = (node: typeof nodes[number]) => node.node_id === 'source' ? '导入视频' : '画质增强'
    const result = distinctNodeLabels(nodes, label)
    expect([...result.values()]).toEqual(['导入视频', '画质增强（1）', '画质增强（2）'])
    expect([...distinctNodeLabels(structuredClone(nodes), label)]).toEqual([...result])
    expect(nodes).toEqual(original)
    expect(distinctNodeLabels([nodes[1]!], label).get(nodes[1]!.node_id)).toBe('画质增强')
  })

  it('用户自带序号的别名不会与自动消歧结果产生新的同名', () => {
    const names = ['增强', '增强', '增强（1）']
    const result = distinctNodeLabels(projectSnapshot.project.graph.nodes, (node) => names[projectSnapshot.project.graph.nodes.indexOf(node)]!)
    expect(new Set(result.values()).size).toBe(3)
  })

  it('输入只显示 basename，帧数保留正式文本且拒绝不同handoff/Run的合同', () => {
    const detail = handoffDetailEnvelope()
    const node = detail.run.node_runs.find((item) => item.external_handoff)!
    const contract = { node_run_id: node.node_run_id, handoff_id: node.external_handoff!.handoff_id,
      input_artifact_id: null, title: '正式要求', fields: [{ label: '输出 exact N', value: '902' }] }
    const actual = { ...detail, handoff_contracts: [contract] }
    expect(handoffExpectedFrames(node, actual)).toEqual(['902'])
    const summary = handoffSummary(node, new Map(detail.artifacts.map((item) => [item.artifact_id, item])), actual)
    expect(summary.some((line) => line.includes('902 帧'))).toBe(true)
    expect(summary.some((line) => line.includes('C:\\synthetic'))).toBe(false)
    expect(handoffExpectedFrames(node, { ...detail, handoff_contracts: [{ ...contract, handoff_id: 'different' }] })).toEqual([])
    expect(handoffExpectedFrames(node, { ...actual, run: { ...detail.run, run_id: 'different' } })).toEqual([])
    expect(handoffExpectedFrames(node, { ...detail, handoff_contracts: [{ ...contract, fields: [{ label: '输出 exact N', value: '由节点声明' }] }] })).toEqual(['由节点声明'])
  })
})
