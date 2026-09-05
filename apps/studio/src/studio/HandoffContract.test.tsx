import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ArtifactMediaSummary, HandoffContract, ReadinessMessages } from './HandoffContract'
import { handoffDetailEnvelope, handoffReadinessEnvelope } from './test-fixtures'

afterEach(cleanup)

describe('只读人工交付说明', () => {
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
