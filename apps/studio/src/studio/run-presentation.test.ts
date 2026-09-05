/** 文案投影不改写运行数据，未知分类不能被误解释为成功。 */
import { describe, expect, it } from 'vitest'
import { creatorElapsedLabel, failurePresentation, nodeStateLabel, runHistoryLabel } from './run-presentation'
import { handoffSummary } from './test-fixtures'

describe('运行展示词汇', () => {
  it('耗时投影只翻译已测时间，未知格式不猜倒计时', () => {
    expect(creatorElapsedLabel('elapsed 28s')).toBe('已用时 28 秒')
    expect(creatorElapsedLabel('elapsed 2m 8s')).toBe('已用时 2 分钟 8 秒')
    expect(creatorElapsedLabel('elapsed 3h 2m', true)).toBe('已等待 3 小时 2 分钟')
    expect(creatorElapsedLabel('已等待 5 分钟', true)).toBe('已等待 5 分钟')
    expect(creatorElapsedLabel('eta 15s', true)).toBe('等待时长暂不可用')
    expect(creatorElapsedLabel('elapsed 3s 5m')).toBe('耗时暂不可用')
  })
  it('状态含等待、失败、复用和失效；未知或原型名安全回退', () => {
    expect(nodeStateLabel('waiting_external')).toBe('等待外部处理')
    expect(nodeStateLabel('stale')).toBe('结果需要更新')
    expect(nodeStateLabel('reused')).toBe('已复用完成结果')
    for (const value of ['alien_state', '__proto__', 'toString']) expect(nodeStateLabel(value)).toBe('状态暂时无法识别')
  })
  it('历史只显示时间、友好目标和结果，不泄漏 ID 或伪造总体百分比', () => {
    const summary = { ...handoffSummary(), target_mode: 'selected' as const, selected_targets: ['private-node-id'] }
    const before = JSON.stringify(summary)
    const label = runHistoryLabel(summary, () => '画质增强')
    expect(label).toContain('2026')
    expect(label).toContain('处理到 画质增强')
    expect(label).toContain('等待外部处理')
    expect(label).not.toMatch(/private-node-id|00000000|%|snapshot/)
    expect(JSON.stringify(summary)).toBe(before)
  })
  it('稳定错误给原因、保留和恢复，但不输出命令资格', () => {
    const presentation = failurePresentation('E_REQUIRED_INPUT_MISSING')
    expect(presentation).toMatchObject({ known: true, title: '还缺少输入连接' })
    expect(presentation.cause).toBeTruthy()
    expect(presentation.preserved).toContain('运行服务')
    expect(presentation.recovery).toContain('连接')
    expect(Object.keys(presentation).sort()).toEqual(['cause', 'known', 'preserved', 'recovery', 'title'])
  })
  it('可重跑 Run 仍为 running 时，失败步骤在用户记录中提示需要处理问题', () => {
    const summary = { ...handoffSummary(), state_counts: { completed: 3, pending: 0, running: 0, waiting_external: 0, failed: 1 } }
    expect(runHistoryLabel(summary)).toContain('需要处理问题')
    expect(summary.state).toBe('running')
  })
  it.each(['unexpected', 'toString', '__proto__'])('未知错误 %s 保守回退，不按原始消息猜测成功', (code) => {
    expect(failurePresentation(code)).toMatchObject({ known: false, title: '出现尚未识别的问题' })
    expect(failurePresentation(code).recovery).toContain('原始信息')
  })
})
