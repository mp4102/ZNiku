/** 诊断卫生本身也有门禁，不把真实 token 或一次性操作票据放进失败包。 */
import { test, expect, type Page } from '@playwright/test'
import { EventEmitter } from 'node:events'
import { readFile } from 'node:fs/promises'
import { boundedDiagnostic, diagnosticValue, DIAGNOSTIC_MASK_SELECTOR, ProductionJournal, SyntheticFixtureHost } from './production-support'

test('合成失败诊断保留绑定与状态，但剔除秘密和媒体内容', () => {
  const secret = 'synthetic-secret-'.repeat(4)
  const result = diagnosticValue({ token: secret, headers: { Authorization: secret },
    bootstrap: { token: secret }, selection_handle: 'synthetic-selection', candidate_handle: 'synthetic-candidate',
    ticket_id: 'synthetic-ticket', user_action_id: 'synthetic-action', image_data_url: 'data:image/png;base64,secret',
    run_id: '00000000-0000-4000-8000-000000000001', node_run_id: '00000000-0000-4000-8000-000000000002',
    state: 'running', error: `unexpected ${secret}`, path: '/synthetic/root/source.mkv' }, ['/synthetic/root'])
  expect(result).toEqual({ run_id: '00000000-0000-4000-8000-000000000001',
    node_run_id: '00000000-0000-4000-8000-000000000002', state: 'running',
    error: 'unexpected <redacted-long-value>', path: '<synthetic-root>/source.mkv' })
  expect(JSON.stringify(result)).not.toContain(secret)
})

test('合成失败诊断限制文本和数组体积', () => {
  expect(diagnosticValue(Array.from({ length: 250 }, (_, index) => index))).toHaveLength(200)
  expect(String(diagnosticValue('短句 '.repeat(5000)))).toHaveLength(4096)
})

test('合成 stderr 与嵌入 URL 也移除用户名、临时根和短凭据', () => {
  const result = diagnosticValue({ stderr: 'at C:\\Users\\synthetic-person\\AppData\\Local\\Temp\\fixture\\file.py token=short-secret\n/home/synthetic-person/work trace',
    message: 'GET http://127.0.0.1:9000/bootstrap?token=short-secret#ticket=value failed ticket=small' }, ['C:\\Users\\synthetic-person\\AppData\\Local\\Temp'])
  expect(result).toEqual({ stderr: '<redacted-sensitive-line>\n<absolute-path>', message: '<redacted-sensitive-line>' })
})

test('短票据、quoted JSON、未知磁盘与UNC位置都不进入诊断', () => {
  expect(diagnosticValue({ authorization: 'Bearer short-secret',
    message: JSON.stringify({ selection_handle: 'short-candidate', token: 'short-token' }),
    windows: 'D:\\outside\\private-media.mov', posix: '/arbitrary/location/private.mov',
    unc: '\\\\synthetic-server\\share\\private.mov', repr: '\\\\\\\\synthetic-server\\\\share\\\\private.mov',
    endpoint: '/api/studio/status' })).toEqual({ message: '<redacted-sensitive-line>', windows: '<absolute-path>',
      posix: '<absolute-path>', unc: '<absolute-path>', repr: '<absolute-path>', endpoint: '/api/studio/status' })
})

test('异常深度、循环、大对象和文本总量均受诊断预算约束', () => {
  let deep: Record<string, unknown> = {}
  for (let index = 0; index < 20_000; index++) deep = { child: deep }
  expect(JSON.stringify(diagnosticValue(deep))).toContain('<truncated>')
  const cycle: Record<string, unknown> = {}; cycle.self = cycle
  expect(diagnosticValue(cycle)).toEqual({ self: '<truncated-cycle>' })
  expect(Object.keys(diagnosticValue(Object.fromEntries(Array.from({ length: 500 }, (_, index) => [`key${index}`, index]))) as object)).toHaveLength(100)
  expect(JSON.stringify(diagnosticValue(Array.from({ length: 200 }, () => '长句 '.repeat(5000)))).length).toBeLessThan(135_000)
})

test('不结束的诊断body到期省略，原文显示容器都在截图mask中', async () => {
  expect(await boundedDiagnostic(new Promise<null>(() => undefined), 5, null)).toBeNull()
  for (const selector of ['pre:visible', 'dd:visible', '[aria-label="工程数据与归档检查"] li:visible', '[aria-label="工程数据与归档检查"] p:visible'])
    expect(DIAGNOSTIC_MASK_SELECTOR).toContain(selector)
})

test('失败journal对未结束body与非法POST有界降级，写包后不再监听页面', async ({}, info) => {
  const host = new SyntheticFixtureHost(); host.origin = 'http://127.0.0.1:9000'
  const fake = Object.assign(new EventEmitter(), { isClosed: () => true,
    request: { get: async () => ({ json: async () => ({ active_run_id: null, active_operation: null, run_summaries: [] }) }) } })
  const journal = new ProductionJournal(fake as unknown as Page, host)
  fake.emit('request', { url: () => `${host.origin}/api/studio/command`, method: () => 'POST', postDataJSON: () => { throw new Error('invalid synthetic request') } })
  for (let index = 0; index < 20; index++) fake.emit('response', { request: () => ({ url: () => `${host.origin}/api/studio/status` }),
    url: () => `${host.origin}/api/studio/status`, status: () => 200, json: () => new Promise(() => undefined) })
  await journal.capture(info)
  const report = JSON.parse(await readFile(info.outputPath('failure-diagnostics.json'), 'utf8')) as {
    mutations: { parse_failed: boolean }[]; events: { kind: string }[]
  }
  expect(report.mutations[0]!.parse_failed).toBe(true)
  expect(report.events.filter((item) => item.kind === 'response_omitted')).toHaveLength(4)
  expect(fake.eventNames()).toEqual([])
})

test('Python exception repr 的转义 Windows 路径仍会脱敏', () => {
  expect(diagnosticValue('error at D:\\\\synthetic\\\\fixture\\\\file.mkv', ['D:\\synthetic\\fixture']))
    .toBe('error at <synthetic-root>\\\\file.mkv')
  expect(diagnosticValue('error at D:/synthetic/fixture/file.mkv', ['D:\\synthetic\\fixture']))
    .toBe('error at <synthetic-root>/file.mkv')
})
