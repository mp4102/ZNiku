import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'
import { App } from '../App'
import { PythonFixtureGateway } from '../test/formal-gateway'
import {
  parseRealHostEnvelope,
  RealMediaBoundaryError,
  type RealHostCommand,
  type RealHostEnvelope,
  type RealMediaGateway,
} from './real-media'

afterEach(cleanup)

const digest = `sha256:${'a'.repeat(64)}`

function projection(state: 'ready' | 'complete'): RealHostEnvelope {
  return {
    host_contract_version: '0.1.0',
    candidate_exists: true,
    projection: {
      monitor_contract_version: '0.1.0',
      workflow_run_id: 'run.real.fixture',
      revision_id: 'revision.real.fixture',
      execution_plan_digest: digest,
      reference_filename: 'reference.mkv',
      reference_digest: digest,
      chapter_split_source_second: 30,
      chapters: [
        { member_id: 'chapter.001', scope_id: 'scope.001', start_frame: 0, end_frame: 60 },
        { member_id: 'chapter.002', scope_id: 'scope.002', start_frame: 60, end_frame: 120 },
      ],
      nodes: [{
        plan_node_id: 'plan.node.enhancement.chapter.001',
        stage_spec_id: 'node.enhancement',
        subject_kind: 'engine',
        scope: 'chapter',
        execution_mode: 'manual_external',
        state,
        attempt: state === 'complete' ? 1 : 0,
        evidence_id: state === 'complete' ? 'evidence.fixture' : null,
      }],
      handoffs: [],
      ready_node_ids: state === 'ready' ? ['plan.node.enhancement.chapter.001'] : [],
      artifact_count: state === 'complete' ? 2 : 1,
      evidence_count: state === 'complete' ? 1 : 0,
      final_verified: false,
      final_artifact_id: null,
      final_digest: null,
    },
  }
}

class FixtureRealMediaGateway implements RealMediaGateway {
  envelope: RealHostEnvelope = {
    host_contract_version: '0.1.0',
    candidate_exists: false,
    projection: null,
  }

  async inspect(): Promise<RealHostEnvelope> {
    return this.envelope
  }

  async command(command: RealHostCommand): Promise<RealHostEnvelope> {
    this.envelope = command.operation === 'acceptance_fixture' ? projection('complete') : projection('ready')
    return this.envelope
  }
}

describe('Real Media Acceptance Run Monitor', () => {
  it('拒绝 Python Schema 之外的 host payload', () => {
    expect(() => parseRealHostEnvelope({
      host_contract_version: '0.1.0',
      candidate_exists: false,
      projection: null,
      runtime_state: 'complete',
    })).toThrow(RealMediaBoundaryError)
  })

  it('只能通过 host 启动并提交人工验收 fixture', async () => {
    const user = userEvent.setup()
    render(
      <App
        gateway={new PythonFixtureGateway()}
        realMediaGateway={new FixtureRealMediaGateway()}
      />,
    )
    await screen.findByText('workflow.synthetic.program')
    await user.click(screen.getByRole('button', { name: 'Real Acceptance' }))
    await screen.findByRole('button', { name: '启动真实媒体验收候选' })
    await user.click(screen.getByRole('button', { name: '启动真实媒体验收候选' }))

    expect(await screen.findByText('reference.mkv')).toBeInTheDocument()
    const manual = screen.getByRole('button', {
      name: /生成并验收 fixture · plan.node.enhancement.chapter.001/,
    })
    await user.click(manual)
    expect(await screen.findByText('evidence.fixture')).toBeInTheDocument()
  })
})
