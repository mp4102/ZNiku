/** 显式准入重试的实例与最新 attempt 门禁；不模拟媒体合法性。 */
import { describe, expect, it } from 'vitest'
import { runningProgressDetail } from './test-fixtures'
import { failedColorAdmissionNode, failedPreparationNode } from './preparation-rerun'
import { COLOR_PREPARED_VERSION } from './prepared-color-contracts'
import { WORK_SOURCE_VERSION } from './working-source-contracts'

function fixture() {
  const original = runningProgressDetail(.1), node = original.run.graph_snapshot.nodes[0]!, attempt = original.run.node_runs[0]!
  const detail = { ...original, run: { ...original.run,
    graph_snapshot: { ...original.run.graph_snapshot, nodes: [{ ...node, node_id: 'renamed-admission', type_id: 'zniku.source_preparation.admission', definition_version: COLOR_PREPARED_VERSION }] },
    node_runs: [{ ...attempt, node_id: 'renamed-admission', definition_version: COLOR_PREPARED_VERSION, state: 'failed' as const }],
  } }
  return { detail, intent: { run_id: detail.run.run_id }, projectId: detail.run.project_id }
}
describe('失败准入目标绑定', () => {
  it('普通准备重试绑定后端返回的实际节点与最新失败attempt，不只认Admission或模板名', () => {
    const { detail, projectId } = fixture(), node = detail.run.graph_snapshot.nodes[0]!, attempt = detail.run.node_runs[0]!
    const workDetail = { ...detail, run: { ...detail.run, graph_snapshot: { ...detail.run.graph_snapshot,
      nodes: [{ ...node, type_id: 'zniku.source_preparation.video_prepare.frame_retime', definition_version: WORK_SOURCE_VERSION }] },
      node_runs: [{ ...attempt, definition_version: WORK_SOURCE_VERSION }] } }
    const intent = { contract_version: WORK_SOURCE_VERSION, run_id: detail.run.run_id, node_id: node.node_id, node_run_id: attempt.node_run_id }
    expect(failedPreparationNode(workDetail, intent, projectId)).toBe(node.node_id)
    expect(() => failedPreparationNode(workDetail, { ...intent, node_run_id: 'wrong-attempt' }, projectId)).toThrow('目标')
    expect(() => failedPreparationNode(workDetail, { ...intent, node_id: 'wrong-node' }, projectId)).toThrow('目标')
    expect(() => failedPreparationNode(workDetail, { ...intent, run_id: 'wrong-run' }, projectId)).toThrow('身份')
    expect(() => failedPreparationNode(workDetail, intent, 'wrong-project')).toThrow('身份')
    expect(() => failedPreparationNode({ ...workDetail, run: { ...workDetail.run, node_runs: [{ ...attempt, definition_version: WORK_SOURCE_VERSION, state: 'completed' }] } }, intent, projectId)).toThrow('目标')
  })
  it('按实际exact节点绑定改名实例，不依赖向导node_id', () => {
    const { detail, intent, projectId } = fixture()
    expect(failedColorAdmissionNode(detail, intent, projectId)).toBe('renamed-admission')
  })
  it('拒绝迟到Run和其他工程响应', () => {
    const { detail, intent, projectId } = fixture()
    expect(() => failedColorAdmissionNode(detail, { run_id: 'another-run' }, projectId)).toThrow('身份已变化')
    expect(() => failedColorAdmissionNode(detail, intent, 'another-project')).toThrow('身份已变化')
  })
  it('旧合同或重复准入实例不猜目标', () => {
    const { detail, intent, projectId } = fixture(), node = detail.run.graph_snapshot.nodes[0]!
    expect(() => failedColorAdmissionNode({ ...detail, run: { ...detail.run, graph_snapshot: { ...detail.run.graph_snapshot, nodes: [{ ...node, definition_version: '0.3.4' }] } } }, intent, projectId)).toThrow('无法唯一绑定')
    expect(() => failedColorAdmissionNode({ ...detail, run: { ...detail.run, graph_snapshot: { ...detail.run.graph_snapshot, nodes: [node, { ...node, node_id: 'another' }] } } }, intent, projectId)).toThrow('无法唯一绑定')
  })
  it.each(['running', 'completed', 'waiting_external'] as const)('最新attempt已%s时不重试历史失败', (state) => {
    const { detail, intent, projectId } = fixture(), old = detail.run.node_runs[0]!
    expect(() => failedColorAdmissionNode({ ...detail, run: { ...detail.run, node_runs: [{ ...old, attempt: old.attempt + 1, state }, old] } }, intent, projectId)).toThrow('已不处于失败状态')
  })
})
