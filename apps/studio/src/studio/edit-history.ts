/**
 * 保存当前编辑会话的有界撤销历史，不保存 Run，也不定义 Graph 的合法性。
 *
 * T 必须是不可变快照；Graph 与 StudioState 应合成一个 T，保证一次撤销同时恢复两者。
 * 连续拖动通过 begin/update/end 合并，批量宏通过一次 apply 提交。调用方提供的 equals
 * 只识别无变化操作，不承担领域校验或运行身份判断。
 */

export interface EditHistoryOptions<T> {
  readonly limit?: number
  readonly equals?: (left: T, right: T) => boolean
}

export interface EditHistorySnapshot<T> {
  readonly value: T
  readonly canUndo: boolean
  readonly canRedo: boolean
  readonly undoLabel: string | null
  readonly redoLabel: string | null
  readonly transactionActive: boolean
}

interface EditAction<T> {
  readonly before: T
  readonly after: T
  readonly label: string
}

interface EditTransaction<T> {
  readonly before: T
  readonly label: string
}

export class EditHistory<T> {
  private current: T
  private readonly limit: number
  private readonly equals: (left: T, right: T) => boolean
  private past: EditAction<T>[] = []
  private future: EditAction<T>[] = []
  private transaction: EditTransaction<T> | null = null

  constructor(initialValue: T, options: EditHistoryOptions<T> = {}) {
    const limit = options.limit ?? 100
    if (!Number.isSafeInteger(limit) || limit < 1) {
      throw new Error('编辑历史容量必须是正整数。')
    }
    this.current = initialValue
    this.limit = limit
    this.equals = options.equals ?? Object.is
  }

  get value(): T { return this.current }

  getSnapshot(): EditHistorySnapshot<T> {
    return {
      value: this.current,
      canUndo: this.transaction === null && this.past.length > 0,
      canRedo: this.transaction === null && this.future.length > 0,
      undoLabel: this.past.at(-1)?.label ?? null,
      redoLabel: this.future.at(-1)?.label ?? null,
      transactionActive: this.transaction !== null,
    }
  }

  /** 应用一个完整操作；无变化时保留 redo，不产生空历史。 */
  commit(value: T, label = '编辑'): boolean {
    this.requireIdle()
    return this.record(this.current, value, label)
  }

  /** updater 可组成任意多个不可变编辑，它们共同成为一条宏历史。 */
  apply(label: string, updater: (value: T) => T): boolean {
    this.requireIdle()
    return this.commit(updater(this.current), label)
  }

  begin(label = '移动节点'): void {
    this.requireIdle()
    this.transaction = { before: this.current, label }
  }

  /** 只更新手势预览；调用方应在 end 成功后才提交 autosave。 */
  update(value: T): void {
    if (this.transaction === null) throw new Error('尚未开始连续编辑。')
    this.current = value
  }

  end(): boolean {
    const transaction = this.transaction
    if (transaction === null) return false
    this.transaction = null
    return this.record(transaction.before, this.current, transaction.label)
  }

  cancel(): T {
    if (this.transaction !== null) {
      this.current = this.transaction.before
      this.transaction = null
    }
    return this.current
  }

  undo(): T {
    this.requireIdle()
    const action = this.past.pop()
    if (action !== undefined) {
      this.future.push(action)
      this.current = action.before
    }
    return this.current
  }

  redo(): T {
    this.requireIdle()
    const action = this.future.pop()
    if (action !== undefined) {
      this.past.push(action)
      this.current = action.after
    }
    return this.current
  }

  /** 打开另一工程或明确丢弃本地编辑时清空整个会话历史。 */
  reset(value: T): void {
    this.current = value
    this.past = []
    this.future = []
    this.transaction = null
  }

  private record(before: T, after: T, label: string): boolean {
    if (this.equals(before, after)) {
      this.current = before
      return false
    }
    this.current = after
    this.past.push({ before, after, label })
    if (this.past.length > this.limit) this.past.shift()
    this.future = []
    return true
  }

  private requireIdle(): void {
    if (this.transaction !== null) throw new Error('请先结束或取消当前连续编辑。')
  }
}
