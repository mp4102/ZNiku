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
  it('只翻译已知原片 cadence 错误，不要求重交 MR、换容器或自动改帧', () => {
    const raw = 'E_RUNNER_VALIDATION_REJECTED: E_AV27_SOURCE_FPS_AMBIGUOUS: Source 全片 cadence 置信度不足'
    const explanation = failurePresentation('validation_failed', raw)
    expect(explanation.title).toBe('原片帧率或时间轴未通过检查')
    expect(explanation.preserved).toContain('不会自动改速、增加或删除帧')
    expect(explanation.recovery).toContain('不要仅改文件后缀或重复交付修复文件来绕过检查')
    expect(failurePresentation('E_AV27_SOURCE_FPS_AMBIGUOUS')).toEqual(explanation)
    for (const message of [`untrusted ${raw}`, raw.replace('FPS_AMBIGUOUS:', 'FPS_AMBIGUOUS_OTHER:'), '其他检查失败']) {
      expect(failurePresentation('validation_failed', message).title).toBe('处理输出未通过检查')
    }
    expect(failurePresentation('execution_error', raw).title).toBe('这一步处理失败')
  })
  it.each(['unexpected', 'toString', '__proto__'])('未知错误 %s 保守回退，不按原始消息猜测成功', (code) => {
    expect(failurePresentation(code)).toMatchObject({ known: false, title: '出现尚未识别的问题' })
    expect(failurePresentation(code).recovery).toContain('原始信息')
  })
  it.each(['E_PROJECT_SAVE_FAILED', 'E_PROJECT_LOAD_FAILED', 'E_PROJECT_STORAGE_READ', 'E_PROJECT_STORAGE_WRITE', 'E_RUNTIME_STORAGE_UNAVAILABLE', 'E_RUNTIME_STORAGE_WRITE'])('存储错误 %s 不推断损坏或空间原因，提示保留媒体和有限恢复', (code) => {
    const presentation = failurePresentation(code, 'unable to open database file')
    expect(presentation.known).toBe(true)
    expect(presentation.recovery).toContain('配额')
    expect(presentation.recovery).toContain('重新打开工程')
    expect(presentation.recovery).toContain('仅从失败步骤重跑')
    expect(presentation.preserved).toContain('不代表最近的状态已保存')
    expect(presentation.cause).not.toMatch(/磁盘已满|数据库已损坏|已全部保存/)
  })
  it('状态未能持久化只翻译服务结论，不把旧百分比当运行证明', () => {
    const presentation = failurePresentation('E_SERVICE_STORAGE_RECOVERY_REQUIRED')
    expect(presentation.title).toBe('处理已停止，工程状态待恢复')
    expect(presentation.cause).toContain('百分比可能是旧记录')
    expect(presentation.recovery).toContain('确认正式状态后')
  })
  it('已落盘 execution_error 的已知存储前缀翻译原因，不按任意消息猜测', () => {
    expect(failurePresentation('execution_error', 'E_RUNTIME_STORAGE_UNAVAILABLE: synthetic read failure').title).toBe('暂时无法读取工程记录')
    expect(failurePresentation('execution_error', 'E_PROJECT_SAVE_FAILED: synthetic write failure').title).toBe('工程记录未能保存')
    for (const message of ['arbitrary E_RUNTIME_STORAGE_UNAVAILABLE: text', 'E_RUNTIME_STORAGE_UNAVAILABLE_OTHER: text']) {
      expect(failurePresentation('execution_error', message).title).toBe('这一步处理失败')
    }
  })
  it('未确认进程退出时不鼓励重新运行或重复提交', () => {
    const presentation = failurePresentation('E_SERVICE_PROCESS_STOP_UNCONFIRMED')
    expect(presentation.recovery).toContain('确认原处理进程已退出')
    expect(presentation.recovery).toContain('不要立即重跑或重复提交')
  })
})
