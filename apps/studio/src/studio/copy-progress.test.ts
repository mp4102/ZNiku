/** 字节展示仅识别通用复制执行器；没有可信测量时不猜速率或完成状态。 */
import { describe, expect, it } from 'vitest'
import type { WorkflowProgressData } from '../model'
import { copyProgressOperation, copyProgressPresentation } from './copy-progress'
import { handoffRun, sourceDefinition } from './test-fixtures'

const nodeRun = { ...handoffRun().node_runs[0]!, state: 'running' as const, started_at: '2026-09-01T00:00:00Z', ended_at: null }
const progress: WorkflowProgressData = { operation: 'copy', mode: 'determinate', fraction: .5, elapsed: null,
  measurement: { node_run_id: nodeRun.node_run_id, current: 1024 ** 3, total: 2 * 1024 ** 3, unit: 'bytes', fraction: .5, observed_at: '2026-09-01T00:00:10Z' } }

describe('通用文件复制进度', () => {
  it('只识别正式 OutputFile copy 参数，不按节点名称或 bytes 单位猜业务', () => {
    const output = { ...sourceDefinition, executor: { kind: 'python' as const, adapter: 'zniku.media.adapters:output_file', output_paths: [] } }
    expect(copyProgressOperation(output, { mode: 'copy' })).toBe('copy')
    expect(copyProgressOperation(output, { mode: 'reference' })).toBeUndefined()
    expect(copyProgressOperation(output)).toBeUndefined()
    expect(copyProgressOperation(sourceDefinition, { mode: 'copy' })).toBeUndefined()
    expect(copyProgressPresentation({ ...progress, operation: undefined }, nodeRun)).toBeNull()
  })
  it('展示易读容量与步骤平均速率，不使用浏览器时间或预测完成时间', () => {
    const before = JSON.stringify(progress)
    expect(copyProgressPresentation(progress, nodeRun)).toMatchObject({ label: '正在复制文件',
      volume: '已复制 1.00 GiB / 2.00 GiB', averageRate: '步骤平均 102.40 MiB/s' })
    expect(JSON.stringify(progress)).toBe(before)
  })
  it('总量达到100%仍在running时只等待完成确认', () => {
    expect(copyProgressPresentation({ ...progress, measurement: { ...progress.measurement!, current: 2 * 1024 ** 3, fraction: 1 } }, nodeRun)?.label).toBe('字节已复制，等待完成确认')
    expect(copyProgressPresentation(progress, { ...nodeRun, state: 'completed' })).toBeNull()
    expect(copyProgressPresentation(progress, { ...nodeRun, state: 'failed' })).toBeNull()
  })
  it.each([null, 'invalid-time', '2026-09-01T00:00:10Z', '2026-09-01T00:00:11Z'])('缺少可信起始时间 %s 不显示速率', (started_at) => {
    expect(copyProgressPresentation(progress, { ...nodeRun, started_at })?.averageRate).toBeNull()
  })
  it('无测量、跨attempt、异常时间或非bytes不误报复制量', () => {
    expect(copyProgressPresentation({ ...progress, measurement: null }, nodeRun)).toBeNull()
    expect(copyProgressPresentation({ ...progress, measurement: { ...progress.measurement!, node_run_id: 'another-attempt' } }, nodeRun)).toBeNull()
    expect(copyProgressPresentation({ ...progress, measurement: { ...progress.measurement!, unit: 'frames' } }, nodeRun)).toBeNull()
    expect(copyProgressPresentation({ ...progress, measurement: { ...progress.measurement!, current: 3 * 1024 ** 3 } }, nodeRun)).toBeNull()
    expect(copyProgressPresentation({ ...progress, measurement: { ...progress.measurement!, observed_at: 'invalid' } }, nodeRun)?.averageRate).toBeNull()
  })
})
