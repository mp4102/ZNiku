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
export type AvEnhanceV27WizardMode = 'create' | 'resume'

interface SourceDraft {
  readonly source_path: string
  readonly chapter_label: string
}

export interface AvEnhanceV27WizardProps {
  readonly open: boolean
  readonly mode: AvEnhanceV27WizardMode
  readonly busy: boolean
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
  readonly onLocateNode: (nodeId: string) => void
}

const stepLabels = ['选择素材', '处理方案', '设置', '分析', '确认工作流'] as const

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
  onLocateNode,
}: AvEnhanceV27WizardProps) {
  const wasOpen = useRef(false)
  const dialogRef = useRef<HTMLElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)
  const previewRequestRef = useRef<AvEnhanceV27TemplatePreviewRequestWire | null>(null)
  const responseEpochRef = useRef(0)
  const pickerFlightRef = useRef(0)
  const expansionFlightRef = useRef<{ readonly runId: string; readonly token: symbol } | null>(null)
  const outputOpenFlightRef = useRef<symbol | null>(null)
  const analysisRunIdRef = useRef<string | null>(null)
  const publicationFlightRef = useRef<symbol | null>(null)

  const [step, setStep] = useState<WizardStep>(1)
  const [dataParent, setDataParent] = useState<string | null>(null)
  const [preview, setPreview] = useState<AvEnhanceV27TemplatePreviewEnvelope | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const [pickerFailure, setPickerFailure] = useState<{ readonly message: string; readonly rawMessage: string } | null>(null)
  const [previewFailure, setPreviewFailure] = useState<{ readonly code: string | null; readonly message: string; readonly recoveryMessage: string | null } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [openingOutput, setOpeningOutput] = useState(false)
  const [checkingOutput, setCheckingOutput] = useState(false)
  const [publicationPreview, setPublicationPreview] = useState<AvEnhanceV27PublicationPreviewEnvelope | null>(null)
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
  const [title, setTitle] = useState('')
  const [year, setYear] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [outputLayout, setOutputLayout] = useState<'direct' | 'title_subdirectory'>('direct')

  const currentSourceMode = mode === 'resume'
    ? sourceModeFromSnapshot(currentSnapshot) ?? sourceMode
    : sourceMode
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
  }, [])

  useEffect(() => {
    if (open && !wasOpen.current) {
      previousFocusRef.current = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
      const continuing = mode === 'resume'
      setStep(continuing ? 3 : 1)
      setPreview(null)
      previewRequestRef.current = null
      expansionFlightRef.current = null
      responseEpochRef.current += 1
      pickerFlightRef.current += 1
      setLocalError(null)
      setPickerFailure(null)
      setPreviewFailure(null)
      setOpeningOutput(false)
      setCheckingOutput(false)
      setPublicationPreview(null)
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
      setOutputRoot('')
      setTitle('')
      setYear('')
      setOverwrite(false)
      setOutputLayout('direct')
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
  }, [currentProjectId, currentProjectName, currentProjectPath, mode, open, projectIdFactory, setAnalysisRunId])

  useEffect(() => {
    if (!open) return
    const handleKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      if (event.key === 'Escape' && !submitting && !busy && !openingOutput) {
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
  }, [busy, onClose, open, openingOutput, submitting])

  const invalidateExpansion = () => {
    responseEpochRef.current += 1
    pickerFlightRef.current += 1
    expansionFlightRef.current = null
    previewRequestRef.current = null
    setPreview(null)
    setLocalError(null)
    setPickerFailure(null)
    setPreviewFailure(null)
    setOpeningOutput(false)
    setCheckingOutput(false)
    setPublicationPreview(null)
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
    if (!projectPath.trim() || !projectName.trim()) {
      throw new Error('请选择工程保存位置，并填写工程名称。')
    }
    if (!projectId) throw new Error('无法生成工程身份；请关闭向导后重试。')
    if (sources.length === 0) throw new Error('至少需要一个视频素材。')
    if (sourceMode === 'program' && sources.length !== 1) {
      throw new Error('“一条完整视频”只能选择一个素材。')
    }
    const normalizedSources = sources.map((source, sourceOrdinal) => {
      if (!source.source_path.trim()) throw new Error(`请选择第 ${sourceOrdinal + 1} 个视频素材。`)
      if (sourceMode === 'pre_chaptered') {
        if (!source.chapter_label.trim()) throw new Error(`请填写第 ${sourceOrdinal + 1} 段的章节名称。`)
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

  const buildExpandRequest = (preparationRunId: string): AvEnhanceV27ExpandRequestWire => {
    if (!preparationRunId) throw new Error('请选择一次已经完成的素材分析记录。')
    if (!enhancementModelName.trim() || !fiModelName.trim()) {
      throw new Error('请填写画质增强与补帧使用的模型名称。')
    }
    if (!outputRoot.trim() || !title.trim() || !year.trim()) {
      throw new Error('请选择输出文件夹，并填写片名与四位年份。')
    }
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
      profile_version: '2.7.0',
      preparation_run_id: preparationRunId,
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
      publication: buildPublicationRequest(),
    }
  }

  const buildPublicationRequest = (): AvEnhanceV27PublicationRequestWire => ({
    output_root: outputRoot, title, year, overwrite, layout: outputLayout,
  })

  const previewExpansion = async (runId: string) => {
    if (expansionFlightRef.current) return
    const epoch = responseEpochRef.current
    const token = Symbol('expansion-preview')
    expansionFlightRef.current = { runId, token }
    setSubmitting(true)
    setLocalError(null)
    setPreviewFailure(null)
    try {
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
      !analysisRunId ||
      analysisRunIdRef.current !== analysisRunId ||
      analysisSummary?.state !== 'completed' ||
      previewRequestRef.current?.action === 'expand' ||
      expansionFlightRef.current?.runId === analysisRunId
    ) return
    void previewExpansion(analysisRunId)
    // 表单改变时只废弃旧预览；已完成的 exact Run 保留，用户重新检查输出并显式生成预览即可。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysisRunId, analysisSummary?.state, open])

  if (!open) return null

  const controlsDisabled = busy || submitting || openingOutput

  const reportPickerError = (error: unknown, fallback: string) => {
    const rawMessage = error instanceof Error ? error.message : fallback
    const message = formatHostBridgeError(error) ?? rawMessage
    setLocalError(message)
    setPickerFailure({ message, rawMessage })
  }

  const chooseProjectPath = async () => {
    if (!onPickProjectPath) return
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
    if (!onPickDataDirectory) return
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
    if (!onPickSources) return
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
    if (!onPickOutputDirectory) return
    const epoch = responseEpochRef.current
    const flight = ++pickerFlightRef.current
    try {
      const path = await onPickOutputDirectory()
      if (path === null || epoch !== responseEpochRef.current || flight !== pickerFlightRef.current) return
      setOutputRoot(path)
      invalidateExpansion()
    } catch (error) {
      if (epoch === responseEpochRef.current && flight === pickerFlightRef.current) {
        reportPickerError(error, '无法打开成片文件夹选择器。')
      }
    }
  }

  const revealOutput = async () => {
    if (!onRevealOutputDirectory || !outputRoot || outputOpenFlightRef.current) return
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
        if (publicationFlightRef.current) return
        if (!enhancementModelName.trim() || !fiModelName.trim() || !outputRoot.trim() || !title.trim() || !year.trim()) {
          throw new Error('请完成画质增强、补帧和输出设置。')
        }
        if (!/^[0-9]{4}$/.test(year)) throw new Error('年份必须是四位数字。')
        const epoch = responseEpochRef.current
        const token = Symbol('publication-check')
        publicationFlightRef.current = token
        setCheckingOutput(true)
        try {
          // 此检查不依赖媒体、不保存工程、不创建目录。输入变化或取消立即撤销迟到响应的展示资格。
          const checked = await onPreviewPublication({ contract_version: '0.3.0', request: buildPublicationRequest() })
          if (epoch !== responseEpochRef.current || publicationFlightRef.current !== token) return
          if (checked.layout !== outputLayout) throw new Error('输出检查与当前整理方式不一致，请重试。')
          setPublicationPreview(checked)
          setStep(4)
        } catch (error) {
          if (epoch === responseEpochRef.current && publicationFlightRef.current === token) {
            setLocalError(`输出位置未通过检查。工程、素材和已有分析均保持不变。${error instanceof StudioGatewayError ? error.serviceMessage ?? error.message : error instanceof Error ? error.message : '请检查所选目录后重试。'}`)
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
      const created = await onCreate(prepareRequest, {
        ...(dataParent ? { data_parent_directory: dataParent } : {}),
        media_basename: `${title} (${year})`,
      })
      if (epoch !== responseEpochRef.current || !open) return
      previewRequestRef.current = null
      if (!created) throw new Error('工程没有创建；素材和原有工程保持不变。')
      setPreparationCreated(true)
      if (!onStartPreparationRun) {
        throw new Error('当前入口无法启动素材分析；工程已安全创建，可返回工作区继续。')
      }
      const runId = await onStartPreparationRun()
      if (epoch !== responseEpochRef.current || !open) return
      if (!runId) throw new Error('素材分析没有启动；工程已创建，但没有产生 Run。')
      setAnalysisRunId(runId)
    } catch (error) {
      if (epoch === responseEpochRef.current) {
        setLocalError(error instanceof Error ? error.message : '素材分析失败。')
      }
    } finally {
      if (epoch === responseEpochRef.current) setSubmitting(false)
    }
  }

  const restartAnalysis = async () => {
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
      const runId = await onStartPreparationRun()
      if (epoch !== responseEpochRef.current || !open) return
      if (!runId) throw new Error('素材分析没有启动；原工程和已完成结果保持不变。')
      setAnalysisRunId(runId)
    } catch (error) {
      if (epoch === responseEpochRef.current) {
        setLocalError(error instanceof Error ? error.message : '无法重新启动素材分析。')
      }
    } finally {
      if (epoch === responseEpochRef.current) setSubmitting(false)
    }
  }

  const selectCompletedAnalysis = (runId: string) => {
    invalidateExpansion()
    setAnalysisRunId(runId)
  }

  const returnToSettings = () => {
    invalidateExpansion()
    setStep(3)
  }

  const confirmWorkflow = async () => {
    const authority = previewRequestRef.current
    if (!preview?.profile.compatible || !authority || authority.action !== 'expand') {
      invalidateExpansion()
      setLocalError('当前设置没有可确认的 Python 工作流预览；请使用已完成的素材分析重新生成预览。')
      setStep(4)
      return
    }
    setSubmitting(true)
    setLocalError(null)
    try {
      const applied = await onExpand(authority.request)
      previewRequestRef.current = null
      if (applied) onClose()
      else {
        setPreview(null)
        setLocalError('工作流未写入工程；预览需要重新检查，已完成的素材分析仍保留。')
        setStep(4)
      }
    } catch (error) {
      previewRequestRef.current = null
      setPreview(null)
      setLocalError(error instanceof Error ? error.message : '工作流创建失败。')
      setStep(4)
    } finally {
      setSubmitting(false)
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
          {step === 1 && (
            <section className="creator-step" aria-label="选择素材">
              <header><span>01</span><div><h3>选择要处理的视频</h3><p>ZNIKU 只记录你选择的本机路径，不上传、复制或改写源视频。</p></div></header>
              <label>素材组织方式<select aria-label="素材组织方式" disabled={controlsDisabled || preparationCreated} onChange={(event) => { const next = event.target.value as AvEnhanceV27SourceMode; setSourceMode(next); if (next === 'program') setSources((current) => current.slice(0, 1)); invalidateExpansion() }} value={sourceMode}><option value="program">一条完整视频</option><option value="pre_chaptered">已经分章的多个视频</option></select></label>
              <button className="creator-picker" disabled={controlsDisabled || preparationCreated || !pickerAvailable} onClick={() => void chooseSources()} type="button"><span>▣</span><strong>{sourceMode === 'program' ? '选择视频素材' : '选择全部章节视频'}</strong><small>{pickerAvailable ? '使用 Windows 文件选择器' : '桌面选择器未连接，可在下方高级入口填写'}</small></button>
              <div className="creator-source-list">{sources.map((source, index) => <article key={`${index}-${source.source_path}`}><span>{index + 1}</span><div><strong>{source.source_path ? pathName(source.source_path) : '尚未选择'}</strong><small>{source.source_path ? '本机文件已选择，原文件保持只读' : '不会在选择前创建工程或目录'}</small></div>{sourceMode === 'pre_chaptered' && <input aria-label={`第 ${index + 1} 章名称`} disabled={controlsDisabled || preparationCreated} onChange={(event) => updateSource(index, { chapter_label: event.target.value })} value={source.chapter_label} />}</article>)}</div>
              <div className="creator-project-fields"><label>工程名称<input aria-label="工程名称" disabled={controlsDisabled || preparationCreated} maxLength={200} onChange={(event) => { setProjectName(event.target.value); invalidateExpansion() }} value={projectName} /></label><button className="button button--ghost" disabled={controlsDisabled || preparationCreated || !pickerAvailable} onClick={() => void chooseProjectPath()} type="button">选择 .zniku 保存位置</button>{projectPath && <span className="creator-selected-path">已选择：{pathName(projectPath)}</span>}</div>
              <details className="creator-advanced-entry"><summary>开发浏览器高级入口</summary><p>正式桌面路径使用原生选择器；这里只为开发环境保留手工路径。</p><label>工程路径<input aria-label="模板工程路径" disabled={controlsDisabled || preparationCreated} onChange={(event) => { setProjectPath(event.target.value); invalidateExpansion() }} value={projectPath} /></label>{sources.map((source, index) => <label key={index}>Source {index + 1} path<input aria-label={`Source ${index + 1} path`} disabled={controlsDisabled || preparationCreated} onChange={(event) => updateSource(index, { source_path: event.target.value })} value={source.source_path} /></label>)}{sourceMode === 'pre_chaptered' && <button className="button button--ghost" disabled={controlsDisabled || preparationCreated} onClick={() => { setSources((current) => [...current, { source_path: '', chapter_label: '' }]); invalidateExpansion() }} type="button">添加 Source</button>}</details>
            </section>
          )}

          {step === 1 && <section className="creator-step" aria-label="工作数据位置">
            <h3>工作数据</h3>
            <p>中间产物和外部处理结果默认保存在工程旁的同名 .data 文件夹，完成或退出不会自动清除。</p>
            <p>{dataParent ? `专用磁盘父目录：${dataParent}` : '使用工程旁默认位置'}</p>
            <button className="button button--ghost" type="button" disabled={controlsDisabled || preparationCreated || !onPickDataDirectory} onClick={() => void chooseDataDirectory()}>选择工作数据父目录</button>
            {dataParent && <button type="button" disabled={controlsDisabled || preparationCreated} onClick={() => { setDataParent(null); invalidateExpansion() }}>恢复工程旁默认</button>}
            <small>系统会在所选父目录内新建工程同名 .data 文件夹，不会混用其他工程的数据。</small>
          </section>}

          {step === 2 && (
            <section className="creator-step" aria-label="处理方案">
              <header><span>02</span><div><h3>选择处理方案</h3><p>标准流程会先增强每段画面，合并章节后再统一补帧和编码。</p></div></header>
              <article className="creator-plan-card is-selected"><span>推荐</span><h4>完整增强流程</h4><p>画质增强 → 合并 → 补帧 → 连续 Main10 编码 → 保留原始音频。</p><ul><li>节点仍可在创建后自由调整</li><li>外部工具步骤会逐项引导</li><li>同一工程可随时切换到节点图继续编辑</li></ul></article>
              <details className="creator-settings-advanced"><summary>马赛克修复（高级）</summary><label className="template-check"><input aria-label="启用外部马赛克修复" checked={mrMode === 'external'} disabled={controlsDisabled || preparationCreated} onChange={(event) => { setMrMode(event.target.checked ? 'external' : 'off'); invalidateExpansion() }} type="checkbox" />素材分析前先在 Jasna 等外部工具中修复马赛克</label>{mrMode === 'external' && <div className="template-form-grid"><label>工具/模型<input aria-label="MR model name" disabled={controlsDisabled || preparationCreated} onChange={(event) => { setMrModelName(event.target.value); invalidateExpansion() }} value={mrModelName} /></label><label>实际版本<input aria-label="MR model version" disabled={controlsDisabled || preparationCreated} onChange={(event) => { setMrModelVersion(event.target.value); invalidateExpansion() }} value={mrModelVersion} /></label></div>}</details>
            </section>
          )}

          {step === 3 && (
            <section className="creator-step" aria-label="设置">
              <header><span>03</span><div><h3>设置成片目标</h3><p>先填写创作决策；精确帧、编码器和模型版本可在高级设置中调整。</p></div></header>
              <div className="creator-basic-settings">
                <label>片名<input aria-label="片名" disabled={controlsDisabled} onChange={(event) => { setTitle(event.target.value); invalidateExpansion() }} value={title} /></label>
                <label>年份<input aria-label="年份" disabled={controlsDisabled} inputMode="numeric" maxLength={4} onChange={(event) => { setYear(event.target.value); invalidateExpansion() }} value={year} /></label>
                <div className="creator-output-picker">
                  <button className="button button--ghost" disabled={controlsDisabled || !pickerAvailable} onClick={() => void chooseOutput()} type="button">选择成片文件夹</button>
                  {outputRoot && <span>已选择输出目录：{outputRoot}</span>}
                  {outputRoot && <button className="button button--ghost" disabled={controlsDisabled || !onRevealOutputDirectory} onClick={() => void revealOutput()} type="button">打开所选输出文件夹</button>}
                  <small>默认直接保存到所选目录，无需预先创建片名文件夹。下一步会先检查输出位置，不创建目录或开始分析。</small>
                  <label className="template-check"><input aria-label="按片名创建子文件夹" checked={outputLayout === 'title_subdirectory'} disabled={controlsDisabled} onChange={(event) => { setOutputLayout(event.target.checked ? 'title_subdirectory' : 'direct'); invalidateExpansion() }} type="checkbox" />按片名创建子文件夹（适合媒体库整理）</label>
                  {outputLayout === 'title_subdirectory' && <small>最终路径由运行服务生成；若子文件夹不存在，将在开始处理后的输出步骤创建。选择、预览和确认工作流均不创建目录。</small>}
                  <label className="template-check"><input aria-label="允许覆盖发布目标" checked={overwrite} disabled={controlsDisabled} onChange={(event) => { setOverwrite(event.target.checked); invalidateExpansion() }} type="checkbox" />如果成片目标已经存在，明确允许覆盖</label>
                  <small>覆盖只针对最终成片，不允许覆盖源素材；默认保留已有文件。</small>
                </div>
              </div>
              <details className="creator-settings-advanced"><summary>高级设置</summary><div className="template-form-grid"><label>画质增强模型<input aria-label="Enhancement model name" disabled={controlsDisabled} onChange={(event) => { setEnhancementModelName(event.target.value); invalidateExpansion() }} value={enhancementModelName} /><small>记录实际外部工具模型，不作为效果证明。</small></label><label>补帧模型<input aria-label="FI model name" disabled={controlsDisabled} onChange={(event) => { setFiModelName(event.target.value); invalidateExpansion() }} value={fiModelName} /></label><label>章节切分<select aria-label="Chapter selector mode" disabled={controlsDisabled || currentSourceMode === 'pre_chaptered'} onChange={(event) => { setSelectorMode(event.target.value as SelectorMode); invalidateExpansion() }} value={selectorMode}><option value="single">整片作为一章</option><option value="exact_frames">按精确帧切分</option><option value="exact_times">按精确时间切分</option></select></label>{selectorMode === 'exact_frames' && currentSourceMode !== 'pre_chaptered' && <label>下一章首帧<textarea aria-label="Exact chapter frames" disabled={controlsDisabled} onChange={(event) => { setSelectorFrames(event.target.value); invalidateExpansion() }} placeholder="899" value={selectorFrames} /></label>}{selectorMode === 'exact_times' && currentSourceMode !== 'pre_chaptered' && <label>切分时间（秒）<textarea aria-label="Exact chapter times" disabled={controlsDisabled} onChange={(event) => { setSelectorTimes(event.target.value); invalidateExpansion() }} placeholder="1800, 7207200/1001" value={selectorTimes} /></label>}<label>单段处理时长<input aria-label="Leaf duration minutes" disabled={controlsDisabled} inputMode="numeric" onChange={(event) => { setLeafDurationMinutes(event.target.value); invalidateExpansion() }} value={leafDurationMinutes} /><small>分钟；只决定分析如何派生处理段，不改变章节边界。</small></label><label>成片编码器<select aria-label="Program encoder" disabled={controlsDisabled} onChange={(event) => { setEncoder(event.target.value as 'gpu' | 'cpu'); invalidateExpansion() }} value={encoder}><option value="gpu">GPU · hevc_nvenc</option><option value="cpu">CPU · libx265</option></select></label><label>增强倍率<input aria-label="Enhancement actual scale factor" disabled={controlsDisabled} inputMode="numeric" onChange={(event) => { setActualScaleFactor(event.target.value); invalidateExpansion() }} value={actualScaleFactor} /></label><label>增强模型版本<input aria-label="Enhancement model version" disabled={controlsDisabled} onChange={(event) => { setEnhancementModelVersion(event.target.value); invalidateExpansion() }} value={enhancementModelVersion} /></label><label>补帧模型版本<input aria-label="FI model version" disabled={controlsDisabled} onChange={(event) => { setFiModelVersion(event.target.value); invalidateExpansion() }} value={fiModelVersion} /></label><label>开发浏览器输出路径<input aria-label="Publication output root" disabled={controlsDisabled} onChange={(event) => { setOutputRoot(event.target.value); invalidateExpansion() }} value={outputRoot} /></label></div></details>
            </section>
          )}

          {step === 4 && (
            <section className="creator-step creator-analysis" aria-label="分析素材">
              <header><span>04</span><div><h3>分析素材并生成准确方案</h3><p>只有点击下方按钮后，ZNIKU 才会创建工程并开始分析；仅打开页面不会修改任何内容。</p></div></header>
              {publicationPreview && <div className="creator-target" aria-label="输出位置检查结果"><span>输出位置已检查</span><strong>{publicationPreview.output_directory}</strong><small>{publicationPreview.will_create_directory ? '将在开始处理后的输出步骤创建此文件夹；当前检查、分析和确认工作流均不创建目录。' : '成片将保存到此目录；已有文件仍需要明确允许覆盖。'}</small></div>}
              {!preparationCreated ? <button className="creator-analysis-action" disabled={controlsDisabled} onClick={() => void analyze()} type="button"><span>◎</span><strong>{submitting ? '正在准备素材分析…' : '开始分析素材'}</strong><small>先安全检查，再创建工程并分析真实媒体信息</small></button> : analysisRunId ? <div className={`creator-analysis-state is-${analysisSummary?.state ?? 'pending'}`} role="status"><strong>{analysisSummary?.state === 'completed' ? isPublicationError(previewFailure?.code) ? '素材分析已完成，输出位置尚未就绪' : previewFailure ? '素材分析已完成，工作流预览尚未就绪' : '素材分析完成' : analysisSummary?.state === 'failed' ? '素材分析没有完成' : analysisSummary?.requires_operator_action ? '需要完成一个外部处理步骤' : '正在分析素材'}</strong><p>{analysisSummary?.state === 'completed' ? submitting ? '正在从这次准确结果生成工作流预览。' : previewFailure ? '这次素材分析记录仍然保留；处理下方提示后即可重新检查，不需要重复分析素材。' : '可使用这次已完成分析生成工作流预览。' : analysisSummary?.state === 'failed' ? '工程和已完成结果仍保留；可返回工作区查看问题，或重新启动分析。' : analysisSummary?.requires_operator_action ? '请返回工作区完成马赛克修复；文件出现不会自动提交。' : `${analysisSummary?.state_counts.completed ?? 0} / ${analysisSummary?.node_count ?? '—'} 个分析步骤已完成`}</p>{analysisSummary?.state === 'failed' && <button className="button button--primary" disabled={controlsDisabled} onClick={() => void restartAnalysis()} type="button">重新分析</button>}</div> : <div className="creator-analysis-records"><strong>选择一次已完成的素材分析</strong><p>恢复已有工程时请按时间明确选择；界面不会猜测“最新”记录。</p>{completedRuns.length === 0 ? <p>当前工程还没有可用的完成记录。请返回工作区先完成素材准备。</p> : completedRuns.map((summary, index) => <button disabled={controlsDisabled} key={summary.run_id} onClick={() => selectCompletedAnalysis(summary.run_id)} type="button"><span>✓</span><strong>分析记录 {index + 1} · {humanRunTime(summary.created_at)} 完成</strong><small>{summary.node_count} 个步骤均已完成</small></button>)}</div>}
              {analysisSummary?.state === 'completed' && previewFailure && outputRoot && <button className="button button--ghost" disabled={controlsDisabled || !onRevealOutputDirectory} onClick={() => void revealOutput()} type="button">打开所选输出文件夹</button>}
            </section>
          )}

          {step === 5 && preview && (
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
          <div>{(localError || serviceError) && <div className="template-error-stack" role="alert">{serviceError && serviceError !== localError && !previewFailure && <p className="template-local-error">{serviceError}</p>}{localError && <p className="template-local-error">{localError}</p>}{pickerFailure?.message === localError && <details><summary>高级 → 选择窗口原始详情</summary><pre>{pickerFailure.rawMessage}</pre></details>}{previewFailure?.recoveryMessage && <p className="template-local-error">{previewFailure.recoveryMessage}</p>}{previewFailure && <details><summary>高级 → 输出位置与预览详情</summary>{previewFailure.code && <code>{previewFailure.code}</code>}<pre>{previewFailure.message}</pre></details>}</div>}</div>
          <div className="creator-footer-actions">
            {step > 1 && step < 5 && !preparationCreated && <button className="button button--ghost" disabled={controlsDisabled} onClick={() => { invalidateExpansion(); setStep((step - 1) as WizardStep) }} type="button">上一步</button>}
            {step === 3 && checkingOutput && <button className="button button--ghost" onClick={invalidateExpansion} type="button">取消检查</button>}
            {step < 4 && <button className="button button--primary" disabled={controlsDisabled || checkingOutput} onClick={() => void moveNext()} type="button">{checkingOutput ? '正在检查输出位置…' : `下一步：${(stepLabels as ReadonlyArray<string>)[step] ?? ''}`}</button>}
            {step === 4 && preparationCreated && <button className="button button--ghost" disabled={controlsDisabled} onClick={returnToSettings} type="button">修改设置</button>}
            {step === 4 && preparationCreated && analysisRunId && analysisSummary?.state === 'completed' && !submitting && !preview && <button className="button button--primary" disabled={controlsDisabled} onClick={() => void previewExpansion(analysisRunId)} type="button">{isPublicationError(previewFailure?.code) ? '重新检查输出位置' : previewFailure ? '重新生成工作流预览' : '生成工作流预览'}</button>}
            {step === 4 && mode === 'resume' && analysisRunId && !submitting && <button className="button button--ghost" disabled={controlsDisabled} onClick={() => { invalidateExpansion(); setAnalysisRunId(null) }} type="button">改选分析记录</button>}
            {step === 5 && <button className="button button--ghost" disabled={controlsDisabled} onClick={returnToSettings} type="button">返回设置</button>}
            {step === 5 && <button className="button button--primary" disabled={controlsDisabled || !preview?.profile.compatible} onClick={() => void confirmWorkflow()} type="button">确认并创建工作流</button>}
          </div>
        </footer>
      </section>
    </div>
  )
}
