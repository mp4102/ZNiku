import { describe, expect, it } from 'vitest'
import { workspacePrimaryAction, type WorkspaceActionContext } from './workspace-primary-action'
const base: WorkspaceActionContext = {
  connected: true, loading: false, hasProject: true, historical: false, viewedState: null,
  viewedWaiting: 0, viewedFailed: 0, viewedRunning: 0, activeWaiting: 0, activeFailed: 0, activeRunning: 0,
  parameterDirty: false, saveError: false, diagnostics: 0, executionChanged: false, staleResults: false, runBlocked: false,
}
describe('主操作只路由当前查看对象', () => {
  it('旧 completed、另一 waiting 与未应用草稿共存时仍查看本次输出', () => {
    expect(workspacePrimaryAction({ ...base, historical: true, viewedState: 'completed', activeWaiting: 2, parameterDirty: true }).target).toBe('outputs')
  })
  it('当前图的草稿不成为已有 handoff 的新提交条件', () => {
    expect(workspacePrimaryAction({ ...base, activeWaiting: 2, parameterDirty: true }).label).toBe('处理外部文件（2）')
  })
  it.each([{ executionChanged: true }, { staleResults: true }])('上次完成不是已变更当前图的完成：%s', (change) => {
    expect(workspacePrimaryAction({ ...base, viewedState: 'completed', ...change }).target).toBe('run')
  })
  it.each([
    [{ parameterDirty: true }, 'settings'], [{ saveError: true }, 'save'], [{ diagnostics: 1 }, 'problems'],
    [{ historical: true, viewedFailed: 1, activeWaiting: 1 }, 'problems'],
    [{ viewedFailed: 1, viewedState: 'failed' }, 'problems'],
    [{ viewedFailed: 1, viewedState: 'failed', executionChanged: true }, 'run'],
    [{ historical: true, viewedRunning: 1, activeFailed: 1 }, 'tasks'],
    [{ connected: false, historical: true, viewedState: 'completed' }, 'reconnect'],
  ] as const)('资格和视图分离：%s', (change, expected) => {
    expect(workspacePrimaryAction({ ...base, ...change }).target).toBe(expected)
  })
  it('启动仍使用调用者传入的门禁，不自行放行', () => {
    expect(workspacePrimaryAction({ ...base, runBlocked: true, runReason: '尚未保存' })).toEqual({ target: 'run', label: '开始处理', disabled: true, reason: '尚未保存' })
  })
})
