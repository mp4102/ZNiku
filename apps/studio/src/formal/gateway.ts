import type {
  AuthoringCommand,
  EngineBinding,
  WorkflowDraftSnapshot,
} from '../generated/authoring-wire.generated'
import type { EngineManifest } from '../generated/engine-manifest.generated'
import {
  ContractBoundaryError,
  parseAuthoringResponse,
  parseEngineManifest,
  verifyManifestBinding,
  verifySnapshot,
  type AuthoringResponse,
} from './contracts'

export interface ResolvedManifest {
  readonly binding: EngineBinding
  readonly manifest: EngineManifest
}

export interface AuthorityState {
  readonly snapshot: WorkflowDraftSnapshot
  readonly manifests: ReadonlyArray<ResolvedManifest>
}

export type GatewayReply = AuthorityState | Exclude<AuthoringResponse, WorkflowDraftSnapshot>

export interface AuthoringGateway {
  loadDraft(draftId: string): Promise<AuthorityState>
  applyCommand(command: AuthoringCommand): Promise<GatewayReply>
}

export interface ZnikuAuthoringBridge {
  invoke(request: {
    readonly operation: 'load_draft' | 'apply_command' | 'load_manifest'
    readonly payload: unknown
  }): Promise<unknown>
}

declare global {
  interface Window {
    znikuAuthoringBridge?: ZnikuAuthoringBridge
  }
}

export class GatewayUnavailableError extends Error {
  constructor(message = 'Python Authoring authority 当前不可用') {
    super(message)
    this.name = 'GatewayUnavailableError'
  }
}

export class WindowAuthoringGateway implements AuthoringGateway {
  constructor(private readonly bridge: ZnikuAuthoringBridge | undefined) {}

  async loadDraft(draftId: string): Promise<AuthorityState> {
    const bridge = this.requireBridge()
    const response = parseAuthoringResponse(
      await bridge.invoke({ operation: 'load_draft', payload: { draft_id: draftId } }),
    )
    if (response.result_kind !== 'draft_snapshot') {
      throw new ContractBoundaryError(`load_draft 返回了 ${response.result_kind}`)
    }
    return this.hydrateSnapshot(response)
  }

  async applyCommand(command: AuthoringCommand): Promise<GatewayReply> {
    const bridge = this.requireBridge()
    const response = parseAuthoringResponse(
      await bridge.invoke({ operation: 'apply_command', payload: command }),
    )
    if (response.result_kind === 'command_rejected') {
      if (
        response.command_id !== command.command_id ||
        response.draft_id !== command.draft_id ||
        response.base_revision !== command.base_revision
      ) {
        throw new ContractBoundaryError('command rejection correlation 与提交命令不一致')
      }
      return response
    }
    if (response.result_kind === 'wire_parse_failure') return response
    return this.hydrateSnapshot(response)
  }

  private requireBridge(): ZnikuAuthoringBridge {
    if (!this.bridge) throw new GatewayUnavailableError()
    return this.bridge
  }

  private async hydrateSnapshot(snapshot: WorkflowDraftSnapshot): Promise<AuthorityState> {
    await verifySnapshot(snapshot)
    const bindings = snapshot.spec.nodes
      .flatMap((node) => {
        if (node.kind === 'engine_stage') return [node.engine]
        if (node.kind === 'core_operator' && node.engine != null) return [node.engine]
        return []
      })
    const unique = new Map(bindings.map((binding) => [binding.manifest_digest, binding]))
    const manifests = await Promise.all(
      Array.from(unique.values(), async (binding) => {
        const manifest = parseEngineManifest(
          await this.requireBridge().invoke({ operation: 'load_manifest', payload: binding }),
        )
        await verifyManifestBinding(binding, manifest)
        return { binding, manifest }
      }),
    )
    return { snapshot, manifests }
  }
}

export function createWindowAuthoringGateway(): AuthoringGateway {
  return new WindowAuthoringGateway(window.znikuAuthoringBridge)
}
