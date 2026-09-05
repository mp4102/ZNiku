/** 检查结果不得串到其他交接、目标路径、文件观察或未通过的产物。 */
import { describe, expect, it } from 'vitest'
import { isFullCheck, readinessMatchesHandoff, sameObservedOutputs } from './handoff-check'
import { handoffDetailEnvelope, handoffReadinessEnvelope } from './test-fixtures'

const waiting = handoffDetailEnvelope().run.node_runs.find((node) => node.state === 'waiting_external')!
describe('外部检查页面绑定', () => {
  it('只接受完整检查且 exact 当前交接和声明目标', () => {
    const checked = handoffReadinessEnvelope('probe_passed', true)
    expect(isFullCheck(checked, waiting)).toBe(true)
    expect(isFullCheck(handoffReadinessEnvelope('present', false), waiting)).toBe(false)
    expect(isFullCheck({ ...checked, probe_requested: false }, waiting)).toBe(false)
    expect(readinessMatchesHandoff({ ...checked, handoff_id: 'another' }, waiting)).toBe(false)
    expect(readinessMatchesHandoff({ ...checked, targets: [] }, waiting)).toBe(false)
    expect(readinessMatchesHandoff({ ...checked, targets: checked.targets.map((target) => ({ ...target, path: 'elsewhere' })) }, waiting)).toBe(false)
    expect(readinessMatchesHandoff(checked, { ...waiting, state: 'completed' })).toBe(false)
  })
  it('被动 present 保留检查说明，但文件变化、缺失或检查失败撤销资格', () => {
    const checked = handoffReadinessEnvelope('probe_passed', true)
    expect(sameObservedOutputs(checked, handoffReadinessEnvelope('present', false))).toBe(true)
    expect(sameObservedOutputs(checked, handoffReadinessEnvelope('missing', false))).toBe(false)
    expect(sameObservedOutputs(checked, handoffReadinessEnvelope('probe_failed', true))).toBe(false)
    for (const field of ['size', 'mtime_ns'] as const) {
      expect(sameObservedOutputs(checked, { ...checked, targets: checked.targets.map((target) => ({ ...target, [field]: (target[field] ?? 0) + 1 })) })).toBe(false)
    }
  })
})
