/** 同比例阶段更新可展示；历史、外部等待及未完成的总量不会被伪造成完成。 */
import { describe, expect, it } from 'vitest'
import { progressStageLabel } from './progress-stage'
import { handoffRun } from './test-fixtures'
import type { WorkflowProgressData } from '../model'

describe('正式进度阶段展示', () => {
  const running = { ...handoffRun().node_runs[0]!, state: 'running' as const }
  const measured: WorkflowProgressData = { mode: 'determinate', fraction: 1, measurement: null, elapsed: null }
  it('优先展示服务端阶段，不按百分比猜校验或登记阶段', () => {
    expect(progressStageLabel({ ...measured, stage: '检查 FI 上下文输出' }, running)).toBe('检查 FI 上下文输出')
    expect(progressStageLabel(measured, running)).toBe('媒体处理已到总量，等待完成确认')
    expect(progressStageLabel({ ...measured, fraction: 0.5 }, running)).toBeNull()
  })
  it.each(['completed', 'failed', 'waiting_external', 'pending'] as const)('%s 不复用 running 阶段', (state) => {
    expect(progressStageLabel({ ...measured, stage: '正在组装' }, { ...running, state })).toBeNull()
  })
})
