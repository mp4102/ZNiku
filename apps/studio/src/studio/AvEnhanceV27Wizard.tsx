/**
 * 提供 AVEnhanceFlow v2.7.0 的两段式受控表单。
 *
 * 组件只收集操作者输入并渲染 Python preview；Chapter/Leaf、dynamic ports、输出命名与
 * profile compatibility 都不得在浏览器中派生。mutation 只有在同一份 server preview 仍然
 * compatible 时才可提交，防止 Advanced JSON 或迟到响应成为模板 authority。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  AvEnhanceV27ChapterSelectorWire,
  AvEnhanceV27ExpandRequestWire,
  AvEnhanceV27PrepareRequestWire,
  AvEnhanceV27SourceMode,
  AvEnhanceV27TemplatePreviewEnvelope,
  AvEnhanceV27TemplatePreviewRequestWire,
  ProjectSnapshotWire,
  RunSummaryWire,
} from './contracts'

type WizardAction = 'prepare' | 'expand'
type SelectorMode = AvEnhanceV27ChapterSelectorWire['mode']

interface SourceDraft {
  readonly source_path: string
  readonly chapter_label: string
}

export interface AvEnhanceV27WizardProps {
  readonly open: boolean
  readonly busy: boolean
  readonly serviceError?: string | null
  readonly currentSnapshot: ProjectSnapshotWire | null
  readonly currentProjectPath: string
  readonly currentProjectId: string
  readonly currentProjectName: string
  readonly runSummaries: ReadonlyArray<RunSummaryWire>
  readonly onClose: () => void
  readonly onPreview: (
    request: AvEnhanceV27TemplatePreviewRequestWire,
  ) => Promise<AvEnhanceV27TemplatePreviewEnvelope | null>
  readonly onCreate: (request: AvEnhanceV27PrepareRequestWire) => Promise<boolean>
  readonly onExpand: (request: AvEnhanceV27ExpandRequestWire) => Promise<boolean>
  readonly onLocateNode: (nodeId: string) => void
}

function optionalText(value: string): string | undefined {
  // 只把真正的空控件解释为“未提供”；显式空白必须原样交给 Python Schema 拒绝，
  // 不能由 Studio 悄悄规范化成合法缺省值。
  return value === '' ? undefined : value
}

function positiveInteger(value: string, label: string): number {
  if (!/^[1-9][0-9]*$/.test(value)) throw new Error(`${label} 必须是严格正整数。`)
  const parsed = Number(value)
  if (!Number.isSafeInteger(parsed)) throw new Error(`${label} 超出安全整数范围。`)
  return parsed
}

function parseFrames(value: string): number[] {
  const tokens = value
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
  if (tokens.length === 0) throw new Error('exact_frames 至少需要一个章节切分首帧。')
  return tokens.map((item) => positiveInteger(item, '章节首帧'))
}

function parseTimes(value: string): string[] {
  const tokens = value
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
  if (tokens.length === 0) throw new Error('exact_times 至少需要一个 canonical rational 秒值。')
  return tokens
}

function sourceModeFromSnapshot(
  snapshot: ProjectSnapshotWire | null,
): AvEnhanceV27SourceMode | null {
  const admission = snapshot?.project.graph.nodes.find(
    (node) => node.type_id === 'zniku.avenhance.v27.source_admission',
  )
  const value = admission?.parameters.source_mode
  return value === 'program' || value === 'pre_chaptered' ? value : null
}

function phaseLabel(action: WizardAction): string {
  return action === 'prepare' ? '1 · 创建 Preparation Graph' : '2 · 展开 Production Graph'
}

export function AvEnhanceV27Wizard({
  open,
  busy,
  serviceError = null,
  currentSnapshot,
  currentProjectPath,
  currentProjectId,
  currentProjectName,
  runSummaries,
  onClose,
  onPreview,
  onCreate,
  onExpand,
  onLocateNode,
}: AvEnhanceV27WizardProps) {
  const wasOpen = useRef(false)
  const dialogRef = useRef<HTMLElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)
  const previewRequestRef = useRef<AvEnhanceV27TemplatePreviewRequestWire | null>(null)
  const [action, setAction] = useState<WizardAction>('prepare')
  const [preview, setPreview] = useState<AvEnhanceV27TemplatePreviewEnvelope | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const [projectPath, setProjectPath] = useState('')
  const [projectId, setProjectId] = useState('project.av27')
  const [projectName, setProjectName] = useState('AVEnhanceFlow v2.7 Project')
  const [sourceMode, setSourceMode] = useState<AvEnhanceV27SourceMode>('program')
  const [sources, setSources] = useState<ReadonlyArray<SourceDraft>>([
    { source_path: '', chapter_label: '' },
  ])
  const [mrMode, setMrMode] = useState<'off' | 'external'>('off')
  const [mrModelName, setMrModelName] = useState('')
  const [mrModelVersion, setMrModelVersion] = useState('')

  const [preparationRunId, setPreparationRunId] = useState('')
  const [selectorMode, setSelectorMode] = useState<SelectorMode>('single')
  const [selectorFrames, setSelectorFrames] = useState('')
  const [selectorTimes, setSelectorTimes] = useState('')
  const [leafDurationMinutes, setLeafDurationMinutes] = useState('1')
  const [enhancementModelName, setEnhancementModelName] = useState('')
  const [enhancementModelVersion, setEnhancementModelVersion] = useState('')
  const [actualScaleFactor, setActualScaleFactor] = useState('1')
  const [fiModelName, setFiModelName] = useState('')
  const [fiModelVersion, setFiModelVersion] = useState('')
  const [encoder, setEncoder] = useState<'gpu' | 'cpu'>('gpu')
  const [outputRoot, setOutputRoot] = useState('')
  const [title, setTitle] = useState('')
  const [year, setYear] = useState('')
  const [overwrite, setOverwrite] = useState(false)

  const currentSourceMode = sourceModeFromSnapshot(currentSnapshot)
  const completedRuns = useMemo(
    () => runSummaries.filter((summary) => summary.state === 'completed'),
    [runSummaries],
  )

  useEffect(() => {
    if (open && !wasOpen.current) {
      previousFocusRef.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null
      setAction(currentSnapshot ? 'expand' : 'prepare')
      setPreview(null)
      previewRequestRef.current = null
      setLocalError(null)
      setSubmitting(false)
      setProjectPath(currentProjectPath)
      setProjectId(currentProjectId || 'project.av27')
      setProjectName(currentProjectName || 'AVEnhanceFlow v2.7 Project')
      setPreparationRunId('')
      closeButtonRef.current?.focus()
    } else if (!open && wasOpen.current) {
      previousFocusRef.current?.focus()
      previousFocusRef.current = null
    }
    wasOpen.current = open
  }, [currentProjectId, currentProjectName, currentProjectPath, currentSnapshot, open])

  useEffect(() => {
    if (!open) return
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting && !busy) {
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const dialog = dialogRef.current
      if (!dialog) return
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        ),
      )
      if (focusable.length === 0) {
        event.preventDefault()
        return
      }
      const first = focusable[0]!
      const last = focusable.at(-1)!
      if (
        event.shiftKey &&
        (document.activeElement === first || !dialog.contains(document.activeElement))
      ) {
        event.preventDefault()
        last.focus()
      } else if (
        !event.shiftKey &&
        (document.activeElement === last || !dialog.contains(document.activeElement))
      ) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [busy, onClose, open, submitting])

  if (!open) return null

  const invalidate = () => {
    setPreview(null)
    previewRequestRef.current = null
    setLocalError(null)
  }

  const switchAction = (next: WizardAction) => {
    setAction(next)
    invalidate()
  }

  const buildPrepareRequest = (): AvEnhanceV27PrepareRequestWire => {
    if (!projectPath.trim() || !projectId.trim() || !projectName.trim()) {
      throw new Error('Project path、Project ID 与 Project name 均为必填。')
    }
    if (sources.length === 0) throw new Error('至少需要一个 Source。')
    if (sourceMode === 'program' && sources.length !== 1) {
      throw new Error('program source mode 精确需要一个 Source。')
    }
    const normalizedSources = sources.map((source, sourceOrdinal) => {
      if (!source.source_path.trim()) throw new Error(`Source ${sourceOrdinal + 1} path 为必填。`)
      if (sourceMode === 'pre_chaptered') {
        if (!source.chapter_label.trim()) {
          throw new Error(`Source ${sourceOrdinal + 1} chapter label 为必填。`)
        }
        return {
          source_path: source.source_path,
          source_ordinal: sourceOrdinal,
          chapter_label: source.chapter_label,
        }
      }
      return { source_path: source.source_path, source_ordinal: sourceOrdinal }
    })
    if (mrMode === 'external') {
      if (!mrModelName.trim() || !mrModelVersion.trim()) {
        throw new Error('external MR 的 model name 与 model version 均为必填。')
      }
    }
    return {
      profile_version: '2.7.0',
      project_path: projectPath,
      project_id: projectId,
      project_name: projectName,
      source_mode: sourceMode,
      sources: normalizedSources,
      mr:
        mrMode === 'off'
          ? { mode: 'off' }
          : {
              mode: 'external',
              model_name: mrModelName,
              model_version: mrModelVersion,
            },
    }
  }

  const buildExpandRequest = (): AvEnhanceV27ExpandRequestWire => {
    if (!currentSnapshot) throw new Error('Expand 必须作用于当前已经打开的 Project。')
    if (!preparationRunId) throw new Error('必须显式选择一个 completed preparation Run。')
    if (!enhancementModelName.trim() || !fiModelName.trim()) {
      throw new Error('Enhancement 与 Frame Interpolation model name 均为必填。')
    }
    if (!outputRoot.trim() || !title.trim() || !year.trim()) {
      throw new Error('Output root、title 与 4 位 year 均为必填。')
    }

    let chapterSelector: AvEnhanceV27ChapterSelectorWire | undefined
    if (currentSourceMode !== 'pre_chaptered') {
      chapterSelector =
        selectorMode === 'single'
          ? { mode: 'single' }
          : selectorMode === 'exact_frames'
            ? { mode: 'exact_frames', frames: parseFrames(selectorFrames) }
            : { mode: 'exact_times', times: parseTimes(selectorTimes) }
    }
    const scale = positiveInteger(actualScaleFactor, 'actual_scale_factor')
    const enhancementVersion = optionalText(enhancementModelVersion)
    const fiVersion = optionalText(fiModelVersion)
    return {
      profile_version: '2.7.0',
      preparation_run_id: preparationRunId,
      ...(chapterSelector ? { chapter_selector: chapterSelector } : {}),
      leaf_duration_minutes: positiveInteger(
        leafDurationMinutes,
        'leaf_duration_minutes',
      ),
      enhancement: {
        model_name: enhancementModelName,
        ...(enhancementVersion ? { model_version: enhancementVersion } : {}),
        ...(scale === 1 ? {} : { actual_scale_factor: scale }),
      },
      frame_interpolation: {
        model_name: fiModelName,
        ...(fiVersion ? { model_version: fiVersion } : {}),
      },
      program_encode: { encoder },
      publication: {
        output_root: outputRoot,
        title,
        year,
        overwrite,
      },
    }
  }

  const previewCurrent = async () => {
    setSubmitting(true)
    setPreview(null)
    previewRequestRef.current = null
    setLocalError(null)
    try {
      const request: AvEnhanceV27TemplatePreviewRequestWire =
        action === 'prepare'
          ? { action: 'prepare', request: buildPrepareRequest() }
          : { action: 'expand', request: buildExpandRequest() }
      const next = await onPreview(request)
      const expectedPhase = request.action === 'prepare' ? 'preparation' : 'expanded'
      if (next?.phase === expectedPhase) {
        previewRequestRef.current = request
        setPreview(next)
      } else if (next) {
        setLocalError('Project Service preview phase 与请求 action 不一致；未执行任何 mutation。')
      } else {
        setLocalError('Project Service 未返回可用 preview；未执行任何 mutation。')
      }
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : '模板 preview 失败。')
    } finally {
      setSubmitting(false)
    }
  }

  const applyCurrent = async () => {
    const authorityRequest = previewRequestRef.current
    if (!preview?.profile.compatible || !authorityRequest || authorityRequest.action !== action) {
      invalidate()
      setLocalError('当前表单没有可应用的 compatible server preview；请重新 preview。')
      return
    }
    setSubmitting(true)
    setLocalError(null)
    try {
      const applied =
        authorityRequest.action === 'prepare'
          ? await onCreate(authorityRequest.request)
          : await onExpand(authorityRequest.request)
      previewRequestRef.current = null
      if (applied) {
        onClose()
      } else {
        setPreview(null)
        setLocalError('Project Service 未应用模板；preview 已失效，Project 保持不变。')
      }
    } catch (error) {
      setPreview(null)
      previewRequestRef.current = null
      setLocalError(error instanceof Error ? error.message : '模板 mutation 失败。')
    } finally {
      setSubmitting(false)
    }
  }

  const updateSource = (index: number, patch: Partial<SourceDraft>) => {
    setSources((current) =>
      current.map((source, sourceIndex) =>
        sourceIndex === index ? { ...source, ...patch } : source,
      ),
    )
    invalidate()
  }

  const controlsDisabled = busy || submitting

  return (
    <div className="template-wizard-backdrop" role="presentation">
      <section
        aria-label="AVEnhanceFlow v2.7.0 模板向导"
        aria-modal="true"
        className="template-wizard"
        ref={dialogRef}
        role="dialog"
      >
        <header className="template-wizard-header">
          <div>
            <span className="eyebrow">SERVER-SIDE WORKFLOW TEMPLATE</span>
            <h2>AVEnhanceFlow v2.7.0</h2>
            <p>先建立 admitted Artifact，再由 Python 从 exact N/FPS 展开普通 DAG。</p>
          </div>
          <button
            aria-label="关闭模板向导"
            className="template-wizard-close"
            disabled={controlsDisabled}
            onClick={onClose}
            ref={closeButtonRef}
            type="button"
          >
            ×
          </button>
        </header>

        <div className="template-phase-tabs" role="tablist" aria-label="模板阶段">
          <button
            aria-selected={action === 'prepare'}
            aria-controls="av27-template-panel"
            className={action === 'prepare' ? 'is-active' : ''}
            disabled={controlsDisabled}
            onClick={() => switchAction('prepare')}
            role="tab"
            type="button"
          >
            1 · Preparation
          </button>
          <button
            aria-selected={action === 'expand'}
            aria-controls="av27-template-panel"
            className={action === 'expand' ? 'is-active' : ''}
            disabled={controlsDisabled || !currentSnapshot}
            onClick={() => switchAction('expand')}
            role="tab"
            type="button"
          >
            2 · Expand workflow
          </button>
        </div>

        <div
          aria-label={phaseLabel(action)}
          className="template-wizard-body"
          id="av27-template-panel"
          role="tabpanel"
        >
          <form className="template-form" onSubmit={(event) => event.preventDefault()}>
            <div className="template-phase-explainer">
              <strong>{phaseLabel(action)}</strong>
              <p>
                {action === 'prepare'
                  ? '本阶段只生成 SourceProgram、SourceAdmission 与可选 MR；不会预先生成 Chapter、Leaf 或下游。'
                  : '显式绑定 completed preparation Run；Chapter/Leaf、Split shape 与发布名称全部由服务端返回。展开后点击 Run all 创建第二个普通 Run，并复用仍有效的 preparation 节点。'}
              </p>
            </div>

            {action === 'prepare' ? (
              <>
                <fieldset>
                  <legend>Project identity</legend>
                  <label>
                    .zniku project path
                    <input
                      aria-label="模板工程路径"
                      disabled={controlsDisabled}
                      onChange={(event) => {
                        setProjectPath(event.target.value)
                        invalidate()
                      }}
                      placeholder="D:\\Projects\\movie.zniku"
                      value={projectPath}
                    />
                  </label>
                  <div className="template-form-grid">
                    <label>
                      Project ID
                      <input
                        aria-label="模板 Project ID"
                        disabled={controlsDisabled}
                        onChange={(event) => {
                          setProjectId(event.target.value)
                          invalidate()
                        }}
                        value={projectId}
                      />
                    </label>
                    <label>
                      Project name
                      <input
                        aria-label="模板 Project name"
                        disabled={controlsDisabled}
                        onChange={(event) => {
                          setProjectName(event.target.value)
                          invalidate()
                        }}
                        value={projectName}
                      />
                    </label>
                  </div>
                </fieldset>

                <fieldset>
                  <legend>Source admission</legend>
                  <label>
                    Source mode
                    <select
                      aria-label="模板 Source mode"
                      disabled={controlsDisabled}
                      onChange={(event) => {
                        const next = event.target.value as AvEnhanceV27SourceMode
                        setSourceMode(next)
                        if (next === 'program') setSources((current) => current.slice(0, 1))
                        invalidate()
                      }}
                      value={sourceMode}
                    >
                      <option value="program">program · 单一完整节目</option>
                      <option value="pre_chaptered">pre_chaptered · 每个 Source 一章</option>
                    </select>
                  </label>
                  <div className="template-source-list">
                    {sources.map((source, index) => (
                      <article key={index}>
                        <header>
                          <strong>Source {index + 1}</strong>
                          <code>ordinal {index}</code>
                          {sourceMode === 'pre_chaptered' && sources.length > 1 && (
                            <button
                              aria-label={`移除 Source ${index + 1}`}
                              disabled={controlsDisabled}
                              onClick={() => {
                                setSources((current) => current.filter((_, item) => item !== index))
                                invalidate()
                              }}
                              type="button"
                            >
                              移除
                            </button>
                          )}
                        </header>
                        <label>
                          Source path
                          <input
                            aria-label={`Source ${index + 1} path`}
                            disabled={controlsDisabled}
                            onChange={(event) =>
                              updateSource(index, { source_path: event.target.value })
                            }
                            value={source.source_path}
                          />
                        </label>
                        {sourceMode === 'pre_chaptered' && (
                          <label>
                            Chapter label
                            <input
                              aria-label={`Source ${index + 1} chapter label`}
                              disabled={controlsDisabled}
                              onChange={(event) =>
                                updateSource(index, { chapter_label: event.target.value })
                              }
                              value={source.chapter_label}
                            />
                          </label>
                        )}
                      </article>
                    ))}
                  </div>
                  {sourceMode === 'pre_chaptered' && (
                    <button
                      className="button button--ghost"
                      disabled={controlsDisabled}
                      onClick={() => {
                        setSources((current) => [
                          ...current,
                          { source_path: '', chapter_label: '' },
                        ])
                        invalidate()
                      }}
                      type="button"
                    >
                      添加 Source
                    </button>
                  )}
                </fieldset>

                <fieldset>
                  <legend>Mosaic Restoration</legend>
                  <label>
                    MR mode
                    <select
                      aria-label="模板 MR mode"
                      disabled={controlsDisabled}
                      onChange={(event) => {
                        setMrMode(event.target.value as 'off' | 'external')
                        invalidate()
                      }}
                      value={mrMode}
                    >
                      <option value="off">off</option>
                      <option value="external">external · manual handoff (.mkv)</option>
                    </select>
                  </label>
                  {mrMode === 'external' && (
                    <div className="template-form-grid">
                      <label>
                        Model name
                        <input
                          aria-label="MR model name"
                          disabled={controlsDisabled}
                          onChange={(event) => {
                            setMrModelName(event.target.value)
                            invalidate()
                          }}
                          value={mrModelName}
                        />
                      </label>
                      <label>
                        Model version
                        <input
                          aria-label="MR model version"
                          disabled={controlsDisabled}
                          onChange={(event) => {
                            setMrModelVersion(event.target.value)
                            invalidate()
                          }}
                          value={mrModelVersion}
                        />
                      </label>
                    </div>
                  )}
                </fieldset>
              </>
            ) : (
              <>
                <fieldset>
                  <legend>Preparation binding</legend>
                  <label>
                    Completed preparation Run
                    <select
                      aria-label="Preparation Run"
                      disabled={controlsDisabled}
                      onChange={(event) => {
                        setPreparationRunId(event.target.value)
                        invalidate()
                      }}
                      value={preparationRunId}
                    >
                      <option value="">请选择；不从 active/latest Run 猜测</option>
                      {completedRuns.map((summary) => (
                        <option key={summary.run_id} value={summary.run_id}>
                          {summary.run_id} · {summary.created_at}
                        </option>
                      ))}
                    </select>
                  </label>
                  <p className="template-field-note">
                    当前 Project source mode：{currentSourceMode ?? '未识别；服务端将失败关闭'}
                  </p>
                </fieldset>

                <fieldset>
                  <legend>Chapter selector</legend>
                  {currentSourceMode === 'pre_chaptered' ? (
                    <p className="template-field-note">
                      pre_chaptered 按 admitted Source ordinal 派生一 Source 一章；请求不会发送 chapter_selector。
                    </p>
                  ) : (
                    <>
                      <label>
                        Selector mode
                        <select
                          aria-label="Chapter selector mode"
                          disabled={controlsDisabled}
                          onChange={(event) => {
                            setSelectorMode(event.target.value as SelectorMode)
                            invalidate()
                          }}
                          value={selectorMode}
                        >
                          <option value="single">single</option>
                          <option value="exact_frames">exact_frames</option>
                          <option value="exact_times">exact_times · canonical rational seconds</option>
                        </select>
                      </label>
                      {selectorMode === 'exact_frames' && (
                        <label>
                          Next chapter first frames
                          <textarea
                            aria-label="Exact chapter frames"
                            disabled={controlsDisabled}
                            onChange={(event) => {
                              setSelectorFrames(event.target.value)
                              invalidate()
                            }}
                            placeholder="54000, 108000"
                            rows={3}
                            value={selectorFrames}
                          />
                        </label>
                      )}
                      {selectorMode === 'exact_times' && (
                        <label>
                          Canonical rational seconds
                          <textarea
                            aria-label="Exact chapter times"
                            disabled={controlsDisabled}
                            onChange={(event) => {
                              setSelectorTimes(event.target.value)
                              invalidate()
                            }}
                            placeholder="1800, 7207200/1001"
                            rows={3}
                            value={selectorTimes}
                          />
                        </label>
                      )}
                    </>
                  )}
                  <label>
                    Enhancement leaf duration (positive integer minutes)
                    <input
                      aria-label="Leaf duration minutes"
                      disabled={controlsDisabled}
                      inputMode="numeric"
                      onChange={(event) => {
                        setLeafDurationMinutes(event.target.value)
                        invalidate()
                      }}
                      value={leafDurationMinutes}
                    />
                  </label>
                  <p className="template-field-note">
                    Chapter selector 只定义章边界；LeafPlan 由 Python 根据 admitted FPS 与分钟数确定性派生。
                  </p>
                </fieldset>

                <fieldset>
                  <legend>Manual stages</legend>
                  <div className="template-form-grid">
                    <label>
                      Enhancement model name
                      <input
                        aria-label="Enhancement model name"
                        disabled={controlsDisabled}
                        onChange={(event) => {
                          setEnhancementModelName(event.target.value)
                          invalidate()
                        }}
                        value={enhancementModelName}
                      />
                    </label>
                    <label>
                      Enhancement model version (optional)
                      <input
                        aria-label="Enhancement model version"
                        disabled={controlsDisabled}
                        onChange={(event) => {
                          setEnhancementModelVersion(event.target.value)
                          invalidate()
                        }}
                        value={enhancementModelVersion}
                      />
                    </label>
                    <label>
                      Actual scale factor
                      <input
                        aria-label="Enhancement actual scale factor"
                        disabled={controlsDisabled}
                        inputMode="numeric"
                        onChange={(event) => {
                          setActualScaleFactor(event.target.value)
                          invalidate()
                        }}
                        value={actualScaleFactor}
                      />
                    </label>
                    <label>
                      FI model name
                      <input
                        aria-label="FI model name"
                        disabled={controlsDisabled}
                        onChange={(event) => {
                          setFiModelName(event.target.value)
                          invalidate()
                        }}
                        value={fiModelName}
                      />
                    </label>
                    <label>
                      FI model version (optional)
                      <input
                        aria-label="FI model version"
                        disabled={controlsDisabled}
                        onChange={(event) => {
                          setFiModelVersion(event.target.value)
                          invalidate()
                        }}
                        value={fiModelVersion}
                      />
                    </label>
                  </div>
                </fieldset>

                <fieldset>
                  <legend>Program & publication</legend>
                  <div className="template-form-grid">
                    <label>
                      Program encoder
                      <select
                        aria-label="Program encoder"
                        disabled={controlsDisabled}
                        onChange={(event) => {
                          setEncoder(event.target.value as 'gpu' | 'cpu')
                          invalidate()
                        }}
                        value={encoder}
                      >
                        <option value="gpu">GPU · hevc_nvenc</option>
                        <option value="cpu">CPU · libx265</option>
                      </select>
                    </label>
                    <label>
                      Output root
                      <input
                        aria-label="Publication output root"
                        disabled={controlsDisabled}
                        onChange={(event) => {
                          setOutputRoot(event.target.value)
                          invalidate()
                        }}
                        value={outputRoot}
                      />
                    </label>
                    <label>
                      Title
                      <input
                        aria-label="Publication title"
                        disabled={controlsDisabled}
                        onChange={(event) => {
                          setTitle(event.target.value)
                          invalidate()
                        }}
                        value={title}
                      />
                    </label>
                    <label>
                      Year
                      <input
                        aria-label="Publication year"
                        disabled={controlsDisabled}
                        inputMode="numeric"
                        maxLength={4}
                        onChange={(event) => {
                          setYear(event.target.value)
                          invalidate()
                        }}
                        value={year}
                      />
                    </label>
                  </div>
                  <label className="template-check">
                    <input
                      aria-label="允许覆盖发布目标"
                      checked={overwrite}
                      disabled={controlsDisabled}
                      onChange={(event) => {
                        setOverwrite(event.target.checked)
                        invalidate()
                      }}
                      type="checkbox"
                    />
                    显式允许覆盖已经存在的 canonical target
                  </label>
                  <p className="template-field-note">
                    Title 子目录必须已经存在；preview 与 mutation 都不会 mkdir 或自动改名。
                  </p>
                </fieldset>
              </>
            )}
          </form>

          <section
            aria-busy={submitting}
            aria-label="模板 Server preview"
            className="template-preview"
          >
            <header>
              <span className="eyebrow">PYTHON AUTHORITY PREVIEW</span>
              <h3>Graph 与 profile preflight</h3>
            </header>
            {!preview ? (
              <div className="template-preview-empty">
                <strong>尚未建立 preview</strong>
                <p>preview 是只读操作；不会创建 Project、目录、Run 或 Artifact。</p>
              </div>
            ) : (
              <>
                <div
                  className={`template-profile-badge ${preview.profile.compatible ? 'is-compatible' : 'is-incompatible'}`}
                  role="status"
                >
                  <strong>{preview.profile.status}</strong>
                  <span>
                    {preview.profile.phase} · {preview.phase} · AVEnhanceFlow {preview.profile_version}
                  </span>
                  {preview.profile.status === 'replan_required' && <em>replan required</em>}
                </div>
                <dl className="template-preview-counts">
                  <div><dt>Graph</dt><dd>{preview.project.graph.nodes.length} nodes / {preview.project.graph.edges.length} edges</dd></div>
                  <div><dt>Definitions</dt><dd>{preview.definitions.length} exact versions</dd></div>
                  <div><dt>Sources</dt><dd>{preview.plan.source_count}</dd></div>
                  <div><dt>Chapters / leaves</dt><dd>{preview.plan.chapter_count} / {preview.plan.leaf_count}</dd></div>
                  <div><dt>MR</dt><dd>{preview.plan.mr_mode}</dd></div>
                </dl>

                <div className="template-planning-source">
                  <strong>Effective-video planning</strong>
                  <p>
                    {preview.plan.mr_mode === 'external'
                      ? 'Chapter 与 Leaf 只基于 admitted MR output；不会在原 Source 上预冻。'
                      : 'MR off；Chapter 与 Leaf 基于 admitted SourceProgram video。'}
                  </p>
                  {preview.plan.effective_video_artifact_ids.map((artifactId) => (
                    <code key={artifactId}>{artifactId}</code>
                  ))}
                </div>

                {preview.plan.manual_stages.length > 0 && (
                  <section className="template-manual-summary">
                    <h4>人工节点与交付容器</h4>
                    {preview.plan.manual_stages.map((stage) => (
                      <div key={stage.stage}>
                        <strong>{stage.stage}</strong>
                        <span>{stage.node_count} nodes</span>
                        <code>{stage.output_container}</code>
                      </div>
                    ))}
                  </section>
                )}

                {preview.plan.output_target_path && (
                  <div className="template-target-path">
                    <span>Canonical publication target</span>
                    <code>{preview.plan.output_target_path}</code>
                  </div>
                )}

                {preview.plan.chapters.length > 0 && (
                  <section className="template-plan" aria-label="服务端 Chapter 与 Leaf plan">
                    <h4>Server-derived Chapter / Leaf plan</h4>
                    {preview.plan.chapters.map((chapter) => (
                      <article key={chapter.chapter_id}>
                        <header>
                          <strong>{chapter.label} · {chapter.chapter_id}</strong>
                          <span>source #{chapter.source_ordinal} · ordinal {chapter.chapter_ordinal}</span>
                        </header>
                        <p>
                          {chapter.start_timecode} ({chapter.start_time_seconds}s / frame {chapter.start_frame}) →{' '}
                          {chapter.end_timecode} ({chapter.end_time_seconds}s / frame {chapter.end_frame})
                        </p>
                        <code>[{chapter.start_frame}, {chapter.end_frame})</code>
                        <div className="template-leaf-list">
                          {chapter.leaves.map((leaf) => (
                            <div key={leaf.leaf_id}>
                              <strong>{leaf.leaf_id}</strong>
                              <span>{leaf.start_timecode} → {leaf.end_timecode}</span>
                              <code>[{leaf.start_frame}, {leaf.end_frame}) · {leaf.port_id}</code>
                            </div>
                          ))}
                        </div>
                      </article>
                    ))}
                  </section>
                )}

                <section className="template-node-summary">
                  <h4>可读节点 ID</h4>
                  <div>
                    {preview.project.graph.nodes.map((node) => (
                      <code key={node.node_id}>{node.node_id}</code>
                    ))}
                  </div>
                </section>

                {preview.profile.diagnostics.length > 0 && (
                  <section className="template-diagnostics" aria-label="Template profile diagnostics">
                    <h4>Template profile diagnostics</h4>
                    {preview.profile.diagnostics.map((diagnostic, index) => (
                      <article key={`${diagnostic.code}-${diagnostic.field_path ?? 'graph'}-${index}`}>
                        <span>{diagnostic.code}</span>
                        <code>{diagnostic.field_path ?? 'graph'}</code>
                        <p>{diagnostic.message}</p>
                        {diagnostic.node_id && (
                          <button
                            onClick={() => onLocateNode(diagnostic.node_id!)}
                            type="button"
                          >
                            定位 {diagnostic.node_id}
                          </button>
                        )}
                      </article>
                    ))}
                  </section>
                )}
              </>
            )}
          </section>
        </div>

        <footer className="template-wizard-footer">
          <div>
            {(localError || serviceError) && (
              <div className="template-error-stack" role="alert">
                {serviceError && serviceError !== localError && (
                  <p className="template-local-error">{serviceError}</p>
                )}
                {localError && <p className="template-local-error">{localError}</p>}
              </div>
            )}
            {!localError && preview && !preview.profile.compatible && (
              <p>Profile preflight 未通过；Graph Core 合法性与 template compatibility 分开处理。</p>
            )}
          </div>
          <button
            className="button button--ghost"
            disabled={controlsDisabled}
            onClick={() => void previewCurrent()}
            type="button"
          >
            {submitting ? '检查中…' : 'Server preview'}
          </button>
          <button
            className="button button--primary"
            disabled={controlsDisabled || !preview?.profile.compatible}
            onClick={() => void applyCurrent()}
            type="button"
          >
            {action === 'prepare' ? '创建 Preparation Project' : '展开 Production Graph'}
          </button>
        </footer>
      </section>
    </div>
  )
}
