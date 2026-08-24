import { describe, expect, it } from 'vitest'
import { parseStudioCommand, parseStudioEnvelope, StudioContractError } from './contracts'
import { handoffEnvelope, studioEnvelope } from './test-fixtures'

describe('Studio Project Service contract', () => {
  it('接受 Python Schema 对齐的 Project、Run、Artifact、日志与 handoff', () => {
    const parsed = parseStudioEnvelope(handoffEnvelope())

    expect(parsed.contract_version).toBe('0.2.0')
    expect(parsed.runs[0]?.node_runs[1]?.state).toBe('waiting_external')
    expect(parsed.artifacts[0]?.path).toContain('source.mkv')
    expect(parsed.logs[0]?.stdout_available).toBe(true)
  })

  it('未知字段、非法版本及未知 Runtime 状态默认失败关闭', () => {
    expect(() => parseStudioEnvelope({})).toThrow(StudioContractError)
    expect(() => parseStudioEnvelope({ ...studioEnvelope(), unexpected: true })).toThrow(
      StudioContractError,
    )
    expect(() => parseStudioEnvelope({ ...studioEnvelope(), contract_version: '0.1.0' })).toThrow(
      /Python 0\.2\.0 Schema/,
    )
    expect(() =>
      parseStudioEnvelope({ ...studioEnvelope(), active_operation: 'save_project' }),
    ).toThrow(StudioContractError)

    const invalid = structuredClone(handoffEnvelope()) as unknown as {
      runs: Array<{ node_runs: Array<{ state: string }> }>
    }
    invalid.runs[0]!.node_runs[0]!.state = 'resuming'
    expect(() => parseStudioEnvelope(invalid)).toThrow(StudioContractError)

    const missingDefaultedField = structuredClone(handoffEnvelope()) as unknown as {
      runs: Array<{ node_runs: Array<{ progress?: number | null }> }>
    }
    delete missingDefaultedField.runs[0]!.node_runs[0]!.progress
    expect(() => parseStudioEnvelope(missingDefaultedField)).toThrow(StudioContractError)
  })

  it('command 也由 Python 生成 Schema 失败关闭', () => {
    expect(parseStudioCommand({ operation: 'run_to', node_id: 'transform' })).toEqual({
      operation: 'run_to',
      node_id: 'transform',
    })
    expect(() => parseStudioCommand({ operation: 'run_all', unexpected: true })).toThrow(
      StudioContractError,
    )
    expect(() => parseStudioCommand({ operation: 'run_to', node_id: 1 })).toThrow(
      /Python 0\.2\.0 Schema/,
    )
    expect(() => parseStudioCommand({ operation: 'unknown' })).toThrow(StudioContractError)
  })
})
