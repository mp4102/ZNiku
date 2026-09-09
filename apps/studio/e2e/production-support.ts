/** 合成生产门禁的有界诊断：不读取 bootstrap/header，不生成 HAR/trace 或完整工程导出。 */
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface } from 'node:readline'
import { dirname, resolve } from 'node:path'
import { writeFile } from 'node:fs/promises'
import type { ConsoleMessage, Page, Request, Response, TestInfo } from '@playwright/test'

export interface SyntheticFixture {
  readonly wizard_project: string
  readonly output_root: string
  readonly output_collision: string
  readonly external_project: string
  readonly small_project: string
  readonly large_project: string
  readonly media_project: string
  readonly geometry_project: string
  readonly batch_b_projects?: Readonly<Record<string, { readonly path: string; readonly nodes: number; readonly edges: number }>>
}

/** 只保存测试生成的绑定与状态；一次性票据、bootstrap、header 及媒体内容永不进入报告。 */
export function diagnosticValue(value: unknown, roots: ReadonlyArray<string> = []): unknown {
  let itemsLeft = 4096
  let textLeft = 131_072
  const visited = new WeakSet<object>()
  const visit = (item: unknown, depth: number): unknown => {
    if (itemsLeft-- <= 0 || depth > 12) return '<truncated>'
    if (typeof item === 'string') {
      let clean = item
      for (const root of roots.filter(Boolean)) {
        // Python exception repr 的双反斜杠和跨平台正斜杠也必须脱敏。
        for (const variant of [root.replace(/\\/g, '\\\\'), root.replace(/\\/g, '/'), root])
          clean = clean.split(variant).join('<synthetic-root>')
      }
      // 原始异常可能嵌入 quoted JSON/短票据。敏感行整体省略，不依赖随机值长度猜测安全。
      clean = clean.split('\n').map((line) => /\b(?:token|ticket|authorization|selection_handle|candidate_handle|user_action_id|bootstrap)\b/i.test(line)
        ? '<redacted-sensitive-line>' : line).join('\n')
      clean = clean
        .replace(/https?:\/\/[^\s"'<>]+/g, (url) => url.split(/[?#]/, 1)[0]!)
        .replace(/\b[A-Za-z]:[\\/][^\r\n"'<>]*/g, '<absolute-path>')
        .replace(/(?<![>\\\w])\\\\[^\r\n"'<>]*/g, '<absolute-path>')
        .replace(/(?<![\w:/>\]])\/(?!api(?:\/|$))[^\r\n"'<>]*/g, '<absolute-path>')
        .replace(/[A-Za-z0-9_-]{40,}/g, '<redacted-long-value>').slice(0, Math.min(4096, Math.max(0, textLeft)))
      textLeft -= clean.length
      return clean
    }
    if (item && typeof item === 'object') {
      if (visited.has(item)) return '<truncated-cycle>'
      visited.add(item)
      if (Array.isArray(item)) return item.slice(0, 200).map((entry) => visit(entry, depth + 1))
      return Object.fromEntries(Object.entries(item).slice(0, 100)
        .filter(([key]) => !/token|header|authorization|bootstrap|user_action_id|selection_handle|candidate_handle|ticket_id|inbox_id|import_id|image_data_url/i.test(key))
        .map(([key, entry]) => [key, visit(entry, depth + 1)]))
    }
    return item
  }
  return visit(value, 0)
}

/** 诊断本身不能无限等待网络 body 或清理子进程；到期只省略证据，不放宽产品断言。 */
export async function boundedDiagnostic<T>(promise: Promise<T>, milliseconds: number, fallback: T): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([promise.catch(() => fallback), new Promise<T>((done) => {
      timer = setTimeout(() => done(fallback), milliseconds); timer.unref()
    })])
  } finally { clearTimeout(timer) }
}

export class SyntheticFixtureHost {
  private child: ChildProcessWithoutNullStreams | null = null
  private stderrTail = ''
  origin = ''
  fixture!: SyntheticFixture

  async start(scriptPath = 'tools/studio_production_fixture.py'): Promise<void> {
    this.child = spawn('uv', ['run', '--locked', '--extra', 'dev', 'python', scriptPath], {
      cwd: resolve('../..'), env: { ...process.env, PYTHONUTF8: '1' }, shell: false,
      windowsHide: true, detached: process.platform !== 'win32',
    })
    const child = this.child
    child.stderr.on('data', (data: Buffer) => { this.stderrTail = (this.stderrTail + data.toString()).slice(-65_536) })
    await new Promise<void>((done, fail) => {
      const timer = setTimeout(() => fail(new Error(`合成服务 60s 内未就绪: ${String(this.stderr())}`)), 60_000)
      timer.unref()
      const lines = createInterface({ input: child.stdout })
      lines.on('line', (line) => {
        try {
          const parsed = JSON.parse(line) as SyntheticFixture & { origin?: string }
          if (parsed.origin && /^http:\/\/127\.0\.0\.1:\d+$/.test(parsed.origin) && parsed.external_project && parsed.wizard_project) {
            this.origin = parsed.origin
            this.fixture = parsed
            clearTimeout(timer)
            done()
          }
        } catch { /* 非就绪消息不冒充服务地址，也不记录原文中的潜在秘密。 */ }
      })
      child.once('error', (error) => { clearTimeout(timer); fail(error) })
      child.once('exit', (code) => { clearTimeout(timer); fail(new Error(`合成服务提前退出 ${code}: ${String(this.stderr())}`)) })
    })
  }

  roots(): string[] { return [this.fixture?.external_project ? dirname(this.fixture.external_project) : '', resolve('../..'), process.env.TEMP ?? '', process.env.TMP ?? ''] }
  stderr(): unknown { return diagnosticValue(this.stderrTail, this.roots()) }

  async stop(): Promise<void> {
    const child = this.child
    if (!child || child.exitCode !== null) return
    const stopped = new Promise<void>((done) => child.once('exit', () => done()))
    child.stdin.end('\n')
    // 只清理本测试创建的子进程，不能让失败门禁遗留合成服务；从不按进程名杀用户服务。
    let graceful = false
    await Promise.race([stopped.then(() => { graceful = true }), new Promise<void>((done) => {
      const timer = setTimeout(done, 10_000); timer.unref(); void stopped.finally(() => clearTimeout(timer))
    })])
    if (!graceful && child.pid) {
      if (process.platform === 'win32') {
        const terminator = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { shell: false, windowsHide: true, stdio: 'ignore' })
        await boundedDiagnostic(new Promise<void>((done) => { terminator.once('error', () => done()); terminator.once('exit', () => done()) }), 5000, undefined)
      } else {
        // POSIX 独立进程组只包含该 fixture 的 uv/Python/媒体子进程，不能留下孤儿服务。
        try { process.kill(-child.pid, 'SIGKILL') } catch { if (child.exitCode === null) child.kill('SIGKILL') }
      }
      await Promise.race([stopped, new Promise<never>((_done, fail) => {
        const timer = setTimeout(() => fail(new Error('合成服务清理失败：owned child 仍未退出')), 5000)
        timer.unref(); void stopped.finally(() => clearTimeout(timer))
      })])
    }
    this.child = null
  }
}

function compactState(value: unknown): unknown {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if (record.run && typeof record.run === 'object') {
    const run = record.run as Record<string, unknown>
    return { run_id: run.run_id, state: run.state, error: run.error,
      node_runs: Array.isArray(run.node_runs) ? run.node_runs.map((node: Record<string, unknown>) => ({
        node_id: node.node_id, node_run_id: node.node_run_id, state: node.state, error: node.error,
        started_at: node.started_at, ended_at: node.ended_at, progress: node.progress,
        handoff_id: (node.external_handoff as Record<string, unknown> | null)?.handoff_id,
        output_artifact_ids: node.output_artifact_ids,
      })) : [] }
  }
  return { project_session_id: record.project_session_id, storage_revision: record.storage_revision,
    active_run_id: record.active_run_id, active_operation: record.active_operation,
    last_error: record.last_error, run_summaries: record.run_summaries }
}

/** 只监听单个合成页面的接口，保留最近事件与单独的副作用记录，避免轮询挤掉 Submit 证据。 */
export class ProductionJournal {
  private readonly events: unknown[] = []
  private readonly mutations: unknown[] = []
  private readonly pending = new Set<Promise<void>>()
  private readonly disposers: Array<() => void> = []
  constructor(readonly page: Page, private readonly host: SyntheticFixtureHost) {
    const append = (value: unknown) => { this.events.push(value); if (this.events.length > 80) this.events.shift() }
    const onPageError = (error: Error) => append({ kind: 'pageerror', message: error.message })
    const onConsole = (message: ConsoleMessage) => { if (message.type() === 'error') append({ kind: 'console', message: message.text() }) }
    const onRequestFailed = (request: Request) => {
      if (request.url().startsWith(`${host.origin}/`)) append({ kind: 'request_failed', endpoint: new URL(request.url()).pathname,
        error: request.failure()?.errorText })
    }
    const onRequest = (request: Request) => {
      if (!this.belongs(request) || request.method() !== 'POST') return
      let body: Record<string, unknown> | null = null, parseFailed = false
      try { body = request.postDataJSON() as Record<string, unknown> | null } catch { parseFailed = true }
      const fields = ['operation', 'project_session_id', 'run_id', 'node_run_id', 'handoff_id', 'port_id', 'ordinal']
      this.mutations.push({ at: Date.now(), kind: 'request', endpoint: new URL(request.url()).pathname, parse_failed: parseFailed,
        binding: Object.fromEntries(fields.flatMap((key) => body?.[key] === undefined ? [] : [[key, body[key]]])) })
      if (this.mutations.length > 80) this.mutations.shift()
    }
    const onResponse = (response: Response) => {
      if (!this.belongs(response.request())) return
      const endpoint = new URL(response.url()).pathname
      if (this.pending.size >= 16) { append({ kind: 'response_omitted', endpoint, reason: 'diagnostic_concurrency_limit' }); return }
      const pending = (async () => {
        const body = await boundedDiagnostic(response.json() as Promise<Record<string, unknown> | null>, 1000, null)
        append({ at: Date.now(), kind: 'response', endpoint, status: response.status(),
          ...(endpoint === '/api/studio/status' || /^\/api\/studio\/runs\/[^/]+$/.test(endpoint)
            ? { state: compactState(body) } : { error: body?.error, active_operation: body?.active_operation, last_error: body?.last_error }) })
      })().finally(() => this.pending.delete(pending))
      this.pending.add(pending)
    }
    page.on('pageerror', onPageError); page.on('console', onConsole); page.on('request', onRequest); page.on('response', onResponse); page.on('requestfailed', onRequestFailed)
    this.disposers.push(() => { page.off('pageerror', onPageError); page.off('console', onConsole); page.off('request', onRequest); page.off('response', onResponse); page.off('requestfailed', onRequestFailed) })
  }

  dispose(): void { for (const dispose of this.disposers.splice(0)) dispose() }

  private belongs(request: Request): boolean {
    return request.url().startsWith(`${this.host.origin}/api/`) && new URL(request.url()).pathname !== '/api/host-bridge/preview'
  }

  async capture(info: TestInfo): Promise<void> {
    this.dispose()
    await boundedDiagnostic(Promise.allSettled([...this.pending]), 2000, [])
    const response = await this.page.request.get(`${this.host.origin}/api/studio/status`, { timeout: 5000 }).catch(() => null)
    const rawStatus = response ? await boundedDiagnostic(response.json() as Promise<Record<string, unknown> | null>, 1000, null) : null
    const status = compactState(rawStatus)
    const runId = rawStatus?.active_run_id ?? (rawStatus?.run_summaries as Array<{ run_id?: string }> | undefined)?.[0]?.run_id
    const runResponse = typeof runId === 'string'
      ? await this.page.request.get(`${this.host.origin}/api/studio/runs/${encodeURIComponent(runId)}`, { timeout: 5000 }).catch(() => null) : null
    const run = runResponse ? compactState(await boundedDiagnostic(runResponse.json(), 1000, null)) : null
    const report = diagnosticValue({ test: info.title, status: info.status, final_status: status, final_run: run,
      mutations: this.mutations, fixture_stderr: this.host.stderr(), events: this.events }, this.host.roots())
    const path = info.outputPath('failure-diagnostics.json')
    await writeFile(path, JSON.stringify(report, null, 2), 'utf8')
    await info.attach('synthetic-failure-diagnostics', { path, contentType: 'application/json' })
    if (!this.page.isClosed()) await maskedScreenshot(this.page, info.outputPath('failure-masked.png')).catch(() => undefined)
  }
}

/** 遮罩只用于公开证据，不改变 DOM、布局或产品行为；保留真实生产 UI 的结构。 */
export const DIAGNOSTIC_MASK_SELECTOR = [
  'code', 'pre', 'textarea', 'dd', 'input:not([type="checkbox"]):not([type="range"])',
  '.run-selector select', '.run-history', '.canvas-context strong',
  '[aria-label="工程数据与归档检查"] li', '[aria-label="工程数据与归档检查"] p',
].map((selector) => `${selector}:visible`).join(', ')
export async function maskedScreenshot(page: Page, path: string): Promise<void> {
  const physicalPathOrId = /(?:[A-Za-z]:[\\/]|\\\\|\/(?:tmp|home|Users|mnt|private)\/|[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[a-f0-9]{4}-[a-f0-9]{12})/
  await page.screenshot({ path, fullPage: true, timeout: 5000, maskColor: '#334155',
    mask: [page.locator(DIAGNOSTIC_MASK_SELECTOR), page.getByText(physicalPathOrId)] })
}
