/**
 * 提供 AVEnhanceFlow v2.7.0 面向创作者的五步建项流程。
 *
 * React 只收集意图、显示 Python preview，并把用户显式发起的“分析素材”编排为 preparation create 与
 * 普通 Run。Chapter/Leaf、dynamic ports、媒体 probe、输出命名和 profile compatibility 始终由 Python
 * 决定；任何输入变化或迟到 preview 都不能触发 mutation。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { dialogFocusTargets } from './components/focus-management'
import { StudioGatewayError } from './gateway'
import { ChapterOverlapSettingsForm } from './ChapterOverlapSettingsForm'
import { ChapterOverlapPreview } from './ChapterOverlapPreview'
import { ChapterOverlapDraftError, chapterSettingsIntent, initialChapterOverlapSettings, type ChapterOverlapFieldIssue } from './chapter-overlap-form'
import { OverlapContractError, type OverlapFullEnvelope, type OverlapFullIntent, type OverlapProcessing, type OverlapProcessingEnvelope, type OverlapProcessingRequest } from './chapter-overlap-contracts'
import { formatHostBridgeError } from './host-error-presentation'
import type {
  AvEnhanceV27ChapterSelectorWire,
  AvEnhanceV27ExpandRequestWire,
  AvEnhanceV27PrepareRequestWire,
  AvEnhanceV27PublicationRequestWire,
  AvEnhanceV27PublicationPreviewEnvelope,
  AvEnhanceV27PublicationPreviewRequestWire,
  AvEnhanceV27SourceMode,
  AvEnhanceV27TemplatePreviewEnvelope,
  AvEnhanceV27TemplatePreviewRequestWire,
  ProjectSnapshotWire,
  RunSummaryWire,
} from './contracts'

type SelectorMode = AvEnhanceV27ChapterSelectorWire['mode']
type WizardStep = 1 | 2 | 3 | 4 | 5
type SettingsField = '片名' | '年份' | 'Chapter selector mode' | 'Exact chapter frames' | 'Exact chapter times'
  | 'Leaf duration minutes' | 'Enhancement model name' | 'Enhancement actual scale factor'
  | 'Enhancement model version' | 'FI model name' | 'FI model version' | 'Program encoder' | 'Publication output root' | '更改成片父目录'
export type AvEnhanceV27WizardMode = 'create' | 'resume'

interface SourceDraft {
  readonly source_path: string
  readonly chapter_label: string
}

export interface AvEnhanceV27WizardProps {
  readonly open: boolean
  readonly mode: AvEnhanceV27WizardMode
  readonly busy: boolean
  readonly serviceUnavailable?: boolean
  readonly reconnecting?: boolean
  readonly connectionEpoch?: number
  readonly onReconnect?: () => void
  readonly serviceError?: string | null
  readonly currentSnapshot: ProjectSnapshotWire | null
  readonly currentProjectPath: string
  readonly currentProjectId: string
  readonly currentProjectName: string
  readonly runSummaries: ReadonlyArray<RunSummaryWire>
  readonly projectIdFactory?: () => string
  readonly pickerAvailable?: boolean
  readonly onClose: () => void
  readonly onPickProjectPath?: (suggestedName: string) => Promise<string | null>
  readonly onPickSources?: (multiple: boolean) => Promise<ReadonlyArray<string> | null>
  readonly onPickOutputDirectory?: () => Promise<string | null>
  readonly onPickDataDirectory?: () => Promise<string | null>
  readonly onRevealOutputDirectory?: (selectedPath: string) => Promise<void>
  readonly onPreview: (
    request: AvEnhanceV27TemplatePreviewRequestWire,
  ) => Promise<AvEnhanceV27TemplatePreviewEnvelope | null>
  readonly onPreviewPublication: (request: AvEnhanceV27PublicationPreviewRequestWire) => Promise<AvEnhanceV27PublicationPreviewEnvelope>
  readonly onCreate: (request: AvEnhanceV27PrepareRequestWire, storage?: { readonly data_parent_directory?: string; readonly media_basename: string }) => Promise<boolean>
  readonly onStartPreparationRun?: () => Promise<string | null>
  readonly onExpand: (request: AvEnhanceV27ExpandRequestWire) => Promise<boolean>
  readonly onPreviewOverlapProcessing?: (request: OverlapProcessingRequest) => Promise<OverlapProcessingEnvelope>
  readonly onPreviewOverlap?: (request: OverlapFullIntent) => Promise<OverlapFullEnvelope>
  readonly onExpandOverlap?: (request: OverlapFullIntent) => Promise<boolean>
  readonly onLocateNode: (nodeId: string) => void
}

const stepLabels = ['选择素材', '处理方案', '成片设置', '分析', '确认工作流'] as const

function defaultProjectId(): string {
  return `project.${globalThis.crypto.randomUUID()}`
}

function optionalText(value: string): string | undefined {
  // 只有真正的空控件表示“未提供”；边界空白原样交给 Python fail closed。
  return value === '' ? undefined : value
}

function positiveInteger(value: string, label: string): number {
  if (!/^[1-9][0-9]*$/.test(value)) throw new Error(`${label} 必须是严格正整数。`)
  const parsed = Number(value)
  if (!Number.isSafeInteger(parsed)) throw new Error(`${label} 超出安全整数范围。`)
  return parsed
}

function parseFrames(value: string): number[] {
  const tokens = value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean)
  if (tokens.length === 0) throw new Error('精确帧切分至少需要一个下一章首帧。')
  return tokens.map((item) => positiveInteger(item, '章节首帧'))
}

function parseTimes(value: string): string[] {
  const tokens = value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean)
  if (tokens.length === 0) throw new Error('精确时间切分至少需要一个秒数。')
  return tokens
}

function sourceModeFromSnapshot(snapshot: ProjectSnapshotWire | null): AvEnhanceV27SourceMode | null {
  const admission = snapshot?.project.graph.nodes.find(
    (node) => node.type_id === 'zniku.avenhance.v27.source_admission',
  )
  const value = admission?.parameters.source_mode
  return value === 'program' || value === 'pre_chaptered' ? value : null
}

function suggestedProjectName(name: string): string {
  const stem = name.trim().replace(/[<>:"/\\|?*]+/g, '-').replace(/[. ]+$/g, '')
  return `${stem || 'ZNIKU 视频工程'}.zniku`
}

function pathName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path
}

function humanRunTime(value: string): string {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return '已完成的素材分析'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(date)
}

function stageLabel(stage: string): string {
  if (stage === 'mosaic_restoration') return '马赛克修复'
  if (stage === 'enhancement') return '画质增强'
  return '补帧'
}

function isPublicationError(code: string | null | undefined): boolean {
  // 只按稳定错误码翻译输出设置问题；未知 message 不用于猜测原因或动作权限。
  return code !== null && code !== undefined && new Set([
    'E_AV27_NAMING_ROOT', 'E_AV27_NAMING_PARENT', 'E_AV27_NAMING_EXISTS', 'E_AV27_NAMING_TARGET',
    'E_AV27_NAMING_TITLE', 'E_AV27_NAMING_YEAR', 'E_AV27_NAMING_RESERVED', 'E_AV27_NAMING_SOURCE',
  ]).has(code)
}

export function AvEnhanceV27Wizard({
  open,
  mode,
  busy,
  serviceUnavailable = false,
  reconnecting = false,
  connectionEpoch = 0,
  onReconnect,
  serviceError = null,
  currentSnapshot,
  currentProjectPath,
  currentProjectId,
  currentProjectName,
  runSummaries,
  projectIdFactory = defaultProjectId,
  pickerAvailable = false,
  onClose,
  onPickProjectPath,
  onPickSources,
  onPickOutputDirectory,
  onPickDataDirectory,
  onRevealOutputDirectory,
  onPreview,
  onPreviewPublication,
  onCreate,
  onStartPreparationRun,
  onExpand,
  onPreviewOverlapProcessing,
  onPreviewOverlap,
  onExpandOverlap,
  onLocateNode,
}: AvEnhanceV27WizardProps) {
  const wasOpen = useRef(false)
  const dialogRef = useRef<HTMLElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const projectNameRef = useRef<HTMLInputElement | null>(null)
  const projectPathButtonRef = useRef<HTMLButtonElement | null>(null)
  const developerProjectPathRef = useRef<HTMLInputElement | null>(null)
  const chapterNameRefs = useRef<Array<HTMLInputElement | null>>([])
  const previousFocusRef = useRef<HTMLElement | null>(null)
  const previewRequestRef = useRef<AvEnhanceV27TemplatePreviewRequestWire | null>(null)
  const overlapRequestRef = useRef<OverlapFullIntent | null>(null)
  const responseEpochRef = useRef(0)
  const pickerFlightRef = useRef(0)
  const expansionFlightRef = useRef<{ readonly runId: string; readonly token: symbol } | null>(null)
  const outputOpenFlightRef = useRef<symbol | null>(null)
  const analysisRunIdRef = useRef<string | null>(null)
  const publicationFlightRef = useRef<symbol | null>(null)
  const publicationAutoFlightRef = useRef<symbol | null>(null)
  const connectionRef = useRef({ unavailable: serviceUnavailable, epoch: connectionEpoch })
  const interruptedDraftModeRef = useRef<AvEnhanceV27WizardMode | null>(null)
  const interruptedProjectIdRef = useRef<string | null>(null)
  const initializedDraftResetRef = useRef(0)
  const [draftResetEpoch, setDraftResetEpoch] = useState(0)
  const autoPreviewAllowedRef = useRef(true)
  const mutationFlightRef = useRef<symbol | null>(null)
  const [connectionInterrupted, setConnectionInterrupted] = useState(false)
  const [mutationInterrupted, setMutationInterrupted] = useState(false)
  const [outputSelectionExpired, setOutputSelectionExpired] = useState(false)

  const [step, setStep] = useState<WizardStep>(1)
  const [dataParent, setDataParent] = useState<string | null>(null)
  const [preview, setPreview] = useState<AvEnhanceV27TemplatePreviewEnvelope | null>(null)
  const [overlapPreview, setOverlapPreview] = useState<OverlapFullEnvelope | null>(null)
  const [workflowProfile, setWorkflowProfile] = useState<'av27' | 'overlap'>('av27')
  const [overlapSettings, setOverlapSettings] = useState(initialChapterOverlapSettings)
  const [overlapIssue, setOverlapIssue] = useState<ChapterOverlapFieldIssue | null>(null)
  const [contextLeft, setContextLeft] = useState('32')
  const [contextRight, setContextRight] = useState('32')
  const [contextMinimum, setContextMinimum] = useState('2')
  const [localError, setLocalError] = useState<string | null>(null)
  const [missingProjectField, setMissingProjectField] = useState<{ readonly field: 'name' | 'path' } | null>(null)
  const [missingChapter, setMissingChapter] = useState<{ readonly index: number } | null>(null)
  const [invalidSetting, setInvalidSetting] = useState<{ readonly field: SettingsField } | null>(null)
  const [pickerFailure, setPickerFailure] = useState<{ readonly message: string; readonly rawMessage: string } | null>(null)
  const [previewFailure, setPreviewFailure] = useState<{ readonly code: string | null; readonly message: string; readonly recoveryMessage: string | null } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [openingOutput, setOpeningOutput] = useState(false)
  const [checkingOutput, setCheckingOutput] = useState(false)
  const [publicationPreview, setPublicationPreview] = useState<AvEnhanceV27PublicationPreviewEnvelope | null>(null)
  const [previewingOutput, setPreviewingOutput] = useState(false)
  const [outputPreviewError, setOutputPreviewError] = useState<string | null>(null)
  // 仅缓存 Python 解析的父目录，按精确工程路径绑定；它不是执行或目录存在的授权。
  const [resolvedProjectOutput, setResolvedProjectOutput] = useState<{ readonly projectPath: string; readonly root: string } | null>(null)
  const [preparationCreated, setPreparationCreated] = useState(false)
  const [analysisRunId, setAnalysisRunIdState] = useState<string | null>(null)
  const setAnalysisRunId = useCallback((runId: string | null) => {
    // 开关向导时，同一批 effect 仍可能捕获旧 state；同步身份用于拒绝旧选择触发新的请求。
    analysisRunIdRef.current = runId
    setAnalysisRunIdState(runId)
  }, [])

  const [projectPath, setProjectPath] = useState('')
  const [projectId, setProjectId] = useState('')
  const [projectName, setProjectName] = useState('未命名视频工程')
  const [sourceMode, setSourceMode] = useState<AvEnhanceV27SourceMode>('program')
  const [sources, setSources] = useState<ReadonlyArray<SourceDraft>>([
    { source_path: '', chapter_label: '' },
  ])
  const [mrMode, setMrMode] = useState<'off' | 'external'>('off')
  const [mrModelName, setMrModelName] = useState('Jasna')
  const [mrModelVersion, setMrModelVersion] = useState('')

  const [selectorMode, setSelectorMode] = useState<SelectorMode>('single')
  const [selectorFrames, setSelectorFrames] = useState('')
  const [selectorTimes, setSelectorTimes] = useState('')
  const [leafDurationMinutes, setLeafDurationMinutes] = useState('1')
  const [enhancementModelName, setEnhancementModelName] = useState('Starlight Precise')
  const [enhancementModelVersion, setEnhancementModelVersion] = useState('')
  const [actualScaleFactor, setActualScaleFactor] = useState('1')
  const [fiModelName, setFiModelName] = useState('Aion')
  const [fiModelVersion, setFiModelVersion] = useState('')
  const [encoder, setEncoder] = useState<'gpu' | 'cpu'>('gpu')
  const [outputRoot, setOutputRoot] = useState('')
  const [outputOrigin, setOutputOrigin] = useState<'project' | 'custom'>('project')
  const [title, setTitle] = useState('')
  const [year, setYear] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [outputLayout, setOutputLayout] = useState<'direct' | 'title_subdirectory'>('title_subdirectory')

  const currentSourceMode = mode === 'resume'
    ? sourceModeFromSnapshot(currentSnapshot) ?? sourceMode
    : sourceMode
  const overlapEnabled = workflowProfile === 'overlap' && currentSourceMode === 'program'
  const overlapAvailable = Boolean(onPreviewOverlapProcessing && onPreviewOverlap && onExpandOverlap)
  const alreadyOverlap = mode === 'resume' && Boolean(currentSnapshot?.project.graph.nodes.some((node) => node.type_id.startsWith('zniku.overlap.')))
  const completedRuns = useMemo(
    () => runSummaries.filter((summary) => summary.state === 'completed'),
    [runSummaries],
  )
  const analysisSummary = analysisRunId
    ? runSummaries.find((summary) => summary.run_id === analysisRunId) ?? null
    : null

  useEffect(() => () => {
    responseEpochRef.current += 1
    pickerFlightRef.current += 1
    expansionFlightRef.current = null
    outputOpenFlightRef.current = null
    analysisRunIdRef.current = null
    publicationFlightRef.current = null
    publicationAutoFlightRef.current = null
  }, [])

  useEffect(() => {
    if (open && (!wasOpen.current || initializedDraftResetRef.current !== draftResetEpoch)) {
      initializedDraftResetRef.current = draftResetEpoch
      previousFocusRef.current = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
      if (interruptedDraftModeRef.current === mode &&
          interruptedProjectIdRef.current === (currentSnapshot?.project.project_id ?? currentProjectId)) {
        // 断线后的重开只恢复本页表单，不恢复旧预览、选择句柄或自动执行资格。
        wasOpen.current = true
        closeButtonRef.current?.focus()
        return
      }
      interruptedDraftModeRef.current = null
      interruptedProjectIdRef.current = null
      mutationFlightRef.current = null
      autoPreviewAllowedRef.current = true
      setConnectionInterrupted(false)
      setMutationInterrupted(false)
      setOutputSelectionExpired(false)
      const continuing = mode === 'resume'
      setStep(continuing ? 3 : 1)
      setPreview(null)
      setOverlapPreview(null)
      overlapRequestRef.current = null
      setWorkflowProfile('av27')
      setOverlapSettings(initialChapterOverlapSettings())
      setOverlapIssue(null)
      setContextLeft('32'); setContextRight('32'); setContextMinimum('2')
      previewRequestRef.current = null
      expansionFlightRef.current = null
      responseEpochRef.current += 1
      pickerFlightRef.current += 1
      setLocalError(null)
      setMissingProjectField(null)
      setMissingChapter(null)
      setInvalidSetting(null)
      setPickerFailure(null)
      setPreviewFailure(null)
      setOpeningOutput(false)
      setCheckingOutput(false)
      setPublicationPreview(null)
      setPreviewingOutput(false)
      setOutputPreviewError(null)
      setResolvedProjectOutput(null)
      publicationAutoFlightRef.current = null
      publicationFlightRef.current = null
      outputOpenFlightRef.current = null
      setSubmitting(false)
      setPreparationCreated(continuing)
      setAnalysisRunId(null)
      setProjectPath(continuing ? currentProjectPath : '')
      setDataParent(null)
      setProjectId(continuing ? currentProjectId : projectIdFactory())
      setProjectName(continuing ? currentProjectName || '未命名视频工程' : '未命名视频工程')
      setSelectorMode('single')
      setSelectorFrames('')
      setSelectorTimes('')
      setLeafDurationMinutes('1')
      setEnhancementModelName('Starlight Precise')
      setEnhancementModelVersion('')
      setActualScaleFactor('1')
      setFiModelName('Aion')
      setFiModelVersion('')
      setEncoder('gpu')
      // 已有输出节点只沿用正式声明的目录与布局，不从成片文件名反推片名或年份。
      const priorOutput = continuing ? currentSnapshot?.project.graph.nodes.find((node) => node.node_id === 'output' && typeof node.parameters.output_root === 'string') : undefined
      setOutputRoot(typeof priorOutput?.parameters.output_root === 'string' ? priorOutput.parameters.output_root : '')
      setOutputOrigin(priorOutput ? 'custom' : 'project')
      if (priorOutput) setOutputSelectionExpired(true)
      setTitle('')
      setYear('')
      setOverwrite(false)
      setOutputLayout(priorOutput ? priorOutput.parameters.create_parent === true ? 'title_subdirectory' : 'direct' : 'title_subdirectory')
      setSourceMode('program')
      setSources([{ source_path: '', chapter_label: '' }])
      setMrMode('off')
      setMrModelName('Jasna')
      setMrModelVersion('')
      closeButtonRef.current?.focus()
    } else if (!open && wasOpen.current) {
      responseEpochRef.current += 1
      pickerFlightRef.current += 1
      previousFocusRef.current?.focus()
      previousFocusRef.current = null
    }
    wasOpen.current = open
  }, [currentProjectId, currentProjectName, currentProjectPath, currentSnapshot?.project.project_id, draftResetEpoch, mode, open, projectIdFactory, setAnalysisRunId])

  useEffect(() => {
    const previous = connectionRef.current
    connectionRef.current = { unavailable: serviceUnavailable, epoch: connectionEpoch }
    if ((!serviceUnavailable || previous.unavailable) && previous.epoch === connectionEpoch) return
    // 网络中断不是“仍在忙”。拒绝所有迟到响应，同时保留用户输入；无法确认的写入绝不自动重试。
    if (open && interruptedDraftModeRef.current === null) {
      interruptedDraftModeRef.current = mode
      interruptedProjectIdRef.current = currentSnapshot?.project.project_id ?? currentProjectId
    }
    responseEpochRef.current += 1
    pickerFlightRef.current += 1
    expansionFlightRef.current = null
    outputOpenFlightRef.current = null
    publicationFlightRef.current = null
    publicationAutoFlightRef.current = null
    previewRequestRef.current = null
    autoPreviewAllowedRef.current = false
    overlapRequestRef.current = null
    setOverlapPreview(null)
    setConnectionInterrupted(true)
    setOutputSelectionExpired(true)
    if (mutationFlightRef.current) setMutationInterrupted(true)
    mutationFlightRef.current = null
    setSubmitting(false)
    setOpeningOutput(false)
    setCheckingOutput(false)
    setPreview(null)
    setPublicationPreview(null)
    setResolvedProjectOutput(null)
    setPreviewingOutput(false)
    setOutputPreviewError(null)
    setStep((current) => current >= 4 ? 3 : current)
  }, [connectionEpoch, currentProjectId, currentSnapshot?.project.project_id, mode, open, serviceUnavailable])

  useEffect(() => {
    if (!open) return
    const handleKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      if (event.key === 'Escape' && (serviceUnavailable || (!submitting && (!busy || connectionInterrupted) && !openingOutput))) {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const dialog = dialogRef.current
      if (!dialog) return
      const focusable = dialogFocusTargets(dialog)
      if (focusable.length === 0) {
        event.preventDefault()
        return
      }
      const first = focusable[0]!
      const last = focusable.at(-1)!
      if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [busy, connectionInterrupted, onClose, open, openingOutput, serviceUnavailable, submitting])

  useEffect(() => {
    if (!open || step !== 1 || (!missingProjectField && !missingChapter)) return
    // 每次校验失败都回到缺项本身；这只导航表单，不打开原生窗口或发起服务请求。
    let target: HTMLInputElement | HTMLButtonElement | null = projectNameRef.current
    if (missingChapter) {
      target = chapterNameRefs.current[missingChapter.index] ?? null
      const entry = target?.closest('details')
      if (entry) entry.open = true
    } else if (missingProjectField?.field === 'path') {
      target = projectPathButtonRef.current
      if (!target || target.disabled) {
        target = developerProjectPathRef.current
        const entry = target?.closest('details')
        if (entry) entry.open = true
      }
    }
    target?.focus()
  }, [missingChapter, missingProjectField, open, step])

  useEffect(() => {
    if (!open || step !== 3 || !invalidSetting) return
    const target = dialogRef.current?.querySelector<HTMLElement>(`[aria-label="${invalidSetting.field}"]`)
    const entry = target?.closest('details')
    if (entry) entry.open = true
    target?.focus()
  }, [invalidSetting, open, step])

  useEffect(() => {
    if (!open || step !== 3 || !overlapIssue) return
    const name = overlapIssue.fieldPath.at(-1)
    const labels: Record<string, string> = { left_context_frames: '前置上下文帧数', right_context_frames: '后置上下文帧数', minimum_input_frames: '最短补帧输入帧数', model_name: 'Enhancement model name', model_version: 'Enhancement model version', actual_scale_factor: 'Enhancement actual scale factor', encoder: 'Program encoder' }
    const label = typeof name === 'string' ? labels[name] : undefined
    const target = label ? dialogRef.current?.querySelector<HTMLElement>(`[aria-label="${label}"]`) : null
    const entry = target?.closest('details')
    if (entry) entry.open = true
    target?.focus()
  }, [open, step, overlapIssue])

  const invalidateExpansion = () => {
    responseEpochRef.current += 1
    pickerFlightRef.current += 1
    expansionFlightRef.current = null
    previewRequestRef.current = null
    setPreview(null)
    overlapRequestRef.current = null
    setOverlapPreview(null)
    setOverlapIssue(null)
    setLocalError(null)
    setMissingProjectField(null)
    setMissingChapter(null)
    setInvalidSetting(null)
    setPickerFailure(null)
    setPreviewFailure(null)
    setOpeningOutput(false)
    setCheckingOutput(false)
    setPublicationPreview(null)
    setPreviewingOutput(false)
    setOutputPreviewError(null)
    publicationAutoFlightRef.current = null
    publicationFlightRef.current = null
    outputOpenFlightRef.current = null
  }

  const updateSource = (index: number, patch: Partial<SourceDraft>) => {
    setSources((current) => current.map((source, sourceIndex) =>
      sourceIndex === index ? { ...source, ...patch } : source,
    ))
    invalidateExpansion()
  }

  const buildPrepareRequest = (): AvEnhanceV27PrepareRequestWire => {
    if (!projectName.trim()) {
      setMissingProjectField({ field: 'name' })
      setStep(1)
      throw new Error('请填写工程名称。')
    }
    if (!projectPath.trim()) {
      setMissingProjectField({ field: 'path' })
      setStep(1)
      throw new Error('请选择工程保存位置。工作数据位置不能代替工程文件位置。')
    }
    if (!projectId) throw new Error('无法生成工程身份；请关闭向导后重试。')
    if (sources.length === 0) throw new Error('至少需要一个视频素材。')
    if (sourceMode === 'program' && sources.length !== 1) {
      throw new Error('“一条完整视频”只能选择一个素材。')
    }
    const normalizedSources = sources.map((source, sourceOrdinal) => {
      if (!source.source_path.trim()) throw new Error(`请选择第 ${sourceOrdinal + 1} 个视频素材。`)
      if (sourceMode === 'pre_chaptered') {
        if (!source.chapter_label.trim()) {
          setMissingProjectField(null)
          setMissingChapter({ index: sourceOrdinal })
          setStep(1)
          throw new Error(`请填写第 ${sourceOrdinal + 1} 段的章节名称。`)
        }
        return {
          source_path: source.source_path,
          source_ordinal: sourceOrdinal,
          chapter_label: source.chapter_label,
        }
      }
      return { source_path: source.source_path, source_ordinal: sourceOrdinal }
    })
    if (mrMode === 'external' && (!mrModelName.trim() || !mrModelVersion.trim())) {
      throw new Error('启用马赛克修复时，需要填写实际工具/模型与版本。')
    }
    return {
      profile_version: '2.7.0',
      project_path: projectPath,
      project_id: projectId,
      project_name: projectName,
      source_mode: sourceMode,
      sources: normalizedSources,
      mr: mrMode === 'off'
        ? { mode: 'off' }
        : { mode: 'external', model_name: mrModelName, model_version: mrModelVersion },
    }
  }

  const buildProcessingRequest = (): NonNullable<AvEnhanceV27PublicationPreviewRequestWire['processing']> => {
    let chapterSelector: AvEnhanceV27ChapterSelectorWire | undefined
    if (currentSourceMode !== 'pre_chaptered') {
      chapterSelector = selectorMode === 'single'
        ? { mode: 'single' }
        : selectorMode === 'exact_frames'
          ? { mode: 'exact_frames', frames: parseFrames(selectorFrames) }
          : { mode: 'exact_times', times: parseTimes(selectorTimes) }
    }
    const scale = positiveInteger(actualScaleFactor, '增强倍率')
    const enhancementVersion = optionalText(enhancementModelVersion)
    const fiVersion = optionalText(fiModelVersion)
    return {
      ...(chapterSelector ? { chapter_selector: chapterSelector } : {}),
      leaf_duration_minutes: positiveInteger(leafDurationMinutes, '单段处理时长'),
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
    }
  }

  const buildExpandRequest = (preparationRunId: string): AvEnhanceV27ExpandRequestWire => {
    if (!preparationRunId) throw new Error('请选择一次已经完成的素材分析记录。')
    validateSettings()
    return { profile_version: '2.7.0', preparation_run_id: preparationRunId, ...buildProcessingRequest(), publication: buildPublicationRequest() }
  }

  const buildOverlapProcessing = (): OverlapProcessing => {
    try {
      const contextInteger = (value: string, field: string): number => {
        if (!/^(0|[1-9][0-9]*)$/.test(value) || !Number.isSafeInteger(Number(value))) {
          throw new ChapterOverlapDraftError({ fieldPath: ['processing', 'fi_profile', field], message: '上下文设置必须是严格非负整数。' })
        }
        return Number(value)
      }
      return {
        settings: chapterSettingsIntent(overlapSettings),
        enhancement: { model_name: enhancementModelName, model_version: optionalText(enhancementModelVersion) ?? null, actual_scale_factor: positiveInteger(actualScaleFactor, '增强倍率') },
        fi_profile: { software_version: 'v1.0', model_name: 'Aion', status: 'pending_real_acceptance', phase: 'even-input-2m-minus-1', left_context_frames: contextInteger(contextLeft, 'left_context_frames'), right_context_frames: contextInteger(contextRight, 'right_context_frames'), minimum_input_frames: contextInteger(contextMinimum, 'minimum_input_frames') },
        program_encode: { encoder },
      }
    } catch (error) {
      if (error instanceof ChapterOverlapDraftError) setOverlapIssue(error.issue)
      throw error
    }
  }

  const applyOverlapIssue = (error: unknown) => {
    const issue = error instanceof OverlapContractError || error instanceof ChapterOverlapDraftError ? error.issue
      : error instanceof StudioGatewayError && error.fieldPath.length ? { fieldPath: error.fieldPath, message: error.serviceMessage ?? error.message } : null
    if (issue) { setOverlapIssue(issue); setStep(3) }
    return issue !== null
  }

  const buildPublicationRequest = (): AvEnhanceV27PublicationRequestWire => {
    const root = outputOrigin === 'custom' ? outputRoot : resolvedProjectOutput?.projectPath === projectPath ? resolvedProjectOutput.root : ''
    if (!root) throw new Error('请返回成片设置，重新检查工程目录。')
    return { output_root: root, title, year, overwrite, layout: outputLayout }
  }

  const publicationIntent = useMemo<AvEnhanceV27PublicationPreviewRequestWire>(() => ({
    contract_version: '0.3.0',
    request: outputOrigin === 'project'
      ? { project_path: projectPath, title, year, overwrite, layout: outputLayout }
      : { output_root: outputRoot, title, year, overwrite, layout: outputLayout },
  }), [outputOrigin, projectPath, outputRoot, title, year, overwrite, outputLayout])

  const validateSettings = () => {
    const check = (field: SettingsField, action: () => unknown) => {
      try { action() } catch (error) { setInvalidSetting({ field }); setStep(3); throw error }
    }
    check('片名', () => { if (!title.trim()) throw new Error('请填写片名。') })
    check('年份', () => { if (!/^[0-9]{4}$/.test(year)) throw new Error('年份必须是四位数字。') })
    if (overlapEnabled) buildOverlapProcessing()
    if (!overlapEnabled && currentSourceMode !== 'pre_chaptered') {
      if (selectorMode === 'exact_frames') check('Exact chapter frames', () => parseFrames(selectorFrames))
      if (selectorMode === 'exact_times') check('Exact chapter times', () => parseTimes(selectorTimes))
    }
    if (!overlapEnabled) check('Leaf duration minutes', () => positiveInteger(leafDurationMinutes, '单段处理时长'))
    check('Enhancement model name', () => { if (!enhancementModelName.trim()) throw new Error('请填写画质增强使用的模型名称。') })
    check('Enhancement actual scale factor', () => positiveInteger(actualScaleFactor, '增强倍率'))
    check('FI model name', () => { if (!fiModelName.trim()) throw new Error('请填写补帧使用的模型名称。') })
    for (const [field, value] of [['Enhancement model version', enhancementModelVersion], ['FI model version', fiModelVersion]] as const) {
      check(field, () => { if (value !== '' && (!value.trim() || value.trim() !== value)) throw new Error('模型版本可留空；填写时不能包含首尾空白。') })
    }
    if (outputOrigin === 'custom') check('Publication output root', () => { if (!outputRoot.trim()) throw new Error('请指定自定义成片父目录，或恢复工程目录。') })
    else if (!projectPath.trim()) {
      if (preparationCreated) setInvalidSetting({ field: '更改成片父目录' })
      else { setMissingProjectField({ field: 'path' }); setStep(1) }
      throw new Error('请选择工程保存位置。')
    }
  }

  useEffect(() => {
    if (!open || step !== 3 || serviceUnavailable || reconnecting || mutationInterrupted ||
        !autoPreviewAllowedRef.current || publicationFlightRef.current ||
        !title.trim() || !/^[0-9]{4}$/.test(year) || !(outputOrigin === 'project' ? projectPath : outputRoot).trim()) return
    const token = Symbol('readonly-directory-preview')
    const epoch = responseEpochRef.current
    publicationAutoFlightRef.current = token
    const timer = window.setTimeout(() => {
      if (publicationAutoFlightRef.current !== token || epoch !== responseEpochRef.current) return
      setPreviewingOutput(true)
      // 自动调用仅展示目录，不锁住表单；显式下一步始终另做一次检查，且可撤销本次响应。
      void onPreviewPublication(publicationIntent).then((checked) => {
        if (publicationAutoFlightRef.current !== token || epoch !== responseEpochRef.current) return
        if (checked.layout !== outputLayout) throw new Error('输出检查与当前整理方式不一致，请重试。')
        setPublicationPreview(checked)
        setOutputPreviewError(null)
        if (outputOrigin === 'project') setResolvedProjectOutput({ projectPath, root: checked.resolved_output_root })
      }).catch((error: unknown) => {
        if (publicationAutoFlightRef.current !== token || epoch !== responseEpochRef.current) return
        setOutputPreviewError(error instanceof StudioGatewayError ? error.serviceMessage ?? error.message : error instanceof Error ? error.message : '目录预览暂不可用，请点击下一步重新检查。')
      }).finally(() => {
        if (publicationAutoFlightRef.current === token) {
          publicationAutoFlightRef.current = null
          setPreviewingOutput(false)
        }
      })
    }, 300)
    return () => {
      window.clearTimeout(timer)
      if (publicationAutoFlightRef.current === token) publicationAutoFlightRef.current = null
    }
  }, [actualScaleFactor, encoder, enhancementModelName, enhancementModelVersion, fiModelName, fiModelVersion, leafDurationMinutes, mutationInterrupted, onPreviewPublication, open, outputLayout, outputOrigin, outputRoot, projectPath, publicationIntent, reconnecting, selectorFrames, selectorMode, selectorTimes, serviceUnavailable, step, title, year])

  const previewExpansion = async (runId: string) => {
    if (serviceUnavailable || reconnecting || mutationInterrupted || expansionFlightRef.current) return
    const epoch = responseEpochRef.current
    const token = Symbol('expansion-preview')
    expansionFlightRef.current = { runId, token }
    setSubmitting(true)
    setLocalError(null)
    setPreviewFailure(null)
    try {
      if (overlapEnabled) {
        if (!onPreviewOverlap) throw new Error('当前服务没有重叠补帧候选接口。')
        validateSettings()
        const request: OverlapFullIntent = { contract_version: '0.3.2', preparation_run_id: runId, processing: buildOverlapProcessing(), publication: buildPublicationRequest() }
        const next = await onPreviewOverlap(request)
        if (epoch !== responseEpochRef.current || !open) return
        if (next.preparation_run_id !== runId) throw new Error('重叠补帧预览与分析记录不一致。')
        overlapRequestRef.current = request
        setOverlapPreview(next)
        setStep(5)
        return
      }
      const request: AvEnhanceV27TemplatePreviewRequestWire = {
        action: 'expand',
        request: buildExpandRequest(runId),
      }
      const next = await onPreview(request)
      if (epoch !== responseEpochRef.current || !open) return
      if (
        next?.phase !== 'expanded' ||
        next.plan.preparation_run_id !== runId ||
        !next.creator.analyzed
      ) {
        throw new Error('服务返回的工作流分析阶段不一致；Project 保持不变。')
      }
      previewRequestRef.current = request
      setPreview(next)
      setStep(5)
    } catch (error) {
      if (epoch === responseEpochRef.current) {
        previewRequestRef.current = null
        setPreview(null)
        overlapRequestRef.current = null
        setOverlapPreview(null)
        applyOverlapIssue(error)
        const code = error instanceof StudioGatewayError ? error.code : null
        setPreviewFailure({ code, message: error instanceof Error ? error.message : '无法生成工作流预览。',
          // Python 提供精确位置；这里只展示原文，绝不解析、拼接或把它提升为系统动作 authority。
          recoveryMessage: isPublicationError(code) && error instanceof StudioGatewayError ? error.serviceMessage : null })
        setLocalError(isPublicationError(code)
          ? '输出位置暂不可用。素材分析已经完成并保留；请修改输出设置，检查目录权限或同名文件冲突后重试，无需重复分析。'
          : '工作流预览暂未生成。已完成的素材分析与工程仍然保留；请修改设置后重试，或在高级详情查看具体原因。')
        // 路径或预览失败不撤销已完成分析；恢复模式也保留 exact Run，改选由用户显式触发。
      }
    } finally {
      if (epoch === responseEpochRef.current) setSubmitting(false)
      if (expansionFlightRef.current?.token === token) expansionFlightRef.current = null
    }
  }

  useEffect(() => {
    if (
      !open ||
      serviceUnavailable || reconnecting || !autoPreviewAllowedRef.current ||
      !analysisRunId ||
      analysisRunIdRef.current !== analysisRunId ||
      analysisSummary?.state !== 'completed' ||
      previewRequestRef.current?.action === 'expand' ||
      overlapRequestRef.current !== null ||
      expansionFlightRef.current?.runId === analysisRunId
    ) return
    void previewExpansion(analysisRunId)
    // 表单改变时只废弃旧预览；已完成的 exact Run 保留，用户重新检查输出并显式生成预览即可。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisRunId, analysisSummary?.state, open])

  if (!open) return null
  if (alreadyOverlap) return <div className="template-wizard-backdrop"><section className="template-wizard creator-wizard" role="dialog" aria-modal="true" aria-label="重叠补帧工程已创建" ref={dialogRef}><h2>这个工程已经包含重叠补帧工作流</h2><p>请在节点图中配置或运行已有节点。向导不会重新展开或覆盖已编辑的工作流，也不会迁移旧结果。</p><p>Aion · 软件 v1.0 · 待真实验收。上下文输入、外部原始结果和裁边结果分别保留。</p><button type="button" className="button button--primary" onClick={onClose} ref={closeButtonRef}>返回当前节点图</button></section></div>

  const controlsDisabled = !serviceUnavailable && ((busy && !connectionInterrupted) || submitting || openingOutput)
  const serviceControlsDisabled = busy || submitting || openingOutput || serviceUnavailable || reconnecting || mutationInterrupted
  const profileChoice = <section className="creator-settings-group" aria-label="工作流版本">
    <h4>处理链</h4><label>工作流方案<select aria-label="工作流方案" disabled={controlsDisabled} value={overlapEnabled ? 'overlap' : 'av27'} onChange={(event) => { setWorkflowProfile(event.target.value as 'av27' | 'overlap'); invalidateExpansion() }}><option value="av27">AVEnhanceFlow v2.7.0 · 既有流程</option><option value="overlap" disabled={!overlapAvailable || currentSourceMode !== 'program'}>ZNIKU 重叠 FI · 0.3.2 候选</option></select></label>
    {currentSourceMode === 'pre_chaptered' ? <p>已经分章的素材沿用既有流程，不会自动迁移为重叠补帧。</p> : overlapEnabled ? <p>支持平均 / 精确时间 / 精确帧分章和章内均衡分叶。Aion 软件 v1.0 尚待真实验收；这是 ZNIKU 新处理链，不是 AVEnhanceFlow 2.7.0 原有行为。</p> : <p>保留既有分章和逐章补帧语义；不会自动升级已有工程或结果。</p>}
    {!overlapAvailable && currentSourceMode === 'program' && <p>当前连接未提供新候选接口；升级本机服务后才能显式选择。</p>}
  </section>

  const reportPickerError = (error: unknown, fallback: string) => {
    const rawMessage = error instanceof Error ? error.message : fallback
    const message = formatHostBridgeError(error) ?? rawMessage
    setLocalError(message)
    setPickerFailure({ message, rawMessage })
  }

  const chooseProjectPath = async () => {
    if (!onPickProjectPath || serviceControlsDisabled) return
    const epoch = responseEpochRef.current
    const flight = ++pickerFlightRef.current
    try {
      const path = await onPickProjectPath(suggestedProjectName(projectName))
      if (path === null || epoch !== responseEpochRef.current || flight !== pickerFlightRef.current) return
      setProjectPath(path)
      invalidateExpansion()
    } catch (error) {
      if (epoch === responseEpochRef.current && flight === pickerFlightRef.current) {
        reportPickerError(error, '无法打开工程保存位置选择器。')
      }
    }
  }

  const chooseDataDirectory = async () => {
    if (!onPickDataDirectory || serviceControlsDisabled) return
    const epoch = responseEpochRef.current
    const flight = ++pickerFlightRef.current
    try {
      const path = await onPickDataDirectory()
      if (path === null || epoch !== responseEpochRef.current || flight !== pickerFlightRef.current) return
      setDataParent(path)
      invalidateExpansion()
    } catch (error) {
      if (epoch === responseEpochRef.current && flight === pickerFlightRef.current) reportPickerError(error, '无法选择工作数据磁盘。')
    }
  }

  const chooseSources = async () => {
    if (!onPickSources || serviceControlsDisabled) return
    const epoch = responseEpochRef.current
    const flight = ++pickerFlightRef.current
    try {
      const paths = await onPickSources(sourceMode === 'pre_chaptered')
      if (
        paths === null ||
        paths.length === 0 ||
        epoch !== responseEpochRef.current ||
        flight !== pickerFlightRef.current
      ) return
      setSources(paths.map((path, index) => ({
        source_path: path,
        chapter_label: sourceMode === 'pre_chaptered' ? `章节 ${index + 1}` : '',
      })))
      invalidateExpansion()
    } catch (error) {
      if (epoch === responseEpochRef.current && flight === pickerFlightRef.current) {
        reportPickerError(error, '无法打开素材选择器。')
      }
    }
  }

  const chooseOutput = async () => {
    if (!onPickOutputDirectory || serviceControlsDisabled) return
    const epoch = responseEpochRef.current
    const flight = ++pickerFlightRef.current
    try {
      const path = await onPickOutputDirectory()
      if (path === null || epoch !== responseEpochRef.current || flight !== pickerFlightRef.current) return
      setOutputRoot(path)
      setOutputOrigin('custom')
      setOutputSelectionExpired(false)
      invalidateExpansion()
    } catch (error) {
      if (epoch === responseEpochRef.current && flight === pickerFlightRef.current) {
        reportPickerError(error, '无法打开成片文件夹选择器。')
      }
    }
  }

  const revealOutput = async () => {
    if (!onRevealOutputDirectory || !outputRoot || outputOpenFlightRef.current || serviceControlsDisabled || outputSelectionExpired) return
    const epoch = responseEpochRef.current
    const token = Symbol('reveal-output')
    outputOpenFlightRef.current = token
    setOpeningOutput(true)
    try {
      // 路径只是给父级匹配当次选择，系统动作 authority 始终由父级的 picker selection handle 提供。
      await onRevealOutputDirectory(outputRoot)
    } catch {
      if (epoch === responseEpochRef.current && outputOpenFlightRef.current === token) {
        setLocalError('无法打开所选输出文件夹。请重新选择输出目录后再试；工程、素材和已完成分析均未修改。')
      }
    } finally {
      if (outputOpenFlightRef.current === token) {
        outputOpenFlightRef.current = null
        setOpeningOutput(false)
      }
    }
  }

  const moveNext = async () => {
    setLocalError(null)
    try {
      if (step === 1) {
        buildPrepareRequest()
        setStep(2)
      } else if (step === 2) {
        buildPrepareRequest()
        setStep(3)
      } else if (step === 3) {
        if (serviceControlsDisabled) return
        if (publicationFlightRef.current) return
        setInvalidSetting(null)
        validateSettings()
        publicationAutoFlightRef.current = null
        setPreviewingOutput(false)
        setOutputPreviewError(null)
        const epoch = responseEpochRef.current
        const token = Symbol('publication-check')
        publicationFlightRef.current = token
        setCheckingOutput(true)
        try {
          // 此检查不依赖媒体、不保存工程、不创建目录。输入变化或取消立即撤销迟到响应的展示资格。
          if (overlapEnabled) {
            if (!onPreviewOverlapProcessing) throw new Error('当前服务没有重叠补帧候选设置接口。')
            await onPreviewOverlapProcessing({ contract_version: '0.3.2', processing: buildOverlapProcessing() })
            if (epoch !== responseEpochRef.current || publicationFlightRef.current !== token) return
          }
          const checked = await onPreviewPublication(overlapEnabled ? publicationIntent : { ...publicationIntent, processing: buildProcessingRequest() })
          if (epoch !== responseEpochRef.current || publicationFlightRef.current !== token) return
          if (checked.layout !== outputLayout) throw new Error('输出检查与当前整理方式不一致，请重试。')
          setPublicationPreview(checked)
          if (outputOrigin === 'project') setResolvedProjectOutput({ projectPath, root: checked.resolved_output_root })
          setStep(4)
        } catch (error) {
          if (epoch === responseEpochRef.current && publicationFlightRef.current === token) {
            if (overlapEnabled && applyOverlapIssue(error)) {
              setLocalError(error instanceof Error ? error.message : '重叠处理设置未通过检查。')
              return
            }
            const code = error instanceof StudioGatewayError ? error.code : null
            // Python 的稳定错误码只负责导航同一表单；不从错误文案猜测路径、时间或执行权限。
            const processingFields: Record<string, SettingsField> = {
              E_AV27_SETTINGS_CHAPTER_SELECTOR: selectorMode === 'exact_frames' ? 'Exact chapter frames' : selectorMode === 'exact_times' ? 'Exact chapter times' : 'Chapter selector mode',
              E_AV27_SETTINGS_LEAF_DURATION: 'Leaf duration minutes',
              E_AV27_SETTINGS_ENHANCEMENT_MODEL_NAME: 'Enhancement model name',
              E_AV27_SETTINGS_ENHANCEMENT_MODEL_VERSION: 'Enhancement model version',
              E_AV27_SETTINGS_ENHANCEMENT_SCALE: 'Enhancement actual scale factor',
              E_AV27_SETTINGS_FI_MODEL_NAME: 'FI model name',
              E_AV27_SETTINGS_FI_MODEL_VERSION: 'FI model version',
              E_AV27_SETTINGS_ENCODER: 'Program encoder',
            }
            const processingField = code ? processingFields[code] : undefined
            if (processingField) setInvalidSetting({ field: processingField })
            else if (code === 'E_AV27_NAMING_PROJECT_PATH' && !preparationCreated) { setMissingProjectField({ field: 'path' }); setStep(1) }
            else setInvalidSetting({ field: code === 'E_AV27_NAMING_YEAR' ? '年份' : code === 'E_AV27_NAMING_TITLE' || code === 'E_AV27_NAMING_RESERVED' ? '片名' : outputOrigin === 'custom' ? 'Publication output root' : '更改成片父目录' })
            setLocalError(`${processingField ? '处理设置' : '输出位置'}未通过检查。工程、素材和已有分析均保持不变。${error instanceof StudioGatewayError ? error.serviceMessage ?? error.message : error instanceof Error ? error.message : '请检查设置后重试。'}`)
          }
        } finally {
          if (publicationFlightRef.current === token) {
            publicationFlightRef.current = null
            setCheckingOutput(false)
          }
        }
      }
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : '请完成当前步骤。')
    }
  }

  const analyze = async () => {
    if (serviceControlsDisabled) return
    const epoch = ++responseEpochRef.current
    previewRequestRef.current = null
    setPreview(null)
    setLocalError(null)
    setSubmitting(true)
    try {
      if (preparationCreated) {
        if (!analysisRunId) throw new Error('请选择一次已经完成的素材分析记录。')
        await previewExpansion(analysisRunId)
        return
      }
      const prepareRequest = buildPrepareRequest()
      const request: AvEnhanceV27TemplatePreviewRequestWire = { action: 'prepare', request: prepareRequest }
      const preparationPreview = await onPreview(request)
      if (epoch !== responseEpochRef.current || !open) return
      if (!preparationPreview || preparationPreview.phase !== 'preparation' || !preparationPreview.profile.compatible) {
        throw new Error('素材准备检查未通过；没有创建工程或运行任务。')
      }
      previewRequestRef.current = request
      const mutationToken = Symbol('create-preparation')
      mutationFlightRef.current = mutationToken
      const created = await onCreate(prepareRequest, {
        ...(dataParent ? { data_parent_directory: dataParent } : {}),
        media_basename: `${title} (${year})`,
      })
      if (epoch !== responseEpochRef.current || !open) return
      if (mutationFlightRef.current === mutationToken) mutationFlightRef.current = null
      previewRequestRef.current = null
      if (!created) throw new Error('工程没有创建；素材和原有工程保持不变。')
      setPreparationCreated(true)
      if (!onStartPreparationRun) {
        throw new Error('当前入口无法启动素材分析；工程已安全创建，可返回工作区继续。')
      }
      mutationFlightRef.current = mutationToken
      const runId = await onStartPreparationRun()
      if (epoch !== responseEpochRef.current || !open) return
      if (mutationFlightRef.current === mutationToken) mutationFlightRef.current = null
      if (!runId) throw new Error('素材分析没有启动；工程已创建，但没有产生 Run。')
      autoPreviewAllowedRef.current = true
      setAnalysisRunId(runId)
    } catch (error) {
      if (epoch === responseEpochRef.current) {
        setLocalError(error instanceof Error ? error.message : '素材分析失败。')
      }
    } finally {
      if (epoch === responseEpochRef.current) { setSubmitting(false); mutationFlightRef.current = null }
    }
  }

  const restartAnalysis = async () => {
    if (serviceControlsDisabled) return
    const epoch = ++responseEpochRef.current
    pickerFlightRef.current += 1
    previewRequestRef.current = null
    expansionFlightRef.current = null
    setPreview(null)
    setAnalysisRunId(null)
    setLocalError(null)
    setSubmitting(true)
    try {
      if (!onStartPreparationRun) throw new Error('当前入口无法重新启动素材分析。')
      mutationFlightRef.current = Symbol('restart-analysis')
      const runId = await onStartPreparationRun()
      if (epoch !== responseEpochRef.current || !open) return
      if (!runId) throw new Error('素材分析没有启动；原工程和已完成结果保持不变。')
      autoPreviewAllowedRef.current = true
      setAnalysisRunId(runId)
    } catch (error) {
      if (epoch === responseEpochRef.current) {
        setLocalError(error instanceof Error ? error.message : '无法重新启动素材分析。')
      }
    } finally {
      if (epoch === responseEpochRef.current) { setSubmitting(false); mutationFlightRef.current = null }
    }
  }

  const selectCompletedAnalysis = (runId: string) => {
    if (serviceControlsDisabled) return
    invalidateExpansion()
    autoPreviewAllowedRef.current = true
    setAnalysisRunId(runId)
  }

  const returnToSettings = () => {
    invalidateExpansion()
    setStep(3)
  }

  const confirmWorkflow = async () => {
    if (serviceControlsDisabled) return
    const epoch = responseEpochRef.current
    const authority = previewRequestRef.current
    if (overlapEnabled ? !overlapPreview || !overlapRequestRef.current || !onExpandOverlap : !preview?.profile.compatible || !authority || authority.action !== 'expand') {
      invalidateExpansion()
      setLocalError('当前设置没有可确认的 Python 工作流预览；请使用已完成的素材分析重新生成预览。')
      setStep(4)
      return
    }
    setSubmitting(true)
    setLocalError(null)
    try {
      mutationFlightRef.current = Symbol('expand-workflow')
      const applied = overlapEnabled && overlapRequestRef.current && onExpandOverlap
        ? await onExpandOverlap(overlapRequestRef.current)
        : authority?.action === 'expand' ? await onExpand(authority.request) : false
      if (epoch !== responseEpochRef.current) return
      previewRequestRef.current = null
      overlapRequestRef.current = null
      if (applied) { interruptedDraftModeRef.current = null; onClose() }
      else {
        setPreview(null)
        setOverlapPreview(null)
        setLocalError('工作流未写入工程；预览需要重新检查，已完成的素材分析仍保留。')
        setStep(4)
      }
    } catch (error) {
      if (epoch !== responseEpochRef.current) return
      previewRequestRef.current = null
      setPreview(null)
      overlapRequestRef.current = null
      setOverlapPreview(null)
      setLocalError(error instanceof Error ? error.message : '工作流创建失败。')
      setStep(4)
    } finally {
      if (epoch === responseEpochRef.current) { setSubmitting(false); mutationFlightRef.current = null }
    }
  }

  return (
    <div className="template-wizard-backdrop" role="presentation">
      <section aria-label="AVEnhanceFlow v2.7.0 创作者向导" aria-modal="true" className="template-wizard creator-wizard" ref={dialogRef} role="dialog">
        <header className="template-wizard-header">
          <div><span className="eyebrow">引导式视频工作流</span><h2>创建增强视频工作流</h2><p>ZNIKU 会准确分析素材并规划工作流，生成后仍可自由编辑节点。</p></div>
          <button aria-label="关闭模板向导" className="template-wizard-close" disabled={controlsDisabled} onClick={onClose} ref={closeButtonRef} type="button">×</button>
        </header>


        <ol className="creator-wizard-steps" aria-label="建项进度">
          {stepLabels.map((label, index) => {
            const number = (index + 1) as WizardStep
            return <li aria-current={number === step ? 'step' : undefined} className={number === step ? 'is-current' : number < step ? 'is-complete' : ''} key={label}><span>{number < step ? '✓' : number}</span><strong>{label}</strong></li>
          })}
        </ol>

        <div className="creator-wizard-body">
        {(serviceUnavailable || reconnecting || connectionInterrupted) && <section className="template-error-stack" aria-label="向导连接恢复" role="status">
          <p>{serviceUnavailable ? '本机服务连接已中断。填写内容已保留；可以关闭向导或继续修改本地设置。' : reconnecting ? '正在重新连接本机服务；不会自动重试之前的操作。' : '连接已恢复，填写内容已保留。请重新检查输出位置并显式生成预览；打开输出文件夹前需重新选择该目录。'}</p>
          {mutationInterrupted && <p>上次创建或运行请求的结果尚未确认。请先关闭向导，在工作区重新打开工程并检查运行记录；不要重复创建或启动任务。</p>}
          {onReconnect && <button className="button button--ghost" type="button" disabled={reconnecting} onClick={onReconnect}>{reconnecting ? '正在重新连接…' : '重新连接本机服务'}</button>}
          {connectionInterrupted && <button className="button button--ghost" type="button" disabled={controlsDisabled} onClick={() => {
            if (!window.confirm('只放弃当前页面保留的向导填写内容，不删除工程或媒体。若上次写入结果尚未确认，请先回工作区检查工程和运行记录；开始新向导不会撤销已发送的请求。确定放弃填写内容吗？')) return
            interruptedDraftModeRef.current = null
            interruptedProjectIdRef.current = null
            setDraftResetEpoch((value) => value + 1)
          }}>放弃保留的向导草稿</button>}
        </section>}
          {step === 1 && (
            <section className="creator-step" aria-label="选择素材">
              <header><span>01</span><div><h3>选择要处理的视频</h3><p>先选择素材，再为工程命名并选择保存位置。</p></div></header>
              <section className="creator-setup-group" aria-label="素材设置">
                <h4>素材</h4>
                <label className="creator-setup-row"><span>素材组织方式</span><select aria-label="素材组织方式" disabled={controlsDisabled || preparationCreated} onChange={(event) => {
                  const next = event.target.value as AvEnhanceV27SourceMode
                  setSourceMode(next)
                  if (next === 'pre_chaptered') setWorkflowProfile('av27')
                  // 默认章名只是可编辑表单值；切换后不应逼操作者展开高级区补内部必填项。
                  setSources((current) => next === 'program' ? current.slice(0, 1) : current.map((source, index) => source.chapter_label.trim() ? source : { ...source, chapter_label: `章节 ${index + 1}` }))
                  invalidateExpansion()
                }} value={sourceMode}><option value="program">一条完整视频</option><option value="pre_chaptered">已经分章的多个视频</option></select></label>
                <div className="creator-setup-row">
                  <span>视频素材</span>
                  <div className="creator-setup-control">
                    <button className="button button--ghost" disabled={serviceControlsDisabled || preparationCreated || !pickerAvailable} onClick={() => void chooseSources()} type="button">{sourceMode === 'program' ? '选择视频素材' : '选择全部章节视频'}</button>
                    <div className="creator-setup-sources">{sources.filter((source) => source.source_path).map((source, index) => <div key={`${index}-${source.source_path}`}><strong>{pathName(source.source_path)}</strong>{sourceMode === 'pre_chaptered' && <span>{source.chapter_label}</span>}</div>)}</div>
                  </div>
                </div>
              </section>
              <section className="creator-setup-group" aria-label="工程设置">
                <h4>工程</h4>
                <label className="creator-setup-row"><span>工程名称</span><input aria-label="工程名称" aria-required="true" aria-invalid={missingProjectField?.field === 'name' || undefined} aria-describedby={missingProjectField?.field === 'name' ? 'creator-project-field-error' : undefined} ref={projectNameRef} disabled={controlsDisabled || preparationCreated} maxLength={200} onChange={(event) => { setProjectName(event.target.value); invalidateExpansion() }} value={projectName} /></label>
                <div className="creator-setup-row">
                  <span>工程保存位置</span>
                  <div className="creator-setup-control">
                    <button className="button button--ghost" aria-describedby={missingProjectField?.field === 'path' ? 'creator-project-field-error' : undefined} ref={projectPathButtonRef} disabled={serviceControlsDisabled || preparationCreated || !pickerAvailable} onClick={() => void chooseProjectPath()} type="button">选择工程保存位置</button>
                    {projectPath && <div className="creator-project-path"><strong className="creator-selected-path">已选择：{pathName(projectPath)}</strong><span>{projectPath}</span></div>}
                  </div>
                </div>
              </section>
              <details className="creator-setup-advanced">
                <summary><span>高级选项（可选）</span>{dataParent && <span className="creator-advanced-badge">已自定义工作数据位置</span>}</summary>
                <section className="creator-advanced-section" aria-label="工作数据位置">
                  <h4>工作数据</h4>
                  <p>{dataParent ? `专用磁盘父目录：${dataParent}` : '使用工程旁默认位置'}</p>
                  <small>{dataParent ? '创建工程时，将在此目录内建立工程文件同名的 .data 文件夹。工程文件位置不变。' : '创建工程时，将在工程文件旁建立同名 .data 文件夹。'} 中间产物和外部处理结果会长期保留，完成或退出不会自动清除。</small>
                  <div className="creator-storage-actions">
                    <button className="button button--ghost" type="button" disabled={serviceControlsDisabled || preparationCreated || !pickerAvailable || !onPickDataDirectory} onClick={() => void chooseDataDirectory()}>选择工作数据父目录</button>
                    {dataParent && <button className="button button--ghost" type="button" disabled={controlsDisabled || preparationCreated} onClick={() => { setDataParent(null); invalidateExpansion() }}>恢复工程旁默认</button>}
                  </div>
                  <small>仅在需要其他磁盘时修改；恢复默认不改变工程文件位置，不移动或删除文件。</small>
                </section>
                {sourceMode === 'pre_chaptered' && <section className="creator-advanced-section" aria-label="章节名称"><h4>章节名称</h4><p>按所选视频顺序命名，默认名称可直接使用。</p>{sources.map((source, index) => <label key={index}>{source.source_path ? pathName(source.source_path) : `第 ${index + 1} 个视频`}<input aria-label={`第 ${index + 1} 章名称`} aria-invalid={missingChapter?.index === index || undefined} aria-describedby={missingChapter?.index === index ? 'creator-project-field-error' : undefined} ref={(element) => { chapterNameRefs.current[index] = element }} disabled={controlsDisabled || preparationCreated} onChange={(event) => updateSource(index, { chapter_label: event.target.value })} value={source.chapter_label} /></label>)}</section>}
                <section className="creator-advanced-section" aria-label="手动路径（开发用）">
                  <h4>手动路径（开发用）</h4>
                  <p>仅供没有桌面选择器的开发环境使用，与上方选择器修改同一组路径；不是另一份设置。</p>
                  <small>工程使用 .zniku 扩展名，保存节点、参数和运行记录。选择路径时不创建文件，不上传、复制或改写源视频。</small>
                  <label>工程路径<input aria-label="模板工程路径" ref={developerProjectPathRef} aria-invalid={missingProjectField?.field === 'path' || undefined} aria-describedby={missingProjectField?.field === 'path' ? 'creator-project-field-error' : undefined} disabled={controlsDisabled || preparationCreated} onChange={(event) => { setProjectPath(event.target.value); invalidateExpansion() }} value={projectPath} /></label>
                  {sources.map((source, index) => <label key={index}>素材 {index + 1} 路径<input aria-label={`Source ${index + 1} path`} disabled={controlsDisabled || preparationCreated} onChange={(event) => updateSource(index, { source_path: event.target.value })} value={source.source_path} /></label>)}
                  {sourceMode === 'pre_chaptered' && <button className="button button--ghost" disabled={controlsDisabled || preparationCreated} onClick={() => { setSources((current) => [...current, { source_path: '', chapter_label: '' }]); invalidateExpansion() }} type="button">添加 Source</button>}
                </section>
              </details>
            </section>
          )}

          {step === 2 && (
            <section className="creator-step" aria-label="处理方案">
              <header><span>02</span><div><h3>选择处理方案</h3><p>标准流程会先增强每段画面，合并章节后再统一补帧和编码。</p></div></header>
              {profileChoice}
              <article className="creator-plan-card is-selected"><span>推荐</span><h4>完整增强流程</h4><p>画质增强 → 合并 → 补帧 → 连续 Main10 编码 → 保留原始音频。</p><ul><li>节点仍可在创建后自由调整</li><li>外部工具步骤会逐项引导</li><li>同一工程可随时切换到节点图继续编辑</li></ul></article>
              <details className="creator-settings-advanced"><summary>马赛克修复（高级）</summary><label className="template-check"><input aria-label="启用外部马赛克修复" checked={mrMode === 'external'} disabled={controlsDisabled || preparationCreated} onChange={(event) => { setMrMode(event.target.checked ? 'external' : 'off'); invalidateExpansion() }} type="checkbox" />素材分析前先在 Jasna 等外部工具中修复马赛克</label>{mrMode === 'external' && <div className="template-form-grid"><label>工具/模型<input aria-label="MR model name" disabled={controlsDisabled || preparationCreated} onChange={(event) => { setMrModelName(event.target.value); invalidateExpansion() }} value={mrModelName} /></label><label>实际版本<input aria-label="MR model version" disabled={controlsDisabled || preparationCreated} onChange={(event) => { setMrModelVersion(event.target.value); invalidateExpansion() }} value={mrModelVersion} /></label></div>}</details>
            </section>
          )}

          {step === 3 && (
            <section className="creator-step" aria-label="设置">
              <header><span>03</span><div><h3>成片设置</h3><p>先确定成片名称，再按处理顺序确认参数。已有默认值可以直接使用；需要填写的项目始终可见。</p></div></header>
              {mode === 'resume' && profileChoice}
              <section className="creator-settings-group" aria-label="成片命名">
                <h4>成片名称与位置</h4>
                <div className="creator-basic-settings">
                  <label>片名<input aria-label="片名" aria-invalid={invalidSetting?.field === '片名' || undefined} disabled={controlsDisabled} onChange={(event) => { setTitle(event.target.value); invalidateExpansion() }} placeholder="Example Film" value={title} /></label>
                  <label>年份<input aria-label="年份" aria-invalid={invalidSetting?.field === '年份' || undefined} disabled={controlsDisabled} inputMode="numeric" maxLength={4} onChange={(event) => { setYear(event.target.value); invalidateExpansion() }} placeholder="2026" value={year} /></label>
                </div>
                <div className="creator-target creator-directory-preview" aria-label="成片目录预览" aria-live="polite">
                  <span>{outputOrigin === 'project' ? '成片父目录：工程目录（.zniku 所在文件夹）' : '成片父目录：自定义位置'}</span>
                  <strong>{publicationPreview?.output_directory ?? (previewingOutput || checkingOutput ? '正在检查成片目录…' : '填写片名和四位年份后，显示完整成片目录')}</strong>
                  <small>{outputLayout === 'title_subdirectory' ? '按“片名（年份）”整理，例如 Example Film (2026)。' : '直接保存到成片父目录，不创建片名子文件夹。'}如需其他磁盘，可在下方“其他选项”修改。</small>
                  <small>这只是只读预览；文件夹仅在实际输出成片时按需创建。中间产物仍保存在工程工作数据目录。</small>
                  {outputPreviewError && <p className="template-local-error">目录暂未通过检查：{outputPreviewError} 点击下一步可重新检查并定位问题。</p>}
                </div>
              </section>
              <div className="creator-processing-settings">
                <section className="creator-settings-group" aria-label="章节与分段">
                  <h4><span>1</span>章节与分段</h4>
                  <p>先划分章节，再把每章拆成便于外部处理的小段。</p>
                  {overlapEnabled ? <ChapterOverlapSettingsForm value={overlapSettings} disabled={controlsDisabled} issue={overlapIssue} onChange={(next) => { setOverlapSettings(next); invalidateExpansion() }} /> : <div className="template-form-grid">
                    {currentSourceMode === 'pre_chaptered' ? <p className="creator-setting-note">沿用所选素材章节，不再次切分章节；仍可调整每章内的单段时长。</p> : <label>章节切分<select aria-label="Chapter selector mode" disabled={controlsDisabled} onChange={(event) => { setSelectorMode(event.target.value as SelectorMode); invalidateExpansion() }} value={selectorMode}><option value="single">整片作为一章</option><option value="exact_frames">按精确帧切分</option><option value="exact_times">按精确时间切分</option></select></label>}
                    <label>单段处理时长<input aria-label="Leaf duration minutes" aria-invalid={invalidSetting?.field === 'Leaf duration minutes' || undefined} disabled={controlsDisabled} inputMode="numeric" onChange={(event) => { setLeafDurationMinutes(event.target.value); invalidateExpansion() }} value={leafDurationMinutes} /><small>分钟；仅在章节内部划分处理段，不改变章节边界。</small></label>
                    {selectorMode === 'exact_frames' && currentSourceMode !== 'pre_chaptered' && <label className="creator-setting-wide">下一章首帧<textarea aria-label="Exact chapter frames" aria-invalid={invalidSetting?.field === 'Exact chapter frames' || undefined} disabled={controlsDisabled} onChange={(event) => { setSelectorFrames(event.target.value); invalidateExpansion() }} placeholder="899" value={selectorFrames} /><small>帧号从 0 开始，多个切分点用逗号分隔。例如 1801 帧素材填写 899，分成 899 + 902 帧两章；不是每 899 帧切一段。</small></label>}
                    {selectorMode === 'exact_times' && currentSourceMode !== 'pre_chaptered' && <label className="creator-setting-wide">切分时间（秒）<textarea aria-label="Exact chapter times" aria-invalid={invalidSetting?.field === 'Exact chapter times' || undefined} disabled={controlsDisabled} onChange={(event) => { setSelectorTimes(event.target.value); invalidateExpansion() }} placeholder="1800, 7207200/1001" value={selectorTimes} /><small>使用秒数或精确分数，多个切分点用逗号分隔；准确帧边界由分析结果校验。</small></label>}
                  </div>}
                </section>
                <section className="creator-settings-group" aria-label="画质增强">
                  <h4><span>2</span>画质增强</h4>
                  <p>各处理段交给外部工具增强；以下设置必须与实际提交的文件一致。</p>
                  <div className="template-form-grid">
                    <label>画质增强模型<input aria-label="Enhancement model name" aria-invalid={invalidSetting?.field === 'Enhancement model name' || undefined} disabled={controlsDisabled} onChange={(event) => { setEnhancementModelName(event.target.value); invalidateExpansion() }} value={enhancementModelName} /></label>
                    <label>增强倍率<input aria-label="Enhancement actual scale factor" aria-invalid={invalidSetting?.field === 'Enhancement actual scale factor' || undefined} disabled={controlsDisabled} inputMode="numeric" onChange={(event) => { setActualScaleFactor(event.target.value); invalidateExpansion() }} value={actualScaleFactor} /><small>相对流程的 1920 × 1080 输入：1 为 1080p，2 为 4K。这是外部产物要求，不会自动替你放大。</small></label>
                    <label>增强模型版本（可选）<input aria-label="Enhancement model version" aria-invalid={invalidSetting?.field === 'Enhancement model version' || undefined} disabled={controlsDisabled} onChange={(event) => { setEnhancementModelVersion(event.target.value); invalidateExpansion() }} value={enhancementModelVersion} /><small>知道实际版本时填写；留空不影响下一步。</small></label>
                  </div>
                </section>
                <section className="creator-settings-group" aria-label="章节补帧">
                  <h4><span>3</span>章节补帧</h4>
                  <p>{overlapEnabled ? '先章内合并增强段，再收集相邻章节上下文进行两倍补帧，最后精确裁边。需要等待所引用的相邻章节增强完成。' : '先合并同一章的增强段，再对完整章节补帧；当前流程固定为两倍帧率。'}</p>
                  {overlapEnabled ? <><p><strong>Aion · 软件 v1.0 · 待真实验收</strong></p><p>外部原始结果按 2M−1 帧检查，裁边另存；不保证与整片单次 AI 处理像素一致。</p><details><summary>补帧上下文设置（高级，可使用默认值）</summary><p>以下是候选工程默认值，不是已经证实的模型最低要求；最终以短真实测试验收为准。</p><div className="template-form-grid">
                    <label>前置上下文帧数<input aria-label="前置上下文帧数" inputMode="numeric" disabled={controlsDisabled} value={contextLeft} onChange={(event) => { setContextLeft(event.target.value); invalidateExpansion() }} /></label>
                    <label>后置上下文帧数<input aria-label="后置上下文帧数" inputMode="numeric" disabled={controlsDisabled} value={contextRight} onChange={(event) => { setContextRight(event.target.value); invalidateExpansion() }} /></label>
                    <label>最短补帧输入帧数<input aria-label="最短补帧输入帧数" inputMode="numeric" disabled={controlsDisabled} value={contextMinimum} onChange={(event) => { setContextMinimum(event.target.value); invalidateExpansion() }} /></label>
                  </div>{overlapIssue?.fieldPath.includes('fi_profile') && <p role="alert">{overlapIssue.message}</p>}</details></> : <div className="template-form-grid">
                    <label>补帧模型<input aria-label="FI model name" aria-invalid={invalidSetting?.field === 'FI model name' || undefined} disabled={controlsDisabled} onChange={(event) => { setFiModelName(event.target.value); invalidateExpansion() }} value={fiModelName} /></label>
                    <label>补帧模型版本（可选）<input aria-label="FI model version" aria-invalid={invalidSetting?.field === 'FI model version' || undefined} disabled={controlsDisabled} onChange={(event) => { setFiModelVersion(event.target.value); invalidateExpansion() }} value={fiModelVersion} /><small>可留空；请按外部工具实际使用版本填写。</small></label>
                  </div>}
                </section>
                <section className="creator-settings-group" aria-label="成片编码">
                  <h4><span>4</span>成片编码</h4>
                  <div className="template-form-grid">
                    <label>成片编码器<select aria-label="Program encoder" disabled={controlsDisabled} onChange={(event) => { setEncoder(event.target.value as 'gpu' | 'cpu'); invalidateExpansion() }} value={encoder}><option value="gpu">GPU · NVIDIA 硬件编码</option><option value="cpu">CPU · 软件编码</option></select><small>GPU 需要支持 NVENC 的 NVIDIA 显卡；CPU 使用 libx265，通常耗时更长。不会自动切换编码器。</small></label>
                    <p className="creator-setting-note">成片使用 HEVC 10-bit 编码，并保留原音轨。编码器决定由显卡还是 CPU 完成这一步。</p>
                  </div>
                </section>
              </div>
              <details className="creator-settings-advanced"><summary>其他选项（可选）</summary>
                <section className="creator-output-picker" aria-label="成片存放位置">
                  <h4>存放位置</h4>
                  <div className="creator-storage-actions">
                    <button className="button button--ghost" aria-label="更改成片父目录" disabled={serviceControlsDisabled || !pickerAvailable} onClick={() => void chooseOutput()} type="button">更改成片父目录</button>
                    <button className="button button--ghost" disabled={controlsDisabled} onClick={() => { setOutputOrigin('project'); setOutputRoot(''); setOutputSelectionExpired(true); invalidateExpansion() }} type="button">恢复工程目录</button>
                    {outputOrigin === 'custom' && outputRoot && <button className="button button--ghost" disabled={serviceControlsDisabled || outputSelectionExpired || !onRevealOutputDirectory} onClick={() => void revealOutput()} type="button">打开所选输出文件夹</button>}
                  </div>
                  <span>{outputOrigin === 'custom' ? `已选择输出目录：${outputRoot || '尚未填写'}` : '使用工程目录，无需再次选择；工程工作数据的专用磁盘设置不影响此处。'}</span>
                  <label className="template-check"><input aria-label="按片名创建子文件夹" checked={outputLayout === 'title_subdirectory'} disabled={controlsDisabled} onChange={(event) => { setOutputLayout(event.target.checked ? 'title_subdirectory' : 'direct'); invalidateExpansion() }} type="checkbox" />按片名创建子文件夹（适合媒体库整理）</label>
                  <small>默认按“片名（年份）”创建；关闭后直接保存到父目录。无需提前创建片名文件夹。</small>
                </section>
                <section className="creator-output-picker" aria-label="已有成片保护">
                  <h4>已有文件保护</h4>
                  <label className="template-check"><input aria-label="允许覆盖发布目标" checked={overwrite} disabled={controlsDisabled} onChange={(event) => { setOverwrite(event.target.checked); invalidateExpansion() }} type="checkbox" />如果成片目标已经存在，明确允许覆盖</label>
                  <small>默认不覆盖。此许可只针对最终成片，不能覆盖源素材。</small>
                </section>
                <label>手动指定成片父目录（开发与故障排查）<input aria-label="Publication output root" aria-invalid={invalidSetting?.field === 'Publication output root' || undefined} disabled={controlsDisabled} onChange={(event) => { setOutputOrigin('custom'); setOutputRoot(event.target.value); setOutputSelectionExpired(true); invalidateExpansion() }} placeholder="留在默认模式时无需填写" value={outputRoot} /><small>手动填写后启用自定义位置；清空不会自动恢复默认，请使用“恢复工程目录”。</small></label>
              </details>
            </section>
          )}

          {step === 4 && (
            <section className="creator-step creator-analysis" aria-label="分析素材">
              <header><span>04</span><div><h3>分析素材并生成准确方案</h3><p>只有点击下方按钮后，ZNIKU 才会创建工程并开始分析；仅打开页面不会修改任何内容。</p></div></header>
              {publicationPreview && <div className="creator-target" aria-label="输出位置检查结果"><span>输出位置已检查</span><strong>{publicationPreview.output_directory}</strong><small>{publicationPreview.will_create_directory ? '将在开始处理后的输出步骤创建此文件夹；当前检查、分析和确认工作流均不创建目录。' : '成片将保存到此目录；已有文件仍需要明确允许覆盖。'}</small></div>}
              {!preparationCreated ? <button className="creator-analysis-action" disabled={serviceControlsDisabled} onClick={() => void analyze()} type="button"><span>◎</span><strong>{submitting ? '正在准备素材分析…' : '开始分析素材'}</strong><small>先安全检查，再创建工程并分析真实媒体信息</small></button> : analysisRunId ? <div className={`creator-analysis-state is-${analysisSummary?.state ?? 'pending'}`} role="status"><strong>{analysisSummary?.state === 'completed' ? isPublicationError(previewFailure?.code) ? '素材分析已完成，输出位置尚未就绪' : previewFailure ? '素材分析已完成，工作流预览尚未就绪' : '素材分析完成' : analysisSummary?.state === 'failed' ? '素材分析没有完成' : analysisSummary?.requires_operator_action ? '需要完成一个外部处理步骤' : '正在分析素材'}</strong><p>{analysisSummary?.state === 'completed' ? submitting ? '正在从这次准确结果生成工作流预览。' : previewFailure ? '这次素材分析记录仍然保留；处理下方提示后即可重新检查，不需要重复分析素材。' : '可使用这次已完成分析生成工作流预览。' : analysisSummary?.state === 'failed' ? '工程和已完成结果仍保留；可返回工作区查看问题，或重新启动分析。' : analysisSummary?.requires_operator_action ? '请返回工作区完成马赛克修复；文件出现不会自动提交。' : `${analysisSummary?.state_counts.completed ?? 0} / ${analysisSummary?.node_count ?? '—'} 个分析步骤已完成`}</p>{analysisSummary?.state === 'failed' && <button className="button button--primary" disabled={serviceControlsDisabled} onClick={() => void restartAnalysis()} type="button">重新分析</button>}</div> : <div className="creator-analysis-records"><strong>选择一次已完成的素材分析</strong><p>恢复已有工程时请按时间明确选择；界面不会猜测“最新”记录。</p>{completedRuns.length === 0 ? <p>当前工程还没有可用的完成记录。请返回工作区先完成素材准备。</p> : completedRuns.map((summary, index) => <button disabled={serviceControlsDisabled} key={summary.run_id} onClick={() => selectCompletedAnalysis(summary.run_id)} type="button"><span>✓</span><strong>分析记录 {index + 1} · {humanRunTime(summary.created_at)} 完成</strong><small>{summary.node_count} 个步骤均已完成</small></button>)}</div>}
              {analysisSummary?.state === 'completed' && previewFailure && outputRoot && <button className="button button--ghost" disabled={serviceControlsDisabled || outputSelectionExpired || !onRevealOutputDirectory} onClick={() => void revealOutput()} type="button">打开所选输出文件夹</button>}
            </section>
          )}

          {step === 5 && overlapEnabled && overlapPreview && <ChapterOverlapPreview preview={overlapPreview} />}
          {step === 5 && !overlapEnabled && preview && (
            <section className="creator-step creator-confirm" aria-label="确认工作流">
              <header><span>05</span><div><h3>确认工作流</h3><p>以下结构、媒体信息和输出名称均来自本次准确分析；确认前不会改动当前工作流。</p></div></header>
              <div className={`creator-profile-result ${preview.profile.compatible ? 'is-compatible' : 'is-incompatible'}`} role="status"><strong>{preview.profile.compatible ? '工作流已就绪' : '工作流需要修正'}</strong><span>{preview.project.graph.nodes.length} 个节点 · {preview.creator.estimated_steps} · {preview.plan.chapter_count} 章 · {preview.plan.leaf_count} 个处理段</span></div>
              {preview.creator.sources.length > 0 && <section className="creator-media-summary" aria-label="媒体摘要"><h4>素材信息</h4>{preview.creator.sources.map((source) => <article key={source.source_ordinal}><strong>{source.display_name}</strong>{source.chapter_label && <span>{source.chapter_label}</span>}<dl><div><dt>文件</dt><dd>{source.size_label} · {source.container}</dd></div><div><dt>画面</dt><dd>{source.resolution}</dd></div><div><dt>帧率</dt><dd>{source.frame_rate}</dd></div><div><dt>时长</dt><dd>{source.duration}</dd></div><div><dt>帧数</dt><dd>{source.frame_count}</dd></div><div><dt>音轨</dt><dd>{source.audio_tracks.length > 0 ? source.audio_tracks.map((track) => track.label).join('；') : '无音轨'}</dd></div></dl><details><summary>媒体技术信息</summary><dl><div><dt>视频编码</dt><dd>{source.video_codec}</dd></div><div><dt>像素格式</dt><dd>{source.pixel_format}</dd></div>{source.audio_tracks.map((track) => <div key={track.ordinal}><dt>音轨 {track.ordinal + 1}</dt><dd>{track.codec}{track.channels ? ` · ${track.channels} 声道` : ''}{track.sample_rate ? ` · ${track.sample_rate} Hz` : ''}{track.language ? ` · ${track.language}` : ''}{track.title ? ` · ${track.title}` : ''}</dd></div>)}</dl></details></article>)}</section>}
              <section className="creator-workflow-summary"><h4>处理顺序</h4><div>{preview.plan.manual_stages.map((stage, index) => <span key={stage.stage}><i>{index + 1}</i><strong>{stageLabel(stage.stage)}</strong><small>{stage.node_count} 个步骤 · {stage.output_container}</small></span>)}</div></section>
              {preview.plan.output_target_path && <div className="creator-target"><span>预计成片</span><strong>{preview.plan.output_target_path}</strong><small>{preview.plan.output_directory_to_create ? `将在开始处理后的输出步骤创建文件夹：${preview.plan.output_directory_to_create}。确认工作流、预览和取消都不会创建。` : '将保存到以上完整路径；已有文件仍需要明确允许覆盖。'}</small></div>}
              <label className="template-check creator-overwrite"><input aria-label="允许覆盖发布目标" checked={overwrite} disabled={controlsDisabled} onChange={(event) => { setOverwrite(event.target.checked); invalidateExpansion(); setStep(3) }} type="checkbox" />如果目标已经存在，明确允许覆盖</label>
              <details className="creator-technical-preview"><summary>查看精确章节与技术诊断</summary>{preview.plan.chapters.map((chapter) => <article key={chapter.chapter_id}><strong>{chapter.label}</strong><span>{chapter.start_timecode} → {chapter.end_timecode}</span><code>[{chapter.start_frame}, {chapter.end_frame}) · {chapter.leaves.length} 段</code></article>)}{preview.profile.diagnostics.map((diagnostic, index) => <article key={`${diagnostic.code}-${index}`}><strong>{diagnostic.code}</strong><p>{diagnostic.message}</p>{diagnostic.node_id && <button onClick={() => onLocateNode(diagnostic.node_id!)} type="button">定位对应节点</button>}</article>)}</details>
            </section>
          )}
        </div>

        <footer className="template-wizard-footer creator-wizard-footer">
          <div>{(localError || serviceError) && <div className="template-error-stack" role="alert">{serviceError && serviceError !== localError && !previewFailure && <p className="template-local-error">{serviceError}</p>}{localError && <p className="template-local-error" id={missingProjectField || missingChapter ? 'creator-project-field-error' : undefined}>{localError}</p>}{pickerFailure?.message === localError && <details><summary>高级 → 选择窗口原始详情</summary><pre>{pickerFailure.rawMessage}</pre></details>}{previewFailure?.recoveryMessage && <p className="template-local-error">{previewFailure.recoveryMessage}</p>}{previewFailure && <details><summary>高级 → 输出位置与预览详情</summary>{previewFailure.code && <code>{previewFailure.code}</code>}<pre>{previewFailure.message}</pre></details>}</div>}</div>
          <div className="creator-footer-actions">
            {step > 1 && step < 5 && !preparationCreated && <button className="button button--ghost" disabled={controlsDisabled} onClick={() => { invalidateExpansion(); setStep((step - 1) as WizardStep) }} type="button">上一步</button>}
            {step === 3 && checkingOutput && <button className="button button--ghost" onClick={invalidateExpansion} type="button">取消检查</button>}
            {step < 4 && <button className="button button--primary" disabled={controlsDisabled || checkingOutput || (step === 3 && serviceControlsDisabled)} onClick={() => void moveNext()} type="button">{checkingOutput ? '正在检查输出位置…' : `下一步：${(stepLabels as ReadonlyArray<string>)[step] ?? ''}`}</button>}
            {step === 4 && preparationCreated && <button className="button button--ghost" disabled={controlsDisabled} onClick={returnToSettings} type="button">修改设置</button>}
            {step === 4 && preparationCreated && analysisRunId && analysisSummary?.state === 'completed' && !submitting && !preview && <button className="button button--primary" disabled={serviceControlsDisabled} onClick={() => void previewExpansion(analysisRunId)} type="button">{isPublicationError(previewFailure?.code) ? '重新检查输出位置' : previewFailure ? '重新生成工作流预览' : '生成工作流预览'}</button>}
            {step === 4 && mode === 'resume' && analysisRunId && !submitting && <button className="button button--ghost" disabled={controlsDisabled} onClick={() => { invalidateExpansion(); setAnalysisRunId(null) }} type="button">改选分析记录</button>}
            {step === 5 && <button className="button button--ghost" disabled={controlsDisabled} onClick={returnToSettings} type="button">返回设置</button>}
            {step === 5 && <button className="button button--primary" disabled={serviceControlsDisabled || (overlapEnabled ? !overlapPreview : !preview?.profile.compatible)} onClick={() => void confirmWorkflow()} type="button">确认并创建工作流</button>}
          </div>
        </footer>
      </section>
    </div>
  )
}
