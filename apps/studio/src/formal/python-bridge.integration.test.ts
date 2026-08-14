// @vitest-environment node

import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { createInterface } from 'node:readline'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import type { AuthoringCommand } from '../generated/authoring-wire.generated'
import { WindowAuthoringGateway, type ZnikuAuthoringBridge } from './gateway'

class JsonLinePythonBridge implements ZnikuAuthoringBridge {
  private readonly pending: Array<{
    resolve: (value: unknown) => void
    reject: (reason: Error) => void
  }> = []

  constructor(private readonly process: ChildProcessWithoutNullStreams) {
    const lines = createInterface({ input: process.stdout })
    lines.on('line', (line) => {
      const request = this.pending.shift()
      if (!request) return
      try {
        request.resolve(JSON.parse(line) as unknown)
      } catch (error) {
        request.reject(error instanceof Error ? error : new Error('Python bridge JSON 无效'))
      }
    })
    process.on('error', (error) => {
      while (this.pending.length > 0) this.pending.shift()?.reject(error)
    })
  }

  invoke(request: {
    readonly operation: 'load_draft' | 'apply_command' | 'load_manifest'
    readonly payload: unknown
  }): Promise<unknown> {
    return new Promise((resolve, reject) => {
      this.pending.push({ resolve, reject })
      this.process.stdin.write(`${JSON.stringify(request)}\n`)
    })
  }
}

describe('Studio → live Python Authoring bridge', () => {
  let process: ChildProcessWithoutNullStreams
  let gateway: WindowAuthoringGateway

  beforeAll(() => {
    const repositoryRoot = fileURLToPath(new URL('../../../../', import.meta.url))
    process = spawn(
      'uv',
      [
        'run',
        '--locked',
        '--extra',
        'dev',
        'python',
        'tests/support/authoring_bridge_harness.py',
      ],
      { cwd: repositoryRoot },
    )
    gateway = new WindowAuthoringGateway(new JsonLinePythonBridge(process))
  })

  afterAll(() => {
    process.kill()
  })

  it('执行 load→connect→idempotent replay→stale rejection 的真实跨语言链', async () => {
    const initial = await gateway.loadDraft('draft.synthetic.program')
    expect(initial.snapshot.spec_revision).toBe(0)
    expect(initial.snapshot.validation.result.outcome).toBe('invalid')

    const command: AuthoringCommand = {
      authoring_contract_version: '0.1.0',
      command_id: 'command.integration.connect',
      draft_id: initial.snapshot.draft_id,
      base_revision: initial.snapshot.spec_revision,
      intent: {
        intent_kind: 'connect_ports',
        source: { node_id: 'node.engine.filter', port_id: 'program_out' },
        target: { node_id: 'node.final.program', port_id: 'program' },
      },
    }
    const connected = await gateway.applyCommand(command)
    expect('snapshot' in connected && connected.snapshot.spec_revision).toBe(1)
    expect(
      'snapshot' in connected && connected.snapshot.validation.result.outcome,
    ).toBe('authoring_valid')

    const replay = await gateway.applyCommand(command)
    expect('snapshot' in replay && replay.snapshot.spec_revision).toBe(1)

    const stale = await gateway.applyCommand({
      ...command,
      command_id: 'command.integration.stale',
      intent: { intent_kind: 'disconnect_ports', edge_id: 'edge.source.filter' },
    })
    expect('result_kind' in stale && stale.result_kind).toBe('command_rejected')
    expect('current_revision' in stale && stale.current_revision).toBe(1)
  })
})
