import fixtureJson from './fixtures/python-authority.json'
import type { AuthoringCommand, WorkflowDraftSnapshot } from '../generated/authoring-wire.generated'
import {
  parseAuthoringResponse,
  parseEngineManifest,
  verifyManifestBinding,
  verifySnapshot,
} from '../formal/contracts'
import type { AuthorityState, AuthoringGateway, GatewayReply } from '../formal/gateway'

type FixtureKey = 'initial' | 'connected' | 'invalid_parameters' | 'disconnected'

export class PythonFixtureGateway implements AuthoringGateway {
  private current: FixtureKey = 'initial'
  private readonly recorded = new Map<string, GatewayReply>()

  async loadDraft(): Promise<AuthorityState> {
    return this.authority(this.current)
  }

  async applyCommand(command: AuthoringCommand): Promise<GatewayReply> {
    const recorded = this.recorded.get(command.command_id)
    if (recorded) return recorded

    let reply: GatewayReply
    if (command.command_id === 'command.fixture.stale') {
      const parsed = parseAuthoringResponse(fixtureJson.stale_rejection)
      if (parsed.result_kind === 'draft_snapshot') throw new Error('stale fixture 必须是 rejection')
      reply = parsed
    } else if (command.intent.intent_kind === 'connect_ports') {
      this.current = 'connected'
      reply = await this.authority(this.current)
    } else if (command.intent.intent_kind === 'replace_parameters') {
      this.current = 'invalid_parameters'
      reply = await this.authority(this.current)
    } else if (command.intent.intent_kind === 'disconnect_ports') {
      this.current = 'disconnected'
      reply = await this.authority(this.current)
    } else {
      throw new Error(`fixture gateway 不支持 ${command.intent.intent_kind}`)
    }
    this.recorded.set(command.command_id, reply)
    return reply
  }

  private async authority(key: FixtureKey): Promise<AuthorityState> {
    const response = parseAuthoringResponse(fixtureJson[key])
    if (response.result_kind !== 'draft_snapshot') throw new Error(`${key} 不是 snapshot`)
    const snapshot: WorkflowDraftSnapshot = response
    const manifest = parseEngineManifest(fixtureJson.manifest)
    const engine = snapshot.spec.nodes.find((node) => node.kind === 'engine_stage')
    if (!engine || engine.kind !== 'engine_stage') throw new Error('fixture 缺少 EngineStage')
    await verifySnapshot(snapshot)
    await verifyManifestBinding(engine.engine, manifest)
    return { snapshot, manifests: [{ binding: engine.engine, manifest }] }
  }
}
