interface FreezeDialogProps {
  draftNodeCount: number
  draftEdgeCount: number
  onCancel: () => void
  onConfirm: () => void
}

export function FreezeDialog({ draftNodeCount, draftEdgeCount, onCancel, onConfirm }: FreezeDialogProps) {
  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="freeze-dialog" role="dialog" aria-modal="true" aria-labelledby="freeze-title">
        <header>
          <span className="eyebrow">MOCK FREEZE GATE</span>
          <h2 id="freeze-title">冻结 WorkflowRevision？</h2>
          <p>确认后 Designer 将切换为只读，并创建仅存在于浏览器内存的模拟 WorkflowRun。</p>
          <p>当前 Draft snapshot 已捕获；后续 Run Monitor 仍使用固定视觉 fixture，不是正式编译结果。</p>
        </header>

        <div className="freeze-grid">
          <div>
            <span>Source</span>
            <strong>MOCK_SOURCE · 1920×1080</strong>
          </div>
          <div>
            <span>Chapter plan</span>
            <strong>A · B · C</strong>
          </div>
          <div>
            <span>Draft snapshot</span>
            <strong>{draftNodeCount} nodes · {draftEdgeCount} edges</strong>
          </div>
          <div>
            <span>Manual stages</span>
            <strong>Enhancement · Decensoring · FI</strong>
          </div>
          <div>
            <span>Original audio</span>
            <strong>2 / 2 tracks · ordered · stream copy</strong>
          </div>
          <div>
            <span>Container path</span>
            <strong>Demux → audio_set → Mux → Final</strong>
          </div>
        </div>

        <div className="digest-box">
          <span>ExecutionPlan digest</span>
          <code>mock:sha256:7a2f…91cd</code>
        </div>

        <div className="dialog-warning">
          此操作不会写入媒体、Evidence、receipt 或正式 Runtime 状态。
        </div>

        <footer>
          <button className="button button--ghost" type="button" onClick={onCancel}>
            返回检查
          </button>
          <button className="button button--primary" type="button" onClick={onConfirm}>
            冻结并开始模拟运行
          </button>
        </footer>
      </section>
    </div>
  )
}
