/**
 * 协调同一工程编辑快照的 debounce、单请求保存和 storage revision/CAS。
 *
 * T 必须不可变，且 save 必须绑定当前工程；本模块只管理存储并发，不校验 Graph 或推断可运行性。
 * 保存回执只确认已发送快照，不覆盖期间的新编辑。失败冻结自动发送并保留本地内容，显式 retry
 * 仍使用原 CAS revision，绝不根据冲突响应自动覆盖远端。切换工程前由调用方 flush 或明确放弃；
 * reset/dispose 只能隔离迟到回调，无法撤回已经发送到服务端的写入。
 */

export interface AuthoringSaveReceipt {
  readonly storage_revision: number
}

export interface AuthoringSaveSnapshot<T> {
  readonly value: T
  readonly storageRevision: number
  readonly dirty: boolean
  readonly saving: boolean
  readonly error: unknown | null
  readonly disposed: boolean
}

export interface AuthoringSaveOptions<T, R extends AuthoringSaveReceipt> {
  readonly initialValue: T
  readonly initialRevision: number
  readonly save: (value: T, expectedRevision: number) => Promise<R>
  readonly equals?: (left: T, right: T) => boolean
  readonly debounceMs?: number
  readonly onChange?: (snapshot: AuthoringSaveSnapshot<T>) => void
  /** 只接收当前 session 的成功回执；它不是替换当前本地 draft 的通知。 */
  readonly onSaved?: (value: T, receipt: R) => void
}

export class AuthoringSaveSessionChangedError extends Error {
  constructor() {
    super('保存会话已结束或切换。')
    this.name = 'AuthoringSaveSessionChangedError'
  }
}

interface SaveFlight<T> {
  readonly value: T
  readonly revision: number
  readonly session: number
}

interface FlushWaiter {
  readonly resolve: () => void
  readonly reject: (error: unknown) => void
}

export class AuthoringSaveController<T, R extends AuthoringSaveReceipt = AuthoringSaveReceipt> {
  private value: T
  private acknowledged: T
  private revision: number
  private readonly equals: (left: T, right: T) => boolean
  private readonly debounceMs: number
  private timer: ReturnType<typeof setTimeout> | null = null
  private flight: SaveFlight<T> | null = null
  private waiters: FlushWaiter[] = []
  private session = 0
  private ready = false
  private error: unknown | null = null
  private blocked = false
  private uncertainWrite = false
  private disposed = false

  constructor(private readonly options: AuthoringSaveOptions<T, R>) {
    requireRevision(options.initialRevision)
    const debounceMs = options.debounceMs ?? 500
    if (!Number.isFinite(debounceMs) || debounceMs < 0) {
      throw new Error('自动保存等待时间必须是非负有限数值。')
    }
    this.value = options.initialValue
    this.acknowledged = options.initialValue
    this.revision = options.initialRevision
    this.equals = options.equals ?? Object.is
    this.debounceMs = debounceMs
  }

  getSnapshot(): AuthoringSaveSnapshot<T> {
    return {
      value: this.value,
      storageRevision: this.revision,
      // 在途写入可能把磁盘改为旧编辑；即使 Undo 回到 ack，也不能提前标记已保存。
      dirty: this.flight !== null || this.uncertainWrite || !this.equals(this.value, this.acknowledged),
      saving: this.flight !== null,
      error: this.error,
      disposed: this.disposed,
    }
  }

  update(value: T): void {
    this.requireActive()
    if (this.equals(this.value, value)) return
    this.value = value
    this.clearTimer()
    this.ready = this.waiters.length > 0
    if (this.error === null && !this.ready && this.getSnapshot().dirty) {
      this.timer = setTimeout(() => {
        this.timer = null
        this.ready = true
        this.pump()
      }, this.debounceMs)
    }
    this.notify()
    this.pump()
  }

  /** 手工保存、离开或运行前调用；等待所有期间新编辑的最后一个快照落盘。 */
  flush(): Promise<void> {
    if (this.disposed) return Promise.reject(new AuthoringSaveSessionChangedError())
    if (this.error !== null) return Promise.reject(this.error)
    if (!this.getSnapshot().dirty) return Promise.resolve()
    this.clearTimer()
    this.ready = true
    const result = new Promise<void>((resolve, reject) => {
      this.waiters.push({ resolve, reject })
    })
    this.pump()
    return result
  }

  /** 显式恢复失败保存；CAS revision 不变，冲突需要调用方重新载入或另存处理。 */
  retry(): Promise<void> {
    if (this.disposed) return Promise.reject(new AuthoringSaveSessionChangedError())
    if (this.blocked) return Promise.reject(this.error)
    this.error = null
    this.notify()
    return this.flush()
  }

  /** 合同冲突冻结所有自动发送；旧回执不能解封，只能在明确载入后 reset。 */
  block(error: unknown): void {
    this.requireActive()
    this.session += 1
    this.clearTimer()
    this.flight = null
    this.ready = false
    this.uncertainWrite = true
    this.error = error ?? new Error('保存上下文已变化，请重新载入工程。')
    this.blocked = true
    this.rejectWaiters(this.error)
    this.notify()
  }

  /** 接纳调用方明确选择的服务端状态，并隔离旧工程/旧会话的异步响应。 */
  reset(value: T, revision: number): void {
    this.requireActive()
    requireRevision(revision)
    this.session += 1
    this.clearTimer()
    this.rejectWaiters(new AuthoringSaveSessionChangedError())
    this.value = value
    this.acknowledged = value
    this.revision = revision
    this.flight = null
    this.error = null
    this.blocked = false
    this.uncertainWrite = false
    this.ready = false
    this.notify()
  }

  dispose(): void {
    if (this.disposed) return
    this.session += 1
    this.disposed = true
    this.clearTimer()
    this.flight = null
    this.rejectWaiters(new AuthoringSaveSessionChangedError())
    this.notify()
  }

  private pump(): void {
    if (this.disposed || this.error !== null || this.flight !== null) return
    if (!this.getSnapshot().dirty) {
      this.clearTimer()
      this.ready = false
      const waiters = this.waiters
      this.waiters = []
      waiters.forEach(({ resolve }) => resolve())
      return
    }
    if (!this.ready) return
    this.clearTimer()
    this.ready = false
    const flight = { value: this.value, revision: this.revision, session: this.session }
    this.flight = flight
    this.notify()
    // onChange 允许调用方结束会话；结束后不得再启动旧工程写入。
    if (this.isCurrent(flight)) void this.performSave(flight)
  }

  private async performSave(flight: SaveFlight<T>): Promise<void> {
    let receipt: R
    try {
      receipt = await this.options.save(flight.value, flight.revision)
      requireRevision(receipt.storage_revision)
      if (receipt.storage_revision <= flight.revision) {
        throw new Error('保存响应没有推进存储版本。')
      }
    } catch (error) {
      if (!this.isCurrent(flight)) return
      this.flight = null
      // 连接错误可能发生在服务端提交之后；保留不确定性，不能把 Undo 当作落盘确认。
      this.uncertainWrite = true
      this.error = error ?? new Error('保存失败。')
      this.clearTimer()
      this.ready = false
      this.rejectWaiters(this.error)
      this.notify()
      return
    }
    if (!this.isCurrent(flight)) return
    this.flight = null
    this.acknowledged = flight.value
    this.revision = receipt.storage_revision
    this.uncertainWrite = false
    const session = this.session
    this.options.onSaved?.(flight.value, receipt)
    if (this.disposed || this.session !== session) return
    this.notify()
    if (this.waiters.length > 0) this.ready = true
    this.pump()
  }

  private isCurrent(flight: SaveFlight<T>): boolean {
    return !this.disposed && flight.session === this.session && this.flight === flight
  }

  private rejectWaiters(error: unknown): void {
    const waiters = this.waiters
    this.waiters = []
    waiters.forEach(({ reject }) => reject(error))
  }

  private notify(): void { this.options.onChange?.(this.getSnapshot()) }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = null
  }

  private requireActive(): void {
    if (this.disposed) throw new AuthoringSaveSessionChangedError()
  }
}

function requireRevision(value: number): void {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error('存储版本必须是非负安全整数。')
  }
}
