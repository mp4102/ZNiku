import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthoringSaveController, AuthoringSaveSessionChangedError } from './authoring-save'
import type { AuthoringSaveReceipt } from './authoring-save'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolveValue, rejectValue) => {
    resolve = resolveValue
    reject = rejectValue
  })
  return { promise, resolve, reject }
}

async function settle() {
  await Promise.resolve()
  await Promise.resolve()
}

describe('authoring 自动保存并发', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('快速编辑只在最后一次 debounce 到期后保存最新值', async () => {
    const save = vi.fn().mockResolvedValue({ storage_revision: 4 })
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 3, save, debounceMs: 100 })
    controller.update(1)
    await vi.advanceTimersByTimeAsync(80)
    controller.update(2)
    await vi.advanceTimersByTimeAsync(80)
    expect(save).not.toHaveBeenCalled()
    controller.update(3)
    await vi.advanceTimersByTimeAsync(100)
    expect(save).toHaveBeenCalledExactlyOnceWith(3, 3)
    expect(controller.getSnapshot()).toMatchObject({ value: 3, storageRevision: 4, dirty: false, saving: false })
  })

  it('单请求在途时合并后续编辑，旧 ack 不覆盖最新本地值', async () => {
    const first = deferred<AuthoringSaveReceipt>()
    const second = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const onSaved = vi.fn()
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 1, save, onSaved, debounceMs: 100 })
    controller.update(1)
    await vi.advanceTimersByTimeAsync(100)
    controller.update(2)
    controller.update(3)
    await vi.advanceTimersByTimeAsync(500)
    expect(save).toHaveBeenCalledTimes(1)
    first.resolve({ storage_revision: 2 })
    await settle()
    expect(save).toHaveBeenNthCalledWith(2, 3, 2)
    expect(controller.getSnapshot()).toMatchObject({ value: 3, storageRevision: 2, dirty: true, saving: true })
    expect(onSaved).toHaveBeenCalledWith(1, { storage_revision: 2 })
    second.resolve({ storage_revision: 3 })
    await settle()
    expect(controller.getSnapshot()).toMatchObject({ value: 3, dirty: false, storageRevision: 3 })
  })

  it('在途期间的新编辑仍遵守自己的 debounce', async () => {
    const first = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({ storage_revision: 2 })
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save, debounceMs: 100 })
    controller.update(1)
    await vi.advanceTimersByTimeAsync(100)
    controller.update(2)
    first.resolve({ storage_revision: 1 })
    await settle()
    expect(save).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(99)
    expect(save).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(save).toHaveBeenNthCalledWith(2, 2, 1)
  })

  it('手工 flush 立即发送且所有调用等待期间产生的最新编辑', async () => {
    const first = deferred<AuthoringSaveReceipt>()
    const second = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save, debounceMs: 100 })
    controller.update(1)
    const done = vi.fn()
    const flush1 = controller.flush().then(done)
    const flush2 = controller.flush()
    expect(save).toHaveBeenCalledExactlyOnceWith(1, 0)
    controller.update(2)
    first.resolve({ storage_revision: 1 })
    await settle()
    expect(done).not.toHaveBeenCalled()
    expect(save).toHaveBeenNthCalledWith(2, 2, 1)
    second.resolve({ storage_revision: 2 })
    await Promise.all([flush1, flush2])
    expect(done).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot().dirty).toBe(false)
  })

  it('在途 Undo 回到原 ack 仍写回原值，不能误报已保存', async () => {
    const first = deferred<AuthoringSaveReceipt>()
    const second = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const controller = new AuthoringSaveController({ initialValue: 'a', initialRevision: 7, save })
    controller.update('b')
    const flushed = controller.flush()
    controller.update('a')
    expect(controller.getSnapshot().dirty).toBe(true)
    first.resolve({ storage_revision: 8 })
    await settle()
    expect(save).toHaveBeenNthCalledWith(2, 'a', 8)
    expect(controller.getSnapshot().dirty).toBe(true)
    second.resolve({ storage_revision: 9 })
    await flushed
    expect(controller.getSnapshot()).toMatchObject({ value: 'a', storageRevision: 9, dirty: false })
  })

  it('发送前 Undo 回到 ack 取消待保存，逻辑相同快照不会重复写入', async () => {
    const save = vi.fn()
    const controller = new AuthoringSaveController({
      initialValue: { text: 'a' }, initialRevision: 0, save,
      equals: (left, right) => left.text === right.text,
    })
    controller.update({ text: 'b' })
    controller.update({ text: 'a' })
    await controller.flush()
    await vi.runAllTimersAsync()
    expect(save).not.toHaveBeenCalled()
    expect(controller.getSnapshot().dirty).toBe(false)
  })

  it('相同在途值的后续替换只保存一次', async () => {
    const first = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValue(first.promise)
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save })
    controller.update(1)
    const flushed = controller.flush()
    controller.update(2)
    controller.update(1)
    first.resolve({ storage_revision: 1 })
    await flushed
    expect(save).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot().dirty).toBe(false)
  })

  it('失败保留最新值并停止自动写入，显式 retry 才使用原 revision 重试', async () => {
    const failure = new Error('offline')
    const first = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({ storage_revision: 5 })
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 4, save })
    controller.update(1)
    const failed = expect(controller.flush()).rejects.toBe(failure)
    controller.update(2)
    first.reject(failure)
    await failed
    controller.update(3)
    await vi.runAllTimersAsync()
    expect(save).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot()).toMatchObject({ value: 3, error: failure, dirty: true, saving: false, storageRevision: 4 })
    await expect(controller.flush()).rejects.toBe(failure)
    expect(save).toHaveBeenCalledTimes(1)
    await controller.retry()
    expect(save).toHaveBeenNthCalledWith(2, 3, 4)
    expect(controller.getSnapshot()).toMatchObject({ value: 3, error: null, dirty: false, storageRevision: 5 })
  })

  it('CAS 冲突不采纳远端 revision 进行自动覆盖', async () => {
    const conflict = { code: 'E_STORAGE_REVISION_CONFLICT', storage_revision: 22 }
    const save = vi.fn().mockRejectedValue(conflict)
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 4, save })
    controller.update(1)
    await expect(controller.flush()).rejects.toBe(conflict)
    await expect(controller.retry()).rejects.toBe(conflict)
    expect(save.mock.calls).toEqual([[1, 4], [1, 4]])
    expect(controller.getSnapshot()).toMatchObject({ value: 1, dirty: true, storageRevision: 4 })
    controller.reset(99, 22)
    expect(controller.getSnapshot()).toMatchObject({ value: 99, dirty: false, storageRevision: 22, error: null })
  })

  it('网络失败后 Undo 回 ack 仍保留不确定写入，重试必须确认落盘', async () => {
    const first = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({ storage_revision: 1 })
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save })
    controller.update(1)
    const failed = expect(controller.flush()).rejects.toThrow('offline')
    controller.update(0)
    first.reject(new Error('offline'))
    await failed
    expect(controller.getSnapshot().dirty).toBe(true)
    await controller.retry()
    expect(save).toHaveBeenNthCalledWith(2, 0, 0)
    expect(controller.getSnapshot().dirty).toBe(false)
  })

  it('reset 隔离乱序旧成功响应、回执及 flush，不污染新工程', async () => {
    const old = deferred<AuthoringSaveReceipt>()
    const current = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    const onSaved = vi.fn()
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save, onSaved })
    controller.update(1)
    const interrupted = expect(controller.flush()).rejects.toBeInstanceOf(AuthoringSaveSessionChangedError)
    controller.reset(10, 8)
    await interrupted
    controller.update(11)
    const latest = controller.flush()
    current.resolve({ storage_revision: 9 })
    await latest
    old.resolve({ storage_revision: 1 })
    await settle()
    expect(controller.getSnapshot()).toMatchObject({ value: 11, storageRevision: 9, dirty: false, error: null })
    expect(onSaved).toHaveBeenCalledExactlyOnceWith(11, { storage_revision: 9 })
  })

  it('reset 隔离旧失败，取消旧 debounce，dispose 后不再通知迟到响应', async () => {
    const old = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValue(old.promise)
    const onChange = vi.fn()
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save, onChange })
    controller.update(1)
    controller.reset(2, 2)
    await vi.runAllTimersAsync()
    expect(save).not.toHaveBeenCalled()
    controller.update(3)
    const ended = expect(controller.flush()).rejects.toBeInstanceOf(AuthoringSaveSessionChangedError)
    controller.dispose()
    await ended
    const calls = onChange.mock.calls.length
    old.reject(new Error('old error'))
    await settle()
    expect(onChange).toHaveBeenCalledTimes(calls)
    expect(controller.getSnapshot()).toMatchObject({ disposed: true, error: null })
    await expect(controller.flush()).rejects.toBeInstanceOf(AuthoringSaveSessionChangedError)
    await expect(controller.retry()).rejects.toBeInstanceOf(AuthoringSaveSessionChangedError)
    expect(() => controller.update(4)).toThrow(AuthoringSaveSessionChangedError)
    expect(() => controller.reset(4, 4)).toThrow(AuthoringSaveSessionChangedError)
  })

  it('旧 session 失败不会阻断新 session 后续保存', async () => {
    const old = deferred<AuthoringSaveReceipt>()
    const save = vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue({ storage_revision: 5 })
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save })
    controller.update(1)
    const ended = expect(controller.flush()).rejects.toBeInstanceOf(AuthoringSaveSessionChangedError)
    controller.reset(3, 4)
    await ended
    old.reject(new Error('old error'))
    await settle()
    controller.update(4)
    await controller.flush()
    expect(controller.getSnapshot()).toMatchObject({ value: 4, error: null, dirty: false, storageRevision: 5 })
  })

  it('非法回执失败关闭，不丢本地编辑或假报保存', async () => {
    for (const storage_revision of [0, -1, 0.5, Infinity, NaN]) {
      const save = vi.fn().mockResolvedValue({ storage_revision })
      const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save })
      controller.update(1)
      await expect(controller.flush()).rejects.toThrow()
      expect(controller.getSnapshot()).toMatchObject({ value: 1, storageRevision: 0, dirty: true, saving: false })
      controller.dispose()
    }
  })

  it('同步 save 错误也保留 pending；重复 dispose 幂等', async () => {
    const save = vi.fn(() => { throw new Error('sync error') })
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save })
    controller.update(1)
    await expect(controller.flush()).rejects.toThrow('sync error')
    expect(controller.getSnapshot()).toMatchObject({ dirty: true, saving: false })
    controller.dispose()
    controller.dispose()
    expect(controller.getSnapshot().disposed).toBe(true)
  })

  it('非法初始计数或计时失败关闭，当前无修改的 flush 不写盘', async () => {
    const save = vi.fn()
    for (const initialRevision of [-1, 0.5, Infinity]) {
      expect(() => new AuthoringSaveController({ initialValue: 0, initialRevision, save })).toThrow()
    }
    for (const debounceMs of [-1, Infinity, NaN]) {
      expect(() => new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save, debounceMs })).toThrow()
    }
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save })
    await controller.flush()
    await controller.retry()
    expect(save).not.toHaveBeenCalled()
  })

  it('明确冲突取消 debounce 并冻结新编辑，retry 不能解封，reset 才接纳新版本', async () => {
    const save = vi.fn().mockResolvedValue({ storage_revision: 5 })
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save })
    controller.update(1)
    const conflict = new Error('回执内容与预览不一致')
    controller.block(conflict)
    controller.update(2)
    await vi.runAllTimersAsync()
    expect(save).not.toHaveBeenCalled()
    await expect(controller.flush()).rejects.toBe(conflict)
    await expect(controller.retry()).rejects.toBe(conflict)
    expect(controller.getSnapshot()).toMatchObject({ value: 2, dirty: true, storageRevision: 0 })
    controller.reset(3, 4)
    controller.update(4)
    await controller.flush()
    expect(save).toHaveBeenCalledExactlyOnceWith(4, 4)
  })

  it('明确冲突隔离在途成功回执，不改变旧 revision 或触发后续保存', async () => {
    const first = deferred<AuthoringSaveReceipt>()
    const onSaved = vi.fn()
    const save = vi.fn().mockReturnValue(first.promise)
    const controller = new AuthoringSaveController({ initialValue: 0, initialRevision: 0, save, onSaved })
    controller.update(1)
    const failed = expect(controller.flush()).rejects.toThrow('发生冲突')
    controller.block(new Error('发生冲突'))
    await failed
    controller.update(2)
    first.resolve({ storage_revision: 1 })
    await settle()
    await vi.runAllTimersAsync()
    expect(controller.getSnapshot()).toMatchObject({ value: 2, storageRevision: 0, dirty: true, saving: false })
    expect(onSaved).not.toHaveBeenCalled()
    expect(save).toHaveBeenCalledTimes(1)
  })
})
