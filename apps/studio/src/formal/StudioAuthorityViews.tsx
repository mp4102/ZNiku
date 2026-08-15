import { studioAuthority } from './contracts'

const manifestByIdentity = new Map(
  studioAuthority.manifests.map((manifest) => [
    `${manifest.engine_id}@${manifest.engine_version}`,
    manifest,
  ]),
)

export function ExpandedPlanView() {
  const plan = studioAuthority.execution_plan
  return (
    <div className="authority-view" aria-label="Expanded Plan authority">
      <header className="authority-view__header">
        <div>
          <span className="eyebrow">IMMUTABLE EXECUTION PLAN</span>
          <h2>默认工作流 · 三章节展开</h2>
        </div>
        <code>{plan.execution_plan_id}</code>
      </header>
      <div className="authority-metrics">
        <span><strong>{studioAuthority.chapter_plan.members.length}</strong> chapters</span>
        <span><strong>{plan.nodes.length}</strong> planned nodes</span>
        <span><strong>{plan.nodes.filter((node) => node.scope === 'chapter').length}</strong> chapter runs</span>
        <span><strong>{plan.nodes.filter((node) => node.engine).length}</strong> Engine bindings</span>
      </div>
      <div className="chapter-strip" aria-label="Chapter plan">
        {studioAuthority.chapter_plan.members.map((member) => (
          <article key={member.member_id}>
            <strong>{member.member_id}</strong>
            <span>{member.coverage.start}–{member.coverage.end} frames</span>
            <code>{member.scope_id}</code>
          </article>
        ))}
      </div>
      <div className="plan-grid">
        {plan.nodes.map((node) => {
          const manifest = node.engine
            ? manifestByIdentity.get(`${node.engine.engine_id}@${node.engine.engine_version}`)
            : undefined
          return (
            <article className={`plan-card plan-card--${node.subject_kind}`} key={node.plan_node_id}>
              <div>
                <span>{node.subject_kind.toUpperCase()} · {node.scope}</span>
                {manifest?.execution_mode === 'manual_external' && <em>MANUAL HANDOFF</em>}
              </div>
              <strong>{node.stage_spec_id}</strong>
              <code>{node.plan_node_id}</code>
              <p>{node.dependencies.length === 0 ? 'root authority' : `depends on ${node.dependencies.join(', ')}`}</p>
              {node.engine && (
                <small>{node.engine.engine_id}@{node.engine.engine_version}</small>
              )}
            </article>
          )
        })}
      </div>
      <footer className="authority-digest">
        <span>ExecutionPlan digest</span>
        <code>{studioAuthority.workflow_revision.execution_plan_digest}</code>
      </footer>
    </div>
  )
}

export function RunMonitorView() {
  const snapshot = studioAuthority.runtime_snapshot
  const counts = new Map<string, number>()
  for (const node of snapshot.runtime.nodes) counts.set(node.state, (counts.get(node.state) ?? 0) + 1)
  return (
    <div className="authority-view" aria-label="Run Monitor authority">
      <header className="authority-view__header">
        <div>
          <span className="eyebrow">PYTHON-GENERATED RUNTIME SNAPSHOT</span>
          <h2>{snapshot.runtime.workflow_run_id}</h2>
        </div>
        <code>{snapshot.runtime.revision_id}</code>
      </header>
      <div className="authority-metrics">
        {['complete', 'ready', 'blocked', 'failed'].map((state) => (
          <span key={state} className={`state-metric state-metric--${state}`}>
            <strong>{counts.get(state) ?? 0}</strong> {state}
          </span>
        ))}
      </div>
      <div className="run-list">
        {snapshot.runtime.nodes.map((node) => (
          <article key={node.plan_node_id} className={`run-row run-row--${node.state}`}>
            <i aria-hidden="true" />
            <div><strong>{node.plan_node_id}</strong><span>attempt {node.attempt}</span></div>
            <em>{node.state}</em>
            <code>{node.evidence_id ?? '等待 Runtime Evidence'}</code>
          </article>
        ))}
      </div>
      <section className="audio-authority-card">
        <span className="eyebrow">EXPLICIT ORIGINAL AUDIO EDGE</span>
        <strong>{snapshot.audio_proof.artifact_set_id}</strong>
        <p>{snapshot.audio_proof.ordered_stream_ids.join(' → ')}</p>
        <code>Demux → Mux · stream_copy={String(snapshot.audio_proof.stream_copy)}</code>
      </section>
      <footer className="authority-digest">
        <span>Runtime plan authority</span>
        <code>{snapshot.runtime.execution_plan_digest}</code>
      </footer>
    </div>
  )
}

export function OperatorPaletteProjection() {
  const videoContracts = studioAuthority.operator_contracts.filter(
    (contract) => contract.media_kind === 'video',
  )
  return (
    <div className="operator-projection">
      <span className="eyebrow">CORE OPERATORS · PYTHON</span>
      {videoContracts.map((contract) => (
        <div key={contract.operator_kind}>
          <strong>{contract.operator_kind}</strong>
          <code>{contract.inputs.map((port) => port.port_id).join('+')} → {contract.outputs.map((port) => port.port_id).join('+')}</code>
        </div>
      ))}
    </div>
  )
}

export function EngineRegistryProjection() {
  return (
    <div className="operator-projection" aria-label="Installed Engine Registry">
      <span className="eyebrow">INSTALLED ENGINES · PYTHON REGISTRY</span>
      {studioAuthority.registry_manifests.map((manifest) => (
        <div key={`${manifest.engine_id}@${manifest.engine_version}`}>
          <strong>{manifest.display_name}</strong>
          <code>{manifest.engine_id}@{manifest.engine_version}</code>
        </div>
      ))}
    </div>
  )
}
