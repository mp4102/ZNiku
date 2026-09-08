import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ArtifactMediaSummary, formatHandoffValidationMessage, HandoffContract, HandoffPrecheckFailure, ReadinessMessages } from './HandoffContract'
import { handoffDetailEnvelope, handoffReadinessEnvelope } from './test-fixtures'

afterEach(cleanup)

describe('只读人工交付说明', () => {
  it('直接显示服务器的预期与实际帧数，历史失败仍标记为上次记录', () => {
    const readiness = handoffReadinessEnvelope('probe_failed', true)
    const message = 'E_RUNNER_VALIDATION_REJECTED: E_AV27_ENHANCEMENT_FRAME_COUNT: 增强结果帧数不符：预期 902 帧，实际 899 帧。请确认是否选错分段。'
    const failure = { ...readiness, targets: readiness.targets.map((target) => ({ ...target, message })) }
    render(<><ReadinessMessages readiness={failure} /><HandoffPrecheckFailure failure={failure} resolved /></>)
    expect(screen.getByRole('status')).toHaveTextContent('预期 902 帧，实际 899 帧')
    expect(screen.getByRole('status')).not.toHaveTextContent('E_RUNNER')
    expect(screen.getByText(/上次原因：增强结果/)).toBeVisible()
    expect(screen.getByText(/新的完整检查已通过/)).toBeVisible()
  })

  it('旧错误提供可操作解释，未知错误与缺失错误不臆造帧数', () => {
    expect(formatHandoffValidationMessage('E_AV27_ENHANCEMENT_FRAME_COUNT: Enhancement 输出不满足 N -> N')).toContain('确认文件是否对应这个分段')
    expect(formatHandoffValidationMessage('E_OTHER: 任意文件路径')).toBeNull()
    expect(formatHandoffValidationMessage(null)).toBeNull()
    expect(formatHandoffValidationMessage('prefix E_AV27_ENHANCEMENT_FRAME_COUNT: arbitrary')).toBeNull()
  })
  it('原样展示 Python 合同数值，不从 N 派生输出或替换服务器文本', () => {
    render(<HandoffContract contract={{
      node_run_id: 'node-run', handoff_id: 'handoff', input_artifact_id: 'input',
      title: 'Frame Interpolation 输出合同',
      fields: [
        { label: '输入 exact N', value: '100' },
        { label: '输出 exact N', value: '199' },
        { label: '输出 canonical FPS', value: '60000/1001' },
        { label: '输出 geometry', value: '不可用（不猜测）' },
        { label: 'toString', value: '只读扩展字段' },
        { label: '__proto__', value: '纯文本，不引用对象原型' },
      ],
    }} />)
    expect(screen.getByText('199')).toBeInTheDocument()
    expect(screen.getByText('60000/1001')).toBeInTheDocument()
    expect(screen.getByText('不可用（不猜测）')).toBeInTheDocument()
    expect(screen.queryByText('200')).not.toBeInTheDocument()
    expect(screen.getByText('toString')).toBeInTheDocument()
    expect(screen.getByText('__proto__')).toBeInTheDocument()
  })

  it('默认解释检测失败且高级详情保留服务端完整原因', () => {
    const readiness = handoffReadinessEnvelope('probe_failed', true)
    render(<ReadinessMessages readiness={{ ...readiness, targets: readiness.targets.map((target) => ({ ...target, message: 'E_AV27_FI_DOUBLE_COUNT: FI 输出必须精确为 2N-1' })) }} />)
    expect(screen.getByRole('status')).not.toHaveTextContent('E_AV27_FI_DOUBLE_COUNT')
    expect(screen.getByText(/E_AV27_FI_DOUBLE_COUNT/)).not.toBeVisible()
  })

  it('Source summary 直接显示已登记 exact N/FPS，header duration 不冒充 exact duration', () => {
    const artifact = handoffDetailEnvelope().artifacts[0]!
    render(<ArtifactMediaSummary artifact={{ ...artifact, media_info: {
      'zniku.avenhance.v27': { frame_count: 100, frame_rate: '30000/1001', duration_seconds: 3.336667, geometry: { width: 1920, height: 1080 }, signal: { color_space: 'bt709' }, audio_tracks: [{ codec: 'aac' }] },
    } }} />)
    expect(screen.getByText('100')).toBeInTheDocument()
    expect(screen.getByText('30000/1001')).toBeInTheDocument()
    expect(screen.getByText('探测时长（秒）')).toBeInTheDocument()
    expect(screen.getByText('3.336667')).toBeInTheDocument()
    expect(screen.getByText('高级 → 完整媒体登记信息（只读）')).toBeInTheDocument()
  })
})
