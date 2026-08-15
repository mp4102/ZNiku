import { MarkerType, type XYPosition } from '@xyflow/react'
import type {
  Diagnostic,
  MediaPort,
  PaletteItem,
  RunStatus,
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeData,
} from './model'

const node = (id: string, position: XYPosition, data: WorkflowNodeData): WorkflowNode => ({
  id,
  type: 'workflow',
  position,
  data,
})

interface EdgeOptions {
  sourceHandle?: string
  targetHandle?: string
  type?: string
  audioLane?: boolean
}

const edge = (
  source: string,
  target: string,
  label?: string,
  options: EdgeOptions = {},
): WorkflowEdge => ({
  id: `${source}-${target}`,
  source,
  target,
  sourceHandle: options.sourceHandle ?? 'out',
  targetHandle: options.targetHandle ?? 'in',
  label,
  type: options.type ?? 'smoothstep',
  markerEnd: {
    type: MarkerType.ArrowClosed,
    width: 16,
    height: 16,
    color: options.audioLane ? '#55b0d2' : undefined,
  },
  style: { strokeWidth: 1.6, stroke: options.audioLane ? '#55b0d2' : undefined },
})

const port = (
  id: string,
  label: string,
  artifactType: string,
  scope: MediaPort['scope'],
  cardinality: MediaPort['cardinality'] = 'one',
): MediaPort => ({ id, label, artifactType, scope, cardinality })

const baseData = (
  label: string,
  protocolId: string,
  subtitle: string,
  category: WorkflowNodeData['category'],
  icon: string,
  scope: WorkflowNodeData['scope'],
  description: string,
  extra: Partial<WorkflowNodeData> = {},
): WorkflowNodeData => ({
  label,
  protocolId,
  subtitle,
  category,
  icon,
  scope,
  description,
  inputs: category === 'source' ? [] : [port('in', 'video', 'video', scope)],
  outputs: category === 'final' ? [] : [port('out', 'video', 'video', scope)],
  execution: category === 'engine' ? 'manual external' : 'runtime operator',
  mediaChanges: [{ label: '媒体', value: '保持完整电影语义', tone: 'neutral' }],
  ...extra,
})

export const paletteItems: PaletteItem[] = [
  {
    id: 'demux',
    label: 'Demux',
    category: 'engine',
    icon: 'DX',
    scope: 'program',
    description: '将 ProgramMedia 分离为视频和有序音轨集合。',
    inputs: [port('program', 'program', 'program_media', 'program')],
    outputs: [
      port('video', 'video', 'program_video', 'program'),
      port('audio', 'audio_set', 'audio_artifact_set', 'program', 'set'),
    ],
  },
  {
    id: 'enhancement',
    label: 'Enhancement',
    category: 'engine',
    icon: 'EN',
    scope: 'chapter',
    description: '画质修复与整数倍超分。',
    inputs: [port('in', 'chapter_video', 'chapter_video', 'chapter')],
    outputs: [port('out', 'chapter_video', 'chapter_video', 'chapter')],
  },
  {
    id: 'decensoring',
    label: 'Decensoring',
    category: 'engine',
    icon: 'DC',
    scope: 'chapter',
    description: '对选择的章节执行去马赛克。',
    inputs: [port('in', 'chapter_video', 'chapter_video', 'chapter')],
    outputs: [port('out', 'chapter_video', 'chapter_video', 'chapter')],
  },
  {
    id: 'frame-interpolation',
    label: 'Frame interpolation',
    category: 'engine',
    icon: 'FI',
    scope: 'chapter',
    description: '按 Engine 合同提升帧率。',
    inputs: [port('in', 'chapter_video', 'chapter_video', 'chapter')],
    outputs: [port('out', 'chapter_video', 'chapter_video', 'chapter')],
  },
  {
    id: 'select',
    label: 'Select',
    category: 'operator',
    icon: 'SL',
    scope: 'chapter',
    description: '选择章节并保留 remainder。',
    inputs: [port('in', 'chapter_videos', 'chapter_video_set', 'chapter', 'set')],
    outputs: [
      port('selected', 'selected', 'chapter_video_set', 'chapter', 'set'),
      port('remainder', 'remainder', 'chapter_video_set', 'chapter', 'set'),
    ],
  },
  {
    id: 'collect',
    label: 'Collect',
    category: 'operator',
    icon: 'CL',
    scope: 'chapter',
    description: '检查成员覆盖并重建有序集合。',
    inputs: [
      port('selected', 'selected', 'chapter_video_set', 'chapter', 'set'),
      port('remainder', 'remainder', 'chapter_video_set', 'chapter', 'set'),
    ],
    outputs: [port('out', 'complete_set', 'chapter_video_set', 'chapter', 'set')],
  },
  {
    id: 'new01',
    label: 'new01',
    category: 'engine',
    icon: 'N1',
    scope: 'program',
    description: '演示汇合后的可扩展 program Engine。',
    inputs: [port('in', 'video', 'program_video', 'program')],
    outputs: [port('out', 'video', 'program_video', 'program')],
  },
  {
    id: 'video-encode',
    label: 'Video encode',
    category: 'engine',
    icon: 'VE',
    scope: 'program',
    description: '对汇合后的节目视频执行一次连续编码。',
    inputs: [port('in', 'video', 'program_video', 'program')],
    outputs: [port('out', 'encoded_video', 'encoded_video', 'program')],
  },
  {
    id: 'mux',
    label: 'Mux',
    category: 'engine',
    icon: 'MX',
    scope: 'program',
    description: '合成编码视频、原始音轨及其他容器轨道。',
    inputs: [
      port('video', 'encoded_video', 'encoded_video', 'program'),
      port('audio', 'audio_set', 'audio_artifact_set', 'program', 'set'),
    ],
    outputs: [port('program', 'program', 'program_media', 'program')],
  },
]

export const initialDesignerNodes: WorkflowNode[] = [
  node(
    'source',
    { x: 0, y: 250 },
    baseData('Source', 'core.source', 'Mock source binding', 'source', '01', 'program', '绑定一份完整节目媒体。', {
      outputs: [port('program', 'program', 'program_media', 'program')],
      mediaChanges: [
        { label: '视频', value: '1920×1080 · 30000/1001', tone: 'neutral' },
        { label: '音频', value: '2 original tracks', tone: 'neutral' },
      ],
    }),
  ),
  node(
    'demux',
    { x: 240, y: 250 },
    baseData('Demux', 'engine.demux', 'Video + 2 audio tracks', 'engine', 'DX', 'program', '将容器分离为节目视频和原始有序音轨集合。', {
      engineVersion: 'builtin.demux · mock 0.1.0',
      execution: 'automatic',
      inputs: [port('program', 'program', 'program_media', 'program')],
      outputs: [
        port('video', 'video', 'program_video', 'program'),
        port('audio', 'audio_set', 'audio_artifact_set', 'program', 'set'),
      ],
      mediaChanges: [
        { label: '视频输出', value: 'ProgramVideoArtifact', tone: 'neutral' },
        { label: '音频输出', value: 'AudioArtifactSet · 2 ordered tracks', tone: 'changed' },
      ],
    }),
  ),
  node(
    'partition',
    { x: 480, y: 250 },
    baseData('Partition', 'core.partition', '3 chapters', 'operator', 'PT', 'program', '将节目视频展开为有序章节集合。', {
      inputs: [port('in', 'video', 'program_video', 'program')],
      outputs: [port('out', 'chapter_videos', 'chapter_video_set', 'chapter', 'set')],
    }),
  ),
  node(
    'enhance',
    { x: 720, y: 250 },
    baseData('Enhancement', 'engine.enhancement', 'All chapters', 'engine', 'EN', 'chapter', '对全部章节执行画质修复和 x2 超分。', {
      engineVersion: 'Starlight Precise 2.6',
      inputs: [port('in', 'chapter_videos', 'chapter_video_set', 'chapter', 'set')],
      outputs: [port('out', 'chapter_videos', 'chapter_video_set', 'chapter', 'set')],
      mediaChanges: [
        { label: '视频', value: '1920×1080 → 3840×2160', tone: 'changed' },
        { label: '帧率', value: '30000/1001（保持）', tone: 'neutral' },
        { label: '音频', value: 'not connected · 独立支线', tone: 'neutral' },
      ],
    }),
  ),
  node(
    'select',
    { x: 960, y: 250 },
    baseData('Select', 'core.select', 'Chapter A / remainder', 'operator', 'SL', 'chapter', '将 Chapter A 与其余章节路由到不同处理链。', {
      inputs: [port('in', 'chapter_videos', 'chapter_video_set', 'chapter', 'set')],
      outputs: [
        port('selected', 'selected', 'chapter_video_set', 'chapter', 'set'),
        port('remainder', 'remainder', 'chapter_video_set', 'chapter', 'set'),
      ],
    }),
  ),
  node(
    'decensor',
    { x: 1200, y: 100 },
    baseData('Decensoring', 'engine.decensoring', 'Chapter A only', 'engine', 'DC', 'chapter', '仅处理 Chapter A。', {
      engineVersion: 'Jasna 0.10.0',
      inputs: [port('in', 'selected', 'chapter_video_set', 'chapter', 'set')],
      outputs: [port('out', 'processed', 'chapter_video_set', 'chapter', 'set')],
      mediaChanges: [
        { label: '作用域', value: 'Chapter A', tone: 'changed' },
        { label: '几何', value: '保持', tone: 'neutral' },
      ],
    }),
  ),
  node(
    'passthrough',
    { x: 1200, y: 400 },
    baseData('Passthrough', 'core.passthrough', 'Chapters B–C', 'operator', 'PS', 'chapter', '让未选择章节保持 Artifact 身份继续。', {
      inputs: [port('in', 'remainder', 'chapter_video_set', 'chapter', 'set')],
      outputs: [port('out', 'unchanged', 'chapter_video_set', 'chapter', 'set')],
    }),
  ),
  node(
    'collect',
    { x: 1440, y: 250 },
    baseData('Collect', 'core.collect', 'Coverage: A–C', 'operator', 'CL', 'chapter', '重建完整有序 ChapterSet。', {
      inputs: [
        port('selected', 'selected', 'chapter_video_set', 'chapter', 'set'),
        port('remainder', 'remainder', 'chapter_video_set', 'chapter', 'set'),
      ],
      outputs: [port('out', 'complete_set', 'chapter_video_set', 'chapter', 'set')],
    }),
  ),
  node(
    'interpolate',
    { x: 1680, y: 250 },
    baseData('Frame interpolation', 'engine.frame_interpolation', 'All chapters', 'engine', 'FI', 'chapter', '对完整章节集合执行插帧。', {
      engineVersion: 'Aion',
      inputs: [port('in', 'chapter_videos', 'chapter_video_set', 'chapter', 'set')],
      outputs: [port('out', 'chapter_videos', 'chapter_video_set', 'chapter', 'set')],
      mediaChanges: [
        { label: '帧率', value: '30000/1001 → 60000/1001', tone: 'changed' },
        { label: '分辨率', value: '3840×2160（保持）', tone: 'neutral' },
      ],
    }),
  ),
  node(
    'reduce',
    { x: 1920, y: 250 },
    baseData('Reduce', 'core.reduce', 'Program video assembly', 'operator', 'RD', 'program', '按原顺序汇合为节目视频。', {
      inputs: [port('in', 'chapter_videos', 'chapter_video_set', 'chapter', 'set')],
      outputs: [port('out', 'video', 'program_video', 'program')],
    }),
  ),
  node(
    'new01',
    { x: 2160, y: 250 },
    baseData('new01', 'engine.new01', 'Program video scope', 'engine', 'N1', 'program', '演示在视频汇合后继续增加处理节点。', {
      engineVersion: 'mock 0.1.0',
      execution: 'automatic',
      inputs: [port('in', 'video', 'program_video', 'program')],
      outputs: [port('out', 'video', 'program_video', 'program')],
      mediaChanges: [{ label: '媒体', value: '由 EngineManifest 待推导', tone: 'warning' }],
    }),
  ),
  node(
    'encode',
    { x: 2400, y: 250 },
    baseData('Video encode', 'engine.video_encode', 'One continuous Main10', 'engine', 'VE', 'program', '对完整节目视频执行一次连续编码。', {
      engineVersion: 'builtin.main10 · mock 0.1.0',
      execution: 'automatic',
      inputs: [port('in', 'video', 'program_video', 'program')],
      outputs: [port('out', 'encoded_video', 'encoded_video', 'program')],
      mediaChanges: [
        { label: '视频', value: 'ProgramVideo → HEVC Main10', tone: 'changed' },
        { label: '音频', value: 'not connected', tone: 'neutral' },
      ],
    }),
  ),
  node(
    'mux',
    { x: 2640, y: 250 },
    baseData('Mux', 'engine.mux', 'Video + original audio', 'engine', 'MX', 'program', '将编码视频与完整原始音轨集合合成为 ProgramMedia。', {
      engineVersion: 'builtin.mux · mock 0.1.0',
      execution: 'automatic',
      inputs: [
        port('video', 'encoded_video', 'encoded_video', 'program'),
        port('audio', 'audio_set', 'audio_artifact_set', 'program', 'set'),
      ],
      outputs: [port('program', 'program', 'program_media', 'program')],
      mediaChanges: [
        { label: '视频', value: 'HEVC Main10', tone: 'neutral' },
        { label: '音频', value: '2 tracks · original order · stream copy', tone: 'changed' },
      ],
    }),
  ),
  node(
    'final',
    { x: 2880, y: 250 },
    baseData('Unique Final', 'core.final', 'Publish ProgramMedia', 'final', 'FN', 'program', '发布当前 WorkflowRun 的唯一终端媒体；不承担 Mux。', {
      inputs: [port('program', 'program', 'program_media', 'program')],
    }),
  ),
]

export const initialDesignerEdges: WorkflowEdge[] = [
  edge('source', 'demux', 'ProgramMedia', { sourceHandle: 'program', targetHandle: 'program' }),
  edge('demux', 'partition', 'program video', { sourceHandle: 'video' }),
  edge('demux', 'mux', 'Original audio · 2 tracks · stream copy', {
    sourceHandle: 'audio',
    targetHandle: 'audio',
    type: 'audioLane',
    audioLane: true,
  }),
  edge('partition', 'enhance', 'ChapterSet'),
  edge('enhance', 'select'),
  edge('select', 'decensor', 'A', { sourceHandle: 'selected' }),
  edge('select', 'passthrough', 'B–C', { sourceHandle: 'remainder' }),
  edge('decensor', 'collect', undefined, { targetHandle: 'selected' }),
  edge('passthrough', 'collect', undefined, { targetHandle: 'remainder' }),
  edge('collect', 'interpolate'),
  edge('interpolate', 'reduce'),
  edge('reduce', 'new01'),
  edge('new01', 'encode'),
  edge('encode', 'mux', 'encoded video', { targetHandle: 'video' }),
  edge('mux', 'final', 'ProgramMedia', { sourceHandle: 'program', targetHandle: 'program' }),
]

const planNode = (
  id: string,
  position: XYPosition,
  label: string,
  subtitle: string,
  category: WorkflowNodeData['category'],
  icon: string,
  scope: WorkflowNodeData['scope'],
  instanceLabel?: string,
  extra: Partial<WorkflowNodeData> = {},
): WorkflowNode =>
  node(
    id,
    position,
    baseData(label, `mock.plan.${id}`, subtitle, category, icon, scope, 'Compiler 展开的只读执行实例。', {
      instanceLabel,
      engineVersion: category === 'engine' ? 'bound · mock digest' : undefined,
      ...extra,
    }),
  )

export const planNodes: WorkflowNode[] = [
  planNode('p-source', { x: 0, y: 290 }, 'Source', 'Bound ProgramMedia', 'source', '01', 'program', undefined, {
    inputs: [],
    outputs: [port('program', 'program', 'program_media', 'program')],
  }),
  planNode('p-demux', { x: 220, y: 290 }, 'Demux', 'Video + AudioArtifactSet', 'engine', 'DX', 'program', undefined, {
    inputs: [port('program', 'program', 'program_media', 'program')],
    outputs: [
      port('video', 'video', 'program_video', 'program'),
      port('audio', 'audio_set', 'audio_artifact_set', 'program', 'set'),
    ],
  }),
  planNode('p-partition', { x: 460, y: 290 }, 'Partition', 'A · B · C', 'operator', 'PT', 'program'),
  planNode('p-enh-a', { x: 700, y: 30 }, 'Enhancement', 'Chapter A', 'engine', 'EN', 'chapter', 'A'),
  planNode('p-enh-b', { x: 700, y: 290 }, 'Enhancement', 'Chapter B', 'engine', 'EN', 'chapter', 'B'),
  planNode('p-enh-c', { x: 700, y: 550 }, 'Enhancement', 'Chapter C', 'engine', 'EN', 'chapter', 'C'),
  planNode('p-decensor-a', { x: 940, y: 30 }, 'Decensoring', 'Chapter A', 'engine', 'DC', 'chapter', 'A'),
  planNode('p-pass-b', { x: 940, y: 290 }, 'Passthrough', 'Chapter B', 'operator', 'PS', 'chapter', 'B'),
  planNode('p-pass-c', { x: 940, y: 550 }, 'Passthrough', 'Chapter C', 'operator', 'PS', 'chapter', 'C'),
  planNode('p-fi-a', { x: 1180, y: 30 }, 'Frame interpolation', 'Chapter A', 'engine', 'FI', 'chapter', 'A'),
  planNode('p-fi-b', { x: 1180, y: 290 }, 'Frame interpolation', 'Chapter B', 'engine', 'FI', 'chapter', 'B'),
  planNode('p-fi-c', { x: 1180, y: 550 }, 'Frame interpolation', 'Chapter C', 'engine', 'FI', 'chapter', 'C'),
  planNode('p-collect', { x: 1420, y: 290 }, 'Collect', '3 / 3 members', 'operator', 'CL', 'chapter', undefined, {
    inputs: [
      port('a', 'Chapter A', 'chapter_video', 'chapter'),
      port('b', 'Chapter B', 'chapter_video', 'chapter'),
      port('c', 'Chapter C', 'chapter_video', 'chapter'),
    ],
    outputs: [port('out', 'complete_set', 'chapter_video_set', 'chapter', 'set')],
  }),
  planNode('p-reduce', { x: 1660, y: 290 }, 'Reduce', 'Program video assembly', 'operator', 'RD', 'program'),
  planNode('p-new01', { x: 1880, y: 290 }, 'new01', 'Program video scope', 'engine', 'N1', 'program'),
  planNode('p-encode', { x: 2100, y: 290 }, 'Video encode', 'One continuous Main10', 'engine', 'VE', 'program'),
  planNode('p-mux', { x: 2340, y: 290 }, 'Mux', 'Video + original audio', 'engine', 'MX', 'program', undefined, {
    inputs: [
      port('video', 'encoded_video', 'encoded_video', 'program'),
      port('audio', 'audio_set', 'audio_artifact_set', 'program', 'set'),
    ],
    outputs: [port('program', 'program', 'program_media', 'program')],
  }),
  planNode('p-final', { x: 2580, y: 290 }, 'Unique Final', 'Publish ProgramMedia', 'final', 'FN', 'program', undefined, {
    inputs: [port('program', 'program', 'program_media', 'program')],
    outputs: [],
  }),
]

export const planEdges: WorkflowEdge[] = [
  edge('p-source', 'p-demux', 'ProgramMedia', { sourceHandle: 'program', targetHandle: 'program' }),
  edge('p-demux', 'p-partition', 'program video', { sourceHandle: 'video' }),
  edge('p-demux', 'p-mux', 'Original audio · 2 tracks · stream copy', {
    sourceHandle: 'audio',
    targetHandle: 'audio',
    type: 'audioLane',
    audioLane: true,
  }),
  edge('p-partition', 'p-enh-a', 'A'),
  edge('p-partition', 'p-enh-b', 'B'),
  edge('p-partition', 'p-enh-c', 'C'),
  edge('p-enh-a', 'p-decensor-a'),
  edge('p-enh-b', 'p-pass-b'),
  edge('p-enh-c', 'p-pass-c'),
  edge('p-decensor-a', 'p-fi-a'),
  edge('p-pass-b', 'p-fi-b'),
  edge('p-pass-c', 'p-fi-c'),
  edge('p-fi-a', 'p-collect', undefined, { targetHandle: 'a' }),
  edge('p-fi-b', 'p-collect', undefined, { targetHandle: 'b' }),
  edge('p-fi-c', 'p-collect', undefined, { targetHandle: 'c' }),
  edge('p-collect', 'p-reduce'),
  edge('p-reduce', 'p-new01'),
  edge('p-new01', 'p-encode'),
  edge('p-encode', 'p-mux', 'encoded video', { targetHandle: 'video' }),
  edge('p-mux', 'p-final', 'ProgramMedia', { sourceHandle: 'program', targetHandle: 'program' }),
]

const statusByStep: Array<Record<string, RunStatus>> = [
  {
    'p-source': 'complete',
    'p-demux': 'complete',
    'p-partition': 'complete',
    'p-enh-a': 'waiting_operator',
    'p-enh-b': 'waiting_operator',
    'p-enh-c': 'waiting_operator',
  },
  {
    'p-source': 'complete',
    'p-demux': 'complete',
    'p-partition': 'complete',
    'p-enh-a': 'complete',
    'p-enh-b': 'complete',
    'p-enh-c': 'complete',
    'p-decensor-a': 'running',
    'p-pass-b': 'complete',
    'p-pass-c': 'complete',
    'p-fi-b': 'ready',
    'p-fi-c': 'ready',
  },
  {
    'p-source': 'complete',
    'p-demux': 'complete',
    'p-partition': 'complete',
    'p-enh-a': 'complete',
    'p-enh-b': 'complete',
    'p-enh-c': 'complete',
    'p-decensor-a': 'complete',
    'p-pass-b': 'complete',
    'p-pass-c': 'complete',
    'p-fi-a': 'verifying',
    'p-fi-b': 'complete',
    'p-fi-c': 'complete',
  },
  Object.fromEntries(planNodes.map((item) => [item.id, 'complete' as RunStatus])),
]

export const getRunNodes = (step: number): WorkflowNode[] => {
  const statuses = statusByStep[Math.min(step, statusByStep.length - 1)]
  return planNodes.map((item) => ({
    ...item,
    data: {
      ...item.data,
      runStatus: statuses[item.id] ?? 'pending',
    },
  }))
}

export const draftDiagnostics: Diagnostic[] = [
  {
    code: 'MOCK_SOURCE_BINDING',
    severity: 'warning',
    title: '使用模拟媒体绑定',
    message: 'GUI-0 不读取真实媒体；正式 source authority 尚未接入。',
    entity: 'source',
  },
  {
    code: 'MOCK_ENGINE_CONTRACT',
    severity: 'info',
    title: 'EngineManifest 为演示数据',
    message: '版本、参数和媒体变化只用于验证界面信息密度。',
  },
]

export const compiledDiagnostics: Diagnostic[] = [
  {
    code: 'MOCK_COMPILE_OK',
    severity: 'info',
    title: '模拟编译完成',
    message: '当前浏览器内 Draft 已捕获为只读 mock snapshot；正式 Compiler 与章节展开尚未接入。',
  },
  {
    code: 'MOCK_NO_SIDE_EFFECTS',
    severity: 'warning',
    title: '原型禁止媒体副作用',
    message: '本次冻结与运行只改变浏览器内存中的演示状态。',
  },
]
