import { afterEach, describe, expect, it, vi } from 'vitest'
import { FetchHostBridge, HostBridgeError } from './host-bridge'

afterEach(() => vi.unstubAllGlobals())

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const action = {
  contract_version: '0.3.0',
  user_action_id: 'action_1234567890_1234567890',
  capability: 'open_file',
  expires_in_seconds: 5,
}

const importRequest = {
  contract_version: '0.3.0' as const,
  selection_handle: 'selection_1234567890_1234567890',
  project_session_id: '00000000-0000-4000-8000-000000000001',
  run_id: '00000000-0000-4000-8000-000000000002',
  node_run_id: '00000000-0000-4000-8000-000000000003',
  handoff_id: '00000000-0000-4000-8000-000000000004',
  port_id: 'video', ordinal: null,
}
const importPreview = {
  ...importRequest, import_id: 'import_1234567890_1234567890', source_name: 'finished.mov',
  source_size: 12345, target_path: 'D:\\Attempts\\outputs\\enhancement.mov',
  replace_existing: true, expires_in_seconds: 300,
}
const importResult = {
  contract_version: importRequest.contract_version, project_session_id: importRequest.project_session_id,
  run_id: importRequest.run_id, node_run_id: importRequest.node_run_id, handoff_id: importRequest.handoff_id,
  port_id: importRequest.port_id, ordinal: null, import_id: importPreview.import_id,
  source_name: importPreview.source_name, source_size: importPreview.source_size,
  target_path: importPreview.target_path, status: 'imported',
}

describe('FetchHostBridge', () => {
  it('导入只发选择句柄和精确任务身份，确认一次且 token 不进入正文', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(importPreview)).mockResolvedValueOnce(response(importResult))
    vi.stubGlobal('fetch', fetch)
    const token = 'a'.repeat(43)
    const bridge = new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token })
    await expect(bridge.previewHandoffImport(importRequest)).resolves.toEqual(importPreview)
    const confirm = { contract_version: '0.3.0' as const, import_id: importPreview.import_id, overwrite: true }
    await expect(bridge.confirmHandoffImport(confirm)).resolves.toEqual(importResult)
    await expect(bridge.confirmHandoffImport(confirm)).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls[0]?.[0]).toContain('/api/studio/handoff-import/preview')
    expect(fetch.mock.calls[1]?.[0]).toContain('/api/studio/handoff-import/confirm')
    for (const [, value] of fetch.mock.calls) {
      const init = value as RequestInit
      expect(init.headers).toMatchObject({ 'X-ZNIKU-Host-Token': token })
      expect(init.body).not.toContain(token)
      expect(init.body).not.toContain('target_path')
      expect(init.body).not.toContain('source_name')
    }
  })

  it('导入预览拒绝原始路径、未知字段及串任务响应', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ ...importPreview, unexpected: true }))
      .mockResolvedValueOnce(response({ ...importPreview, node_run_id: importRequest.handoff_id }))
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token: 'a'.repeat(43) })
    await expect(bridge.previewHandoffImport({ ...importRequest, path: 'D:\\arbitrary.mov' } as typeof importRequest)).rejects.toThrow('Schema')
    expect(fetch).not.toHaveBeenCalled()
    await expect(bridge.previewHandoffImport(importRequest)).rejects.toThrow('Schema')
    await expect(bridge.previewHandoffImport(importRequest)).rejects.toThrow('不属于')
  })

  it('确认拒绝绑定漂移，网络结果不明也不会重放文件副作用', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(importPreview))
      .mockResolvedValueOnce(response({ ...importResult, target_path: 'D:\\wrong.mov' }))
      .mockResolvedValueOnce(response(importPreview)).mockRejectedValueOnce(new Error('network disconnected'))
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token: 'a'.repeat(43) })
    const confirm = { contract_version: '0.3.0' as const, import_id: importPreview.import_id, overwrite: true }
    await bridge.previewHandoffImport(importRequest)
    await expect(bridge.confirmHandoffImport(confirm)).rejects.toThrow('不属于')
    await bridge.previewHandoffImport(importRequest)
    await expect(bridge.confirmHandoffImport(confirm)).rejects.toThrow('network disconnected')
    await expect(bridge.confirmHandoffImport(confirm)).rejects.toThrow('已失效')
    expect(fetch).toHaveBeenCalledTimes(4)
  })
  it('静帧只读 POST 精确绑定，拒绝未知字段、错误引用与非法 data URL', async () => {
    const request = { contract_version: '0.3.0' as const,
      project_session_id: '00000000-0000-4000-8000-000000000001',
      reference: { kind: 'artifact' as const, run_id: '00000000-0000-4000-8000-000000000002', artifact_id: '00000000-0000-4000-8000-000000000003' } }
    const value = { ...request, image_data_url: 'data:image/png;base64,iVBORw0KGgo=', width: 160, height: 90, cache_hit: false }
    const fetch = vi.fn().mockResolvedValueOnce(response(value))
      .mockResolvedValueOnce(response({ ...value, unknown: true }))
      .mockResolvedValueOnce(response({ ...value, reference: { ...value.reference, artifact_id: '00000000-0000-4000-8000-000000000004' } }))
      .mockResolvedValueOnce(response({ ...value, image_data_url: 'javascript:alert(1)' }))
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token: 'a'.repeat(43) })
    await expect(bridge.preview(request)).resolves.toEqual(value)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0]?.[0]).toBe('http://127.0.0.1:18765/api/host-bridge/preview')
    await expect(bridge.preview(request)).rejects.toThrow('Schema')
    await expect(bridge.preview(request)).rejects.toThrow('不属于')
    await expect(bridge.preview(request)).rejects.toThrow('Schema')
  })
  it('每次 picker 只执行一次 ticket+invoke，token 只放 header', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(action, 201))
      .mockResolvedValueOnce(response({
        contract_version: '0.3.0',
        status: 'selected',
        selections: [{ selection_handle: 'selection_1234567890_1234567890', path: 'D:\\Media\\source.mkv' }],
      }))
    vi.stubGlobal('fetch', fetch)
    const token = 'a'.repeat(43)
    const bridge = new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token })

    await expect(bridge.pick('open_file', { extensions: ['.mkv'] })).resolves.toEqual([
      { selection_handle: 'selection_1234567890_1234567890', path: 'D:\\Media\\source.mkv' },
    ])
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch.mock.calls[0]?.[0]).toBe('http://127.0.0.1:18765/api/host-bridge/user-actions')
    expect(fetch.mock.calls[1]?.[0]).toBe('http://127.0.0.1:18765/api/host-bridge/invoke')
    for (const call of fetch.mock.calls) {
      const init = call[1] as RequestInit
      expect(init.headers).toMatchObject({ 'X-ZNIKU-Host-Token': token })
      expect(String(call[0])).not.toContain(token)
      expect(init.body ?? '').not.toContain(token)
    }
  })

  it('取消返回 null，未知响应字段失败关闭', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(action, 201))
      .mockResolvedValueOnce(response({ contract_version: '0.3.0', status: 'cancelled', selections: [] }))
      .mockResolvedValueOnce(response(action, 201))
      .mockResolvedValueOnce(response({ contract_version: '0.3.0', status: 'cancelled', selections: [], extra: true }))
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token: 'a'.repeat(43) })
    await expect(bridge.pick('open_file')).resolves.toBeNull()
    await expect(bridge.pick('open_file')).rejects.toBeInstanceOf(HostBridgeError)
  })

  it('未注入 launcher bootstrap 时不发请求且明确降级', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge(undefined)
    await expect(bridge.inspectCapabilities()).rejects.toMatchObject({
      code: 'E_HOST_BRIDGE_UNAVAILABLE',
    })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('系统播放器只发送 schema 验证后的 Artifact reference', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response({ ...action, capability: 'open_with_system_player' }, 201))
      .mockResolvedValueOnce(response({ contract_version: '0.3.0', status: 'launched', selections: [] }))
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token: 'a'.repeat(43) })
    await bridge.launch('open_with_system_player', {
      kind: 'artifact',
      run_id: '00000000-0000-4000-8000-000000000001',
      artifact_id: '00000000-0000-4000-8000-000000000002',
    })
    const body = JSON.parse(String((fetch.mock.calls[1]?.[1] as RequestInit).body)) as unknown
    expect(body).toEqual({
      user_action_id: 'action_1234567890_1234567890',
      capability: 'open_with_system_player',
      arguments: {
        reference: {
          kind: 'artifact',
          run_id: '00000000-0000-4000-8000-000000000001',
          artifact_id: '00000000-0000-4000-8000-000000000002',
        },
      },
    })
  })

  it.each([
    ['http://localhost:18765', 'a'.repeat(43)],
    ['https://127.0.0.1:18765', 'a'.repeat(43)],
    ['http://127.0.0.1:18765/path', 'a'.repeat(43)],
    ['http://127.0.0.1:18765?token=bad', 'a'.repeat(43)],
    ['http://127.0.0.1:80', 'a'.repeat(43)],
    ['http://127.0.0.1:18765', 'too-short'],
  ])('非法 bootstrap %s 不会发送 token 或网络请求', async (baseUrl, token) => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge({ baseUrl, token })
    await expect(bridge.inspectCapabilities()).rejects.toMatchObject({
      code: 'E_HOST_BRIDGE_BOOTSTRAP',
    })
    expect(bridge.configured).toBe(false)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('invoke 网络失败不重放一次性 ticket', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(action, 201))
      .mockRejectedValueOnce(new Error('offline'))
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge({
      baseUrl: 'http://127.0.0.1:18765',
      token: 'a'.repeat(43),
    })
    await expect(bridge.pick('open_file')).rejects.toThrow('offline')
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('capabilities 必须保持 Python 固定闭集顺序和 available/reason 语义', async () => {
    const allCapabilities = [
      'open_file', 'open_files', 'select_directory', 'save_file',
      'reveal_in_file_manager', 'open_with_system_player',
    ]
    const fetch = vi.fn()
      .mockResolvedValueOnce(response({
        contract_version: '0.3.0',
        capabilities: allCapabilities.map((capability) => ({
          capability,
          available: true,
          unavailable_reason: null,
        })).reverse(),
      }))
      .mockResolvedValueOnce(response({
        contract_version: '0.3.0',
        capabilities: allCapabilities.map((capability) => ({
          capability,
          available: capability !== 'save_file',
          unavailable_reason: null,
        })),
      }))
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token: 'a'.repeat(43) })
    await expect(bridge.inspectCapabilities()).rejects.toThrow('完整固定闭集')
    await expect(bridge.inspectCapabilities()).rejects.toThrow('可用性语义')
  })

  it('picker 响应的状态关系和绝对路径由 TS 再次失败关闭', async () => {
    const selection = { selection_handle: 'selection_1234567890_1234567890', path: 'relative.mkv' }
    const fetch = vi.fn()
      .mockResolvedValueOnce(response(action, 201))
      .mockResolvedValueOnce(response({ contract_version: '0.3.0', status: 'cancelled', selections: [selection] }))
      .mockResolvedValueOnce(response(action, 201))
      .mockResolvedValueOnce(response({ contract_version: '0.3.0', status: 'selected', selections: [selection] }))
    vi.stubGlobal('fetch', fetch)
    const bridge = new FetchHostBridge({ baseUrl: 'http://127.0.0.1:18765', token: 'a'.repeat(43) })
    await expect(bridge.pick('open_file')).rejects.toThrow('selected 与 selections 不一致')
    await expect(bridge.pick('open_file')).rejects.toThrow('非绝对路径')
  })
})
